# Standalone inference core provenance

This directory contains the dependency closure of `tools/run_night_ablation.py`
and its diagnostic renderer. It was extracted by static AST import analysis from
RefereeLink Backend and verified by a separate real CUDA job on AstraForge.

Upstream repository: https://github.com/refereelink-team/refereelink-backend

- Main: `57a567b5056b00554d60aaa07f6cdc7acad07a4b`.
- PR #72 (foul candidates): `e929072eabb8403cb52c386da8907993d14612fc`.
- PR #82 (projection): `e20c21fc7406dd14bf50a8aeaf992fa1f8054aeb`.
- Local integration: `1cd187a7eac9459864a02179eccb295599de9b30`.
- Subsequent production corrections: preserve all manual calibration labels while
  excluding weak training tracks; explicit pitch ablation flag; finite-output
  validation and identical-model CUDA FP32 retry for VARS FP16 numeric overflow.

`source-manifest.json` records every copied file's original and extracted SHA-256,
the dependency edges, upstream runner SHA, and the integration patch SHA. Original
extraction preserved production algorithm bytes. Subsequent local production
corrections are recorded separately from the original source identities and retain
verifiable current hashes in the manifest. Two package initializers omit unrelated
service exports (`app.field_ingest`, `app.services`), and `tools/__init__.py` is added.
No original server/API/admin routes, frontend, multi-view review service, model
weights, calibration files, user videos, tokens, or transport session data are bundled.

Keep `LICENSE` and `NOTICE.md` when redistributing this extracted code. They preserve
the upstream Roboflow MIT lineage. External SoccerNet/VARS code, checkpoints,
Ultralytics weights, and user media remain separate assets under their own terms;
these notices grant no redistribution rights to those assets.

## Runtime

All Python execution and inference belong on the remote CUDA host. The runner
refuses CPU inference. Install the direct dependencies in
`requirements-inference.txt` using a CUDA-compatible PyTorch index/runtime; the
tested package versions are recorded in `runtime-environment.json`. FFmpeg and
ffprobe must be on PATH.

Launch the independent entry point from this directory:

```bash
/path/to/cuda-venv/bin/python tools/run_night_ablation.py \
  --input /path/to/video.mp4 --mode projection_only \
  --output /path/to/new-output-directory --bundle /path/to/match-1.npz \
  --player-model /path/to/yolo11s.pt \
  --pitch-model /path/to/football-pitch-detection.pt \
  --foul-model /path/to/mvfoul.pth.tar \
  --foul-code '/path/to/sn-mvfoul/VARS model'
```

Modes are `projection_only`, `foul_only`, and `combined`. Foul modes use an authorized
external VARS source directory provided through `--foul-code` or
`SC_MVFOUL_CODE_PATH`; the file may be named `mvfoul.pth.tar` while the upstream default
name is `14_model.pth.tar`. The exact checkpoint SHA, strict key/shape verification,
all raw model outputs, and numeric retry attempts are preserved in each job report.

`--output` names one job directory. Completed outputs require an explicit
`--overwrite` to replace them. Videos are `annotated.mp4`, reports are `report.json`,
and original FrameState plus per-window scores are saved as JSONL. The showcase server
owns job scheduling and asset registration; it can invoke this CLI without importing
the original backend service. No temporary night-source checkout is required.

## Source-byte preservation

Extracted Python files retain their original line endings and terminal blank lines so their recorded SHA-256 provenance remains verifiable. `.gitattributes` disables line-ending conversion for these files and recognizes their historical whitespace. New repository-owned code follows `.editorconfig`; do not reformat extracted files without deliberately updating provenance and validating the change.

## Isolated calibration and geometry corrections

The showcase reuses the extracted team-calibration services with independent session-owned
paths. A local correction adds an opt-in clean review video, verified source FPS,
and cooperative cancellation to `app/classification/team_calibration/clip.py`;
legacy callers retain their existing defaults.
The clean clip prevents rendered IDs and boxes from entering jersey feature extraction.

Geometry changes are maintained separately from the original PR identities. The manifest
records their original source hashes, current extracted hashes, and local modification
descriptions. The canonical backend checkout may be older than the integrated extracted
core; it must not be overwritten wholesale to imitate source equality.

The new `app/geometry/pitch_lines.py` is a post-extraction addition, with no
invented original-source identity. Source-bound geometry profiles, isolated
field-marking registration, explicit coordinate-transition gaps, and per-result
geometry recording are local changes. `camera.py` adds a separate paint-only
similarity estimator; the inherited model estimator behavior remains unchanged,
although the file bytes have changed. Pipeline and renderer extensions explicitly
pass the selected configuration. Default and unknown inputs retain legacy
behavior. See [Projection corrections](../docs/PROJECTION_CORRECTIONS.md) for
measurements and limitations.
