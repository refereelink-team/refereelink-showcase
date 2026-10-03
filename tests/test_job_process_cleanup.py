"""Real POSIX process-group regression tests; execute on the remote host."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

import pytest

from showcase.jobs import JobManager


def _child_running(pid: int) -> bool:
    status = Path(f'/proc/{pid}/status')
    if status.exists():
        # An orphan may briefly await reaping; a zombie cannot retain resources.
        state = next(line for line in status.read_text().splitlines() if line.startswith('State:'))
        return state.split()[1] != 'Z'
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until(predicate, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.mark.skipif(os.name != 'posix', reason='Job runner uses POSIX process groups')
@pytest.mark.parametrize('leader_already_exited', [False, True])
def test_terminate_process_cleans_descendant_ignoring_sigterm(tmp_path, leader_already_exited):
    ready = tmp_path / 'child-ready.pid'
    child_code = (
        'import os,signal,time; from pathlib import Path; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'Path({str(ready)!r}).write_text(str(os.getpid())); '
        'time.sleep(60)'
    )
    parent_code = (
        'import subprocess,sys,time; '
        f'subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
        'time.sleep(60)'
    )
    leader = subprocess.Popen([sys.executable, '-c', parent_code], start_new_session=True)
    child_pid = None
    try:
        assert _wait_until(ready.exists), 'Child did not install its SIGTERM handler'
        child_pid = int(ready.read_text())
        assert _child_running(child_pid)
        if leader_already_exited:
            os.kill(leader.pid, signal.SIGTERM)
            leader.wait(timeout=3)
            assert _child_running(child_pid), 'Test requires a surviving group descendant'
        JobManager._terminate_process(leader)
        assert leader.poll() is not None, 'Group leader was not reaped'
        assert _wait_until(lambda: not _child_running(child_pid)), (
            'A child ignoring SIGTERM remains alive after the leader exits'
        )
    finally:
        # Always clean only this test's own group, including on regression failure.
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        leader.wait(timeout=3)
        if child_pid is not None:
            _wait_until(lambda: not _child_running(child_pid))


def test_missing_queued_input_fails_persistently_and_worker_continues(tmp_path):
    import json
    from showcase.catalog import MediaCatalog
    from showcase.settings import Settings

    media = tmp_path / 'media'
    (media / 'input').mkdir(parents=True)
    first_input = media / 'input/calibration.mp4'
    first_input.write_bytes(b'queue boundary only')
    (media / 'input/foul-1.mp4').write_bytes(b'queue boundary only')
    core = tmp_path / 'core'
    (core / 'tools').mkdir(parents=True)
    (core / 'tools/run_night_ablation.py').write_text('# Must never execute in this test')
    settings = Settings(
        media_root=media, state_root=tmp_path / 'state', upstream='http://upstream.invalid',
        core_root=core, core_python=tmp_path / 'intentionally-missing-python',
        player_model=tmp_path / 'player.pt', pitch_model=tmp_path / 'pitch.pt',
        foul_model=tmp_path / 'foul.pt', foul_code=tmp_path / 'foul-code',
        bundle=tmp_path / 'bundle.npz', enforce_cuda=False,
    )
    manager = JobManager(settings, MediaCatalog(media))
    first = manager.submit('tracking', 'calibration')
    second = manager.submit('foul', 'foul-1')
    first_input.unlink()
    manager.start()
    try:
        assert _wait_until(lambda: all(manager.get(job['id'])['status'] == 'failed'
                                     for job in (first, second)))
        assert manager.worker.is_alive(), 'One missing input terminated the GPU queue worker'
        assert _wait_until(lambda: manager.queue.unfinished_tasks == 0)
        assert 'Media not found' in manager.get(first['id'])['error']
        for job in (first, second):
            persisted = json.loads((settings.state_root / f"job-{job['id']}.json").read_text())
            assert persisted['status'] == 'failed'
            assert persisted['finished_at'] >= persisted['started_at']
    finally:
        manager.stop()


def test_refined_atomic_jsonl_replacement_does_not_double_count_progress(tmp_path):
    from types import SimpleNamespace
    import threading

    states = tmp_path / 'frame-states.jsonl'
    states.write_text('{}\n{}\n')
    replacement = tmp_path / 'replacement.jsonl'
    replacement.write_text('{"refined":true}\n' * 3)
    observed = []
    step = 0

    def poll():
        nonlocal step
        step += 1
        if step == 2:
            replacement.replace(states)
        return None if step <= 2 else 0

    manager = JobManager.__new__(JobManager)
    manager.settings = SimpleNamespace(job_timeout_s=10)
    manager.catalog = SimpleNamespace(clip=lambda case_id: {'decoded_frames': 3})
    manager.stopped = SimpleNamespace(wait=lambda seconds: False)
    manager.lock = threading.RLock()
    manager._save = lambda job: observed.append(job['processed_frames'])
    job = {'case_id': 'fixture'}
    assert manager._wait(SimpleNamespace(poll=poll, returncode=0), tmp_path, job) == 0
    assert observed == [2, 3]
