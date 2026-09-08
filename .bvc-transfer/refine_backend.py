"""Transfer-only replay of follow-up fixes already exercised by the local suite."""
from pathlib import Path
import os
p=Path(os.environ['BVC_REPO'])/'big-video-converter/usr/bin/big-video-converter'
s=p.read_text()
s=s.replace('# Version: 3.0.0','# Version: 3.0.0\n# Public CLI settings below are supplied through the environment.\n# shellcheck disable=SC2154')
s=s.replace('mapfile -d \'\' -t option_args < "$work_dir/options"','''mapfile -d '' -t option_args < "$work_dir/options"
input_options=()
output_options=()
for ((opt=0; opt<${#option_args[@]}; opt++)); do
    case ${option_args[$opt]} in
    -ss|-sseof|-itsoffset|-hwaccel|-hwaccel_output_format|-probesize|-analyzeduration|-thread_queue_size)
        input_options+=("${option_args[$opt]}" "${option_args[$((opt+1))]}")
        ((opt += 1)) ;;
    *) output_options+=("${option_args[$opt]}") ;;
    esac
done''')
s=s.replace('init_qsv=(-init_hw_device vaapi=va:,driver=iHD -init_hw_device qsv=qs@va)','init_qsv=(-init_hw_device "vaapi=va:,driver=iHD" -init_hw_device qsv=qs@va)')
s=s.replace('[[ -n "$subtitle_cmd" &&', '[[ ${#subtitle_cmd[@]} -gt 0 &&')
s=s.replace('nr_video_file="$output_stage_dir/nr-video.mkv"', 'nr_video_file="$output_stage_dir/nr-video.${output_file##*.}"')
s=s.replace('"${option_args[@]}" "${decoder_format[@]}" "${encoder_format[@]}"','"${input_options[@]}" "${decoder_format[@]}" "${encoder_format[@]}" "${output_options[@]}"')
s=s.replace('"${option_args[@]}" "${encoder_format[@]}"','"${input_options[@]}" "${encoder_format[@]}" "${output_options[@]}"')
s=s.replace('"${option_args[@]}" "${encoder_software[@]}"','"${input_options[@]}" "${encoder_software[@]}" "${output_options[@]}"')
s=s.replace('local -a trim_args=()', 'local -a trim_args=()\n    local -a trim_output_args=()')
s=s.replace('''-ss|-sseof|-t|-to|-itsoffset)
            trim_args+=("${option_args[$opt]}" "${option_args[$((opt+1))]}")
            ((opt += 1)) ;;''','''-ss|-sseof|-itsoffset)
            trim_args+=("${option_args[$opt]}" "${option_args[$((opt+1))]}")
            ((opt += 1)) ;;
        -t|-to)
            trim_output_args+=("${option_args[$opt]}" "${option_args[$((opt+1))]}")
            ((opt += 1)) ;;''')
s=s.replace('-map "0:$index_audio" \\\n', '-map "0:$index_audio" "${trim_output_args[@]}" \\\n')
s=s.replace('-map "0:$index_audio" -ac "$channels"', '-map "0:$index_audio" "${trim_output_args[@]}" -ac "$channels"')
s=s.replace('nr_layout=$(probe_audio_layout "$i" "$index_audio")', '''nr_layout=$(probe_audio_layout "$i" "$index_audio")
            [[ -z $audio_channels ]] || nr_layout="${channels}c"''')
