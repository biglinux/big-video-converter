"""Replay the already-tested UI/backend integration edits on the pinned baseline.

Transfer helper only: it is excluded from the final source tree. The transfer
verifier rejects every result whose blob hash differs from the tested manifest.
"""
from pathlib import Path
import ast
import os
import re

R = Path(os.environ['BVC_REPO']) / 'big-video-converter/usr/share/big-video-converter'

p=R/'queue_manager.py';s=p.read_text().replace('import shutil','import shutil\nimport threading\nimport uuid',1)
s=s.replace('self.conversion_page.file_metadata[file_path] = {','self.conversion_page.file_metadata.setdefault(file_path, {',1).replace('''                    "hue": 0.0,
                }''','''                    "hue": 0.0,
                })''',1)
s=s.replace('''            "start_time": GLib.get_real_time(),
        }''','''            "start_time": GLib.get_real_time(),
            "job_id": uuid.uuid4().hex,
            "cancel_event": threading.Event(),
        }''',1)
s=s.replace('''        result = self.conversion_page.force_start_conversion(gpu_override=gpu_slot)
''','''        try:
            result = self.conversion_page.force_start_conversion(gpu_override=gpu_slot)
        except Exception:
            self.logger.exception("Failed to prepare conversion")
            result = False
''')
a=s.index('''        if result is False:
            # Failed to start''');b=s.index('''        # Try to start another''',a)
s=s[:a]+'''        if result is False:
            self.conversion_completed(False, file_path=next_file,
                                      job_id=conversion_info["job_id"])

'''+s[b:]
s=s.replace('GLib.idle_add(lambda: self.conversion_completed(False))','GLib.idle_add(lambda: self.conversion_completed(False, file_path=self.conversion_page.current_file_path))')
s=s.replace('''        self, success, skip_tracking: bool = False, file_path: str = None
''','''        self, success, skip_tracking: bool = False, file_path: str = None,
        job_id: str = None,
''')
a=s.index('''            # Handle cancellation''',s.index('def conversion_completed'));b=s.index('''            # Check single file conversion mode''',a)
s=s[:a]+'''            # A completion must match an active job. A late/duplicate event
            # never releases the oldest (or any unrelated) GPU slot.
            with self.conversions_lock:
                matches = [i for i, info in enumerate(self.active_conversions)
                           if (info.get("job_id") == job_id if job_id
                               else file_path is not None and info.get("file_path") == file_path)]
                if len(matches) != 1:
                    self.logger.debug("Ignoring stale or unidentified completion: %s", job_id or file_path)
                    return
                finished = self.active_conversions.pop(matches[0])
                if finished.get("gpu_slot"):
                    self.gpu_slots.append(finished["gpu_slot"])
                self.currently_converting = bool(self.active_conversions)

            if self.is_cancellation_requested:
                if not self.active_conversions:
                    self.header_bar.set_buttons_sensitive(True)
                    self.currently_converting = False
                    self._was_queue_processing = False
                    self.progress_page.show_completion_summary()
                return

'''+s[b:]
s=s.replace('# Always initialize/reset per-file metadata with default values when adding to queue','# Initialize new files without discarding edits when a file is added again')
s=s.replace('''            self.logger.debug(f"Skipping missing file: {next_file}")
            # Try next one immediately''','''            self.logger.debug(f"Skipping missing file: {next_file}")
            self.progress_page.finish_pending(next_file, success=False)
            # Try next one immediately''')
s=s.replace('''            if self.is_cancellation_requested:
                if not self.active_conversions:''','''            self.progress_page.finish_pending(
                finished["file_path"], success=success,
                cancelled=bool(finished.get("cancel_event") and finished["cancel_event"].is_set()),
            )
            if self.is_cancellation_requested:
                if not self.active_conversions:
                    self.is_cancellation_requested = False''')
