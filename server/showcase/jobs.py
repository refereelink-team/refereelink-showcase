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
from .catalog import MediaCatalog
from .settings import Settings


MODES = {'tracking': 'projection_only', 'foul': 'foul_only', 'combined': 'combined'}
MODE_LABELS = {'projection_only': '跟踪与场地投影', 'foul_only': '跟踪与犯规候选',
               'combined': '跟踪、投影与犯规候选'}


class JobManager:
    def __init__(self, settings: Settings, catalog: MediaCatalog):
        self.settings, self.catalog = settings, catalog
        self.lock = threading.RLock()
        self.gpu_lease = threading.Lock()
        self.queue = queue.Queue(maxsize=3)
        self.stopped = threading.Event()
        self.jobs = {}
        self.process = None
        self.worker = None
        settings.state_root.mkdir(parents=True, exist_ok=True)
        for path in settings.state_root.glob('job-*.json'):
            try:
                job = json.loads(path.read_text())
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

    def _save(self, job):
        path = self.settings.state_root / ('job-' + job['id'] + '.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(job, ensure_ascii=False))
        temp.replace(path)

    def submit(self, kind: str, case_id: str):
        if kind not in MODES:
            raise HTTPException(422, 'Unsupported experiment kind')
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
                           and job['status'] in {'queued', 'running'}), None)
            if active:
                return dict(active)
            job = {'id': uuid.uuid4().hex, 'kind': kind, 'case_id': case_id,
                   'mode': MODES[kind], 'status': 'queued', 'progress': 0.0,
                   'created_at': time.time(), 'error': None, 'artifacts': [], 'summary': None,
                   'pitch_profile_id': profile, 'paint_enabled': profile != 'legacy',
                   'source_sha256': source_sha256}
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
        if job['mode'] != 'projection_only' and report.get('foul_actual_forward_windows', 0) < 1:
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
                                      'path': str(path.relative_to(self.catalog.root))})
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
                                    'homography_available_frame_rate', 'foul_candidates']
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
