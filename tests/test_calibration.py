from types import SimpleNamespace
import threading

import pytest
from fastapi import HTTPException
from showcase.calibration import CalibrationManager, Prepare, Revision, Label
from showcase.catalog import MediaCatalog


def manager(tmp_path):
    settings = SimpleNamespace(state_root=tmp_path / 'state', bundle=tmp_path / 'demo.npz')
    settings.bundle.write_bytes(b'protected-demo')
    return CalibrationManager(settings, MediaCatalog(tmp_path), threading.Lock())


def seed(service, identifier='a' * 32):
    item = {'id': identifier, 'revision': 0, 'status': 'idle', 'operation': None,
            'error': None, 'source_id': 'calibration', 'session': {'state': 'source_preview'},
            'source': {'duration_ms': 19000}, 'labels': {}, 'clip': None}
    service.records[identifier] = item
    service._save(item)
    return identifier


def test_paths_and_protected_demo(tmp_path):
    service = manager(tmp_path)
    with pytest.raises(HTTPException) as error:
        service.create('../tracking-projection')
    assert error.value.status_code == 422
    identifier = seed(service)
    result = service.get(identifier)
    assert result['demo'] == {'ready': True, 'protected': True}
    assert service.settings.bundle.read_bytes() == b'protected-demo'
    assert service.root == tmp_path / 'calibration-sessions'
    with pytest.raises(HTTPException):
        service.get('../demo')


def test_revision_and_clip_validation(tmp_path):
    service = manager(tmp_path)
    identifier = seed(service)
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'prepare', Prepare(revision=5, start_ms=0, end_ms=1000))
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'prepare', Prepare(revision=0, start_ms=100, end_ms=20000))
    assert error.value.status_code == 422
    result = service.submit(identifier, 'prepare', Prepare(revision=0, start_ms=0, end_ms=1000))
    assert result['status'] == 'queued'
    assert result['revision'] == 1
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'reset', Revision(revision=1))
    assert error.value.status_code == 409
    assert service.settings.bundle.read_bytes() == b'protected-demo'


def test_recovery_retains_choices_and_marks_interrupted_work(tmp_path):
    service = manager(tmp_path)
    identifier = seed(service)
    service.records[identifier].update(status='running', labels={'4': 'home_outfield'})
    service._save(service.records[identifier])
    recovered = manager(tmp_path)
    result = recovered.get(identifier)
    assert result['status'] == 'error'
    assert result['labels'] == {'4': 'home_outfield'}
    assert result['revision'] == 1
    assert recovered.settings.bundle.read_bytes() == b'protected-demo'


def test_queue_bounded_and_unknown_track_rejected(tmp_path):
    service = manager(tmp_path)
    for number in range(3):
        identifier = seed(service, str(number) * 32)
        service.submit(identifier, 'prepare', Prepare(revision=0, start_ms=0, end_ms=1000))
    identifier = seed(service, 'b' * 32)
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'prepare', Prepare(revision=0, start_ms=0, end_ms=1000))
    assert error.value.status_code == 429
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'labels', Label(revision=0, track_id=8, label='home_outfield'))
    assert error.value.status_code == 409


def test_shared_gpu_lease_blocks_calibration_and_shutdown_cancels(tmp_path):
    import time
    service = manager(tmp_path)
    identifier = seed(service)
    calls = []
    service._service = lambda item: calls.append(item)
    service.gpu_lease.acquire()
    service.start()
    service.submit(identifier, 'reset', Revision(revision=0))
    time.sleep(.05)
    assert service.get(identifier)['status'] == 'queued'
    assert not calls
    service.stopped.set()
    service.gpu_lease.release()
    service.stop()
    assert not calls
    assert service.get(identifier)['status'] == 'error'
    with pytest.raises(HTTPException) as error:
        service.submit(identifier, 'reset', Revision(revision=2))
    assert error.value.status_code == 503


def test_repeat_validation_is_idempotent():
    calls = []
    session = SimpleNamespace(ready=True, validate=lambda: calls.append('validate'))
    service = SimpleNamespace(session=session)
    CalibrationManager._validate_service(service)
    assert not calls
    session.ready = False
    CalibrationManager._validate_service(service)
    assert calls == ['validate']


