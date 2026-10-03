"""Fresh-window verification and alert lifecycle tests without model weights.

These deterministic fixtures test mechanics. They do not measure football
recognition performance and execute on the remote CUDA development host.
"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def temporal(monkeypatch):
    np = pytest.importorskip("numpy")
    root = Path(__file__).resolve().parents[1] / "backend_core/app/foul_detection"
    # Avoid importing application startup or a checkpoint merely to verify the
    # independent scheduling contract. Restore all names after each test.
    for name in ("app", "app.foul_detection"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    for name, file in (("app.foul_detection.interactions", "interactions.py"),
                       ("app.foul_detection.temporal", "temporal.py")):
        spec = importlib.util.spec_from_file_location(name, root / file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return np, sys.modules["app.foul_detection.temporal"]


class Predictor:
    temporal_duration_s = 0.96
    temporal_frames = 16
    temporal_fps = 17.0
    model_id = "fixture-only"

    def __init__(self, records):
        self.records = records
        self.calls = 0
        self.last_record = None
        self.errors = []

    def predict(self, frames):
        assert len(frames) == self.temporal_frames
        record = self.records[min(self.calls, len(self.records) - 1)]
        self.calls += 1
        self.last_record = {key: value for key, value in record.items() if key != "return_none"}
        if record.get("return_none"):
            return None
        return SimpleNamespace(
            confidence=0.99, action=record.get("action", "Tackle"),
            decision=record.get("decision", "yellow_card"), raw_scores={},
        )


def _scores(*, offence=0.9, action=0.8, **kwargs):
    # The winning non-offence class remains below the total offence mass.
    return {
        "severity_probs": [1 - offence, offence * 0.2, offence * 0.7, offence * 0.1],
        "action_probs": [action] + [(1 - action) / 7] * 7,
        "action": "Tackle", "decision": "yellow_card", **kwargs,
    }


def _players():
    return [{"track_id": 1, "bbox": [30, 30, 50, 70], "team": "unknown", "confidence": 0.8},
            {"track_id": 2, "bbox": [55, 30, 75, 70], "team": "unknown", "confidence": 0.8}]


def _run(detector, np, *, seconds=2, fps=30, players=None):
    frame = np.full((120, 240, 3), 100, dtype=np.uint8)
    outputs = []
    for index in range(int(seconds * fps) + 1):
        prediction = detector.update(frame, index, pts_s=index / fps, players=players or _players())
        if prediction is not None:
            outputs.append(prediction)
    return outputs, frame


def _config(module, **overrides):
    return module.VerificationConfig(
        offence_threshold=overrides.pop("offence_threshold", 0.68),
        action_threshold=overrides.pop("action_threshold", 0.5),
        confirmation_windows=overrides.pop("confirmation_windows", 2),
        post_contact_s=overrides.pop("post_contact_s", 0),
        evidence_threshold=overrides.pop("evidence_threshold", 0),
        **overrides,
    )


def test_cached_prediction_cannot_emit_repeatedly_or_rerun_at_same_pts(temporal):
    np, module = temporal
    predictor = Predictor([_scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    outputs, frame = _run(detector, np)
    assert len(outputs) == 1
    calls = predictor.calls
    for index in range(61, 100):
        assert detector.update(frame, index, pts_s=2.0, players=_players()) is None
    assert predictor.calls == calls
    assert detector.stats["confirmed_episodes"] == 1


def test_high_action_score_cannot_compensate_low_offence_score(temporal):
    np, module = temporal
    predictor = Predictor([_scores(offence=0.4, action=0.99)])
    detector = module.TemporalFoulDetector(predictor, "mvit-full-v2", _config(module))
    outputs, _ = _run(detector, np)
    assert outputs == []
    assert predictor.calls > 0
    assert all(record["offence_score"] == pytest.approx(0.4) for record in detector.records)


@pytest.mark.parametrize("as_none", [False, True])
def test_no_offence_is_rejected_even_with_high_action_score(temporal, as_none):
    np, module = temporal
    predictor = Predictor([_scores(decision="no_offence", return_none=as_none)])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    outputs, _ = _run(detector, np)
    assert outputs == []
    assert detector.stats["rejected_no_offence"] > 0


def test_unaccepted_window_breaks_confirmation_streak(temporal):
    np, module = temporal
    predictor = Predictor([_scores(), _scores(return_none=True), _scores(), _scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    outputs, _ = _run(detector, np)
    assert len(outputs) == 1
    streaks = [record["confirmation_streak"] for record in detector.records]
    assert streaks[:4] == [1, 0, 1, 2]
    assert outputs[0].raw_scores["event_evidence"]["confirmation_windows"] == 2


def test_unknown_teams_do_not_become_offender_or_victim(temporal):
    np, module = temporal
    predictor = Predictor([_scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    outputs, _ = _run(detector, np)
    assert len(outputs) == 1
    evidence = outputs[0].raw_scores["event_evidence"]
    assert evidence["offender"] is None and evidence["victim"] is None
    assert all(actor["team"] == "unknown" for actor in evidence["involved_targets"])
    assert evidence["event_time_s"] <= evidence["emitted_time_s"]


def test_two_independent_pairs_can_emit_without_global_cooldown(temporal):
    np, module = temporal
    predictor = Predictor([_scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module, max_windows_per_tick=4))
    players = _players() + [
        {"track_id": 3, "bbox": [145, 30, 165, 70], "team": "away", "confidence": 0.8},
        {"track_id": 4, "bbox": [170, 30, 190, 70], "team": "home", "confidence": 0.8},
    ]
    outputs, _ = _run(detector, np, players=players)
    assert len(outputs) == 2
    evidence = [prediction.raw_scores["event_evidence"] for prediction in outputs]
    assert len({record["episode_id"] for record in evidence}) == 2
    assert abs(evidence[0]["emitted_time_s"] - evidence[1]["emitted_time_s"]) <= 1 / 30 + 1e-8


def test_second_episode_after_separation_can_emit_again(temporal):
    np, module = temporal
    predictor = Predictor([_scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    outputs, frame = _run(detector, np, seconds=1.5)
    for index in range(46, 70):
        detector.update(frame, index, pts_s=index / 30, players=[
            _players()[0], {**_players()[1], "bbox": [140, 30, 160, 70]},
        ])
    for index in range(70, 101):
        prediction = detector.update(frame, index, pts_s=index / 30, players=_players())
        if prediction is not None:
            outputs.append(prediction)
    assert len(outputs) == 2
    assert outputs[0].raw_scores["event_evidence"]["episode_id"] != outputs[1].raw_scores["event_evidence"]["episode_id"]


def test_nonfinite_secondary_class_cannot_hide_behind_finite_max(temporal):
    np, module = temporal
    record = _scores()
    record["severity_probs"][3] = float("nan")
    predictor = Predictor([record])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module))
    with pytest.raises(RuntimeError, match="[Nn]on-finite|probabilit"):
        _run(detector, np)
    assert predictor.errors


def test_scene_cut_clears_pending_streaks(temporal):
    np, module = temporal
    predictor = Predictor([_scores()])
    detector = module.TemporalFoulDetector(predictor, "mvit-pair-v2", _config(module, confirmation_windows=3))
    outputs, _ = _run(detector, np, seconds=1.3)
    assert outputs == []
    old_epoch = detector.interactions.epoch
    frame = np.full((120, 240, 3), 255, dtype=np.uint8)
    assert detector.update(frame, 40, pts_s=40 / 30, players=_players()) is None
    assert detector.interactions.epoch != old_epoch
    assert detector.streaks == {}
