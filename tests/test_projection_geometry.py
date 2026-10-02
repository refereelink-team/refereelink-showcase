# ruff: noqa: E402
from typing import Dict, Iterable, Tuple

import sys
from pathlib import Path
import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
sv = pytest.importorskip("supervision")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend_core"))

from app.geometry.pitch_projection import (
    PitchProjectionEngine,
    build_pitch_point_references,
)
from app.config.pitch import SoccerPitchConfiguration

CONFIG = SoccerPitchConfiguration()


REFERENCES = build_pitch_point_references(CONFIG)


def _make_frame_with_points(points: Iterable[Tuple[float, float]]) -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (0, 96, 0)
    for a, b in CONFIG.edges:
        start = tuple(int(v) for v in _image_point_from_world(CONFIG.vertices[a - 1]))
        end = tuple(int(v) for v in _image_point_from_world(CONFIG.vertices[b - 1]))
        cv2.line(frame, start, end, (255, 255, 255), 2)
    for x_coord, y_coord in points:
        x_int = int(round(x_coord))
        y_int = int(round(y_coord))
        frame[max(0, y_int - 2) : y_int + 3, max(0, x_int - 2) : x_int + 3] = (
            255,
            255,
            255,
        )
    return frame


def _make_keypoints(
    assignments: Dict[int, Tuple[Tuple[float, float], float]],
) -> sv.KeyPoints:
    xy = np.zeros((1, len(REFERENCES), 2), dtype=np.float32)
    confidence = np.zeros((1, len(REFERENCES)), dtype=np.float32)
    for index, (point, score) in assignments.items():
        xy[0, index] = np.array(point, dtype=np.float32)
        confidence[0, index] = score
    return sv.KeyPoints(xy=xy, confidence=confidence)


def _image_point_from_world(point: Tuple[float, float]) -> Tuple[float, float]:
    return point[0] * 0.05 + 100.0, point[1] * 0.05 + 60.0


def test_pitch_projection_engine_filters_border_and_low_confidence_points() -> None:
    engine = PitchProjectionEngine(config=CONFIG, fps=25.0)
    assignments = {
        0: ((_image_point_from_world(REFERENCES[0].world_xy)), 0.95),
        5: ((_image_point_from_world(REFERENCES[5].world_xy)), 0.95),
        13: ((_image_point_from_world(REFERENCES[13].world_xy)), 0.95),
        24: ((_image_point_from_world(REFERENCES[24].world_xy)), 0.95),
        29: ((_image_point_from_world(REFERENCES[29].world_xy)), 0.20),
        30: ((3.0, 300.0), 0.95),
    }
    visible_points = [point for point, _ in assignments.values()]
    frame = _make_frame_with_points(visible_points)

    projection = engine.update(frame=frame, keypoints=_make_keypoints(assignments))
    labels = {
        observation.reference.label for observation in projection.tracking_observations
    }

    assert REFERENCES[29].label not in labels
    assert REFERENCES[30].label not in labels
    assert REFERENCES[0].label in labels
    assert REFERENCES[24].label in labels


def test_pitch_projection_engine_rejects_outlier_and_builds_fresh_homography() -> None:
    engine = PitchProjectionEngine(config=CONFIG, fps=25.0)
    inlier_indices = [0, 5, 13, 16, 24, 29]
    assignments = {
        index: (_image_point_from_world(REFERENCES[index].world_xy), 0.95)
        for index in inlier_indices
    }
    assignments[30] = ((1100.0, 600.0), 0.95)
    frame = _make_frame_with_points([point for point, _ in assignments.values()])

    projection = engine.update(frame=frame, keypoints=_make_keypoints(assignments))
    labels = {
        observation.reference.label for observation in projection.tracking_observations
    }

    assert projection.homography_status == "fresh"
    assert projection.available
    assert REFERENCES[30].label not in labels


def test_pitch_projection_engine_reuses_stale_homography_without_showing_missing_points() -> (
    None
):
    engine = PitchProjectionEngine(config=CONFIG, fps=25.0)
    inlier_indices = [0, 5, 13, 16, 24, 29]
    first_assignments = {
        index: (_image_point_from_world(REFERENCES[index].world_xy), 0.95)
        for index in inlier_indices
    }
    first_frame = _make_frame_with_points(
        [point for point, _ in first_assignments.values()]
    )
    first_projection = engine.update(
        frame=first_frame,
        keypoints=_make_keypoints(first_assignments),
    )

    second_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    second_frame[:] = (0, 96, 0)
    second_projection = engine.update(
        frame=second_frame,
        keypoints=_make_keypoints({}),
    )

    assert first_projection.homography_status == "fresh"
    assert second_projection.homography_status == "stale"
    assert second_projection.available
    assert second_projection.tracking_observations == []


