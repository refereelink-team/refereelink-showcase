"""Optional CUDA adapter for the author's single-view MultiDimStacker.

The GPL-3.0 implementation and checkpoint remain external runtime assets.
This adapter invokes that implementation; it does not vendor the network.
The caller samples exactly 15 frames at 25 Hz from source presentation times.
The resulting observed span is 0.56 seconds, or 0.60 seconds of frame support.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
import torch

from app.foul_detection.types import FoulPrediction


_IMPORT_LOCK = threading.RLock()
_ACTIONS = ("Tackle", "Standing Tackle", "High Leg", "Holding", "Pushing", "Elbowing", "Challenge", "Dive")
_SEVERITIES = ("No Offence", "Offence + No Card", "Offence + Yellow Card", "Offence + Red Card")
_DECISIONS = ("no_offence", "offence_no_card", "yellow_card", "red_card")
_CARDS = ("none", "none", "yellow", "red")


def _load_author_modules(source_dir: Path, dependency_dir: Optional[Path]):
    """Import external modules without replacing the VARS module cache."""
    files = ("utils.py", "multidim_stacker_mod.py", "mvaggregate.py")
    for name in files:
        if not (source_dir / name).is_file():
            raise FileNotFoundError(f"Author MultiDimStacker source is missing: {source_dir / name}")
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode())
        digest.update((source_dir / name).read_bytes())
    source_sha256 = digest.hexdigest()
    namespace = f"_refereelink_multidim_{source_sha256[:12]}"
    with _IMPORT_LOCK:
        original_path = list(sys.path)
        aliases = ("utils", "multidim_stacker_mod", "mvaggregate")
        saved = {name: sys.modules.get(name) for name in aliases}
        modules: dict[str, Any] = {}
        try:
            if dependency_dir is not None:
                sys.path.insert(0, str(dependency_dir))
            for filename in files:
                alias = filename.removesuffix(".py")
                module_name = f"{namespace}.{alias}"
                module = sys.modules.get(module_name)
                if module is None:
                    spec = importlib.util.spec_from_file_location(module_name, source_dir / filename)
                    if spec is None or spec.loader is None:
                        raise ImportError(f"Cannot load external author module: {filename}")
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    # The author's modules use absolute imports for utilities.
                    sys.modules[alias] = module
                    spec.loader.exec_module(module)
                else:
                    sys.modules[alias] = module
                modules[alias] = module
        finally:
            sys.path[:] = original_path
            for alias, module in saved.items():
                if module is None:
                    sys.modules.pop(alias, None)
                else:
                    sys.modules[alias] = module
    return modules["multidim_stacker_mod"], modules["mvaggregate"], source_sha256


class _AuthorNetwork(torch.nn.Module):
    """Compose the author's public classes with checkpoint-compatible names."""

    def __init__(self, stacker_module, aggregate_module) -> None:
        super().__init__()
        # The final checkpoint includes the entire backbone. Disabling its
        # ImageNet initialization avoids an unrelated download before strict
        # loading; it does not change the trained architecture or parameters.
        backbone = stacker_module.MultiDimStacker(
            model_name="tf_efficientnetv2_b0.in1k",
            num_3d_stack_proj=256,
            num_classes=15,
            drop_rate=0.3,
            drop_path_rate=0.6,
            pretrained=False,
        )
        backbone.fc = torch.nn.Sequential()
        self.mvnetwork = aggregate_module.MVAggregate(
            model=backbone,
            agr_type="multidim_stacking",
            feat_dim=1280,
            drop_rate=0.3,
        )

    def forward(self, tensor):
        return self.mvnetwork(tensor)


