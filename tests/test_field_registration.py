"""Numerical field-registration regressions using independent synthetic geometry.

These fixtures establish geometry and lifecycle behavior, not model accuracy on
football footage. Scientific execution belongs on the remote CUDA host.
"""

from pathlib import Path
import sys

import pytest


@pytest.fixture
def geometry():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend_core"))
    from app.config.pitch import build_pitch_profile
    from app.geometry.pitch_registration import FieldLineRegistration, FieldPaintEvidence

    config, _ = build_pitch_profile("source-informed105")
    return np, cv2, config, FieldLineRegistration, FieldPaintEvidence


def _map(np, points, matrix):
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ matrix.T
    return homogeneous[:, :2] / homogeneous[:, 2:3]


def _scene(geometry, *, markings="full", boxes=(), texture_seed=91):
    """Render a perspective pitch without using the registration's template."""
    np, cv2, config, _, _ = geometry
    world_corners = np.array(
        [[0, 0], [config.length, 0], [config.length, config.width], [0, config.width]],
        dtype=np.float32,
    )
    image_corners = np.array([[765, 85], [210, 40], [40, 410], [835, 435]], dtype=np.float32)
    world_to_image = cv2.getPerspectiveTransform(world_corners, image_corners)
    image_to_world = np.linalg.inv(world_to_image)
    frame = np.full((480, 852, 3), (52, 44, 40), dtype=np.uint8)
    grass = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(grass, image_corners.astype(np.int32), 255)
    rng = np.random.default_rng(texture_seed)
    texture = cv2.GaussianBlur(rng.normal(0, 12, grass.shape).astype(np.float32), (3, 3), 0)
    color = np.array([35, 108, 35])[None, None, :] + texture[:, :, None]
    frame[grass > 0] = np.clip(color[grass > 0], 0, 255).astype(np.uint8)

    def draw(points):
        pixels = _map(np, points, world_to_image)
        cv2.polylines(frame, [np.round(pixels).astype(np.int32)], False, (235, 235, 235), 3)

    if markings == "full":
        for first, second in config.edges:
            draw([config.vertices[first - 1], config.vertices[second - 1]])
    elif markings == "parallel":
        for x in (0, config.goal_box_length, config.penalty_box_length, config.length / 2,
                  config.length - config.penalty_box_length, config.length - config.goal_box_length,
                  config.length):
            draw([(x, 0), (x, config.width)])

    if markings in {"full", "circle"}:
        theta = np.linspace(0, 2 * np.pi, 241)
        draw(np.column_stack((config.length / 2 + config.centre_circle_radius * np.cos(theta),
                              config.width / 2 + config.centre_circle_radius * np.sin(theta))))
    if markings == "full":
        angle = np.arccos((config.penalty_box_length - config.penalty_spot_distance)
                          / config.centre_circle_radius)
        for center, midpoint in ((config.penalty_spot_distance, 0),
                                 (config.length - config.penalty_spot_distance, np.pi)):
            theta = np.linspace(midpoint - angle, midpoint + angle, 81)
            draw(np.column_stack((center + config.centre_circle_radius * np.cos(theta),
                                  config.width / 2 + config.centre_circle_radius * np.sin(theta))))
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        color = (190, 55, 30) if index % 2 else (30, 45, 190)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
    return frame, image_to_world