s=s.replace('            GLib.idle_add(lambda: self.conversion_completed(False, file_path=self.conversion_page.current_file_path))','''            failed_path = self.conversion_page.current_file_path
            GLib.idle_add(lambda: self.conversion_completed(False, file_path=failed_path))''')
p.write_text(s)

p=R/'ui/progress_page.py';s=p.read_text()
s=s.replace('''            conv_data = self.active_conversions[conversion_id]
            row = conv_data.get("row")''','''            conv_data = self.active_conversions.pop(conversion_id)
            row = conv_data.get("row")''',1)
s=s.replace('''        self.app.is_cancellation_requested = True

        for conv_data''','''        self.app.is_cancellation_requested = True
        for info in list(getattr(self.app, "active_conversions", [])):
            token = info.get("cancel_event")
            if token is not None:
                token.set()
        self.app.conversion_queue.clear()

        for conv_data''',1)
s=s.replace('''            if row.status == "pending":
                row.mark_cancelled()''','''            if row.status == "pending":
                row.cancel(cancel_all=True)''',1)
s=s.replace('''        self._cancelled = True
        if cancel_all:''','''        self._cancelled = True
        token = getattr(self, "cancel_event", None)
        if token is not None:
            token.set()
        if cancel_all:''',1)
a=s.index('''        if was_active and not cancel_all and self.progress_page:''');b=s.index('''    def set_delete_original''',a)
s=s[:a]+'''        # The supervisor alone completes active jobs after reaping the child.
        # A timer here used to release another job's slot after cancellation.

'''+s[b:]
s=s.replace('''        self.status = "active"
        self.start_time''','''        self.status = "active"
        self._cancelled = False
        self.start_time''',1)
s=s.replace('''        self.terminal_buffer.insert(end_iter, text)

    def mark_complete''','''        self.terminal_buffer.insert(end_iter, text)
        # Bound the live GTK buffer; high-volume diagnostics must not exhaust
        # memory or make the main loop progressively slower.
        excess = self.terminal_buffer.get_char_count() - 250000
        if excess > 0:
            self.terminal_buffer.delete(self.terminal_buffer.get_start_iter(),
                                        self.terminal_buffer.get_iter_at_offset(excess))

    def mark_complete''')
a=s.index('    def mark_conversion_complete(')
s=s[:a]+'''    def finish_pending(self, file_path, *, success=False, cancelled=False):
        """Complete a job rejected before a process/active row was created."""
        row = self.queue_items.get(file_path)
        if row is None or row.status != "pending":
            return
        if cancelled:
            row.mark_cancelled()
        else:
            row.mark_complete(success)
        self.completed_count += 1
        self._update_overall_progress()
        self._check_all_complete()

'''+s[a:]
s=s.replace('        if was_active and self.process:\n', '        if was_active and self.process and token is None:\n')
p.write_text(s)

p=R/'ui/conversion_page.py';s=p.read_text().replace('import os\n','import os\nfrom copy import deepcopy\n',1)
s=s.replace('from utils.conversion import run_with_progress_dialog','from utils.conversion import run_with_progress_dialog\nfrom utils.segment_batch import start_segment_batch')
s=s.replace('trim_segments = file_metadata.get("trim_segments", [])','trim_segments = deepcopy(file_metadata.get("trim_segments", []))')
s=s.replace('video_width = getattr(self.app, "video_width", None)\n                video_height = getattr(self.app, "video_height", None)','video_width = None\n                video_height = None')
s=s.replace('''                                # Store these dimensions for future use
                                self.app.video_width = video_width
                                self.app.video_height = video_height
''','')
s=s.replace('''                # Handle additional options. They are expanded by the shell in
                # the conversion script, so validate and quote them first.''','''                # Validate the option grammar shared with the backend. The
                # transport text is parsed into argv, never executed as shell.''')
s=s.replace('''            traceback.print_exc()

        # Get the extension''','''            traceback.print_exc()
            return False

        # Get the extension''',1)
