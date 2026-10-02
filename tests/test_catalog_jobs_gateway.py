from dataclasses import replace
from pathlib import Path
import asyncio
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from showcase.app import create_app
from showcase.catalog import MediaCatalog
from showcase.gateway import create_gateway
from showcase.jobs import JobManager
from showcase.settings import Settings


@pytest.fixture
def settings(tmp_path):
    media = tmp_path / 'media'
    (media / 'input').mkdir(parents=True)
    (media / 'input/calibration.mp4').write_bytes(b'0123456789' * 100)
    core = tmp_path / 'core'
    (core / 'tools').mkdir(parents=True)
    (core / 'tools/run_night_ablation.py').write_text('# Boundary test only; never executed')
    return Settings(media_root=media, state_root=tmp_path / 'state',
                    upstream='http://127.0.0.1:8000', core_root=core,
                    core_python=Path('/not/executed'), player_model=tmp_path / 'player.pt',
                    pitch_model=tmp_path / 'pitch.pt', foul_model=tmp_path / 'foul.pt',
                    foul_code=tmp_path / 'foul-code', bundle=tmp_path / 'bundle.npz',
                    enforce_cuda=False)


def test_media_paths_cannot_escape_including_symlinks(settings, tmp_path):
    catalog = MediaCatalog(settings.media_root)
    outside = tmp_path / 'secret.mp4'
    outside.write_text('private')
    (settings.media_root / 'escape.mp4').symlink_to(outside)
    for path in ['../secret.mp4', str(outside), 'escape.mp4']:
        with pytest.raises(HTTPException):
            catalog.safe_path(path)


def test_artifact_registry_excludes_missing_or_escaped_files(settings, tmp_path):
    import json
    (settings.media_root / 'artifacts.json').write_text(json.dumps([
        {'id':'valid', 'case_id':'calibration', 'path':'input/calibration.mp4'},
        {'id':'missing', 'case_id':'calibration', 'path':'missing.mp4'},
        {'id':'escaped', 'case_id':'calibration', 'path':'../private.mp4'},
    ]))
    catalog = MediaCatalog(settings.media_root)
    public = catalog.clips()[0]['artifacts']
    assert len(public) == 1 and public[0]['id'] == 'valid'
    assert 'path' not in public[0]