def test_three_parallel_lines_and_one_crossing_line_do_not_fix_transverse_scale(geometry):
    """Four line names can still leave one projective degree of freedom unknown."""
    np, cv2, config, Registration, Evidence = geometry
    world_to_image = np.array([[-.25, 0, 760], [0, .13, 40], [0, 0, 1]], float)
    frame = np.full((480, 852, 3), (52, 44, 40), dtype=np.uint8)
    field = _map(np, [[0, 0], [config.length, 0],
                      [config.length, config.width], [0, config.width]], world_to_image)
    cv2.fillConvexPoly(frame, np.round(field).astype(np.int32), (35, 108, 35))
    penalty_low = (config.width - config.penalty_box_width) / 2
    penalty_high = (config.width + config.penalty_box_width) / 2
    goal_low = (config.width - config.goal_box_width) / 2
    goal_high = (config.width + config.goal_box_width) / 2
    # Only one horizontal line is visible. The three vertical lines do not
    # determine distance in the transverse direction without more evidence.
    observed_segments = [
        [[0, 0], [config.length, 0]],
        [[0, 0], [0, config.width]],
        [[config.penalty_box_length, penalty_low], [config.penalty_box_length, penalty_high]],
        [[config.goal_box_length, goal_low], [config.goal_box_length, goal_high]],
    ]
    for segment in observed_segments:
        pixels = _map(np, segment, world_to_image)
        cv2.polylines(frame, [np.round(pixels).astype(np.int32)], False, (235, 235, 235), 3)
    evidence = Evidence(frame)
    registration = Registration(config, 30.)
    truth = np.linalg.inv(world_to_image)
    for transverse_scale in (1., 1.05, 1.10):
        candidate = np.diag([1., transverse_scale, 1.]) @ truth
        score = registration._score(evidence, candidate)
        assert score is None or not score["accepted"], (
            "Three concurrent canonical lines and one other line cannot independently "
            f"validate metric scale: {score}"
        )


def _holdouts(geometry):
    np, _, config, _, _ = geometry
    # These probe points are independent of the seed perturbation and fit API.
    return np.array([[300, 300], [config.length - 300, 300],
                     [300, config.width - 300], [config.length - 300, config.width - 300],
                     [config.length * .27, config.width * .36],
                     [config.length * .73, config.width * .64]], dtype=float)


def _midfield_scene(geometry):
    np, cv2, config, _, _ = geometry
    frame, truth = _scene(geometry)
    center = _map(np, [[config.length / 2, config.width / 2]], np.linalg.inv(truth))[0]
    view = np.array([[2.5, 0, 426 - 2.5 * center[0]], [0, 1.05, -14], [0, 0, 1]], dtype=float)
    frame = cv2.warpPerspective(frame, view, (852, 480), borderValue=(52, 44, 40))
    return frame, truth @ np.linalg.inv(view)


def test_one_sided_boundary_paint_survives_and_remote_board_does_not(geometry):
    np, cv2, _, _, evidence_type = geometry
    frame = np.full((480, 852, 3), (52, 44, 40), dtype=np.uint8)
    frame[90:] = (35, 108, 35)
    cv2.line(frame, (40, 91), (812, 91), (235, 235, 235), 3)
    cv2.line(frame, (40, 35), (812, 35), (235, 235, 235), 3)
    evidence = evidence_type(frame)
    assert evidence.paint[88:96, 50:802].sum() > 600
    assert evidence.paint[30:40].sum() == 0
    assert any(line["boundary"] and line["length"] > 500 for line in evidence.lines)


@pytest.mark.parametrize("occluded", [False, True])
def test_refinement_improves_independently_shifted_seed(geometry, occluded):
    np, _, config, registration_type, evidence_type = geometry
    boxes = ((725, 175, 750, 230), (440, 210, 465, 270), (100, 305, 127, 370)) if occluded else ()
    frame, truth = _scene(geometry, boxes=boxes)
    evidence = evidence_type(frame, boxes)
    registration = registration_type(config, 30)
    pixel_shift = np.array([[1, 0, 7], [0, 1, -5], [0, 0, 1]], dtype=float)
    seed = truth @ pixel_shift
    probes = _holdouts(geometry)
    expected = _map(np, probes, np.linalg.inv(truth))
    initial = _map(np, probes, np.linalg.inv(seed))
    refined = registration._refine(evidence, seed)
    assert refined is not None, "A supported, mildly perturbed full pitch should be refinable"
    actual = _map(np, probes, np.linalg.inv(refined))
    error = np.linalg.norm(actual - expected, axis=1)
    assert np.median(error) < 3
    assert np.max(error) < 6
    assert np.median(error) < np.median(np.linalg.norm(initial - expected, axis=1)) / 2
    quality = registration._score(evidence, refined)
    assert quality is not None and quality["accepted"]


