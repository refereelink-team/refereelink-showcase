# ruff: noqa: E402
"""CUDA-only, reproducible single-video and batch ablations.

Run on the GPU host. Production InferencePipeline performs detection,
tracking, semantics, projection and foul inference. This runner adds lossless
JSON observability and a review video, without substituting predictions.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.classification.team_calibration.bundle import CalibrationBundle
from app.foul_detection.detector import FoulDetector
from app.foul_detection.predictor import MViTFoulPredictor
from app.foul_detection.causal_predictor import CausalMViTPredictor
from app.foul_detection.temporal import PROFILE_IDS, TemporalFoulDetector, VerificationConfig, EXPERIMENTAL_CONFIG_V2
from app.foul_detection.contact import ContactFoulDetector, CONTACT_VERIFICATION_V3
from app.pipeline.buffer import PipelineMode
from app.pipeline.engine import InferencePipeline
from app.pipeline.source import LocalFileSource
from app.state.store import StateStore
from tools.render_diagnostic_video import _make_contact_sheet, _render_panel
from tools.source_provenance import verified_source_manifest
from app.config.pitch import PITCH_PROFILE_IDS, build_pitch_profile
from app.geometry.sequence_refinement import refine_sequence
from app.state.models import FrameState

MODES = ("foul_only", "projection_only", "combined")
VIDEOS = {
    "calibration": "标定用视频.mp4",
    "foul-1": "实时犯规1.mp4",
    "foul-2": "实时犯规2.mp4",
    "tracking-projection": "跟踪与投影.mp4",
}
SEVERITIES = ("no_offence", "offence_no_card", "yellow_card", "red_card")
ACTIONS = ("Tackle", "Standing Tackle", "High Leg", "Holding", "Pushing", "Elbowing", "Challenge", "Dive")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def line_json(handle, value: object) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    """Publish one completed artifact without exposing a partial JSONL file."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sequence-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _jsonl_bytes(records) -> bytes:
    def numpy_value(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        raise TypeError(f"Unsupported sequence JSON value: {type(value).__name__}")

    return "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                              default=numpy_value)
                   + "\n" for record in records).encode()


def _rerender_sequence(input_path, states, video_raw, pitch_config, fps):
    """Render refined records against the original decoder frames, without inference."""
    capture = cv2.VideoCapture(str(input_path))
    temporary = video_raw.with_name(".sequence-annotated.mp4")
    writer = None
    held_event, hold_until = None, -1
    try:
        if not capture.isOpened():
            raise RuntimeError("Cannot reopen original sequence media")
        for record in states:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Original sequence media ended before refined records")
            state = FrameState.model_validate(record)
            rendered, held_event, hold_until = render(
                frame, state, "projection_only", None, held_event, hold_until, pitch_config,
            )
            if writer is None:
                writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                         (rendered.shape[1], rendered.shape[0]))
                if not writer.isOpened():
                    raise RuntimeError("Cannot open refined sequence video writer")
            writer.write(rendered)
        if capture.read()[0]:
            raise RuntimeError("Original sequence media contains unrepresented frames")
        if writer is None:
            raise RuntimeError("Refined sequence has no source frames")
        writer.release()
        writer = None
        os.replace(temporary, video_raw)
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        temporary.unlink(missing_ok=True)


def _offline_refine(output, input_path, video_raw, history, metadata, config, fps, *, reuse_causal=False):
    """Keep causal artifacts verbatim and publish source-aligned offline geometry."""
    started = time.perf_counter()
    states_path, metrics_path = output / "frame-states.jsonl", output / "frame-metrics.jsonl"
    causal_states_path, causal_metrics_path = output / "causal-frame-states.jsonl", output / "causal-frame-metrics.jsonl"
    causal_states = (causal_states_path if reuse_causal else states_path).read_bytes()
    causal_metrics = (causal_metrics_path if reuse_causal else metrics_path).read_bytes()
    _atomic_bytes(output / "causal-registration-history.jsonl", _jsonl_bytes(history))
    states = [json.loads(line) for line in causal_states.splitlines()]
    metrics = [json.loads(line) for line in causal_metrics.splitlines()]
    from app.geometry.pitch_registration import prepare_offline_corrector
    corrector = prepare_offline_corrector(input_path, history, config)
    result = refine_sequence(states, history, metadata["decoded_timestamps_ms"], config, fps,
                             (metadata["height"], metadata["width"]), corrector=corrector)
    if len(metrics) != len(result.frame_states):
        raise ValueError("Sequence timing records are incomplete")
    for metric, state in zip(metrics, result.frame_states):
        if metric["frame_id"] != state["frame_id"]:
            raise ValueError("Sequence timing frame identities do not align")
        metric.update(homography_status=state["homography_status"],
                      projection_quality=state["projection_quality"], geometry_epoch=state["geometry_epoch"])
    # Validate JSON before changing any artifact, then finish the new video.
    state_bytes, metric_bytes = _jsonl_bytes(result.frame_states), _jsonl_bytes(metrics)
    homography_bytes = _jsonl_bytes([
        {"frame_id": state["frame_id"], "source_pts_ms": metadata["decoded_timestamps_ms"][index],
         "homography": None if matrix is None else matrix.tolist(),
         "quality": state["projection_quality"], "epoch": state["geometry_epoch"]}
        for index, (state, matrix) in enumerate(zip(result.frame_states, result.homographies))
    ])
    _rerender_sequence(input_path, result.frame_states, video_raw, config, fps)
    if not reuse_causal:
        _atomic_bytes(causal_states_path, causal_states)
        _atomic_bytes(causal_metrics_path, causal_metrics)
    _atomic_bytes(states_path, state_bytes)
    _atomic_bytes(metrics_path, metric_bytes)
    _atomic_bytes(output / "sequence-homographies.jsonl", homography_bytes)
    result.summary["postprocess_seconds"] = time.perf_counter() - started
    result.summary["paint_corrector"] = deepcopy(getattr(corrector, "summary", {}))
    return result


