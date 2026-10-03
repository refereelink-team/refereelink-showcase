"""Official-preprocessing VARS control for causal single-source experiments.

This adapter does not change the legacy predictor or multi-view adjudication.
Duplicated aggregation slots are explicitly recorded as one source, not cameras.
"""

from __future__ import annotations

import numpy as np
import torch
from math import ceil
from torchvision.models.video import MViT_V2_S_Weights

from app.foul_detection.predictor import MViTFoulPredictor


class CausalMViTPredictor(MViTFoulPredictor):
    """Use the author's transform and an independent offence score."""

    model_id = "mvit-v2-s-vars"
    temporal_duration_s = 0.96
    temporal_frames = 16
    temporal_fps = 17.0

    def _preprocess(self, frames: list[np.ndarray]) -> torch.Tensor:
        if len(frames) != self.temporal_frames:
            raise ValueError("Causal VARS requires exactly 16 PTS-sampled frames")
        if any(frame.shape != frames[0].shape for frame in frames):
            raise ValueError("A temporal crop must have one stable shape")
        context = getattr(self, "window_context", {})
        region = context.get("region_xyxy")
        height, width = frames[0].shape[:2]
        touches_border = bool(region and (
            region[0] <= 0 or region[1] <= 0
            or region[2] >= context.get("source_width", float("inf"))
            or region[3] >= context.get("source_height", float("inf"))))
        if region and (height != width or touches_border):
            # Experimental ROI padding preserves both actors even after the
            # official center crop. Constant padding adds no invented players.
            side = ceil(max(height, width) * 256 / 224)
            top, left = (side - height) // 2, (side - width) // 2
            padded = []
            for frame in frames:
                image = np.full((side, side, 3), 115, dtype=np.uint8)
                image[top:top + height, left:left + width] = frame[..., :3]
                padded.append(image)
            frames = padded
            context["spatial_padding"] = {"side": side, "top": top, "left": left,
                                          "original_height": height, "original_width": width}
        array = np.stack(frames)[..., :3][..., ::-1].copy()
        tensor = torch.from_numpy(array).permute(0, 3, 1, 2)
        # The official weights resize the short edge to 256, center crop to
        # 224 and normalize with Kinetics statistics. Never stretch to 224.
        tensor = MViT_V2_S_Weights.KINETICS400_V1.transforms()(tensor)
        return tensor.unsqueeze(0).unsqueeze(0)

    def predict(self, frames):
        prediction = super().predict(frames)
        if prediction is None:
            return None
        severity = prediction.raw_scores["severity_probs"]
        action = prediction.raw_scores["action_probs"]
        prediction.confidence = 1.0 - float(severity[0])
        prediction.raw_scores.update(
            offence_score=prediction.confidence,
            action_score=float(max(action)),
            severity_score=float(max(severity)),
            model_id=self.model_id,
        )
        return prediction