class MultiDimStackerPredictor:
    """Strictly loaded, single-view author checkpoint with official flip TTA."""

    model_id = "multidim-stacker"
    input_frames = 15
    temporal_frames = 15
    target_fps = 25.0
    temporal_fps = 25.0
    temporal_duration_s = 0.60
    observed_span_s = 0.56

    def __init__(
        self,
        checkpoint_path: str,
        source_dir: str,
        *,
        device: str = "cuda",
        dependency_dir: Optional[str] = None,
        test_time_flip: bool = True,
    ) -> None:
        self.device = torch.device(device)
        if self.device.type != "cuda" or not torch.cuda.is_available():
            raise RuntimeError("MultiDimStacker comparison requires an available CUDA device")
        self.checkpoint_path = str(Path(checkpoint_path).resolve())
        self.source_dir = str(Path(source_dir).resolve())
        self.test_time_flip = bool(test_time_flip)
        dependency = dependency_dir or os.environ.get("MULTIDIM_DEPENDENCY_DIR")
        if not dependency:
            adjacent = Path(self.source_dir).parent / "python-deps"
            if adjacent.is_dir():
                dependency = str(adjacent)
        self.dependency_dir = str(Path(dependency).resolve()) if dependency else None
        path = Path(self.checkpoint_path)
        if not path.is_file():
            raise FileNotFoundError(f"Author MultiDimStacker checkpoint is missing: {path}")
        self.checkpoint_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        stacker, aggregate, self.source_sha256 = _load_author_modules(
            Path(self.source_dir), Path(self.dependency_dir) if self.dependency_dir else None
        )
        self._model = _AuthorNetwork(stacker, aggregate)
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        state = checkpoint.get("state_dict", checkpoint) if isinstance(checkpoint, dict) else checkpoint
        if not isinstance(state, dict) or not state:
            raise ValueError("MultiDimStacker checkpoint has no state dictionary")
        load_result = self._model.load_state_dict(state, strict=True)
        self.strict_loaded_tensors = len(state)
        self.load_state_result = {
            "strict": True,
            "missing_keys": list(load_result.missing_keys),
            "unexpected_keys": list(load_result.unexpected_keys),
            "loaded_tensors": self.strict_loaded_tensors,
            "state_tensors": self.strict_loaded_tensors,
        }
        self._model.to(self.device).eval()
        self.last_inference_ms = 0.0
        self.fp32_retry_count = 0
        self.last_precision = "fp32"
        self.last_record: dict[str, Any] = {}
        self.records = 0
        self.errors: list[str] = []

    @staticmethod
    def _preprocess(frames: list[np.ndarray]) -> torch.Tensor:
        """Match author decode size, grayscale scaling and center crop."""
        if len(frames) != 15:
            raise ValueError("MultiDimStacker requires exactly 15 PTS-sampled frames at 25 Hz")
        images = []
        # The author uses a 16:9 decode aspect ratio and rounds 726*1280/720.
        height, width = 726, round(726 * 1280 / 720)
        top, left = round((height - 720) / 2), round((width - 1280) / 2)
        for frame in frames:
            image = np.asarray(frame)
            if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                raise ValueError("MultiDimStacker input must contain uint8 BGR frames")
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            images.append(gray[top : top + 720, left : left + 1280])
        array = np.stack(images).astype(np.float32) / 255.0
        # One batch, one live view, one grayscale channel, 15 temporal samples.
        return torch.from_numpy(array).unsqueeze(0).unsqueeze(0).unsqueeze(0)

    def _forward(self, tensor):
        offence, action, features = self._model(tensor)
        outputs = [offence, action, features]
        if self.test_time_flip:
            flipped_offence, flipped_action, flipped_features = self._model(torch.flip(tensor, dims=[-1]))
            outputs.extend((flipped_offence, flipped_action, flipped_features))
            offence = (offence + flipped_offence) / 2.0
            action = (action + flipped_action) / 2.0
            outputs.extend((offence, action))
        finite = all(value is None or bool(torch.isfinite(value).all()) for value in outputs)
        return offence, action, finite

    def predict(self, frames: list[np.ndarray]) -> Optional[FoulPrediction]:
        tensor = self._preprocess(frames).to(self.device)
        torch.cuda.synchronize(self.device)
        started = time.perf_counter()
        with torch.inference_mode():
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                offence_logits, action_logits, finite = self._forward(tensor)
            self.last_precision = "fp16"
            if not finite:
                self.fp32_retry_count += 1
                with torch.autocast(device_type="cuda", enabled=False):
                    offence_logits, action_logits, finite = self._forward(tensor.float())
                self.last_precision = "fp32_retry"
            torch.cuda.synchronize(self.device)
        self.last_inference_ms = (time.perf_counter() - started) * 1000.0
        if not finite:
            self.errors.append("non_finite_cuda_output_after_fp32_retry")
            raise RuntimeError("MultiDimStacker produced non-finite CUDA output after identical FP32 retry")
        offence_logits = offence_logits.float().reshape(-1, 4)[0]
        action_logits = action_logits.float().reshape(-1, 8)[0]
        severity_probs = torch.softmax(offence_logits, dim=-1)
        action_probs = torch.softmax(action_logits, dim=-1)
        severity_index = int(severity_probs.argmax().item())
        action_index = int(action_probs.argmax().item())
        offence_score = float(1.0 - severity_probs[0].item())
        self.last_record = {
            "model_id": self.model_id,
            "input_shape": list(tensor.shape),
            "input_frames": self.input_frames,
            "target_fps": self.target_fps,
            "temporal_duration_s": self.temporal_duration_s,
            "test_time_flip": self.test_time_flip,
            "severity_logits": offence_logits.cpu().tolist(),
            "action_logits": action_logits.cpu().tolist(),
            "severity_probs": severity_probs.cpu().tolist(),
            "action_probs": action_probs.cpu().tolist(),
            "offence_score": offence_score,
            "severity_score": float(severity_probs[severity_index].item()),
            "action_score": float(action_probs[action_index].item()),
            "action": _ACTIONS[action_index],
            "severity": _SEVERITIES[severity_index],
            "decision": _DECISIONS[severity_index],
            "precision": self.last_precision,
            "latency_ms": self.last_inference_ms,
        }
        self.records += 1
        if severity_index == 0:
            return None
        return FoulPrediction(
            confidence=offence_score,
            action=_ACTIONS[action_index],
            severity=_SEVERITIES[severity_index],
            card=_CARDS[severity_index],
            decision=_DECISIONS[severity_index],
            raw_scores=dict(self.last_record),
        )


__all__ = ["MultiDimStackerPredictor"]
