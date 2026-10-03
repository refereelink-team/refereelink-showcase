"""Conservative local contact verification with independent causal aftermath.

Geometry only schedules a classifier. A completed positive model window is
still required, and an event describes a contact region rather than presumed
offender/victim identities. The configuration is global and annotation-free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import hypot, isfinite
from typing import Any

from app.foul_detection.temporal import TemporalFoulDetector, VerificationConfig


@dataclass(frozen=True)
class ContactConfig:
    min_overlap: float = 0.45
    max_normalized_distance: float = 0.55
    min_detection_confidence: float = 0.30
    min_contact_age_s: float = 0.20
    max_contact_age_s: float = 0.65
    prior_window_s: float = 0.80
    prior_min_observations: int = 2
    prior_min_span_s: float = 0.20
    prior_max_overlap: float = 0.80
    prior_min_distance: float = 0.20
    aftermath_start_s: float = 0.12
    aftermath_min_observations: int = 2
    aftermath_min_span_s: float = 0.06
    aftermath_height_drop: float = 0.15
    aftermath_posture_change: float = 0.30
    region_center_distance: float = 0.75
    merge_anchor_gap_s: float = 0.60
    merge_min_overlap: float = 0.45

    def __post_init__(self) -> None:
        values = asdict(self)
        if (any(not isfinite(value) or value < 0 for value in values.values())
                or not 0 < self.min_overlap <= 1
                or not 0 <= self.min_detection_confidence <= 1
                or not 0 < self.prior_max_overlap <= 1
                or not 0 < self.merge_min_overlap <= 1
                or not 0 < self.max_normalized_distance
                or self.max_contact_age_s < self.min_contact_age_s
                or self.prior_window_s < self.prior_min_span_s
                or not isinstance(self.prior_min_observations, int)
                or self.prior_min_observations < 2
                or not isinstance(self.aftermath_min_observations, int)
                or self.aftermath_min_observations < 2):
            raise ValueError("Invalid causal contact configuration")


CONTACT_VERIFICATION_V3 = VerificationConfig(
    offence_threshold=0.60,
    action_threshold=0.70,
    confirmation_windows=1,
    stride_s=0.20,
    post_contact_s=0.20,
    evidence_threshold=0.0,
    max_windows_per_tick=2,
)


def _value(evidence: dict[str, Any], name: str) -> float | None:
    value = evidence.get(name)
    if not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return float(value)


def _intersection(first, second) -> float:
    return max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )


def _region_overlap(first, second) -> float:
    smaller = min(
        (first[2] - first[0]) * (first[3] - first[1]),
        (second[2] - second[0]) * (second[3] - second[1]),
    )
    return _intersection(first, second) / max(smaller, 1.0)


class ContactFoulDetector(TemporalFoulDetector):
    """Verify supported pairs while publishing unassigned regional evidence."""

    def __init__(self, predictor, config=None, record_sink=None,
                 contact_config: ContactConfig | None = None):
        super().__init__(predictor, "mvit-contact-v3", config or CONTACT_VERIFICATION_V3, record_sink)
        # This identifier distinguishes local-input inference using the same
        # strict-loaded VARS checkpoint; it does not imply different weights.
        self.predictor.model_id = "mvit-v2-local"
        self.contact_config = contact_config or ContactConfig()
        self._support_cache: dict[str, dict[str, Any] | None] = {}
        self._support_tick: float | None = None
        self._region_emissions: list[dict[str, Any]] = []
        # These counters count supported episode scheduling opportunities,
        # rather than unique incidents or classifier-confirmed fouls.
        self.stats.update(contact_qualified_pairs=0, contact_supported_candidates=0,
                          suppressed_contact_duplicates=0)

    def _reset_profile_state(self) -> None:
        self._support_cache.clear()
        self._support_tick = None
        self._region_emissions.clear()

    def _same_region(self, first, second) -> bool:
        if _intersection(first, second) > 0:
            return True
        height = max(first[3] - first[1], second[3] - second[1], 1.0)
        centers = [((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for box in (first, second)]
        return hypot(centers[0][0] - centers[1][0], centers[0][1] - centers[1][1]) / height <= self.contact_config.region_center_distance

    def _geometry(self, row, *, prior: bool = False) -> bool:
        evidence = row.get("evidence", {})
        confidence = _value(evidence, "min_detection_confidence")
        overlap = _value(evidence, "overlap_fraction")
        distance = _value(evidence, "normalized_distance")
        if any(value is None for value in (confidence, overlap, distance)):
            return False
        if confidence < self.contact_config.min_detection_confidence:
            return False
        if prior:
            return overlap < self.contact_config.prior_max_overlap and distance > self.contact_config.prior_min_distance
        return overlap >= self.contact_config.min_overlap and distance <= self.contact_config.max_normalized_distance

    @staticmethod
    def _spanning_rows(rows, minimum: int, span_s: float) -> bool:
        times = sorted({row["pts_s"] for row in rows if isfinite(row["pts_s"])})
        return len(times) >= minimum and times[-1] - times[0] >= span_s - 1e-9

    def _has_prior(self, history, contact_pts: float) -> bool:
        rows = [row for row in history
                if contact_pts - self.contact_config.prior_window_s <= row["pts_s"] < contact_pts
                and self._geometry(row, prior=True)]
        return self._spanning_rows(rows, self.contact_config.prior_min_observations,
                                   self.contact_config.prior_min_span_s)

    def _aftermath(self, history, anchor, sample_end: float):
        rows = []
        for row in history:
            if not anchor["pts_s"] + self.contact_config.aftermath_start_s <= row["pts_s"] <= sample_end + 1e-9:
                continue
            evidence = row.get("evidence", {})
            confidence = _value(evidence, "min_detection_confidence")
            height_drop = _value(evidence, "height_drop_fraction")
            posture = _value(evidence, "posture_change")
            if confidence is None or confidence < self.contact_config.min_detection_confidence:
                continue
            changed = (height_drop is not None and height_drop >= self.contact_config.aftermath_height_drop
                       or posture is not None and posture >= self.contact_config.aftermath_posture_change)
            if changed and self._same_region(anchor["region_xyxy"], row["region_xyxy"]):
                rows.append(row)
        return rows if self._spanning_rows(rows, self.contact_config.aftermath_min_observations,
                                          self.contact_config.aftermath_min_span_s) else []

    @staticmethod
    def _contact_strength(row) -> float:
        evidence = row["evidence"]
        growth = _value(evidence, "overlap_change") or 0.0
        approach = _value(evidence, "relative_approach_per_second") or 0.0
        change = max(_value(evidence, "posture_change") or 0.0,
                     _value(evidence, "height_drop_fraction") or 0.0)
        return 0.6 * max(0.0, min(growth, 1.0)) + 0.2 * min(max(approach, 0.0), 3.0) / 3.0 + 0.2 * min(max(change, 0.0), 1.0)

    def _support(self, episode, pts_s: float):
        if self._support_tick != pts_s:
            self._support_tick = pts_s
            self._support_cache.clear()
        if episode.id in self._support_cache:
            return self._support_cache[episode.id]
        self._support_cache[episode.id] = None
        window = self.interactions.sample_episode(
            episode, pts_s, duration_s=self.predictor.temporal_duration_s,
            count=self.predictor.temporal_frames, target_fps=self.predictor.temporal_fps,
            crop=True,
        )
        if window is None:
            return None
        sample_start, sample_end = min(window.source_pts), max(window.source_pts)
        candidates = []
        for row in episode.evidence_history:
            contact_pts = row["pts_s"]
            age = sample_end - contact_pts
            if not (sample_start - 1e-9 <= contact_pts <= sample_end + 1e-9
                    and self.contact_config.min_contact_age_s - 1e-9 <= age <= self.contact_config.max_contact_age_s + 1e-9
                    and self._geometry(row)
                    and self._has_prior(episode.evidence_history, contact_pts)):
                continue
            after = self._aftermath(episode.evidence_history, row, sample_end)
            if after:
                candidates.append((self._contact_strength(row), row, after))
        if not candidates:
            return None
        strength, anchor, after = max(candidates, key=lambda item: item[0])
        support = {
            "anchor": anchor,
            "aftermath": after,
            "sample_start_pts_s": sample_start,
            "sample_end_pts_s": sample_end,
            "source_frame_ids": list(window.frame_indices),
            "model_crop_xyxy": window.region_xyxy,
            "strength": strength,
        }
        self._support_cache[episode.id] = support
        return support

    def _ready_episodes(self, pts_s: float):
        ready = []
        for episode in self._observed_episodes:
            if not episode.emitted:
                support = self._support(episode, pts_s)
                if support is not None:
                    ready.append(episode)
        self.stats["contact_qualified_pairs"] += len(ready)
        self.stats["contact_supported_candidates"] += len(ready)

        def priority(episode):
            support = self._support_cache[episode.id]
            crop = support["model_crop_xyxy"]
            area = (crop[2] - crop[0]) * (crop[3] - crop[1]) if crop else float("inf")
            return self.last_window_by_episode.get(episode.id, float("-inf")), area, -support["strength"]

        return sorted(ready, key=priority)

    def _interaction_peak(self, episode, pts_s: float):
        support = self._support(episode, pts_s)
        if support is None:
            return None
        anchor = support["anchor"]
        evidence = {**anchor["evidence"], "contact_strength": support["strength"]}
        return anchor["pts_s"], evidence, anchor["region_xyxy"]

    def _crop_to_pair(self) -> bool:
        return True

    def _decorate_evidence(self, evidence, episode, peak, pts_s):
        support = self._support(episode, pts_s)
        if support is None:
            raise RuntimeError("A contact alert requires current causal physical support")
        after = support["aftermath"]
        return {
            **evidence,
            "localization_status": "contact_supported_region",
            "attribution_reliable": False,
            "involved_targets": [],
            "offender": None,
            "victim": None,
            "confirmation_mode": "fresh_model_with_independent_aftermath",
            "contact_support": {
                "sample_start_pts_s": support["sample_start_pts_s"],
                "sample_end_pts_s": support["sample_end_pts_s"],
                "contact_pts_s": support["anchor"]["pts_s"],
                "aftermath_pts_s": [row["pts_s"] for row in after],
                "aftermath_span_s": max(row["pts_s"] for row in after) - min(row["pts_s"] for row in after),
                "source_frame_ids": support["source_frame_ids"],
            },
        }

    @staticmethod
    def _reliable_members(episode) -> set[int]:
        return {actor["track_id"] for actor in episode.identities
                if actor.get("identity_reliable") and actor.get("track_id") is not None}

    def _mark_emitted(self, episode, window_id: str, peak, pts_s: float) -> bool:
        contact_pts, _, region = peak
        members = self._reliable_members(episode)
        ambiguous = len(members) < 2
        for old in self._region_emissions:
            if old["epoch"] != episode.epoch or abs(old["contact_pts"] - contact_pts) > self.contact_config.merge_anchor_gap_s:
                continue
            if not (ambiguous or old["ambiguous"]):
                continue
            # Separate, reliably observed pairs remain independent, even when
            # their regions happen to overlap in a crowded scene.
            if len(members) >= 2 and len(old["members"]) >= 2 and members.isdisjoint(old["members"]):
                continue
            if _region_overlap(old["region"], region) < self.contact_config.merge_min_overlap:
                continue
            self.interactions.try_mark_emitted(episode.id, window_id)
            self.stats["suppressed_contact_duplicates"] += 1
            return False
        if not self.interactions.try_mark_emitted(episode.id, window_id):
            return False
        self._region_emissions.append({
            "epoch": episode.epoch, "contact_pts": contact_pts,
            "region": region, "members": members, "ambiguous": ambiguous,
        })
        self._region_emissions = [old for old in self._region_emissions
                                  if pts_s - old["contact_pts"] <= self.interactions.config.buffer_seconds]
        return True

    def configuration(self):
        return {
            **super().configuration(),
            "contact_config": asdict(self.contact_config),
            "confirmation_mode": "fresh_model_with_independent_aftermath",
        }
