from __future__ import annotations

from typing import Iterable

import numpy as np

from app.classification.team_calibration.prototypes import build_prototypes
from app.classification.team_calibration.types import (
    PlayerRole,
    TeamLabel,
    TrackFeature,
    ValidationReport,
)


class CalibrationValidator:
    def __init__(
        self,
        *,
        min_tracks_per_team: int = 3,
        min_samples_per_track: int = 5,
        require_leave_one_out: bool = True,
        min_leave_one_out_accuracy: float = 0.8,
    ) -> None:
        self.min_tracks_per_team = int(min_tracks_per_team)
        self.min_samples_per_track = int(min_samples_per_track)
        self.require_leave_one_out = bool(require_leave_one_out)
        self.min_leave_one_out_accuracy = float(min_leave_one_out_accuracy)

    def eligible_tracks(self, tracks: Iterable[TrackFeature]) -> list[TrackFeature]:
        """Select training tracks without removing their manual labels."""

        return [
            track for track in tracks
            if _is_labelled_track(track)
            and track.observation_count >= self.min_samples_per_track
        ]

    def validate(self, tracks: Iterable[TrackFeature]) -> ValidationReport:
        all_tracks = [track for track in tracks if _is_labelled_track(track)]
        eligible = self.eligible_tracks(all_tracks)
        items = [track for track in all_tracks if track.team in {TeamLabel.HOME, TeamLabel.AWAY}]
        home = [track for track in items if track.team == TeamLabel.HOME and track.role == PlayerRole.OUTFIELD]
        away = [track for track in items if track.team == TeamLabel.AWAY and track.role == PlayerRole.OUTFIELD]
        eligible_home = [
            track for track in eligible
            if track.team == TeamLabel.HOME and track.role == PlayerRole.OUTFIELD
        ]
        eligible_away = [
            track for track in eligible
            if track.team == TeamLabel.AWAY and track.role == PlayerRole.OUTFIELD
        ]
        goalkeepers = [
            track for track in items if track.role == PlayerRole.GOALKEEPER
        ]
        referees = [
            track for track in all_tracks
            if track.team == TeamLabel.NONE and track.role == PlayerRole.REFEREE
        ]
        reasons: list[str] = []
        if len(home) < self.min_tracks_per_team:
            reasons.append("home_outfield_tracks_insufficient")
        if len(away) < self.min_tracks_per_team:
            reasons.append("away_outfield_tracks_insufficient")
        if len(eligible_home) < self.min_tracks_per_team and len(eligible_home) < len(home):
            reasons.append("home_track_samples_insufficient")
        if len(eligible_away) < self.min_tracks_per_team and len(eligible_away) < len(away):
            reasons.append("away_track_samples_insufficient")

        prototypes = build_prototypes(eligible, include_goalkeepers=False)
        home_proto = prototypes.get((TeamLabel.HOME, PlayerRole.OUTFIELD))
        away_proto = prototypes.get((TeamLabel.AWAY, PlayerRole.OUTFIELD))
        separation = _feature_distance(
            home_proto.color_feature if home_proto else None,
            away_proto.color_feature if away_proto else None,
        )
        if home_proto is not None and away_proto is not None and separation <= 1e-6:
            reasons.append("outfield_prototypes_not_separable")

        loo_accuracy = self._leave_one_out(eligible)
        if self.require_leave_one_out and (
            loo_accuracy is None or loo_accuracy < self.min_leave_one_out_accuracy
        ):
            reasons.append("leave_one_track_out_failed")
        prototypes = build_prototypes(eligible)
        goalkeeper_ready = all(
            (team, PlayerRole.GOALKEEPER) in prototypes
            for team in (TeamLabel.HOME, TeamLabel.AWAY)
        )
        referee_ready = (TeamLabel.NONE, PlayerRole.REFEREE) in prototypes
        return ValidationReport(
            passed=not reasons,
            reasons=reasons,
            home_track_count=len(home),
            away_track_count=len(away),
            home_sample_count=sum(track.observation_count for track in home),
            away_sample_count=sum(track.observation_count for track in away),
            home_intra_class_dispersion=home_proto.intra_class_dispersion if home_proto else 0.0,
            away_intra_class_dispersion=away_proto.intra_class_dispersion if away_proto else 0.0,
            inter_class_separation=separation,
            leave_one_track_out_accuracy=loo_accuracy,
            goalkeeper_mapping_ready=goalkeeper_ready,
            referee_mapping_ready=referee_ready,
            goalkeeper_track_count=len(goalkeepers),
            referee_track_count=len(referees),
            referee_sample_count=sum(track.observation_count for track in referees),
            min_samples_per_track=self.min_samples_per_track,
            min_tracks_per_team=self.min_tracks_per_team,
            home_eligible_track_count=len(eligible_home),
            away_eligible_track_count=len(eligible_away),
            excluded_track_ids=sorted(
                track.track_id for track in all_tracks
                if track.observation_count < self.min_samples_per_track
            ),
        )

    def _leave_one_out(self, tracks: list[TrackFeature]) -> float | None:
        outfield = [
            track
            for track in tracks
            if track.role == PlayerRole.OUTFIELD and track.team in {TeamLabel.HOME, TeamLabel.AWAY}
        ]
        if len(outfield) < 2:
            return None
        correct = 0
        evaluated = 0
        for index, held_out in enumerate(outfield):
            remaining = outfield[:index] + outfield[index + 1:]
            prototypes = build_prototypes(remaining, include_goalkeepers=False)
            home = prototypes.get((TeamLabel.HOME, PlayerRole.OUTFIELD))
            away = prototypes.get((TeamLabel.AWAY, PlayerRole.OUTFIELD))
            if home is None or away is None or held_out.color_feature is None:
                continue
            distances = [
                _feature_distance(held_out.color_feature, home.color_feature),
                _feature_distance(held_out.color_feature, away.color_feature),
            ]
            prediction = TeamLabel.HOME if distances[0] <= distances[1] else TeamLabel.AWAY
            correct += prediction == held_out.team
            evaluated += 1
        return float(correct / evaluated) if evaluated else None


def _feature_distance(first: np.ndarray | None, second: np.ndarray | None) -> float:
    if first is None or second is None:
        return 0.0
    return float(np.linalg.norm(first - second))


def _is_labelled_track(track: TrackFeature) -> bool:
    return (
        track.team in {TeamLabel.HOME, TeamLabel.AWAY}
        and track.role in {PlayerRole.OUTFIELD, PlayerRole.GOALKEEPER}
    ) or (track.team == TeamLabel.NONE and track.role == PlayerRole.REFEREE)
