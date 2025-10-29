"""
MPV-based video player for real-time preview with filter controls.
Uses MPV's OpenGL render API for proper GTK4 integration.
"""

import gi
import locale
import os
import ctypes

# Force GTK to use OpenGL renderer (NGL - New GL)
# This is necessary for MPV's OpenGL render context to work properly
os.environ['GSK_RENDERER'] = 'ngl'

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib

try:
    import mpv
    from mpv import MpvGlGetProcAddressFn, MpvRenderContext
except (ImportError, OSError) as e:
    print(f"Warning: MPV library not found: {e}")
    mpv = None
    MpvGlGetProcAddressFn = None
    MpvRenderContext = None

try:
    from OpenGL import GL
except ImportError:
    print("Warning: PyOpenGL not found. Install with: pip install PyOpenGL")
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
    Real-time video player using MPV with OpenGL render API.
    Designed for preview only - final render uses external script.
    Compatible with GTK4 on both X11 and Wayland.
    """

    def __init__(self, video_widget):
        """
        Initialize MPV player with OpenGL rendering.

        Args:
            video_widget: Gtk.GLArea widget for OpenGL rendering.
        """
        self.video_widget = video_widget
        self.mpv_instance = None
        self.render_context = None

        # State
        self.is_playing = False
        self.duration = 0
        self.current_file = None
        self.current_volume = 1.0

        # Track current filter values
        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        
        # Cache current adjustment values to avoid redundant updates
        self.cached_brightness = 0
        self.cached_saturation = 0
        self.cached_hue = 0
        
        # Cache crop values to avoid redundant filter updates
        self.cached_crop_left = 0
        self.cached_crop_right = 0
        self.cached_crop_top = 0
        self.cached_crop_bottom = 0

        # Audio/subtitle tracks
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.current_audio_track = -1
        self.current_subtitle_track = -1

        if mpv is None or GL is None:
            print("ERROR: MPV or OpenGL library not available")
            return

        # Connect signals for GLArea lifecycle
        self.video_widget.connect("realize", self._on_realize)
        self.video_widget.connect("render", self._on_render)

    def _on_realize(self, widget):
        """Callback when the GLArea is realized, allowing us to initialize MPV."""
        if self.mpv_instance:
            return

        print("MPV: GLArea realized, initializing player with OpenGL render API...")
        print("MPV: GSK_RENDERER is set to 'ngl' for OpenGL support")

        try:
            # Set locale for MPV (required on some systems)
            try:
                locale.setlocale(locale.LC_NUMERIC, "C")
            except:
                pass

            # Make OpenGL context current
            self.video_widget.make_current()

            # Initialize MPV with libmpv video output
            self.mpv_instance = mpv.MPV(
                vo="libmpv",
                hwdec="auto",
                keep_open="yes",
                idle="yes",
                osc="no",
                input_default_bindings="no",
                input_vo_keyboard="no",
            )
            print("MPV: Instance created successfully")

            # Create OpenGL render context
            opengl_init_params = {
                "get_proc_address": MpvGlGetProcAddressFn(get_proc_address_wrapper())
            }
            
            self.render_context = MpvRenderContext(
                self.mpv_instance,
                "opengl",
                opengl_init_params=opengl_init_params
            )
            print("MPV: OpenGL render context created successfully")

            # Set up update callback
            self.render_context.update_cb = self._on_mpv_render_update

            # Set up event callback
            @self.mpv_instance.event_callback("file-loaded")
            def on_file_loaded(event):
                GLib.idle_add(self._on_file_loaded)

        except Exception:
            print("ERROR: Failed to initialize MPV with OpenGL")
            print("Make sure GSK_RENDERER=ngl is set and OpenGL is available")
            import traceback
            traceback.print_exc()
            self.mpv_instance = None
            self.render_context = None

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
            # Get framebuffer dimensions
            factor = gl_area.get_scale_factor()
            width = gl_area.get_width() * factor
            height = gl_area.get_height() * factor
            fbo = GL.glGetIntegerv(GL.GL_DRAW_FRAMEBUFFER_BINDING)

            # Render MPV frame to the OpenGL framebuffer
            # Use skip_rendering flag to avoid rendering if no update
            self.render_context.render(
                flip_y=True,
                opengl_fbo={"w": width, "h": height, "fbo": fbo}
            )
            return True
        except Exception:
            return False

    def _on_file_loaded(self):
        """Main-thread callback after a file is loaded in MPV."""
        # Safety check - don't process if cleanup has been called
        if not self.mpv_instance:
            return False
        print("MPV: file-loaded event received on main thread")
        self._detect_tracks()
        return False

    def load_video(self, file_path):
        # MPV should always be initialized (only once on first realize)
        if not self.mpv_instance:
            print("MPV: Instance not found, initializing...")
            self._on_realize(self.video_widget)
            if not self.mpv_instance:
                print("MPV: Failed to initialize - cannot load video")
                return False
        
        # Restore render callback if it was cleared during cleanup
        if self.render_context and not self.render_context.update_cb:
            print("MPV: Restoring render context update callback")
            self.render_context.update_cb = self._on_mpv_render_update

        # Verify file exists
        if not os.path.exists(file_path):
            print(f"MPV: File does not exist: {file_path}")
            return False

        try:
            self.current_file = file_path
            # Convert to absolute path
            abs_path = os.path.abspath(file_path)
            print(f"MPV: Loading file: {abs_path}")
            print(f"MPV: File size: {os.path.getsize(abs_path)} bytes")
            
            self.mpv_instance.loadfile(abs_path)
            print("MPV: Loadfile command sent")

            GLib.timeout_add(100, self._query_duration)

            return True

        except Exception as e:
            print(f"Error loading video in MPV: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _pause_after_frame(self):
        """Pause playback after first frame is rendered"""
        try:
            if self.mpv_instance:
                self.mpv_instance.pause = True
                print("MPV: Paused after frame render")
        except:
            pass
        return False

    def _query_duration(self):
        if self.mpv_instance:
            try:
                duration = self.mpv_instance.duration
                if duration:
                    self.duration = duration
            except:
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
            print(f"Error detecting tracks: {e}")

    def play(self):
        if self.mpv_instance:
            self.mpv_instance.pause = False
            self.is_playing = True

    def pause(self):
        if self.mpv_instance:
            self.mpv_instance.pause = True
            self.is_playing = False

    def stop(self):
        if self.mpv_instance:
            self.mpv_instance.command("stop")
            self.is_playing = False

    def seek(self, position_seconds):
        if not self.mpv_instance:
            return False
        try:
            self.mpv_instance.seek(position_seconds, reference="absolute")
            return True
        except Exception:
            return False

    def get_position(self):
        if not self.mpv_instance:
            return 0
        try:
            pos = self.mpv_instance.time_pos
            return pos if pos is not None else 0
        except:
            return 0

    def get_duration(self):
        return self.duration

    def set_brightness(self, value):
        if self.mpv_instance:
            # mpv brightness range is -100 to 100. Our UI is -1.0 to 1.0.
            new_value = int(value * 100)
            # Only update if value actually changed to avoid redundant operations
            if new_value != self.cached_brightness:
                self.cached_brightness = new_value
                try:
                    self.mpv_instance.brightness = new_value
                except:
                    pass

    def set_saturation(self, value):
        if self.mpv_instance:
            # mpv saturation range is -100 to 100 (0 is normal). Our UI is 0.0 to 2.0 (1.0 is normal).
            new_value = int((value - 1.0) * 100)
            # Only update if value actually changed
            if new_value != self.cached_saturation:
                self.cached_saturation = new_value
                try:
                    self.mpv_instance.saturation = new_value
                except:
                    pass

    def set_hue(self, value):
        if self.mpv_instance:
            # mpv hue range is -180 to 180. Our UI is -1.0 to 1.0.
            new_value = int(value * 180)
            # Only update if value actually changed
            if new_value != self.cached_hue:
                self.cached_hue = new_value
                try:
                    self.mpv_instance.hue = new_value
                except:
                    pass

    def set_crop(self, left, right, top, bottom):
        if self.mpv_instance:
            # Convert to integers and check if values actually changed
            new_left = int(left)
            new_right = int(right) 
            new_top = int(top)
            new_bottom = int(bottom)
            
            # Only update if values actually changed to avoid unnecessary updates
            if (new_left != self.cached_crop_left or 
                new_right != self.cached_crop_right or 
                new_top != self.cached_crop_top or 
                new_bottom != self.cached_crop_bottom):
                
                self.crop_left = new_left
                self.crop_right = new_right
                self.crop_top = new_top
                self.crop_bottom = new_bottom
                
                # Update cache
                self.cached_crop_left = new_left
                self.cached_crop_right = new_right
                self.cached_crop_top = new_top
                self.cached_crop_bottom = new_bottom
                
                # Use MPV's built-in video-crop property
                self._update_video_crop()

    def _update_video_crop(self):
        """Update MPV video-crop property using proper format."""
        if not self.mpv_instance:
            print("MPV: No instance available for crop update")
            return

        print(f"MPV: Updating video-crop - L:{self.crop_left} R:{self.crop_right} T:{self.crop_top} B:{self.crop_bottom}")

        # Get video dimensions first
        try:
            video_width = self.mpv_instance.width
            video_height = self.mpv_instance.height
            
            if not video_width or not video_height:
                print("MPV: Video dimensions not available yet")
                return
                
            print(f"MPV: Video dimensions: {video_width}x{video_height}")
            
            # Calculate cropped dimensions
            # video-crop format: WxH+X+Y where W,H are result dimensions and X,Y are offsets
            crop_width = video_width - self.crop_left - self.crop_right
            crop_height = video_height - self.crop_top - self.crop_bottom
            
            # Ensure positive dimensions
            if crop_width <= 0 or crop_height <= 0:
                print(f"MPV: Invalid crop dimensions: {crop_width}x{crop_height}")
                return
            
            # Check if any crop is applied
            if self.crop_left == 0 and self.crop_right == 0 and self.crop_top == 0 and self.crop_bottom == 0:
                # Reset crop to clear any previous crop
                crop_str = ""
                print("MPV: Clearing video-crop")
            else:
                # Format: WxH+X+Y
                crop_str = f"{crop_width}x{crop_height}+{self.crop_left}+{self.crop_top}"
                print(f"MPV: Setting video-crop to: {crop_str}")
            
            # Set the video-crop property
            try:
                self.mpv_instance["video-crop"] = crop_str
                print("MPV: video-crop property set successfully")
                # Queue render update
                GLib.timeout_add(50, self._request_render_update)
            except Exception as e:
                print(f"MPV: Error setting video-crop property: {e}")
                
        except Exception as e:
            print(f"MPV: Error getting video dimensions: {e}")
    
    def _request_render_update(self):
        """Request a render update after crop change"""
        if self.render_context and self.video_widget:
            try:
                self.video_widget.queue_render()
                print("MPV: Render update requested")
            except Exception as e:
                print(f"MPV: Error requesting render update: {e}")
        return False

    def set_volume(self, volume):
        if self.mpv_instance:
            self.current_volume = volume
            self.mpv_instance.volume = volume * 100

    def set_audio_track(self, track_index):
        if self.mpv_instance:
            try:
                self.mpv_instance.aid = track_index
                self.current_audio_track = track_index
            except Exception as e:
                print(f"Error switching audio track: {e}")

    def set_subtitle_track(self, track_index):
        if self.mpv_instance:
            try:
                if track_index == -1:
                    self.mpv_instance.sid = "no"
                else:
                    self.mpv_instance.sid = track_index
                self.current_subtitle_track = track_index
            except Exception:
                print("Error switching subtitle track")

    def get_audio_tracks(self):
        return self.audio_tracks

    def get_subtitle_tracks(self):
        return self.subtitle_tracks

    def cleanup(self, *args):
        """Clean up playback state but keep MPV instance alive for reuse"""
        print("MPV: Starting cleanup (keeping instance alive)")
        
        # Stop playback and clear current file
        if self.mpv_instance:
            try:
                print("MPV: Stopping playback")
                self.mpv_instance.command("stop")
                self.mpv_instance.pause = True
            except Exception as e:
                print(f"MPV: Error stopping playback: {e}")
        
        # Clear render context update callback to prevent render updates while inactive
        if self.render_context:
            try:
                print("MPV: Clearing render context update callback")
                self.render_context.update_cb = None
            except:
                pass

        # Reset playback state
        self.is_playing = False
        self.current_file = None
        self.duration = 0
        
        print("MPV: Cleanup complete (instance preserved for reuse)")
