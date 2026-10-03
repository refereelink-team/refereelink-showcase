"""Isolated practice calibration with a shared CUDA execution lease."""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import uuid
from dataclasses import asdict
from contextlib import contextmanager
import gc
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)


class Prepare(Revision):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class Label(Revision):
    track_id: int = Field(ge=0)
    label: str


class Create(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = 'calibration'


class CalibrationManager:
    """Persist operator choices independently of the protected demonstration bundle.

    JSON is the recovery record. Features are recomputed from clean frames after
    restart under the same lease used by experiment subprocesses; no pickle or
    client-supplied filesystem path is accepted.
    """

    def __init__(self, settings, catalog, gpu_lease):
        self.settings, self.catalog, self.gpu_lease = settings, catalog, gpu_lease
        self.root = settings.state_root.parent / 'calibration-sessions'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=3)
        self.stopped = threading.Event()
        self.records, self.services = {}, {}
        self.worker = None
        for path in self.root.glob('*/session.json'):
            try:
                item = json.loads(path.read_text())
                if path.parent.name != item['id'] or len(item['id']) != 32:
                    continue
                if item['status'] in {'queued', 'running'}:
                    item.update(status='error', error='Server restarted; retry this operation.',
                                revision=item['revision'] + 1)
                self.records[item['id']] = item
                self._save(item)
            except (OSError, ValueError, KeyError):
                continue

    def start(self):
        self.worker = threading.Thread(target=self._run, daemon=True,
                                       name='showcase-calibration')
        self.worker.start()

    def request_stop(self):
        self.stopped.set()
        for service in list(self.services.values()):
            service.cancel_processing()

    def stop(self):
        self.request_stop()
        with self.lock:
            for item in self.records.values():
                if item['status'] == 'queued':
                    item.update(status='error', error='Service stopped; retry this operation.',
                                revision=item['revision'] + 1)
                    self._save(item)
        if self.worker:
            self.worker.join(timeout=8)

    def _save(self, item):
        directory = self.root / item['id']
        directory.mkdir(exist_ok=True)
        temporary = directory / 'session.tmp'
        temporary.write_text(json.dumps(item, ensure_ascii=False))
        temporary.replace(directory / 'session.json')

    def create(self, source_id):
        if source_id != 'calibration':
            raise HTTPException(422, 'Only the prepared calibration source is allowed')
        source = self.catalog.safe_path('input/calibration.mp4')
        # ffprobe does not initialize the CUDA model or mutate upstream state.
        import subprocess
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                '-show_entries', 'stream=width,height,r_frame_rate:format=duration',
                                '-of', 'json', str(source)], capture_output=True,
                               text=True, check=True, timeout=10)
        media = json.loads(probe.stdout)
        stream = media['streams'][0]
        numerator, denominator = stream['r_frame_rate'].split('/')
        identifier = uuid.uuid4().hex
        item = {'id': identifier, 'revision': 0, 'status': 'idle', 'operation': None,
                'error': None, 'source_id': source_id, 'created_at': time.time(),
                'source': {'duration_ms': int(float(media['format']['duration']) * 1000),
                           'fps': float(numerator) / float(denominator),
                           'width': stream['width'], 'height': stream['height']},
                'session': {'state': 'source_preview', 'tracks': [], 'ready': False,
                            'validation_report': None}, 'labels': {}, 'clip': None}
        with self.lock:
            self.records[identifier] = item
            self._save(item)
        return self.get(identifier)

    def _record(self, identifier):
        if identifier not in self.records:
            raise HTTPException(404, 'Unknown calibration session')
        return self.records[identifier]

    def get(self, identifier):
        with self.lock:
            item = self._record(identifier)
            result = json.loads(json.dumps(item))
            if result.get('clip'):
                result['clip'] = {key: value for key, value in result['clip'].items()
                                  if key not in {'source_path', 'clip_path', 'metadata_path'}}
            service = self.services.get(identifier)
            if service and item['status'] == 'running':
                result['session'] = self._public_snapshot(service.session.snapshot(), identifier)
            result.update(source_url='/media/input/calibration.mp4',
                          video_url=f'/api/calibration/sessions/{identifier}/video'
                          if item.get('clip') else '/media/input/calibration.mp4',
                          metadata_url=f'/api/calibration/sessions/{identifier}/metadata',
                          demo={'ready': self.settings.bundle.is_file(), 'protected': True})
            return result

    @staticmethod
    def _public_snapshot(snapshot, identifier):
        result = dict(snapshot)
        result.pop('bundle_path', None)
        result.update(source_url='/media/input/calibration.mp4',
                      review_video_url=f'/api/calibration/sessions/{identifier}/video',
                      metadata_url=f'/api/calibration/sessions/{identifier}/metadata')
        return result

    def submit(self, identifier, operation, payload):
        with self.lock:
            item = self._record(identifier)
            if self.stopped.is_set():
                raise HTTPException(503, 'Calibration service is stopping')
            if item['revision'] != payload.revision:
                raise HTTPException(409, 'Calibration draft changed; refresh before retrying')
            if item['status'] in {'queued', 'running'}:
                raise HTTPException(409, 'A calibration operation is already active')
            if operation == 'prepare':
                if item.get('clip'):
                    raise HTTPException(409, 'Reset before preparing another clip')
                if not payload.start_ms < payload.end_ms <= item['source']['duration_ms']:
                    raise HTTPException(422, 'Choose a valid interval inside the source')
                if payload.end_ms - payload.start_ms > 60000:
                    raise HTTPException(422, 'Clip must not exceed 60 seconds')
            if operation in {'labels', 'validate'} and not item.get('clip'):
                raise HTTPException(409, 'Prepare a clip before reviewing it')
            if operation == 'labels':
                allowed = {'home_outfield', 'away_outfield', 'home_goalkeeper',
                           'away_goalkeeper', 'referee', 'ignore'}
                if payload.label not in allowed:
                    raise HTTPException(422, 'Unsupported calibration label')
                metadata = self.metadata(identifier)
                if payload.track_id not in {track['track_id'] for track in metadata['tracks']}:
                    raise HTTPException(422, 'Unknown clip track')
            try:
                self.queue.put_nowait((identifier, operation, payload.model_dump()))
            except queue.Full as exc:
                raise HTTPException(429, 'Calibration queue is full') from exc
            item.update(status='queued', operation=operation, error=None,
                        revision=item['revision'] + 1)
            self._save(item)
            return self.get(identifier)

    def metadata(self, identifier):
        with self.lock:
            item = self._record(identifier)
            if not item.get('clip'):
                raise HTTPException(409, 'Clip metadata is not ready')
            path = self.root / identifier / 'clips' / item['clip']['clip_id'] / 'metadata.json'
        if not path.is_file():
            raise HTTPException(409, 'Clip metadata is not ready')
        return {**json.loads(path.read_text()), 'session_id': identifier}

    def video(self, identifier):
        with self.lock:
            item = self._record(identifier)
            if not item.get('clip'):
                return self.catalog.safe_path('input/calibration.mp4')
            path = self.root / identifier / 'clips' / item['clip']['clip_id'] / 'review.mp4'
        if not path.is_file():
            raise HTTPException(409, 'Review video is not ready')
        # Browser derivatives never replace the clean original used for feature recovery.
        seekable = path.with_name('review-seekable.mp4')
        return seekable if seekable.is_file() else path

    def _service(self, item):
        identifier = item['id']
        if identifier in self.services:
            return self.services[identifier]
        sys.path.insert(0, str(self.settings.core_root))
        from app.classification.team_calibration.session import TeamCalibrationSession
        from app.classification.team_calibration.clip import CalibrationClipService, ClipJob
        session = TeamCalibrationSession(device='cuda')
        service = CalibrationClipService(session, temp_root=self.root / identifier / 'clips')
        service.preview(source_path=str(self.catalog.safe_path('input/calibration.mp4')),
                        match_id='practice-' + identifier, camera_id='practice', device='cuda',
                        bundle_path=str(self.root / identifier / 'bundle.npz'),
                        config=SimpleNamespace(player_model_path=str(self.settings.player_model),
                                               pitch_model_path=str(self.settings.pitch_model),
                                               enable_undistortion=False, clean_calibration_video=True,
                                               calibration_source_fps=item['source']['fps'],
                                               inference_backend='auto'))
        if item.get('clip'):
            clip = item['clip']
            service._job = ClipJob(**clip)
            session.begin_clip_selecting(clip['start_ms'])
            session.begin_processing(clip_id=clip['clip_id'], job_id=clip['job_id'],
                                     start_ms=clip['start_ms'], end_ms=clip['end_ms'],
                                     duration_ms=clip['end_ms'] - clip['start_ms'],
                                     review_video_url='', metadata_url='')
            metadata = service.metadata()
            session.complete_processing(tracks=metadata['tracks'],
                                        observed_frames=metadata['frame_count'],
                                        last_frame_index=metadata['frame_count'] - 1)
            for track_id, label in item['labels'].items():
                if self.stopped.is_set():
                    raise RuntimeError('Service stopped during draft recovery')
                service.label_track(int(track_id), label)
        self.services[identifier] = service
        return service

    @staticmethod
    def _validate_service(service):
        # An unchanged validated draft is already complete; repeated checks are safe.
        if not service.session.ready:
            service.session.validate()

    @contextmanager
    def _cuda_operation(self):
        with self.gpu_lease:
            try:
                yield
            finally:
                # Feature banks hold NumPy CPU arrays. Retain them, but never retain
                # a MobileNet model across a GPU lease handed to another process.
                for service in list(self.services.values()):
                    with service.session._lock:
                        service.session.appearance_extractor = None
                gc.collect()
                torch = sys.modules.get('torch')
                if torch is not None and torch.cuda.is_initialized():
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()

    def _run(self):
        while not self.stopped.is_set():
            try:
                identifier, operation, payload = self.queue.get(timeout=.3)
            except queue.Empty:
                continue
            try:
                # Experiment subprocesses hold this exact lease for their lifetime.
                with self._cuda_operation():
                    if self.stopped.is_set():
                        continue
                    with self.lock:
                        item = self.records[identifier]
                        item.update(status='running')
                        self._save(item)
                    service = self._service(item)
                    if operation == 'prepare':
                        service.start_clip(payload['start_ms'])
                        service.finish_clip(payload['end_ms'])
                        deadline = time.monotonic() + self.settings.job_timeout_s
                        while service._worker.is_alive():
                            if self.stopped.is_set() or time.monotonic() >= deadline:
                                service.cancel_processing()
                            # Keep the lease until the inference thread has actually exited.
                            service._worker.join(timeout=.2)
                        if service.job.status != 'completed':
                            raise RuntimeError(service.job.error or 'Clip processing failed')
                        item['clip'] = asdict(service.job)
                        weak = [track['track_id'] for track in service.metadata()['tracks']
                                if track['quality_observation_count'] < service.session.min_samples_per_track]
                        item['auto_ignored_track_ids'] = weak
                        for track_id in weak:
                            service.label_track(track_id, 'ignore')
                            item['labels'][str(track_id)] = 'ignore'
                    elif operation == 'labels':
                        from app.classification.team_calibration.session import CalibrationState
                        with service.session._lock:
                            if service.session.state == CalibrationState.READY:
                                service.session._state = CalibrationState.REVIEW
                                service.session._report = None
                                service.session._bundle = None
                        service.label_track(payload['track_id'], payload['label'])
                        item['auto_ignored_track_ids'] = [track_id for track_id in
                            item.get('auto_ignored_track_ids', []) if track_id != payload['track_id']]
                        item['labels'][str(payload['track_id'])] = payload['label']
                    elif operation == 'validate':
                        # Validation only writes the session-owned bundle path.
                        self._validate_service(service)
                    elif operation == 'reset':
                        service.reset()
                        self.services.pop(identifier, None)
                        (self.root / identifier / 'bundle.npz').unlink(missing_ok=True)
                        item.update(clip=None, labels={}, auto_ignored_track_ids=[])
                        service = self._service(item)
                    if self.stopped.is_set():
                        raise RuntimeError('Service stopped; retry this operation.')
                    with self.lock:
                        item.update(status='idle', error=None,
                                    session=self._public_snapshot(service.status(), identifier),
                                    revision=item['revision'] + 1)
                        self._save(item)
            except Exception as exc:
                with self.lock:
                    item = self.records[identifier]
                    item.update(status='error', error=str(exc), revision=item['revision'] + 1)
                    self._save(item)
            finally:
                self.queue.task_done()


def create_calibration_router(manager):
    router = APIRouter(prefix='/api/calibration/sessions', tags=['calibration'])

    @router.post('', status_code=201)
    def create(payload: Create):
        return manager.create(payload.source_id)

    @router.get('/{identifier}')
    def get(identifier: str):
        return manager.get(identifier)

    @router.post('/{identifier}/prepare', status_code=202)
    def prepare(identifier: str, payload: Prepare):
        return manager.submit(identifier, 'prepare', payload)

    @router.post('/{identifier}/labels', status_code=202)
    def label(identifier: str, payload: Label):
        return manager.submit(identifier, 'labels', payload)

    @router.post('/{identifier}/validate', status_code=202)
    def validate(identifier: str, payload: Revision):
        return manager.submit(identifier, 'validate', payload)

    @router.post('/{identifier}/reset', status_code=202)
    def reset(identifier: str, payload: Revision):
        return manager.submit(identifier, 'reset', payload)

    @router.get('/{identifier}/metadata')
    def metadata(identifier: str):
        return manager.metadata(identifier)

    @router.get('/{identifier}/video')
    def video(identifier: str):
        return FileResponse(manager.video(identifier), media_type='video/mp4',
                            headers={'Cache-Control': 'no-cache'})

    return router
