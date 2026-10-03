"""Interaction mechanics, not a claim of classifier accuracy on football.

Run these numerical tests on the remote CUDA host. The module is loaded directly
to keep proposal tests independent of model weights and application startup.
"""

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="module")
def core():
    np = pytest.importorskip("numpy")
    path = Path(__file__).resolve().parents[1] / "backend_core/app/foul_detection/interactions.py"
    spec = importlib.util.spec_from_file_location("foul_interactions_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return np, module


def _player(track_id, x, *, y=30, width=20, height=40, team="unknown", **kwargs):
    return {
        "track_id": track_id,
        "bbox": [x, y, x + width, y + height],
        "confidence": 0.85,
        "team": team,
        "track_status": "detected",
        **kwargs,
    }


def _frame(core, color=100):
    np, _ = core
    return np.full((120, 240, 3), color, dtype=np.uint8)


@pytest.mark.parametrize("teams", [("home", "away"), ("unknown", "unknown"), ("home", "home")])
def test_close_contest_is_only_a_proposal(core, teams):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(31):
        episodes = detector.observe(
            _frame(core), index, index / 30,
            [_player(4, 40, team=teams[0]), _player(9, 63, team=teams[1])],
        )
    assert len(episodes) == 1
    assert detector.stats["episodes"] == 1
    assert detector.stats["emitted_episodes"] == 0
    assert not episodes[0].emitted
    assert not episodes[0].evidence["strong_evidence"]


def test_single_player_falling_does_not_create_pair(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(20):
        assert detector.observe(
            _frame(core), index, index / 30,
            [_player(8, 80, width=20 + index, height=40 - index)],
        ) == []
    assert detector.stats["episodes"] == 0


def test_ordinary_crossing_never_confirms_without_verifier(core):
    _, module = core
    detector = module.InteractionDetector()
    proposed = False
    for index in range(61):
        players = [_player(1, 30 + index * 1.4), _player(2, 150 - index * 1.4)]
        proposed |= bool(detector.observe(_frame(core), index, index / 30, players))
    assert proposed
    assert detector.stats["emitted_episodes"] == 0


def test_explicit_emission_is_once_per_pair_episode_without_global_cooldown(core):
    _, module = core
    detector = module.InteractionDetector()
    episodes = detector.observe(
        _frame(core), 0, 0,
        [_player(1, 10), _player(2, 35), _player(3, 145), _player(4, 170)],
    )
    assert len(episodes) == 2
    assert detector.try_mark_emitted(episodes[0].id, "fresh-a")
    assert not detector.try_mark_emitted(episodes[0].id, "fresh-a")
    assert not detector.try_mark_emitted(episodes[0].id, "fresh-b")
    assert detector.try_mark_emitted(episodes[1].id, "fresh-c")
    old_id = episodes[0].id
    for index in range(1, 23):
        detector.observe(_frame(core), index, index / 30, [_player(1, 10), _player(2, 100)])
    new_episode = detector.observe(_frame(core), 23, 23 / 30, [_player(1, 10), _player(2, 35)])[0]
    assert new_episode.id != old_id
    assert detector.try_mark_emitted(new_episode.id, "fresh-d")
    assert detector.stats["emitted_episodes"] == 3


def test_brief_occlusion_retains_episode_but_withholds_missing_identity(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(5):
        episode = detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])[0]
    assert all(actor["identity_reliable"] for actor in episode.identities)
    retained = detector.observe(_frame(core), 5, 5 / 30, [_player(1, 30)])[0]
    assert retained.id == episode.id
    missing = next(actor for actor in retained.identities if actor["observed_track_id"] == 2)
    assert missing["track_id"] is None
    assert not missing["identity_reliable"]
    assert detector.stats["emitted_episodes"] == 0


def test_spatial_continuity_can_merge_interaction_without_guessing_player_identity(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(5):
        old_episode = detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])[0]
    assert detector.try_mark_emitted(old_episode.id, "old-window")
    episodes = detector.observe(_frame(core), 5, 5 / 30, [_player(1, 30), _player(27, 56)])
    assert len(episodes) == 1
    assert episodes[0].id == old_episode.id
    rebound = next(actor for actor in episodes[0].identities if actor["observed_track_id"] == 27)
    assert rebound["track_id"] is None
    assert rebound["entity_id"] is None
    assert not rebound["identity_reliable"]
    assert not detector.try_mark_emitted(episodes[0].id, "new-window")


