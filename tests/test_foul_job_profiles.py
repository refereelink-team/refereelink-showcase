"""Versioned foul-job boundaries; execute on the remote CUDA host."""
from dataclasses import replace
from pathlib import Path
import json
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from showcase.app import JobRequest
from showcase.catalog import MediaCatalog
from showcase.jobs import JobManager
from showcase.settings import Settings
from showcase.foul_validation import FoulValidation, configuration_sha256


@pytest.fixture
def settings(tmp_path):
    media = tmp_path / 'media'
    (media / 'input').mkdir(parents=True)
    (media / 'input/foul-1.mp4').write_bytes(b'job boundary fixture, not model evidence')
    core = tmp_path / 'core'
    (core / 'tools').mkdir(parents=True)
    (core / 'tools/run_night_ablation.py').write_text('# Never execute in boundary tests')
    runner = core / 'tools/run_night_ablation.py'
    (core / 'source-manifest.json').write_text(json.dumps({'python_files': {
        'tools/run_night_ablation.py': {'extracted_sha256': hashlib.sha256(runner.read_bytes()).hexdigest()}
    }}))
    (tmp_path / 'foul.pt').write_bytes(b'strict-checkpoint boundary fixture, never loaded')
    (tmp_path / 'foul-code').mkdir()
    (tmp_path / 'foul-code/author.py').write_text('# Author-source boundary fixture')
    return Settings(
        media_root=media, state_root=tmp_path / 'state', upstream='http://unused.invalid',
        core_root=core, core_python=Path('/not/executed'), player_model=tmp_path / 'player.pt',
        pitch_model=tmp_path / 'pitch.pt', foul_model=tmp_path / 'foul.pt',
        foul_code=tmp_path / 'foul-code', bundle=tmp_path / 'bundle.npz', enforce_cuda=False,
        foul_qualification=tmp_path / 'foul-qualification-v3.json',
    )


