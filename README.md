# RefereeLink Showcase

A focused, four-page workspace for live demonstrations: multi-view refereeing, player tracking and team classification with pitch projection, foul candidates, and football UWB/IMU telemetry. The React interface uses browser-style tabs. All Python services and model inference run on a remote CUDA host; the presentation Mac runs only the frontend.

## Deployment layout

- Remote showcase: `http://<cuda-host>:8010/`. Replace the placeholder with your CUDA host.
- Local frontend: `http://localhost:5174/`. Vite proxies `/api`, `/media`, and `/ws` to the remote service on port 8010.
- Remote user service: configure the paths in `refereelink-showcase.service`, then enable it with `systemctl --user enable --now refereelink-showcase.service`.
- The original device-ingest service remains on remote port 8000. The showcase reuses its camera and phone inputs, prepared cases, and human-review APIs.

The [software validation record](docs/VALIDATION.md) summarizes 12 real CUDA ablation runs across four supplied videos. Full reports, organized data, and output videos remain in the original backend's `docs/night-validation-20261002/` and `assets/validation/20261002/ablation/` directories. These assets are not distributed with this repository; obtain authorized runtime assets separately. Model weights, media, and calibration bundles stay outside Git.

## Four demonstration workflows

1. **Multi-view refereeing:** switch between live inputs and prepared videos at the upper left. Video mode loads five prepared cases and their actual view media. Live mode reads the current ingest, preview, and buffer status of two RTSP cameras and a phone; event capture is available when the required inputs are online. Video review displays every camera together with one synchronized playback clock, per-view attention timelines, source-labeled Grad-CAM focus regions, pitch location editing, complete fact review, and revision-bound rule explanations. Cases are labeled only as Case 1, Case 2, and so on in the localized interface. New model suggestions receive a short, reduced-motion-aware highlight and remain unconfirmed until a reviewer accepts or edits them. CUDA analysis and human-review decisions are displayed separately. Missing input produces an offline state rather than an old frame presented as live. See [the multi-view review guide](docs/MULTIVIEW_REVIEW.md) for the workflow and migration validation.
2. **Player tracking and calibration:** the calibration tab automatically prepares the entire supplied video for a fresh active session, exposes native clickable tracks, collects team and role labels, and validates an independent practice bundle. It reuses the real backend calibration pipeline on CUDA. Practice sessions never activate or overwrite the prepared demonstration bundle. The match tab renders the original video with native detection boxes and a synchronized interactive pitch; select a target to inspect its team, role, field position, and recorded trajectory, or start an independent CUDA run. Unknown or unavailable observations remain explicit. See [the calibration guide](docs/CALIBRATION.md) and [the native tracking guide](docs/NATIVE_TRACKING.md).
3. **Foul detection:** a verified CUDA backend startup queues fresh `mvit-contact-v3` tasks for the two foul clips, provided that a valid private qualification record is available. The native page waits for the current boot's complete results, then loops the original video with frontend SVG contact markers and optional player boxes. Notifications, the timeline, and the inspector reveal candidates at their recorded emission PTS and clear when playback returns to the beginning. Selecting a candidate seeks to the actual interaction time and displays its action, severity, and model scores. Prepared contact markers follow the observed contact frame independently of the later notification. A model candidate is not a final referee decision. See [the detector guide](docs/REALTIME_FOUL.md) and [the native presentation guide](docs/NATIVE_FOUL_PRESENTATION.md).
4. **Football positioning:** configure three anchor coordinates, a tag ID, and UWB/IMU serial ports. The page displays 2D positions and trajectories, with XYZ acceleration waveforms at the lower right. An explicit simulation mode is available before hardware arrives. Simulated UART frames use the same parsers and remain labeled `simulation` with `hardware_verified=false`.

## Repository boundaries

```text
web/                       React / Vite client
server/showcase/           API, media ranges, single-GPU job queue, multi-view gateway
server/showcase/telemetry/ Vendor UART parsers, trilateration, serial lifecycle, WebSocket
backend_core/             Extracted standalone model pipeline and command-line tools
tests/                    Protocol, serial, lifecycle, job, and media-boundary tests
docs/hardware/            Vendor sources, protocols, and software validation
```

