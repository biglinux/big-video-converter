"""Real subprocess supervision with lightweight UI test doubles and real GLib."""
from collections import deque
import logging
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

gi = pytest.importorskip('gi')
gi.require_version('Adw', '1')
from gi.repository import GLib

from conftest import CLI
from queue_manager import QueueManagerMixin
from utils import conversion
from utils.media_validation import ConversionResult
from utils.segment_batch import start_segment_batch


def pump_until(predicate, timeout=15):
    context=GLib.MainContext.default()
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        for _ in range(100):
            if not context.pending():break
            context.iteration(False)
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for main-loop completion')


class Widget:
    def __init__(self):self.value=None
    def set_text(self,value):
        assert threading.current_thread() is threading.main_thread()
        self.value=value
    set_sensitive=set_text


class Row:
    def __init__(self,owner,source,process):
        self.owner=owner;self.process=process;self.input_file=source
        self.conversion_id=len(owner.rows);self.status='active';self._cancelled=False
        self.cmd_text=Widget();self.cancel_button=Widget();self.lines=[]
    def add_output_text(self,text):
        assert threading.current_thread() is threading.main_thread()
        self.lines.append(text)
    def update_status(self,status):
        assert threading.current_thread() is threading.main_thread()
        self.status_text=status
    def update_progress(self,progress):
        assert threading.current_thread() is threading.main_thread()
        self.progress=progress
    def mark_success(self):
        self.status='completed';self.owner.mark_conversion_complete(self.conversion_id,True)
    def mark_failure(self):
        self.status='failed';self.owner.mark_conversion_complete(self.conversion_id,False)
    def mark_cancelled(self):self.status='cancelled'
    def was_cancelled(self):return self._cancelled


class Page:
    def __init__(self):self.rows=[];self.completions=[];self.summary=0;self.pending=[]
    def add_conversion(self,title,source,process):
        assert threading.current_thread() is threading.main_thread()
        row=Row(self,source,process);self.rows.append(row);return row
    def mark_conversion_complete(self,identity,success):self.completions.append((identity,success))
    def finish_pending(self,path,**kwargs):self.pending.append((path,kwargs))
    def show_completion_summary(self):self.summary+=1


class App:
    def __init__(self):
        self.progress_page=Page();self.conversions_running=0
        self.notifications=[];self.errors=[];self.completed_conversions=[]
        self.delete_original_after_conversion=False;self.system_notifications=[]
        self.conversion_queue=deque()
    def conversion_completed(self,success,**kwargs):
        assert threading.current_thread() is threading.main_thread()
        self.notifications.append((success,kwargs))
    def show_error_dialog(self,*message):self.errors.append(message)
    def send_system_notification(self,title,body):
        assert threading.current_thread() is threading.main_thread()
        self.system_notifications.append((title,body))


def test_real_cli_supervisor_validates_and_finishes_once(media,tmp_path,cli_env):
    app=App();out=tmp_path/'out.mp4';env={**cli_env,'output_file':str(out)}
    conversion.run_with_progress_dialog(app,[str(CLI),str(media['video'])], 'test',
        str(media['video']),False,env,job_id='a')
    pump_until(lambda:len(app.notifications)==1)
    assert app.notifications==[(True,{'file_path':str(media['video']),'job_id':'a'})]
    assert app.conversions_running==0
    assert app.progress_page.completions==[(0,True)]
    assert app.progress_page.rows[0].process is None
    assert out.exists()


def test_log_opens_with_the_source_codec_and_pixel_format(media,tmp_path,cli_env):
    app=App();out=tmp_path/'logged.mkv'
    conversion.run_with_progress_dialog(app,[str(CLI),str(media['video'])],'test',
        str(media['video']),False,{**cli_env,'output_file':str(out)},job_id='log')
    pump_until(lambda:bool(app.notifications))
    lines=''.join(app.progress_page.rows[0].lines)
    assert 'codec=h264' in lines and 'pixel format=yuv420p' in lines


def test_finished_job_notifies_the_user(media,tmp_path,cli_env):
    app=App();out=tmp_path/'notified.mkv'
    conversion.run_with_progress_dialog(app,[str(CLI),str(media['video'])],'test',
        str(media['video']),False,{**cli_env,'output_file':str(out)},job_id='n')
    pump_until(lambda:bool(app.notifications))
    assert len(app.system_notifications)==1
    assert app.system_notifications[0][0]=='Conversion Complete'
    assert not app.errors