def test_old_client_keeps_legacy_default_and_new_profile_is_a_distinct_task(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    legacy = manager.submit('foul', 'foul-1')
    pair = manager.submit('foul', 'foul-1', 'mvit-pair-v2')
    assert legacy['detection_profile'] == 'legacy-v1'
    assert legacy['id'] != pair['id']
    assert manager.submit('foul', 'foul-1', 'mvit-pair-v2')['id'] == pair['id']
    assert pair['model_id'] == 'mvit-v2-s-vars'
    assert manager.foul_configuration()['default_profile'] == 'legacy-v1'
    assert JobRequest(kind='foul', case_id='foul-1').detection_profile is None


def test_default_changes_only_when_explicitly_configured(settings):
    configured = replace(settings, foul_detection_profile='mvit-pair-v2')
    manager = JobManager(configured, MediaCatalog(settings.media_root))
    assert manager.submit('foul', 'foul-1')['detection_profile'] == 'mvit-pair-v2'
    assert manager.foul_configuration()['default_profile'] == 'mvit-pair-v2'
    assert manager.submit('foul', 'foul-1', 'legacy-v1')['detection_profile'] == 'legacy-v1'


def acceptance(settings):
    evidence = settings.state_root.parent / 'acceptance-evidence.json'
    evidence.write_text('{"scope":"development_clip","accepted":true}')
    assets = FoulValidation(settings).assets('mvit-contact-v3')
    config = {'offence_threshold': 0.6, 'confirmation_windows': 1,
              'confirmation_mode': 'fresh_model_with_independent_aftermath'}
    record = {
        'detection_profile': 'mvit-contact-v3', 'status': 'passed',
        'scope': 'development_clip', 'verified_at': '2026-10-03T12:00:00+00:00',
        'config': config, 'config_sha256': configuration_sha256(config),
        **assets, 'evidence_path': str(evidence),
        'evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }
    settings.foul_qualification.write_text(json.dumps(record))
    return record


def test_contact_default_requires_matching_acceptance_record(settings):
    configured = replace(settings, foul_detection_profile='mvit-contact-v3')
    manager = JobManager(configured, MediaCatalog(settings.media_root))
    assert manager.foul_configuration()['default_profile'] == 'legacy-v1'
    with pytest.raises(HTTPException) as error:
        manager.submit('foul', 'foul-1', 'mvit-contact-v3')
    assert error.value.status_code == 503
    record = acceptance(configured)
    assert manager.foul_configuration()['default_profile'] == 'mvit-contact-v3'
    job = manager.submit('foul', 'foul-1')
    assert job['model_id'] == 'mvit-v2-local'
    assert job['detector_fingerprint']['config_sha256'] == record['config_sha256']
    assert job['qualification']['scope'] == 'development_clip'
    assert 'evidence_path' not in job['qualification']


@pytest.mark.parametrize('defect', ['evidence', 'checkpoint', 'author_source', 'core_source',
                                  'manifest', 'config', 'scope', 'status', 'timestamp'])
def test_modified_acceptance_or_assets_disable_contact_default(settings, defect):
    configured = replace(settings, foul_detection_profile='mvit-contact-v3')
    record = acceptance(configured)
    if defect == 'evidence':
        Path(record['evidence_path']).write_text('different evaluation')
    elif defect == 'checkpoint':
        settings.foul_model.write_bytes(b'different checkpoint')
    elif defect == 'author_source':
        (settings.foul_code / 'author.py').write_text('# Different author source')
    elif defect == 'core_source':
        (settings.core_root / 'tools/run_night_ablation.py').write_text('# Different detector')
    elif defect == 'manifest':
        (settings.core_root / 'source-manifest.json').write_text('{"python_files":{}}')
    else:
        if defect == 'config':
            record['config']['offence_threshold'] = 0.1
        else:
            record[{'scope': 'scope', 'status': 'status', 'timestamp': 'verified_at'}[defect]] = 'invalid'
        settings.foul_qualification.write_text(json.dumps(record))
    manager = JobManager(configured, MediaCatalog(settings.media_root))
    assert manager.foul_configuration()['default_profile'] == 'legacy-v1'
    assert manager.foul_configuration()['qualification'] is None


def test_changed_checkpoint_is_a_distinct_pending_task(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    first = manager.submit('foul', 'foul-1', 'mvit-pair-v2')
    settings.foul_model.write_bytes(b'different installed checkpoint')
    second = manager.submit('foul', 'foul-1', 'mvit-pair-v2')
    assert first['id'] != second['id']
    assert first['detector_fingerprint'] != second['detector_fingerprint']


def test_profile_never_silently_falls_back_when_unknown_or_unavailable(settings):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    for kind, profile, code in [
        ('foul', 'future-unknown', 422),
        ('foul', '', 422),
        ('tracking', 'mvit-pair-v2', 422),
        ('foul', 'multidim-full-v2', 503),
    ]:
        with pytest.raises(HTTPException) as error:
            manager.submit(kind, 'foul-1', profile)
        assert error.value.status_code == code
    assert not manager.list()


def test_loaded_historical_job_is_explicitly_legacy(settings):
    settings.state_root.mkdir()
    (settings.state_root / 'job-old.json').write_text(json.dumps({
        'id': 'old', 'kind': 'foul', 'case_id': 'foul-1', 'mode': 'foul_only',
        'status': 'completed', 'created_at': 1, 'artifacts': [],
    }))
    old = JobManager(settings, MediaCatalog(settings.media_root)).get('old')
    assert old['detection_profile'] == 'legacy-v1'
    assert old['model_id'] == 'mvit-v2-s-vars'


def test_multidim_availability_requires_all_author_runtime_modules(settings, tmp_path):
    source = tmp_path / 'author-source'
    source.mkdir()
    checkpoint = tmp_path / 'author-model.pth.tar'
    checkpoint.write_bytes(b'boundary fixture, never loaded as a model')
    configured = replace(settings, multidim_model=checkpoint, multidim_code=source)
    manager = JobManager(configured, MediaCatalog(settings.media_root))
    def alternative():
        return next(item for item in manager.foul_configuration()['profiles']
                    if item['id'] == 'multidim-full-v2')
    assert alternative()['available'] is False
    for name in ('utils.py', 'multidim_stacker_mod.py', 'mvaggregate.py'):
        (source / name).write_text('# File presence only; not model acceptance')
    assert alternative()['available'] is True


def report_fixture(profile='mvit-pair-v2'):
    return {
        'status': 'complete', 'mode': 'foul_only', 'environment': {'device': 'cuda'},
        'decoded_frames': 175, 'processed_frames': 175, 'output_frames': 175,
        'dropped_frame_count': 0, 'foul_errors': [], 'foul_actual_forward_windows': 1,
        'foul_candidates': 0,
        'model_id': 'mvit-v2-s-vars',
        'checkpoint_validation': {'strict': True, 'state_tensors': 418,
                                  'missing_keys': [], 'unexpected_keys': []},
        'config': {'pitch_enabled': False, 'foul_enabled': True,
                   'detection_profile': profile},
    }


@pytest.mark.parametrize('defect', ['profile', 'model', 'missing_version'])
def test_completed_output_cannot_be_registered_as_a_different_configuration(settings, tmp_path, defect):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture()
    if defect == 'profile':
        report['config']['detection_profile'] = 'legacy-v1'
    elif defect == 'missing_version':
        report['config'].pop('detection_profile')
        report.pop('model_id')
    else:
        report['model_id'] = 'another-model'
    (tmp_path / 'report.json').write_text(json.dumps(report))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars'}
    with pytest.raises(RuntimeError, match='detection profile and model'):
        manager._validate_output(tmp_path, job)


def test_historical_unversioned_report_is_accepted_only_as_legacy(settings, tmp_path, monkeypatch):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture()
    report['config'].pop('detection_profile')
    report.pop('model_id')
    monkeypatch.setattr('showcase.jobs.subprocess.run', lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps({'streams': [{'codec_name': 'h264', 'nb_read_frames': '175'}]})))
    (tmp_path / 'report.json').write_text(json.dumps(report))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'legacy-v1', 'model_id': 'mvit-v2-s-vars'}
    assert manager._validate_output(tmp_path, job)['foul_actual_forward_windows'] == 1
    job['detection_profile'] = 'mvit-full-v2'
    with pytest.raises(RuntimeError, match='detection profile and model'):
        manager._validate_output(tmp_path, job)


def test_zero_forwards_require_explicit_empty_candidate_scan(settings, tmp_path, monkeypatch):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture()
    report.update(foul_actual_forward_windows=0, interaction_candidates=0,
                  foul_skip_reason='no_interaction_candidates', foul_candidates=0)
    (tmp_path / 'candidate-events.jsonl').write_text('')
    monkeypatch.setattr('showcase.jobs.subprocess.run', lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps({'streams': [{'codec_name': 'h264', 'nb_read_frames': '175'}]})))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars'}
    (tmp_path / 'report.json').write_text(json.dumps(report))
    assert manager._validate_output(tmp_path, job)['foul_actual_forward_windows'] == 0
    report['interaction_candidates'] = 1
    (tmp_path / 'report.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match='No real foul-model forward'):
        manager._validate_output(tmp_path, job)


def test_contact_empty_scan_requires_explicit_supported_contact_count(settings, tmp_path, monkeypatch):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture('mvit-contact-v3')
    report.update(model_id='mvit-v2-local', foul_actual_forward_windows=0,
                  interaction_candidates=20, contact_supported_candidates=0,
                  foul_skip_reason='no_supported_contacts', foul_candidates=0)
    (tmp_path / 'candidate-events.jsonl').write_text('')
    monkeypatch.setattr('showcase.jobs.subprocess.run', lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps({'streams': [{'codec_name': 'h264', 'nb_read_frames': '175'}]})))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-contact-v3', 'model_id': 'mvit-v2-local'}
    (tmp_path / 'report.json').write_text(json.dumps(report))
    assert manager._validate_output(tmp_path, job)['foul_actual_forward_windows'] == 0
    report['contact_supported_candidates'] = 1
    (tmp_path / 'report.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match='No real foul-model forward'):
        manager._validate_output(tmp_path, job)


@pytest.mark.parametrize('defect', ['configuration', 'configuration_hash', 'checkpoint_hash',
                                  'manifest_hash', 'unverified_source', 'author_hash'])
def test_report_cannot_relabel_an_actual_run_as_the_submitted_snapshot(settings, tmp_path, defect):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    fingerprint = manager.foul_validation.snapshot('mvit-pair-v2')
    report = report_fixture()
    report.update(
        config_sha256=fingerprint['config_sha256'],
        models={'foul': {'sha256': fingerprint['model_sha256']}},
        source_code={'verification': {'verified': True,
                                     'manifest_sha256': fingerprint['core_manifest_sha256']}},
        external_source_sha256=fingerprint['external_source_sha256'],
    )
    from showcase.foul_validation import V2_CONFIGURATION
    report['config']['verification'] = dict(V2_CONFIGURATION)
    if defect == 'configuration':
        report['config']['verification']['confirmation_windows'] = 100
    elif defect == 'configuration_hash':
        report['config_sha256'] = 'different hash'
    elif defect == 'checkpoint_hash':
        report['models']['foul']['sha256'] = 'different checkpoint'
    elif defect == 'manifest_hash':
        report['source_code']['verification']['manifest_sha256'] = 'different source'
    elif defect == 'unverified_source':
        report['source_code']['verification']['verified'] = False
    else:
        report['external_source_sha256'] = 'different author source'
    (tmp_path / 'report.json').write_text(json.dumps(report))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars',
           'detector_fingerprint': fingerprint}
    with pytest.raises(RuntimeError, match='submitted detector snapshot'):
        manager._validate_output(tmp_path, job)