needle='''        # Check MP4 compatibility when copying without reencoding to MP4'''
s=s.replace(needle,'''        job_info = next((info for info in getattr(self.app, "active_conversions", [])
                         if info.get("file_path") == input_file), {})
        job_id = job_info.get("job_id")
        cancel_event = job_info.get("cancel_event") or threading.Event()

'''+needle,1)
s=s.replace('''                    "cmd": cmd,
                    "env_vars": env_vars,''','''                    "cmd": cmd,
                    "env_vars": env_vars,
                    "job_id": job_id,
                    "cancel_event": cancel_event,''',1)
s=s.replace('''            "cmd": cmd,
            "env_vars": env_vars,''','''            "cmd": cmd,
            "env_vars": env_vars,
            "job_id": job_id,
            "cancel_event": cancel_event,''',1)
needle='''                def show_compatibility_warning() -> None:'''
s=s.replace(needle,'''                reencode_env = dict(env_vars)
                reencode_env.update(force_copy_video="0", gpu="software", force_software="1")
                for env_key, setting, default in (("video_quality", "video-quality", "default"),
                        ("video_encoder", "video-codec", "h264"), ("preset", "preset", "default")):
                    reencode_env[env_key] = self.app.settings_manager.load_setting(setting, default)
                reencode_env["video_filter"] = get_video_filter_string(
                    filter_settings, video_width=video_width, video_height=video_height,
                    input_file=input_file)

'''+needle,1)
a=s.index('''                    def on_response(dialog, response) -> None:''',s.index('job_info ='));b=s.index('''                    dialog.connect("response", on_response)''',a)
s=s[:a]+'''                    def on_response(dialog, response) -> None:
                        if cancel_event.is_set() or response == "cancel":
                            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)
                            return
                        if response == "reencode":
                            conversion_context["env_vars"] = reencode_env
                        # Direct invocation: this callback is already on GTK's
                        # main loop. Do not schedule a True-returning idle task.
                        try:
                            self._continue_conversion(conversion_context)
                        except Exception as error:
                            logger.exception("Could not resume conversion")
                            self.app.show_error_dialog(str(error))
                            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)

'''+s[b:]
s=s.replace('return False  # Stop here, dialog will handle continuation','return True  # The active job remains reserved while awaiting a decision.')
a=s.index('''    def _retry_without_copy_mode''');b=s.index('''    def _continue_conversion''',a);s=s[:a]+s[b:]
a=s.index('''        # Handle multi-segment processing''',s.index('def _continue_conversion'));b=s.index('''        # Single segment or no segments''',a)
s=s[:a]+'''        if context.get("cancel_event") is not None and context["cancel_event"].is_set():
            self.app.conversion_completed(False, file_path=input_file, job_id=context.get("job_id"))
            return False
        if len(trim_segments) > 1:
            return start_segment_batch(self, context)

'''+s[b:]
s=s.replace('''            segment_duration=segment_duration,  # Pass segment duration for progress calculation
        )''','''            segment_duration=segment_duration,
            job_id=context.get("job_id"), cancel_event=context.get("cancel_event"),
        )''',1)
s=s.replace('''                        if cancel_event.is_set() or response == "cancel":
                            self.app.conversion_completed''','''                        if cancel_event.is_set() or response == "cancel":
                            cancel_event.set()
                            self.app.conversion_completed''')
p.write_text(s)

p=R/'utils/video_settings.py';s=p.read_text();a=s.index('    is_hevc_10bit_to_h264 = False');b=s.index('    # 1. Add crop filter',a)
s=s[:a]+'''    # Pixel-format negotiation belongs to the encoder backend. It must never
    # suppress the user's crop, colour, rotation or flip operations for HEVC.

'''+s[b:];s=s.replace('from utils.ffmpeg_path import get_ffprobe_executable\n\n','');p.write_text(s)
p=R/'utils/gpu_selector.py';p.write_text(p.read_text().replace('"vp9_nvenc"','""'))
p=R/'utils/file_info.py';s=p.read_text();a=s.index('                            import locale');b=s.index('\n',s.index('except (ImportError, locale.Error, ValueError):',a))
a=s.rfind('                        try:',0,a);b=s.index('\n\n',b)
s=s[:a]+'''                        # Unknown ISO language codes are metadata, not an
                        # instruction to change the process-wide locale.
                        lang_name = lang_code.upper()'''+s[b:]
