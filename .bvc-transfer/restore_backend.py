"""Transfer-only replay of the tested Bash argument-array conversion."""
from pathlib import Path
import os
import re
import shlex
R=Path(os.environ['BVC_REPO'])
f=R/'big-video-converter/usr/bin/big-video-converter'
s=f.read_text()
s=s.replace('input_file="$1"', '''input_file="$1"
# Absolute paths also keep leading '-' and ':' in filenames away from option
# parsing and FFmpeg's protocol selector. Do not normalize via command output:
# that would strip a trailing newline from an otherwise valid filename.
[[ $input_file == /* ]] || input_file="$PWD/$input_file"''')
idx=s.index('#############################################################################\n# Probing helpers')
s=s[:idx]+'''# All paths created by this process live in private directories. Never remove
# a file merely because its name resembles one of our outputs.
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/bvc.XXXXXXXX") || exit 1
output_stage_dir=""
cleanup() {
    [[ -z $output_stage_dir ]] || rm -rf -- "$output_stage_dir"
    rm -rf -- "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
option_parser="$script_dir/../share/big-video-converter/utils/ffmpeg_options.py"
if ! python3 "$option_parser" --null "${options:-}" > "$work_dir/options"; then
    echo "ERROR: Invalid additional FFmpeg options" >&2
    exit 2
fi
mapfile -d '' -t option_args < "$work_dir/options"

# Use the same FFprobe distribution as the selected FFmpeg whenever possible.
if [[ -z ${ffprobe_executable:-} ]]; then
    ffmpeg_path=$(command -v -- "$ffmpeg_executable") || exit 1
    if [[ -x ${ffmpeg_path%/*}/ffprobe ]]; then
        ffprobe_executable="${ffmpeg_path%/*}/ffprobe"
    else
        ffprobe_executable=ffprobe
    fi
fi

# Scalar values used in arithmetic must not become awk/bc expressions.
for name in hpf_frequency compressor_intensity gate_intensity noise_strength \
    noise_speech_strength noise_model noise_lookahead noise_model_blend noise_voice_recovery; do
    value=${!name}
    if [[ -n $value && ! $value =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "ERROR: Invalid numeric setting: $name" >&2
        exit 2
    fi
done
if [[ -n $audio_channels && ! $audio_channels =~ ^([1-9]|[1-5][0-9]|6[0-4])$ ]]; then
    echo "ERROR: Invalid audio channel count" >&2
    exit 2
fi
if [[ -n $eq_bands ]]; then
    IFS=, read -ra eq_values <<< "$eq_bands"
    [[ ${#eq_values[@]} -eq 10 ]] || { echo "ERROR: Expected ten equalizer bands" >&2; exit 2; }
    for value in "${eq_values[@]}"; do
        [[ $value =~ ^-?[0-9]+([.][0-9]+)?$ ]] || { echo "ERROR: Invalid equalizer gain" >&2; exit 2; }
    done
fi

''' +s[idx:]
s=re.sub(r'(?<![\w"{])\$ffmpeg_executable(?= )', r'"$ffmpeg_executable"', s)
s=re.sub(r'(?<![\w/])ffprobe -v', '"$ffprobe_executable" -v', s)
a=s.index('# Remove extension from the input file'); b=s.index('# Info about the input file',a)
s=s[:a]+'''# Derive the destination without treating dots in parent directories as an
# extension. User-specified paths are literal data, never shell fragments.
input_file_without_extension=${input_file##*/}
input_file_without_extension=${input_file_without_extension%.*}
if [[ -z $output_file ]]; then
    input_dir=${input_file%/*}
    output_file="${output_folder:-$input_dir}/$input_file_without_extension.${output_format:-mp4}"
elif [[ -n $output_format ]]; then
    output_dir=${output_file%/*}
    output_name=${output_file##*/}
    [[ $output_dir != "$output_file" ]] || output_dir=.
    output_file="$output_dir/${output_name%.*}.$output_format"
elif [[ ! ${output_file,,} =~ \.(mp4|avi|mkv|mov|wmv|flv|webm|m4v|mpeg|mpg)$ ]]; then
    output_file+=.mp4
fi
[[ $output_file == /* ]] || output_file="$PWD/$output_file"
if [[ -e $output_file || -L $output_file ]]; then
    echo "ERROR: Refusing to overwrite an existing output: $output_file" >&2
    exit 1
fi
output_stage_dir=$(mktemp -d -- "${output_file%/*}/.bvc.XXXXXXXX") || exit 1
staged_output="$output_stage_dir/video.${output_file##*.}"

''' +s[b:]
a=s.index('# Info about the input file'); b=s.index('##############\n# Audio\n',a)
s=s[:a]+'''##############
# Subtitles
##############
subtitle_cmd=()
subtitle_outputs=()
subtitle_destinations=()
: "${subtitle_extract:=extract}"
if [[ $ffprobe_usable == true ]]; then
    subtitle_codec_info=$("$ffprobe_executable" -v error -select_streams s -show_entries stream=index,codec_name -of csv=p=0 "$input_file") || exit 2
else
    subtitle_codec_info=$(fb_subtitle_codecs)
fi
if [[ $subtitle_extract == embedded ]]; then
    case ${output_file##*.} in
    mp4|mov|m4v|MP4|MOV|M4V) subtitle_encoder=mov_text ;;
    webm|WEBM) subtitle_encoder=webvtt ;;
    *) subtitle_encoder=copy ;;
    esac
    while IFS=, read -r index codec_name; do
        [[ $index =~ ^[0-9]+$ ]] || continue
        if [[ $subtitle_encoder != copy && ! $codec_name =~ ^(subrip|ass|ssa|mov_text|text|webvtt)$ ]]; then
            echo "ERROR: Cannot preserve subtitle $index ($codec_name) in this container; select MKV or extract subtitles." >&2
            exit 2
        fi
        subtitle_cmd+=(-map "0:$index")
    done <<< "$subtitle_codec_info"
    [[ ${#subtitle_cmd[@]} -eq 0 ]] || subtitle_cmd+=(-c:s "$subtitle_encoder")
elif [[ $subtitle_extract == extract || $only_extract_subtitles == 1 ]]; then
    while IFS=, read -r index codec_name; do
        [[ $index =~ ^[0-9]+$ && $codec_name == subrip ]] || continue
        language_code=und
        if [[ $ffprobe_usable == true ]]; then
            language_code=$("$ffprobe_executable" -v error -select_streams "$index" -show_entries stream_tags=language -of default=nw=1:nk=1 "$input_file") || exit 2
            [[ $language_code =~ ^[a-zA-Z]{2,3}$ ]] || language_code=und
        fi
        sidecar="$output_stage_dir/subtitle-$index.srt"
        destination="${output_file%.*}.${language_code}.s${index}.srt"
        if [[ -e $destination || -L $destination ]]; then
            echo "ERROR: Refusing to overwrite an existing subtitle: $destination" >&2
            exit 1
        fi
        subtitle_cmd+=(-map "0:$index" -c:s copy "$sidecar")
        subtitle_outputs+=("$sidecar")
        subtitle_destinations+=("$destination")
    done <<< "$subtitle_codec_info"
fi

# Publish without a check-then-overwrite race. Staging is on the destination
# filesystem, so creating a hard link is atomic and fails if the name exists.
publish_subtitles() {
    local i
    for i in "${!subtitle_outputs[@]}"; do
        ln -- "${subtitle_outputs[$i]}" "${subtitle_destinations[$i]}" || return 1
    done
}

''' +s[b:]
a=s.index('# Function to build the audio command string'); b=s.index('# Pre-process all audio streams',a)
s=s[:a]+'''# Build argv arrays; empty audio metadata must produce no audio maps.
function build_audio_cmd() {
    audio_cmd=()
    audio_inputs=()
    nr_deferred_preprocess=false
    nr_stream_channels=()
    nr_stream_layouts=()
    nr_stream_indices=()
    nr_stream_bitrates=()
    nr_stream_languages=()
    [[ $audio_handling != none ]] || { audio_cmd=(-an); return; }
    local i=0 index_audio language_audio channels nr_layout brate
    local audio_info
    audio_info=$(probe_audio_info)
    while IFS=, read -r index_audio language_audio; do
        [[ $index_audio =~ ^[0-9]+$ ]] || continue
        if [[ $audio_handling == copy ]]; then
            audio_cmd+=(-map "0:$index_audio" "-c:a:$i" copy)
        else
            channels=${audio_channels:-$(probe_audio_channels "$i" "$index_audio")}
            [[ $channels =~ ^[1-9][0-9]*$ ]] || channels=2
            brate=${audio_bitrate:-$((channels * 32))k}
            nr_layout=$(probe_audio_layout "$i" "$index_audio")
            if [[ -n $noise_audio_filter && $nr_requires_mono == true ]]; then
                nr_deferred_preprocess=true
                nr_temp_dir="$work_dir/nr"
                mkdir -p -- "$nr_temp_dir"
                nr_stream_channels+=("$channels")
                nr_stream_layouts+=("${nr_layout:-${channels}c}")
                nr_stream_indices+=("$index_audio")
                nr_stream_bitrates+=("$brate")
                nr_stream_languages+=("${language_audio:-und}")
            fi
            audio_cmd+=(-map "0:$index_audio")
            append_audio_codec audio_cmd "$i" "$brate" "$channels"
            if [[ -n $noise_audio_filter ]]; then
                audio_cmd+=("-filter:a:$i" "$noise_audio_filter")
            fi
        fi
        ((i += 1))
    done <<< "$audio_info"
}

append_audio_codec() {
    local -n target=$1
    local i=$2 brate=$3 channels=$4
    case $audio_codec in
    opus) target+=("-c:a:$i" libopus "-b:a:$i" "$brate" "-ac:a:$i" "$channels") ;;
    ac3) target+=("-c:a:$i" ac3 "-b:a:$i" "$brate" "-ac:a:$i" "$channels") ;;
    *) target+=("-c:a:$i" aac -aac_coder fast -profile:a aac_low "-b:a:$i" "$brate" "-ac:a:$i" "$channels") ;;
    esac
}

''' +s[b:]
s=s.replace('local num_streams=${#nr_stream_indices[@]}\n\techo', '''local num_streams=${#nr_stream_indices[@]}
    local -a trim_args=()
    local opt
    for ((opt=0; opt<${#option_args[@]}; opt++)); do
        case ${option_args[$opt]} in
        -ss|-sseof|-t|-to|-itsoffset)
            trim_args+=("${option_args[$opt]}" "${option_args[$((opt+1))]}")
            ((opt += 1)) ;;
        esac
    done
\techo''',1)
s=s.replace('"$ffmpeg_executable" -i "$input_file" -map "0:$index_audio"', '"$ffmpeg_executable" -nostdin "${trim_args[@]}" -i "$input_file" -map "0:$index_audio"')
s=s.replace('"$ffmpeg_executable" -i "$input_file" -vn -map "0:$index_audio"', '"$ffmpeg_executable" -nostdin "${trim_args[@]}" -i "$input_file" -vn -map "0:$index_audio" -ac "$channels"')
s=s.replace('echo "WARNING: Audio extraction failed for: ${extract_failed[*]}"','echo "ERROR: Audio extraction failed for: ${extract_failed[*]}" >&2\n        return 1')
s=s.replace('echo "WARNING: NR per-channel failed for: ${nr_failed[*]}"','echo "ERROR: NR per-channel failed for: ${nr_failed[*]}" >&2\n            return 1')
s=s.replace('local merge_inputs=""','local -a merge_inputs=()')
s=s.replace('merge_inputs+="-i $ch_dir/ch${ch}.flac "','merge_inputs+=(-i "$ch_dir/ch${ch}.flac")')
s=s.replace('''eval "$ffmpeg_executable" $merge_inputs \\
\t\t\t-filter_complex "'${merge_filter}'" \\
\t\t\t-c:a flac -y "'$temp_audio'"''','''"$ffmpeg_executable" -nostdin "${merge_inputs[@]}" \\
            -filter_complex "$merge_filter" \\
            -c:a flac -y "$temp_audio"''')