@pytest.mark.parametrize('defect', ['strict', 'missing_keys', 'unexpected_keys', 'no_tensors',
                                  'missing_validation', 'null_validation'])
def test_modern_result_requires_strict_complete_checkpoint_loading(settings, tmp_path, defect):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture()
    if defect == 'strict':
        report['checkpoint_validation']['strict'] = False
    elif defect == 'missing_validation':
        report.pop('checkpoint_validation')
    elif defect == 'null_validation':
        report['checkpoint_validation'] = None
    elif defect == 'no_tensors':
        report['checkpoint_validation']['state_tensors'] = 0
    else:
        report['checkpoint_validation'][defect] = ['unmatched.weight']
    (tmp_path / 'report.json').write_text(json.dumps(report))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars'}
    with pytest.raises(RuntimeError, match='strict checkpoint loading'):
        manager._validate_output(tmp_path, job)


@pytest.mark.parametrize('defect', ['count', 'profile', 'model', 'missing_version', 'missing_file',
                                  'malformed_json'])
def test_modern_candidate_records_cannot_borrow_legacy_or_mismatched_events(settings, tmp_path, defect):
    manager = JobManager(settings, MediaCatalog(settings.media_root))
    report = report_fixture()
    report['foul_candidates'] = 1
    evidence = {'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars'}
    event = {'event': {'event_type': 'foul_candidate', 'evidence': evidence}}
    if defect == 'count':
        report['foul_candidates'] = 0
    elif defect == 'profile':
        evidence['detection_profile'] = 'legacy-v1'
    elif defect == 'model':
        evidence['model_id'] = 'another-model'
    elif defect == 'missing_version':
        evidence.clear()
    if defect != 'missing_file':
        (tmp_path / 'candidate-events.jsonl').write_text(
            '{invalid json' if defect == 'malformed_json' else json.dumps(event) + '\n')
    (tmp_path / 'report.json').write_text(json.dumps(report))
    job = {'case_id': 'foul-1', 'mode': 'foul_only',
           'detection_profile': 'mvit-pair-v2', 'model_id': 'mvit-v2-s-vars'}
    with pytest.raises(RuntimeError, match='CUDA candidate'):
        manager._validate_output(tmp_path, job)