def test_failed_job_notifies_and_reports_only_outside_a_queue(media,tmp_path,cli_env):
    for queued, dialogs in ((False,1), (True,0)):
        app=App();out=tmp_path/f'fail{queued}.mkv'
        if queued:app.conversion_queue.append('pending.mkv')
        conversion.run_with_progress_dialog(app,['/usr/bin/false'],'test',
            str(media['video']),False,{**cli_env,'output_file':str(out)},job_id='f')
        pump_until(lambda:bool(app.notifications))
        assert app.notifications[0][0] is False
        assert len(app.system_notifications)==1 and len(app.errors)==dialogs


def test_real_conversion_deletes_the_original_after_validation(media,tmp_path,cli_env):
    source=tmp_path/'source.mp4';shutil.copyfile(media['video'],source)
    app=App();out=tmp_path/'converted.mkv'
    conversion.run_with_progress_dialog(app,[str(CLI),str(source)],'test',str(source),
        True,{**cli_env,'output_file':str(out)},job_id='del')
    pump_until(lambda:bool(app.notifications))
    assert app.notifications[0][0] is True
    assert out.exists() and not source.exists()
    assert any('Original deleted' in line for line in app.progress_page.rows[0].lines)


def test_output_shorter_than_requested_keeps_the_original(media,tmp_path,cli_env):
    """A file that plays but misses part of the requested interval is still a
    success for the user; it just never authorizes deleting the source."""
    source=tmp_path/'source.mp4';shutil.copyfile(media['video'],source)
    app=App();out=tmp_path/'short.mp4'
    conversion.run_with_progress_dialog(app,[str(CLI),str(source)],'test',str(source),
        True,{**cli_env,'output_file':str(out)},segment_duration=30,job_id='short')
    pump_until(lambda:bool(app.notifications))
    assert app.notifications[0][0] is True
    assert out.exists() and source.exists()
    assert any('duration not verified' in line for line in app.progress_page.rows[0].lines)


@pytest.mark.parametrize('exitcode', [0,1])
def test_partial_output_never_success_or_deletion(media,tmp_path,cli_env,exitcode,monkeypatch):
    app=App();out=tmp_path/'partial.mp4';calls=[]
    monkeypatch.setattr(conversion,'remove_original',lambda *a,**k:calls.append(a))
    script='import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b"partial");sys.exit(int(sys.argv[2]))'
    conversion.run_with_progress_dialog(app,['/usr/bin/python3','-c',script,str(out),str(exitcode)],
        'test',str(media['video']),True,{**cli_env,'output_file':str(out)},job_id='p')
    pump_until(lambda:bool(app.notifications))
    assert len(app.notifications)==1 and app.notifications[0][0] is False
    assert not calls and app.conversions_running==0
    assert app.progress_page.rows[0].status=='failed'


def test_explicit_false_never_inherits_delete_preference(media,tmp_path,cli_env,monkeypatch):
    app=App();app.delete_original_after_conversion=True;calls=[]
    monkeypatch.setattr(conversion,'remove_original',lambda *a,**k:calls.append(a))
    out=tmp_path/'kept.mp4'
    conversion.run_with_progress_dialog(app,[str(CLI),str(media['video'])],'test',
        str(media['video']),False,{**cli_env,'output_file':str(out)},job_id='keep')
    pump_until(lambda:bool(app.notifications))
    assert app.notifications[0][0] and not calls


@pytest.mark.parametrize('closed_pipes', [False,True])
def test_cancel_silent_process_even_after_pipe_eof(tmp_path,cli_env,closed_pipes):
    app=App();event=threading.Event()
    script='import os,time;'+('os.close(1);os.close(2);' if closed_pipes else '')+'time.sleep(30)'
    conversion.run_with_progress_dialog(app,['/usr/bin/python3','-c',script], 'test',
        env_vars={**cli_env,'output_file':str(tmp_path/'never.mp4')},
        cancel_event=event,job_id='cancel')
    process=app.progress_page.rows[0].process
    GLib.timeout_add(100,lambda:(event.set(),False)[1])
    started=time.monotonic()
    pump_until(lambda:bool(app.notifications),timeout=5)
    assert time.monotonic()-started<4
    assert process.poll() is not None
    assert app.progress_page.rows[0].status=='cancelled'
    assert app.notifications[0][0] is False and app.conversions_running==0


