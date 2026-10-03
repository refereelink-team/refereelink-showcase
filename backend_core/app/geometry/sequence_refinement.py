"""Offline pitch registration anchored by independently validated field paint.

Future frames may supply an anchor for the same continuous camera shot. This
is explicit offline evidence, never a causal or live-model measurement.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from app.geometry.position_filter import PlayerPositionFilter


MAX_ANCHOR_SHORTLIST = 12
MAX_ANCHOR_DISAGREEMENT_CM = 200.0


@dataclass
class SequenceGeometry:
    homographies: list[np.ndarray | None]
    qualities: list[dict]
    epochs: list[int]
    summary: dict


@dataclass
class SequenceRefinementResult:
    frame_states: list[dict]
    homographies: list[np.ndarray | None]
    summary: dict


def _points(points, matrix):
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    values = np.c_[points, np.ones(len(points))] @ matrix.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return values[:, :2] / values[:, 2:]


def _normalise(matrix):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return None
    scale = matrix[2, 2] if abs(matrix[2, 2]) > 1e-10 else np.linalg.norm(matrix)
    if abs(scale) < 1e-12:
        return None
    return matrix / scale


def _probes(record, frame_shape):
    supplied = record.get("grass_probes")
    if supplied is not None:
        points = np.asarray(supplied, dtype=float).reshape(-1, 2)
        points = points[np.isfinite(points).all(axis=1)]
        if len(points):
            return points[:: max(1, int(np.ceil(len(points) / 200)))]
    height, width = frame_shape[:2]
    return np.array(
        [
            [x, y]
            for y in np.linspace(height * 0.4, height * 0.9, 5)
            for x in np.linspace(width * 0.1, width * 0.9, 9)
        ]
    )


def _valid_motion(matrix, frame_shape):
    try:
        matrix = _normalise(matrix)
        if matrix is None:
            return None
        height, width = frame_shape[:2]
        units = np.diag([width, height, 1.0])
        if np.linalg.cond(np.linalg.inv(units) @ matrix @ units) > 1e4:
            return None
        np.linalg.inv(matrix)
        return matrix
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return None


def _valid_homography(matrix, record, frame_shape, config):
    try:
        matrix = _normalise(matrix)
        if matrix is None:
            return None
        height, width = frame_shape[:2]
        image_units = np.diag([width, height, 1.0])
        field_units = np.diag([1.0 / config.length, 1.0 / config.width, 1.0])
        if np.linalg.cond(field_units @ matrix @ image_units) > 1e6:
            return None
        probes = _probes(record, frame_shape)
        denominator = np.c_[probes, np.ones(len(probes))] @ matrix[2]
        if (
            not np.isfinite(denominator).all()
            or np.min(np.abs(denominator)) < max(1e-10, 0.01 * np.max(np.abs(denominator)))
            or np.any(denominator > 0)
            and np.any(denominator < 0)
            or not np.isfinite(_points(probes, matrix)).all()
        ):
            return None
        np.linalg.inv(matrix)
        return matrix
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return None


def _strong_anchor(record):
    quality = record.get("quality") or {}
    try:
        supported = set(quality.get("supported_ids", []))
        metrics = [
            quality.get("line_support_fraction", quality.get("line_support", 0)),
            quality.get("paint_coverage", 0),
            quality.get("line_error_px", quality.get("line_error", float("inf"))),
        ]
        return bool(
            record.get("homography") is not None
            and quality.get("source") == "field_lines"
            and quality.get("accepted") is True
            and quality.get("supported_segments", 0) >= 6
            # Both complete nested rectangles and their shared endline must
            # agree. Two rectangle fronts alone cannot disambiguate geometry.
            and ({3, 11, 12, 13, 14, 15, 16} <= supported or {2, 5, 6, 7, 8, 9, 10} <= supported)
            and np.isfinite(metrics).all()
            and metrics[0] >= 0.7
            and metrics[1] >= 0.35
            and metrics[2] <= 2.0
        )
    except (TypeError, ValueError):
        return False


def _segments(records, frame_shape):
    segments = []
    for index, record in enumerate(records):
        motion = _valid_motion(record.get("motion"), frame_shape) if index else None
        continuous = (
            index > 0 and record["frame"] == records[index - 1]["frame"] + 1 and motion is not None
        )
        if not continuous:
            segments.append([])
        segments[-1].append((index, motion))
    return segments


def refine_homographies(
    records: Sequence[dict], frame_shape, config, corrector=None
) -> SequenceGeometry:
    """Resolve each shot from a bounded anchor medoid and validated motion chain.

    All strong anchors, including those outside the medoid shortlist, must
    agree at the reference image. A contradictory shot remains unavailable.
    """
    homographies = [None] * len(records)
    qualities = [{} for _ in records]
    epochs = [0] * len(records)
    summaries = []
    for epoch, segment in enumerate(_segments(records, frame_shape), start=1):
        indices = [index for index, _ in segment]
        reference = records[indices[0]]
        prefixes = [np.eye(3)]
        for _, motion in segment[1:]:
            prefixes.append(_normalise(motion @ prefixes[-1]) if prefixes[-1] is not None else None)
        candidates = []
        invalid_anchor_transport = 0
        for local, index in enumerate(indices):
            record = records[index]
            if not _strong_anchor(record):
                continue
            accepted = _valid_homography(record["homography"], record, frame_shape, config)
            common = (
                _valid_homography(accepted @ prefixes[local], reference, frame_shape, config)
                if accepted is not None and prefixes[local] is not None
                else None
            )
            if common is not None:
                candidates.append((local, common))
            elif accepted is not None:
                invalid_anchor_transport += 1
        shortlist = []
        if candidates:
            chosen = np.unique(
                np.linspace(
                    0, len(candidates) - 1, min(MAX_ANCHOR_SHORTLIST, len(candidates))
                ).astype(int)
            )
            shortlist = [candidates[item] for item in chosen]
        rejection = "no_strong_anchor" if not shortlist else None
        if invalid_anchor_transport:
            rejection = "invalid_anchor_transport"
        anchor_local, reference_h, disagreement = None, None, None
        if shortlist:
            probes = _probes(reference, frame_shape)
            maps = np.array([_points(probes, matrix) for _, matrix in shortlist])
            distances = np.median(np.linalg.norm(maps[:, None] - maps[None, :], axis=3), axis=2)
            medoid = int(np.argmin(np.median(distances, axis=1)))
            anchor_local, reference_h = shortlist[medoid]
            all_distances = [
                float(np.median(np.linalg.norm(_points(probes, matrix) - maps[medoid], axis=1)))
                for _, matrix in candidates
            ]
            disagreement = max(all_distances)
            if disagreement > MAX_ANCHOR_DISAGREEMENT_CM:
                rejection = "inconsistent_anchors"
                reference_h = None
        if rejection is not None:
            reference_h = None
        anchor_frame = records[indices[anchor_local]]["frame"] if anchor_local is not None else None
        transported = [None] * len(indices)
        corrections = [{} for _ in indices]

        def correct(local, matrix, previous_correction=None):
            index = indices[local]
            before = _normalise(matrix)
            if before is None:
                return None, previous_correction
            candidate = corrector(index, before.copy()) if corrector is not None else before
            accepted = _valid_homography(candidate, records[index], frame_shape, config)
            changed = accepted is not None and not np.allclose(
                accepted, before, rtol=1e-8, atol=1e-8
            )
            correction_frame = records[index]["frame"] if changed else previous_correction
            corrections[local] = {
                "local_paint_corrected": bool(changed),
                "paint_correction_frame": correction_frame,
                "paint_correction_age_frames": (
                    abs(records[index]["frame"] - correction_frame)
                    if correction_frame is not None
                    else None
                ),
            }
            diagnostics = (
                getattr(corrector, "diagnostics", {}).get(index) if corrector is not None else None
            )
            if diagnostics is not None:
                corrections[local]["paint_quality"] = deepcopy(diagnostics)
                corrections[local]["coordinate_transition"] = bool(
                    diagnostics.get("coordinate_transition", False)
                )
            return accepted, correction_frame

        if reference_h is not None:
            anchor_matrix = reference_h @ np.linalg.inv(prefixes[anchor_local])
            transported[anchor_local], anchor_correction = correct(anchor_local, anchor_matrix)
            last_correction = anchor_correction
            for local in range(anchor_local - 1, -1, -1):
                if transported[local + 1] is None:
                    break
                candidate = transported[local + 1] @ segment[local + 1][1]
                transported[local], last_correction = correct(local, candidate, last_correction)
            last_correction = anchor_correction
            for local in range(anchor_local + 1, len(indices)):
                if transported[local - 1] is None:
                    break
                candidate = transported[local - 1] @ np.linalg.inv(segment[local][1])
                transported[local], last_correction = correct(local, candidate, last_correction)
        refined = 0
        for local, index in enumerate(indices):
            epochs[index] = epoch
            matrix = transported[local]
            coordinate_transition = bool(corrections[local].get("coordinate_transition"))
            reason = (
                "coordinate_transition"
                if coordinate_transition
                else rejection or ("invalid_transport" if matrix is None else None)
            )
            # Keep the corrected matrix in the internal propagation chain, but
            # never present a coordinate-system correction as player motion.
            if coordinate_transition:
                matrix = None
            qualities[index] = {
                "source": "sequence_refined" if matrix is not None else "unavailable",
                "offline": True,
                "anchor_frame": anchor_frame,
                "anchor_source": "field_lines" if anchor_frame is not None else None,
                "anchor_chain_length": abs(local - anchor_local)
                if anchor_local is not None
                else None,
                "future_anchor": anchor_frame is not None
                and anchor_frame > records[index]["frame"],
                "reason": reason,
                "geometry_epoch": epoch,
                "causal_quality": deepcopy(records[index].get("quality") or {}),
                **corrections[local],
            }
            if anchor_local is not None:
                qualities[index]["anchor_quality"] = deepcopy(
                    records[indices[anchor_local]].get("quality") or {}
                )
            homographies[index] = matrix
            refined += int(matrix is not None)
        summaries.append(
            {
                "geometry_epoch": epoch,
                "start_frame": reference["frame"],
                "end_frame": records[indices[-1]]["frame"],
                "strong_anchor_count": len(candidates),
                "shortlist_frames": [records[indices[local]]["frame"] for local, _ in shortlist],
                "anchor_frame": anchor_frame,
                "max_anchor_disagreement_cm": disagreement,
                "rejection_reason": rejection,
                "refined_frame_count": refined,
            }
        )
    # Camera boundaries and correction boundaries receive monotonically
    # increasing output epochs in source order, including backward refinement.
    epoch = 0
    prior_segment = None
    for index, segment_epoch in enumerate(epochs):
        if segment_epoch != prior_segment or qualities[index].get("coordinate_transition", False):
            epoch += 1
        epochs[index] = epoch
        qualities[index]["geometry_epoch"] = epoch
        prior_segment = segment_epoch
    for segment in summaries:
        indices = [
            index
            for index, record in enumerate(records)
            if segment["start_frame"] <= record["frame"] <= segment["end_frame"]
        ]
        segment["geometry_epochs"] = sorted({epochs[index] for index in indices})
        segment["geometry_epoch"] = segment["geometry_epochs"][0]
        segment["refined_frame_count"] = sum(homographies[index] is not None for index in indices)
    refined = sum(matrix is not None for matrix in homographies)
    return SequenceGeometry(
        homographies,
        qualities,
        epochs,
        {
            "enabled": True,
            "offline": True,
            "segments": summaries,
            "refined_frame_count": refined,
            "unavailable_frame_count": len(records) - refined,
            "causal_available_frame_count": sum(
                record.get("homography") is not None for record in records
            ),
            "anchor_consistency_limit_cm": MAX_ANCHOR_DISAGREEMENT_CM,
            "local_paint_correction_count": sum(
                bool(quality.get("local_paint_corrected")) for quality in qualities
            ),
            "coordinate_transition_frame_count": sum(
                bool(quality.get("coordinate_transition")) for quality in qualities
            ),
            "geometry_epoch_count": len(set(epochs)),
        },
    )


def refine_sequence(
    frame_states: Sequence[dict],
    records: Sequence[dict],
    source_pts_ms,
    config,
    fps: float,
    frame_shape,
    corrector=None,
) -> SequenceRefinementResult:
    """Reproject actual detections while preserving their nongeometric facts."""
    if len(frame_states) != len(records) or len(records) != len(source_pts_ms):
        raise ValueError("Sequence records, states, and source timestamps must align")
    if any(state["frame_id"] != record["frame"] for state, record in zip(frame_states, records)):
        raise ValueError("Sequence frame identities do not align")
    timestamps = np.asarray(source_pts_ms, dtype=float)
    if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
        raise ValueError("Sequence source timestamps must increase")
    geometry = refine_homographies(records, frame_shape, config, corrector=corrector)
    output = deepcopy(list(frame_states))
    filter_ = PlayerPositionFilter(fps=fps, strict_outliers=True)
    prior_positions = {}
    prior_tracks = {}
    filter_keys = {}
    prior_epoch = None
    prior_ball = None
    projected = 0
    for index, state in enumerate(output):
        epoch, matrix = geometry.epochs[index], geometry.homographies[index]
        if epoch != prior_epoch or matrix is None:
            filter_.reset()
            prior_positions.clear()
            prior_tracks.clear()
            prior_ball = None
        state["geometry_epoch"] = epoch
        state["projection_quality"] = geometry.qualities[index]
        state["homography_status"] = (
            "fresh"
            if matrix is not None and state["frame_id"] == geometry.qualities[index]["anchor_frame"]
            else "reused"
            if matrix is not None
            else "unavailable"
        )
        players = state.get("players", [])
        keys, measurements, heights, detected_indices = [], [], [], []
        for player_index, player in enumerate(players):
            player["field_x"] = player["field_y"] = None
            player["velocity_x"] = player["velocity_y"] = None
            bbox = player.get("bbox")
            if (
                matrix is None
                or bbox is None
                or player.get("track_status", "detected") != "detected"
                or player.get("missing_frames", 0) != 0
            ):
                continue
            entity = player.get("entity_id")
            identity = ("entity", entity) if entity is not None else ("track", player["track_id"])
            key = filter_keys.setdefault(identity, len(filter_keys) + 1)
            if key in prior_tracks and prior_tracks[key] != player["track_id"]:
                filter_.reset_key(key)
                prior_positions.pop(key, None)
            prior_tracks[key] = player["track_id"]
            x1, y1, x2, y2 = bbox
            point = _points([[(x1 + x2) / 2, y2]], matrix)[0]
            if (
                not np.isfinite(point).all()
                or not 0 <= point[0] <= config.length
                or not 0 <= point[1] <= config.width
            ):
                continue
            keys.append(key)
            measurements.append(point)
            heights.append(max(1.0, y2 - y1))
            detected_indices.append(player_index)
        if measurements:
            positions = filter_.update(keys, np.array(measurements), state["frame_id"], heights)
            for player_index, key, point in zip(detected_indices, keys, positions):
                if (
                    not np.isfinite(point).all()
                    or not 0 <= point[0] <= config.length
                    or not 0 <= point[1] <= config.width
                ):
                    prior_positions.pop(key, None)
                    continue
                player = players[player_index]
                player["field_x"], player["field_y"] = map(float, point)
                projected += 1
                previous = prior_positions.get(key)
                if previous is not None and previous[0] == index - 1:
                    dt = (timestamps[index] - timestamps[index - 1]) / 1000.0
                    velocity = (point - previous[1]) / dt
                    if np.linalg.norm(velocity) <= filter_.max_speed:
                        player["velocity_x"], player["velocity_y"] = map(float, velocity)
                prior_positions[key] = (index, point.copy())
        ball = state.get("ball")
        if ball is not None:
            ball["field_x"] = ball["field_y"] = None
            ball["velocity_x"] = ball["velocity_y"] = None
            if (
                matrix is not None
                and ball.get("status") == "fresh"
                and ball.get("age_frames", 0) == 0
                and ball.get("image_x") is not None
                and ball.get("image_y") is not None
            ):
                point = _points([[ball["image_x"], ball["image_y"]]], matrix)[0]
                if (
                    np.isfinite(point).all()
                    and 0 <= point[0] <= config.length
                    and 0 <= point[1] <= config.width
                ):
                    ball["field_x"], ball["field_y"] = map(float, point)
                    if prior_ball is not None and prior_ball[0] == index - 1:
                        velocity = (point - prior_ball[1]) / (
                            (timestamps[index] - timestamps[index - 1]) / 1000.0
                        )
                        if np.linalg.norm(velocity) <= 6000:
                            ball["velocity_x"], ball["velocity_y"] = map(float, velocity)
                    prior_ball = (index, point.copy())
            if ball["field_x"] is None:
                prior_ball = None
        else:
            prior_ball = None
        state["possession_track_id"] = None
        for event in state.get("events", []):
            if "field_x" in event:
                event["field_x"] = None
            if "field_y" in event:
                event["field_y"] = None
        prior_epoch = epoch
    summary = {**geometry.summary, "projected_player_observations": projected}
    return SequenceRefinementResult(output, geometry.homographies, summary)