def test_player_boxes_remove_paint_evidence_without_erasing_whole_pitch(geometry):
    _, _, _, _, evidence_type = geometry
    boxes = ((440, 210, 465, 270),)
    frame, _ = _scene(geometry, boxes=boxes)
    evidence = evidence_type(frame, boxes)
    assert evidence.allowed[205:276, 435:471].sum() == 0
    assert evidence.paint[205:276, 435:471].sum() == 0
    assert evidence.paint.sum() > 1500


@pytest.mark.parametrize("markings", ["circle", "parallel", "none"])
def test_underconstrained_markings_never_pass_geometry_score(geometry, markings):
    _, _, config, registration_type, evidence_type = geometry
    frame, truth = _scene(geometry, markings=markings)
    registration = registration_type(config, 30)
    evidence = evidence_type(frame)
    quality = registration._score(evidence, truth)
    assert quality is None or not quality["accepted"]
    if markings == "circle":
        assert registration._ellipse_seeds(evidence, truth) == []


def test_horizon_through_visible_grass_is_rejected(geometry):
    np, _, config, registration_type, evidence_type = geometry
    frame, truth = _scene(geometry)
    evidence = evidence_type(frame)
    registration = registration_type(config, 30)
    crossed = truth.copy()
    crossed[2] = [1 / 425, 0, -1]
    assert not registration._valid_plane(evidence, crossed)
    assert registration._score(evidence, crossed) is None


def test_concave_projective_fold_is_rejected(geometry):
    np, cv2, config, registration_type, evidence_type = geometry
    frame, truth = _scene(geometry)
    evidence = evidence_type(frame)
    registration = registration_type(config, 30)
    scale = np.array([evidence.width, evidence.height])
    source = np.array([[.1, .2], [.9, .2], [.9, .9], [.1, .9]], dtype=np.float32) * scale
    destination = np.array([[.32, .42], [.68, -.02], [1.12, .68], [-.12, .68]], dtype=np.float32) * scale
    delta = cv2.getPerspectiveTransform(source.astype(np.float32), destination.astype(np.float32))
    folded = truth @ np.linalg.inv(delta)
    assert not registration._valid_plane(evidence, folded)
    assert registration._score(evidence, folded) is None


def test_singular_seed_is_rejected_without_numerical_exception(geometry):
    np, _, config, registration_type, evidence_type = geometry
    frame, _ = _scene(geometry)
    registration = registration_type(config, 30)
    evidence = evidence_type(frame)
    singular = np.zeros((3, 3))
    assert registration._refine(evidence, singular) is None
    assert registration._score(evidence, singular) is None


def test_midfield_circle_halfway_and_touchlines_provide_metric_registration(geometry):
    np, _, config, registration_type, _ = geometry
    # Crop the broadcast view to midfield while retaining visible touchlines.
    frame, truth = _midfield_scene(geometry)
    registration = registration_type(config, 30)
    seed = truth @ np.array([[1, 0, 7], [0, 1, -5], [0, 0, 1]], dtype=float)
    result = registration.update(frame, seed, np.empty((0, 4)), 0)
    assert result.homography is not None, "A painted circle, halfway line and touchlines constrain midfield"
    probes = np.array([[config.length / 2 - 1000, config.width / 2 - 1200],
                       [config.length / 2 + 1000, config.width / 2 - 1200],
                       [config.length / 2 - 1000, config.width / 2 + 1200],
                       [config.length / 2 + 1000, config.width / 2 + 1200]], dtype=float)
    expected = _map(np, probes, np.linalg.inv(truth))
    error = np.linalg.norm(_map(np, probes, np.linalg.inv(result.homography)) - expected, axis=1)
    assert np.median(error) < 4 and np.max(error) < 6
    assert result.quality["accepted"]


def test_wide_white_halfway_line_does_not_discard_one_grass_half(geometry):
    np, _, config, _, evidence_type = geometry
    frame, truth = _midfield_scene(geometry)
    evidence = evidence_type(frame)
    world = [[config.length / 2 - 1800, config.width / 2],
             [config.length / 2 + 1800, config.width / 2]]
    pixels = np.round(_map(np, world, np.linalg.inv(truth))).astype(int)
    for x, y in pixels:
        assert frame[y, x, 1] > frame[y, x, 0] * 2
        assert evidence.grass[y, x] > 0, "White field markings must not disconnect and remove playing grass"