`backend_core` retains source attribution and exact commit identifiers. A real job was validated in Python's isolated `-I` mode: all 56 loaded app/tools modules came from the extracted core, with no imports from the temporary night-validation checkout. See [SOURCE.md](backend_core/SOURCE.md) and [VALIDATION.md](backend_core/VALIDATION.md) for provenance and strict checkpoint validation. The original full dashboard, administrator APIs, web application, and device SRT service are not copied into this repository.

## Local frontend

Node.js 22.12 or newer is required. CI uses Node.js 24; see `.nvmrc`.

```bash
cd refereelink-showcase
npm ci
cp web/.env.example web/.env.local
# Set BACKEND_URL in web/.env.local to your remote CUDA showcase service.
npm run dev
```

The default port is 5174. Development requires `BACKEND_URL` in `web/.env.local` or the launch environment. Static builds do not require a backend connection. You can override the remote target by setting `BACKEND_URL` before starting Vite; `web/vite.config.ts` defines the configuration. Client checks such as `npm run lint`, `npm test`, and `npm run build` may run on the Mac. Do not start the Python backend or model inference there.

## Software checks and contributions

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and validation commands. CI checks frontend types, tests, formatting, and builds, together with Linux API/serial software tests and extracted-source hashes. Hosted CI does not perform real GPU inference or physical-device acceptance. See [SECURITY.md](SECURITY.md) for deployment boundaries and private vulnerability reporting.

## Remote backend

On the CUDA Linux host, install Python 3.12 or newer, GPU-compatible PyTorch and TorchVision, FFmpeg/ffprobe, and the project dependencies. Model dependencies are listed in `backend_core/requirements-inference.txt`; the tested environment is recorded in `backend_core/runtime-environment.json`. The upstream VARS implementation and its checkpoint are separate runtime assets and are not redistributed here.

```bash
python -m pip install -e '.[test]'
python -m pip install -r backend_core/requirements-inference.txt
python -m uvicorn showcase.app:app --app-dir server --host 0.0.0.0 --port 8010
```

Run these commands only on the remote CUDA host. Production startup checks `torch.cuda.is_available()` and fails if CUDA is unavailable. Use a single Uvicorn worker: that process owns both serial readers and the GPU queue.

Model subprocesses use the backend's Python executable by default. Set `SHOWCASE_CORE_PYTHON` to use another CUDA environment. Runtime assets default to the repository's `runtime/` directory; existing deployments should explicitly configure their asset paths in the ignored `deploy/showcase.env` file. Configuration variables are listed in [deploy/showcase.env.example](deploy/showcase.env.example), and the user-service template is [deploy/refereelink-showcase.service](deploy/refereelink-showcase.service). Match the paths to your deployment. Example serial-port names are not evidence that hardware has been discovered.

Foul startup preparation is enabled by default through `SHOWCASE_PREPARE_FOUL_ON_STARTUP`. It submits work to the single-GPU queue without waiting for inference to finish before serving HTTP. The native demonstration requires an independently verified, source-bound V3 qualification record; this private runtime asset is not supplied by the repository. An absent or invalid record keeps the native page blocked instead of displaying historical output or selecting another detector. Set the startup flag to `false` to disable automatic preparation. The configurable API default and experimental profiles remain available separately; see [the detector guide](docs/REALTIME_FOUL.md).

Media layout:

```text
runtime/media/input/{calibration,foul-1,foul-2,tracking-projection}.mp4
runtime/media/posters/<clip-id>.jpg
runtime/media/calibration/match-1.npz
runtime/media/prepared/<clip-id>/<mode>/...
runtime/media/jobs/<job-id>/...
runtime/media/artifacts.json
runtime/state/job-<job-id>.json
runtime/calibration-sessions/<session-id>/{session.json,bundle.npz,clips/}
```