def test_gpu_lease_releases_extractors_and_cache_even_on_error(tmp_path, monkeypatch):
    import sys
    service = manager(tmp_path)
    features = {'track': [1., 2.]}
    session = SimpleNamespace(_lock=threading.RLock(), appearance_extractor=object(),
                              features=features)
    service.services['example'] = SimpleNamespace(session=session)
    calls = []
    fake_cuda = SimpleNamespace(is_initialized=lambda: True,
                               synchronize=lambda: calls.append('sync'),
                               empty_cache=lambda: calls.append('empty'))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=fake_cuda))
    with pytest.raises(RuntimeError):
        with service._cuda_operation():
            assert service.gpu_lease.locked()
            raise RuntimeError('operation failed')
    assert session.appearance_extractor is None
    assert session.features is features
    assert calls == ['sync', 'empty']
    assert not service.gpu_lease.locked()


def test_stop_requests_clip_cancellation_before_waiting(tmp_path):
    service = manager(tmp_path)
    calls = []
    service.services['active'] = SimpleNamespace(cancel_processing=lambda: calls.append('cancel'))
    service.request_stop()
    assert service.stopped.is_set()
    assert calls == ['cancel']


@pytest.mark.parametrize('seekable', [False, True])
def test_review_video_prefers_existing_derivative_without_mutating_session(tmp_path, seekable):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from showcase.calibration import create_calibration_router

    service = manager(tmp_path)
    identifier = seed(service)
    directory = service.root / identifier / 'clips' / 'prepared'
    directory.mkdir(parents=True)
    original = directory / 'review.mp4'
    original.write_bytes(b'original-video')
    derivative = directory / 'review-seekable.mp4'
    if seekable:
        derivative.write_bytes(b'seekable-video')
    item = service.records[identifier]
    item.update(clip={'clip_id': 'prepared', 'clip_path': str(original)},
                labels={'4': 'home_outfield'})
    service._save(item)
    bundle = service.root / identifier / 'bundle.npz'
    bundle.write_bytes(b'practice-bundle')
    record = service.root / identifier / 'session.json'
    before_snapshot = service.get(identifier)
    before_record = record.read_bytes()
    expected = derivative if seekable else original
    assert service.video(identifier) == expected

    app = FastAPI()
    app.include_router(create_calibration_router(service))
    with TestClient(app) as client:
        url = f'/api/calibration/sessions/{identifier}/video'
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == expected.read_bytes()
        assert response.headers['cache-control'] == 'no-cache'
        response = client.get(url, headers={'Range': 'bytes=0-3'})
        assert response.status_code == 206
        assert response.content == expected.read_bytes()[:4]
        assert response.headers['cache-control'] == 'no-cache'
    assert service.get(identifier) == before_snapshot
    assert record.read_bytes() == before_record
    assert original.read_bytes() == b'original-video'
    assert bundle.read_bytes() == b'practice-bundle'
    assert service.settings.bundle.read_bytes() == b'protected-demo'
    assert service.records[identifier]['clip']['clip_path'] == str(original)


def test_source_and_missing_original_video_behavior_is_unchanged(tmp_path):
    service = manager(tmp_path)
    identifier = seed(service)
    source = tmp_path / 'input' / 'calibration.mp4'
    source.parent.mkdir()
    source.write_bytes(b'prepared-source')
    assert service.video(identifier) == source
    item = service.records[identifier]
    item['clip'] = {'clip_id': 'missing'}
    service._save(item)
    directory = service.root / identifier / 'clips' / 'missing'
    directory.mkdir(parents=True)
    (directory / 'review-seekable.mp4').write_bytes(b'derivative-only')
    before = service.get(identifier)
    record = (service.root / identifier / 'session.json').read_bytes()
    with pytest.raises(HTTPException) as error:
        service.video(identifier)
    assert error.value.status_code == 409
    assert error.value.detail == 'Review video is not ready'
    assert service.get(identifier) == before
    assert (service.root / identifier / 'session.json').read_bytes() == record
    assert source.read_bytes() == b'prepared-source'
