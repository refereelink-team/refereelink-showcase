"""Startup-scheduler boundary tests; fixtures never execute the model runner."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from showcase.catalog import MediaCatalog
from showcase.jobs import JobManager
from showcase.settings import Settings

FINGERPRINT = {'config_sha256': '1' * 64, 'model_sha256': '2' * 64,
               'core_manifest_sha256': '3' * 64, 'external_source_sha256': '4' * 64}


def qualify(manager, monkeypatch):
    qualification = {**FINGERPRINT, 'detection_profile': 'mvit-contact-v3',
                     'status': 'passed', 'scope': 'development_clip'}
    monkeypatch.setattr(manager.foul_validation, 'qualification', lambda: qualification)
    monkeypatch.setattr(manager.foul_validation, 'snapshot',
                        lambda profile, evidence=None: dict(FINGERPRINT))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    media, core = tmp_path / 'media', tmp_path / 'core'
    (media / 'input').mkdir(parents=True)
    (core / 'tools').mkdir(parents=True)
    (core / 'tools/run_night_ablation.py').write_text('# Fixture; never executed')
    for case_id in ('foul-1', 'foul-2'):
        (media / 'input' / (case_id + '.mp4')).write_bytes(('source ' + case_id).encode())
    settings = Settings(media_root=media, state_root=tmp_path / 'state',
                        upstream='http://unused.invalid', core_root=core,
                        core_python=Path('/never/executed'), player_model=tmp_path / 'players.pt',
                        pitch_model=tmp_path / 'pitch.pt', foul_model=tmp_path / 'foul.pt',
                        foul_code=tmp_path / 'author-code', bundle=tmp_path / 'bundle.npz',
                        enforce_cuda=True, foul_detection_profile='mvit-contact-v3')
    result = JobManager(settings, MediaCatalog(media))
    qualify(result, monkeypatch)
    return result


class NativeResults:
    """Controllable descriptor boundary, without fabricated runtime detections."""

    def __init__(self, manager):
        self.manager = manager
        self.calls = []
        self.invalid = False

    def result(self, result_id):
        self.calls.append(result_id)
        if self.invalid:
            raise HTTPException(409, 'Invalid source-bound native record')
        job_id = result_id.removesuffix('-frame-states')
        job = self.manager.get(job_id)
        return SimpleNamespace(descriptor={
            'id': result_id, 'case_id': job['case_id'], 'revision': 'a' * 64,
            'detection_profile': 'mvit-contact-v3', 'model_id': 'mvit-v2-local',
            'detector_fingerprint': dict(FINGERPRINT), 'events': [],
        })


def complete_all(manager):
    for job in manager.jobs.values():
        job.update(status='completed', progress=1.0)
        manager._save(job)


def test_verified_boot_queues_both_clips_and_pins_actual_task_ids(manager):
    manager.prepare_foul_clips(cuda_verified=True)
    repository = NativeResults(manager)
    value = manager.foul_preparation(repository)
    assert value['schema_version'] == 1 and len(value['boot_id']) == 32
    assert value['enabled'] is True and value['status'] == 'preparing'
    assert value['detection_profile'] == 'mvit-contact-v3'
    assert [clip['case_id'] for clip in value['clips']] == ['foul-1', 'foul-2']
    for clip in value['clips']:
        assert clip['status'] == 'preparing' and clip['job_status'] == 'queued'
        job = manager.get(clip['job_id'])
        assert job['kind'] == 'foul' and job['mode'] == 'foul_only'
        assert job['case_id'] == clip['case_id'] and job['detector_fingerprint'] == FINGERPRINT
        assert clip['result_id'] is None and clip['revision'] is None
    assert manager.queue.qsize() == 2 and repository.calls == []


def test_completed_history_is_not_reused_on_new_boot(manager, monkeypatch):
    manager.prepare_foul_clips(cuda_verified=True)
    complete_all(manager)
    old_ids = set(manager.jobs)
    replacement = JobManager(manager.settings, manager.catalog)
    qualify(replacement, monkeypatch)
    replacement.prepare_foul_clips(cuda_verified=True)
    value = replacement.foul_preparation(NativeResults(replacement))
    assert replacement.boot_id != manager.boot_id
    assert all(clip['job_id'] not in old_ids for clip in value['clips'])
    assert value['status'] == 'preparing' and replacement.queue.qsize() == 2


def test_preparation_is_once_per_boot_and_existing_active_dedup_remains(manager):
    active = manager.submit('foul', 'foul-1', 'mvit-contact-v3')
    manager.prepare_foul_clips(cuda_verified=True)
    first = manager.foul_preparation(NativeResults(manager))
    assert first['clips'][0]['job_id'] == active['id']
    complete_all(manager)
    manager.prepare_foul_clips(cuda_verified=True)
    second = manager.foul_preparation(NativeResults(manager))
    assert [clip['job_id'] for clip in second['clips']] == [clip['job_id'] for clip in first['clips']]
    assert len(manager.jobs) == 2


def test_progress_and_measured_empty_result_become_ready_only_after_validation(manager):
    manager.prepare_foul_clips(cuda_verified=True)
    repository = NativeResults(manager)
    job = next(iter(manager.jobs.values()))
    job.update(status='running', progress=0.45)
    value = manager.foul_preparation(repository)
    assert value['clips'][0]['job_status'] == 'running'
    assert value['clips'][0]['progress'] == 0.45 and repository.calls == []
    complete_all(manager)
    value = manager.foul_preparation(repository)
    assert value['status'] == 'ready'
    assert all(clip['status'] == 'ready' and clip['revision'] == 'a' * 64 for clip in value['clips'])
    assert repository.calls == [clip['result_id'] for clip in value['clips']]
    assert all(clip['result_id'] == clip['job_id'] + '-frame-states' for clip in value['clips'])


def test_invalid_completed_records_and_failed_jobs_do_not_unlock_replay(manager):
    manager.prepare_foul_clips(cuda_verified=True)
    complete_all(manager)
    repository = NativeResults(manager)
    repository.invalid = True
    value = manager.foul_preparation(repository)
    assert value['status'] == 'error'
    assert all(clip['reason'] == 'native_result_invalid' and clip['result_id'] is None
               for clip in value['clips'])
    repository.invalid = False
    job = next(iter(manager.jobs.values()))
    job.update(status='failed', error='/private/path/or-sensitive-detail')
    value = manager.foul_preparation(repository)
    assert value['clips'][0]['reason'] == 'inference_failed'
    assert '/private/path' not in str(value)
    assert len(manager.jobs) == 2  # Polling never submits retries.


@pytest.mark.parametrize('defect', ['disabled-setting', 'cuda-unverified', 'enforce-disabled'])
def test_unverified_or_disabled_boot_never_schedules_inference(manager, defect):
    if defect == 'disabled-setting':
        manager.settings = replace(manager.settings, prepare_foul_on_startup=False)
    elif defect == 'enforce-disabled':
        manager.settings = replace(manager.settings, enforce_cuda=False)
    manager.prepare_foul_clips(cuda_verified=defect != 'cuda-unverified')
    value = manager.foul_preparation(NativeResults(manager))
    assert value['status'] == 'disabled' and value['enabled'] is False
    assert value['reason'] == ('startup_preparation_disabled' if defect == 'disabled-setting'
                              else 'cuda_unverified')
    assert not manager.jobs and manager.queue.empty()


def test_missing_qualification_reports_error_without_legacy_fallback(manager, monkeypatch):
    monkeypatch.setattr(manager.foul_validation, 'qualification', lambda: None)
    manager.prepare_foul_clips(cuda_verified=True)
    value = manager.foul_preparation(NativeResults(manager))
    assert value['enabled'] is True and value['status'] == 'error'
    assert value['reason'] == 'qualification_unavailable'
    assert all(clip['job_id'] is None for clip in value['clips'])
    assert not manager.jobs and manager.queue.empty()


def test_qualification_drift_blocks_even_already_completed_results(manager, monkeypatch):
    manager.prepare_foul_clips(cuda_verified=True)
    complete_all(manager)
    repository = NativeResults(manager)
    assert manager.foul_preparation(repository)['status'] == 'ready'
    monkeypatch.setattr(manager.foul_validation, 'snapshot', lambda *args: {
        **FINGERPRINT, 'model_sha256': '0' * 64})
    value = manager.foul_preparation(repository)
    assert value['status'] == 'error' and value['reason'] == 'qualification_changed'
    assert all(clip['result_id'] is None for clip in value['clips'])


def test_queue_failure_reports_each_clip_and_remains_read_only(manager):
    for item in range(manager.queue.maxsize):
        manager.queue.put_nowait('other-task-' + str(item))
    manager.prepare_foul_clips(cuda_verified=True)
    value = manager.foul_preparation(NativeResults(manager))
    assert value['status'] == 'error'
    assert all(clip['reason'] == 'queue_full' and clip['job_id'] is None for clip in value['clips'])
    assert not manager.jobs
    before = manager.queue.qsize()
    manager.foul_preparation(NativeResults(manager))
    assert manager.queue.qsize() == before


@pytest.mark.parametrize('value, expected', [('true', True), ('1', True), ('yes', True),
                                            ('false', False), ('0', False), ('no', False)])
def test_startup_boolean_environment_setting(monkeypatch, value, expected):
    monkeypatch.setenv('SHOWCASE_PREPARE_FOUL_ON_STARTUP', value)
    assert Settings.from_environment().prepare_foul_on_startup is expected


def test_invalid_startup_boolean_setting_is_rejected(monkeypatch):
    monkeypatch.setenv('SHOWCASE_PREPARE_FOUL_ON_STARTUP', 'sometimes')
    with pytest.raises(ValueError, match='must be a boolean'):
        Settings.from_environment()


def test_preparation_endpoint_on_unverified_boot_is_disabled(manager):
    from showcase.app import create_app
    app = create_app(replace(manager.settings, enforce_cuda=False))
    with TestClient(app) as client:
        response = client.get('/api/foul/preparation')
        assert response.status_code == 200
        value = response.json()
        assert value['status'] == 'disabled' and value['reason'] == 'cuda_unverified'
        assert all(clip['job_id'] is None for clip in value['clips'])
        assert not app.state.jobs.list()


@pytest.mark.cuda
def test_verified_lifespan_serves_http_while_fresh_tasks_are_still_queued(manager, monkeypatch):
    from showcase.app import create_app
    from showcase.foul_validation import FoulValidation
    # Stop at the scheduling boundary: no fixture media reaches an inference subprocess.
    monkeypatch.setattr(JobManager, 'start', lambda self: None)
    monkeypatch.setattr(FoulValidation, 'qualification', lambda self: {
        **FINGERPRINT, 'detection_profile': 'mvit-contact-v3', 'status': 'passed',
        'scope': 'development_clip'})
    monkeypatch.setattr(FoulValidation, 'snapshot', lambda self, *args: dict(FINGERPRINT))
    app = create_app(manager.settings)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['backend']['cuda_available'] is True
        response = client.get('/api/foul/preparation')
        assert response.status_code == 200
        value = response.json()
        assert value['status'] == 'preparing'
        assert all(clip['job_status'] == 'queued' for clip in value['clips'])
        assert app.state.jobs.queue.qsize() == 2 and app.state.jobs.process is None
        assert client.get('/api/foul/preparation').json()['boot_id'] == value['boot_id']
