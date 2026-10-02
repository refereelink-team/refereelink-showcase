"""Software-boundary tests; synthetic records do not claim model or GPU accuracy."""

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from showcase.catalog import MediaCatalog
from showcase.jobs import JobManager
from showcase.settings import Settings
from showcase.tracking import TrackingRepository, create_tracking_router


@pytest.fixture
def tracking(tmp_path):
    media, core = tmp_path / 'media', tmp_path / 'core'
    (media / 'input').mkdir(parents=True)
    (core / 'tools').mkdir(parents=True)
    (core / 'tools/run_night_ablation.py').write_text('# Boundary fixture; never execute')
    source = media / 'input/calibration.mp4'
    source.write_bytes(b'original source fixture, not an annotated video')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = media / 'prepared/calibration/projection_only'
    directory.mkdir(parents=True)
    metadata = {
        'sha256': digest, 'reference_decoded_frames': 579,
        'width': 852, 'height': 480, 'output_fps': 30.0,
        'ffprobe': {'duration': '19.313'},
        'decoded_timestamps_ms': [position * 1000 / 30 for position in range(579)],
    }
    report = {
        'status': 'complete', 'mode': 'projection_only', 'environment': {'device': 'cuda'},
        'config': {'pitch_enabled': True, 'foul_enabled': False},
        'decoded_frames': 579, 'processed_frames': 579, 'output_frames': 579,
        'dropped_frame_count': 0, 'foul_errors': [], 'source': {'sha256': digest},
        'completed_at': '2026-10-02T01:00:00+00:00',
        'fps_including_render_and_json': 45.0, 'pipeline_latency_ms': {'mean': 12.0},
        'homography_available_frame_rate': 0.9, 'projected_player_rate': 0.8,
        'unknown_role_rate': 0.1, 'unknown_team_rate_all_persons': 0.2,
    }
    frames = [
        {'frame_id': position + 1, 'homography_status': 'fresh', 'players': [
            {'track_id': 7, 'entity_id': 3, 'track_status': 'detected', 'missing_frames': 0,
             'role': 'outfield', 'team': 'home', 'confidence': 0.9,
             'role_confidence': 0.8, 'team_confidence': 0.7,
             'bbox': [10, 20, 30, 80], 'field_x': 1234.0, 'field_y': 4567.0,
             'velocity_x': 150.0, 'velocity_y': -20.0,
             'semantic_status': 'locked', 'team_rejection_reason': None}],
         'ball': {'status': 'fresh', 'image_x': 45, 'image_y': 70,
                  'field_x': 5000.0, 'field_y': 3500.0, 'velocity_x': 400.0,
                  'velocity_y': 0.0, 'confidence': 0.9, 'age_frames': 0},
         'capture_timestamp_ms': 10000000 + position,
         'events': [{'private': 'not part of the tracking protocol'}]}
        for position in range(579)
    ]
    (directory / 'source-metadata.json').write_text(json.dumps(metadata))
    (directory / 'report.json').write_text(json.dumps(report))
    (directory / 'frame-states.jsonl').write_text(''.join(json.dumps(frame) + '\n' for frame in frames))
    artifacts = [
        {'id': 'prepared-calibration-projection_only-' + stem, 'case_id': 'calibration',
         'mode': 'projection_only', 'kind': 'data', 'label': name,
         'path': 'prepared/calibration/projection_only/' + name}
        for stem, name in [('frame-states', 'frame-states.jsonl'), ('report', 'report.json'),
                           ('source-metadata', 'source-metadata.json')]
    ]
    (media / 'artifacts.json').write_text(json.dumps(artifacts))
    settings = Settings(media_root=media, state_root=tmp_path / 'state',
                        upstream='http://upstream.invalid', core_root=core,
                        core_python=Path('/never/executed'), player_model=tmp_path / 'player.pt',
                        pitch_model=tmp_path / 'pitch.pt', foul_model=tmp_path / 'foul.pt',
                        foul_code=tmp_path / 'foul-code', bundle=tmp_path / 'bundle.npz',
                        enforce_cuda=False)
    catalog = MediaCatalog(media)
    manager = JobManager(settings, catalog)
    repository = TrackingRepository(catalog, manager)
    app = FastAPI()
    app.include_router(create_tracking_router(repository))
    return TestClient(app), repository, directory, metadata, report, frames


