"""Native presentation records from verified CUDA tracking runs.

The browser renders original source media and these model records separately.
Coordinates come from VisionCore, never from an annotated video or synthetic tracks.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from pathlib import Path
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from .catalog import MediaCatalog
from .jobs import JobManager


# Legacy runs omitted geometry from their report. Preserve their recorded coordinate model.
PITCH = {
    'length_m': 120.0, 'width_m': 70.0,
    'penalty_area_length_m': 20.15, 'penalty_area_width_m': 41.0,
    'goal_area_length_m': 5.5, 'goal_area_width_m': 18.32,
    'center_circle_radius_m': 9.15, 'penalty_spot_distance_m': 11.0,
    'goal_width_m': 7.32,
}
def _pitch_geometry(config: dict) -> dict:
    geometry = config.get('pitch_geometry_m')
    if geometry is None:
        return dict(PITCH)
    if not isinstance(geometry, dict) or set(geometry) != set(PITCH):
        raise ValueError('Tracking pitch geometry is incomplete')
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in geometry.values()):
        raise ValueError('Tracking pitch geometry must contain positive finite metres')
    length, width = geometry['length_m'], geometry['width_m']
    if not (geometry['goal_area_length_m'] < geometry['penalty_area_length_m'] < length / 2
            and geometry['goal_width_m'] < geometry['goal_area_width_m']
            < geometry['penalty_area_width_m'] < width
            and geometry['center_circle_radius_m'] < min(length, width) / 2
            and geometry['penalty_spot_distance_m'] < geometry['penalty_area_length_m']):
        raise ValueError('Tracking pitch geometry has inconsistent areas')
    return dict(geometry)


MAX_LINE_BYTES = 1024 * 1024
MAX_PAGE_FRAMES = 240


class TrackingPlayer(BaseModel):
    track_id: int
    entity_id: int | None = None
    track_status: str = 'detected'
    missing_frames: int = 0
    role: Literal['outfield', 'goalkeeper', 'referee', 'staff', 'unknown']
    team: Literal['home', 'away', 'none', 'unknown']
    confidence: FiniteFloat
    role_confidence: FiniteFloat = 0.0
    team_confidence: FiniteFloat = 0.0
    bbox: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    field_x: FiniteFloat | None = None
    field_y: FiniteFloat | None = None
    velocity_x: FiniteFloat | None = None
    velocity_y: FiniteFloat | None = None


class TrackingBall(BaseModel):
    status: Literal['fresh', 'predicted', 'stale', 'unavailable']
    image_x: FiniteFloat | None = None
    image_y: FiniteFloat | None = None
    field_x: FiniteFloat | None = None
    field_y: FiniteFloat | None = None
    velocity_x: FiniteFloat | None = None
    velocity_y: FiniteFloat | None = None
    confidence: FiniteFloat = 0.0
    age_frames: int = 0


class TrackingFrame(BaseModel):
    frame_id: int = Field(ge=1)
    homography_status: Literal['fresh', 'reused', 'stale', 'unavailable']
    projection_quality: dict[str, object] | None = None
    geometry_epoch: int | None = Field(default=None, ge=0)
    players: list[TrackingPlayer] = Field(max_length=1000)
    ball: TrackingBall | None = None


def _fingerprint(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Tracking metadata is too large')
    return json.loads(path.read_text())


def _frame(line: bytes, expected: int) -> dict:
    if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
        raise ValueError('Tracking frame record is incomplete or too large')
    value = TrackingFrame.model_validate_json(line)
    if value.frame_id != expected:
        raise ValueError('Tracking source frame sequence is incomplete')
    result = value.model_dump(mode='json')
    for entity in [*result['players'], *([result['ball']] if result['ball'] else [])]:
        for field in ('field_x', 'field_y', 'velocity_x', 'velocity_y'):
            if entity[field] is not None:
                entity[field] /= 100.0
    return result


@dataclass(frozen=True)
class TrackingIndex:
    descriptor: dict
    path: Path
    paths: tuple[Path, ...]
    offsets: tuple[int, ...]
    timestamps_s: tuple[float, ...]
    fingerprints: tuple


class TrackingRepository:
    """Bounded indexes over immutable completed-run FrameState JSONL artifacts."""

    def __init__(self, catalog: MediaCatalog, jobs: JobManager):
        self.catalog, self.jobs = catalog, jobs
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, TrackingIndex] = OrderedDict()
        self._source_hashes: dict[str, tuple[tuple, str]] = {}

    def _siblings(self, artifact: dict) -> tuple[Path, Path, Path, Path]:
        if artifact.get('mode') != 'projection_only':
            raise ValueError('Result is not a tracking-only CUDA run')
        relative = Path(artifact['path'])
        if relative.name != 'frame-states.jsonl':
            raise ValueError('Result is not a native tracking artifact')
        parts = relative.parts
        prepared = len(parts) == 4 and parts[:3] == ('prepared', artifact['case_id'], 'projection_only')
        job = len(parts) == 3 and parts[0] == 'jobs' and re.fullmatch('[0-9a-f]{32}', parts[1])
        if not prepared and not job:
            raise ValueError('Result is outside an allowlisted CUDA run directory')
        artifacts = self.catalog.artifacts()
        relatives = {item['path'] for item in artifacts
                     if item['case_id'] == artifact['case_id']
                     and item.get('mode') == 'projection_only'}
        report = relative.with_name('report.json').as_posix()
        metadata = relative.with_name('source-metadata.json').as_posix()
        if report not in relatives or metadata not in relatives:
            raise ValueError('Tracking result metadata is not registered')
        return (self.catalog.safe_path(artifact['path']), self.catalog.safe_path(report),
                self.catalog.safe_path(metadata),
                self.catalog.safe_path('input/' + artifact['case_id'] + '.mp4'))

    def _index(self, artifact: dict) -> TrackingIndex:
        paths = self._siblings(artifact)
        fingerprints = tuple(_fingerprint(path) for path in paths)
        with self._lock:
            cached = self._cache.get(artifact['id'])
            if cached is not None and cached.fingerprints == fingerprints:
                self._cache.move_to_end(artifact['id'])
                return cached
            state_path, report_path, meta_path, source_path = paths
            report, metadata = _json(report_path), _json(meta_path)
            if not isinstance(report, dict) or not isinstance(metadata, dict):
                raise ValueError('Tracking metadata must be an object')
            expected = self.catalog.clip(artifact['case_id'])['decoded_frames']
            config = report.get('config', {})
            environment, source = report.get('environment', {}), report.get('source', {})
            latency = report.get('pipeline_latency_ms', {})
            if not all(isinstance(value, dict) for value in (config, environment, source, latency)):
                raise ValueError('Tracking report sections must be objects')
            completed_at = report.get('completed_at')
            if not isinstance(completed_at, str) or datetime.fromisoformat(completed_at).tzinfo is None:
                raise ValueError('Tracking completion time must include its timezone')
            if (report.get('status') != 'complete' or report.get('mode') != 'projection_only'
                    or environment.get('device') != 'cuda'
                    or config.get('pitch_enabled') is not True
                    or config.get('foul_enabled') is not False
                    or report.get('decoded_frames') != expected
                    or report.get('processed_frames') != expected
                    or report.get('output_frames') != expected
                    or report.get('dropped_frame_count') != 0
                    or report.get('foul_errors')):
                raise ValueError('Tracking run does not prove complete CUDA processing')
            cached_hash = self._source_hashes.get(artifact['case_id'])
            if cached_hash is None or cached_hash[0] != fingerprints[3]:
                cached_hash = (fingerprints[3], _sha256(source_path))
                self._source_hashes[artifact['case_id']] = cached_hash
            if (metadata.get('sha256') != cached_hash[1]
                    or source.get('sha256') != cached_hash[1]):
                raise ValueError('Tracking output belongs to a different source video')
            timestamps = metadata['decoded_timestamps_ms']
            if (not isinstance(timestamps, list)
                    or metadata.get('reference_decoded_frames') != expected or len(timestamps) != expected):
                raise ValueError('Tracking source timestamps do not cover every decoded frame')
            timestamps_s = tuple(float(value) / 1000 for value in timestamps)
            if (any(not math.isfinite(value) or value < 0 for value in timestamps_s)
                    or any(right <= left for left, right in zip(timestamps_s, timestamps_s[1:]))):
                raise ValueError('Tracking source timestamps are not finite and increasing')
            width, height, fps = int(metadata['width']), int(metadata['height']), float(metadata['output_fps'])
            probe = metadata.get('ffprobe', {})
            if not isinstance(probe, dict):
                raise ValueError('Tracking source probe must be an object')
            duration = float(probe.get('duration')
                             or self.catalog.clip(artifact['case_id'])['duration_seconds'])
            if (width < 1 or height < 1 or not math.isfinite(fps) or fps <= 0
                    or not math.isfinite(duration) or duration <= timestamps_s[-1]):
                raise ValueError('Tracking source media metadata is invalid')
            offsets, state_hash = [], hashlib.sha256()
            with state_path.open('rb') as stream:
                while True:
                    offset = stream.tell()
                    line = stream.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(offsets) >= expected:
                        raise ValueError('Tracking result contains extra frames')
                    _frame(line, len(offsets) + 1)
                    state_hash.update(line)
                    offsets.append(offset)
            if len(offsets) != expected or tuple(_fingerprint(path) for path in paths) != fingerprints:
                raise ValueError('Tracking records are incomplete or changed during validation')
            revision = hashlib.sha256((state_hash.hexdigest() + _sha256(report_path)
                                       + _sha256(meta_path) + cached_hash[1]).encode()).hexdigest()
            job_id = str(Path(artifact['path']).parts[1]) if Path(artifact['path']).parts[0] == 'jobs' else None
            descriptor = {
                'id': artifact['id'], 'case_id': artifact['case_id'], 'job_id': job_id,
                'revision': revision,
                'source': {'url': '/media/input/' + artifact['case_id'] + '.mp4',
                           'width': width, 'height': height, 'fps': fps,
                           'duration_seconds': duration, 'decoded_frames': expected},
                'pitch': _pitch_geometry(config), 'frame_count': expected,
                'frames_url': '/api/tracking/results/' + artifact['id'] + '/frames',
                'metrics': {
                    'actual_fps': report.get('fps_including_render_and_json'),
                    'latency_ms': latency.get('mean'),
                    'latency_ms_p95': latency.get('p95'),
                    'homography_available_frame_rate': report.get('homography_available_frame_rate'),
                    'projected_player_rate': report.get('projected_player_rate'),
                    'unknown_role_rate': report.get('unknown_role_rate'),
                    'unknown_team_rate': report.get('unknown_team_rate_all_persons'),
                },
                'provenance': {'device': 'cuda', 'recorded_at': completed_at,
                               'live': False, 'source_sha256': cached_hash[1],
                               'pitch_profile_id': config.get('pitch_profile_id', 'legacy'),
                               'paint_enabled': config.get('paint_enabled', False)},
            }
            json.dumps(descriptor, allow_nan=False)
            if tuple(_fingerprint(path) for path in paths) != fingerprints:
                raise ValueError('Tracking source metadata changed during validation')
            index = TrackingIndex(descriptor, state_path, paths, tuple(offsets), timestamps_s, fingerprints)
            self._cache[artifact['id']] = index
            self._cache.move_to_end(artifact['id'])
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
            return index

    def result(self, result_id: str) -> TrackingIndex:
        artifact = self.catalog.artifact(result_id)
        try:
            return self._index(artifact)
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise HTTPException(409, 'Native tracking result is incomplete or incompatible') from exc

    def case(self, case_id: str) -> dict:
        clip = self.catalog.clip(case_id)
        self.catalog.safe_path('input/' + case_id + '.mp4')
        results = []
        invalid = False
        for artifact in self.catalog.artifacts():
            if (artifact['case_id'] != case_id or artifact.get('mode') != 'projection_only'
                    or Path(artifact['path']).name != 'frame-states.jsonl'):
                continue
            try:
                results.append(self._index(artifact).descriptor)
            except (OSError, ValueError, KeyError, TypeError, ValidationError):
                invalid = True
        latest = max(results, key=lambda result: datetime.fromisoformat(result['provenance']['recorded_at']),
                     default=None)
        active = next((job for job in self.jobs.list() if job['case_id'] == case_id
                       and job['kind'] == 'tracking' and job['status'] in {'queued', 'running'}), None)
        return {'case_id': case_id, 'source_url': '/media/input/' + case_id + '.mp4',
                'poster_url': '/media/posters/' + case_id + '.jpg',
                'duration_seconds': clip['duration_seconds'], 'decoded_frames': clip['decoded_frames'],
                'latest_result': latest, 'active_job': active,
                'result_warning': 'Some stored runs failed source or completeness validation' if invalid else None}

    def frames(self, result_id: str, offset: int, limit: int, revision: str | None = None) -> dict:
        index = self.result(result_id)
        if revision is not None and revision != index.descriptor['revision']:
            raise HTTPException(409, 'Tracking result changed; reload the result descriptor')
        total = len(index.offsets)
        if offset > total:
            raise HTTPException(416, 'Frame offset is outside this tracking result')
        stop, frames = min(offset + limit, total), []
        try:
            with index.path.open('rb') as stream:
                for position in range(offset, stop):
                    stream.seek(index.offsets[position])
                    frame = _frame(stream.readline(MAX_LINE_BYTES + 1), position + 1)
                    frame['source_pts_s'] = index.timestamps_s[position]
                    frames.append(frame)
            if tuple(_fingerprint(path) for path in index.paths) != index.fingerprints:
                raise ValueError('Tracking frames changed during the page read')
        except (OSError, ValueError, ValidationError) as exc:
            raise HTTPException(409, 'Tracking records changed; reload the result descriptor') from exc
        return {'result_id': result_id, 'revision': index.descriptor['revision'],
                'offset': offset, 'total': total,
                'next_offset': stop if stop < total else None, 'frames': frames}


def create_tracking_router(repository: TrackingRepository) -> APIRouter:
    router = APIRouter(prefix='/api/tracking', tags=['native-tracking'])

    @router.get('/cases/{case_id}')
    def tracking_case(case_id: str):
        return repository.case(case_id)

    @router.post('/cases/{case_id}/jobs', status_code=202)
    def submit_tracking(case_id: str):
        return repository.jobs.submit('tracking', case_id)

    @router.get('/results/{result_id}')
    def tracking_result(result_id: str):
        return repository.result(result_id).descriptor

    @router.get('/results/{result_id}/frames')
    def tracking_frames(result_id: str, offset: int = Query(0, ge=0),
                        limit: int = Query(MAX_PAGE_FRAMES, ge=1, le=MAX_PAGE_FRAMES),
                        revision: str | None = Query(None, pattern='^[0-9a-f]{64}$')):
        return repository.frames(result_id, offset, limit, revision)

    return router