def test_role_observation_preserves_confidence_and_unknown_values(core):
    _, module = core
    detector = module.InteractionDetector()
    episode = detector.observe(_frame(core), 0, 0, [
        _player(1, 30, role="outfield", role_confidence=0.9),
        _player(2, 55, role="goalkeeper", role_confidence=0.2),
    ])[0]
    roles = {actor["observed_track_id"]: actor for actor in episode.identities}
    assert roles[1]["role"] == "outfield"
    assert roles[2]["role"] == "unknown"
    assert roles[2]["observed_role"] == "goalkeeper"
    assert roles[2]["role_confidence"] == pytest.approx(0.2)


def test_replacement_far_away_is_a_different_interaction(core):
    _, module = core
    detector = module.InteractionDetector()
    original = detector.observe(_frame(core), 0, 0, [_player(1, 30), _player(2, 55)])[0]
    updated = detector.observe(_frame(core), 1, 1 / 30, [_player(1, 30), _player(27, 0)])
    assert len(updated) == 2
    assert len({episode.id for episode in updated}) == 2
    assert original.id in {episode.id for episode in updated}


@pytest.mark.parametrize("fps", [12, 25, 30, 60])
def test_sampling_matches_source_time_and_records_repeated_low_fps_frames(core, fps):
    np, module = core
    detector = module.InteractionDetector()
    count = int(fps * 1.2)
    for index in range(count + 1):
        episode = detector.observe(
            _frame(core, 80 + index), index, index / fps, [_player(1, 30), _player(2, 55)],
        )[0]
    end = count / fps
    sample = detector.sample_episode(episode, end)
    assert sample is not None
    assert len(sample.frames) == 16
    assert sample.start_pts_s == pytest.approx(end - 0.96)
    targets = end - 0.96 + np.arange(16) / 17
    assert max(abs(target - actual) for target, actual in zip(targets, sample.source_pts)) <= 0.5 / fps + 1e-8
    assert max(sample.source_pts) <= end
    assert sample.observation_coverage == 1
    if fps == 12:
        assert len(set(sample.frame_indices)) < 16


def test_sampling_rejects_incomplete_future_cross_cut_and_sparse_windows(core):
    _, module = core
    detector = module.InteractionDetector()
    episode = detector.observe(_frame(core), 0, 0, [_player(1, 30), _player(2, 55)])[0]
    assert detector.sample_episode(episode, 0) is None
    for index in range(1, 37):
        detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])
    assert detector.sample_episode(episode, 1.2) is not None
    assert detector.sample_episode(episode, 1.25) is None
    detector.observe(_frame(core), 37, 1.24, [_player(1, 30), _player(2, 55)], scene_cut=True)
    assert detector.sample_episode(episode, 1.24) is None
    assert not detector.try_mark_emitted(episode.id, "late-old-cut-window")
    sparse = module.InteractionDetector()
    for index, pts in enumerate([0.0, 0.2, 0.4, 0.7, 0.9, 1.1]):
        sparse_episode = sparse.observe(_frame(core), index, pts, [_player(1, 30), _player(2, 55)])[0]
    assert sparse.sample_episode(sparse_episode, 1.1) is None