The four clip IDs correspond to the supplied calibration video, two foul clips, and the tracking/projection clip. Jobs accept only these allowlisted inputs. Their container frame-count metadata is unreliable; completion checks use the actual decoded counts of 579, 175, 189, and 682 frames, respectively.

## APIs and execution evidence

| Endpoint | Purpose |
| --- | --- |
| GET `/api/health` | Actual CUDA availability and GPU information |
| GET `/api/catalog` | Input clips, prepared cases, and completed artifacts |
| POST `/api/experiments/jobs` | Submit `{kind: tracking / foul / combined, case_id, detection_profile?}`; foul profiles are optional for foul/combined jobs and rejected for tracking-only jobs; returns 202 |
| GET `/api/experiments/jobs[/<id>]` | Persistent job state, frame-based progress, and result summaries |
| GET `/api/experiments/artifacts/<id>` | Videos, reports, candidate NDJSON, and raw data |
| GET `/api/foul/preparation` | Read-only current-boot task IDs, preparation state, and validated result revisions |
| GET `/api/foul/results/<id>[/frames]` | Source-verified native foul descriptor and immutable revision-bound FrameState pages |
| GET `/api/tracking/cases/<id>` | Original media and latest complete source-verified CUDA tracking result |
| POST `/api/tracking/cases/<id>/jobs` | Submit or deduplicate a native tracking CUDA job |
| GET `/api/tracking/results/<id>[/frames]` | Source geometry and immutable revision-bound FrameState pages |
| POST `/api/calibration/sessions` | Create an isolated practice session for the allowlisted calibration source |
| GET `/api/calibration/sessions/<id>` | Persistent session status, labels, validation and revision |
| POST `/api/calibration/sessions/<id>/{prepare,labels,validate,reset}` | Revision-bound practice operations, sharing the tracking CUDA lease |
| GET `/api/calibration/sessions/<id>/{video,metadata}` | Clean review media and native track observations |
| `/api/multiview/*` | Gateway to the corresponding original device-service APIs |
| `/api/telemetry/*`, WS `/ws/telemetry` | Serial, position, and acceleration snapshots |

The queue allows at most three waiting jobs, deduplicates active jobs for the same input/mode and detector snapshot, and uses one sequential experiment worker. Calibration preparation, feature extraction and validation share the same GPU execution lease, preventing overlap with experiment subprocesses. Progress comes from FrameState records already written to disk. Completion requires a genuine CUDA report, correct ablation flags, processing and output of every decoded frame, and no processing-frame drops. Foul jobs must prove actual model forwards or an explicitly recorded, profile-specific empty scan with no supported classification opportunity and no candidates. A complete empty scan does not prove that the foul classifier ran; see [the empty-scan contract](docs/REALTIME_FOUL.md#http-and-saved-result-compatibility). FFprobe then decodes the H.264 output to verify its frame count. A zero exit code alone is insufficient. On restart, unfinished jobs are persisted as failed; shutdown cleans up each complete subprocess group. Completed historical jobs do not unlock a new boot's native foul demonstration.

See [docs/VALIDATION.md](docs/VALIDATION.md) for software and browser evidence, and [docs/hardware/PROTOCOLS.md](docs/hardware/PROTOCOLS.md) for UART and positioning boundaries. STM32 protocol-bridge source is maintained separately in [refereelink-football](https://github.com/refereelink-team/refereelink-football).

## Validation limits

Ablation runs, tracking action buttons, and foul startup preparation perform sequential inference on supplied videos. The native foul page presents startup-prepared results during looping playback; it does not run new inference for each browser playback loop. Playback FPS, startup time, offline inference throughput, and source-paced wall-clock alert latency are separate measurements. A video's 30 fps playback rate does not establish lossless, real-time multi-device operation. Internal leave-one-track-out calibration validation does not establish team-classification accuracy across matches.

At the recorded acceptance session, the physical camera links were unreachable and the phone was not transmitting. Live mode displayed those states. Full three-input acceptance still requires restored connectivity and phone capture. UWB/IMU boards have not arrived, so physical positioning accuracy, installation-coordinate transforms, STM32 board flashing, and wireless range have not been validated.