def test_pitch_projection_engine_reuse_expires_after_half_second() -> None:
    engine = PitchProjectionEngine(config=CONFIG, fps=25.0)
    inlier_indices = [0, 5, 13, 16, 24, 29]
    assignments = {
        index: (_image_point_from_world(REFERENCES[index].world_xy), 0.95)
        for index in inlier_indices
    }
    frame = _make_frame_with_points([point for point, _ in assignments.values()])
    engine.update(frame=frame, keypoints=_make_keypoints(assignments))

    reused = [engine.reuse(frame) for _ in range(engine.max_stale_frames)]
    expired = engine.reuse(frame)

    assert all(result.homography_status == "reused" for result in reused)
    assert expired.homography_status == "unavailable"
    assert not expired.available


from app.geometry.camera import CameraMotionEstimator


def _field_like_frame(shift_x: int = 0) -> np.ndarray:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:] = (0, 96, 0)
    cv2.line(frame, (20, 30), (300, 70), (255, 255, 255), 3)
    cv2.line(frame, (10, 210), (300, 150), (255, 255, 255), 3)
    cv2.rectangle(frame, (70, 60), (250, 190), (255, 255, 255), 2)
    if shift_x == 0:
        return frame
    return cv2.warpAffine(
        frame,
        np.float32([[1, 0, shift_x], [0, 1, 0]]),
        (frame.shape[1], frame.shape[0]),
    )


def test_camera_motion_estimator_detects_horizontal_view_shift() -> None:
    estimator = CameraMotionEstimator(threshold_px=6.0, analysis_width=320)
    reference = _field_like_frame()
    estimator.mark_reference(reference)

    estimate = estimator.measure(_field_like_frame(shift_x=10))

    assert estimate is not None
    assert estimate.shift_x_px > 8.0
    assert estimate.magnitude_px > 8.0
    assert estimate.response >= 0.15
    assert estimate.requires_refresh


def test_camera_motion_reference_resets_after_refresh() -> None:
    estimator = CameraMotionEstimator(threshold_px=6.0, analysis_width=320)
    reference = _field_like_frame()
    shifted = _field_like_frame(shift_x=10)
    estimator.mark_reference(reference)

    assert estimator.measure(shifted).requires_refresh
    estimator.mark_reference(shifted)
    stable = estimator.measure(shifted)

    assert stable is not None
    assert not stable.requires_refresh


def test_paint_bootstrap_does_not_invent_geometry_without_lines() -> None:
    from app.geometry.pitch_lines import PaintLineProjection

    frame = np.full((480, 852, 3), (0, 96, 0), dtype=np.uint8)
    assert PaintLineProjection(CONFIG).estimate(frame, np.eye(3)) is None


def test_paint_bootstrap_recovers_layout_from_bad_landmark_seed() -> None:
    from app.geometry.pitch_lines import PaintLineProjection

    config = SoccerPitchConfiguration(length=12000, width=7000, penalty_box_width=4100)
    # Synthetic projective view, independent of the real held-out footage.
    world = np.array(
        [
            [
                config.length - config.penalty_box_length,
                (config.width + config.penalty_box_width) / 2,
            ],
            [config.length, (config.width + config.penalty_box_width) / 2],
            [
                config.length - config.goal_box_length,
                (config.width + config.goal_box_width) / 2,
            ],
            [
                config.length - config.goal_box_length,
                (config.width - config.goal_box_width) / 2,
            ],
        ],
        np.float32,
    )
    observed = np.array([[560, 360], [55, 304], [405, 264], [670, 201]], np.float32)
    image_to_world = cv2.getPerspectiveTransform(observed, world)
    image = np.full((480, 852, 3), (35, 110, 35), dtype=np.uint8)
    for a, b in config.edges:
        endpoints = np.asarray(
            [config.vertices[a - 1], config.vertices[b - 1]], dtype=float
        )
        projected = cv2.perspectiveTransform(
            endpoints.reshape(-1, 1, 2), np.linalg.inv(image_to_world)
        ).reshape(-1, 2)
        if np.isfinite(projected).all() and np.max(np.abs(projected)) < 10000:
            cv2.line(
                image,
                tuple(np.round(projected[0]).astype(int)),
                tuple(np.round(projected[1]).astype(int)),
                (240, 240, 240),
                2,
            )
    seed = image_to_world @ np.array([[1, 0, 80], [0, 1, 50], [0, 0, 1]], dtype=float)
    recovered = PaintLineProjection(config).estimate(image, seed, seed)
    assert recovered is not None
    predicted = cv2.perspectiveTransform(
        world.reshape(-1, 1, 2), np.linalg.inv(recovered)
    ).reshape(-1, 2)
    assert np.median(np.linalg.norm(predicted - observed, axis=1)) < 15


