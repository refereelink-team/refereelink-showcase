"""Serve native foul overlays from immutable, source-bound CUDA run records.

This adapter never runs a model, reads annotation pixels, or fills absent identity
and event facts. Original media and measured records remain separate resources.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading

from fastapi import APIRouter, HTTPException, Query

from .catalog import MediaCatalog
from .foul_validation import HASH_FIELDS, configuration_sha256
from .jobs import FOUL_PROFILES, JobManager

MAX_LINE_BYTES = 1024 * 1024
MAX_PAGE_FRAMES = 500
MAX_EVENTS = 10000
SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _reject_constant(value: str):
    raise ValueError('Nonfinite JSON number: ' + value)


def _loads(data: str | bytes):
    return json.loads(data, parse_constant=_reject_constant)


def _json(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Foul metadata is too large')
    value = _loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError('Foul metadata must be an object')
    return value


def _number(value, *, minimum=0.0, maximum=None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('Record number is outside its valid range')
    if maximum is not None and value > maximum:
        raise ValueError('Record number exceeds its valid range')
    return float(value)


def _integer(value, *, minimum=0, maximum=None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError('Record identifier is invalid')
    return value


def _box(value, width: int, height: int):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('Observation box must have four coordinates')
    box = [_number(item) for item in value]
    if not (box[0] < box[2] <= width and box[1] < box[3] <= height):
        raise ValueError('Observation box is outside the original source geometry')
    return box


def _frame(line: bytes, expected: int, width: int, height: int) -> dict:
    if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
        raise ValueError('Foul frame record is incomplete or too large')
    value = _loads(line)
    if not isinstance(value, dict) or value.get('frame_id') != expected:
        raise ValueError('Foul source frame sequence is incomplete')
    _integer(value['frame_id'], minimum=1)
    observations = value.get('players')
    if not isinstance(observations, list) or len(observations) > 1000:
        raise ValueError('Foul frame player list is invalid')
    players, track_ids = [], set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError('Player observation must be an object')
        track_id = _integer(observation.get('track_id'))
        if track_id in track_ids:
            raise ValueError('Duplicate player track identifier in one frame')
        track_ids.add(track_id)
        role, team = observation.get('role'), observation.get('team')
        if role not in {'outfield', 'goalkeeper', 'referee', 'staff', 'unknown'}:
            raise ValueError('Player role is invalid')
        if team not in {'home', 'away', 'none', 'unknown'}:
            raise ValueError('Player team is invalid')
        status = observation.get('track_status', 'detected')
        if not isinstance(status, str) or not status or len(status) > 64:
            raise ValueError('Player track status is invalid')
        player = {'track_id': track_id, 'role': role, 'team': team,
                  'track_status': status,
                  'confidence': _number(observation.get('confidence'), maximum=1),
                  'bbox': (_box(observation['bbox'], width, height)
                           if observation.get('bbox') is not None else None)}
        # Optional observations stay optional; no jersey number or identity is inferred.
        if 'entity_id' in observation:
            player['entity_id'] = (None if observation['entity_id'] is None
                                   else _integer(observation['entity_id']))
        if 'missing_frames' in observation:
            player['missing_frames'] = _integer(observation['missing_frames'])
        players.append(player)
    return {'frame_id': expected, 'players': players}


def _event(record: dict, timestamps: tuple[float, ...], duration: float,
           width: int, height: int, profile: str, model: str) -> str:
    if not isinstance(record, dict) or not isinstance(record.get('event'), dict):
        raise ValueError('Candidate event record is invalid')
    event = record['event']
    event_id = event.get('id')
    if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
        raise ValueError('Candidate event identifier is invalid')
    if event.get('event_type') != 'foul_candidate' or type(event.get('reviewed')) is not bool:
        raise ValueError('Candidate event type or review state is invalid')
    _number(event.get('confidence'), maximum=1)
    source_id = _integer(record.get('source_frame_id'), minimum=1, maximum=len(timestamps))
    received_time = timestamps[source_id - 1]
    media_time = _number(record.get('media_pts_seconds'), maximum=duration)
    evidence = event.get('evidence', {})
    if not isinstance(evidence, dict):
        raise ValueError('Candidate evidence must be an object')
    if profile != 'legacy-v1' and (
            evidence.get('detection_profile') != profile or evidence.get('model_id') != model):
        raise ValueError('Candidate evidence belongs to another detector')
    if (evidence.get('detection_profile', profile) != profile
            or evidence.get('model_id', model) != model):
        raise ValueError('Historical candidate detector metadata is incompatible')
    event_time = evidence.get('event_time_s', event.get('event_time_s', media_time))
    event_time = _number(event_time, maximum=duration)
    emitted = evidence.get('emitted_time_s', event.get('emitted_time_s'))
    if abs(event_time - media_time) > 0.002 or event_time > received_time + 0.002:
        raise ValueError('Candidate event time is not causal or source aligned')
    if emitted is not None:
        emitted = _number(emitted, minimum=event_time, maximum=duration)
        if abs(emitted - received_time) > 0.002:
            raise ValueError('Candidate emission time does not map to its source frame')
    start, end = evidence.get('evidence_start_s'), evidence.get('evidence_end_s')
    if start is not None:
        start = _number(start, maximum=duration)
        if start > received_time + 0.002:
            raise ValueError('Candidate evidence start is not causal')
    if end is not None:
        end = _number(end, maximum=duration)
        if end > received_time + 0.002 or (start is not None and end < start):
            raise ValueError('Candidate evidence interval is not causal')
    for container in (event, evidence):
        for field in ('event_time_s', 'emitted_time_s'):
            if field in container and container[field] is not None:
                _number(container[field], maximum=duration)
    for field in ('event_time_s', 'emitted_time_s'):
        event_value, evidence_value = event.get(field), evidence.get(field)
        if (event_value is not None and evidence_value is not None
                and abs(event_value - evidence_value) > 0.002):
            raise ValueError('Candidate event and evidence time aliases disagree')
    details = event.get('foul_details')
    if details is not None and not isinstance(details, dict):
        raise ValueError('Candidate foul details must be an object')
    for container in (event, evidence, details or {}):
        for field in ('severity', 'decision'):
            value = container.get(field)
            if isinstance(value, str) and value.strip().lower().replace('_', ' ') == 'no offence':
                raise ValueError('A no-offence model result is not a foul candidate')
    for container in (evidence, details or {}):
        for field in ('offence_score', 'action_score', 'severity_score'):
            if field in container and container[field] is not None:
                _number(container[field], maximum=1)
    if evidence.get('region_xyxy') is not None:
        if evidence.get('source_width') != width or evidence.get('source_height') != height:
            raise ValueError('Candidate region uses different source dimensions')
        _box(evidence['region_xyxy'], width, height)
    for field in ('involved_targets', 'candidate_targets'):
        targets = evidence.get(field)
        if targets is None:
            continue
        if not isinstance(targets, list) or len(targets) > 1000:
            raise ValueError('Candidate target list is invalid')
        ids = set()
        for target in targets:
            if not isinstance(target, dict):
                raise ValueError('Candidate target is invalid')
            track_id = target.get('track_id')
            if track_id is not None:
                track_id = _integer(track_id)
                if track_id in ids:
                    raise ValueError('Duplicate candidate target identifier')
                ids.add(track_id)
            if target.get('entity_id') is not None:
                _integer(target['entity_id'])
            if target.get('bbox') is not None:
                _box(target['bbox'], width, height)
    # Keep the original CandidateRecord exactly, including unknown attribution.
    json.dumps(record, allow_nan=False)
    return event_id


@dataclass(frozen=True)
class FoulIndex:
    descriptor: dict
    path: Path
    paths: tuple[Path, ...]
    offsets: tuple[int, ...]
    timestamps_s: tuple[float, ...]
    fingerprints: tuple
    binding: str


class FoulPresentationRepository:
    """Bounded indexes over verified completed foul-only records."""

    def __init__(self, catalog: MediaCatalog, jobs: JobManager):
        self.catalog, self.jobs = catalog, jobs
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, FoulIndex] = OrderedDict()
        self._source_hashes: dict[str, tuple[tuple, str]] = {}

    def _siblings(self, artifact: dict) -> tuple[tuple[Path, ...], dict | None, str]:
        if artifact.get('mode') != 'foul_only' or artifact.get('kind') != 'data':
            raise ValueError('Result is not a foul-only data artifact')
        relative = Path(artifact['path'])
        if relative.name != 'frame-states.jsonl':
            raise ValueError('Result is not a native foul artifact')
        parts = relative.parts
        prepared = len(parts) == 4 and parts[:3] == ('prepared', artifact['case_id'], 'foul_only')
        task = len(parts) == 3 and parts[0] == 'jobs' and re.fullmatch('[0-9a-f]{32}', parts[1])
        if not prepared and not task:
            raise ValueError('Result is outside an allowlisted CUDA run directory')
        inventory = self.catalog.artifacts()
        ids = [item['id'] for item in inventory]
        if len(ids) != len(set(ids)):
            raise ValueError('Artifact inventory contains duplicate identifiers')
        related = []
        paths = []
        for name in ('frame-states.jsonl', 'report.json', 'source-metadata.json',
                     'candidate-events.jsonl'):
            path = relative.with_name(name).as_posix()
            matches = [item for item in inventory if item['path'] == path
                       and item['case_id'] == artifact['case_id']
                       and item.get('mode') == 'foul_only']
            if len(matches) != 1:
                raise ValueError('Foul run sibling record is not registered uniquely')
            related.append(matches[0])
            paths.append(self.catalog.safe_path(path))
        job = self.jobs.get(parts[1]) if task else None
        if job is not None and (job.get('status') != 'completed' or job.get('kind') != 'foul'
                                or job.get('case_id') != artifact['case_id']
                                or job.get('mode') != 'foul_only'):
            raise ValueError('Native foul result task has not completed')
        paths.append(self.catalog.safe_path('input/' + artifact['case_id'] + '.mp4'))
        paths.append(self.catalog.safe_path('artifacts.json'))
        if job is not None:
            state = self.jobs.settings.state_root / ('job-' + job['id'] + '.json')
            if state.is_file():
                paths.append(state)
        binding = json.dumps({'artifacts': related, 'job': job}, sort_keys=True, allow_nan=False)
        return tuple(paths), job, binding

    def _index(self, artifact: dict) -> FoulIndex:
        paths, job, binding = self._siblings(artifact)
        fingerprints = tuple(_fingerprint(path) for path in paths)
        profile = artifact.get('detection_profile', 'legacy-v1')
        if profile not in FOUL_PROFILES:
            raise ValueError('Foul result detector profile is unknown')
        model = artifact.get('model_id', FOUL_PROFILES[profile][1])
        if model != FOUL_PROFILES[profile][1]:
            raise ValueError('Foul result model is incompatible')
        detector = artifact.get('detector_fingerprint')
        if profile != 'legacy-v1':
            # Recheck even cached indexes: qualification and model files may change.
            actual = self.jobs.foul_validation.snapshot(profile)
            if detector != actual:
                raise ValueError('Foul result detector is no longer the validated snapshot')
        with self._lock:
            cached = self._cache.get(artifact['id'])
            if cached is not None and cached.fingerprints == fingerprints and cached.binding == binding:
                self._cache.move_to_end(artifact['id'])
                return cached
            state_path, report_path, metadata_path, events_path, source_path = paths[:5]
            report, metadata = _json(report_path), _json(metadata_path)
            expected = self.catalog.clip(artifact['case_id'])['decoded_frames']
            config, environment, source = (report.get(key, {}) for key in
                                           ('config', 'environment', 'source'))
            if not all(isinstance(value, dict) for value in (config, environment, source)):
                raise ValueError('Foul report sections must be objects')
            complete = report.get('completed_at')
            if not isinstance(complete, str) or datetime.fromisoformat(complete).tzinfo is None:
                raise ValueError('Foul completion time must have a timezone')
            if (report.get('status') != 'complete' or report.get('mode') != 'foul_only'
                    or environment.get('device') != 'cuda'
                    or config.get('pitch_enabled') is not False
                    or config.get('foul_enabled') is not True
                    or any(type(report.get(key)) is not int or report[key] != expected for key in
                           ('decoded_frames', 'processed_frames', 'output_frames'))
                    or report.get('dropped_frame_count') != 0 or report.get('foul_errors')):
                raise ValueError('Foul run does not prove complete CUDA processing')
            reported_profile = config.get('detection_profile', config.get('foul_profile_id', 'legacy-v1'))
            reported_model = report.get('model_id', config.get('model_id', FOUL_PROFILES['legacy-v1'][1]))
            if reported_profile != profile or reported_model != model:
                raise ValueError('Foul report belongs to another detector')
            inventory = self.catalog.artifacts()
            for sibling in inventory:
                if Path(sibling['path']).parent != Path(artifact['path']).parent:
                    continue
                if (sibling.get('detection_profile', 'legacy-v1') != profile
                        or sibling.get('model_id', FOUL_PROFILES['legacy-v1'][1]) != model
                        or sibling.get('detector_fingerprint') != detector):
                    raise ValueError('Foul run artifact detector bindings disagree')
            if job is not None and (job.get('detection_profile', 'legacy-v1') != profile
                                    or job.get('model_id', FOUL_PROFILES['legacy-v1'][1]) != model
                                    or job.get('detector_fingerprint') != detector):
                raise ValueError('Foul task detector binding disagrees with its artifacts')
            if detector is not None:
                reported = {'config_sha256': report.get('config_sha256'),
                            'model_sha256': report.get('models', {}).get('foul', {}).get('sha256'),
                            'core_manifest_sha256': report.get('source_code', {}).get('verification', {}).get('manifest_sha256'),
                            'external_source_sha256': report.get('external_source_sha256')}
                verification = config.get('verification')
                if (any(not isinstance(detector.get(key), str) or not SHA256_PATTERN.fullmatch(detector[key])
                        or reported[key] != detector[key] for key in HASH_FIELDS)
                        or report.get('source_code', {}).get('verification', {}).get('verified') is not True
                        or not isinstance(verification, dict)
                        or configuration_sha256(verification) != detector['config_sha256']):
                    raise ValueError('Foul report does not match its detector snapshot')
                checkpoint = report.get('checkpoint_validation', {})
                if (not isinstance(checkpoint, dict) or checkpoint.get('strict') is not True
                        or type(checkpoint.get('state_tensors')) is not int
                        or checkpoint['state_tensors'] < 1 or checkpoint.get('missing_keys') != []
                        or checkpoint.get('unexpected_keys') != []):
                    raise ValueError('Foul run does not prove strict checkpoint loading')
            cached_hash = self._source_hashes.get(artifact['case_id'])
            if cached_hash is None or cached_hash[0] != fingerprints[4]:
                cached_hash = (fingerprints[4], _sha256(source_path))
                self._source_hashes[artifact['case_id']] = cached_hash
            digest = cached_hash[1]
            if metadata.get('sha256') != digest or source.get('sha256') != digest:
                raise ValueError('Foul result belongs to another source video')
            if job is not None and job.get('source_sha256', digest) != digest:
                raise ValueError('Foul task belongs to another source video')
            pts = metadata.get('decoded_timestamps_ms')
            if (not isinstance(pts, list) or len(pts) != expected
                    or metadata.get('reference_decoded_frames') != expected):
                raise ValueError('Foul source timestamps do not cover every decoded frame')
            timestamps = tuple(_number(value) / 1000 for value in pts)
            if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
                raise ValueError('Foul source timestamps are not strictly increasing')
            width, height = (_integer(metadata.get(key), minimum=1) for key in ('width', 'height'))
            fps = _number(metadata.get('output_fps'), minimum=0.000001)
            probe = metadata.get('ffprobe')
            if not isinstance(probe, dict):
                raise ValueError('Foul source probe must be an object')
            duration = _number(float(probe.get('duration')), minimum=0.000001)
            if duration <= timestamps[-1]:
                raise ValueError('Foul timestamps exceed source duration')
            for key, value in (('width', width), ('height', height), ('output_fps', fps)):
                if key in source and source[key] != value:
                    raise ValueError('Foul report and metadata source geometry disagree')
            offsets, state_hash = [], hashlib.sha256()
            with state_path.open('rb') as stream:
                while line := stream.readline(MAX_LINE_BYTES + 1):
                    offset = stream.tell() - len(line)
                    if len(offsets) >= expected:
                        raise ValueError('Foul result contains extra frames')
                    _frame(line, len(offsets) + 1, width, height)
                    state_hash.update(line)
                    offsets.append(offset)
            if len(offsets) != expected:
                raise ValueError('Foul result frame sequence is incomplete')
            if events_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Foul candidate record file is too large')
            events, event_ids = [], set()
            with events_path.open('rb') as stream:
                while line := stream.readline(MAX_LINE_BYTES + 1):
                    if len(events) >= MAX_EVENTS or len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                        raise ValueError('Foul event records are incomplete or too large')
                    record = _loads(line)
                    event_id = _event(record, timestamps, duration, width, height, profile, model)
                    if event_id in event_ids:
                        raise ValueError('Duplicate candidate event identifier')
                    event_ids.add(event_id)
                    events.append(record)
            if type(report.get('foul_candidates')) is not int or len(events) != report['foul_candidates']:
                raise ValueError('Foul event count does not match its report')
            revision = hashlib.sha256((state_hash.hexdigest() + ''.join(_sha256(path) for path in
                                      paths[1:4]) + digest + binding).encode()).hexdigest()
            descriptor = {'schema_version': 1, 'id': artifact['id'], 'case_id': artifact['case_id'],
                          'revision': revision, 'detection_profile': profile, 'model_id': model,
                          'source': {'url': '/media/input/' + artifact['case_id'] + '.mp4',
                                     'width': width, 'height': height, 'duration_s': duration,
                                     'fps': fps, 'sha256': digest},
                          'frame_count': expected,
                          'frames_url': '/api/foul/results/' + artifact['id'] + '/frames',
                          'events': events}
            if detector is not None:
                descriptor['detector_fingerprint'] = detector
            json.dumps(descriptor, allow_nan=False)
            if tuple(_fingerprint(path) for path in paths) != fingerprints:
                raise ValueError('Foul records changed during index validation')
            index = FoulIndex(descriptor, state_path, paths, tuple(offsets), timestamps,
                              fingerprints, binding)
            self._cache[artifact['id']] = index
            self._cache.move_to_end(artifact['id'])
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
            return index

    def result(self, result_id: str) -> FoulIndex:
        artifact = self.catalog.artifact(result_id)
        try:
            return self._index(artifact)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(409, 'Native foul result is incomplete or incompatible') from exc

    def frames(self, result_id: str, offset: int, limit: int, revision: str | None = None) -> dict:
        index = self.result(result_id)
        if revision is not None and revision != index.descriptor['revision']:
            raise HTTPException(409, 'Foul result changed; reload the result descriptor')
        total = len(index.offsets)
        if offset > total:
            raise HTTPException(416, 'Frame offset is outside this foul result')
        stop, frames = min(offset + limit, total), []
        source = index.descriptor['source']
        try:
            with index.path.open('rb') as stream:
                for position in range(offset, stop):
                    stream.seek(index.offsets[position])
                    frame = _frame(stream.readline(MAX_LINE_BYTES + 1), position + 1,
                                   source['width'], source['height'])
                    frame['time_s'] = index.timestamps_s[position]
                    frames.append(frame)
            if tuple(_fingerprint(path) for path in index.paths) != index.fingerprints:
                raise ValueError('Foul records changed during the page read')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Foul records changed; reload the result descriptor') from exc
        return {'result_id': result_id, 'revision': index.descriptor['revision'], 'offset': offset,
                'total': total, 'next_offset': stop if stop < total else None, 'frames': frames}


def create_foul_presentation_router(repository: FoulPresentationRepository) -> APIRouter:
    router = APIRouter(prefix='/api/foul', tags=['native-foul'])

    @router.get('/results/{result_id}')
    def foul_result(result_id: str):
        return repository.result(result_id).descriptor

    @router.get('/results/{result_id}/frames')
    def foul_frames(result_id: str, offset: int = Query(0, ge=0),
                    limit: int = Query(MAX_PAGE_FRAMES, ge=1, le=MAX_PAGE_FRAMES),
                    revision: str | None = Query(None, pattern='^[0-9a-f]{64}$')):
        return repository.frames(result_id, offset, limit, revision)

    return router
