"""Exercise the actual NR orchestration with anull, not a simulated GTCRN model.

The Bash function is taken directly from the application. The expensive native
plugin is replaced by a pass-through filter; stream extraction/channel merge and
error propagation still use real FFmpeg processes. No plugin quality is asserted.
"""
from pathlib import Path
import subprocess

import pytest

from conftest import CLI
from utils.media_validation import media_duration, probe_media


def nr_function():
    text=CLI.read_text()
    start=text.index('function preprocess_audio_nr() {')
    end=text.index('\n}\n',start)+2
    return text[start:end]


def invoke(media,tmp_path,channels,filter_name='anull',layout=None,executable='ffmpeg'):
    script=nr_function()+r'''
input_file=$1
nr_temp_dir=$2
nr_stream_indices=(1)
nr_stream_channels=("$3")
nr_stream_layouts=("$4")
noise_audio_filter=$5
ffmpeg_executable=$6
option_args=(-ss 1 -t 0.75)
preprocess_audio_nr
'''
    return subprocess.run(['bash','-c',script,'test',str(media['video']),str(tmp_path),
        str(channels),layout or ('mono' if channels==1 else 'stereo'),filter_name,executable],
        capture_output=True,text=True,timeout=20)


@pytest.mark.parametrize('channels',[1,2])
def test_processing_preserves_requested_window_and_channels(media,tmp_path,channels):
    result=invoke(media,tmp_path,channels)
    assert result.returncode==0,result.stdout+result.stderr
    data=probe_media(str(tmp_path/'audio_0.flac'))
    assert data['streams'][0]['channels']==channels
    assert abs(media_duration(data)-.75)<.03


def test_extraction_error_is_not_hidden_by_final_echo(media,tmp_path):
    result=invoke(media,tmp_path,2,executable='/bin/false')
    assert result.returncode!=0 and 'Audio extraction failed' in result.stderr


def test_one_channel_filter_failure_aborts_processing(media,tmp_path):
    result=invoke(media,tmp_path,2,filter_name='no_such_filter')
    assert result.returncode!=0 and 'NR per-channel failed' in result.stderr


def test_merge_failure_returns_failure(media,tmp_path):
    result=invoke(media,tmp_path,2,layout='no_such_layout')
    assert result.returncode!=0
