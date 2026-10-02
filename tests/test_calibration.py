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
