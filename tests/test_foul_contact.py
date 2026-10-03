"""Causal contact mechanics, independent of learned model performance.

Run on the remote CUDA host. Fixtures intentionally contain no match-specific
timestamps, regions, player labels, or author checkpoints.
"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def contact(monkeypatch):
    np = pytest.importorskip("numpy")
    root = Path(__file__).resolve().parents[1] / "backend_core/app/foul_detection"
    for name in ("app", "app.foul_detection"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    for name in ("interactions", "temporal", "contact"):
        full_name = f"app.foul_detection.{name}"
        spec = importlib.util.spec_from_file_location(full_name, root / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full_name, module)
        spec.loader.exec_module(module)
    return np, sys.modules["app.foul_detection.contact"], sys.modules["app.foul_detection.interactions"]


class Predictor:
    temporal_duration_s = 0.96
    temporal_frames = 16
    temporal_fps = 17.0
    model_id = "fixture-only"

    def __init__(self, *, decision="yellow_card", action="Tackle", offence=0.85, return_none=False):
        self.calls = 0
        self.last_record = None
        self.errors = []
        self.decision = decision
        self.action = action
        self.offence = offence
        self.return_none = return_none

    def predict(self, frames):
        assert len(frames) == self.temporal_frames
        self.calls += 1
        self.last_record = {
            "severity_probs": [1 - self.offence, self.offence * 0.2,
                               self.offence * 0.7, self.offence * 0.1],
            "action_probs": [0.8] + [0.2 / 7] * 7,
            "decision": self.decision,
            "action": self.action,
        }
        if self.return_none:
            return None
        return SimpleNamespace(decision=self.decision, action=self.action,
                               confidence=0.99, raw_scores={})


def _players(pts, *, aftermath=True, contact=True, offset=0, ids=(11, 29)):
    first = [30 + offset, 30, 50 + offset, 70]
    second = [55 + offset, 30, 75 + offset, 70]
    if contact and pts >= 1.0:
        second = [38 + offset, 30, 58 + offset, 70]
    if aftermath and contact and pts >= 1.2:
        first = [30 + offset, 50, 60 + offset, 70]
    return [
        {"track_id": ids[0], "bbox": first, "team": "unknown", "confidence": 0.85},
        {"track_id": ids[1], "bbox": second, "team": "unknown", "confidence": 0.85},
    ]


def _run(detector, np, *, fps=30, seconds=1.8, aftermath=True, contact=True):
    frame = np.full((120, 240, 3), 100, dtype=np.uint8)
    outputs = []
    for index in range(int(seconds * fps) + 1):
        pts = index / fps
        result = detector.update(frame, index, pts_s=pts,
                                 players=_players(pts, aftermath=aftermath, contact=contact))
        if result is not None:
            outputs.append(result)
        outputs.extend(detector.drain_pending(pts))
    return outputs, frame


@pytest.mark.parametrize("fps", [12, 25, 30, 60])
def test_contact_requires_fresh_model_and_independent_causal_aftermath(contact, fps):
    np, module, _ = contact
    predictor = Predictor()
    detector = module.ContactFoulDetector(predictor)
    outputs, _ = _run(detector, np, fps=fps)
    assert len(outputs) == 1
    evidence = outputs[0].raw_scores["event_evidence"]
    support = evidence["contact_support"]
    assert support["sample_start_pts_s"] <= support["contact_pts_s"] <= support["sample_end_pts_s"]
    times = support["aftermath_pts_s"]
    assert len(set(times)) >= 2
    assert max(times) - min(times) >= 0.06 - 1e-9
    assert min(times) >= support["contact_pts_s"] + 0.12 - 1e-9
    assert max(times) <= support["sample_end_pts_s"] <= evidence["emitted_time_s"]
    assert evidence["offender"] is None and evidence["victim"] is None
    assert evidence["involved_targets"] == []
    assert evidence["model_id"] == "mvit-v2-local"
    assert outputs[0].action == "Tackle"


@pytest.mark.parametrize("decision,return_none", [("no_offence", False), ("yellow_card", True)])
def test_physical_contact_never_overrides_no_offence(contact, decision, return_none):
    np, module, _ = contact
    predictor = Predictor(decision=decision, return_none=return_none)
    detector = module.ContactFoulDetector(predictor)
    outputs, _ = _run(detector, np)
    assert predictor.calls > 0
    assert outputs == []
    assert detector.stats["rejected_no_offence"] > 0


@pytest.mark.parametrize("contact_present,aftermath", [(True, False), (False, True)])
def test_geometry_or_single_player_posture_alone_cannot_alert(contact, contact_present, aftermath):
    np, module, _ = contact
    predictor = Predictor()
    detector = module.ContactFoulDetector(predictor)
    outputs, _ = _run(detector, np, contact=contact_present, aftermath=aftermath)
    assert outputs == []
    assert predictor.calls == 0


def test_high_action_score_cannot_compensate_low_offence(contact):
    np, module, _ = contact
    predictor = Predictor(offence=0.4)
    detector = module.ContactFoulDetector(predictor)
    outputs, _ = _run(detector, np)
    assert predictor.calls > 0
    assert outputs == []


def test_repeated_received_pts_cannot_reuse_cached_positive(contact):
    np, module, _ = contact
    predictor = Predictor()
    detector = module.ContactFoulDetector(predictor)
    outputs, frame = _run(detector, np)
    assert len(outputs) == 1
    calls = predictor.calls
    for index in range(55, 80):
        assert detector.update(frame, index, pts_s=1.8, players=_players(1.8)) is None
    assert detector.drain_pending(1.8) == []
    assert predictor.calls == calls


def _row(pts, *, contact=False, changed=False, distinct=False, region=(20, 20, 70, 90)):
    return {
        "pts_s": pts,
        "region_xyxy": region,
        "evidence": {
            "min_detection_confidence": 0.8,
            "overlap_fraction": 0.0 if distinct else 0.9,
            "normalized_distance": 0.5 if distinct else 0.2,
            "overlap_change": 0.8 if contact else 0.0,
            "relative_approach_per_second": 1.0 if contact else 0.0,
            "height_drop_fraction": 0.3 if changed else 0.0,
            "posture_change": 0.0,
            "proposal_strength": 0.5 if contact else 0.1,
        },
    }


def _supported_episode(*, name="fixture-a", epoch=0, members=(11, 29), reliable=True,
                       contact_pts=1.0, region=(20, 20, 70, 90)):
    history = [_row(contact_pts - 0.5, distinct=True, region=region),
               _row(contact_pts - 0.25, distinct=True, region=region),
               _row(contact_pts, contact=True, region=region),
               _row(contact_pts + 0.15, changed=True, region=region),
               _row(contact_pts + 0.22, changed=True, region=region)]
    return SimpleNamespace(
        id=name, epoch=epoch, emitted=False, evidence_history=history,
        identities=[{"track_id": member if reliable else None,
                     "identity_reliable": reliable} for member in members],
    )


def _stub_window(detector, monkeypatch, *, start=0.5, end=1.3):
    monkeypatch.setattr(detector.interactions, "sample_episode", lambda *args, **kwargs:
                        SimpleNamespace(source_pts=[start, end], frame_indices=[5, 13],
                                        region_xyxy=(10, 10, 90, 100)))


def test_newly_duplicated_body_boxes_need_prior_distinct_observations(contact, monkeypatch):
    _, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    episode = _supported_episode()
    _stub_window(detector, monkeypatch)
    assert detector._support(episode, 1.4) is not None
    # Keep the same contact and aftermath, but replace the preceding separate
    # bodies by overlapping duplicate boxes. This is not new physical evidence.
    for row in episode.evidence_history[:2]:
        row["evidence"].update(overlap_fraction=0.99, normalized_distance=0.02)
    assert detector._support(episode, 1.5) is None


@pytest.mark.parametrize("after_pts", [[1.15], [1.15, 1.15], [1.15, 1.18]])
def test_one_or_repeated_or_too_narrow_aftermath_is_insufficient(contact, monkeypatch, after_pts):
    _, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    episode = _supported_episode()
    episode.evidence_history = episode.evidence_history[:3] + [_row(pts, changed=True) for pts in after_pts]
    _stub_window(detector, monkeypatch)
    assert detector._support(episode, 1.4) is None


@pytest.mark.parametrize("sample_start,sample_end", [(1.01, 1.3), (0.5, 1.1), (0.5, 1.2)])
def test_contact_and_aftermath_must_be_inside_actual_sample_support(contact, monkeypatch, sample_start, sample_end):
    _, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    episode = _supported_episode()
    # A high-strength stale peak and future posture row must neither move the
    # localization outside source samples nor corroborate an earlier window.
    episode.evidence_history.insert(0, _row(0.1, contact=True))
    episode.evidence_history.append(_row(1.8, changed=True))
    _stub_window(detector, monkeypatch, start=sample_start, end=sample_end)
    assert detector._support(episode, 1.4) is None


def test_ambiguous_duplicate_region_merges_but_disjoint_reliable_pairs_survive(contact, monkeypatch):
    _, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    emitted_ids = set()

    def mark(episode_id, window_id):
        if episode_id in emitted_ids:
            return False
        emitted_ids.add(episode_id)
        return True

    monkeypatch.setattr(detector.interactions, "try_mark_emitted", mark)
    first = _supported_episode(name="first", members=(11, 29))
    peak = (1.0, {}, (20, 20, 70, 90))
    assert detector._mark_emitted(first, "first-window", peak, 1.4)
    duplicate = _supported_episode(name="duplicate", reliable=False)
    assert not detector._mark_emitted(duplicate, "duplicate-window", (1.1, {}, (22, 22, 72, 92)), 1.5)
    assert detector.stats["suppressed_contact_duplicates"] == 1
    independent = _supported_episode(name="independent", members=(44, 75))
    assert detector._mark_emitted(independent, "independent-window", (1.15, {}, (22, 22, 72, 92)), 1.55)
    assert len(detector._region_emissions) == 2


def test_ended_interaction_can_form_a_later_event_without_global_cooldown(contact, monkeypatch):
    _, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    monkeypatch.setattr(detector.interactions, "try_mark_emitted", lambda *args: True)
    first = _supported_episode(name="first", reliable=False)
    later = _supported_episode(name="later", reliable=False, contact_pts=2.0)
    assert detector._mark_emitted(first, "first-window", (1.0, {}, (20, 20, 70, 90)), 1.4)
    assert detector._mark_emitted(later, "later-window", (2.0, {}, (20, 20, 70, 90)), 2.4)


def test_scene_cut_invalidates_old_support_and_region_ledger(contact):
    np, module, _ = contact
    detector = module.ContactFoulDetector(Predictor())
    outputs, _ = _run(detector, np)
    assert len(outputs) == 1 and detector._region_emissions
    old_epoch = detector.interactions.epoch
    old_episode_ids = set(detector._support_cache)
    frame = np.full((120, 240, 3), 255, dtype=np.uint8)
    assert detector.update(frame, 55, pts_s=55 / 30, players=_players(55 / 30)) is None
    assert detector.interactions.epoch > old_epoch
    assert detector._region_emissions == []
    assert old_episode_ids.isdisjoint(detector._support_cache)
    assert all(support is None for support in detector._support_cache.values())
    assert detector.pending == []


@pytest.mark.parametrize("overrides", [
    {"min_overlap": float("nan")}, {"min_overlap": 0},
    {"aftermath_min_observations": 1}, {"prior_min_observations": 1},
    {"min_contact_age_s": 0.7, "max_contact_age_s": 0.3},
])
def test_invalid_contact_configuration_fails_closed(contact, overrides):
    _, module, _ = contact
    with pytest.raises(ValueError, match="configuration"):
        module.ContactConfig(**overrides)
