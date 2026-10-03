"""Native-presentation boundary tests; fixture records do not claim model accuracy."""

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from showcase.catalog import MediaCatalog
from showcase.foul_presentation import FoulPresentationRepository, create_foul_presentation_router
from showcase.foul_validation import configuration_sha256
from showcase.jobs import JobManager
from showcase.settings import Settings

RESULT_ID = 'prepared-foul-1-foul_only-frame-states'


@pytest.fixture
def presentation(tmp_path):
    media = tmp_path / 'media'
    (media / 'input').mkdir(parents=True)
    source = media / 'input/foul-1.mp4'
    source.write_bytes(b'original media boundary fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = media / 'prepared/foul-1/foul_only'
    directory.mkdir(parents=True)
    metadata = {'sha256': digest, 'reference_decoded_frames': 175,
                'width': 852, 'height': 480, 'output_fps': 30.0,
                'ffprobe': {'duration': '5.842'},
                'decoded_timestamps_ms': [i * 1000 / 30 for i in range(175)]}
    report = {'status': 'complete', 'mode': 'foul_only',
              'environment': {'device': 'cuda'},
              'config': {'pitch_enabled': False, 'foul_enabled': True},
              'decoded_frames': 175, 'processed_frames': 175, 'output_frames': 175,
              'dropped_frame_count': 0, 'foul_errors': [], 'foul_candidates': 1,
              'source': {'sha256': digest, 'width': 852, 'height': 480, 'output_fps': 30.0},
              'completed_at': '2026-10-03T00:00:00+00:00'}
    frames = [{'frame_id': i + 1, 'players': [
        {'track_id': 7, 'entity_id': None, 'team': 'unknown', 'role': 'unknown',
         'bbox': [10, 20, 30, 80], 'confidence': 0.9, 'track_status': 'detected',
         'missing_frames': 0, 'field_x': None, 'team_id': -1,
         'private': 'must not enter native protocol'}], 'ball': None,
        'capture_timestamp_ms': 1700000000, 'events': [{'private': True}]}
        for i in range(175)]
    events = [{'media_pts_seconds': 3.0, 'source_frame_id': 91, 'event': {
        'id': 'candidate-1', 'event_type': 'foul_candidate', 'confidence': 0.7,
        'reviewed': False, 'foul_details': {'action': 'Tackle'}, 'evidence': {'source': 'mvfoul'}}}]
    artifacts = [{'id': 'prepared-foul-1-foul_only-' + Path(name).stem,
                  'case_id': 'foul-1', 'mode': 'foul_only',
                  'kind': 'data' if name == 'frame-states.jsonl' else
                          'events' if name == 'candidate-events.jsonl' else 'report',
                  'path': 'prepared/foul-1/foul_only/' + name}
                 for name in ('frame-states.jsonl', 'report.json', 'source-metadata.json',
                              'candidate-events.jsonl')]
    (media / 'artifacts.json').write_text(json.dumps(artifacts))
    settings = Settings(media_root=media, state_root=tmp_path / 'state',
                        upstream='http://unused.invalid', core_root=tmp_path / 'core',
                        core_python=Path('/never/executed'), player_model=tmp_path / 'player.pt',
                        pitch_model=tmp_path / 'pitch.pt', foul_model=tmp_path / 'foul.pt',
                        foul_code=tmp_path / 'author-code', bundle=tmp_path / 'bundle.npz',
                        enforce_cuda=False)
    catalog = MediaCatalog(media)
    jobs = JobManager(settings, catalog)
    repository = FoulPresentationRepository(catalog, jobs)
    app = FastAPI()
    app.include_router(create_foul_presentation_router(repository))
    fixture = {'client': TestClient(app), 'repository': repository, 'directory': directory,
               'metadata': metadata, 'report': report, 'frames': frames, 'events': events,
               'artifacts': artifacts, 'source': source}
    write_records(fixture)
    return fixture


def write_records(fixture):
    directory = fixture['directory']
    for key, name in (('metadata', 'source-metadata.json'), ('report', 'report.json')):
        (directory / name).write_text(json.dumps(fixture[key]))
    for key, name in (('frames', 'frame-states.jsonl'), ('events', 'candidate-events.jsonl')):
        (directory / name).write_text(''.join(json.dumps(row) + '\n' for row in fixture[key]))
    (fixture['repository'].catalog.root / 'artifacts.json').write_text(json.dumps(fixture['artifacts']))


def base(result_id=RESULT_ID):
    return '/api/foul/results/' + result_id


def test_descriptor_and_frames_use_original_media_and_preserve_unknown_facts(presentation):
    client = presentation['client']
    response = client.get(base())
    assert response.status_code == 200
    result = response.json()
    assert result['schema_version'] == 1 and result['frame_count'] == 175
    assert result['source'] == {'url': '/media/input/foul-1.mp4', 'width': 852,
                                'height': 480, 'duration_s': 5.842, 'fps': 30.0,
                                'sha256': presentation['metadata']['sha256']}
    assert result['detection_profile'] == 'legacy-v1' and result['model_id'] == 'mvit-v2-s-vars'
    assert result['events'] == presentation['events']
    assert 'region_xyxy' not in result['events'][0]['event']['evidence']
    page = client.get(result['frames_url'] + '?offset=90&limit=2&revision=' + result['revision']).json()
    assert page['frames'][0]['frame_id'] == 91 and page['frames'][0]['time_s'] == 3.0
    player = page['frames'][0]['players'][0]
    assert player['team'] == player['role'] == 'unknown' and player['entity_id'] is None
    assert player['bbox'] == [10, 20, 30, 80]
    assert set(player) == {'track_id', 'entity_id', 'team', 'role', 'bbox', 'confidence',
                           'track_status', 'missing_frames'}
    assert set(page['frames'][0]) == {'frame_id', 'time_s', 'players'}
    assert page['offset'] == 90 and page['next_offset'] == 92
    assert 'annotated' not in json.dumps(result) and 'path' not in result


def test_bounded_pages_and_revision_guard(presentation):
    client = presentation['client']
    revision = client.get(base()).json()['revision']
    assert client.get(base() + '/frames?limit=501').status_code == 422
    assert client.get(base() + '/frames?offset=-1').status_code == 422
    assert client.get(base() + '/frames?offset=176').status_code == 416
    assert client.get(base() + '/frames?revision=bad').status_code == 422
    assert client.get(base() + '/frames?revision=' + '0' * 64).status_code == 409
    last = client.get(base() + '/frames?offset=174&limit=500').json()
    assert last['next_offset'] is None and len(last['frames']) == 1
    empty = client.get(base() + '/frames?offset=175').json()
    assert empty['frames'] == [] and empty['revision'] == revision


@pytest.mark.parametrize('defect', [
    'cpu', 'pitch-on', 'foul-off', 'wrong-mode', 'drops', 'foul-errors', 'no-completion',
    'truncated', 'extra-frame', 'duplicate-frame', 'duplicate-track', 'invalid-box',
    'unknown-team-enum', 'nonfinite', 'timestamps', 'timestamps-nan', 'source-sha',
    'report-sha', 'dimensions', 'event-count', 'event-time', 'future-event',
    'invalid-event-id', 'duplicate-event', 'partial-event-line', 'partial-frame-line',
    'region-dimensions', 'region-box', 'event-frame', 'unregistered-sibling', 'wrong-artifact-kind',
])
def test_invalid_runs_never_supply_native_results(presentation, defect):
    f = presentation
    report, frames, events, metadata = (f[key] for key in ('report', 'frames', 'events', 'metadata'))
    if defect == 'cpu':
        report['environment']['device'] = 'cpu'
    elif defect == 'pitch-on':
        report['config']['pitch_enabled'] = True
    elif defect == 'foul-off':
        report['config']['foul_enabled'] = False
    elif defect == 'wrong-mode':
        report['mode'] = 'combined'
    elif defect == 'drops':
        report['dropped_frame_count'] = 1
    elif defect == 'foul-errors':
        report['foul_errors'] = ['nonfinite model output']
    elif defect == 'no-completion':
        report['completed_at'] = '2026-10-03T00:00:00'
    elif defect == 'truncated':
        frames.pop()
    elif defect == 'extra-frame':
        frames.append({**frames[-1], 'frame_id': 176})
    elif defect == 'duplicate-frame':
        frames[2]['frame_id'] = 2
    elif defect == 'duplicate-track':
        frames[0]['players'].append(dict(frames[0]['players'][0]))
    elif defect == 'invalid-box':
        frames[0]['players'][0]['bbox'] = [0, 20, 853, 30]
    elif defect == 'unknown-team-enum':
        frames[0]['players'][0]['team'] = 'guessed-team'
    elif defect == 'nonfinite':
        frames[0]['players'][0]['confidence'] = float('nan')
    elif defect == 'timestamps':
        metadata['decoded_timestamps_ms'][1] = 0
    elif defect == 'timestamps-nan':
        metadata['decoded_timestamps_ms'][1] = float('nan')
    elif defect == 'source-sha':
        metadata['sha256'] = '0' * 64
    elif defect == 'report-sha':
        report['source']['sha256'] = '0' * 64
    elif defect == 'dimensions':
        report['source']['width'] = 853
    elif defect == 'event-count':
        report['foul_candidates'] = 0
    elif defect == 'event-time':
        events[0]['media_pts_seconds'] = -1
    elif defect == 'future-event':
        events[0]['event']['evidence']['emitted_time_s'] = 4
    elif defect == 'invalid-event-id':
        events[0]['event']['id'] = None
    elif defect == 'duplicate-event':
        events.append(events[0])
        report['foul_candidates'] = 2
    elif defect == 'region-dimensions':
        events[0]['event']['evidence'].update(region_xyxy=[10, 20, 30, 40],
                                            source_width=1280, source_height=720)
    elif defect == 'region-box':
        events[0]['event']['evidence'].update(region_xyxy=[10, 20, 5, 40],
                                            source_width=852, source_height=480)
    elif defect == 'event-frame':
        events[0]['source_frame_id'] = 176
    elif defect == 'unregistered-sibling':
        f['artifacts'].pop()
    elif defect == 'wrong-artifact-kind':
        f['artifacts'][0]['kind'] = 'video'
    write_records(f)
    if defect == 'partial-frame-line':
        path = f['directory'] / 'frame-states.jsonl'
        path.write_text(path.read_text().rstrip('\n'))
    elif defect == 'partial-event-line':
        path = f['directory'] / 'candidate-events.jsonl'
        path.write_text(path.read_text().rstrip('\n'))
    assert f['client'].get(base()).status_code == 409


def test_all_record_files_change_revision_and_source_change_invalidates(presentation):
    f, client = presentation, presentation['client']
    for key in ('frames', 'metadata', 'report', 'events'):
        revision = client.get(base()).json()['revision']
        if key == 'frames':
            f[key][0]['players'][0]['confidence'] = 0.8
        elif key == 'metadata':
            f[key]['decoded_timestamps_ms'][1] += 0.1
        elif key == 'report':
            f[key]['new_metric'] = 12
        else:
            f[key][0]['event']['foul_details']['severity'] = 'unknown'
        write_records(f)
        assert client.get(base() + '/frames?revision=' + revision).status_code == 409
        assert client.get(base()).json()['revision'] != revision
    f['source'].write_bytes(b'replaced original media')
    assert client.get(base()).status_code == 409


def test_records_changed_while_reading_are_rejected(presentation, monkeypatch):
    import showcase.foul_presentation as module
    client = presentation['client']
    revision = client.get(base()).json()['revision']
    original = module._frame
    def change_record(line, expected, width, height):
        presentation['report']['new_metric'] = 12
        (presentation['directory'] / 'report.json').write_text(json.dumps(presentation['report']))
        return original(line, expected, width, height)
    monkeypatch.setattr(module, '_frame', change_record)
    assert client.get(base() + '/frames?limit=1&revision=' + revision).status_code == 409


def _modern(presentation, monkeypatch):
    f = presentation
    verification = {'boundary_fixture': True}
    fingerprint = {'config_sha256': configuration_sha256(verification),
                   'model_sha256': '1' * 64, 'core_manifest_sha256': '2' * 64,
                   'external_source_sha256': '3' * 64}
    f['report'].update(model_id='mvit-v2-local', config_sha256=fingerprint['config_sha256'],
                       external_source_sha256=fingerprint['external_source_sha256'],
                       models={'foul': {'sha256': fingerprint['model_sha256']}},
                       source_code={'verification': {'verified': True,
                                    'manifest_sha256': fingerprint['core_manifest_sha256']}},
                       checkpoint_validation={'strict': True, 'state_tensors': 1,
                                              'missing_keys': [], 'unexpected_keys': []})
    f['report']['config'].update(detection_profile='mvit-contact-v3', verification=verification)
    for artifact in f['artifacts']:
        artifact.update(detection_profile='mvit-contact-v3', model_id='mvit-v2-local',
                        detector_fingerprint=dict(fingerprint))
    f['events'][0]['source_frame_id'] = 103
    f['events'][0]['event']['evidence'].update(
        detection_profile='mvit-contact-v3', model_id='mvit-v2-local', event_time_s=3.0,
        emitted_time_s=3.4, evidence_start_s=2.5, evidence_end_s=3.4,
        region_xyxy=[10, 20, 40, 80], source_width=852, source_height=480,
        involved_targets=[], offender=None, victim=None)
    monkeypatch.setattr(f['repository'].jobs.foul_validation, 'snapshot', lambda profile: dict(fingerprint))
    write_records(f)
    return fingerprint


def test_qualified_events_preserve_causal_times_and_unknown_attribution(presentation, monkeypatch):
    fingerprint = _modern(presentation, monkeypatch)
    result = presentation['client'].get(base()).json()
    assert result['detector_fingerprint'] == fingerprint
    evidence = result['events'][0]['event']['evidence']
    assert evidence['event_time_s'] == 3.0 and evidence['emitted_time_s'] == 3.4
    assert evidence['involved_targets'] == [] and evidence['offender'] is None
    assert 'identity' not in result['events'][0]['event']


@pytest.mark.parametrize('container', ['evidence', 'foul_details'])
@pytest.mark.parametrize('field', ['offence_score', 'action_score', 'severity_score'])
@pytest.mark.parametrize('value', [-0.1, 1.1, True, '0.8'])
def test_optional_model_scores_are_bounded(presentation, container, field, value):
    presentation['events'][0]['event'][container][field] = value
    write_records(presentation)
    assert presentation['client'].get(base()).status_code == 409


def test_evidence_start_alone_cannot_refer_to_future_frames(presentation):
    presentation['events'][0]['event']['evidence']['evidence_start_s'] = 4.0
    write_records(presentation)
    assert presentation['client'].get(base()).status_code == 409


@pytest.mark.parametrize('container', ['event', 'evidence', 'foul_details'])
@pytest.mark.parametrize('value', ['no_offence', 'No offence'])
def test_no_offence_result_never_becomes_a_native_foul_candidate(presentation, container, value):
    event = presentation['events'][0]['event']
    target = event if container == 'event' else event[container]
    target['decision' if container != 'foul_details' else 'severity'] = value
    write_records(presentation)
    assert presentation['client'].get(base()).status_code == 409


@pytest.mark.parametrize('field', ['event_time_s', 'emitted_time_s'])
def test_event_and_evidence_time_aliases_cannot_disagree(presentation, monkeypatch, field):
    _modern(presentation, monkeypatch)
    event = presentation['events'][0]['event']
    event[field] = event['evidence'][field] + 0.05
    write_records(presentation)
    assert presentation['client'].get(base()).status_code == 409


@pytest.mark.parametrize('defect', ['current-snapshot', 'artifact-snapshot', 'report-snapshot',
                                    'event-profile', 'checkpoint', 'config-hash'])
def test_detector_bindings_reject_borrowed_records_and_stale_qualification(presentation, monkeypatch, defect):
    _modern(presentation, monkeypatch)
    client = presentation['client']
    assert client.get(base()).status_code == 200
    if defect == 'current-snapshot':
        monkeypatch.setattr(presentation['repository'].jobs.foul_validation, 'snapshot',
                            lambda profile: (_ for _ in ()).throw(ValueError('Qualification revoked')))
    elif defect == 'artifact-snapshot':
        presentation['artifacts'][1]['detector_fingerprint']['model_sha256'] = '0' * 64
    elif defect == 'report-snapshot':
        presentation['report']['config_sha256'] = '0' * 64
    elif defect == 'event-profile':
        presentation['events'][0]['event']['evidence']['detection_profile'] = 'legacy-v1'
    elif defect == 'checkpoint':
        presentation['report']['checkpoint_validation']['missing_keys'] = ['missing']
    else:
        presentation['report']['config']['verification']['changed'] = True
    write_records(presentation)
    assert client.get(base()).status_code == 409


def test_only_registered_results_and_unique_identifiers_are_accepted(presentation):
    client = presentation['client']
    assert client.get(base('unregistered')).status_code == 404
    presentation['artifacts'].append(dict(presentation['artifacts'][0]))
    write_records(presentation)
    assert client.get(base()).status_code == 409


def test_job_directory_requires_completed_matching_foul_task(presentation):
    f = presentation
    job_id = 'a' * 32
    target = f['repository'].catalog.root / 'jobs' / job_id
    target.parent.mkdir()
    f['directory'].rename(target)
    f['directory'] = target
    for artifact in f['artifacts']:
        artifact['path'] = 'jobs/' + job_id + '/' + Path(artifact['path']).name
    jobs = f['repository'].jobs
    job = {'id': job_id, 'case_id': 'foul-1', 'mode': 'foul_only', 'kind': 'foul',
           'status': 'running', 'source_sha256': f['metadata']['sha256']}
    jobs.jobs[job_id] = job
    write_records(f)
    assert f['client'].get(base()).status_code == 409
    job['status'] = 'completed'
    assert f['client'].get(base()).status_code == 200
    job['mode'] = 'combined'
    assert f['client'].get(base()).status_code == 409
