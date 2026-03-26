"""
MPV-based video player for real-time preview with filter controls.
Uses MPV's OpenGL render API for proper GTK4 integration.
Supports X11 native mode for better compatibility in virtual machines.
"""

import ctypes
import locale
import logging
import os
import subprocess

import gi

logger = logging.getLogger(__name__)

def is_running_in_vm() -> bool:
    """Detect if running inside a virtual machine (VirtualBox, VMware, QEMU, etc.)"""
    try:
        # Check systemd-detect-virt (most reliable on modern Linux)
        result = subprocess.run(
            ['systemd-detect-virt', '--vm'],
            capture_output=True, text=True, timeout=2
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


def is_running_on_x11() -> bool:
    """Detect if running on X11 (not Wayland)"""
    # Check XDG_SESSION_TYPE first
    session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
    logger.debug(f"MPV: XDG_SESSION_TYPE = '{session_type}'")
    
    if session_type == 'x11':
        logger.debug("MPV: X11 detected via XDG_SESSION_TYPE")
        return True
    if session_type == 'wayland':
        logger.debug("MPV: Wayland detected via XDG_SESSION_TYPE")
        return False
    
    # Check WAYLAND_DISPLAY - if set, we're on Wayland
    wayland_display = os.environ.get('WAYLAND_DISPLAY', '')
    logger.debug(f"MPV: WAYLAND_DISPLAY = '{wayland_display}'")
    if wayland_display:
        logger.debug("MPV: Wayland detected via WAYLAND_DISPLAY")
        return False
    
    # Check DISPLAY - if set without WAYLAND_DISPLAY, assume X11
    display = os.environ.get('DISPLAY', '')
    logger.debug(f"MPV: DISPLAY = '{display}'")
    if display:
        logger.debug("MPV: X11 detected via DISPLAY (no WAYLAND_DISPLAY)")
        return True
    
    # Last resort: check GDK_BACKEND
    gdk_backend = os.environ.get('GDK_BACKEND', '').lower()
    logger.debug(f"MPV: GDK_BACKEND = '{gdk_backend}'")
    if gdk_backend == 'x11':
        logger.debug("MPV: X11 detected via GDK_BACKEND")
        return True
    if gdk_backend == 'wayland':
        logger.debug("MPV: Wayland detected via GDK_BACKEND")
        return False
    
    # Default: assume X11 if we couldn't detect (more compatible)
    logger.error("MPV: Could not detect session type, assuming X11")
    return True


def get_render_mode_setting():
    """Read render mode setting directly from settings JSON file.
    Returns: 'auto', 'opengl', or 'software'
    """
    import json
    settings_file = os.path.expanduser("~/.config/big-video-converter/settings.json")
    try:
        if os.path.exists(settings_file):
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                mode = settings.get("video-preview-render-mode", "auto")
                logger.debug(f"MPV: Loaded render mode setting: '{mode}'")
                return mode
    except Exception as e:
        logger.error(f"MPV: Could not read settings file: {e}")
    return "auto"


# Cache detection results at module load
_IS_VIRTUAL_MACHINE = is_running_in_vm()
_IS_X11 = is_running_on_x11()
_IS_WAYLAND = not _IS_X11

# Read user's render mode preference
_RENDER_MODE_SETTING = get_render_mode_setting()

logger.debug(
    f"MPV: Detection results - VM: {_IS_VIRTUAL_MACHINE}, X11: {_IS_X11}, Wayland: {_IS_WAYLAND}"
)
logger.debug(f"MPV: User render mode preference: '{_RENDER_MODE_SETTING}'")

# Determine rendering mode based on user setting or auto-detection
if _RENDER_MODE_SETTING == "opengl":
    # User explicitly chose OpenGL
    _USE_X11_MODE = False
    _USE_SOFTWARE_MODE = False
    logger.debug("MPV: Will use OpenGL mode (user preference)")
    os.environ['GSK_RENDERER'] = 'ngl'
elif _RENDER_MODE_SETTING == "software":
    # User explicitly chose Software mode
    _USE_X11_MODE = False
    _USE_SOFTWARE_MODE = True
    logger.debug("MPV: Will use software rendering mode (user preference)")
    os.environ['GSK_RENDERER'] = 'cairo'
else:
    # Auto mode: let GTK auto-detect the best renderer
    # Do NOT force GSK_RENDERER - it causes issues on some GPUs (e.g. NVIDIA)
    _USE_X11_MODE = _IS_VIRTUAL_MACHINE and _IS_X11
    _USE_SOFTWARE_MODE = _IS_VIRTUAL_MACHINE and _IS_WAYLAND
    
    if _USE_X11_MODE:
        logger.debug("MPV: Will use X11 native mode (auto: VM on X11 detected)")
    elif _USE_SOFTWARE_MODE:
        logger.debug(
            "MPV: Will use software rendering mode (auto: VM on Wayland detected)"
        )
        os.environ['GSK_RENDERER'] = 'cairo'
    else:
        logger.debug("MPV: Will use auto-detected renderer (no GSK_RENDERER override)")

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib

try:
    import mpv
    from mpv import MpvGlGetProcAddressFn, MpvRenderContext
except (ImportError, OSError) as e:
    logger.warning(f"Warning: MPV library not found: {e}")
    mpv = None
    MpvGlGetProcAddressFn = None
    MpvRenderContext = None

# Only import OpenGL if not using X11 mode
GL = None
if not _USE_X11_MODE:
    try:
        from OpenGL import GL
    except ImportError:
        logger.warning(
            "Warning: PyOpenGL not found. Install with: pip install PyOpenGL"
        )
        GL = None


def get_proc_address_wrapper():
    """Get OpenGL function address for MPV render context"""
    def glx_impl(name: bytes):
        from OpenGL import GLX  # noqa: F401
        return GLX.glXGetProcAddress(name.decode("utf-8"))

    def egl_impl(name: bytes):
        from OpenGL import EGL  # noqa: F401
        return EGL.eglGetProcAddress(name.decode("utf-8"))

    platform_func = None

    try:
        from OpenGL import GLX  # noqa: F401
        platform_func = glx_impl
    except (AttributeError, ImportError):
        pass

    if platform_func is None:
        try:
            from OpenGL import EGL  # noqa: F401
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
    Real-time video player using MPV.
    Supports two rendering modes:
    - OpenGL render API (default): Uses libmpv with GLArea for hardware-accelerated rendering
    - X11 native mode (VMs): Uses X11 window embedding for better VM compatibility
    Compatible with GTK4 on both X11 and Wayland.
    """

    # Class variables to indicate which mode is being used
    use_x11_mode = _USE_X11_MODE
    use_software_mode = _USE_SOFTWARE_MODE

    def __init__(self, video_widget):
        """
        Initialize MPV player.

        Args:
            video_widget: Gtk.GLArea (OpenGL mode) or Gtk.DrawingArea (X11 mode)
        """
        self.video_widget = video_widget
        self.mpv_instance = None
        self.render_context = None
        self._wid = None  # Window ID for X11 mode

        # State
        self.is_playing = False
        self.duration = 0
        self.current_file = None
        self.current_volume = 1.0

        # Track current crop values
        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        
        # Cache current adjustment values to avoid redundant updates
        self.cached_brightness = 0
        self.cached_saturation = 0
        self.cached_hue = 0

        # Flip state (applied via render API + GL blit)
        self._flip_h = False
        self._flip_v = False
        self._user_rotation = 0

        # Audio/subtitle tracks
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.current_audio_track = -1
        self.current_subtitle_track = -1

        if mpv is None:
            logger.error("ERROR: MPV library not available")
            return

        if not _USE_X11_MODE and GL is None:
            logger.error("ERROR: OpenGL library not available for OpenGL mode")
            return

        # Connect signals for widget lifecycle
        self.video_widget.connect("realize", self._on_realize)
        
        # Only connect render signal for OpenGL mode
        if not _USE_X11_MODE:
            self.video_widget.connect("render", self._on_render)

    def _get_x11_window_id(self):
        """Get the X11 window ID (XID) from a GTK4 widget."""
        try:
            native = self.video_widget.get_native()
            if native is None:
                logger.debug("MPV X11: No native window found")
                return None
            
            surface = native.get_surface()
            if surface is None:
                logger.debug("MPV X11: No surface found")
                return None
            
            # Check if it's an X11 surface
            display = Gdk.Display.get_default()
            if display is None:
                logger.debug("MPV X11: No display found")
                return None
            
            # Try to get XID using GdkX11
            try:
                gi.require_version("GdkX11", "4.0")
                from gi.repository import GdkX11
                
                if isinstance(surface, GdkX11.X11Surface):
                    xid = surface.get_xid()
                    logger.debug(f"MPV X11: Got window XID: {xid}")
                    return xid
                else:
                    logger.debug(f"MPV X11: Surface is not X11Surface: {type(surface)}")
                    return None
            except (ValueError, ImportError) as e:
                logger.debug(f"MPV X11: GdkX11 not available: {e}")
                return None
                
        except Exception as e:
            logger.error(f"MPV X11: Error getting window ID: {e}")
            import traceback
            traceback.print_exc()
            return None

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

            if _USE_X11_MODE:
                self._init_x11_mode()
            elif _USE_SOFTWARE_MODE:
                self._init_software_mode()
            else:
                self._init_opengl_mode()

        except Exception:
            logger.error("ERROR: Failed to initialize MPV")
            import traceback
            traceback.print_exc()
            self.mpv_instance = None
            self.render_context = None

    def _init_x11_mode(self):
        """Initialize MPV in X11 native mode (for VMs)."""
        logger.debug("MPV: Initializing in X11 native mode...")
        
        # Get the X11 window ID
        self._wid = self._get_x11_window_id()
        
        if self._wid is None:
            logger.error(
                "MPV X11: Failed to get window ID, falling back to software mode"
            )
            self._init_software_mode()
            return

        # Initialize MPV with X11/GPU output using window embedding
        # Try gpu first (uses X11 EGL), fall back to x11
        try:
            self.mpv_instance = mpv.MPV(
                vo="gpu,x11,xv",      # Try gpu, then x11, then xv
                hwdec="no",            # Disable hardware decoding in VMs
                wid=str(self._wid),    # Embed into our widget's window
                keep_open="yes",
                idle="yes",
                osc="no",
                input_default_bindings="no",
                input_vo_keyboard="no",
                # Performance options for VMs
                video_sync="audio",
                interpolation="no",
            )
            logger.debug("MPV X11: Instance created successfully with window embedding")
            
            # Set up event callback
            @self.mpv_instance.event_callback("file-loaded")
            def on_file_loaded(event) -> None:
                GLib.idle_add(self._on_file_loaded)
                
        except Exception as e:
            logger.error(f"MPV X11: Failed to create instance: {e}")
            raise

    def _init_software_mode(self):
        """Initialize MPV with pure software rendering (for VMs on Wayland without 3D)."""
        logger.debug("MPV: Initializing in software rendering mode...")
        logger.debug("MPV: GSK_RENDERER is set to 'cairo' for software rendering")
        
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
                
            except Exception as e:
                logger.error(f"MPV Software: Failed to create render context: {e}")
                logger.debug(
                    "MPV Software: Will continue without render context (video may not display)"
                )
            
            # Set up event callback
            @self.mpv_instance.event_callback("file-loaded")
            def on_file_loaded(event) -> None:
                GLib.idle_add(self._on_file_loaded)
                
        except Exception as e:
            logger.error(f"MPV Software: Failed to create instance: {e}")
            raise

    def _init_opengl_mode(self):
        """Initialize MPV with OpenGL render context."""
        logger.debug("MPV: Initializing in OpenGL render context mode...")
        logger.debug("MPV: GSK_RENDERER is set to 'ngl' for OpenGL support")

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

        # Set up event callback
        @self.mpv_instance.event_callback("file-loaded")
        def on_file_loaded(event) -> None:
            GLib.idle_add(self._on_file_loaded)

    def _on_mpv_render_update(self):
        """Callback from MPV when it needs to render a new frame"""
        # Safety check - don't process if cleanup has been called
        if not self.render_context:
            return
        # Use idle_add to schedule render in GTK main loop
        # This coalesces multiple update requests
        GLib.idle_add(self._update_frame, priority=GLib.PRIORITY_HIGH_IDLE)

    def _update_frame(self):
        """Update frame rendering - only queue if render context indicates update needed"""
        if self.render_context:
            # Check if MPV actually has a new frame to render
            if self.render_context.update():
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
        except Exception:
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
        self._detect_tracks()
        return False

    def load_video(self, file_path: str) -> bool:
        # MPV should always be initialized (only once on first realize)
        if not self.mpv_instance:
            logger.debug("MPV: Instance not found, initializing...")
            self._on_realize(self.video_widget)
            if not self.mpv_instance:
                logger.error("MPV: Failed to initialize - cannot load video")
                return False
        
        # Restore render callback if it was cleared during cleanup (OpenGL mode only)
        if not _USE_X11_MODE and self.render_context and not self.render_context.update_cb:
            logger.debug("MPV: Restoring render context update callback")
            self.render_context.update_cb = self._on_mpv_render_update

        # Verify file exists
        if not os.path.exists(file_path):
            logger.debug(f"MPV: File does not exist: {file_path}")
            return False

        try:
            self.current_file = file_path
            # Convert to absolute path
            abs_path = os.path.abspath(file_path)
            logger.debug(f"MPV: Loading file: {abs_path}")
            logger.debug(f"MPV: File size: {os.path.getsize(abs_path)} bytes")
            
            self.mpv_instance.loadfile(abs_path)
            logger.debug("MPV: Loadfile command sent")

            GLib.timeout_add(100, self._query_duration)

            return True

        except Exception as e:
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
            except Exception:
                pass
        return False

    def _detect_tracks(self):
        if not self.mpv_instance:
            return

        self.audio_tracks = []
        self.subtitle_tracks = []

        try:
            track_list = self.mpv_instance.track_list
            for track in track_list:
                track_id = track.get("id")
                if track.get("type") == "audio":
                    self.audio_tracks.append({
                        "index": track_id,
                        "label": track.get("lang", f"Track {track_id}")
                        or f"Track {track_id}",
                    })
                elif track.get("type") == "sub":
                    self.subtitle_tracks.append({
                        "index": track_id,
                        "label": track.get("lang", f"Track {track_id}")
                        or f"Track {track_id}",
                    })

            self.current_audio_track = self.mpv_instance.aid
            self.current_subtitle_track = (
                self.mpv_instance.sid if self.mpv_instance.sid != "no" else -1
            )

        except Exception as e:
            logger.error(f"Error detecting tracks: {e}")

    def play(self) -> None:
        if self.mpv_instance:
            self.mpv_instance.pause = False
            self.is_playing = True

    def pause(self) -> None:
        if self.mpv_instance:
            self.mpv_instance.pause = True
            self.is_playing = False

    def stop(self) -> None:
        if self.mpv_instance:
            self.mpv_instance.command("stop")
            self.is_playing = False

    def seek(self, position_seconds) -> bool:
        if not self.mpv_instance:
            return False
        try:
            self.mpv_instance.time_pos = position_seconds
            return True
        except Exception:
            try:
                self.mpv_instance.seek(position_seconds, reference="absolute")
                return True
            except Exception:
                return False

    def get_position(self):
        if not self.mpv_instance:
            return None
        try:
            return self.mpv_instance.time_pos
        except Exception:
            return None

    def get_duration(self):
        return self.duration

    def set_brightness(self, value: str) -> None:
        if self.mpv_instance:
            # mpv brightness range is -100 to 100. Our UI is -1.0 to 1.0.
            new_value = int(value * 100)
            # Only update if value actually changed to avoid redundant operations
            if new_value != self.cached_brightness:
                self.cached_brightness = new_value
                try:
                    self.mpv_instance.brightness = new_value
                except Exception:
                    pass

    def set_saturation(self, value: str) -> None:
        if self.mpv_instance:
            # mpv saturation range is -100 to 100 (0 is normal). Our UI is 0.0 to 2.0 (1.0 is normal).
            new_value = int((value - 1.0) * 100)
            # Only update if value actually changed
            if new_value != self.cached_saturation:
                self.cached_saturation = new_value
                try:
                    self.mpv_instance.saturation = new_value
                except Exception:
                    pass

    def set_hue(self, value: str) -> None:
        if self.mpv_instance:
            # mpv hue range is -180 to 180. Our UI is -1.0 to 1.0.
            new_value = int(value * 180)
            # Only update if value actually changed
            if new_value != self.cached_hue:
                self.cached_hue = new_value
                try:
                    self.mpv_instance.hue = new_value
                except Exception:
                    pass

    def set_crop(self, left: int, right: int, top: int, bottom: int) -> None:
        if self.mpv_instance:
            # Convert to integers and check if values actually changed
            new_left = int(left)
            new_right = int(right) 
            new_top = int(top)
            new_bottom = int(bottom)
            
            # Only update if values actually changed to avoid unnecessary updates
            if (
                new_left != self.crop_left
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

            # Calculate cropped dimensions
            # video-crop format: WxH+X+Y where W,H are result dimensions and X,Y are offsets
            crop_width = video_width - self.crop_left - self.crop_right
            crop_height = video_height - self.crop_top - self.crop_bottom

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
                crop_str = (
                    f"{crop_width}x{crop_height}+{self.crop_left}+{self.crop_top}"
                )
                logger.debug(f"MPV: Setting video-crop to: {crop_str}")

            # Set the video-crop property
            try:
                self.mpv_instance["video-crop"] = crop_str
                logger.debug("MPV: video-crop property set successfully")
                # Queue render update
                GLib.timeout_add(50, self._request_render_update)
            except Exception as e:
                logger.error(f"MPV: Error setting video-crop property: {e}")

        except Exception as e:
            logger.error(f"MPV: Error getting video dimensions: {e}")

    def _request_render_update(self):
        """Request a render update after crop change"""
        if self.render_context and self.video_widget:
            try:
                self.video_widget.queue_render()
                logger.debug("MPV: Render update requested")
            except Exception as e:
                logger.error(f"MPV: Error requesting render update: {e}")
        return False

    def set_volume(self, volume) -> None:
        if self.mpv_instance:
            self.current_volume = volume
            self.mpv_instance.volume = volume * 100

    def set_audio_track(self, track_index: int) -> None:
        if self.mpv_instance:
            try:
                self.mpv_instance.aid = track_index
                self.current_audio_track = track_index
            except Exception as e:
                logger.error(f"Error switching audio track: {e}")

    def set_subtitle_track(self, track_index: int) -> None:
        if self.mpv_instance:
            try:
                if track_index == -1:
                    self.mpv_instance.sid = "no"
                else:
                    self.mpv_instance.sid = track_index
                self.current_subtitle_track = track_index
            except Exception:
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
            except Exception:
                pass
        except Exception as e:
            logger.error(f"MPV: Error setting audio filter: {e}")

    def set_speed(self, speed: float) -> None:
        """Set playback speed (1.0 = normal)."""
        if self.mpv_instance:
            try:
                self.mpv_instance.speed = speed
            except Exception as e:
                logger.error(f"MPV: Error setting speed: {e}")

    def set_rotation(self, degrees: int) -> None:
        """Set user rotation in degrees (0, 90, 180, 270)."""
        self._user_rotation = degrees % 360
        self._apply_transform()

    def set_video_flip(self, flip_h: bool, flip_v: bool) -> None:
        """Set horizontal/vertical flip.

        Uses a combination of video-rotate and render flip_y to avoid
        vf filters which cause segfaults with the OpenGL render API.
        hflip = rotate(180) + vflip, so we combine all transforms.
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

            # Request re-render with new flip state
            if self.video_widget:
                self.video_widget.queue_render()
        except Exception as e:
            logger.error(f"MPV: Error applying transform: {e}")

    def get_audio_tracks(self):
        return self.audio_tracks

    def get_subtitle_tracks(self):
        return self.subtitle_tracks

    def get_video_dimensions(self) -> tuple[int, int] | None:
        """Return (width, height) of the current video, or None."""
        if not self.mpv_instance:
            return None
        try:
            w = self.mpv_instance.width
            h = self.mpv_instance.height
            if w and h:
                return (w, h)
        except Exception:
            pass
        return None

    def clear_crop(self) -> None:
        """Remove video-crop from MPV (show full video)."""
        if self.mpv_instance:
            try:
                self.mpv_instance["video-crop"] = ""
                if self.video_widget:
                    self.video_widget.queue_render()
            except Exception as e:
                logger.error(f"MPV: Error clearing crop: {e}")

    def apply_crop(self) -> None:
        """Re-apply current crop values to MPV."""
        self._update_video_crop()

    def cleanup(self, *args) -> None:
        """Clean up playback state but keep MPV instance alive for reuse"""
        logger.debug("MPV: Starting cleanup (keeping instance alive)")

        # Stop playback and clear current file
        if self.mpv_instance:
            try:
                logger.debug("MPV: Stopping playback")
                self.mpv_instance.command("stop")
                self.mpv_instance.pause = True
            except Exception as e:
                logger.error(f"MPV: Error stopping playback: {e}")

        # Clear render context update callback to prevent render updates while inactive
        if self.render_context:
            try:
                logger.debug("MPV: Clearing render context update callback")
                self.render_context.update_cb = None
            except Exception:
                pass

        # Reset playback state
        self.is_playing = False
        self.current_file = None
        self.duration = 0
        self._flip_h = False
        self._flip_v = False
        self._user_rotation = 0
