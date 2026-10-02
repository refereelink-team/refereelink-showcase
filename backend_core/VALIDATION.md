# Extracted-core CUDA verification — 2026-10-02

The extracted core passed one independent real CUDA job on AstraForge:
`foul-2`, `projection_only`, using the validated five-class match calibration.
The existing 12 ablation jobs were not rerun or modified.

- Python ran with `-I` isolated mode, ignoring external PYTHONPATH/user-site entries.
- All 57 copied Python files matched the extracted SHA-256 manifest.
- All 56 imported `app`/`tools` modules resolved inside this extracted core.
- No night-source modules, original backend server/API, field ingest service,
  or original WebSocket publisher were imported.
- RTX 5060 Ti CUDA: 189 decoded/processed frames, 189 original FrameState records,
  zero frame drops, 38 pitch-model calls and 1,768 projected person observations.
- The resulting H.264 video decoded to 189 frames, 1272×480, 30 fps, 6.300 seconds.
- Foul inference was deliberately disabled for this extraction smoke test. Its
  production predictor is byte-identical to the previously validated corrected
  implementation; this smoke test does not independently revalidate its checkpoint.

Machine-readable results and imported relative module paths are in
`validation-summary.json`. Raw evidence belongs under the showcase runtime directory
`runtime/core-validation/foul-2-projection-only`, outside version control.
The validator in that runtime run used the new core and showcase-owned input/bundle
paths; the previous temporary night source was not required.

Authorized weights, cached MobileNet features, and external VARS source remain
runtime assets. The remote installation has a runtime-only `third_party/sn-mvfoul`
symlink to its external asset directory so the default foul-code location resolves;
other deployments can pass `--foul-code` explicitly. External assets are not bundled.