def source_metadata(path: Path) -> dict:
    """Count actual OpenCV decoder outputs; never trust container nb_frames."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot decode {path}")
    metadata_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    reported_fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = 0
    timestamps = []
    while True:
        ok, _ = capture.read()
        if not ok:
            break
        frames += 1
        timestamps.append(float(capture.get(cv2.CAP_PROP_POS_MSEC)))
    capture.release()
    positive = np.diff(timestamps)
    positive = positive[positive > 0.01]
    cadence_fps = float(1000.0 / np.median(positive)) if len(positive) else reported_fps
    if not np.isfinite(cadence_fps) or cadence_fps <= 0:
        raise RuntimeError("No valid decoded frame cadence")
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=r_frame_rate,avg_frame_rate,time_base,duration,nb_frames", "-of", "json", str(path),
    ], text=True))["streams"][0]
    numerator, denominator = probe["r_frame_rate"].split("/")
    nominal_fps = float(numerator) / float(denominator)
    # Millisecond PTS quantization makes the median 33ms for 30fps. Use the
    # verified nominal rate when it agrees with the observed cadence.
    output_fps = nominal_fps if abs(nominal_fps / cadence_fps - 1) < 0.05 else (frames - 1) * 1000 / (timestamps[-1] - timestamps[0])
    return {
        "path": str(path), "sha256": sha256(path), "container_frame_count": metadata_count,
        "reference_decoded_frames": frames, "opencv_reported_fps": reported_fps,
        "ffprobe": probe, "nominal_fps": nominal_fps,
        "measured_median_cadence_fps": cadence_fps, "output_fps": output_fps,
        "width": width, "height": height, "decoded_timestamps_ms": timestamps,
    }


class LoggedPredictor(MViTFoulPredictor):
    """Preserve raw output even when production predictor rejects no_offence."""

    def __init__(self, checkpoint_path: str, code_path: str, handle) -> None:
        self.code_path = Path(code_path)
        author_source = hashlib.sha256()
        for path in sorted(self.code_path.rglob("*.py")):
            author_source.update(path.relative_to(self.code_path).as_posix().encode())
            author_source.update(path.read_bytes())
        self.source_sha256 = author_source.hexdigest()
        self.handle = handle
        self.frame_id = 0
        self.last_record = None
        self.records = 0
        self.errors = []
        self.load_state_result = None
        self.media_pts_ms = []
        self.threshold = 0.48
        self.attempts = []
        super().__init__(checkpoint_path, "cuda")
        self._model.register_forward_hook(self._capture_forward)
        if next(self._model.parameters()).device.type != "cuda":
            raise RuntimeError("Foul model is not on CUDA")

    def _resolve_model_dir(self) -> Path:
        return self.code_path

    def _load_model(self, checkpoint_path: str):
        model = super()._load_model(checkpoint_path)
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        state = checkpoint.get("state_dict", checkpoint)
        incompatible = model.load_state_dict(state, strict=True)
        self.load_state_result = {
            "strict": True, "missing_keys": incompatible.missing_keys,
            "unexpected_keys": incompatible.unexpected_keys, "state_tensors": len(state),
        }
        del checkpoint, state
        return model

    def _capture_forward(self, model, inputs, output) -> None:
        off, act, attention = output
        finite = self._finite_output(off, act, attention)
        def preserve(value):
            if isinstance(value, list):
                return [preserve(item) for item in value]
            if isinstance(value, float) and not np.isfinite(value):
                return "NaN" if np.isnan(value) else "Infinity" if value > 0 else "-Infinity"
            return value
        self.attempts.append({
            "precision": "fp16" if torch.is_autocast_enabled("cuda") else "fp32",
            "finite": finite,
            "severity_logits": preserve(off.detach().float().cpu().tolist()),
            "action_logits": preserve(act.detach().float().cpu().tolist()),
            "attention": preserve(attention.detach().float().cpu().tolist()),
        })
        if not finite:
            return
        off_probs = torch.softmax(off.float().reshape(-1, 4), dim=-1)[0]
        act_probs = torch.softmax(act.float().reshape(-1, 8), dim=-1)[0]
        sev_index, act_index = int(off_probs.argmax().item()), int(act_probs.argmax().item())
        self.last_record = {
            "frame_id": self.frame_id, "window_start_frame": max(1, self.frame_id - 23),
            "window_end_frame": self.frame_id, "input_tensor_shape": list(inputs[0].shape),
            "window_start_media_pts_s": self.media_pts_ms[max(0, self.frame_id - 24)] / 1000,
            "window_end_media_pts_s": self.media_pts_ms[self.frame_id - 1] / 1000,
            "threshold": self.threshold,
            "severity_logits": off.detach().float().cpu().tolist(),
            "action_logits": act.detach().float().cpu().tolist(),
            "attention": attention.detach().float().cpu().tolist(),
            "severity_probs": off_probs.detach().cpu().tolist(),
            "action_probs": act_probs.detach().cpu().tolist(),
            "model_decision": SEVERITIES[sev_index], "model_action": ACTIONS[act_index],
            "combined_confidence": float((off_probs[sev_index] + act_probs[act_index]).item() / 2.0),
        }

    def predict(self, frames):
        torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            self.attempts = []
            self.last_record = None
            prediction = super().predict(frames)
            torch.cuda.synchronize()
            self.last_record["synchronized_latency_ms"] = (time.perf_counter() - started) * 1000
            self.last_record["prediction_returned"] = prediction is not None
            self.last_record["above_threshold"] = self.last_record.get("offence_score", self.last_record["combined_confidence"]) >= self.threshold
            self.last_record["suppressed_no_offence"] = self.last_record["model_decision"] == "no_offence"
            self.last_record["precision"] = self.last_precision
            self.last_record["forward_attempts"] = self.attempts
            self.records += 1
            line_json(self.handle, self.last_record)
            self.handle.flush()
            return prediction
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise


class LoggedCausalPredictor(LoggedPredictor, CausalMViTPredictor):
    """Keep raw negative windows while correcting temporal/model preprocessing."""

    def _capture_forward(self, model, inputs, output):
        super()._capture_forward(model, inputs, output)
        if self.last_record is not None:
            self.last_record.update(getattr(self, "window_context", {}))
            self.last_record.update(
                offence_score=1.0 - self.last_record["severity_probs"][0],
                action_score=max(self.last_record["action_probs"]),
                severity_score=max(self.last_record["severity_probs"]),
                model_id=self.model_id,
            )


def label(player) -> str:
    team = {"home": "H", "away": "A", "none": "N", "unknown": "U"}[player.team.value]
    role = {"outfield": "P", "goalkeeper": "GK", "referee": "REF", "unknown": "?", "staff": "STAFF"}[player.role.value]
    return f"{team}-{role} #{player.track_id}"


def text(frame, message: str, xy: tuple[int, int], color=(255, 255, 255), scale=0.46):
    cv2.putText(frame, message, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, message, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def render(frame, state, mode: str, record: dict | None, held_event, hold_until: int, pitch_config):
    annotated = frame.copy()
    for player in state.players:
        color = (0, 215, 255) if player.role.value == "referee" else {"home": (147, 20, 255), "away": (255, 191, 0)}.get(player.team.value, (175, 175, 175))
        x1, y1, x2, y2 = map(int, player.bbox)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 1)
        text(annotated, label(player), (max(0, x1), max(45, y1 - 3)), color, 0.37)
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 40), (28, 28, 28), -1)
    text(annotated, f"{mode} | frame {state.frame_id} | H=HOME A=AWAY GK=goalkeeper REF=referee U=UNKNOWN", (8, 17), scale=0.43)
    latest_events = [event for event in state.events if event.event_type == "foul_candidate"]
    if latest_events:
        held_event, hold_until = latest_events, state.frame_id + 60
    if held_event is not None and state.frame_id < hold_until:
        labels = [f"{(event.foul_details or {}).get('action', '')} {event.confidence:.3f}" for event in held_event]
        text(annotated, f"MODEL CANDIDATES: {', '.join(labels)} | HUMAN REVIEW REQUIRED", (8, 35), (80, 180, 255), 0.44)
        for event in held_event:
            evidence = event.evidence or {}
            region = evidence.get("region_xyxy")
            if region:
                x1, y1, x2, y2 = map(int, region)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (50, 220, 255), 2)
                text(annotated, f"event {evidence['event_time_s']:.2f}s / alert {evidence['emitted_time_s']:.2f}s", (x1, max(55, y1 - 8)), (50, 220, 255), .42)
    elif record is not None:
        text(annotated, f"model: {record.get('model_decision', record.get('decision'))} / {record.get('model_action', record.get('action'))} score={record.get('offence_score', record.get('combined_confidence', 0)):.3f} | model output only", (8, 35), (195, 195, 195), 0.43)
    else:
        text(annotated, "foul model disabled" if mode == "projection_only" else "foul model warming up (24-frame window)", (8, 35), (195, 195, 195), 0.43)
    if mode != "foul_only":
        panel_width = max(420, min(560, frame.shape[1] // 3))
        combined = _render_panel(annotated, state, panel_width, pitch_config=pitch_config)
        panel = combined[:, frame.shape[1]:]
        # Match the production diagnostic panel's pitch coordinate transform.
        padding, pitch_top, pitch_bottom = 18, 160, frame.shape[0] - 28
        scale = max(0.01, min((panel_width - 2 * padding - 2) / pitch_config.length, (pitch_bottom - pitch_top - 2 * padding) / pitch_config.width))
        pitch_w = int(pitch_config.length * scale + 2 * padding)
        pitch_h = int(pitch_config.width * scale + 2 * padding)
        px = max((panel_width - pitch_w) // 2, 0)
        py = pitch_top + max((pitch_bottom - pitch_top - pitch_h) // 2, 0)
        for player in state.players:
            if player.field_x is not None and player.field_y is not None:
                x = int(px + padding + player.field_x * scale)
                y = int(py + padding + player.field_y * scale)
                text(panel, label(player), (min(max(x - 35, 2), panel_width - 90), min(max(y + 17, 178), frame.shape[0] - 5)), scale=0.30)
        return combined, held_event, hold_until
    return annotated, held_event, hold_until


def run_job(args, input_path: Path, mode: str, output: Path) -> dict:
    source_snapshot = verified_source_manifest(ROOT)
    args = deepcopy(args)
    foul_profile = getattr(args, "foul_profile", "legacy-v1")
    causal = foul_profile != "legacy-v1" and mode != "projection_only"
    defaults = CONTACT_VERIFICATION_V3 if foul_profile == "mvit-contact-v3" else EXPERIMENTAL_CONFIG_V2
    if args.threshold is None:
        args.threshold = defaults.offence_threshold if causal else 0.48
    pitch_profile_id = getattr(args, "pitch_profile", "legacy")
    pitch_config, paint_enabled = build_pitch_profile(pitch_profile_id)
    job_started_at = datetime.now(timezone.utc).isoformat()
    if output.exists() and (output / "report.json").exists() and not args.overwrite:
        raise RuntimeError(f"Existing completed output: {output}; use --overwrite explicitly")
    output.mkdir(parents=True, exist_ok=True)
    assert torch.cuda.is_available(), "CUDA required, CPU inference is forbidden"
    torch.set_num_threads(2)
    torch.cuda.reset_peak_memory_stats()
    meta = source_metadata(input_path)
    write_json(output / "source-metadata.json", meta)
    bundle = CalibrationBundle.load(args.bundle)
    assert bundle.team_ready and bundle.goalkeeper_mapping_ready and bundle.validation_report.referee_mapping_ready
    assert len(bundle.prototypes) == 5, "All 5 calibrated roles required"
    store = StateStore()
    source = LocalFileSource(str(input_path), store=store)
    source._fps = meta["output_fps"]
    log = (output / "foul-windows.jsonl").open("w")
    frames_log = (output / "frame-states.jsonl").open("w")
    timings_log = (output / "frame-metrics.jsonl").open("w")
    events_log = (output / "candidate-events.jsonl").open("w")
    predictor = None
    detector = None
    # Capture assets before loading: a later on-disk replacement cannot relabel
    # an already-running model or calibration as another validated experiment.
    asset_snapshot = {name: {"path": path, "sha256": sha256(Path(path))}
                      for name, path in (("player", args.player_model), ("pitch", args.pitch_model),
                                         ("foul", args.multidim_model if foul_profile == "multidim-full-v2" else args.foul_model))}
    bundle_sha256 = sha256(Path(args.bundle))
    load_started = time.perf_counter()
    if mode != "projection_only":
        if foul_profile == "multidim-full-v2":
            from app.foul_detection.multidim import MultiDimStackerPredictor
            predictor = MultiDimStackerPredictor(args.multidim_model, args.multidim_code)
        else:
            predictor_class = LoggedCausalPredictor if causal else LoggedPredictor
            predictor = predictor_class(args.foul_model, args.foul_code, io.StringIO() if causal else log)
        predictor.media_pts_ms = meta["decoded_timestamps_ms"]
        predictor.threshold = args.threshold
        if causal:
            verification_config = VerificationConfig(
                offence_threshold=args.threshold, action_threshold=args.action_threshold if args.action_threshold is not None else defaults.action_threshold,
                confirmation_windows=args.confirmation_windows if args.confirmation_windows is not None else defaults.confirmation_windows,
                evidence_threshold=args.evidence_threshold if args.evidence_threshold is not None else defaults.evidence_threshold,
                post_contact_s=args.post_contact_s if args.post_contact_s is not None else defaults.post_contact_s,
                max_windows_per_tick=args.max_windows_per_tick,
            )
            detector = (ContactFoulDetector(predictor, config=verification_config,
                            record_sink=lambda record: line_json(log, record))
                        if foul_profile == "mvit-contact-v3" else
                        TemporalFoulDetector(predictor, foul_profile, verification_config,
                            record_sink=lambda record: line_json(log, record)))
        else:
            detector = FoulDetector(args.foul_model, device="cuda", predictor=predictor,
                input_fps=source.fps, confidence_threshold=args.threshold, cooldown_frames=25)
    captured = {}
    pipeline = InferencePipeline(source, store, device="cuda", mode=PipelineMode.OFFLINE,
        player_model_path=args.player_model, pitch_model_path=args.pitch_model,
        enable_pitch=mode != "foul_only", pitch_configuration=pitch_config, enable_paint_projection=paint_enabled, camera_calibration_path=None, enable_undistortion=False,
        team_calibration_path=args.bundle, enable_foul_detection=mode != "projection_only",
        foul_detector=detector, foul_checkpoint_path=args.foul_model,
        foul_confidence_threshold=args.threshold, inference_backend="pytorch",
        frame_sink=lambda frame, state: captured.update(frame=frame))
    pipeline._load_models()
    sequence_enabled = pitch_profile_id == "source-informed105" and mode == "projection_only"
    if sequence_enabled:
        pipeline._vision_core._paint_registration.record_history = True
    assert pipeline._vision_core.enable_pitch == (mode != "foul_only")
    if mode != "projection_only":
        assert pipeline._foul_detector is detector and predictor is not None
    warmup_report = None
    if causal:
        # Compile/initialize CUDA kernels before arrival pacing starts. Synthetic
        # frames contain no evidence, and never participate in event confirmation.
        predictor.frame_id = 1
        warmup_started = time.perf_counter()
        predictor.predict([np.zeros((source.frame_height, source.frame_width, 3), dtype=np.uint8)] * predictor.temporal_frames)
        warmup_report = {"seconds": time.perf_counter() - warmup_started,
                         "synthetic": True, "forward_windows": predictor.records,
                         "fp32_retries": predictor.fp32_retry_count}
        vision_started = time.perf_counter()
        synthetic = np.zeros((source.frame_height, source.frame_width, 3), dtype=np.uint8)
        pipeline._vision_core._predict_player(synthetic)
        # Dynamic semantic batches should not repeatedly benchmark CUDA
        # convolutions in the arrival loop. Warm the same learned models with
        # synthetic crops without updating any track or team feature bank.
        torch.backends.cudnn.benchmark = False
        extractors = set()
        warmed_semantic_batches = []
        for classifier in (pipeline._semantic_manager.role_classifier,
                           pipeline._semantic_manager.team_classifier):
            extractor = getattr(classifier, "appearance_extractor", None)
            if extractor is not None and id(extractor) not in extractors:
                extractors.add(id(extractor))
                crop = np.full((128, 64, 3), 115, dtype=np.uint8)
                # CUDA first-use kernel initialization is shape-dependent even
                # with convolution benchmarking disabled. Each extractor can
                # receive any tail batch from 1 to its configured maximum.
                # Initialize every possible shape before accepting arrivals;
                # otherwise a new person count repeatedly stalls a cold run.
                batch_sizes = list(range(1, extractor.batch_size + 1))
                for batch_size in batch_sizes:
                    extractor.extract_batch([crop] * batch_size)
                warmed_semantic_batches.append(batch_sizes)
        torch.cuda.synchronize()
        warmup_report["vision_and_semantics_seconds"] = time.perf_counter() - vision_started
        warmup_report["track_state_updated"] = False
        warmup_report["semantic_batch_sizes"] = warmed_semantic_batches
        predictor.records = 0
        predictor.last_record = None
        predictor.fp32_retry_count = 0
        torch.cuda.reset_peak_memory_stats()
    model_load_seconds = time.perf_counter() - load_started
    video_raw = output / "annotated-raw.mp4"
    video_width = source.frame_width + (max(420, min(560, source.frame_width // 3)) if mode != "foul_only" else 0)
    writer = cv2.VideoWriter(str(video_raw), cv2.VideoWriter_fourcc(*"mp4v"), source.fps, (video_width, source.frame_height))
    assert writer.isOpened()
    pipeline._metrics_start = pipeline._metrics_last = time.monotonic()
    frames = 0
    latencies = []
    roles, teams, statuses, rejection_reasons, track_statuses = Counter(), Counter(), Counter(), Counter(), Counter()
    registration_reasons, registration_sources, geometry_epochs = Counter(), Counter(), Counter()
    registration_quality_frames = geometry_epoch_changes = 0
    previous_geometry_epoch = None
    players_total = projected = unknown_roles = unknown_teams = candidate_count = empty_frames = missing_observations = 0
    short_gap_missing_track_frames = observed_active_track_frames = 0
    held_event, hold_until = None, -1
    started = time.perf_counter()
    replay = bool(getattr(args, "causal_replay", False))
    if causal and replay:
        detector.wall_clock_origin = started
    print(json.dumps({"status": "started", "input": input_path.name, "mode": mode, "device": "cuda", "gpu": torch.cuda.get_device_name(), "expected_frames": meta["reference_decoded_frames"]}), flush=True)
    try:
        while True:
            ok, packet = source.read_packet()
            if not ok or packet is None:
                break
            if predictor:
                predictor.frame_id = source.frame_count
            if causal:
                detector.source_pts_s = meta["decoded_timestamps_ms"][source.frame_count - 1] / 1000
            if replay:
                arrival_due = started + meta["decoded_timestamps_ms"][source.frame_count - 1] / 1000
                if arrival_due > time.perf_counter():
                    time.sleep(arrival_due - time.perf_counter())
            torch.cuda.synchronize()
            frame_start = time.perf_counter()
            state = pipeline._process_frame(packet.image, packet.capture_unix_us / 1000.0, packet)
            torch.cuda.synchronize()
            latency = (time.perf_counter() - frame_start) * 1000
            state.processed_timestamp_ms = time.time() * 1000
            pipeline._metrics_frames += 1
            pipeline._latency_acc_ms += latency
            pipeline._end_to_end_acc_ms += state.processed_timestamp_ms - state.capture_timestamp_ms
            latencies.append(latency)
            frames += 1
            empty_frames += int(not state.players)
            short_gaps = sum(0 < gap <= 6 for gap in pipeline._vision_core._track_missing_frames.values())
            short_gap_missing_track_frames += short_gaps
            observed_active_track_frames += short_gaps + len(state.players)
            statuses[state.homography_status.value] += 1
            if state.projection_quality is not None:
                registration_quality_frames += 1
                reason = state.projection_quality.get("reason")
                if isinstance(reason, str):
                    registration_reasons[reason] += 1
                registration_source = state.projection_quality.get("source")
                if isinstance(registration_source, str):
                    registration_sources[registration_source] += 1
            if state.geometry_epoch is not None:
                geometry_epochs[state.geometry_epoch] += 1
                if (
                    previous_geometry_epoch is not None
                    and state.geometry_epoch != previous_geometry_epoch
                ):
                    geometry_epoch_changes += 1
                previous_geometry_epoch = state.geometry_epoch
            for player in state.players:
                roles[player.role.value] += 1
                teams[player.team.value] += 1
                track_statuses[player.track_status] += 1
                rejection_reasons[player.team_rejection_reason or "accepted"] += 1
                players_total += 1
                projected += int(player.field_x is not None and player.field_y is not None)
                unknown_roles += int(player.role.value == "unknown")
                unknown_teams += int(player.team.value == "unknown")
                missing_observations += int(player.track_status != "detected")
            for event in state.events:
                if event.event_type == "foul_candidate":
                    candidate_count += 1
                    media_time = event.evidence.get("event_time_s", meta["decoded_timestamps_ms"][frames - 1] / 1000)
                    line_json(events_log, {"media_pts_seconds": media_time, "source_frame_id": state.frame_id, "event": event.model_dump(mode="json")})
            line_json(frames_log, state.model_dump(mode="json"))
            line_json(timings_log, {"frame_id": state.frame_id, "source_pts_ms": meta["decoded_timestamps_ms"][frames - 1], "synchronized_pipeline_latency_ms": latency, "processed_timestamp_ms": state.processed_timestamp_ms, "read_to_processed_ms": state.processed_timestamp_ms - state.capture_timestamp_ms, "homography_status": state.homography_status.value, "projection_quality": state.projection_quality, "geometry_epoch": state.geometry_epoch, "tracked_persons": len(state.players), "short_gap_missing_tracks": short_gaps})
            final, held_event, hold_until = render(captured["frame"], state, mode, predictor.last_record if predictor else None, held_event, hold_until, pitch_config)
            writer.write(final)
            if frames % 120 == 0:
                print(json.dumps({"status": "progress", "input": input_path.name, "mode": mode, "frames": frames, "foul_windows": predictor.records if predictor else 0, "candidates": candidate_count}), flush=True)
            if predictor and predictor.errors:
                raise RuntimeError(f"Foul inference error: {predictor.errors}")
        elapsed = time.perf_counter() - started
        pipeline._emit_metrics()
    finally:
        pipeline.stop()
        writer.release()
        for handle in (log, frames_log, timings_log, events_log):
            handle.close()
    assert frames == source.frame_count == meta["reference_decoded_frames"] and frames > 0
    expected_windows = detector.inference_count if causal else 0 if mode == "projection_only" else 1 + (frames - 24) // 8 if frames >= 24 else 0
    assert (predictor.records if predictor else 0) == expected_windows
    if mode == "foul_only":
        assert pipeline._vision_core.pitch_detection_count == 0 and projected == 0
    else:
        assert pipeline._vision_core.pitch_detection_count > 0
    core = pipeline._vision_core
    sequence_summary = {"enabled": False}
    if sequence_enabled:
        causal_counts = {
            "projected_player_observations": projected,
            "homography_status_frames": dict(statuses),
            "source_frames": dict(registration_sources),
            "coordinate_transition_frames": core.paint_coordinate_transitions,
            "elapsed_seconds": elapsed,
        }
        refined = _offline_refine(output, input_path, video_raw, core._paint_registration.records,
                                  meta, pitch_config, source.fps)
        sequence_summary = {**refined.summary, "causal_counts": causal_counts}
        projected = refined.summary["projected_player_observations"]
        statuses = Counter(state["homography_status"] for state in refined.frame_states)
        registration_quality_frames = len(refined.frame_states)
        registration_reasons = Counter(state["projection_quality"]["reason"]
                                       for state in refined.frame_states if state["projection_quality"]["reason"])
        registration_sources = Counter(state["projection_quality"]["source"] for state in refined.frame_states)
        geometry_epochs = Counter(state["geometry_epoch"] for state in refined.frame_states)
        geometry_epoch_changes = sum(a["geometry_epoch"] != b["geometry_epoch"]
                                     for a, b in zip(refined.frame_states, refined.frame_states[1:]))
        elapsed = time.perf_counter() - started
    if causal:
        write_json(output / "interaction-episodes.json", [ep.as_dict() for ep in detector.interactions.episodes.values()])
    summary = {
        "status": "complete", "input": input_path.name, "mode": mode,
        "started_at": job_started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_code": source_snapshot,
        "environment": {"hostname": subprocess.check_output(["hostname"], text=True).strip(), "gpu": torch.cuda.get_device_name(), "device": "cuda", "python": sys.version, "torch": torch.__version__, "cuda_runtime": torch.version.cuda, "opencv": cv2.__version__},
        "source": {key: value for key, value in meta.items() if key != "decoded_timestamps_ms"},
        "config": {"pitch_profile_id": pitch_profile_id, "paint_enabled": core.enable_paint_projection, "pitch_geometry_m": {
            "length_m": pitch_config.length / 100.0, "width_m": pitch_config.width / 100.0,
            "penalty_area_length_m": pitch_config.penalty_box_length / 100.0,
            "penalty_area_width_m": pitch_config.penalty_box_width / 100.0,
            "goal_area_length_m": pitch_config.goal_box_length / 100.0,
            "goal_area_width_m": pitch_config.goal_box_width / 100.0,
            "center_circle_radius_m": pitch_config.centre_circle_radius / 100.0,
            "penalty_spot_distance_m": pitch_config.penalty_spot_distance / 100.0,
            "goal_width_m": 7.32}, "pitch_enabled": mode != "foul_only", "foul_enabled": mode != "projection_only", "undistortion_enabled": False, "imgsz": 640, "pitch_interval": 5, "foul_window": 24, "foul_stride": 8, "foul_tensor_frames": 16, "foul_replicated_single_view_slots": 4, "foul_confidence_threshold": args.threshold, "foul_cooldown_frames": 25},
        "models": asset_snapshot,
        "bundle": {"path": args.bundle, "sha256": bundle_sha256, "match_id": bundle.match_id, "validation": asdict(bundle.validation_report), "prototypes": [f"{team.value}/{role.value}" for team, role in bundle.prototypes]},
        "checkpoint_validation": predictor.load_state_result if predictor else None,
        "decoded_frames": source.frame_count, "processed_frames": frames, "output_frames": frames,
        "dropped_frame_count": meta["reference_decoded_frames"] - frames, "dropped_frame_rate": (meta["reference_decoded_frames"] - frames) / meta["reference_decoded_frames"],
        "model_load_seconds": model_load_seconds, "elapsed_seconds_excluding_model_load": elapsed, "fps_including_render_and_json": frames / elapsed,
        "pipeline_latency_ms": {"mean": float(np.mean(latencies)), "median": float(np.median(latencies)), "p95": float(np.percentile(latencies, 95)), "max": float(max(latencies))},
        "empty_player_frame_rate": empty_frames / frames,
        "player_observations": players_total, "roles": dict(roles), "teams": dict(teams),
        "unknown_role_rate": unknown_roles / max(1, players_total), "unknown_team_rate_all_persons": unknown_teams / max(1, players_total),
        "projected_player_observations": projected, "projected_player_rate": projected / max(1, players_total),
        "homography_status_frames": dict(statuses), "homography_available_frame_rate": (statuses["fresh"] + statuses["reused"]) / frames,
        "projection_registration": {
            "quality_frame_count": registration_quality_frames,
            "quality_reason_frames": dict(registration_reasons),
            "source_frames": dict(registration_sources),
            "geometry_epoch_frames": dict(geometry_epochs),
            "geometry_epoch_change_frames": geometry_epoch_changes,
            "coordinate_transition_frames": sequence_summary.get("coordinate_transition_frame_count", 0) if sequence_enabled else core.paint_coordinate_transitions,
        },
        "sequence_refinement": sequence_summary,
        "track_statuses": dict(track_statuses), "non_detected_observation_rate": missing_observations / max(1, players_total), "team_rejection_reasons": dict(rejection_reasons),
        "short_gap_missing_track_frames": short_gap_missing_track_frames,
        "observed_active_track_frames": observed_active_track_frames,
        "short_gap_missing_track_rate": short_gap_missing_track_frames / max(1, observed_active_track_frames),
        "track_id_switches": core.track_id_switches, "track_id_interruptions": core.track_id_interruptions,
        "track_entity_fragmentations": core.track_entity_fragmentations, "homography_rejections": core.homography_rejections,
        "foul_actual_forward_windows": predictor.records if predictor else 0, "foul_expected_forward_windows": expected_windows,
        "foul_candidates": candidate_count, "foul_errors": predictor.errors if predictor else [],
        "foul_fp32_retry_windows": predictor.fp32_retry_count if predictor else 0,
        "production_metrics": store.metrics.model_dump(mode="json"),
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(), "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "limitations": ["Offline sequential throughput includes JSON/video rendering; it is not real-time stream-drop acceptance.", "Unknown and tracker switch values are algorithm outputs, not manually annotated precision/ID-switch ground truth.", "Match-specific kit calibration was reused unchanged across all four clips; independent-video semantics are not proven by bundle leave-one-track-out validation.", "A single source view is replicated into four VARS slots; these are not four independent camera views.", "Candidates and severity classes are model evidence for human review, not final referee decisions."],
    }
    summary["config"].update(
        detection_profile=foul_profile,
        model_id=predictor.model_id if causal else "mvit-v2-s-vars" if predictor else None,
    )
    summary["model_id"] = summary["config"]["model_id"]
    summary["causal_replay"] = {"enabled": replay, "source_fps": source.fps,
                                "scheduling": "source PTS; sequential arrivals; no drops" if replay else None}
    summary["warmup"] = warmup_report
    summary["config"]["cudnn_benchmark"] = torch.backends.cudnn.benchmark
    if causal:
        summary["config_sha256"] = hashlib.sha256(json.dumps(
            detector.configuration(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        summary["external_source_sha256"] = getattr(predictor, "source_sha256", None)
        summary["config"].update(
            verification=detector.configuration(), foul_window_seconds=predictor.temporal_duration_s,
            foul_tensor_frames=predictor.temporal_frames, foul_target_fps=predictor.temporal_fps,
            foul_window=None, foul_stride=None,
            foul_stride_seconds=detector.config.stride_s, foul_cooldown_frames=None,
            official_preprocessing=True,
            foul_replicated_single_view_slots=1 if foul_profile.startswith("multidim") else 4,
        )
        summary["interaction_candidates"] = detector.interactions.stats["episodes"]
        summary["foul_skip_reason"] = "no_interaction_candidates" if not summary["interaction_candidates"] else None
        if foul_profile == "mvit-contact-v3":
            summary["contact_supported_candidates"] = detector.stats.get("contact_supported_candidates", 0)
            if not detector.records and not summary["contact_supported_candidates"]:
                summary["foul_skip_reason"] = "no_supported_contacts"
        summary["interaction_mechanism"] = {**detector.stats, **detector.interactions.stats}
        summary["model_latency_ms"] = {
            "mean": float(np.mean([r.get("synchronized_latency_ms", 0) for r in detector.records])) if detector.records else None,
            "p95": float(np.percentile([r.get("synchronized_latency_ms", 0) for r in detector.records], 95)) if detector.records else None,
        }
        summary["limitations"].append("Versioned causal profiles are experimental until event/action/latency acceptance passes; no cross-match accuracy is established.")
    final_video = output / "annotated.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_raw), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final_video)], check=True)
    cap = cv2.VideoCapture(str(final_video))
    n_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert n_video == frames
    video_raw.unlink()
    _make_contact_sheet(final_video, output / "contact-sheet.jpg", count=6)
    if sequence_enabled:
        elapsed = time.perf_counter() - started
        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
        summary["elapsed_seconds_excluding_model_load"] = elapsed
        summary["fps_including_render_and_json"] = frames / elapsed
        summary["sequence_refinement"]["timing_scope"] = "pipeline, offline refinement, JSON, rendering, encoding, contact sheet"
    line_json_file = output / "track-lifecycle.json"
    write_json(line_json_file, core.track_lifecycle_events)
    summary["artifacts"] = {"video": str(final_video), "video_sha256": sha256(final_video), "states": str(output / "frame-states.jsonl"), "foul_windows": str(output / "foul-windows.jsonl"), "frame_metrics": str(output / "frame-metrics.jsonl"), "candidate_events": str(output / "candidate-events.jsonl"), "contact_sheet": str(output / "contact-sheet.jpg")}
    if sequence_enabled:
        summary["artifacts"].update(causal_states=str(output / "causal-frame-states.jsonl"),
                                    causal_frame_metrics=str(output / "causal-frame-metrics.jsonl"),
                                    sequence_homographies=str(output / "sequence-homographies.jsonl"),
                                    sequence_homographies_sha256=sha256(output / "sequence-homographies.jsonl"),
                                    causal_registration_history=str(output / "causal-registration-history.jsonl"),
                                    causal_registration_history_sha256=sha256(output / "causal-registration-history.jsonl"))
    write_json(output / "report.json", summary)
    print(json.dumps({"status": "complete", "input": input_path.name, "mode": mode, "frames": frames, "fps": frames / elapsed, "windows": summary["foul_actual_forward_windows"], "candidates": candidate_count}), flush=True)
    del predictor, detector, pipeline, core
    gc.collect()
    torch.cuda.empty_cache()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--player-model", required=True)
    parser.add_argument("--pitch-model", required=True)
    parser.add_argument("--foul-model", required=True)
    parser.add_argument("--foul-code", default=os.environ.get("SC_MVFOUL_CODE_PATH", str(ROOT / "third_party" / "sn-mvfoul" / "VARS model")))
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--foul-profile", choices=PROFILE_IDS, default="legacy-v1")
    parser.add_argument("--action-threshold", type=float)
    parser.add_argument("--confirmation-windows", type=int)
    parser.add_argument("--evidence-threshold", type=float)
    parser.add_argument("--post-contact-s", type=float)
    parser.add_argument("--max-windows-per-tick", type=int, default=2)
    parser.add_argument("--multidim-model")
    parser.add_argument("--multidim-code")
    parser.add_argument("--causal-replay", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--pitch-profile", choices=PITCH_PROFILE_IDS, default="legacy")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.batch:
        assert args.input_dir
        results, errors = [], []
        args.output.mkdir(parents=True, exist_ok=True)
        # Start each clip's projection-only case before the heavier foul path.
        for video_id, name in VIDEOS.items():
            for mode in ("projection_only", "foul_only", "combined"):
                out = args.output / video_id / mode
                try:
                    results.append(run_job(args, args.input_dir / name, mode, out))
                except Exception as exc:
                    error = {"input": name, "mode": mode, "error": f"{type(exc).__name__}: {exc}"}
                    errors.append(error)
                    print(json.dumps({"status": "error", **error}), flush=True)
                    out.mkdir(parents=True, exist_ok=True)
                    write_json(out / "error.json", error)
                    gc.collect()
                    torch.cuda.empty_cache()
                write_json(args.output / "batch-summary.json", {"jobs": results, "errors": errors})
        return 1 if errors else 0
    assert args.input and args.mode, "--input and --mode required without --batch"
    run_job(args, args.input, args.mode, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