s=s.replace('''for pid in "${merge_pids[@]}"; do
\t\twait "$pid"
\tdone''','''local merge_failed=0
    for pid in "${merge_pids[@]}"; do
        wait "$pid" || merge_failed=1
    done
    [[ $merge_failed == 0 ]] || return 1''')
a=s.index('function build_encoder_commands()'); b=s.index('# If gpu = auto',a)
block=s[a:b]
block=block.replace('general_params="-i \\"$input_file\\" $audio_inputs -map 0:v:0 $audio_cmd"','general_params=(-i "$input_file" "${audio_inputs[@]}" -map 0:v:0 "${audio_cmd[@]}")')
for line in list(block.splitlines()):
    m=re.match(r'(\s*)(general_params|copy_video|encoder_[a-z]+)="(.*)"$', line)
    if not m: continue
    indent,name,template=m.groups()
    template=template.replace('\\"','"')
    tokens=shlex.split(template)
    out=[]
    for t in tokens:
        if t in ('$general_params','$audio_inputs','$audio_cmd'):
            out.append('"${'+t[1:]+'[@]}"')
        else:
            out.append('"'+t.replace('\\','\\\\').replace('"','\\"')+'"' if '$' in t else shlex.quote(t))
    new=indent+name+'=('+' '.join(out)+')'
    block=block.replace(line,new)