s=s.replace('                        # Try to get the full language name\n','').replace('                        lang_name = lang_code.upper()\n','')
p.write_text(s)

f=R/'ui/video_edit_page.py';s=f.read_text();a=s.index('    def _save_file_metadata_debounced(');b=s.index('    def set_video(',a)
s=s[:a]+'''    def _save_file_metadata_debounced(self):
        """Keep the in-memory job state current, including during slider drags."""
        if self.metadata_save_timeout:
            GLib.source_remove(self.metadata_save_timeout)
            self.metadata_save_timeout = None
        self._save_file_metadata()

'''+s[b:]
s=s.replace('"""Save metadata immediately - use _save_file_metadata_debounced for slider changes"""','"""Persist edits in the per-file in-memory model."""')
s=s.replace('        self.cleanup_called = True\n        logger.debug("VideoEditPage: Starting cleanup")','''        self._save_file_metadata_debounced()
        self.cleanup_called = True
        self.requested_video_path = None
        self.loading_video = False
        self.processor.invalidate()
        logger.debug("VideoEditPage: Starting cleanup")''')
s=s.replace('self._on_remove_segment_clicked, segment["start"]','self._on_remove_segment_clicked, segment')
s=s.replace('    def _on_remove_segment_clicked(self, button, start_time):\n        self.trim_segments = [s for s in self.trim_segments if s["start"] != start_time]', '''    def _on_remove_segment_clicked(self, button, segment):
        # Equal start times do not make two selections the same segment.
        self.trim_segments = [s for s in self.trim_segments if s is not segment]''')
s=s.replace('  # Use debounced save to avoid file I/O during drag','').replace('  # Use debounced save','')
f.write_text(s)

f=R/'ui/video_processing.py';s=f.read_text();s=s.replace('        self.page = page\n','''        self.page = page
        self._generation = 0

    def invalidate(self):
        """Invalidate outstanding workers without touching GTK from those workers."""
        self._generation += 1
''')
s=s.replace('        # Update the UI with the file path immediately','        self._generation += 1\n        generation = self._generation\n\n        # Update the UI with the file path immediately')
s=s.replace('args=(file_path,)','args=(file_path, generation)')
s=s.replace('def _get_video_info_thread(self, file_path):','def _get_video_info_thread(self, file_path, generation):')
s=s.replace('GLib.idle_add(self._on_video_info_loaded, info, file_path)','GLib.idle_add(self._on_video_info_loaded, info, file_path, generation)')
s=s.replace('GLib.idle_add(self._on_video_info_error, error_message)','GLib.idle_add(self._on_video_info_error, error_message, generation)')
s=s.replace('def _on_video_info_error(self, error_message):\n        """Handle errors from the ffprobe thread."""','''def _on_video_info_error(self, error_message, generation):
        """Ignore errors from an editor session that is no longer current."""
        if generation != self._generation:
            return False''')
s=s.replace('def _on_video_info_loaded(self, info, file_path):','def _on_video_info_loaded(self, info, file_path, generation):')
a=s.index('        # CRITICAL FIX:');b=s.index('        video_stream =',a)
s=s[:a]+'''        if generation != self._generation or file_path != self.page.requested_video_path:
            logger.debug("Ignoring stale video info for: %s", os.path.basename(file_path))
            return False

'''+s[b:]
f.write_text(s)