def test_cancel_kills_descendants_holding_pipes(tmp_path,cli_env):
    app=App();event=threading.Event();marker=tmp_path/'descendant'
    grandchild=f'import time,pathlib;time.sleep(1);pathlib.Path({str(marker)!r}).touch()'
    script=f'import subprocess,time;subprocess.Popen(["/usr/bin/python3","-c",{grandchild!r}]);time.sleep(30)'
    conversion.run_with_progress_dialog(app,['/usr/bin/python3','-c',script],'test',
        env_vars={**cli_env,'output_file':str(tmp_path/'out.mp4')},cancel_event=event,job_id='tree')
    GLib.timeout_add(100,lambda:(event.set(),False)[1])
    pump_until(lambda:bool(app.notifications),timeout=5)
    time.sleep(1.1)
    assert not marker.exists()


def test_deadline_works_before_eof(tmp_path,cli_env,monkeypatch):
    app=App();monkeypatch.setattr(conversion,'MAX_CONVERSION_SECONDS',.2)
    conversion.run_with_progress_dialog(app,['/usr/bin/python3','-c','import time;time.sleep(30)'],'test',
        env_vars={**cli_env,'output_file':str(tmp_path/'out.mp4')},job_id='deadline')
    pump_until(lambda:bool(app.notifications),timeout=5)
    assert app.notifications[0][0] is False and app.conversions_running==0
    assert 'time limit' in app.completed_conversions[0].get('error','') or any(
        'time limit' in line for line in app.progress_page.rows[0].lines)


def test_start_failure_completes_row_once(tmp_path,cli_env):
    app=App()
    result=conversion.run_with_progress_dialog(app,['/no/such/executable'],'test',
        env_vars={**cli_env,'output_file':str(tmp_path/'out.mp4')},job_id='start')
    assert isinstance(result,ConversionResult) and not result.success
    assert app.notifications==[(False,{'file_path':None,'job_id':'start'})]
    assert app.conversions_running==0 and app.progress_page.rows[0].status=='failed'


def test_wait_on_main_thread_rejected():
    with pytest.raises(RuntimeError,match='GTK thread'):
        conversion.run_with_progress_dialog(App(),['echo'],'test',wait_for_completion=True)


def test_updates_are_bounded_and_one_shot():
    owner=Page();row=owner.add_conversion('a','a',None);updates=conversion._Updates(row)
    for _ in range(20000):updates.push(text='x'*9000,progress=.4)
    assert len(updates.lines)==256 and max(map(len,updates.lines))==8192
    assert updates.flush() is False
    updates.close();updates.push(text='after close')
    assert not updates.lines


class QueueApp(QueueManagerMixin):
    def __init__(self):
        self.logger=logging.getLogger('queue-test');self.completion_lock=threading.Lock()
        self.conversions_lock=threading.Lock();self._processing_completion=False
        self.active_conversions=[];self.gpu_slots=deque();self.conversion_queue=deque()
        self.is_cancellation_requested=False;self.currently_converting=True
        self.progress_page=Page();self.next_calls=0
        self.header_bar=SimpleNamespace(set_buttons_sensitive=lambda value:None)
    def process_next_in_queue(self):self.next_calls+=1;return False


def test_queue_completion_matches_identity_and_ignores_duplicates():
    app=QueueApp()
    app.active_conversions=[{'file_path':'same','job_id':identity,'gpu_slot':{'name':identity}}
                            for identity in ['first','second']]
    app.conversion_completed(True,file_path='same')  # Ambiguous: do not guess.
    app.conversion_completed(False)  # No identity: do not release oldest job.
    assert len(app.active_conversions)==2
    app.conversion_completed(True,job_id='first',file_path='same')
    app.conversion_completed(True,job_id='first',file_path='same')
    assert [v['job_id'] for v in app.active_conversions]==['second']
    assert list(app.gpu_slots)==[{'name':'first'}]
    assert len(app.progress_page.pending)==1