block=block.replace('copy_video=("${general_params[@]}" -c:v copy)','''if [[ $subtitle_extract == embedded ]]; then
        general_params+=("${subtitle_cmd[@]}")
    fi
    copy_video=("${general_params[@]}" -c:v copy)''')
block=re.sub(r'encoder_nvenc=\([^\n]*-c:v vp9_nvenc[^\n]*\)', 'encoder_nvenc=()',block)
s=s[:a]+block+s[b:]
a=s.index('# Generic options for ffmpeg'); b=s.index('# Function to attempt the conversion',a)
s=s[:a]+'''# Encode to an owned staging file. Existing user files are never overwritten.
ffmpeg_generic_options=(-y "$staged_output")
case ${output_file##*.} in
mp4|mov|m4v|MP4|MOV|M4V) ffmpeg_generic_options=(-movflags +faststart -y "$staged_output") ;;
esac
if [[ $only_extract_subtitles == 1 ]]; then
    if [[ ${#subtitle_cmd[@]} -eq 0 ]]; then
        echo "ERROR: No extractable subtitles found" >&2
        exit 2
    fi
    "$ffmpeg_executable" -nostdin -i "$input_file" "${subtitle_cmd[@]}" || exit $?
    publish_subtitles
    exit $?
fi

# Keep human-readable diagnostics; only argv is executed.
run_ffmpeg() {
    printf 'Running command:'
    printf ' %q' "$ffmpeg_executable" -nostdin "$@"
    printf '\\n'
    "$ffmpeg_executable" -nostdin "$@"
}

''' +s[b:]
a=s.index('function attempt_conversion()'); block=s[a:]
block=block.replace('''# This function now re-initializes all hardware-specific variables to ensure a clean state''','''# Reset all hardware state before a retry.
    encoder_format=()
    decoder_format=()
    init_hardware=()
    force_8bit_color=""''')
