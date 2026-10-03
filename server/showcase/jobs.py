from __future__ import annotations
import hashlib
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from fastapi import HTTPException
from .catalog import CLIPS, MediaCatalog
from .foul_validation import FoulValidation, HASH_FIELDS, configuration_sha256
from .settings import Settings


MODES = {'tracking': 'projection_only', 'foul': 'foul_only', 'combined': 'combined'}
MODE_LABELS = {'projection_only': '跟踪与场地投影', 'foul_only': '跟踪与犯规候选',
               'combined': '跟踪、投影与犯规候选'}
FOUL_PROFILES = {
    'legacy-v1': ('历史算法', 'mvit-v2-s-vars'),
    'mvit-full-v2': ('MViT · 全画面复核', 'mvit-v2-s-vars'),
    'mvit-pair-v2': ('MViT · 交互区域复核', 'mvit-v2-s-vars'),
    'multidim-full-v2': ('MultiDimStacker · 单视角复核', 'multidim-stacker'),
    'mvit-contact-v3': ('MViT · 接触与动作复核', 'mvit-v2-local'),
}


class JobManager:
    def __init__(self, settings: Settings, catalog: MediaCatalog):
        self.settings, self.catalog = settings, catalog
        self.foul_validation = FoulValidation(settings)
        if settings.foul_detection_profile not in FOUL_PROFILES:
            raise RuntimeError('Unknown configured foul detection profile')
        self.lock = threading.RLock()
        self.gpu_lease = threading.Lock()
        self.queue = queue.Queue(maxsize=3)
        self.stopped = threading.Event()
        self.jobs = {}
        self.process = None
        self.worker = None
        self.boot_id = uuid.uuid4().hex
        self._foul_preparation_started = False
        self._foul_preparation = {
            'enabled': False, 'reason': 'not_started', 'fingerprint': None,
            'clips': {clip['id']: {'job_id': None, 'reason': None}
                      for clip in CLIPS if clip['id'].startswith('foul-')},
        }
        settings.state_root.mkdir(parents=True, exist_ok=True)
        for path in settings.state_root.glob('job-*.json'):
            try:
                job = json.loads(path.read_text())
                if job.get('mode') != 'projection_only':
                    job.setdefault('detection_profile', 'legacy-v1')
                    job.setdefault('model_id', 'mvit-v2-s-vars')
                if job['status'] in {'queued', 'running'}:
                    job.update(status='failed', error='Server restarted before completion',
                               finished_at=time.time())
                    self._save(job)
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError):
                continue

    def start(self):
        self.worker = threading.Thread(target=self._run, name='showcase-cuda-jobs', daemon=True)
        self.worker.start()

    def prepare_foul_clips(self, *, cuda_verified: bool):
        """Queue fresh qualified inference once per verified CUDA service boot.

        Completed tasks remain historical. Only this boot's submitted task IDs
        may unlock the automatic demonstration, including empty measured scans.
        """
        with self.lock:
            if self._foul_preparation_started:
                return
            self._foul_preparation_started = True
            if not self.settings.prepare_foul_on_startup:
                self._foul_preparation['reason'] = 'startup_preparation_disabled'
                return
            if not self.settings.enforce_cuda or not cuda_verified:
                self._foul_preparation['reason'] = 'cuda_unverified'
                return
            self._foul_preparation.update(enabled=True, reason=None)
        try:
            qualification = self.foul_validation.qualification()
            if qualification is None:
                raise ValueError('No qualified detector snapshot')
            fingerprint = self.foul_validation.snapshot('mvit-contact-v3', qualification)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            with self.lock:
                self._foul_preparation['reason'] = 'qualification_unavailable'
            return
        with self.lock:
            self._foul_preparation['fingerprint'] = fingerprint
            case_ids = tuple(self._foul_preparation['clips'])
        for case_id in case_ids:
            try:
                job = self.submit('foul', case_id, 'mvit-contact-v3')
                reason = (None if job.get('detector_fingerprint') == fingerprint
                          else 'qualification_changed')
                selected = {'job_id': job['id'], 'reason': reason}
            except HTTPException as exc:
                selected = {'job_id': None,
                            'reason': 'queue_full' if exc.status_code == 429 else 'submission_failed'}
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                selected = {'job_id': None, 'reason': 'submission_failed'}
            with self.lock:
                self._foul_preparation['clips'][case_id] = selected

    def foul_preparation(self, repository):
        """Read boot-pinned preparation progress without submitting work."""
        with self.lock:
            enabled = self._foul_preparation['enabled']
            reason = self._foul_preparation['reason']
            fingerprint = self._foul_preparation['fingerprint']
            selected = {key: dict(value) for key, value in self._foul_preparation['clips'].items()}
            jobs = {key: dict(self.jobs[value['job_id']])
                    for key, value in selected.items() if value['job_id'] in self.jobs}
        if enabled and reason is None and fingerprint is not None:
            try:
                qualification = self.foul_validation.qualification()
                if qualification is None:
                    reason = 'qualification_unavailable'
                elif self.foul_validation.snapshot('mvit-contact-v3', qualification) != fingerprint:
                    reason = 'qualification_changed'
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                reason = 'qualification_unavailable'
        clips = []
        for case_id, selection in selected.items():
            job = jobs.get(case_id)
            item = {'case_id': case_id, 'status': 'preparing' if enabled else 'disabled',
                    'job_status': job['status'] if job else None,
                    'progress': job.get('progress', 0.0) if job else 0.0,
                    'job_id': selection['job_id'], 'result_id': None, 'revision': None,
                    'reason': reason or selection['reason']}
            if item['reason'] is not None:
                item['status'] = 'error' if enabled else 'disabled'
            elif job is None:
                item.update(status='error', reason='job_unavailable')
            elif (job.get('kind') != 'foul' or job.get('case_id') != case_id
                  or job.get('mode') != 'foul_only'
                  or job.get('detection_profile') != 'mvit-contact-v3'
                  or job.get('detector_fingerprint') != fingerprint):
                item.update(status='error', reason='job_binding_changed')
            elif job['status'] == 'failed':
                item.update(status='error', reason='inference_failed')
            elif job['status'] == 'completed':
                result_id = job['id'] + '-frame-states'
                try:
                    result = repository.result(result_id).descriptor
                    if (result['case_id'] != case_id
                            or result['detection_profile'] != 'mvit-contact-v3'
                            or result.get('detector_fingerprint') != fingerprint):
                        raise ValueError('Native descriptor does not match its boot task')
                    item.update(status='ready', result_id=result_id, revision=result['revision'])
                except (HTTPException, OSError, ValueError, KeyError, TypeError, AttributeError):
                    item.update(status='error', reason='native_result_invalid')
            elif job['status'] not in {'queued', 'running'}:
                item.update(status='error', reason='job_unavailable')
            clips.append(item)
        status = ('disabled' if not enabled else 'error' if any(
            clip['status'] == 'error' for clip in clips) else 'ready' if all(
            clip['status'] == 'ready' for clip in clips) else 'preparing')
        return {'schema_version': 1, 'boot_id': self.boot_id, 'enabled': enabled,
                'status': status, 'detection_profile': 'mvit-contact-v3',
                'model_id': FOUL_PROFILES['mvit-contact-v3'][1], 'reason': reason, 'clips': clips}

    def _save(self, job):
        path = self.settings.state_root / ('job-' + job['id'] + '.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(job, ensure_ascii=False))
        temp.replace(path)

    def foul_configuration(self):
        qualification = self.foul_validation.qualification()
        requested = self.settings.foul_detection_profile
        default = 'legacy-v1' if requested == 'mvit-contact-v3' and not qualification else requested
        multidim_available = bool(
            self.settings.multidim_model and self.settings.multidim_model.is_file()
            and self.settings.multidim_code and all(
                (self.settings.multidim_code / name).is_file()
                for name in ('utils.py', 'multidim_stacker_mod.py', 'mvaggregate.py')))
        return {
            'default_profile': default,
            'qualification': qualification,
            'profiles': [
                {'id': key, 'label': label, 'model_id': model,
                 'available': (bool(qualification) if key == 'mvit-contact-v3' else
                               key != 'multidim-full-v2' or multidim_available),
                 'experimental': key != 'legacy-v1' and not (
                    key == 'mvit-contact-v3' and qualification),
                 'qualification_scope': qualification['scope']
                    if key == 'mvit-contact-v3' and qualification else None}
                for key, (label, model) in FOUL_PROFILES.items()
            ],
        }

    def submit(self, kind: str, case_id: str, detection_profile: str | None = None):
        if kind not in MODES:
            raise HTTPException(422, 'Unsupported experiment kind')
        if kind == 'tracking' and detection_profile is not None:
            raise HTTPException(422, 'A tracking job does not use a foul detection profile')
        foul_configuration = self.foul_configuration()
        foul_profile = None if kind == 'tracking' else (
            foul_configuration['default_profile']
            if detection_profile is None else detection_profile)
        if foul_profile is not None and foul_profile not in FOUL_PROFILES:
            raise HTTPException(422, 'Unsupported foul detection profile')
        if foul_profile in {'multidim-full-v2', 'mvit-contact-v3'} and not next(
                item['available'] for item in foul_configuration['profiles']
                if item['id'] == foul_profile):
            raise HTTPException(503, 'Requested foul profile is not ready or qualified')
        fingerprint = None
        qualification = None
        if foul_profile not in {None, 'legacy-v1'}:
            try:
                qualification = (foul_configuration['qualification']
                                 if foul_profile == 'mvit-contact-v3' else None)
                fingerprint = self.foul_validation.snapshot(foul_profile, qualification)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(503, 'Foul profile code or asset snapshot is invalid') from exc
        clip = self.catalog.clip(case_id)
        source = self.catalog.safe_path('input/' + case_id + '.mp4')
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        source_sha256 = digest.hexdigest()
        profile = 'legacy'
        if (MODES[kind] != 'foul_only'
                and clip.get('pitch_profile_id') == 'source-informed105'
                and source_sha256 == clip.get('profile_source_sha256')):
            profile = 'source-informed105'
        runner = self.settings.core_root / 'tools/run_night_ablation.py'
        if not runner.exists():
            raise HTTPException(503, 'CUDA experiment runner is not installed')
        with self.lock:
            if self.stopped.is_set():
                raise HTTPException(503, 'Experiment service is stopping')
            active = next((job for job in self.jobs.values()
                           if job['kind'] == kind and job['case_id'] == case_id
                           and job.get('detection_profile') == foul_profile
                           and job.get('source_sha256') == source_sha256
                           and job.get('detector_fingerprint') == fingerprint
                           and job['status'] in {'queued', 'running'}), None)
            if active:
                return dict(active)
            job = {'id': uuid.uuid4().hex, 'kind': kind, 'case_id': case_id,
                   'mode': MODES[kind], 'status': 'queued', 'progress': 0.0,
                   'created_at': time.time(), 'error': None, 'artifacts': [], 'summary': None,
                   'pitch_profile_id': profile, 'paint_enabled': profile != 'legacy',
                   'source_sha256': source_sha256}
            if foul_profile is not None:
                job.update(detection_profile=foul_profile,
                           model_id=FOUL_PROFILES[foul_profile][1])
            if fingerprint is not None:
                job['detector_fingerprint'] = fingerprint
                if qualification:
                    job['qualification'] = qualification
            try:
                self.queue.put_nowait(job['id'])
            except queue.Full as exc:
                raise HTTPException(429, 'GPU queue is full; please wait for the current run') from exc
            self.jobs[job['id']] = job
            self._save(job)
            return dict(job)

    def list(self):
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                                               key=lambda item: item['created_at'], reverse=True)]

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, 'Unknown experiment job')
            return dict(self.jobs[job_id])

    @staticmethod
    def _terminate_process(process):
        if process is None:
            return
        # A reaped parent can leave descendants in its process group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # Waiting for the parent alone does not prove its children exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    def stop(self):
        with self.lock:
            self.stopped.set()
            process = self.process
            for job in self.jobs.values():
                if job['status'] == 'queued':
                    job.update(status='failed', error='Experiment service stopped',
                               finished_at=time.time())
                    self._save(job)
        self._terminate_process(process)
        if self.worker:
            self.worker.join(timeout=8)

    def _wait(self, process, directory, job):
        deadline = time.monotonic() + self.settings.job_timeout_s
        offset, frames = 0, 0
        file_identity = None
        expected = self.catalog.clip(job['case_id'])['decoded_frames']
        while process.poll() is None:
            if self.stopped.wait(0.5):
                raise RuntimeError('Experiment service stopped')
            if time.monotonic() >= deadline:
                raise RuntimeError('CUDA experiment exceeded its time limit')
            states = directory / 'frame-states.jsonl'
            if states.exists():
                with states.open('rb') as stream:
                    stat = os.fstat(stream.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    # Offline refinement publishes a replacement JSONL file.
                    # Count its frames afresh instead of appending its tail to
                    # the previously counted causal observations.
                    if identity != file_identity or stat.st_size < offset:
                        offset, frames = 0, 0
                        file_identity = identity
                    stream.seek(offset)
                    new = stream.read()
                    offset = stream.tell()
                    frames += new.count(b'\n')
                with self.lock:
                    job.update(progress=min(0.95, 0.05 + 0.9 * frames / expected),
                               processed_frames=min(frames, expected))
                    self._save(job)
        return process.returncode

    def _validate_output(self, directory, job):
        report = json.loads((directory / 'report.json').read_text())
        frames = report.get('decoded_frames', 0)
        config = report.get('config', {})
        expected = self.catalog.clip(job['case_id'])['decoded_frames']
        if (report.get('status') != 'complete' or report.get('mode') != job['mode']
                or report.get('environment', {}).get('device') != 'cuda'
                or frames != expected or report.get('processed_frames') != frames
                or report.get('output_frames') != frames or report.get('dropped_frame_count') != 0
                or config.get('pitch_enabled') != (job['mode'] != 'foul_only')
                or config.get('foul_enabled') != (job['mode'] != 'projection_only')
                or report.get('foul_errors')):
            raise RuntimeError('CUDA result failed completeness or experiment-mode validation')
        if job.get('pitch_profile_id') is not None and (
                config.get('pitch_profile_id') != job['pitch_profile_id']
                or config.get('paint_enabled') is not job['paint_enabled']
                or report.get('source', {}).get('sha256') != job['source_sha256']):
            raise RuntimeError('CUDA result does not match its source-bound geometry profile')
        if job['mode'] != 'projection_only':
            profile = job.get('detection_profile')
            reported_profile = config.get('detection_profile', config.get('foul_profile_id'))
            reported_model = report.get('model_id', config.get('model_id'))
            # Unversioned stored results belong only to the historical VARS
            # branch. They can never satisfy a newer profile's registration.
            if profile == 'legacy-v1' and reported_profile is None:
                reported_profile = 'legacy-v1'
                if reported_model is None:
                    reported_model = 'mvit-v2-s-vars'
            if profile is not None and (
                    reported_profile != profile
                    or reported_model != job.get('model_id')):
                raise RuntimeError('CUDA result does not match its detection profile and model')
            if profile not in {None, 'legacy-v1'}:
                fingerprint = job.get('detector_fingerprint')
                if fingerprint is not None:
                    actual = {
                        'config_sha256': report.get('config_sha256'),
                        'model_sha256': report.get('models', {}).get('foul', {}).get('sha256'),
                        'core_manifest_sha256': report.get('source_code', {}).get(
                            'verification', {}).get('manifest_sha256'),
                        'external_source_sha256': report.get('external_source_sha256'),
                    }
                    verified = report.get('source_code', {}).get('verification', {}).get('verified')
                    verification = config.get('verification')
                    if (verified is not True or not isinstance(verification, dict)
                            or any(actual[key] != fingerprint.get(key) for key in HASH_FIELDS)
                            or configuration_sha256(verification) != actual['config_sha256']):
                        raise RuntimeError('CUDA result does not match its submitted detector snapshot')
                checkpoint = report.get('checkpoint_validation', {})
                if (not isinstance(checkpoint, dict) or checkpoint.get('strict') is not True
                        or type(checkpoint.get('state_tensors')) is not int
                        or checkpoint['state_tensors'] < 1
                        or checkpoint.get('missing_keys') != []
                        or checkpoint.get('unexpected_keys') != []):
                    raise RuntimeError('CUDA result does not prove strict checkpoint loading')
                try:
                    records = [json.loads(line) for line in
                               (directory / 'candidate-events.jsonl').read_text().splitlines()
                               if line.strip()]
                except (OSError, ValueError) as exc:
                    raise RuntimeError('CUDA candidate records could not be read') from exc
                expected_candidates = report.get('foul_candidates')
                if (type(expected_candidates) is not int or expected_candidates < 0
                        or len(records) != expected_candidates):
                    raise RuntimeError('CUDA candidate count does not match its report')
                for record in records:
                    event = record.get('event') if isinstance(record, dict) else None
                    evidence = event.get('evidence') if isinstance(event, dict) else None
                    if (not isinstance(event, dict) or event.get('event_type') != 'foul_candidate'
                            or not isinstance(evidence, dict)
                            or evidence.get('detection_profile') != profile
                            or evidence.get('model_id') != reported_model):
                        raise RuntimeError('CUDA candidate does not match its detection profile and model')
            if report.get('foul_actual_forward_windows', 0) < 1:
                # A candidate-first scan may have nothing to classify. This is a
                # measured empty scan, never evidence of a model forward.
                empty_scan = (
                    profile not in {None, 'legacy-v1', 'mvit-contact-v3'}
                    and report.get('interaction_candidates') == 0
                    and report.get('foul_skip_reason') == 'no_interaction_candidates'
                    and report.get('foul_candidates') == 0)
                empty_scan = empty_scan or (
                    profile == 'mvit-contact-v3'
                    and all(type(report.get(key)) is int for key in (
                        'contact_supported_candidates', 'foul_actual_forward_windows',
                        'foul_candidates'))
                    and report.get('contact_supported_candidates') == 0
                    and report.get('foul_skip_reason') == 'no_supported_contacts'
                    and report.get('foul_actual_forward_windows') == 0
                    and report.get('foul_candidates') == 0)
                if not empty_scan:
                    raise RuntimeError('No real foul-model forward was recorded')
        video = directory / 'annotated.mp4'
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                '-count_frames', '-show_entries',
                                'stream=codec_name,nb_read_frames,width,height', '-of', 'json',
                                str(video)], capture_output=True, text=True, check=True, timeout=60)
        media = json.loads(probe.stdout)['streams'][0]
        if media['codec_name'] != 'h264' or int(media['nb_read_frames']) != frames:
            raise RuntimeError('Rendered video does not contain every processed frame')
        return report

    def _run(self):
        while not self.stopped.is_set():
            try:
                job_id = self.queue.get(timeout=0.3)
            except queue.Empty:
                continue
            with self.lock:
                job = self.jobs[job_id]
                if self.stopped.is_set():
                    self.queue.task_done()
                    break
            directory = self.catalog.root / 'jobs' / job_id
            process = None
            self.gpu_lease.acquire()
            try:
                with self.lock:
                    if self.stopped.is_set():
                        raise RuntimeError('Experiment service stopped')
                    job.update(status='running', progress=0.02, started_at=time.time())
                    self._save(job)
                directory.mkdir(parents=True, exist_ok=True)
                if job.get('detector_fingerprint') is not None:
                    actual = self.foul_validation.snapshot(job['detection_profile'])
                    if actual != job['detector_fingerprint']:
                        raise RuntimeError('Detector code or assets changed while the job was queued')
                args = [str(self.settings.core_python), '-B',
                        str(self.settings.core_root / 'tools/run_night_ablation.py'),
                        '--input', str(self.catalog.safe_path('input/' + job['case_id'] + '.mp4')),
                        '--mode', job['mode'], '--output', str(directory),
                        '--pitch-profile', job.get('pitch_profile_id', 'legacy'),
                        '--bundle', str(self.settings.bundle),
                        '--player-model', str(self.settings.player_model),
                        '--pitch-model', str(self.settings.pitch_model),
                        '--foul-model', str(self.settings.foul_model),
                        '--foul-code', str(self.settings.foul_code)]
                if job['mode'] != 'projection_only':
                    args += ['--foul-profile', job.get('detection_profile', 'legacy-v1')]
                if job.get('detection_profile') == 'multidim-full-v2':
                    args += ['--multidim-model', str(self.settings.multidim_model),
                             '--multidim-code', str(self.settings.multidim_code)]
                with (directory / 'run.log').open('w') as log:
                    with self.lock:
                        if self.stopped.is_set():
                            raise RuntimeError('Experiment service stopped')
                        process = subprocess.Popen(args, cwd=self.settings.core_root,
                                                   stdout=log, stderr=subprocess.STDOUT,
                                                   start_new_session=True)
                        self.process = process
                    code = self._wait(process, directory, job)
                if code != 0:
                    raise RuntimeError('CUDA runner failed; see the saved run log')
                report = self._validate_output(directory, job)
                artifacts = []
                for path in sorted(directory.iterdir()):
                    if not path.is_file():
                        continue
                    kind = ('video' if path.name == 'annotated.mp4' else
                            'report' if path.name == 'report.json' else
                            'events' if path.name == 'candidate-events.jsonl' else 'data')
                    label = (MODE_LABELS[job['mode']] if kind == 'video' else
                             '候选事件' if kind == 'events' else
                             '实验报告' if kind == 'report' else path.name)
                    artifacts.append({'id': job_id + '-' + path.stem, 'case_id': job['case_id'],
                                      'kind': kind, 'mode': job['mode'], 'label': label,
                                      'path': str(path.relative_to(self.catalog.root)),
                                      **{key: job[key] for key in ('detection_profile', 'model_id',
                                                                 'qualification', 'detector_fingerprint')
                                         if key in job}})
                with self.lock:
                    current = self.catalog.artifacts()
                    index = self.catalog.root / 'artifacts.json'
                    temp = index.with_suffix('.tmp')
                    temp.write_text(json.dumps(current + artifacts, ensure_ascii=False))
                    temp.replace(index)
                    summary_keys = ['decoded_frames', 'processed_frames', 'output_frames',
                                    'dropped_frame_count', 'fps_including_render_and_json',
                                    'pipeline_latency_ms', 'unknown_role_rate',
                                    'unknown_team_rate_all_persons', 'track_id_switches',
                                    'homography_available_frame_rate', 'foul_candidates',
                                    'detection_profile', 'model_id', 'interaction_candidates',
                                    'contact_supported_candidates', 'config_sha256',
                                    'foul_actual_forward_windows', 'foul_skip_reason',
                                    'model_latency_ms', 'alert_latency_s', 'peak_gpu_memory_mb',
                                    'gpu_peak_allocated_bytes', 'gpu_peak_reserved_bytes',
                                    'causal_replay']
                    job.update(status='completed', progress=1.0,
                               processed_frames=report['processed_frames'],
                               artifacts=[self.catalog.public_artifact(a) for a in artifacts],
                               summary={key: report[key] for key in summary_keys if key in report})
            except Exception as exc:
                self._terminate_process(process)
                with self.lock:
                    job.update(status='failed', error=str(exc))
            finally:
                with self.lock:
                    job['finished_at'] = time.time()
                    self.process = None
                    self._save(job)
                self.gpu_lease.release()
                self.queue.task_done()
