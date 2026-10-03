"""Registration integration boundaries; fixtures are not model accuracy evidence."""

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from showcase.tracking import _frame
from test_native_tracking import tracking as tracking


@pytest.fixture
def vision_types():
    """Load scientific dependencies only for remote projection tests."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    sv = pytest.importorskip("supervision")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend_core"))
    from app.geometry.pitch_projection import PitchProjectionResult
    from app.geometry.position_filter import PlayerPositionFilter
    from app.vision import core

    return SimpleNamespace(
        np=np, sv=sv, core=core, projection=PitchProjectionResult,
        filter=PlayerPositionFilter,
    )


def _core(vision_types, monkeypatch, result, *, rebindings=None, paint_enabled=True):
    np, sv, core_module = vision_types.np, vision_types.sv, vision_types.core
    calls = []
    controller = SimpleNamespace(
        update=lambda *args: calls.append(args) or result,
    )
    monkeypatch.setattr(core_module, "FieldLineRegistration", lambda *args: controller)
    detections = sv.Detections(
        xyxy=np.array([[20, 40, 40, 100]], dtype=np.float32),
        confidence=np.array([0.9], dtype=np.float32),
        class_id=np.array([0]),
        tracker_id=np.array([7]),
    )
    entity_update = SimpleNamespace(
        entity_ids={7: 3}, statuses={7: "detected"},
        rebindings=rebindings or {}, fragmentations=0,
    )
    core = core_module.VisionCore(
        fps=30, enable_undistortion=False, camera_calibration_path=None,
        enable_paint_projection=paint_enabled,
        tracker=SimpleNamespace(update_with_detections=lambda value: value),
        entity_manager=SimpleNamespace(update=lambda *args: entity_update),
        camera_motion_estimator=SimpleNamespace(
            measure=lambda frame: None, mark_reference=lambda frame: None,
        ),
    )
    projection = vision_types.projection(
        [], [], np.diag([10.0, 10.0, 1.0]), "fresh", 0.0,
    )
    monkeypatch.setattr(core, "_predict_player", lambda frame: detections)
    monkeypatch.setattr(core, "_projection_for_frame", lambda *args, **kwargs: projection)
    return core, calls, projection


def _registration(vision_types, *, available=True, transition=False, epoch=2):
    return SimpleNamespace(
        homography=vision_types.np.diag([2.0, 2.0, 1.0]) if available else None,
        status="fresh" if available else "unavailable",
        coordinate_transition=transition,
        quality={"reason": "validated" if available else "insufficient_lines",
                 "line_support": 0.91 if available else 0.0},
        epoch=epoch,
    )


def test_registration_is_explicitly_enabled(vision_types, monkeypatch):
    created = []
    monkeypatch.setattr(
        vision_types.core, "FieldLineRegistration",
        lambda *args: created.append(args) or object(),
    )
    disabled = vision_types.core.VisionCore(enable_pitch=False, enable_paint_projection=True)
    legacy = vision_types.core.VisionCore(enable_paint_projection=False)
    enabled = vision_types.core.VisionCore(enable_paint_projection=True)
    assert disabled._paint_registration is None and legacy._paint_registration is None
    assert len(created) == 1 and enabled._paint_registration is not None
    assert enabled._paint_filter.strict_outliers
    assert not legacy._position_filter.strict_outliers


def test_valid_registration_replaces_model_coordinates_and_preserves_seed(vision_types, monkeypatch):
    np = vision_types.np
    registration = _registration(vision_types)
    core, calls, seed = _core(vision_types, monkeypatch, registration)
    frame = np.zeros((120, 180, 3), dtype=np.uint8)
    result = core.process(frame, 0)
    assert np.allclose(result.field_xy, [[60.0, 200.0]])
    assert result.projection.fit_source == "paint"
    assert result.projection.projection_quality == registration.quality
    assert result.projection.geometry_epoch == 2
    assert np.array_equal(seed.homography, np.diag([10.0, 10.0, 1.0]))
    assert calls[0][3] == 0 and np.array_equal(calls[0][1], seed.homography)


def test_disabled_registration_keeps_legacy_model_control(vision_types, monkeypatch):
    np = vision_types.np
    core, calls, _ = _core(
        vision_types, monkeypatch, _registration(vision_types, available=False),
        paint_enabled=False,
    )
    result = core.process(np.zeros((120, 180, 3), dtype=np.uint8), 0)
    assert np.allclose(result.field_xy, [[300.0, 1000.0]])
    assert calls == [] and result.projection.fit_source == "model"
    assert result.projection.projection_quality is None
    assert result.projection.geometry_epoch is None


def test_unavailable_registration_never_leaks_model_coordinates(vision_types, monkeypatch):
    np = vision_types.np
    core, _, _ = _core(vision_types, monkeypatch, _registration(vision_types, available=False))
    result = core.process(np.zeros((120, 180, 3), dtype=np.uint8), 0)
    assert np.isnan(result.field_xy).all()
    assert result.projection.homography is None
    assert result.projection.homography_status == "unavailable"
    assert result.projection.projection_quality["reason"] == "insufficient_lines"
    assert core.homography_available_count == 0


def test_geometry_transition_blanks_frame_and_resets_motion_history(vision_types, monkeypatch):
    np = vision_types.np
    core, _, _ = _core(vision_types, monkeypatch, _registration(vision_types, transition=True))
    core._paint_filter.update([3], np.array([[5000.0, 5000.0]]), 0)
    result = core.process(np.zeros((120, 180, 3), dtype=np.uint8), 1)
    assert np.isnan(result.field_xy).all()
    assert result.projection.coordinate_transition
    assert core._paint_filter.active_keys == set()
    assert core.paint_coordinate_transitions == 1
    assert result.projection.geometry_epoch == 2


def test_entity_rebind_resets_strict_filter_explicitly(vision_types, monkeypatch):
    np = vision_types.np
    core, _, _ = _core(
        vision_types, monkeypatch, _registration(vision_types), rebindings={7: 6},
    )
    core._paint_filter.update([3], np.array([[5000.0, 5000.0]]), 0)
    core._paint_filter.update([99], np.array([[1200.0, 1500.0]]), 0)
    unrelated = core._paint_filter._tracks[99].state.copy()
    result = core.process(np.zeros((120, 180, 3), dtype=np.uint8), 1)
    assert np.allclose(result.field_xy, [[60.0, 200.0]])
    assert result.rebindings == {7: 6}
    assert np.array_equal(core._paint_filter._tracks[99].state, unrelated)


def test_strict_filter_never_teleports_after_persistent_outliers(vision_types):
    np = vision_types.np
    filter_ = vision_types.filter(fps=30, strict_outliers=True)
    initial = np.array([[1000.0, 1000.0]])
    assert np.array_equal(filter_.update([3], initial, 0), initial)
    covariance = filter_._tracks[3].covariance.copy()
    for index in range(1, 31):
        assert np.isnan(filter_.update([3], np.array([[7000.0, 6000.0]]), index)).all()
    assert filter_.outliers_rejected == 30 and filter_.resets == 0
    assert np.array_equal(filter_._tracks[3].covariance, covariance)
    assert np.array_equal(filter_.update([3], initial, 31), initial)
    filter_.reset()
    assert np.array_equal(
        filter_.update([3], np.array([[7000.0, 6000.0]]), 32), [[7000.0, 6000.0]],
    )


def test_legacy_filter_retains_original_persistent_jump_behavior(vision_types):
    np = vision_types.np
    filter_ = vision_types.filter(fps=30)
    filter_.update([3], np.array([[1000.0, 1000.0]]), 0)
    for index in range(1, 4):
        result = filter_.update([3], np.array([[7000.0, 6000.0]]), index)
    assert np.array_equal(result, [[7000.0, 6000.0]])
    assert filter_.resets == 1


def test_strict_filter_recovers_moving_player_after_persistent_outliers(vision_types):
    np = vision_types.np
    filter_ = vision_types.filter(fps=30, strict_outliers=True)
    start = np.array([[1000.0, 1000.0]])
    motion_per_frame = np.array([[10.0, 4.0]])
    for index in range(30):
        assert np.isfinite(filter_.update([3], start + motion_per_frame * index, index)).all()
    track = filter_._tracks[3]
    assert np.linalg.norm(track.state[2:]) > 250
    accepted_state = track.state.copy()
    accepted_covariance = track.covariance.copy()
    for index in range(30, 60):
        assert np.isnan(filter_.update([3], np.array([[7000.0, 6000.0]]), index)).all()
        assert filter_._tracks[3] is track
        assert track.last_frame == 29 and track.last_observed_frame == index
        assert np.array_equal(track.state, accepted_state)
        assert np.array_equal(track.covariance, accepted_covariance)
    expected = start + motion_per_frame * 60
    recovered = filter_.update([3], expected, 60)
    assert np.isfinite(recovered).all()
    assert np.linalg.norm(recovered - expected) < 25
    assert track.last_frame == 60 and track.last_observed_frame == 60
    assert track.outlier_streak == 0 and filter_.resets == 0


def test_raw_frame_state_retains_registration_metadata(vision_types):
    from app.state.models import FrameState

    quality = {"reason": "validated", "line_support": 0.91}
    state = FrameState(frame_id=1, projection_quality=quality, geometry_epoch=4)
    record = json.loads(state.model_dump_json())
    assert record["projection_quality"] == quality and record["geometry_epoch"] == 4
    restored = FrameState.model_validate(record)
    assert restored.projection_quality == quality and restored.geometry_epoch == 4


def test_pipeline_publishes_registration_metadata(vision_types):
    from app.pipeline.engine import InferencePipeline

    np, sv = vision_types.np, vision_types.sv
    quality = {"source": "unavailable", "observed_lines": 1}
    projection = vision_types.projection(
        [], [], None, "unavailable", None,
        projection_quality=quality, geometry_epoch=3,
    )
    vision = vision_types.core.VisionFrame(
        undistorted_frame=np.zeros((120, 180, 3), dtype=np.uint8),
        detections=sv.Detections.empty(), tracked_detections=sv.Detections.empty(),
        projection=projection, field_xy=np.empty((0, 2)),
        color_lookup=np.empty(0), person_only=True,
    )
    pipeline = object.__new__(InferencePipeline)
    pipeline._vision_core = SimpleNamespace(process=lambda *args: vision)
    pipeline._source = SimpleNamespace(frame_count=1)
    pipeline._store = SimpleNamespace(publish_raw_frame=lambda frame: None)
    pipeline._metrics_start = time.monotonic()
    pipeline._metrics_frames = 0
    pipeline._update_semantics = lambda **kwargs: {}
    state = pipeline._process_frame(vision.undistorted_frame, 1000.0)
    assert state.projection_quality == quality and state.geometry_epoch == 3
    assert state.homography_status.value == "unavailable" and state.players == []


def test_tracking_api_preserves_quality_epoch_and_only_converts_world_units(tracking):
    client, _, directory, _, _, frames = tracking
    quality = {"reason": "validated", "line_support": 0.91, "residual_px": 2.7}
    for frame in frames:
        frame["projection_quality"] = quality
        frame["geometry_epoch"] = 4
    (directory / "frame-states.jsonl").write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames)
    )
    result_id = "prepared-calibration-projection_only-frame-states"
    response = client.get(f"/api/tracking/results/{result_id}/frames?limit=1")
    assert response.status_code == 200
    frame = response.json()["frames"][0]
    assert frame["projection_quality"] == quality and frame["geometry_epoch"] == 4
    assert frame["players"][0]["field_x"] == 12.34
    assert frame["players"][0]["bbox"] == [10, 20, 30, 80]


def test_legacy_tracking_records_have_unknown_registration_metadata():
    record = {"frame_id": 1, "homography_status": "unavailable", "players": []}
    result = _frame((json.dumps(record) + "\n").encode(), 1)
    assert result["projection_quality"] is None and result["geometry_epoch"] is None


def test_tracking_rejects_negative_geometry_epoch():
    record = {"frame_id": 1, "homography_status": "fresh", "players": [],
              "geometry_epoch": -1}
    with pytest.raises(ValueError):
        _frame((json.dumps(record) + "\n").encode(), 1)