for line in list(block.splitlines()):
    m=re.match(r'''(\s*)(init_qsv|init_vaapi|init_vulkan|init_nvidia|decoder_qsv|decoder_vaapi|decoder_vulkan|decoder_cuda)=(['"])(.*)\3$''',line)
    if not m: continue
    indent,name,_,template=m.groups()
    tokens=shlex.split(template)
    out=['"'+t+'"' if '$' in t else shlex.quote(t) for t in tokens]
    block=block.replace(line,indent+name+'=('+' '.join(out)+')')
block=re.sub(r'(encoder_format|decoder_format|init_hardware)="?\$(encoder_\w+|decoder_\w+|init_\w+|copy_video)"?(?=\n)',lambda m:m[1]+'=("${'+m[2]+'[@]}")',block)
block=block.replace('encoder_vaapi="$general_params -c:v hevc_vaapi -global_quality $qp_hevc -rc_mode CQP"','encoder_vaapi=("${general_params[@]}" -c:v hevc_vaapi -global_quality "$qp_hevc" -rc_mode CQP)')
oldstart=block.index('\tif [[ -n $force_encoder ]]; then');oldend=block.index('\n\t#####################################################',oldstart)
block=block[:oldstart]+'''    if [[ -n $force_encoder ]]; then
        case $force_encoder in
        nvenc) detected_encoder_var=encoder_nvenc; init_hardware=("${init_nvidia[@]}"); scale_filter_name=scale_cuda ;;
        vaapi) detected_encoder_var=encoder_vaapi; init_hardware=("${init_vaapi[@]}"); scale_filter_name=scale_vaapi ;;
        qsv) detected_encoder_var=encoder_qsv; init_hardware=("${init_qsv[@]}"); scale_filter_name=scale_qsv ;;
        vulkan) detected_encoder_var=encoder_vulkan; init_hardware=("${init_vulkan[@]}"); scale_filter_name=scale_vulkan ;;
        software) detected_encoder_var=encoder_software ;;
        *) echo "ERROR: Unsupported encoder backend: $force_encoder" >&2; exit_code=2; return ;;
        esac
        local -n requested_encoder=$detected_encoder_var
        encoder_format=("${requested_encoder[@]}")
    fi
    if [[ -n $force_decoder ]]; then
        case $force_decoder in
        cuda) decoder_format=("${decoder_cuda[@]}"); init_hardware=("${init_nvidia[@]}") ;;
        vaapi) decoder_format=("${decoder_vaapi[@]}"); init_hardware=("${init_vaapi[@]}") ;;
        qsv) decoder_format=("${decoder_qsv[@]}"); init_hardware=("${init_qsv[@]}") ;;
        vulkan) decoder_format=("${decoder_vulkan[@]}"); init_hardware=("${init_vulkan[@]}") ;;
        software) decoder_format=(); gpu_partial=1 ;;
        *) echo "ERROR: Unsupported decoder backend: $force_decoder" >&2; exit_code=2; return ;;
        esac
    fi
    if [[ $force_software == 1 || ${#encoder_format[@]} -eq 0 ]]; then
        encoder_format=("${encoder_software[@]}")
        detected_encoder_var=encoder_software
        init_hardware=()
        decoder_format=()
    fi
    if [[ $force_copy_video == 1 ]]; then
        encoder_format=("${copy_video[@]}")
        detected_encoder_var=copy_video
        init_hardware=()
        decoder_format=()
    fi
''' +block[oldend:]
block=block.replace('video_filter_chain="-vf $force_8bit_color"','video_filter_chain="-vf ${force_8bit_color%,}"')
old='''if eval "$ffmpeg_executable" -y -i \\"$input_file\\" $subtitle_cmd 2>/dev/null; then'''
block=block.replace(old,'''if run_ffmpeg -y -i "$input_file" "${subtitle_cmd[@]}"; then''')
block=block.replace('echo "Warning: Subtitle extraction failed (non-critical, continuing with conversion)"','echo "ERROR: Subtitle extraction failed" >&2\n            exit_code=2\n            return')
block=block.replace('[[ $subtitle_extract == "extract" && -n "$subtitle_cmd"', '[[ $subtitle_extract == "extract" && ${#subtitle_cmd[@]} -gt 0')
block=block.replace('local saved_audio_cmd=""','local -a saved_audio_cmd=()').replace('local saved_audio_inputs=""','local -a saved_audio_inputs=()').replace('local saved_ffmpeg_generic=""','local -a saved_ffmpeg_generic=()')
block=block.replace('nr_video_file=$(mktemp --suffix=.mkv)','nr_video_file="$output_stage_dir/nr-video.mkv"')
for lhs,rhs in [('saved_audio_cmd','audio_cmd'),('saved_audio_inputs','audio_inputs'),('saved_ffmpeg_generic','ffmpeg_generic_options'),('audio_cmd','saved_audio_cmd'),('audio_inputs','saved_audio_inputs'),('ffmpeg_generic_options','saved_ffmpeg_generic')]:
    block=block.replace(f'{lhs}="${rhs}"',f'{lhs}=("${{{rhs}[@]}}")')