def test_queue_deduplicates_and_stays_bounded(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    first = manager.submit('tracking', 'calibration')
    assert manager.submit('tracking', 'calibration')['id'] == first['id']
    manager.submit('foul', 'calibration')
    manager.submit('combined', 'calibration')
    (settings.media_root / 'input/foul-1.mp4').write_bytes(b'video')
    with pytest.raises(HTTPException) as error:
        manager.submit('tracking', 'foul-1')
    assert error.value.status_code == 429
    assert len(manager.list()) == 3


def test_invalid_job_never_reaches_process_execution(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    for kind, case_id in [('tracking; echo bad', 'calibration'), ('tracking', '../outside')]:
        with pytest.raises(HTTPException):
            manager.submit(kind, case_id)
    assert not manager.list()


def test_restart_marks_interrupted_jobs_failed(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    job = manager.submit('tracking', 'calibration')
    restored = JobManager(settings, MediaCatalog(settings.media_root))
    assert restored.get(job['id'])['status'] == 'failed'


@pytest.mark.cuda
def test_health_reports_real_cuda_on_remote_host(settings):
    import torch
    assert torch.cuda.is_available(), 'This backend test must run on the CUDA machine'
    app = create_app(replace(settings, enforce_cuda=True))
    with TestClient(app) as client:
        result = client.get('/api/health').json()
        assert result['backend']['device'] == 'cuda'
        assert result['backend']['gpu_name'] == torch.cuda.get_device_name()


def test_file_range_and_invalid_inputs(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get('/media/input/calibration.mp4', headers={'Range':'bytes=100-199'})
        assert response.status_code == 206
        assert response.content == (b'0123456789' * 100)[100:200]
        assert response.headers['content-range'] == 'bytes 100-199/1000'
        assert client.get('/media/escape.txt').status_code == 404
        assert client.post('/api/experiments/jobs', json={'kind':'other', 'case_id':'calibration'}).status_code == 422
        assert client.post('/api/experiments/jobs', json={'kind':'foul', 'case_id':'no-such-clip'}).status_code == 404
        assert client.post('/api/experiments/jobs', json={'kind':'foul', 'case_id':'calibration', 'device':'cpu'}).status_code == 422


def test_multiview_gateway_streams_range_and_preserves_errors():
    seen = []
    class WireStream(httpx.AsyncByteStream):
        def __init__(self, data):
            self.data = data
        async def __aiter__(self):
            yield self.data
    def upstream(request):
        seen.append(request)
        if request.url.path.endswith('/missing'):
            return httpx.Response(404, stream=WireStream(b'{"detail":"Evidence media not found"}'), headers={'content-type':'application/json'})
        return httpx.Response(206, stream=WireStream(b'clip-range'),
                              headers={'content-range':'bytes 10-19/100', 'content-type':'video/mp4'})
    remote = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = FastAPI()
    app.include_router(create_gateway(remote, 'http://remote.invalid'))
    with TestClient(app) as client:
        response = client.get('/api/multiview/cases/a/media/view_1', headers={'Range':'bytes=10-19'})
        assert response.status_code == 206 and response.content == b'clip-range'
        assert seen[0].headers['range'] == 'bytes=10-19'
        assert client.get('/api/multiview/missing').status_code == 404
    asyncio.run(remote.aclose())


def test_shutdown_rejects_new_jobs_and_persists_queued_failure(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    job = manager.submit('tracking', 'calibration')
    manager.stop()
    assert manager.get(job['id'])['status'] == 'failed'
    assert JobManager(settings, MediaCatalog(settings.media_root)).get(job['id'])['status'] == 'failed'
    with pytest.raises(HTTPException) as error:
        manager.submit('tracking', 'calibration')
    assert error.value.status_code == 503


@pytest.mark.parametrize('defect', ['cpu', 'truncated', 'wrong_mode', 'no_foul_forward'])
def test_output_validation_rejects_false_success(settings, tmp_path, defect):
    import json
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = {'status': 'complete', 'mode': 'foul_only', 'environment': {'device': 'cuda'},
              'decoded_frames': 579, 'processed_frames': 579, 'output_frames': 579,
              'dropped_frame_count': 0, 'config': {'pitch_enabled': False, 'foul_enabled': True},
              'foul_actual_forward_windows': 1, 'foul_errors': []}
    if defect == 'cpu':
        report['environment']['device'] = 'cpu'
    if defect == 'truncated':
        report['processed_frames'] = 578
    if defect == 'wrong_mode':
        report['config']['pitch_enabled'] = True
    if defect == 'no_foul_forward':
        report['foul_actual_forward_windows'] = 0
    (tmp_path / 'report.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError):
        manager._validate_output(tmp_path, {'case_id': 'calibration', 'mode': 'foul_only'})


def test_source_profile_requires_matching_allowlisted_media(settings, monkeypatch):
    import hashlib
    catalog = MediaCatalog(settings.media_root)
    source = settings.media_root / 'input/tracking-projection.mp4'
    source.write_bytes(b'software fixture, not football footage')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    clip = catalog.clip('tracking-projection')
    unmatched = JobManager(settings, catalog).submit('tracking', clip['id'])
    assert unmatched['pitch_profile_id'] == 'legacy'
    assert unmatched['paint_enabled'] is False
    assert unmatched['source_sha256'] == digest
    monkeypatch.setitem(clip, 'profile_source_sha256', digest)
    matched = JobManager(settings, catalog).submit('tracking', clip['id'])
    assert matched['pitch_profile_id'] == 'source-informed105'
    assert matched['paint_enabled'] is True
    assert matched['source_sha256'] == digest
    source.write_bytes(b'replaced source must not inherit the previous geometry')
    replacement = JobManager(settings, catalog).submit('tracking', clip['id'])
    assert replacement['pitch_profile_id'] == 'legacy'
    assert replacement['paint_enabled'] is False


def test_calibration_and_foul_only_keep_legacy_profile(settings, monkeypatch):
    import hashlib
    catalog = MediaCatalog(settings.media_root)
    manager = JobManager(settings, catalog)
    calibration = manager.submit('tracking', 'calibration')
    assert calibration['pitch_profile_id'] == 'legacy'
    assert calibration['paint_enabled'] is False
    source = settings.media_root / 'input/tracking-projection.mp4'
    source.write_bytes(b'fixture')
    monkeypatch.setitem(catalog.clip('tracking-projection'), 'profile_source_sha256',
                        hashlib.sha256(source.read_bytes()).hexdigest())
    foul = manager.submit('foul', 'tracking-projection')
    assert foul['pitch_profile_id'] == 'legacy' and foul['paint_enabled'] is False


@pytest.mark.parametrize('defect', ['profile', 'paint_enabled', 'source'])
def test_output_rejects_mismatched_source_profile_before_registering(settings, tmp_path, defect):
    import json
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    job = {'case_id': 'tracking-projection', 'mode': 'projection_only',
           'pitch_profile_id': 'source-informed105', 'paint_enabled': True,
           'source_sha256': 'a' * 64}
    report = {'status': 'complete', 'mode': 'projection_only',
              'environment': {'device': 'cuda'}, 'decoded_frames': 682,
              'processed_frames': 682, 'output_frames': 682, 'dropped_frame_count': 0,
              'foul_errors': [], 'source': {'sha256': 'a' * 64},
              'config': {'pitch_enabled': True, 'foul_enabled': False,
                         'pitch_profile_id': 'source-informed105', 'paint_enabled': True}}
    if defect == 'profile':
        report['config']['pitch_profile_id'] = 'legacy'
    elif defect == 'paint_enabled':
        report['config']['paint_enabled'] = False
    else:
        report['source']['sha256'] = 'b' * 64
    (tmp_path / 'report.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match='source-bound geometry profile'):
        manager._validate_output(tmp_path, job)