def test_adjacent_motion_recovers_projective_pan_not_only_similarity(geometry):
    np, cv2, config, registration_type, evidence_type = geometry
    frame, _ = _scene(geometry)
    transform = np.array([[1.006, .007, 5], [-.002, 1.009, -2],
                          [4e-5, -2e-5, 1]], dtype=float)
    moved = cv2.warpPerspective(frame, transform, (852, 480), borderValue=(52, 44, 40))
    registration = registration_type(config, 30)
    assert registration._motion(frame, evidence_type(frame)) == (None, None)
    recovered, quality = registration._motion(moved, evidence_type(moved))
    assert recovered is not None and quality is not None
    probes = np.array([[250, 130], [650, 130], [180, 350], [700, 350], [430, 240]], dtype=float)
    error = np.linalg.norm(_map(np, probes, recovered) - _map(np, probes, transform), axis=1)
    assert np.median(error) < 1
    assert np.max(error) < 2


@pytest.mark.parametrize("flip_x,flip_y", [(False, False), (True, False), (False, True), (True, True)])
def test_mirror_hypotheses_share_the_explicit_source_axis(geometry, flip_x, flip_y):
    np, _, config, registration_type, evidence_type = geometry
    frame, truth = _scene(geometry)
    evidence = evidence_type(frame)
    registration = registration_type(config, 30)
    mirror = np.array([[-1 if flip_x else 1, 0, config.length if flip_x else 0],
                       [0, -1 if flip_y else 1, config.width if flip_y else 0],
                       [0, 0, 1]], dtype=float)
    oriented = registration._orient(mirror @ truth, evidence)
    points = np.array([[300, 150], [600, 150], [300, 350], [600, 350]], dtype=float)
    assert np.allclose(_map(np, points, oriented), _map(np, points, truth), atol=1e-6)
    assert registration._valid_plane(evidence, oriented)


def test_blank_cut_omits_positions_and_does_not_restore_model_seed(geometry):
    np, _, config, registration_type, _ = geometry
    frame, truth = _scene(geometry)
    registration = registration_type(config, 30)
    first = registration.update(frame, truth, np.empty((0, 4)), 0)
    assert first.homography is not None
    cut = np.full(frame.shape, (35, 108, 35), dtype=np.uint8)
    result = registration.update(cut, truth, np.empty((0, 4)), 1)
    assert result.homography is None
    assert result.status == "unavailable"
    assert not result.quality["accepted"]
    assert result.epoch > first.epoch


def test_frame_gap_breaks_filter_history_even_when_geometry_can_be_recovered(geometry):
    np, _, config, registration_type, _ = geometry
    frame, truth = _scene(geometry)
    registration = registration_type(config, 30)
    first = registration.update(frame, truth, np.empty((0, 4)), 0)
    assert first.homography is not None
    recovered = registration.update(frame, truth, np.empty((0, 4)), 4)
    assert recovered.epoch > first.epoch
    assert recovered.homography is None or recovered.coordinate_transition


def test_backward_frame_index_resets_attempt_and_uncertainty_clocks(geometry):
    np, cv2, config, registration_type, _ = geometry
    frame, truth = _scene(geometry)
    registration = registration_type(config, 30)
    registration.previous_frame = registration.last_fit = registration.last_attempt = 300
    registration.last_bootstrap = 300
    registration.homography = truth
    registration.previous_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    registration.previous_mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
    registration.transport_error = 5
    registration.last_quality = {"accepted": True, "line_error_px": 1}
    blank = np.full(frame.shape, (52, 44, 40), dtype=np.uint8)
    result = registration.update(blank, None, np.empty((0, 4)), 0)
    assert registration.last_attempt == 0
    assert registration.last_bootstrap == 0
    assert registration.last_fit < 0 and registration.transport_error == 0
    assert result.homography is None and result.quality["source"] == "unavailable"
    assert "line_error_px" not in result.quality