f=R/'ui/mpv_player.py';s=f.read_text();s=s.replace('import os\n','import os\nimport math\n',1)
a=s.index('    def set_brightness(');b=s.index('    def set_crop(',a)
s=s[:a]+'''    def _set_color_property(self, name: str, value: float) -> None:
        if not self.mpv_instance or not math.isfinite(value):
            return
        value = max(-100, min(100, round(value)))
        cache_name = "cached_" + name
        if value == getattr(self, cache_name):
            return
        try:
            setattr(self.mpv_instance, name, value)
        except Exception as error:
            logger.debug("MPV could not apply %s: %s", name, error)
            return
        # A failed property write must not suppress the next retry.
        setattr(self, cache_name, value)

    def set_brightness(self, value: float) -> None:
        self._set_color_property("brightness", value * 100)

    def set_saturation(self, value: float) -> None:
        self._set_color_property("saturation", (value - 1.0) * 100)

    def set_hue(self, value: float) -> None:
        # MPV's hue property uses -100..100, not degrees.
        self._set_color_property("hue", value * 100)

'''+s[b:]
s=s.replace('                new_left != self.crop_left','                not getattr(self, "_crop_applied", False)\n                or new_left != self.crop_left',1)
s=s.replace('                self.mpv_instance["video-crop"] = crop_str\n','                self.mpv_instance["video-crop"] = crop_str\n                self._crop_applied = True\n',1)
s=s.replace('                self.mpv_instance["video-crop"] = ""\n                if self.video_widget:', '                self.mpv_instance["video-crop"] = ""\n                self._crop_applied = False\n                if self.video_widget:',1)
node=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef))
method=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='load_video')
ln=method.body[0].end_lineno if isinstance(method.body[0],ast.Expr) and isinstance(method.body[0].value,ast.Constant) else method.lineno
lines=s.splitlines(keepends=True);lines.insert(ln,'        self._crop_applied = False\n');s=''.join(lines)
f.write_text(s)
f=R/'ui/noise_dialog.py';s=f.read_text();s=s.replace('        """Set sliders to match the currently selected preset."""\n        idx =', '        """Set sliders only when the user actually selected a preset."""\n        if _eq_slider_guard["active"]:\n            return\n        idx =')
f.write_text(s)

for name in ['audio_dialog','video_encoding_dialog','noise_dialog','extra_dialog','subtitles_dialog','video_options_dialog']:
    f=R/f'ui/{name}.py';s=f.read_text();s=s.replace('import gettext\n','import gettext\nfrom utils.signal_connections import SignalConnections\n',1)
    for helper in ['_make_combo_sync','_make_switch_sync']:
        if f'def {helper}(' not in s:continue
        tree=ast.parse(s);calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==helper]
        lines=s.splitlines(keepends=True);offsets=[0]
        for line in lines:offsets.append(offsets[-1]+len(line))
        for node in sorted(calls,key=lambda n:(n.end_lineno,n.end_col_offset),reverse=True):
            end=offsets[node.end_lineno-1]+node.end_col_offset
            s=s[:end-1]+', connections'+s[end-1:]
        s=re.sub(r'def '+helper+r'\(([^\n]+)\):',lambda m:'def '+helper+'('+m[1]+', connections):',s)
    s=s.replace('    source.connect(', '    connections.connect(source, ')
    s=re.sub(r'(?m)^(\s+)(app\.[\w.]+)\.connect\(',r'\1connections.connect(\2, ',s)
    match=re.search(r'(?m)^    dialog = Adw\.Dialog(?:\.new)?\(\)\n',s)
    if not match:raise RuntimeError(name+' dialog not found')
    s=s[:match.end()]+'    connections = SignalConnections(dialog)\n'+s[match.end():]
    ast.parse(s);f.write_text(s)
for file in [R/'ui/extra_dialog.py',R/'ui/video_options_dialog.py']:
    file.write_text(file.read_text().replace(', \n',',\n'))