block=block.replace('audio_cmd="-an"','audio_cmd=(-an)').replace('audio_inputs=""','audio_inputs=()')
block=block.replace('encoder_format="${!detected_encoder_var}"','local -n selected_encoder=$detected_encoder_var\n            encoder_format=("${selected_encoder[@]}")')
block=block.replace('ffmpeg_generic_options="-y \\"$nr_video_file\\""','ffmpeg_generic_options=(-y "$nr_video_file")')
block=block.replace('eval "$ffmpeg_executable" "$options" "$decoder_format" "$encoder_format" "$ffmpeg_generic_options"','run_ffmpeg "${option_args[@]}" "${decoder_format[@]}" "${encoder_format[@]}" "${ffmpeg_generic_options[@]}"')
block=block.replace('''# After copy always exit (when not in parallel NR mode)
\t\t\texit $exit_code''','''# Return to common publication/error handling after a successful copy.
            return''')
block=block.replace('"$encoder_format" != "$encoder_software"','"$detected_encoder_var" != "encoder_software"').replace('"$encoder_format" == "$encoder_software"','"$detected_encoder_var" == "encoder_software"')
for chain,args in [('video_filter_chain','"${init_hardware[@]}" "${option_args[@]}" "${decoder_format[@]}" "${encoder_format[@]}"'),('partial_video_filter_chain','"${init_hardware[@]}" "${option_args[@]}" "${encoder_format[@]}"'),('software_filter_chain','"${option_args[@]}" "${encoder_software[@]}"')]:
    pat=r'eval "\$ffmpeg_executable" [^\n]*"\$'+chain+r'" "\$ffmpeg_generic_options"'
    repl=f'''local -a filters=()
        [[ -z ${chain} ]] || filters=(-vf "${{{chain}#-vf }}")
        run_ffmpeg {args} "${{filters[@]}}" "${{ffmpeg_generic_options[@]}}"'''
    block,count=re.subn(pat,lambda m:repl,block)
    assert count==1,(chain,count)