def _result_id():
    return 'prepared-calibration-projection_only-frame-states'


def test_case_descriptor_uses_original_media_and_actual_processing_metadata(tracking):
    client, _, _, _, _, _ = tracking
    response = client.get('/api/tracking/cases/calibration')
    assert response.status_code == 200
    case = response.json()
    result = case['latest_result']
    assert case['active_job'] is None and case['result_warning'] is None
    assert result['source']['url'] == '/media/input/calibration.mp4'
    assert result['source']['fps'] == 30 and result['metrics']['actual_fps'] == 45
    assert result['metrics']['latency_ms'] == 12
    assert result['source']['width'] == 852 and result['source']['height'] == 480
    assert result['pitch']['length_m'] == 120 and result['pitch']['width_m'] == 70
    assert result['provenance']['device'] == 'cuda' and result['provenance']['live'] is False
    assert result['job_id'] is None and len(result['revision']) == 64
    assert 'path' not in json.dumps(result) and 'annotated' not in json.dumps(result)


def test_frames_join_source_pts_and_convert_only_world_units(tracking):
    client, _, _, _, _, _ = tracking
    page = client.get('/api/tracking/results/' + _result_id() + '/frames?offset=30&limit=2').json()
    frame = page['frames'][0]
    assert frame['frame_id'] == 31 and frame['source_pts_s'] == 1.0
    player = frame['players'][0]
    assert player['track_id'] == 7 and player['entity_id'] == 3
    assert player['role'] == 'outfield' and player['team'] == 'home'
    assert player['bbox'] == [10, 20, 30, 80]
    assert player['field_x'] == 12.34 and player['field_y'] == 45.67
    assert player['velocity_x'] == 1.5 and player['velocity_y'] == -0.2
    assert frame['ball']['image_x'] == 45 and frame['ball']['field_x'] == 50
    assert frame['ball']['velocity_x'] == 4
    assert 'capture_timestamp_ms' not in frame and 'events' not in frame
    assert page['offset'] == 30 and page['total'] == 579 and page['next_offset'] == 32


def test_page_boundaries_and_version_guard(tracking):
    client, _, _, _, _, _ = tracking
    base = '/api/tracking/results/' + _result_id()
    result = client.get(base).json()
    revision = result['revision']
    assert client.get(base + '/frames?limit=241').status_code == 422
    assert client.get(base + '/frames?offset=-1').status_code == 422
    assert client.get(base + '/frames?offset=580').status_code == 416
    assert client.get(base + '/frames?revision=malformed').status_code == 422
    assert client.get(base + '/frames?revision=' + '0' * 64).status_code == 409
    last = client.get(base + '/frames?offset=578&revision=' + revision).json()
    assert len(last['frames']) == 1 and last['next_offset'] is None
    empty = client.get(base + '/frames?offset=579').json()
    assert empty['frames'] == [] and empty['next_offset'] is None
    assert empty['revision'] == revision


def test_native_job_submission_reuses_real_runner_queue_and_dedup(tracking):
    client, repository, _, _, _, _ = tracking
    first = client.post('/api/tracking/cases/calibration/jobs')
    assert first.status_code == 202
    job = first.json()
    assert job['kind'] == 'tracking' and job['mode'] == 'projection_only'
    assert client.post('/api/tracking/cases/calibration/jobs').json()['id'] == job['id']
    assert repository.jobs.get(job['id'])['status'] == 'queued'
    assert client.get('/api/tracking/cases/calibration').json()['active_job']['id'] == job['id']
    assert client.post('/api/tracking/cases/not-allowlisted/jobs').status_code == 404


@pytest.mark.parametrize('defect', ['cpu', 'wrong-mode', 'drops', 'truncated', 'source-sha',
                                    'timestamps', 'nonfinite', 'missing-id', 'partial-line',
                                    'extra-frame', 'unregistered-metadata', 'nonfinite-metric'])