def test_paint_overlay_preserves_model_state_without_independent_support():
    from types import SimpleNamespace
    from app.vision.core import VisionCore
    from app.geometry.pitch_projection import PitchProjectionResult

    core = object.__new__(VisionCore)
    core.fps = 30
    core._paint_homography = None
    core._paint_motion = SimpleNamespace()
    core._paint_lines = SimpleNamespace(estimate=lambda *args: None)
    core._paint_attempt_frame = -10000
    core._paint_accepted_frame = -10000
    core._paint_visible = False
    core._base_homography = np.eye(3)
    core._consecutive_homography_rejects = 2
    projection = PitchProjectionResult([], [], np.eye(3), "fresh", 0.0)
    result = core._paint_override(_field_like_frame(), projection, np.empty((0, 4)), 0)
    assert result is projection
    assert core._consecutive_homography_rejects == 2
    assert np.array_equal(core._base_homography, np.eye(3))


def test_paint_transition_breaks_world_trajectory_then_transports_reference():
    from types import SimpleNamespace
    from app.vision.core import VisionCore
    from app.geometry.pitch_projection import PitchProjectionResult

    core = object.__new__(VisionCore)
    core.fps = 30
    core._paint_homography = None
    transform = np.eye(3)
    transform[0, 2] = 8
    core._paint_motion = SimpleNamespace(
        mark_reference=lambda *args, **kwargs: None,
        measure=lambda *args, **kwargs: SimpleNamespace(
            response=1.0, minimum_response=0.15, image_transform=transform
        ),
    )
    core._paint_lines = SimpleNamespace(estimate=lambda *args: np.eye(3))
    core._paint_filter = SimpleNamespace(reset=lambda: None)
    core._paint_attempt_frame = -10000
    core._paint_accepted_frame = -10000
    core._paint_visible = False
    core.paint_coordinate_transitions = 0
    projection = PitchProjectionResult([], [], np.diag([2.0, 2.0, 1.0]), "fresh", 0.0)
    transition = core._paint_override(
        _field_like_frame(), projection, np.empty((0, 4)), 0
    )
    assert transition.coordinate_transition and transition.homography is None
    assert core.paint_coordinate_transitions == 1
    moved = core._paint_override(_field_like_frame(), projection, np.empty((0, 4)), 1)
    assert moved.fit_source == "paint" and not moved.coordinate_transition
    assert np.allclose(moved.homography, np.linalg.inv(transform))
    assert np.allclose(projection.homography, np.diag([2.0, 2.0, 1.0]))


def test_paint_camera_detects_zoom_without_changing_legacy_camera_class():
    from app.geometry.camera import PaintCameraMotionEstimator

    rng = np.random.default_rng(82)
    reference = cv2.GaussianBlur(
        (rng.random((240, 320, 3)) * 255).astype(np.uint8), (3, 3), 0
    )
    transform = cv2.getRotationMatrix2D((160, 120), 3.0, 1.06)
    changed = cv2.warpAffine(reference, transform, (320, 240))
    estimator = PaintCameraMotionEstimator(analysis_width=320)
    estimator.mark_reference(reference)
    motion = estimator.measure(changed)
    assert motion is not None and motion.image_transform is not None
    assert np.allclose(motion.image_transform[:2], transform, atol=0.8)
    legacy = CameraMotionEstimator(analysis_width=320)
    legacy.mark_reference(reference)
    assert legacy.measure(changed).image_transform is None


def test_profiles_are_explicit_and_legacy_is_unchanged():
    from app.config.pitch import build_pitch_profile

    legacy, paint = build_pitch_profile()
    assert (
        legacy.length,
        legacy.width,
        legacy.penalty_box_length,
        legacy.penalty_box_width,
    ) == (12000, 7000, 2015, 4100)
    assert not paint
    configured, paint = build_pitch_profile("source-informed105")
    assert (
        configured.length,
        configured.width,
        configured.penalty_box_length,
        configured.penalty_box_width,
    ) == (10500, 6800, 1650, 4032)
    assert paint and configured is not build_pitch_profile("source-informed105")[0]
    with pytest.raises(ValueError):
        build_pitch_profile("unknown")


def test_disabled_pitch_never_constructs_or_runs_paint(monkeypatch):
    import app.vision.core as module
    from app.config.pitch import build_pitch_profile

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled pitch must not construct paint or its motion estimator")

    monkeypatch.setattr(module, "PaintLineProjection", forbidden)
    monkeypatch.setattr(module, "PaintCameraMotionEstimator", forbidden)
    config, paint = build_pitch_profile("source-informed105")
    core = module.VisionCore(
        device="cpu",
        fps=30,
        enable_player=False,
        enable_pitch=False,
        pitch_configuration=config,
        enable_paint_projection=paint,
    )
    assert core.projection_engine.config is config
    assert not core.enable_paint_projection
    assert (
        core._paint_lines is None
        and core._paint_motion is None
        and core._paint_filter is None
    )
    from types import SimpleNamespace

    core._tracker = SimpleNamespace(
        update_with_detections=lambda detections: sv.Detections(
            xyxy=np.empty((0, 4), dtype=np.float32),
            confidence=np.empty(0),
            class_id=np.empty(0, dtype=int),
            tracker_id=np.empty(0, dtype=int),
        )
    )
    core.process(_field_like_frame(), 1)