a=s.index('\t\t# Build pan expression');b=s.index('\n\t\tlocal -a merge_inputs',a)
s=s[:a]+'''        # Restore exactly the requested layout, including side/back variants.
        local pan_expr="$nr_layout"
        for ((ch=0; ch<channels; ch++)); do
            pan_expr+="|c${ch}=c${ch}"
        done
''' +s[b:]
s=s.replace('local -a mux_inputs=(-i "$nr_video_file")','local -a mux_inputs=(-i "$nr_video_file" -i "$input_file")')
s=s.replace("local -a mux_maps=(-map 0:v:0 -map '0:s?' -c:s copy)","local -a mux_maps=(-map 0:v:0 -map '0:s?' -c:s copy -map_metadata 1 -map_chapters 0)")
s=s.replace('input_idx=$((i + 1))','input_idx=$((i + 2))')
s=s.replace('mux_maps+=(-map "$input_idx:a:0" "-metadata:s:a:$i" "language=${nr_stream_languages[$i]}")','''mux_maps+=(-map "$input_idx:a:0" "-map_metadata:s:a:$i" "1:s:${nr_stream_indices[$i]}" "-metadata:s:a:$i" "language=${nr_stream_languages[$i]}")''')
s=s.replace('echo "WARNING: Noise reduction requested but GTCRN LADSPA plugin not found at /usr/lib/ladspa/libgtcrn_ladspa.so"','echo "ERROR: Noise reduction requested but GTCRN LADSPA plugin not found at /usr/lib/ladspa/libgtcrn_ladspa.so" >&2\n        exit 2')
s=s.replace('''\tqp_hevc=37
\t;;
esac''','''\tqp_hevc=37
\t;;
*) echo "ERROR: Unknown video quality: $video_quality" >&2; exit 2 ;;
esac''')
s=s.replace('    -ss|-sseof|-itsoffset|-hwaccel|','    -sseof|-itsoffset|-hwaccel|')
s=s.replace('if [[ $subtitle_extract == embedded ]]; then\n    case', '''subtitle_input_options=()
subtitle_packet_options=()
defer_subtitles=false
if [[ -n $subtitle_codec_info && ( $subtitle_extract != none || $only_extract_subtitles == 1 ) ]]; then
    if ! python3 "$script_dir/../share/big-video-converter/utils/subtitle_timing.py" "${options:-}" > "$work_dir/subtitle-timing"; then
        echo "ERROR: Invalid subtitle trim interval" >&2
        exit 2
    fi
    mapfile -d '' -t subtitle_timing < "$work_dir/subtitle-timing"
    [[ -z ${subtitle_timing[0]} ]] || subtitle_input_options=(-t "${subtitle_timing[0]}")
    if [[ -n ${subtitle_timing[1]} ]]; then
        subtitle_packet_options=(-bsf:s "${subtitle_timing[1]}")
        [[ $subtitle_extract != embedded ]] || defer_subtitles=true
    fi
fi
if [[ $only_extract_subtitles == 1 ]]; then
    subtitle_extract=extract
fi
if [[ $subtitle_extract == embedded ]]; then
    case''')
s=s.replace('subtitle_cmd+=(-map "0:$index" -c:s copy "$sidecar")','subtitle_cmd+=(-map "0:$index" -c:s copy "${subtitle_packet_options[@]}" "$sidecar")')
s=s.replace('if [[ $subtitle_extract == embedded ]]; then\n        general_params', 'if [[ $subtitle_extract == embedded && $defer_subtitles == false ]]; then\n        general_params')
s=s.replace('"$ffmpeg_executable" -nostdin -i "$input_file" "${subtitle_cmd[@]}"', '"$ffmpeg_executable" -nostdin "${subtitle_input_options[@]}" -i "$input_file" "${subtitle_cmd[@]}"')
s=s.replace('run_ffmpeg -y -i "$input_file" "${subtitle_cmd[@]}"','run_ffmpeg -y "${subtitle_input_options[@]}" -i "$input_file" "${subtitle_cmd[@]}"')
s=s.replace('        -ss|-sseof|-itsoffset)', '        -sseof|-itsoffset)').replace('        -t|-to)', '        -ss|-t|-to)')
s=s.replace('# Final publication fails rather than overwriting a name created concurrently.', '''# Subtitle packets spanning a cut must be clipped, not omitted or allowed to
# extend the A/V output. The first pass intentionally omitted subtitle streams.
if [[ $defer_subtitles == true ]]; then
    with_subtitles="$output_stage_dir/subtitled.${output_file##*.}"
    run_ffmpeg -i "$staged_output" "${subtitle_input_options[@]}" -i "$input_file" \
        -map 0:v:0 -map '0:a?' -map '1:s?' -map_metadata 0 -map_chapters 0 \
        -c copy -c:s "$subtitle_encoder" "${subtitle_packet_options[@]}" -y "$with_subtitles" || exit $?
    staged_output=$with_subtitles
fi

# Final publication fails rather than overwriting a name created concurrently.''')
s=s.replace('echo "Video encoding failed. Killing audio NR..."\n\t\t\tkill "$nr_bg_pid" 2>/dev/null\n\t\t\twait "$nr_bg_pid" 2>/dev/null','echo "Video encoding failed. Reaping audio NR before retry..."\n            # Reap all per-channel children before any retry reuses the private\n            # workspace. Cancellation terminates the entire supervised group.\n            wait "$nr_bg_pid" 2>/dev/null')
p.write_text(s)