def test_cancel_all_releases_slots_only_when_each_job_finishes():
    app=QueueApp();app.is_cancellation_requested=True
    app.active_conversions=[{'file_path':i,'job_id':i,'gpu_slot':None} for i in ['a','b']]
    app.conversion_completed(False,job_id='a')
    assert app.is_cancellation_requested and len(app.active_conversions)==1
    app.conversion_completed(False,job_id='b')
    assert not app.is_cancellation_requested and not app.active_conversions
    assert app.progress_page.summary==1


def batch_context(media,tmp_path,cli_env,mode):
    source=str(media['multi'])
    return {'input_file':source,'input_basename':"apostrophe's film",'input_ext':'.mkv',
        'output_ext':'.mkv','full_output_path':str(tmp_path/"apostrophe's joined.mkv"),
        'output_folder':str(tmp_path),'trim_segments':[{'start':.5,'end':1.5},{'start':2,'end':2.5}],
        'output_mode':mode,'env_vars':cli_env.copy(),'cmd':[str(CLI),source],
        'delete_original':False,'job_id':'batch','cancel_event':threading.Event()}


@pytest.mark.parametrize('mode',['split','join'])
def test_real_segment_batch_has_one_parent_completion(media,tmp_path,cli_env,mode):
    app=App();page=SimpleNamespace(app=app,_format_time_ffmpeg=lambda v:f'{v:.6f}')
    context=batch_context(media,tmp_path,cli_env,mode)
    assert start_segment_batch(page,context)
    pump_until(lambda:bool(app.notifications),timeout=25)
    assert app.notifications==[(True,{'file_path':str(media['multi']),'job_id':'batch'})], app.progress_page.rows[0].lines
    assert app.conversions_running==0
    assert app.progress_page.completions==[(0,True)]
    outputs=app.completed_conversions[-1]['output_files']
    assert len(outputs)==(2 if mode=='split' else 1)
    assert all(Path(f).is_file() for f in outputs)
    assert not list(tmp_path.glob('.bvc-segments-*'))


def test_first_failed_segment_prevents_following_stages(media,tmp_path,cli_env,monkeypatch):
    import utils.segment_batch as module
    calls=[]
    def fail(*args,**kwargs):
        calls.append(args);return ConversionResult(False,1,error='injected failure')
    monkeypatch.setattr(module,'run_with_progress_dialog',fail)
    app=App();page=SimpleNamespace(app=app,_format_time_ffmpeg=str)
    context=batch_context(media,tmp_path,cli_env,'join')
    start_segment_batch(page,context);pump_until(lambda:bool(app.notifications))
    assert len(calls)==1
    assert app.notifications[0][0] is False
    assert not list(tmp_path.glob('*.mkv'))


def test_cancel_between_segments_stops_the_batch(media,tmp_path,cli_env,monkeypatch):
    import utils.segment_batch as module
    calls=[];context=batch_context(media,tmp_path,cli_env,'join')
    def one(*args,**kwargs):
        calls.append(args);context['cancel_event'].set();return ConversionResult(True,0)
    monkeypatch.setattr(module,'run_with_progress_dialog',one)
    app=App();page=SimpleNamespace(app=app,_format_time_ffmpeg=str)
    start_segment_batch(page,context);pump_until(lambda:bool(app.notifications))
    assert len(calls)==1 and app.notifications[0][0] is False
    assert app.progress_page.rows[0].status=='cancelled'

@pytest.mark.parametrize('mode',['split','join'])
def test_subtitle_only_batch_never_converts_or_removes_video(media,tmp_path,cli_env,mode,monkeypatch):
    import utils.segment_batch as module
    def forbidden(*a,**k):raise AssertionError('Subtitle extraction must not encode/remove video')
    monkeypatch.setattr(module,'remove_original',forbidden)
    monkeypatch.setattr(module,'run_with_progress_dialog',forbidden)
    app=App();page=SimpleNamespace(app=app,_format_time_ffmpeg=str)
    context=batch_context(media,tmp_path,cli_env,mode)
    context['env_vars']['only_extract_subtitles']='1';context['delete_original']=True
    start_segment_batch(page,context);pump_until(lambda:bool(app.notifications))
    assert app.notifications[0][0] is True and app.conversions_running==0
    outputs=app.completed_conversions[-1]['output_files']
    assert len(outputs)==(4 if mode=='split' else 2)
    assert all(Path(path).suffix=='.srt' for path in outputs)
