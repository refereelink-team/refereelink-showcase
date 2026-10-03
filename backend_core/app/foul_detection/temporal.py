"""Causal verification of player-interaction episodes, never clip annotations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Callable
import time

from app.foul_detection.interactions import InteractionDetector


PROFILE_IDS = ("legacy-v1", "mvit-full-v2", "mvit-pair-v2", "multidim-full-v2", "mvit-contact-v3")


@dataclass(frozen=True)
class VerificationConfig:
    """Global parameters; evaluate and freeze without per-video overrides."""

    offence_threshold: float = 0.68
    action_threshold: float = 0.50
    confirmation_windows: int = 2
    stride_s: float = 0.20
    post_contact_s: float = 0.35
    evidence_threshold: float = 0.0
    max_windows_per_tick: int = 2

    def __post_init__(self):
        if not (0 <= self.offence_threshold <= 1 and 0 <= self.action_threshold <= 1
                and self.confirmation_windows >= 1 and self.stride_s > 0
                and self.post_contact_s >= 0 and self.max_windows_per_tick >= 1
                and isinstance(self.confirmation_windows, int)
                and isinstance(self.max_windows_per_tick, int)
                and all(isfinite(value) for value in
                        (self.evidence_threshold, self.stride_s, self.post_contact_s))):
            raise ValueError("Invalid causal verification configuration")


# A frozen research configuration, not an approval to promote a production
# model. The same values apply to every source and comparison branch.
EXPERIMENTAL_CONFIG_V2 = VerificationConfig(
    offence_threshold=0.55, action_threshold=0.70, evidence_threshold=0.25,
    confirmation_windows=2, post_contact_s=0.20,
)


class TemporalFoulDetector:
    """Only fresh, completed model windows can confirm an interaction.

    The cache is for evidence, never a repeating alert source. Track labels are
    runtime identities; their order does not identify offender and victim.
    """

    causal_interactions = True

    def __init__(self, predictor, profile_id: str, config: VerificationConfig | None = None,
                 record_sink: Callable[[dict], None] | None = None):
        if profile_id not in PROFILE_IDS[1:]:
            raise ValueError("A causal detector requires a versioned experimental profile")
        self.predictor = predictor
        self.profile_id = profile_id
        self.config = config or EXPERIMENTAL_CONFIG_V2
        self.interactions = InteractionDetector()
        self.record_sink = record_sink
        self.source_pts_s = None
        self.inference_count = 0
        self.latest_prediction = None
        self.last_tick_s = float("-inf")
        self.last_window_by_episode = {}
        self.last_sample_by_episode = {}
        self.streaks = {}
        self.pending = []
        self.records = []
        self.wall_clock_origin = None
        self._epoch = self.interactions.epoch
        self.stats = {"budget_deferred_windows": 0, "rejected_no_offence": 0,
                      "fresh_windows": 0, "confirmed_episodes": 0}

    def update(self, frame, frame_index: int, *, pts_s: float, players):
        try:
            return self._update(frame, frame_index, pts_s=pts_s, players=players)
        except (ValueError, RuntimeError, TypeError, AttributeError) as exc:
            errors = getattr(self.predictor, "errors", None)
            if isinstance(errors, list):
                errors.append(f"{type(exc).__name__}: {exc}")
            raise

    def _update(self, frame, frame_index: int, *, pts_s: float, players):
        if not isfinite(pts_s):
            raise ValueError("Source PTS must be finite")
        episodes = self.interactions.observe(frame, frame_index, pts_s, players)
        self._observed_episodes = episodes
        if pts_s < self.last_tick_s or self._epoch != self.interactions.epoch:
            self._epoch = self.interactions.epoch
            self.last_tick_s = float("-inf")
            self.streaks.clear()
            self.last_window_by_episode.clear()
            self.last_sample_by_episode.clear()
            self.pending.clear()
            self._reset_profile_state()
        if pts_s - self.last_tick_s >= self.config.stride_s - 1e-6:
            self.last_tick_s = pts_s
            ready = self._ready_episodes(pts_s)
            done = 0
            for ep in ready:
                local_peak = self._interaction_peak(ep, pts_s)
                if local_peak is None:
                    continue
                contact_pts, evidence, contact_region = local_peak
                strength = float(evidence.get("proposal_strength", 0.0))
                if strength < self.config.evidence_threshold:
                    continue
                window = self.interactions.sample_episode(
                    ep, pts_s, duration_s=self.predictor.temporal_duration_s,
                    count=self.predictor.temporal_frames, target_fps=self.predictor.temporal_fps,
                    crop=self._crop_to_pair(),
                )
                if window is None:
                    continue
                sample_key = tuple(window.frame_indices)
                if self.last_sample_by_episode.get(ep.id) == sample_key:
                    continue
                if done >= self.config.max_windows_per_tick:
                    self.stats["budget_deferred_windows"] += 1
                    continue
                done += 1
                self.last_window_by_episode[ep.id] = pts_s
                self.last_sample_by_episode[ep.id] = sample_key
                self.predictor.window_context = {
                    "episode_id": ep.id, "sample_pts_s": window.source_pts,
                    "sample_frame_ids": window.frame_indices,
                    "window_start_media_pts_s": window.start_pts_s,
                    "window_end_media_pts_s": window.end_pts_s,
                    "region_xyxy": list(window.region_xyxy) if window.region_xyxy else None,
                    "source_width": frame.shape[1], "source_height": frame.shape[0],
                }
                prediction_started = time.perf_counter()
                prediction = self.predictor.predict(window.frames)
                prediction_ms = (time.perf_counter() - prediction_started) * 1000.0
                self.inference_count += 1
                self.stats["fresh_windows"] += 1
                raw = getattr(self.predictor, "last_record", None) or {}
                raw = {**raw, "model_decision": raw.get("model_decision", raw.get("decision")),
                       "model_action": raw.get("model_action", raw.get("action")),
                       "synchronized_latency_ms": prediction_ms}
                self.predictor.last_record = raw
                severity = raw.get("severity_probs", [])
                action = raw.get("action_probs", [])
                if (len(severity) != 4 or len(action) != 8
                    or any(not isfinite(value) or not 0 <= value <= 1 for value in severity + action)
                    or abs(sum(severity) - 1) > 1e-3 or abs(sum(action) - 1) > 1e-3):
                    raise RuntimeError("Invalid or non-finite model probability vectors")
                scores = (1 - severity[0], max(action), max(severity))
                if not all(isfinite(x) for x in scores):
                    raise RuntimeError("Non-finite verification score")
                evidence = dict(evidence)
                eligible = bool(prediction is not None and prediction.decision != "no_offence"
                                and scores[0] >= self.config.offence_threshold
                                and scores[1] >= self.config.action_threshold
                                and strength >= self.config.evidence_threshold
                                and pts_s >= contact_pts + self.config.post_contact_s)
                if prediction is None or prediction.decision == "no_offence":
                    self.stats["rejected_no_offence"] += 1
                previous = self.streaks.get(ep.id, (None, 0))
                label = prediction.action if prediction is not None else None
                streak = previous[1] + 1 if eligible and previous[0] == label else 1 if eligible else 0
                self.streaks[ep.id] = (label, streak)
                record = {**raw, **self.predictor.window_context,
                          "detection_profile": self.profile_id, "offence_score": scores[0],
                          "action_score": scores[1], "severity_score": scores[2],
                          "interaction_evidence": evidence,
                          "interaction_peak_pts_s": contact_pts,
                          "interaction_region_xyxy": list(contact_region),
                          "interaction_history": [dict(item) for item in ep.evidence_history],
                          "involved_targets": ep.identities,
                          "eligible": eligible, "confirmation_streak": streak}
                self.records.append(record)
                if self.record_sink is not None:
                    self.record_sink(record)
                if not eligible or streak < self.config.confirmation_windows:
                    continue
                window_id = f"{ep.id}:{frame_index}"
                if not self._mark_emitted(ep, window_id, local_peak, pts_s):
                    continue
                prediction.confidence = scores[0]
                prediction.raw_scores.update(
                    offence_score=scores[0], action_score=scores[1], severity_score=scores[2],
                    event_evidence={
                        "detection_profile": self.profile_id,
                        "model_id": self.predictor.model_id,
                        "episode_id": ep.id, "event_time_s": contact_pts,
                        "emitted_time_s": pts_s,
                        "evidence_start_s": min(window.start_pts_s, contact_pts), "evidence_end_s": pts_s,
                        "region_xyxy": list(contact_region),
                        "model_region_xyxy": list(window.region_xyxy) if window.region_xyxy else None,
                        "source_width": frame.shape[1], "source_height": frame.shape[0],
                        "involved_targets": [], "candidate_targets": ep.identities,
                        "attribution_reliable": False,
                        "localization_status": "candidate_interaction",
                        "offender": None, "victim": None,
                        "interaction_evidence": evidence,
                        "confirmation_windows": streak,
                    },
                )
                prediction.raw_scores["event_evidence"] = self._decorate_evidence(
                    prediction.raw_scores["event_evidence"], ep, local_peak, pts_s)
                self.stats["confirmed_episodes"] += 1
                self.latest_prediction = prediction
                self.pending.append(prediction)
        # Each queued confirmation is delivered exactly once, including two
        # independently verified pairs completed in the same source frame.
        if not self.pending:
            return None
        return self._deliver(self.pending.pop(0), pts_s)

    def _deliver(self, prediction, pts_s):
        evidence = prediction.raw_scores["event_evidence"]
        evidence["emitted_time_s"] = pts_s
        if self.wall_clock_origin is not None:
            evidence["emitted_wall_s"] = time.perf_counter() - self.wall_clock_origin
            evidence["realtime_latency_s"] = evidence["emitted_wall_s"] - evidence["event_time_s"]
        return prediction

    def drain_pending(self, pts_s):
        """Deliver all completed confirmations, including the final input frame."""
        predictions, self.pending = self.pending, []
        return [self._deliver(prediction, pts_s) for prediction in predictions]

    def configuration(self):
        return asdict(self.config)

    def _ready_episodes(self, pts_s):
        # Fair scheduling preserves independent interactions in V2 profiles.
        ready = [ep for ep in self._observed_episodes if not ep.emitted]
        ready.sort(key=lambda ep: self.last_window_by_episode.get(ep.id, float("-inf")))
        return ready

    def _interaction_peak(self, episode, pts_s):
        return episode.peak_in_interval(pts_s - 1.2, pts_s - self.config.post_contact_s)

    def _crop_to_pair(self):
        return self.profile_id == "mvit-pair-v2"

    def _mark_emitted(self, episode, window_id, peak, pts_s):
        return self.interactions.try_mark_emitted(episode.id, window_id)

    def _decorate_evidence(self, evidence, episode, peak, pts_s):
        return evidence

    def _reset_profile_state(self):
        pass
