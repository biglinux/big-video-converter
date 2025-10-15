"""
Subtitle processor for joined video segments.
Handles extraction, merging, and embedding of subtitles across multiple segments.
"""

import os
import subprocess
import re


class SubtitleProcessor:
    """Processes subtitles for joined video segments."""
    
    def __init__(self, input_file, output_folder, output_basename, 
                 trim_segments, temp_dir, subtitle_mode):
        """
        Initialize subtitle processor.
        
        Args:
            input_file: Source video file path
            output_folder: Output directory
            output_basename: Base name for output files
            trim_segments: List of segment definitions
            temp_dir: Temporary working directory
            subtitle_mode: "extract" or "embedded"
        """
        self.input_file = input_file
        self.output_folder = output_folder
        self.output_basename = output_basename
        self.trim_segments = trim_segments
        self.temp_dir = temp_dir
        self.subtitle_mode = subtitle_mode
    
    def process(self):
        """
        Process subtitles for all segments.
        
        Returns:
            List of tuples (subtitle_file_path, language) for embedded mode,
            or empty list for extract mode.
        """
        subtitle_files_to_embed = []
        
        # Get subtitle streams from source
        subtitle_streams = self._get_subtitle_streams()
        if not subtitle_streams:
            print("No subtitle streams found in source video")
            return []
        
        # Process each subtitle stream
        for stream_info in subtitle_streams:
            parts = stream_info.split(",")
            stream_index = parts[0]
            language = parts[1] if len(parts) > 1 else "und"
            
            print(f"Processing subtitle stream {stream_index} (language: {language})")
            
            # Extract and merge subtitles for this stream
            merged_content = self._merge_subtitle_stream(stream_index, language)
            
            if merged_content:
                output_file = self._save_merged_subtitles(merged_content, language)
                
                if self.subtitle_mode == "embedded":
                    subtitle_files_to_embed.append((output_file, language))
        
        return subtitle_files_to_embed
    
    def _get_subtitle_streams(self):
        """Get list of subtitle streams from source video."""
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index:stream_tags=language",
            "-of", "csv=p=0",
            self.input_file
        ]
        
        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            if result.stdout.strip():
                return result.stdout.strip().split("\n")
        except Exception as e:
            print(f"Error probing subtitle streams: {e}")
        
        return []
    
    def _merge_subtitle_stream(self, stream_index, language):
        """
        Extract and merge subtitles for a specific stream across all segments.
        
        Args:
            stream_index: Index of the subtitle stream
            language: Language code
            
        Returns:
            Merged subtitle content as string
        """
        merged_subs = []
        cumulative_time = 0.0
        
        for i, segment in enumerate(self.trim_segments):
            seg_sub_file = os.path.join(
                self.temp_dir, 
                f"sub_{stream_index}_seg{i}.srt"
            )
            
            start = segment["start"]
            end = segment["end"]
            duration = end - start
            
            # Extract subtitle for this segment
            if self._extract_segment_subtitle(seg_sub_file, stream_index):
                # Read and filter subtitles
                filtered = self._filter_subtitle_range(
                    seg_sub_file, 
                    start, 
                    end, 
                    cumulative_time
                )
                if filtered.strip():
                    merged_subs.append(filtered)
            
            cumulative_time += duration
        
        return "\n\n".join(merged_subs) if merged_subs else ""
    
    def _extract_segment_subtitle(self, output_file, stream_index):
        """
        Extract subtitle stream to file.
        
        Args:
            output_file: Path to save extracted subtitle
            stream_index: Index of subtitle stream
            
        Returns:
            True if extraction succeeded, False otherwise
        """
        extract_cmd = [
            "ffmpeg",
            "-y",
            "-i", self.input_file,
            "-map", f"0:{stream_index}",
            output_file
        ]
        
        try:
            subprocess.run(extract_cmd, capture_output=True, timeout=60)
            return os.path.exists(output_file)
        except Exception as e:
            print(f"Error extracting subtitle: {e}")
            return False
    
    def _filter_subtitle_range(self, subtitle_file, start_time, end_time, time_offset):
        """
        Filter and adjust subtitle timecodes for a specific time range.
        
        Args:
            subtitle_file: Path to subtitle file
            start_time: Start time in seconds
            end_time: End time in seconds
            time_offset: Time offset to apply (cumulative time)
            
        Returns:
            Filtered subtitle content with adjusted timecodes
        """
        try:
            with open(subtitle_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading subtitle file: {e}")
            return ""
        
        # Parse SRT format
        # Pattern: sequence number, timecode line, text lines, blank line
        subtitle_pattern = re.compile(
            r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)',
            re.DOTALL
        )
        
        filtered_subs = []
        
        for match in subtitle_pattern.finditer(content):
            seq_num = match.group(1)
            start_tc = match.group(2)
            end_tc = match.group(3)
            text = match.group(4)
            
            # Convert timecodes to seconds
            start_sec = self._timecode_to_seconds(start_tc)
            end_sec = self._timecode_to_seconds(end_tc)
            
            # Check if subtitle is within segment range
            if start_sec >= start_time and end_sec <= end_time:
                # Adjust timecodes relative to segment start + cumulative offset
                new_start = start_sec - start_time + time_offset
                new_end = end_sec - start_time + time_offset
                
                new_start_tc = self._seconds_to_timecode(new_start)
                new_end_tc = self._seconds_to_timecode(new_end)
                
                filtered_subs.append(
                    f"{len(filtered_subs) + 1}\n"
                    f"{new_start_tc} --> {new_end_tc}\n"
                    f"{text}"
                )
        
        return "\n\n".join(filtered_subs)
    
    def _timecode_to_seconds(self, timecode):
        """Convert SRT timecode (HH:MM:SS,mmm) to seconds."""
        try:
            time_part, ms_part = timecode.split(',')
            h, m, s = map(int, time_part.split(':'))
            ms = int(ms_part)
            return h * 3600 + m * 60 + s + ms / 1000.0
        except:
            return 0.0
    
    def _seconds_to_timecode(self, seconds):
        """Convert seconds to SRT timecode format (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milliseconds = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
    
    def _save_merged_subtitles(self, content, language):
        """
        Save merged subtitle content to file.
        
        Args:
            content: Merged subtitle content
            language: Language code
            
        Returns:
            Path to saved subtitle file
        """
        output_sub_basename = os.path.splitext(self.output_basename)[0]
        
        if self.subtitle_mode == "embedded":
            # Keep temp file for embedding
            output_file = os.path.join(self.temp_dir, f"merged_{language}.srt")
        else:
            # Extract mode: save to output folder
            output_file = os.path.join(
                self.output_folder,
                f"{output_sub_basename}.{language}.srt"
            )
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        print(f"Created merged subtitle: {output_file}")
        return output_file
