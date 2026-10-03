"""Causal player-interaction proposals and source-time evidence windows.

Proximity, overlap and posture changes describe an interaction; none establishes
a foul. This module has no classifier, match annotation or player attribution
rule. A verifier must explicitly confirm an episode before marking it emitted.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from itertools import combinations
from math import ceil, floor, isfinite, sqrt
from typing import Any, Mapping, Sequence

import numpy as np


BBox = tuple[float, float, float, float]
PairKey = tuple[str, str]


@dataclass(frozen=True)
class InteractionConfig:
    """Generic proposal and retention parameters, independent of a video."""

    buffer_seconds: float = 3.0
    release_seconds: float = 0.60
    proximity_distance: float = 0.85
    approach_distance: float = 1.65
    approach_speed: float = 0.70
    overlap_fraction: float = 0.08
    min_detection_confidence: float = 0.15
    max_active_pairs: int = 32
    discontinuity_seconds: float = 0.50
    max_sample_gap_seconds: float = 0.25
    crop_context: float = 0.40
    scene_difference: float = 0.45


@dataclass(frozen=True)
class PlayerObservation:
    key: str
    track_id: int
    entity_id: int | None
    bbox: BBox
    team: str
    confidence: float
    identity_reliable: bool
    role: str = "unknown"
    role_confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id if self.identity_reliable else None,
            "observed_track_id": self.track_id,
            "entity_id": self.entity_id if self.identity_reliable else None,
            "team": self.team,
            "role": self.role if self.role_confidence >= 0.5 else "unknown",
            "observed_role": self.role,
            "role_confidence": self.role_confidence,
            "identity_reliable": self.identity_reliable,
            "bbox": list(self.bbox),
        }


@dataclass(frozen=True)
class EvidenceFrame:
    """An owned image and observations from one received source frame."""

    frame: np.ndarray
    frame_index: int
    pts_s: float
    epoch: int
    players: Mapping[str, PlayerObservation]


@dataclass
class InteractionEpisode:
    """One continuous pair interaction, with no presumed offender or victim."""

    id: str
    key: PairKey
    epoch: int
    start_pts_s: float
    last_pts_s: float
    peak_pts_s: float
    region_xyxy: BBox
    identities: list[dict[str, Any]]
    evidence: dict[str, Any]
    peak_evidence: dict[str, Any] = field(default_factory=dict)
    evidence_history: list[dict[str, Any]] = field(default_factory=list)
    peak_strength: float = 0.0
    observation_count: int = 1
    emitted: bool = False
    emitted_window_id: str | None = None
    ended_pts_s: float | None = None

    @property
    def pair_key(self) -> PairKey:
        return self.key

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pair_key": list(self.key),
            "epoch": self.epoch,
            "start_pts_s": self.start_pts_s,
            "last_pts_s": self.last_pts_s,
            "peak_pts_s": self.peak_pts_s,
            "region_xyxy": list(self.region_xyxy),
            "identities": self.identities,
            "evidence": self.evidence,
            "peak_evidence": self.peak_evidence,
            "observation_count": self.observation_count,
            "emitted": self.emitted,
            "ended_pts_s": self.ended_pts_s,
        }

    def peak_in_interval(
        self, start_pts_s: float, end_pts_s: float
    ) -> tuple[float, dict[str, Any], BBox] | None:
        """Localize motion inside received verification evidence, not an old peak."""
        available = [
            item for item in self.evidence_history
            if start_pts_s <= item["pts_s"] <= end_pts_s
        ]
        if not available:
            return None
        peak = max(available, key=lambda item: item["evidence"]["proposal_strength"])
        return peak["pts_s"], peak["evidence"], peak["region_xyxy"]


@dataclass(frozen=True)
class SampledWindow:
    """A causal window sampled in media time, rather than input frame count."""

    frames: list[np.ndarray]
    source_pts: list[float]
    frame_indices: list[int]
    start_pts_s: float
    end_pts_s: float
    epoch: int
    region_xyxy: BBox | None
    observation_coverage: float
    window_id: str

    @property
    def source_pts_s(self) -> list[float]:
        return self.source_pts


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) else default


def _integer(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None and result.is_integer() else None


def _union(boxes: Sequence[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _distance(first: BBox, second: BBox) -> float:
    """Compare foot locations using the mean apparent player height."""
    scale = max(((first[3] - first[1]) + (second[3] - second[1])) / 2.0, 1.0)
    dx = ((first[0] + first[2]) - (second[0] + second[2])) / 2.0
    dy = first[3] - second[3]
    return sqrt(dx * dx + dy * dy) / scale


def _overlap(first: BBox, second: BBox) -> float:
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    smaller_area = min(
        (first[2] - first[0]) * (first[3] - first[1]),
        (second[2] - second[0]) * (second[3] - second[1]),
    )
    return intersection / max(smaller_area, 1.0)


class InteractionDetector:
    """Maintain pair episodes, evidence ownership and an explicit emit ledger.

    ``observe`` returns active proposals, including a brief occlusion grace
    interval. Consumers choose their own model schedule and confirmation rule.
    Unknown teams are never used to reject a pair. Known same-team interactions
    are retained too, because a semantic label is not sufficient foul evidence.
    """

    def __init__(self, config: InteractionConfig | None = None) -> None:
        self.config = config or InteractionConfig()
        if self.config.buffer_seconds <= 0 or self.config.release_seconds <= 0:
            raise ValueError("Evidence retention and episode release must be positive")
        if self.config.max_active_pairs < 1:
            raise ValueError("At least one proposal slot is required")
        self.buffer: deque[EvidenceFrame] = deque()
        self.episodes: dict[str, InteractionEpisode] = {}
        self._active: dict[PairKey, InteractionEpisode] = {}
        self._previous: dict[str, PlayerObservation] = {}
        self._last_pts_s: float | None = None
        self._last_signature: np.ndarray | None = None
        self._identity_counts: dict[str, int] = {}
        self._aliases: dict[str, str] = {}
        self._uncertain_associations: set[str] = set()
        self._sequence = 0
        self.epoch = 0
        self.stats: dict[str, int] = {
            "received_frames": 0,
            "duplicate_pts": 0,
            "discontinuities": 0,
            "scene_cuts": 0,
            "proposals": 0,
            "episodes": 0,
            "emitted_episodes": 0,
            "proposal_budget_drops": 0,
            "rejected_windows": 0,
        }

    def _reset(self, pts_s: float) -> None:
        for episode in self._active.values():
            episode.ended_pts_s = episode.last_pts_s
        self._active.clear()
        self.buffer.clear()
        self._previous.clear()
        self._identity_counts.clear()
        self._aliases.clear()
        self._uncertain_associations.clear()
        self._last_pts_s = None
        self._last_signature = None
        self.epoch += 1

    @staticmethod
    def _signature(frame: np.ndarray) -> np.ndarray:
        # Broad scene changes are checked on a small grid. Small camera motion
        # or moving players cannot normally change most of this image at once.
        y = np.linspace(0, frame.shape[0] - 1, 24, dtype=int)
        x = np.linspace(0, frame.shape[1] - 1, 32, dtype=int)
        pixels = frame[y[:, None], x[None, :]]
        if pixels.ndim == 3:
            pixels = pixels.mean(axis=2)
        return pixels.astype(np.float32) / 255.0

    def _players(self, players: Sequence[Any], frame: np.ndarray) -> dict[str, PlayerObservation]:
        result: dict[str, PlayerObservation] = {}
        for raw in players:
            data = raw if isinstance(raw, Mapping) else raw.model_dump()
            track_id = _integer(data.get("track_id"))
            if track_id is None:
                continue
            box = data.get("bbox")
            if not isinstance(box, (list, tuple, np.ndarray)) or len(box) != 4:
                continue
            values = [_number(value) for value in box]
            if any(value is None for value in values):
                continue
            x1, y1, x2, y2 = (float(value) for value in values)
            height, width = frame.shape[:2]
            bbox = (max(0.0, x1), max(0.0, y1), min(float(width), x2), min(float(height), y2))
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            confidence = _number(data.get("confidence"), 1.0) or 0.0
            if confidence < self.config.min_detection_confidence:
                continue
            entity_id = _integer(data.get("entity_id"))
            key = f"entity:{entity_id}" if entity_id is not None else f"track:{track_id}"
            self._identity_counts[key] = self._identity_counts.get(key, 0) + 1
            detected = str(data.get("track_status", "detected")) == "detected"
            role = str(getattr(data.get("role"), "value", data.get("role", "unknown")))
            role_confidence = _number(data.get("role_confidence"), 0.0) or 0.0
            if role == "player":
                role = "outfield"
            if role not in {"outfield", "goalkeeper", "referee"}:
                role = "unknown"
            if role == "referee" and role_confidence >= 0.75:
                continue
            team = str(getattr(data.get("team"), "value", data.get("team", "unknown")))
            if team not in {"home", "away"}:
                team = "unknown"
            result[key] = PlayerObservation(
                key, track_id, entity_id, bbox, team, confidence,
                detected and self._identity_counts[key] >= 3,
                role=role, role_confidence=role_confidence,
            )
        return result

    def _associate_continuity(
        self, observations: dict[str, PlayerObservation], pts_s: float
    ) -> dict[str, PlayerObservation]:
        """Keep an interrupted interaction without claiming a new ID is the old player.

        A continuity hypothesis requires one unchanged member of an active pair,
        one absent member and a nearby replacement inside the short grace span.
        Its actor identity remains unknown for the rest of the episode. This
        association supports episode merging, not offender/victim attribution.
        """
        canonical = {
            self._aliases.get(key, key): replace(
                player, key=self._aliases.get(key, key),
                identity_reliable=player.identity_reliable
                and self._aliases.get(key, key) not in self._uncertain_associations,
            )
            for key, player in observations.items()
        }
        active_keys = {key for episode in self._active.values() for key in episode.key}
        for episode in self._active.values():
            present = [key for key in episode.key if key in canonical]
            absent = [key for key in episode.key if key not in canonical]
            if len(present) != 1 or len(absent) != 1 or pts_s - episode.last_pts_s > self.config.release_seconds:
                continue
            missing = absent[0]
            old = None
            for item in reversed(self.buffer):
                if pts_s - item.pts_s > self.config.release_seconds:
                    break
                if missing in item.players:
                    old = item.players[missing]
                    break
            if old is None:
                continue
            possibilities = [
                player for key, player in canonical.items()
                if key not in active_keys
                and (_overlap(old.bbox, player.bbox) >= 0.20 or _distance(old.bbox, player.bbox) <= 0.45)
            ]
            if len(possibilities) != 1:
                continue
            replacement = possibilities[0]
            del canonical[replacement.key]
            self._aliases[replacement.key] = missing
            self._uncertain_associations.add(missing)
            canonical[missing] = replace(replacement, key=missing, identity_reliable=False)
        return canonical

    def _evidence(
        self, first: PlayerObservation, second: PlayerObservation, delta_s: float, pts_s: float
    ) -> dict[str, Any]:
        distance = _distance(first.bbox, second.bbox)
        overlap = _overlap(first.bbox, second.bbox)
        previous = (self._previous.get(first.key), self._previous.get(second.key))
        comparison_delta = delta_s
        # Compare motion over a short source-time interval, rather than one
        # detector step, to reduce FPS dependence and box jitter amplification.
        for item in reversed(self.buffer):
            age = pts_s - item.pts_s
            if age < 0.20:
                continue
            if age > 0.45:
                break
            if first.key in item.players and second.key in item.players:
                previous = (item.players[first.key], item.players[second.key])
                comparison_delta = age
                break
        approach = 0.0
        posture_change = 0.0
        height_drop = 0.0
        overlap_change = 0.0
        if previous[0] is not None and previous[1] is not None and comparison_delta > 0:
            approach = (_distance(previous[0].bbox, previous[1].bbox) - distance) / comparison_delta
            overlap_change = max(0.0, overlap - _overlap(previous[0].bbox, previous[1].bbox))
            drops = []
            for current, old in zip((first, second), previous):
                assert old is not None
                ratio = (current.bbox[2] - current.bbox[0]) / max(current.bbox[3] - current.bbox[1], 1)
                old_ratio = (old.bbox[2] - old.bbox[0]) / max(old.bbox[3] - old.bbox[1], 1)
                posture_change = max(posture_change, abs(ratio - old_ratio))
                drops.append(1.0 - (current.bbox[3] - current.bbox[1]) / max(old.bbox[3] - old.bbox[1], 1))
            # Common scale changes affect both players. Record the relative
            # height drop, so a camera zoom is not treated as a player falling.
            height_drop = max(0.0, max(drops) - max(0.0, min(drops)))
        proximity = distance <= self.config.proximity_distance
        approaching = distance <= self.config.approach_distance and approach >= self.config.approach_speed
        overlapping = overlap >= self.config.overlap_fraction
        motion_change = distance <= self.config.approach_distance and (
            posture_change >= 0.25 or height_drop >= 0.15
        )
        identity_ambiguity = overlap >= 0.95 and distance <= 0.15
        strong_evidence = bool(
            motion_change
            or (approaching and overlap_change >= 0.08)
        )
        strength = (
            0.15 * max(0.0, 1.0 - distance / self.config.approach_distance)
            + 0.15 * overlap_change
            + 0.25 * min(max(approach, 0.0), 3.0) / 3.0
            + 0.30 * min(posture_change, 1.0)
            + 0.15 * min(height_drop, 1.0)
        )
        return {
            "candidate": bool(proximity or approaching or overlapping or motion_change),
            "normalized_distance": distance,
            "relative_approach_per_second": approach,
            "overlap_fraction": overlap,
            "posture_change": posture_change,
            "height_drop_fraction": height_drop,
            "overlap_change": overlap_change,
            "strong_evidence": strong_evidence,
            "min_detection_confidence": min(first.confidence, second.confidence),
            "identity_ambiguity": identity_ambiguity,
            "proximity": proximity,
            "approaching": approaching,
            "overlapping": overlapping,
            "motion_change": motion_change,
            "proposal_strength": strength,
        }

    def observe(
        self,
        frame: np.ndarray,
        frame_index: int,
        pts_s: float,
        players: Sequence[Any],
        *,
        scene_cut: bool = False,
    ) -> list[InteractionEpisode]:
        """Accept one frame in source order and return proposals, never alerts."""
        if not isinstance(frame, np.ndarray) or frame.ndim not in (2, 3) or not frame.size:
            raise ValueError("A nonempty source image is required")
        if not isfinite(pts_s):
            raise ValueError("Source PTS must be finite")
        signature = self._signature(frame)
        discontinuity = self._last_pts_s is not None and (
            pts_s < self._last_pts_s
            or pts_s - self._last_pts_s > self.config.discontinuity_seconds
        )
        cut = scene_cut or (
            self._last_signature is not None
            and float(np.mean(np.abs(signature - self._last_signature))) >= self.config.scene_difference
        )
        changed_shape = bool(self.buffer and self.buffer[-1].frame.shape != frame.shape)
        if discontinuity or cut or changed_shape:
            self.stats["scene_cuts" if cut or changed_shape else "discontinuities"] += 1
            self._reset(pts_s)
        if self._last_pts_s is not None and pts_s == self._last_pts_s:
            self.stats["duplicate_pts"] += 1
            return list(self._active.values())

        delta_s = pts_s - self._last_pts_s if self._last_pts_s is not None else 0.0
        observations = self._associate_continuity(self._players(players, frame), pts_s)
        self.buffer.append(EvidenceFrame(frame.copy(), int(frame_index), pts_s, self.epoch, observations))
        while self.buffer and self.buffer[0].pts_s < pts_s - self.config.buffer_seconds:
            self.buffer.popleft()
        self.stats["received_frames"] += 1

        # Expire separated episodes before considering fresh proposals, so a
        # returning pair creates a new episode without a global cooldown.
        for key, episode in list(self._active.items()):
            if pts_s - episode.last_pts_s > self.config.release_seconds:
                episode.ended_pts_s = episode.last_pts_s
                del self._active[key]

        candidates: list[tuple[float, PairKey, PlayerObservation, PlayerObservation, dict[str, Any]]] = []
        for first, second in combinations(observations.values(), 2):
            evidence = self._evidence(first, second, delta_s, pts_s)
            if evidence["candidate"]:
                key = tuple(sorted((first.key, second.key)))
                candidates.append((evidence["proposal_strength"], key, first, second, evidence))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        self.stats["proposal_budget_drops"] += max(0, len(candidates) - self.config.max_active_pairs)
        for strength, key, first, second, evidence in candidates[: self.config.max_active_pairs]:
            self.stats["proposals"] += 1
            region = _union((first.bbox, second.bbox))
            identities = [player.as_dict() for player in sorted((first, second), key=lambda player: player.key)]
            if evidence["identity_ambiguity"]:
                identities = [
                    {**identity, "track_id": None, "entity_id": None, "identity_reliable": False}
                    for identity in identities
                ]
            episode = self._active.get(key)
            if episode is None:
                self._sequence += 1
                episode = InteractionEpisode(
                    id=f"interaction:{self.epoch}:{self._sequence}", key=key, epoch=self.epoch,
                    start_pts_s=pts_s, last_pts_s=pts_s, peak_pts_s=pts_s,
                    region_xyxy=region, identities=identities, evidence=evidence,
                    peak_strength=strength, peak_evidence=evidence,
                )
                self._active[key] = episode
                self.episodes[episode.id] = episode
                self.stats["episodes"] += 1
            else:
                episode.last_pts_s = pts_s
                episode.observation_count += 1
                episode.evidence = evidence
                if strength > episode.peak_strength:
                    episode.peak_strength = strength
                    episode.peak_pts_s = pts_s
                    episode.region_xyxy = region
                    episode.peak_evidence = evidence
                episode.identities = identities
            episode.evidence_history.append({
                "pts_s": pts_s, "region_xyxy": region, "identities": identities,
                "evidence": evidence,
            })
            episode.evidence_history = [
                item for item in episode.evidence_history
                if item["pts_s"] >= pts_s - self.config.buffer_seconds
            ]

        observed_keys = set(observations)
        for episode in self._active.values():
            # During a missing detection, retain the episode and location but
            # explicitly withhold the missing identity instead of guessing it.
            episode.identities = [
                identity if key in observed_keys else {
                    **identity, "track_id": None, "entity_id": None, "identity_reliable": False
                }
                for key, identity in zip(episode.key, episode.identities)
            ]
        self._previous = observations
        self._last_pts_s = pts_s
        self._last_signature = signature
        active = sorted(self._active.values(), key=lambda episode: (-episode.peak_strength, episode.id))
        return active[: self.config.max_active_pairs]

    def try_mark_emitted(self, episode_id: str, window_id: str) -> bool:
        """Record one confirmed alert; repeated windows or later scores cannot emit again."""
        episode = self.episodes.get(episode_id)
        if episode is None or episode.emitted or episode.epoch != self.epoch or not window_id:
            return False
        episode.emitted = True
        episode.emitted_window_id = window_id
        self.stats["emitted_episodes"] += 1
        return True

    def sample_episode(
        self,
        episode: InteractionEpisode,
        end_pts_s: float,
        *,
        duration_s: float = 0.96,
        count: int = 16,
        target_fps: float = 17.0,
        crop: bool = False,
    ) -> SampledWindow | None:
        """Sample the official temporal span using only already received images.

        Sixteen targets occur at 17 Hz within a 0.96-second source window.
        A low-FPS source may repeat a nearest frame; that does not create new
        visual evidence. Source times and indices expose such repeats directly.
        A single fixed crop encloses both players across the sampled window.
        """
        if (
            not all(isfinite(value) for value in (end_pts_s, duration_s, target_fps))
            or duration_s <= 0 or count < 2 or target_fps <= 0
            or (count - 1) / target_fps > duration_s
        ):
            raise ValueError("Invalid temporal sampling parameters")
        start_pts_s = end_pts_s - duration_s
        available = [
            item for item in self.buffer
            if item.epoch == episode.epoch and item.pts_s <= end_pts_s + 1e-9
        ]
        if (
            episode.epoch != self.epoch or len(available) < 2
            or self._last_pts_s is None or end_pts_s > self._last_pts_s + 1e-9
            or available[0].pts_s > start_pts_s + 1e-6
            or available[-1].pts_s < end_pts_s - self.config.max_sample_gap_seconds
        ):
            self.stats["rejected_windows"] += 1
            return None
        times = np.asarray([item.pts_s for item in available])
        targets = start_pts_s + np.arange(count) / target_fps
        indexes = np.searchsorted(times, targets)
        indexes = np.clip(indexes, 0, len(times) - 1)
        left_indexes = np.maximum(indexes - 1, 0)
        choose_left = np.abs(times[left_indexes] - targets) <= np.abs(times[indexes] - targets)
        indexes = np.where(choose_left, left_indexes, indexes)
        selected = [available[int(index)] for index in indexes]
        relevant_times = times[(times >= start_pts_s) & (times <= end_pts_s)]
        if (
            np.max(np.abs(times[indexes] - targets)) > self.config.max_sample_gap_seconds
            or (len(relevant_times) > 1 and np.max(np.diff(relevant_times)) > self.config.max_sample_gap_seconds)
        ):
            self.stats["rejected_windows"] += 1
            return None
        boxes: list[BBox] = []
        both_present = 0
        for item in selected:
            pair = [item.players.get(key) for key in episode.key]
            boxes.extend(player.bbox for player in pair if player is not None)
            both_present += int(all(player is not None for player in pair))
        region: BBox | None = None
        frames = [item.frame for item in selected]
        if crop:
            if not boxes:
                self.stats["rejected_windows"] += 1
                return None
            x1, y1, x2, y2 = _union(boxes)
            player_height = max(box[3] - box[1] for box in boxes)
            side = max(x2 - x1, y2 - y1) + 2.0 * self.config.crop_context * player_height
            height, width = frames[0].shape[:2]
            side = min(float(min(width, height)), side)
            # A wide pair may require a rectangular crop. Preserve every box;
            # never silently truncate a player to force a square input.
            crop_width = max(side, x2 - x1)
            crop_height = max(side, y2 - y1)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            left = max(0, min(floor(cx - crop_width / 2), width - ceil(crop_width)))
            top = max(0, min(floor(cy - crop_height / 2), height - ceil(crop_height)))
            right, bottom = min(width, left + ceil(crop_width)), min(height, top + ceil(crop_height))
            region = (float(left), float(top), float(right), float(bottom))
            frames = [frame[top:bottom, left:right] for frame in frames]
        source_pts = [item.pts_s for item in selected]
        frame_indices = [item.frame_index for item in selected]
        window_id = f"{episode.id}:{end_pts_s:.9f}:{','.join(map(str, frame_indices))}"
        return SampledWindow(
            frames=frames, source_pts=source_pts, frame_indices=frame_indices,
            start_pts_s=start_pts_s, end_pts_s=end_pts_s, epoch=episode.epoch,
            region_xyxy=region, observation_coverage=both_present / count, window_id=window_id,
        )
