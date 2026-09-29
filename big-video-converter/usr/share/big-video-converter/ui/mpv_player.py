"""
MPV-based video player for real-time preview with filter controls.
Uses MPV's OpenGL render API for proper GTK4 integration.
"""

import ctypes
import gettext
import locale
import logging
import math
import os
import subprocess
from typing import ClassVar

import gi

logger = logging.getLogger(__name__)
_ = gettext.gettext

def is_running_in_vm() -> bool:
    """Detect if running inside a virtual machine (VirtualBox, VMware, QEMU, etc.)"""
    try:
        # Check systemd-detect-virt (most reliable on modern Linux)
        result = subprocess.run(
            ['systemd-detect-virt', '--vm'],
            capture_output=True, text=True, timeout=2, check=False
        )
        if result.returncode == 0 and result.stdout.strip() != 'none':
            vm_type = result.stdout.strip()
            logger.debug(f"MPV: Detected virtual machine: {vm_type}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    try:
        # Fallback: check DMI info
        with open('/sys/class/dmi/id/product_name', 'r') as f:
            product = f.read().lower()
            if any(vm in product for vm in ['virtualbox', 'vmware', 'qemu', 'kvm', 'virtual']):
                logger.debug(
                    f"MPV: Detected virtual machine from DMI: {product.strip()}"
                )
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    try:
        # Fallback: check for hypervisor in cpuinfo
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read().lower()
            if 'hypervisor' in cpuinfo:
                logger.debug("MPV: Detected hypervisor flag in CPU")
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    return False


def get_render_mode_setting():
    """Read render mode setting directly from settings JSON file.
    Returns: 'auto', 'opengl', or 'software'
    """
    import json
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    settings_file = os.path.join(config_home, "big-video-converter", "settings.json")
    try:
        if os.path.exists(settings_file):
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                mode = settings.get("video-preview-render-mode", "auto")
                logger.debug(f"MPV: Loaded render mode setting: '{mode}'")
                return mode
    except (OSError, ValueError, AttributeError) as e:
        logger.error(f"MPV: Could not read settings file: {e}")
    return "auto"


# Cache detection results at module load
_IS_VIRTUAL_MACHINE = is_running_in_vm()

# Read user's render mode preference
_RENDER_MODE_SETTING = get_render_mode_setting()

logger.debug(f"MPV: Detection results - VM: {_IS_VIRTUAL_MACHINE}")
logger.debug(f"MPV: User render mode preference: '{_RENDER_MODE_SETTING}'")

# Determine rendering mode based on user setting or auto-detection
if _RENDER_MODE_SETTING == "opengl":
    # User explicitly chose OpenGL
    _USE_SOFTWARE_MODE = False
    logger.debug("MPV: Will use OpenGL mode (user preference)")
elif _RENDER_MODE_SETTING == "software":
    # User explicitly chose Software mode
    _USE_SOFTWARE_MODE = True
    logger.debug("MPV: Will use software rendering mode (user preference)")
else:
    # Auto mode: let GTK auto-detect the best renderer
    # Do NOT force GSK_RENDERER - it causes issues on some GPUs (e.g. NVIDIA)
    _USE_SOFTWARE_MODE = _IS_VIRTUAL_MACHINE

    if _USE_SOFTWARE_MODE:
        logger.debug("MPV: Will use software rendering mode (auto: VM detected)")
    else:
        logger.debug("MPV: Will use auto-detected renderer (no GSK_RENDERER override)")

# GTK's default Vulkan renderer runs on lavapipe in a VM without 3D, and there it
# composites the GLArea preview late: frames from before a seek, rotate or flip
# stay on screen. The GL renderer presents each mpv frame. This module is
# imported before the first window is realized, which is when GTK reads the
# variable; a renderer the person chose explicitly wins. GTK 4.18 renamed the
# "ngl" renderer to "gl" and removed the old one that used that name.
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

if _USE_SOFTWARE_MODE:
    os.environ.setdefault("GSK_RENDERER", "gl" if Gtk.get_minor_version() >= 18 else "ngl")

try:
    import mpv
    from mpv import MpvGlGetProcAddressFn, MpvRenderContext
except (ImportError, OSError) as e:
    logger.warning(f"Warning: MPV library not found: {e}")
    mpv = None
    MpvGlGetProcAddressFn = None
    MpvRenderContext = None

# python-mpv turns libmpv error codes into these built-in exceptions
# (mpv.ErrorCode.EXCEPTION_DICT); its ShutdownError is a SystemError.
_MPV_ERRORS = (
    AttributeError,
    MemoryError,
    NotImplementedError,
    RuntimeError,
    SystemError,
    TypeError,
    ValueError,
)

try:
    from OpenGL import GL
except ImportError:
    logger.warning("Warning: PyOpenGL not found. Install with: pip install PyOpenGL")
    GL = None


def get_proc_address_wrapper():
    """Get OpenGL function address for MPV render context"""
    def glx_impl(name: bytes):
        from OpenGL import GLX
        return GLX.glXGetProcAddress(name.decode("utf-8"))

    def egl_impl(name: bytes):
        from OpenGL import EGL
        return EGL.eglGetProcAddress(name.decode("utf-8"))

    platform_func = None

    try:
        from OpenGL import GLX  # noqa: F401 - probes that the GLX binding loads
        platform_func = glx_impl
    except (AttributeError, ImportError):
        pass

    if platform_func is None:
        try:
            from OpenGL import EGL  # noqa: F401 - probes that the EGL binding loads
            platform_func = egl_impl
        except (AttributeError, ImportError):
            pass

    if platform_func is None:
        raise RuntimeError("Cannot initialize OpenGL for MPV")

    def wrapper(_, name: bytes):
        address = platform_func(name)
        return ctypes.cast(address, ctypes.c_void_p).value

    return wrapper


class MPVPlayer:
    """
    Real-time video player using MPV's OpenGL render API in a Gtk.GLArea,
    with hardware acceleration or, in software mode, mpv's software settings.
    Compatible with GTK4 on both X11 and Wayland.
    """

    def __init__(self, video_widget):
        """
        Initialize MPV player.

        Args:
            video_widget: Gtk.GLArea that mpv renders into
        """
        self.video_widget = video_widget
        self.mpv_instance = None
        self.render_context = None

        # State
        self.is_playing = False
        self.duration = 0
        self.current_file = None

        # Track current crop values
        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        
        # Cache current adjustment values to avoid redundant updates
        self.cached_brightness = 0
        self.cached_contrast = 0
        self.cached_saturation = 0
        self.cached_hue = 0
        self._pending_colors = {}
        self._color_flush_id = None

        # Decoder mode chosen at start-up, restored when no CPU effect is on.
        self._base_hwdec = None

        # Flip state (applied via render API + GL blit)
        self._flip_h = False
        self._flip_v = False
        self._user_rotation = 0

        # Audio/subtitle tracks; on_tracks_changed() runs on the GTK thread
        # once a loaded file's tracks are known.
        self._reset_tracks()
        self.on_tracks_changed = None

        if mpv is None:
            logger.error("ERROR: MPV library not available")
            return

        if GL is None:
            logger.error("ERROR: OpenGL library not available for OpenGL mode")
            return

        # Connect signals for widget lifecycle
        self.video_widget.connect("realize", self._on_realize)
        self.video_widget.connect("render", self._on_render)

    def _on_realize(self, widget):
        """Callback when the widget is realized, allowing us to initialize MPV."""
        if self.mpv_instance:
            return

        try:
            # Set locale for MPV (required on some systems)
            try:
                locale.setlocale(locale.LC_NUMERIC, "C")
            except locale.Error:
                pass

            if _USE_SOFTWARE_MODE:
                self._init_software_mode()
            else:
                self._init_opengl_mode()

        except Exception:  # noqa: BLE001 - realize handler: a GL, ctypes or libmpv failure is logged and must not escape into GTK
            logger.error("ERROR: Failed to initialize MPV")
            import traceback
            traceback.print_exc()
            self.mpv_instance = None
            self.render_context = None

    def _init_software_mode(self):
        """Initialize MPV with pure software rendering (for VMs without 3D)."""
        logger.debug("MPV: Initializing in software rendering mode...")
        
        try:
            # Use libmpv with aggressive software rendering settings
            self.mpv_instance = mpv.MPV(
                vo="libmpv",
                hwdec="no",               # Disable hardware decoding
                keep_open="yes",
                idle="yes",
                osc="no",
                input_default_bindings="no",
                input_vo_keyboard="no",
                # Software rendering options
                gpu_sw="yes",             # Force software GPU rendering
                opengl_swapinterval=0,    # Disable vsync
                video_sync="audio",       # Sync to audio (less demanding)
                interpolation="no",       # Disable interpolation
                scale="bilinear",         # Fast scaling
                dscale="bilinear",        # Fast downscaling
                cscale="bilinear",        # Fast chroma scaling
            )
            logger.debug("MPV Software: Instance created successfully")
            
            # For software mode, we still need the OpenGL render context
            # but with reduced expectations
            try:
                self.video_widget.make_current()
                
                opengl_init_params = {
                    "get_proc_address": MpvGlGetProcAddressFn(get_proc_address_wrapper())
                }
                
                self.render_context = MpvRenderContext(
                    self.mpv_instance,
                    "opengl",
                    opengl_init_params=opengl_init_params
                )
                logger.debug("MPV Software: OpenGL render context created")
                
                # Set up update callback
                self.render_context.update_cb = self._on_mpv_render_update
                
            except _MPV_ERRORS as e:
                logger.error(f"MPV Software: Failed to create render context: {e}")
                logger.debug(
                    "MPV Software: Will continue without render context (video may not display)"
                )
            
            self._register_events()
                
        except Exception as e:
            logger.error(f"MPV Software: Failed to create instance: {e}")
            raise

    def _init_opengl_mode(self):
        """Initialize MPV with OpenGL render context."""
        logger.debug("MPV: Initializing in OpenGL render context mode...")

        # Make OpenGL context current
        self.video_widget.make_current()

        # Initialize MPV with libmpv video output
        if _IS_VIRTUAL_MACHINE:
            logger.debug("MPV: Using VM-optimized settings (software rendering)")
            self.mpv_instance = mpv.MPV(
                vo="libmpv",
                hwdec="no",           # Disable hardware decoding in VMs
                keep_open="yes",
                idle="yes",
                osc="no",
                input_default_bindings="no",
                input_vo_keyboard="no",
                # Software rendering fallbacks for VMs
                gpu_sw="yes",         # Use software rendering for GPU operations
                opengl_swapinterval=0,  # Disable vsync for better performance in VMs
            )
        else:
            logger.debug("MPV: Using hardware-accelerated settings")
            self.mpv_instance = mpv.MPV(
                vo="libmpv",
                hwdec="auto",         # Auto-detect best hardware decoder
                keep_open="yes",
                idle="yes",
                osc="no",
                input_default_bindings="no",
                input_vo_keyboard="no",
            )
        logger.debug("MPV: Instance created successfully")

        # Create OpenGL render context
        opengl_init_params = {
            "get_proc_address": MpvGlGetProcAddressFn(get_proc_address_wrapper())
        }
        
        self.render_context = MpvRenderContext(
            self.mpv_instance,
            "opengl",
            opengl_init_params=opengl_init_params
        )
        logger.debug("MPV: OpenGL render context created successfully")

        # Set up update callback
        self.render_context.update_cb = self._on_mpv_render_update

        self._register_events()

    def _register_events(self):
        @self.mpv_instance.event_callback("file-loaded")
        def on_file_loaded(event) -> None:
            GLib.idle_add(self._on_file_loaded)

        # The frame size and the file's rotation are known only once the first
        # frame is decoded: a crop restored at load time waits for this event.
        @self.mpv_instance.event_callback("video-reconfig")
        def on_video_reconfig(event) -> None:
            GLib.idle_add(self._on_video_reconfig)

    def _on_mpv_render_update(self):
        """Callback from MPV when it needs to render a new frame"""
        # Safety check - don't process if cleanup has been called
        if not self.render_context or self.current_file is None:
            return
        # Use idle_add to schedule render in GTK main loop
        # This coalesces multiple update requests
        GLib.idle_add(self._update_frame, priority=GLib.PRIORITY_HIGH_IDLE)

    def _update_frame(self):
        """Update frame rendering - only queue if render context indicates update needed"""
        # Check if MPV actually has a new frame to render
        if (
            self.render_context
            and self.current_file is not None
            and self.render_context.update()
        ):
            self.video_widget.queue_render()
        return False

    def _on_render(self, gl_area, context):
        """Callback for the GLArea's 'render' signal."""
        if not self.render_context:
            return False

        try:
            factor = gl_area.get_scale_factor()
            width = int(gl_area.get_width() * factor)
            height = int(gl_area.get_height() * factor)
            fbo = int(GL.glGetIntegerv(GL.GL_DRAW_FRAMEBUFFER_BINDING))

            # flip_y controls vertical flip: True = normal, False = vflipped
            flip_y_val = not self._flip_v

            if self._flip_h:
                # Render to temp FBO, then blit with reversed X for hflip
                self._ensure_temp_fbo(width, height)
                self.render_context.render(
                    flip_y=flip_y_val,
                    opengl_fbo={"w": width, "h": height, "fbo": int(self._temp_fbo)},
                )
                GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, int(self._temp_fbo))
                GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, fbo)
                GL.glBlitFramebuffer(
                    0,
                    0,
                    width,
                    height,
                    width,
                    0,
                    0,
                    height,
                    GL.GL_COLOR_BUFFER_BIT,
                    GL.GL_NEAREST,
                )
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            else:
                self.render_context.render(
                    flip_y=flip_y_val, opengl_fbo={"w": width, "h": height, "fbo": fbo}
                )
            return True
        except Exception:  # noqa: BLE001 - GLArea render handler: a GL or libmpv failure must not escape into GTK mid-frame
            return False

    def _ensure_temp_fbo(self, width: int, height: int) -> None:
        """Create or resize temporary FBO for horizontal flip rendering."""
        if getattr(self, "_temp_fbo", None) is not None and self._temp_fbo_size == (
            width,
            height,
        ):
            return

        # Clean up old resources
        if getattr(self, "_temp_fbo", None) is not None:
            GL.glDeleteFramebuffers(1, [int(self._temp_fbo)])
            GL.glDeleteTextures(1, [int(self._temp_tex)])

        self._temp_tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, int(self._temp_tex))
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA8,
            width,
            height,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            None,
        )
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)

        self._temp_fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, int(self._temp_fbo))
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER,
            GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D,
            int(self._temp_tex),
            0,
        )
        self._temp_fbo_size = (width, height)

    def _on_file_loaded(self):
        """Main-thread callback after a file is loaded in MPV."""
        # Safety check - don't process if cleanup has been called
        if not self.mpv_instance:
            return False
        logger.debug("MPV: file-loaded event received on main thread")
        # The event of a file already stopped would report no tracks.
        if self.current_file is None:
            return False
        self._detect_tracks()
        if self.on_tracks_changed:
            self.on_tracks_changed()
        return False

    def _reset_tracks(self) -> None:
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.current_audio_track = -1
        self.current_subtitle_track = -1

    def load_video(self, file_path: str) -> bool:
        self._crop_applied = False
        # MPV should always be initialized (only once on first realize)
        if not self.mpv_instance:
            logger.debug("MPV: Instance not found, initializing...")
            self._on_realize(self.video_widget)
            if not self.mpv_instance:
                logger.error("MPV: Failed to initialize - cannot load video")
                return False
        
        # Verify file exists
        if not os.path.exists(file_path):
            logger.debug(f"MPV: File does not exist: {file_path}")
            return False

        try:
            self.current_file = file_path
            # The previous file's tracks are not this one's.
            self._reset_tracks()
            # Convert to absolute path
            abs_path = os.path.abspath(file_path)
            logger.debug(f"MPV: Loading file: {abs_path}")
            logger.debug(f"MPV: File size: {os.path.getsize(abs_path)} bytes")
            
            self.mpv_instance.loadfile(abs_path)
            logger.debug("MPV: Loadfile command sent")

            GLib.timeout_add(100, self._query_duration)

            return True

        except (OSError, *_MPV_ERRORS) as e:
            logger.error(f"Error loading video in MPV: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _query_duration(self):
        if self.mpv_instance:
            try:
                duration = self.mpv_instance.duration
                if duration:
                    self.duration = duration
            except _MPV_ERRORS as e:
                logger.debug(f"MPV: Duration not available yet: {e}")
        return False

    def _detect_tracks(self):
        if not self.mpv_instance:
            return

        self._reset_tracks()

        def selected(track_id):
            # python-mpv answers False for "no" and "auto" before a choice.
            return track_id if type(track_id) is int else -1

        try:
            track_list = self.mpv_instance.track_list
            for track in track_list:
                track_id = track.get("id")
                entry = {
                    "index": track_id,
                    "label": track.get("lang")
                    or _("Track {number}").format(number=track_id),
                }
                if track.get("type") == "audio":
                    self.audio_tracks.append(entry)
                elif track.get("type") == "sub":
                    self.subtitle_tracks.append(entry)

            self.current_audio_track = selected(self.mpv_instance.aid)
            self.current_subtitle_track = selected(self.mpv_instance.sid)

        except _MPV_ERRORS as e:
            logger.error(f"Error detecting tracks: {e}")

    def play(self) -> None:
        if self.mpv_instance:
            self.mpv_instance.pause = False
            self.is_playing = True

    def pause(self) -> None:
        if self.mpv_instance:
            self.mpv_instance.pause = True
            self.is_playing = False

    def seek(self, position_seconds) -> bool:
        # Writing time-pos with no file loaded (the editor resets its slider
        # while the next video opens) left that property stuck at 0 for the
        # whole next file. A seek command has no such state, and asynchronous
        # it never makes the GTK thread, which also renders, wait on mpv.
        if not self.mpv_instance or not self.current_file:
            return False
        try:
            self.mpv_instance.command_async("seek", f"{position_seconds:.6f}", "absolute+exact")
            return True
        except _MPV_ERRORS:
            return False

    def get_position(self):
        if not self.mpv_instance:
            return None
        try:
            return self.mpv_instance.playback_time
        except _MPV_ERRORS:
            return None

    def get_duration(self):
        return self.duration

    def _set_color_property(self, name: str, value: float) -> None:
        if not self.mpv_instance or not math.isfinite(value):
            return
        value = max(-100, min(100, round(value)))
        if value == getattr(self, "cached_" + name) and name not in self._pending_colors:
            return
        # A slider drag changes the value on every pointer event, and each
        # change makes mpv redraw the current frame, which takes the slot of
        # the next one. So mpv gets the latest value at most every 100 ms, and
        # asynchronously: this is the GTK thread, which also renders mpv's
        # frames. Measured over a 2 s drag: 88 dropped frames with blocking
        # writes on every event, 10 now.
        self._pending_colors[name] = value
        if self._color_flush_id is None:
            self._color_flush_id = GLib.timeout_add(100, self._flush_colors)

    def _flush_colors(self):
        self._color_flush_id = None
        pending, self._pending_colors = self._pending_colors, {}
        for name, value in pending.items():
            if not self.mpv_instance or value == getattr(self, "cached_" + name):
                continue
            try:
                self.mpv_instance.command_async("set", name, str(value))
            except _MPV_ERRORS as error:
                logger.debug("MPV could not apply %s: %s", name, error)
                continue
            # A failed write must not suppress the next retry.
            setattr(self, "cached_" + name, value)
        return GLib.SOURCE_REMOVE

    def set_brightness(self, value: float) -> None:
        self._set_color_property("brightness", value * 100)

    def set_contrast(self, value: float) -> None:
        self._set_color_property("contrast", value * 100)

    def set_saturation(self, value: float) -> None:
        self._set_color_property("saturation", (value - 1.0) * 100)

    def set_hue(self, value: float) -> None:
        # MPV's hue property uses -100..100, not degrees.
        self._set_color_property("hue", value * 100)

    # mpv's own format filter reads the picture as the user says it is.
    _SOURCE_HDR_FORMATS: ClassVar[dict[str, str]] = {
        "pq": "format=gamma=pq:primaries=bt.2020:colormatrix=bt.2020-ncl",
        "hlg": "format=gamma=hlg:primaries=bt.2020:colormatrix=bt.2020-ncl",
        "sdr": "format=gamma=bt.1886:primaries=bt.709:colormatrix=bt.709",
    }

    def set_video_effects(self, graph: str, shader: str, source_hdr: str = "auto") -> None:
        """Preview an FFmpeg filter graph, an mpv shader (.hook) and the
        colours the source is read as.

        mpv feeds no CPU filter with frames decoded into GPU memory: it drops
        the graph ("Disabling filter lavfi because it has failed") and plays
        on unchanged. While a graph is set the decoder copies its frames back
        (auto-copy). The graph goes in length-prefixed (%n%), so the commas,
        colons and brackets of an escaped path are not read as mpv syntax.
        """
        if not self.mpv_instance:
            return
        try:
            if self._base_hwdec is None:
                self._base_hwdec = str(self.mpv_instance["hwdec"])
            if self._base_hwdec != "no":
                self.mpv_instance["hwdec"] = "auto-copy" if graph else self._base_hwdec
            size = len(graph.encode())
            chain = [self._SOURCE_HDR_FORMATS.get(source_hdr, "")]
            chain.append(f"lavfi=graph=%{size}%{graph}" if graph else "")
            self.mpv_instance.command("vf", "set", ",".join(part for part in chain if part))
            self.mpv_instance.command("change-list", "glsl-shaders", "clr", "")
            if shader:
                self.mpv_instance.command("change-list", "glsl-shaders", "append", shader)
        except _MPV_ERRORS as error:
            logger.warning("MPV could not preview the effects: %s", error)

    def set_crop(self, left: int, right: int, top: int, bottom: int) -> None:
        if self.mpv_instance:
            # Convert to integers and check if values actually changed
            new_left = int(left)
            new_right = int(right) 
            new_top = int(top)
            new_bottom = int(bottom)
            
            # Only update if values actually changed to avoid unnecessary updates
            if (
                not getattr(self, "_crop_applied", False)
                or new_left != self.crop_left
                or new_right != self.crop_right
                or new_top != self.crop_top
                or new_bottom != self.crop_bottom
            ):
                
                self.crop_left = new_left
                self.crop_right = new_right
                self.crop_top = new_top
                self.crop_bottom = new_bottom

                # Use MPV's built-in video-crop property
                self._update_video_crop()

    def _update_video_crop(self):
        """Update MPV video-crop property using proper format."""
        if not self.mpv_instance:
            logger.debug("MPV: No instance available for crop update")
            return

        logger.debug(
            f"MPV: Updating video-crop - L:{self.crop_left} R:{self.crop_right} T:{self.crop_top} B:{self.crop_bottom}"
        )

        # Get video dimensions first
        try:
            video_width = self.mpv_instance.width
            video_height = self.mpv_instance.height
            
            if not video_width or not video_height:
                logger.debug("MPV: Video dimensions not available yet")
                return

            logger.debug(f"MPV: Video dimensions: {video_width}x{video_height}")

            # The margins are in displayed pixels, like FFmpeg's crop after
            # its display-matrix rotation, but mpv crops the decoded frame
            # before turning it by the file's clockwise rotation. Undo that
            # rotation one quarter turn at a time.
            left, right = self.crop_left, self.crop_right
            top, bottom = self.crop_top, self.crop_bottom
            params = getattr(self.mpv_instance, "video_dec_params", None) or {}
            rotation = int(params.get("rotate", 0)) % 360
            for _ in range(rotation // 90):
                left, top, right, bottom = top, right, bottom, left

            # Calculate cropped dimensions
            # video-crop format: WxH+X+Y where W,H are result dimensions and X,Y are offsets
            crop_width = video_width - left - right
            crop_height = video_height - top - bottom

            # Ensure positive dimensions
            if crop_width <= 0 or crop_height <= 0:
                logger.debug(
                    f"MPV: Invalid crop dimensions: {crop_width}x{crop_height}"
                )
                return

            # Check if any crop is applied
            if (
                self.crop_left == 0
                and self.crop_right == 0
                and self.crop_top == 0
                and self.crop_bottom == 0
            ):
                # Reset crop to clear any previous crop
                crop_str = ""
                logger.debug("MPV: Clearing video-crop")
            else:
                # Format: WxH+X+Y
                crop_str = f"{crop_width}x{crop_height}+{left}+{top}"
                logger.debug(f"MPV: Setting video-crop to: {crop_str}")

            # Set the video-crop property
            try:
                self.mpv_instance["video-crop"] = crop_str
                self._crop_applied = True
                logger.debug("MPV: video-crop property set successfully")
                # Queue render update
                GLib.timeout_add(50, self._request_render_update)
            except _MPV_ERRORS as e:
                logger.error(f"MPV: Error setting video-crop property: {e}")

        except _MPV_ERRORS as e:
            logger.error(f"MPV: Error getting video dimensions: {e}")

    def _request_render_update(self):
        """Request a render update after crop change"""
        if self.render_context and self.video_widget:
            # Gtk.Widget.queue_render reports no errors, so nothing to catch.
            self.video_widget.queue_render()
            logger.debug("MPV: Render update requested")
        return False

    def set_volume(self, volume) -> None:
        if self.mpv_instance:
            self.mpv_instance.volume = volume * 100

    def set_audio_track(self, track_index: int) -> None:
        if self.mpv_instance:
            try:
                self.mpv_instance.aid = track_index
                self.current_audio_track = track_index
            except _MPV_ERRORS as e:
                logger.error(f"Error switching audio track: {e}")

    def set_subtitle_track(self, track_index: int) -> None:
        if self.mpv_instance:
            try:
                if track_index == -1:
                    self.mpv_instance.sid = "no"
                else:
                    self.mpv_instance.sid = track_index
                self.current_subtitle_track = track_index
            except _MPV_ERRORS:
                logger.error("Error switching subtitle track")

    def set_audio_filter(self, filter_string: str) -> None:
        """Set or clear the audio filter chain on the mpv instance.

        Uses property set + micro-seek for live updates during playback.

        Args:
            filter_string: A lavfi audio filter (e.g. ``lavfi=[ladspa=...]``)
                           or empty string to clear filters.
        """
        if not self.mpv_instance:
            return
        try:
            self.mpv_instance.af = filter_string if filter_string else ""
            logger.debug(f"MPV: Audio filter set to: {filter_string!r}")
            # Force audio pipeline rebuild during playback
            try:
                if self.mpv_instance.time_pos is not None:
                    self.mpv_instance.command("seek", "0", "relative")
            except _MPV_ERRORS as e:
                logger.debug(f"MPV: Could not rebuild the audio pipeline: {e}")
        except _MPV_ERRORS as e:
            logger.error(f"MPV: Error setting audio filter: {e}")

    def set_speed(self, speed: float) -> None:
        """Set playback speed (1.0 = normal)."""
        if self.mpv_instance:
            try:
                self.mpv_instance.speed = speed
            except _MPV_ERRORS as e:
                logger.error(f"MPV: Error setting speed: {e}")

    def set_rotation(self, degrees: int) -> None:
        """Set user rotation in degrees (0, 90, 180, 270)."""
        self._user_rotation = degrees % 360
        self._apply_transform()

    def set_video_flip(self, flip_h: bool, flip_v: bool) -> None:
        """Set horizontal/vertical flip.

        The renderer mirrors the picture (render flip_y and a reversed
        blit), as vf filters segfault with the OpenGL render API.
        """
        self._flip_h = flip_h
        self._flip_v = flip_v
        self._apply_transform()

    def _apply_transform(self) -> None:
        """Apply combined rotation + flip using video-rotate and GL render."""
        if not self.mpv_instance:
            return
        try:
            # Rotation is applied via MPV property (user rotation only)
            self.mpv_instance.video_rotate = self._user_rotation

            # Flips are handled in _on_render:
            # - vflip: via flip_y parameter in render()
            # - hflip: via glBlitFramebuffer with reversed X coords
            if self.video_widget:
                self.video_widget.queue_render()
        except _MPV_ERRORS as e:
            logger.error(f"MPV: Error applying transform: {e}")

    def _on_video_reconfig(self):
        if not self.mpv_instance:
            return False
        try:
            if self.crop_left or self.crop_right or self.crop_top or self.crop_bottom:
                self._update_video_crop()
        except _MPV_ERRORS as e:
            logger.error(f"MPV: Error applying the crop after reconfig: {e}")
        return False

    def get_audio_tracks(self):
        return self.audio_tracks

    def get_subtitle_tracks(self):
        return self.subtitle_tracks

    def clear_crop(self) -> None:
        """Remove video-crop from MPV (show full video)."""
        if self.mpv_instance:
            try:
                self.mpv_instance["video-crop"] = ""
                self._crop_applied = False
                if self.render_context and self.video_widget:
                    self.video_widget.queue_render()
            except _MPV_ERRORS as e:
                logger.error(f"MPV: Error clearing crop: {e}")

    def cleanup(self, *args) -> None:
        """Clean up playback state but keep MPV instance alive for reuse"""
        logger.debug("MPV: Starting cleanup (keeping instance alive)")

        # Stop playback and clear current file
        if self.mpv_instance:
            try:
                logger.debug("MPV: Stopping playback")
                self.mpv_instance.command("stop")
                self.mpv_instance.pause = True
            except _MPV_ERRORS as e:
                logger.error(f"MPV: Error stopping playback: {e}")

        # Reset playback state
        self.is_playing = False
        self.current_file = None
        self.duration = 0
        self._reset_tracks()
        self._flip_h = False
        self._flip_v = False
        self._user_rotation = 0
