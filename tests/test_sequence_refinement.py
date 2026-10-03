"""Offline sequence boundaries use synthetic geometry, not model accuracy claims."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def geometry():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("supervision")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend_core"))
    from app.config.pitch import build_pitch_profile
    from app.geometry.sequence_refinement import refine_homographies, refine_sequence

    config, _ = build_pitch_profile("source-informed105")
    return np, cv2, config, refine_homographies, refine_sequence


def _records(geometry, count=6, anchors=(5,), shift=2.):
    np = geometry[0]
    result = []
    for index in range(count):
        transform = np.eye(3)
        transform[0, 2] = shift * index
        motion = np.eye(3)
        motion[0, 2] = shift
        anchored = index in anchors
        result.append({
            "frame": index + 1, "motion": None if index == 0 else motion,
            "homography": np.diag([10., 10., 1.]) @ np.linalg.inv(transform) if anchored else None,
            "quality": {"source": "field_lines" if anchored else "unavailable",
                        "accepted": anchored, "supported_segments": 7,
                        "supported_ids": [3, 11, 12, 13, 14, 15, 16],
                        "line_support_fraction": .9, "paint_coverage": .6, "line_error_px": .8},
            "epoch": index // 2, "transition": index % 2 == 0,
            "grass_probes": np.array([[50., 100.], [250., 100.], [50., 220.], [250., 220.]]),
            "dimensions": (320, 240),
        })
    return result


def _states(count=6):
    return [{
        "type": "frame_state", "frame_id": index + 1,
        "capture_timestamp_ms": 100000. + index * 40,
        "processed_timestamp_ms": 100010. + index * 40,
        "processing_fps": 22., "homography_status": "fresh", "geometry_epoch": 99,
        "projection_quality": {"source": "old"},
        "players": [{"track_id": 7, "entity_id": 3, "track_status": "detected",
                     "missing_frames": 0, "role": "outfield", "team": "home", "team_label": "home",
                     "team_id": 0, "confidence": .9, "bbox": [70., 80., 90., 140.],
                     "field_x": 8000., "field_y": 6000., "velocity_x": 900., "velocity_y": 800.}],
        "ball": {"status": "fresh", "age_frames": 0, "image_x": 100., "image_y": 160.,
                 "field_x": 7000., "field_y": 6000., "velocity_x": 400., "velocity_y": 500.,
                 "confidence": .8},
        "possession_track_id": 7, "events": [],
    } for index in range(count)]


def test_future_anchor_corrects_backward_and_forward_geometry(geometry):
    np, _, config, refine, _ = geometry
    records = _records(geometry, anchors=(3,))
    result = refine(records, (240, 320), config)
    assert result.summary["refined_frame_count"] == 6
    for index, matrix in enumerate(result.homographies):
        expected = np.diag([10., 10., 1.])
        expected[0, 2] = -20. * index
        assert np.allclose(matrix, expected)
        assert result.qualities[index]["anchor_frame"] == 4
        assert result.qualities[index]["anchor_chain_length"] == abs(index - 3)
        assert result.qualities[index]["future_anchor"] == (index < 3)
        assert result.qualities[index]["offline"]
    assert result.epochs == [1] * 6


def test_missing_motion_splits_shots_and_prevents_future_anchor_crossing(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry)
    records[3]["motion"] = None
    result = refine(records, (240, 320), config)
    assert result.homographies[:3] == [None] * 3
    assert all(matrix is not None for matrix in result.homographies[3:])
    assert result.epochs == [1, 1, 1, 2, 2, 2]
    assert result.summary["segments"][0]["rejection_reason"] == "no_strong_anchor"


def test_frame_gap_splits_even_with_present_motion(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry)
    for record in records[3:]:
        record["frame"] += 1
    result = refine(records, (240, 320), config)
    assert result.homographies[:3] == [None] * 3
    assert result.epochs == [1, 1, 1, 2, 2, 2]


def test_inconsistent_independent_anchors_reject_whole_shot(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry, anchors=(0, 5))
    records[5]["homography"][0, 2] += 300.
    result = refine(records, (240, 320), config)
    assert result.homographies == [None] * 6
    assert result.summary["segments"][0]["rejection_reason"] == "inconsistent_anchors"
    assert result.summary["segments"][0]["max_anchor_disagreement_cm"] == pytest.approx(300.)


def test_anchor_shortlist_is_bounded_but_every_strong_anchor_is_checked(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry, count=30, anchors=tuple(range(30)), shift=0.)
    records[1]["homography"][0, 2] += 300.
    result = refine(records, (240, 320), config)
    segment = result.summary["segments"][0]
    assert len(segment["shortlist_frames"]) == 12 and 2 not in segment["shortlist_frames"]
    assert segment["strong_anchor_count"] == 30
    assert segment["rejection_reason"] == "inconsistent_anchors"
    assert result.homographies == [None] * 30


@pytest.mark.parametrize("defect", ["weak_support", "coverage", "residual", "missing_fronts",
                                    "few_segments", "model_source", "unaccepted",
                                    "partial_two_fronts", "missing_endline"])
def test_weak_or_model_evidence_cannot_supply_offline_anchor(geometry, defect):
    _, _, config, refine, _ = geometry
    records = _records(geometry)
    quality = records[-1]["quality"]
    updates = {
        "weak_support": {"line_support_fraction": .69}, "coverage": {"paint_coverage": .34},
        "residual": {"line_error_px": 2.01}, "missing_fronts": {"supported_ids": [0, 1, 3, 11, 12, 13]},
        "few_segments": {"supported_segments": 5}, "model_source": {"source": "model"},
        "unaccepted": {"accepted": False},
        "partial_two_fronts": {"supported_ids": [0, 1, 2, 3, 4, 11, 14]},
        "missing_endline": {"supported_ids": [0, 1, 11, 12, 13, 14, 15, 16]},
    }
    quality.update(updates[defect])
    assert refine(records, (240, 320), config).homographies == [None] * 6


def test_complete_nested_topology_on_either_goal_supplies_anchor(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry)
    records[-1]["quality"]["supported_ids"] = [2, 5, 6, 7, 8, 9, 10]
    result = refine(records, (240, 320), config)
    assert result.summary["refined_frame_count"] == 6


def test_biased_partial_rectangles_do_not_conflict_with_complete_anchor(geometry):
    _, _, config, refine, _ = geometry
    records = _records(geometry, anchors=(0, 5))
    records[0]["quality"]["supported_ids"] = [0, 1, 2, 3, 4, 11, 14]
    records[0]["homography"][0, 2] += 582.
    result = refine(records, (240, 320), config)
    assert result.summary["segments"][0]["strong_anchor_count"] == 1
    assert result.summary["segments"][0]["anchor_frame"] == 6
    assert result.summary["refined_frame_count"] == 6
    assert result.summary["segments"][0]["rejection_reason"] is None


def test_anchor_horizon_and_singular_motion_are_rejected(geometry):
    np, _, config, refine, _ = geometry
    records = _records(geometry)
    records[-1]["homography"][2] = [0., .01, -1.5]
    assert refine(records, (240, 320), config).homographies == [None] * 6
    records = _records(geometry)
    records[3]["motion"] = np.zeros((3, 3))
    result = refine(records, (240, 320), config)
    assert result.epochs == [1, 1, 1, 2, 2, 2]


def test_local_paint_correction_propagates_and_retains_original_anchor_guard(geometry):
    np, _, config, refine, _ = geometry
    records = _records(geometry)
    calls = []

    def corrector(index, matrix):
        calls.append(index)
        if index == 3:
            matrix[0, 2] += 20.
        return matrix

    corrector.diagnostics = {3: {"line_error_px": .5, "line_support_fraction": .9}}
    result = refine(records, (240, 320), config, corrector=corrector)
    assert calls == [5, 4, 3, 2, 1, 0]
    assert result.homographies[0][0, 2] == pytest.approx(20.)
    assert result.qualities[3]["local_paint_corrected"]
    assert result.qualities[0]["paint_correction_frame"] == 4
    assert result.qualities[0]["paint_correction_age_frames"] == 3
    assert result.qualities[3]["paint_quality"]["line_error_px"] == .5
    assert result.summary["local_paint_correction_count"] == 1
    assert np.allclose(records[3]["motion"], [[1, 0, 2], [0, 1, 0], [0, 0, 1]])
    conflicting = _records(geometry, anchors=(0, 5))
    conflicting[-1]["homography"][0, 2] += 300
    calls.clear()
    assert refine(conflicting, (240, 320), config, corrector=corrector).homographies == [None] * 6
    assert calls == []


def test_invalid_local_correction_blanks_remaining_transport_leg(geometry):
    _, _, config, refine, _ = geometry
    result = refine(_records(geometry), (240, 320), config,
                    corrector=lambda index, matrix: None if index == 3 else matrix)
    assert result.homographies[:4] == [None] * 4
    assert all(matrix is not None for matrix in result.homographies[4:])


def test_large_paint_correction_splits_epochs_blanks_frame_and_resets_players_ball(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry, shift=0.)

    def corrector(index, matrix):
        if index == 3:
            matrix[0, 2] += 300.
        return matrix

    corrector.diagnostics = {3: {"coordinate_transition": True, "correction_cm": 300.}}
    result = refine(_states(), records, [index * 40. for index in range(6)],
                    config, 25., (240, 320), corrector=corrector)
    assert [state["geometry_epoch"] for state in result.frame_states] == [1, 1, 1, 2, 2, 2]
    assert result.homographies[3] is None
    assert result.homographies[0][0, 2] == pytest.approx(300.)
    transition = result.frame_states[3]
    assert transition["homography_status"] == "unavailable"
    assert transition["projection_quality"]["reason"] == "coordinate_transition"
    for key in ("field_x", "field_y", "velocity_x", "velocity_y"):
        assert transition["players"][0][key] is None and transition["ball"][key] is None
    after = result.frame_states[4]
    assert after["players"][0]["field_x"] == pytest.approx(800.)
    assert after["ball"]["field_x"] == pytest.approx(1000.)
    assert after["players"][0]["velocity_x"] is None and after["ball"]["velocity_x"] is None
    assert result.summary["coordinate_transition_frame_count"] == 1
    assert result.summary["geometry_epoch_count"] == 2
    assert result.summary["refined_frame_count"] == 5


def test_no_anchor_clears_causal_geometry_and_preserves_detection_facts(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry, anchors=())
    original = _states()
    result = refine(original, records, [index * 40. for index in range(6)], config, 25., (240, 320))
    for before, after in zip(original, result.frame_states):
        player = after["players"][0]
        for key in ("field_x", "field_y", "velocity_x", "velocity_y"):
            assert player[key] is None and after["ball"][key] is None
        assert after["homography_status"] == "unavailable"
        assert after["possession_track_id"] is None
        assert after["capture_timestamp_ms"] == before["capture_timestamp_ms"]
        assert after["processed_timestamp_ms"] == before["processed_timestamp_ms"]
        assert after["events"] == before["events"]
        for key in ("bbox", "track_id", "entity_id", "role", "team", "confidence"):
            assert player[key] == before["players"][0][key]
        assert after["projection_quality"]["causal_quality"] == records[after["frame_id"] - 1]["quality"]
    assert original[0]["players"][0]["field_x"] == 8000.


def test_detection_gaps_rebinds_and_native_bounds_are_respected(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry, shift=0.)
    states = _states()
    states[1]["players"] = []
    states[2]["players"][0]["track_status"] = "predicted"
    states[3]["players"][0]["missing_frames"] = 1
    states[4]["players"][0]["bbox"] = [-20., 80., -10., 140.]
    states[5]["players"][0]["track_id"] = 15
    states[5]["players"][0]["bbox"] = [250., 80., 270., 140.]
    result = refine(states, records, [index * 40. for index in range(6)], config, 25., (240, 320))
    assert result.frame_states[1]["players"] == []
    for state in result.frame_states[2:5]:
        assert state["players"][0]["field_x"] is None
    assert result.frame_states[5]["players"][0]["field_x"] == pytest.approx(2600.)
    assert result.frame_states[5]["players"][0]["velocity_x"] is None
    assert result.summary["projected_player_observations"] == 2


def test_velocities_use_adjacent_actual_source_pts_and_ball_uses_same_matrix(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry, count=4, anchors=(3,), shift=0.)
    states = _states(4)
    timestamps = [0., 40., 100., 150.]
    for index, state in enumerate(states):
        state["players"][0]["bbox"] = [70. + index, 80., 90. + index, 140.]
        state["ball"]["image_x"] = 100. + index
    result = refine(states, records, timestamps, config, 25., (240, 320))
    assert result.frame_states[0]["players"][0]["velocity_x"] is None
    for index in range(1, 4):
        previous, current = result.frame_states[index - 1], result.frame_states[index]
        dt = (timestamps[index] - timestamps[index - 1]) / 1000
        assert current["players"][0]["velocity_x"] == pytest.approx(
            (current["players"][0]["field_x"] - previous["players"][0]["field_x"]) / dt)
        assert current["ball"]["field_x"] == pytest.approx((100 + index) * 10.)
        assert current["ball"]["velocity_x"] == pytest.approx(10. / dt)


def test_geometry_epoch_boundary_prevents_cross_shot_velocity(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry, anchors=(0, 5), shift=0.)
    records[3]["motion"] = None
    result = refine(_states(), records, [index * 40. for index in range(6)], config, 25., (240, 320))
    assert result.frame_states[3]["players"][0]["velocity_x"] is None
    assert result.frame_states[3]["ball"]["velocity_x"] is None


def test_sequence_strict_filter_rejects_repeated_player_jumps(geometry):
    _, _, config, _, refine = geometry
    states = _states()
    for state in states[1:5]:
        state["players"][0]["bbox"] = [270., 80., 290., 140.]
    result = refine(states, _records(geometry, shift=0.), [index * 40. for index in range(6)],
                    config, 25., (240, 320))
    for state in result.frame_states[1:5]:
        assert state["players"][0]["field_x"] is None
    assert result.frame_states[5]["players"][0]["field_x"] == pytest.approx(800.)
    assert result.frame_states[5]["players"][0]["velocity_x"] is None


def test_unrelated_entity_rebind_preserves_moving_player_filter_and_outlier_gate(geometry):
    _, _, config, _, refine = geometry
    control = _states(30)
    for index, state in enumerate(control):
        offset = index + (40 if index == 20 else 0)
        state["players"][0]["bbox"] = [70. + offset, 80., 90. + offset, 140.]
    with_rebind = deepcopy(control)
    for index, state in enumerate(with_rebind):
        state["players"].append({
            **state["players"][0], "entity_id": 5, "track_id": 15 if index < 20 else 16,
            "bbox": [200., 60., 220., 140.],
        })
    records = _records(geometry, count=30, anchors=(29,), shift=0.)
    timestamps = [index * 40. for index in range(30)]
    expected = refine(control, records, timestamps, config, 25., (240, 320))
    actual = refine(with_rebind, records, timestamps, config, 25., (240, 320))
    for expected_state, actual_state in zip(expected.frame_states, actual.frame_states):
        expected_player, actual_player = expected_state["players"][0], actual_state["players"][0]
        for field in ("field_x", "field_y", "velocity_x", "velocity_y"):
            assert actual_player[field] == expected_player[field]
    assert actual.frame_states[20]["players"][0]["field_x"] is None
    rebound = actual.frame_states[20]["players"][1]
    assert rebound["field_x"] == pytest.approx(2100.)
    assert rebound["velocity_x"] is None


def test_event_identity_and_evidence_survive_cleared_geometry(geometry):
    _, _, config, _, refine = geometry
    states = _states()
    event = {"id": "review-event", "event_type": "foul_candidate", "confidence": .7,
             "severity": "review", "timestamp": 100., "frame_id": 1,
             "field_x": 8000., "field_y": 6000., "evidence": {"source": "actual_model"}}
    states[0]["events"] = [event]
    result = refine(states, _records(geometry, anchors=()), [index * 40. for index in range(6)],
                    config, 25., (240, 320))
    after = result.frame_states[0]["events"][0]
    assert after == {**event, "field_x": None, "field_y": None}
    assert states[0]["events"][0] == event


def test_sequence_validates_frame_and_timestamp_alignment(geometry):
    _, _, config, _, refine = geometry
    records = _records(geometry)
    with pytest.raises(ValueError, match="align"):
        refine(_states(5), records, list(range(6)), config, 25., (240, 320))
    states = _states()
    states[0]["frame_id"] = 2
    with pytest.raises(ValueError, match="identities"):
        refine(states, records, list(range(6)), config, 25., (240, 320))
    with pytest.raises(ValueError, match="timestamps"):
        refine(_states(), records, [0.] * 6, config, 25., (240, 320))


def test_runner_atomic_refinement_preserves_causal_bytes_and_original_media(geometry, tmp_path, monkeypatch):
    np, cv2, config, _, _ = geometry
    from app.geometry import pitch_registration
    from tools.run_night_ablation import _offline_refine

    monkeypatch.setattr(pitch_registration, "prepare_offline_corrector",
                        lambda *args: lambda index, matrix: matrix, raising=False)
    original = tmp_path / "original.mp4"
    writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*"mp4v"), 25., (320, 240))
    assert writer.isOpened()
    for index in range(6):
        writer.write(np.full((240, 320, 3), (20, 80 + index, 20), dtype=np.uint8))
    writer.release()
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    states = _states()
    metrics = [{"frame_id": index + 1, "source_pts_ms": index * 40.,
                "homography_status": "fresh", "synchronized_pipeline_latency_ms": 12.}
               for index in range(6)]
    state_bytes = "".join(json.dumps(state) + "\n" for state in states).encode()
    metric_bytes = "".join(json.dumps(metric) + "\n" for metric in metrics).encode()
    (tmp_path / "frame-states.jsonl").write_bytes(state_bytes)
    (tmp_path / "frame-metrics.jsonl").write_bytes(metric_bytes)
    raw = tmp_path / "annotated-raw.mp4"
    raw.write_bytes(b"previous causal video")
    result = _offline_refine(tmp_path, original, raw, _records(geometry, shift=0.),
                             {"height": 240, "width": 320, "decoded_timestamps_ms": [index * 40. for index in range(6)]},
                             config, 25.)
    assert (tmp_path / "causal-frame-states.jsonl").read_bytes() == state_bytes
    assert (tmp_path / "causal-frame-metrics.jsonl").read_bytes() == metric_bytes
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    rendered = cv2.VideoCapture(str(raw))
    count = 0
    while rendered.read()[0]:
        count += 1
    rendered.release()
    assert count == 6 and result.summary["refined_frame_count"] == 6
    refined_metrics = [json.loads(line) for line in (tmp_path / "frame-metrics.jsonl").read_text().splitlines()]
    assert all(metric["projection_quality"]["offline"] for metric in refined_metrics)
    assert all(metric["synchronized_pipeline_latency_ms"] == 12. for metric in refined_metrics)
    homographies = [json.loads(line) for line in (tmp_path / "sequence-homographies.jsonl").read_text().splitlines()]
    assert len(homographies) == 6
    assert homographies[0]["frame_id"] == 1 and homographies[0]["source_pts_ms"] == 0.
    assert np.allclose(homographies[0]["homography"], np.diag([10., 10., 1.]))
    assert homographies[0]["quality"]["offline"] and homographies[0]["epoch"] == 1
    history = [json.loads(line) for line in (tmp_path / "causal-registration-history.jsonl").read_text().splitlines()]
    assert len(history) == 6
    assert history[0]["frame"] == 1 and history[0]["motion"] is None
    assert history[0]["dimensions"] == [320, 240]
    assert np.allclose(history[-1]["homography"], np.diag([10., 10., 1.]))
    assert np.allclose(history[-1]["grass_probes"], _records(geometry)[-1]["grass_probes"])
    assert not list(tmp_path.glob(".sequence-*"))
    current_states = result.frame_states
    current_states[0]["players"][0]["role"] = "unknown"
    (tmp_path / "frame-states.jsonl").write_text("".join(json.dumps(state) + "\n" for state in current_states))
    repeated = _offline_refine(
        tmp_path, original, raw, _records(geometry, shift=0.),
        {"height": 240, "width": 320, "decoded_timestamps_ms": [index * 40. for index in range(6)]},
        config, 25., reuse_causal=True,
    )
    assert repeated.frame_states[0]["players"][0]["role"] == "outfield"
    assert (tmp_path / "causal-frame-states.jsonl").read_bytes() == state_bytes
    assert (tmp_path / "causal-frame-metrics.jsonl").read_bytes() == metric_bytes
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest


def test_atomic_publish_failure_keeps_original_artifact(geometry, tmp_path, monkeypatch):
    from tools import run_night_ablation

    original = tmp_path / "states.jsonl"
    original.write_bytes(b"original complete record\n")
    with monkeypatch.context() as context:
        context.setattr(run_night_ablation.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("fixture failure")))
        with pytest.raises(OSError, match="fixture failure"):
            run_night_ablation._atomic_bytes(original, b"new complete record\n")
    assert original.read_bytes() == b"original complete record\n"
    assert not list(tmp_path.glob(".sequence-*"))