def test_fixed_crop_keeps_both_players_and_context_for_entire_window(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(37):
        episode = detector.observe(
            _frame(core), index, index / 30,
            [_player(1, 30 + index / 4), _player(2, 55 + index / 4)],
        )[0]
    sample = detector.sample_episode(episode, 1.2, crop=True)
    assert sample is not None and sample.region_xyxy is not None
    left, top, right, bottom = sample.region_xyxy
    assert left <= 30 and top < 30
    assert right >= 84 and bottom > 70
    assert len({frame.shape for frame in sample.frames}) == 1
    assert sample.frames[0].shape[1] == right - left


def test_camera_scale_change_is_not_relative_player_height_drop(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(15):
        scale = 1.0 - index / 100
        episode = detector.observe(
            _frame(core), index, index / 30,
            [_player(1, 30, width=20 * scale, height=40 * scale),
             _player(2, 50, width=20 * scale, height=40 * scale)],
        )[0]
    assert episode.evidence["height_drop_fraction"] == pytest.approx(0)
    assert not episode.evidence["strong_evidence"]


def test_posture_change_during_close_pair_is_evidence_not_a_foul_label(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(10):
        episode = detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])[0]
    episode = detector.observe(_frame(core), 10, 10 / 30, [_player(1, 30), _player(2, 50, width=40, height=25)])[0]
    assert episode.evidence["strong_evidence"]
    assert episode.evidence["posture_change"] > 0.25
    assert not episode.emitted


def test_verification_interval_does_not_reuse_old_motion_peak(core):
    _, module = core
    detector = module.InteractionDetector()
    for index in range(10):
        episode = detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])[0]
    detector.observe(_frame(core), 10, 10 / 30, [_player(1, 30), _player(2, 50, width=40, height=25)])
    for index in range(11, 70):
        episode = detector.observe(_frame(core), index, index / 30, [_player(1, 30), _player(2, 55)])[0]
    assert episode.peak_evidence["strong_evidence"]
    assert not episode.evidence["strong_evidence"]
    local = episode.peak_in_interval(1.5, 2.3)
    assert local is not None
    assert local[0] >= 1.5
    assert not local[1]["strong_evidence"]
    assert episode.peak_in_interval(4.0, 4.5) is None


def test_source_discontinuity_and_invalid_values_do_not_reuse_evidence(core):
    np, module = core
    detector = module.InteractionDetector()
    original = detector.observe(_frame(core), 0, 0, [_player(1, 30), _player(2, 55)])[0]
    duplicate = detector.observe(_frame(core), 1, 0, [_player(1, 30), _player(2, 55)])[0]
    assert duplicate.id == original.id
    assert detector.stats["duplicate_pts"] == 1
    reset = detector.observe(_frame(core), 2, 0.7, [_player(1, 30), _player(2, 55)])[0]
    assert reset.epoch != original.epoch
    assert len(detector.buffer) == 1
    with pytest.raises(ValueError, match="PTS"):
        detector.observe(_frame(core), 3, float("nan"), [])
    assert detector.observe(_frame(core), 3, 0.74, [_player(4, 30, bbox=[0, 0, np.nan, 1])]) == [reset]


def test_image_ownership_and_ring_retention(core):
    np, module = core
    detector = module.InteractionDetector()
    frame = _frame(core)
    for index in range(121):
        frame[:] = 100 + index % 2
        detector.observe(frame, index, index / 30, [_player(1, 30), _player(2, 55)])
    frame[:] = 0
    assert detector.buffer[-1].frame.max() == 100
    assert detector.buffer[0].pts_s >= 1.0
    assert np.all(detector.buffer[-1].frame == 100)


def test_scene_cut_and_pair_budget_are_explicit(core):
    _, module = core
    detector = module.InteractionDetector(module.InteractionConfig(max_active_pairs=2))
    first = detector.observe(_frame(core, 0), 0, 0, [_player(i, 40 + i * 2) for i in range(5)])
    assert len(first) == 2
    assert detector.stats["proposal_budget_drops"] > 0
    second = detector.observe(_frame(core, 255), 1, 1 / 30, [_player(1, 40), _player(2, 60)])
    assert second[0].epoch != first[0].epoch
    assert detector.stats["scene_cuts"] == 1
