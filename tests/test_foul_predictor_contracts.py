"""Checkpoint-free predictor contracts, run on the remote CUDA test host.

CPU tensors below are preprocessing and deterministic test fixtures. They do
not run a learned model or provide a CPU inference fallback. External author
modules are replaced with small original fixtures, never vendored GPL code.
"""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def predictors(monkeypatch):
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    pytest.importorskip("cv2")
    pytest.importorskip("torchvision")
    root = Path(__file__).resolve().parents[1] / "backend_core/app/foul_detection"
    prior_private_modules = {
        name for name in sys.modules if name.startswith("_refereelink_multidim_")
    }
    for name in ("app", "app.foul_detection"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    for file in ("types.py", "predictor.py", "causal_predictor.py", "multidim.py"):
        name = f"app.foul_detection.{file.removesuffix('.py')}"
        spec = importlib.util.spec_from_file_location(name, root / file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    yield SimpleNamespace(
        np=np,
        torch=torch,
        multidim=sys.modules["app.foul_detection.multidim"],
        causal=sys.modules["app.foul_detection.causal_predictor"],
    )
    for name in tuple(sys.modules):
        if name.startswith("_refereelink_multidim_") and name not in prior_private_modules:
            del sys.modules[name]


def test_multidim_uses_one_grayscale_view_and_unscaled_numeric_values(predictors):
    np, torch = predictors.np, predictors.torch
    frame = np.zeros((48, 85, 3), dtype=np.uint8)
    frame[:, :, 1] = 255
    tensor = predictors.multidim.MultiDimStackerPredictor._preprocess([frame] * 15)
    assert tuple(tensor.shape) == (1, 1, 1, 15, 720, 1280)
    assert tensor.dtype == torch.float32
    # OpenCV's RGB grayscale conversion maps pure green to 150, and the
    # author's single-view branch divides by 255 without channel normalization.
    assert torch.allclose(tensor, torch.full_like(tensor, 150 / 255))


@pytest.mark.parametrize("sample_count", [0, 14, 16])
def test_multidim_refuses_wrong_temporal_sample_count(predictors, sample_count):
    frame = predictors.np.zeros((20, 40, 3), dtype=predictors.np.uint8)
    with pytest.raises(ValueError, match="exactly 15"):
        predictors.multidim.MultiDimStackerPredictor._preprocess([frame] * sample_count)


@pytest.mark.parametrize("invalid", ["grayscale", "float", "rgba"])
def test_multidim_refuses_malformed_decoded_frames(predictors, invalid):
    np = predictors.np
    frame = np.zeros((20, 40, 3), dtype=np.uint8)
    if invalid == "grayscale":
        frame = frame[:, :, 0]
    elif invalid == "float":
        frame = frame.astype(np.float32)
    else:
        frame = np.zeros((20, 40, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="uint8 BGR"):
        predictors.multidim.MultiDimStackerPredictor._preprocess([frame] * 15)


@pytest.mark.parametrize("device,available", [("cpu", True), ("cuda", False)])
def test_multidim_refuses_cpu_or_unavailable_cuda(predictors, monkeypatch, device, available):
    monkeypatch.setattr(predictors.torch.cuda, "is_available", lambda: available)
    with pytest.raises(RuntimeError, match="requires an available CUDA"):
        predictors.multidim.MultiDimStackerPredictor("missing", "missing", device=device)


def test_multidim_requires_strict_full_checkpoint_loading(predictors, monkeypatch, tmp_path):
    module, torch = predictors.multidim, predictors.torch
    calls = []

    class NetworkFixture:
        def load_state_dict(self, state, *, strict):
            calls.append((state, strict))
            return SimpleNamespace(missing_keys=[], unexpected_keys=[])

        def to(self, device):
            assert device.type == "cuda"
            return self

        def eval(self):
            return self

    checkpoint = tmp_path / "fixture-checkpoint"
    checkpoint.write_bytes(b"fixture; not a learned checkpoint")
    state = {"fixture.weight": torch.ones(1)}
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch, "load", lambda *args, **kwargs: {"state_dict": state})
    monkeypatch.setattr(module, "_load_author_modules", lambda *args: (None, None, "fixture"))
    monkeypatch.setattr(module, "_AuthorNetwork", lambda *args: NetworkFixture())
    predictor = module.MultiDimStackerPredictor(str(checkpoint), str(tmp_path))
    assert calls == [(state, True)]
    assert predictor.load_state_result["strict"] is True
    assert predictor.strict_loaded_tensors == 1
    assert predictor.load_state_result["missing_keys"] == []
    assert predictor.load_state_result["unexpected_keys"] == []


def _forward_fixture(predictors, outputs, *, flip=True):
    module = predictors.multidim
    predictor = module.MultiDimStackerPredictor.__new__(module.MultiDimStackerPredictor)
    predictor.test_time_flip = flip
    predictor._model = lambda tensor: outputs
    return predictor


def test_finite_check_covers_overflow_while_averaging_flip_logits(predictors):
    torch = predictors.torch
    outputs = (
        torch.full((4,), 50_000.0, dtype=torch.float16),
        torch.full((8,), 50_000.0, dtype=torch.float16),
        torch.ones(2),
    )
    assert all(torch.isfinite(value).all() for value in outputs)
    predictor = _forward_fixture(predictors, outputs)
    _, _, finite = predictor._forward(torch.zeros((1, 1, 1, 15, 1, 1)))
    assert not finite


@pytest.mark.parametrize("bad_output", ["severity", "action", "features"])
def test_finite_check_rejects_each_nonfinite_model_output(predictors, bad_output):
    torch = predictors.torch
    outputs = [torch.zeros(4), torch.zeros(8), torch.ones(2)]
    index = {"severity": 0, "action": 1, "features": 2}[bad_output]
    outputs[index][0] = float("nan")
    predictor = _forward_fixture(predictors, tuple(outputs))
    assert not predictor._forward(torch.zeros((1, 1, 1, 15, 1, 1)))[2]


def _prediction_fixture(predictors, monkeypatch, forward):
    module, torch = predictors.multidim, predictors.torch
    predictor = module.MultiDimStackerPredictor.__new__(module.MultiDimStackerPredictor)
    predictor.device = torch.device("cuda")
    predictor.test_time_flip = True
    predictor.fp32_retry_count = 0
    predictor.errors = []
    predictor.records = 0
    predictor.last_record = {}
    tensor = SimpleNamespace(shape=(1, 1, 1, 15, 720, 1280))
    tensor.to = lambda device: tensor
    tensor.float = lambda: tensor
    predictor._preprocess = lambda frames: tensor
    predictor._forward = forward
    monkeypatch.setattr(torch.cuda, "synchronize", lambda device: None)
    monkeypatch.setattr(torch, "autocast", lambda **kwargs: nullcontext())
    return predictor, tensor


def test_cuda_retry_keeps_identical_evidence_and_separate_scores(predictors, monkeypatch):
    torch = predictors.torch
    attempts = []
    offence = torch.tensor([0.0, 1.0, 2.0, -1.0])
    action = torch.tensor([3.0] + [0.0] * 7)

    def forward(tensor):
        attempts.append(tensor)
        return offence, action, len(attempts) == 2

    predictor, tensor = _prediction_fixture(predictors, monkeypatch, forward)
    prediction = predictor.predict([])
    assert attempts == [tensor, tensor]
    assert predictor.fp32_retry_count == 1
    assert predictor.last_precision == "fp32_retry"
    assert prediction is not None
    expected_offence = 1 - torch.softmax(offence, dim=-1)[0].item()
    assert prediction.confidence == pytest.approx(expected_offence)
    assert predictor.last_record["offence_score"] == pytest.approx(expected_offence)
    assert predictor.last_record["action_score"] == pytest.approx(
        torch.softmax(action, dim=-1).max().item()
    )
    assert predictor.records == 1


def test_nonfinite_retry_cannot_become_a_candidate_or_negative(predictors, monkeypatch):
    torch = predictors.torch
    predictor, _ = _prediction_fixture(
        predictors, monkeypatch, lambda tensor: (torch.zeros(4), torch.zeros(8), False)
    )
    with pytest.raises(RuntimeError, match="non-finite CUDA output"):
        predictor.predict([])
    assert predictor.fp32_retry_count == 1
    assert predictor.records == 0
    assert predictor.last_record == {}
    assert predictor.errors == ["non_finite_cuda_output_after_fp32_retry"]


def test_no_offence_rejection_preserves_raw_window_scores(predictors, monkeypatch):
    torch = predictors.torch
    predictor, _ = _prediction_fixture(
        predictors, monkeypatch,
        lambda tensor: (torch.tensor([3.0, 0.0, 0.0, 0.0]), torch.zeros(8), True),
    )
    assert predictor.predict([]) is None
    assert predictor.last_record["decision"] == "no_offence"
    assert predictor.records == 1


@pytest.mark.parametrize("fail_import", [False, True])
def test_author_import_restores_vars_module_cache_and_path(
    predictors, monkeypatch, tmp_path, fail_import
):
    module = predictors.multidim
    # These tiny fixtures are original test code, not author implementation.
    (tmp_path / "utils.py").write_text("VALUE = 'isolated-fixture'\n")
    (tmp_path / "multidim_stacker_mod.py").write_text("TOKEN = 'fixture'\n")
    aggregate = "from utils import VALUE\nIMPORTED_VALUE = VALUE\n"
    if fail_import:
        aggregate += "raise RuntimeError('fixture import failed')\n"
    (tmp_path / "mvaggregate.py").write_text(aggregate)
    originals = {
        name: ModuleType(name) for name in ("utils", "multidim_stacker_mod", "mvaggregate")
    }
    for name, value in originals.items():
        monkeypatch.setitem(sys.modules, name, value)
    original_path = list(sys.path)
    if fail_import:
        with pytest.raises(RuntimeError, match="fixture import failed"):
            module._load_author_modules(tmp_path, tmp_path / "dependencies")
    else:
        _, aggregate_module, source_hash = module._load_author_modules(
            tmp_path, tmp_path / "dependencies"
        )
        assert aggregate_module.IMPORTED_VALUE == "isolated-fixture"
        assert len(source_hash) == 64
    assert sys.path == original_path
    assert all(sys.modules[name] is value for name, value in originals.items())


def test_causal_mvit_matches_official_aspect_preserving_transform(predictors):
    np, torch = predictors.np, predictors.torch
    frame = np.zeros((120, 240, 3), dtype=np.uint8)
    frame[:, :70, 0] = 255
    frame[:, 170:, 2] = 255
    predictor = predictors.causal.CausalMViTPredictor.__new__(predictors.causal.CausalMViTPredictor)
    frames = [frame] * 16
    actual = predictor._preprocess(frames)
    rgb = np.stack(frames)[..., ::-1].copy()
    expected = predictors.causal.MViT_V2_S_Weights.KINETICS400_V1.transforms()(
        torch.from_numpy(rgb).permute(0, 3, 1, 2)
    )
    assert tuple(actual.shape) == (1, 1, 3, 16, 224, 224)
    assert torch.equal(actual[0, 0], expected)


@pytest.mark.parametrize("height,width,region,source_shape", [
    (40, 200, [100, 100, 300, 140], (480, 852)),
    (120, 120, [0, 10, 120, 130], (480, 852)),
    (120, 120, [732, 360, 852, 480], (480, 852)),
])
def test_causal_crop_keeps_both_edge_actors_without_stretching(
    predictors, height, width, region, source_shape
):
    np, torch = predictors.np, predictors.torch
    frame = np.full((height, width, 3), 115, dtype=np.uint8)
    frame[height // 4 : 3 * height // 4, :10] = (0, 0, 255)
    frame[height // 4 : 3 * height // 4, -10:] = (255, 0, 0)
    predictor = predictors.causal.CausalMViTPredictor.__new__(predictors.causal.CausalMViTPredictor)
    predictor.window_context = {
        "region_xyxy": region, "source_height": source_shape[0], "source_width": source_shape[1],
    }
    tensor = predictor._preprocess([frame] * 16)
    assert tuple(tensor.shape) == (1, 1, 3, 16, 224, 224)
    padding = predictor.window_context["spatial_padding"]
    assert padding["side"] > max(height, width)
    assert padding["original_height"] == height
    assert padding["original_width"] == width
    rgb = tensor[0, 0, :, 0] * .225 + .45
    red = (rgb[0] > .8) & (rgb[2] < .2)
    blue = (rgb[2] > .8) & (rgb[0] < .2)
    assert red.sum().item() > 20
    assert blue.sum().item() > 20
    # Padding is gray context, and no actor pixels are stretched across it.
    assert torch.allclose(rgb[:, 0, 0], torch.full((3,), 115 / 255), atol=.01)


def test_causal_mvit_refuses_resampling_or_unstable_crop_shapes(predictors):
    np = predictors.np
    predictor = predictors.causal.CausalMViTPredictor.__new__(predictors.causal.CausalMViTPredictor)
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="exactly 16"):
        predictor._preprocess([frame] * 15)
    with pytest.raises(ValueError, match="stable shape"):
        predictor._preprocess([frame] * 15 + [np.zeros((81, 100, 3), dtype=np.uint8)])