for backend in ['nvenc','vulkan','vaapi','qsv']:
    block=block.replace('${encoder_format,,} =~ '+backend,'$detected_encoder_var == encoder_'+backend)
block=block.replace('|| "$gpu_partial" == "1" ]]; then','|| "$gpu_partial" == "1" ) && "$detected_encoder_var" != "encoder_software" && "$force_copy_video" != "1" ]]; then')
block=block.replace('if [[ ($exit_code != 0 && $exit_code != 255 && $force_software != 1 && $force_copy_video != 1) || "$gpu_partial"', 'if [[ ( ($exit_code != 0 && $exit_code != 255 && $force_software != 1 && $force_copy_video != 1) || "$gpu_partial"')
block=block.replace('''\telse
\t\texit_code=1
\tfi

\t# Stage 2''','''\telif [[ "$force_copy_video" != "1" ]]; then
        exit_code=1
    fi

\t# Stage 2''')
ma=block.index('\t\t\t\t# Build mux command:'); mb=block.index('\n\t\t\t\tif [[ $exit_code -eq 0 ]]; then',ma)
block=block[:ma]+'''                local -a mux_inputs=(-i "$nr_video_file")
                local -a mux_maps=(-map 0:v:0 -map '0:s?' -c:s copy)
                local i input_idx temp_audio
                local missing_audio=false
                for i in "${!nr_stream_indices[@]}"; do
                    temp_audio="$nr_temp_dir/audio_${i}.flac"
                    if [[ ! -s $temp_audio ]]; then
                        echo "ERROR: NR output missing for stream $i" >&2
                        missing_audio=true
                        break
                    fi
                    input_idx=$((i + 1))
                    mux_inputs+=(-i "$temp_audio")
                    mux_maps+=(-map "$input_idx:a:0" "-metadata:s:a:$i" "language=${nr_stream_languages[$i]}")
                    append_audio_codec mux_maps "$i" "${nr_stream_bitrates[$i]}" "${nr_stream_channels[$i]}"
                done
                if [[ $missing_audio == true ]]; then
                    exit_code=1
                else
                    run_ffmpeg "${mux_inputs[@]}" "${mux_maps[@]}" -c:v copy "${ffmpeg_generic_options[@]}"
                    exit_code=$?
                fi
''' +block[mb:]
s=s[:a]+block
s='\n'.join(line for line in s.splitlines() if not ('echo "Running command:' in line))+'\n'
s+='''
# Final publication fails rather than overwriting a name created concurrently.
if [[ ! -s $staged_output ]]; then
    echo "ERROR: Conversion produced no output" >&2
    exit 1
fi
publish_subtitles || exit 1
ln -- "$staged_output" "$output_file" || exit 1
printf 'Output file: %s\\n' "$output_file"
'''
f.write_text(s)