def test_invalid_runs_never_supply_native_overlay(tracking, defect):
    client, repository, directory, metadata, report, frames = tracking
    if defect == 'cpu':
        report['environment']['device'] = 'cpu'
    elif defect == 'wrong-mode':
        report['config']['pitch_enabled'] = False
    elif defect == 'drops':
        report['dropped_frame_count'] = 1
    elif defect == 'truncated':
        frames.pop()
    elif defect == 'source-sha':
        metadata['sha256'] = '0' * 64
    elif defect == 'timestamps':
        metadata['decoded_timestamps_ms'][1] = 0
    elif defect == 'nonfinite':
        frames[0]['players'][0]['field_x'] = float('nan')
    elif defect == 'missing-id':
        frames[10]['frame_id'] = 12
    elif defect == 'extra-frame':
        frames.append({**frames[-1], 'frame_id': 580})
    elif defect == 'nonfinite-metric':
        report['fps_including_render_and_json'] = float('nan')
    elif defect == 'unregistered-metadata':
        artifacts = repository.catalog.artifacts()
        (repository.catalog.root / 'artifacts.json').write_text(json.dumps(artifacts[:-1]))
    (directory / 'source-metadata.json').write_text(json.dumps(metadata))
    (directory / 'report.json').write_text(json.dumps(report))
    data = ''.join(json.dumps(frame) + '\n' for frame in frames)
    (directory / 'frame-states.jsonl').write_text(data.rstrip('\n') if defect == 'partial-line' else data)
    assert client.get('/api/tracking/results/' + _result_id() + '/frames').status_code == 409
    case = client.get('/api/tracking/cases/calibration').json()
    assert case['latest_result'] is None and case['result_warning'] is not None


def test_changed_source_and_record_revision_invalidate_cached_index(tracking):
    client, repository, directory, _, _, frames = tracking
    base = '/api/tracking/results/' + _result_id()
    before = client.get(base).json()['revision']
    frames[0]['players'][0]['team'] = 'unknown'
    frames[0]['players'][0]['role'] = 'unknown'
    frames[0]['players'][0]['field_x'] = None
    (directory / 'frame-states.jsonl').write_text(''.join(json.dumps(frame) + '\n' for frame in frames))
    assert client.get(base + '/frames?revision=' + before).status_code == 409
    after = client.get(base + '/frames').json()['frames'][0]['players'][0]
    assert after['team'] == 'unknown' and after['role'] == 'unknown' and after['field_x'] is None
    (repository.catalog.root / 'input/calibration.mp4').write_bytes(b'different source fixture')
    assert client.get(base + '/frames').status_code == 409


def test_changed_source_alignment_and_report_change_the_result_revision(tracking):
    client, _, directory, metadata, report, _ = tracking
    base = '/api/tracking/results/' + _result_id()
    before = client.get(base).json()['revision']
    metadata['decoded_timestamps_ms'][1] += 0.1
    (directory / 'source-metadata.json').write_text(json.dumps(metadata))
    assert client.get(base + '/frames?revision=' + before).status_code == 409
    after = client.get(base).json()['revision']
    assert after != before
    report['pipeline_latency_ms']['mean'] = 13.0
    (directory / 'report.json').write_text(json.dumps(report))
    assert client.get(base + '/frames?revision=' + after).status_code == 409


def test_alignment_change_during_page_read_is_rejected(tracking, monkeypatch):
    import showcase.tracking as reader
    client, _, directory, metadata, _, _ = tracking
    base = '/api/tracking/results/' + _result_id()
    revision = client.get(base).json()['revision']
    original = reader._frame
    def replace_alignment(line, expected):
        metadata['decoded_timestamps_ms'][1] += 0.1
        (directory / 'source-metadata.json').write_text(json.dumps(metadata))
        return original(line, expected)
    monkeypatch.setattr(reader, '_frame', replace_alignment)
    assert client.get(base + '/frames?limit=1&revision=' + revision).status_code == 409


def test_only_allowlisted_registered_results_are_accessible(tracking):
    client, repository, _, _, _, _ = tracking
    assert client.get('/api/tracking/results/../../private').status_code == 404
    assert client.get('/api/tracking/results/not-registered/frames').status_code == 404
    artifacts = repository.catalog.artifacts()
    artifacts[0]['path'] = '../outside/frame-states.jsonl'
    (repository.catalog.root / 'artifacts.json').write_text(json.dumps(artifacts))
    assert client.get('/api/tracking/results/' + _result_id() + '/frames').status_code == 404
