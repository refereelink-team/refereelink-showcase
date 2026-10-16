# RefereeLink Showcase

A focused, four-page workspace for live demonstrations: multi-view refereeing, player tracking and team classification with pitch projection, foul candidates, and football UWB/IMU telemetry. The React interface uses browser-style tabs. The default deployment runs both frontend and Python/CUDA backend on **one PC in WSL2 Ubuntu**, with the UI opened in the Windows browser. A remote Linux CUDA backend remains optional; no cloud host or Mac is required.

## Deployment layout

- Local showcase: port **8010**, bound to loopback by default. After building the frontend, this backend serves both the UI and API.
- Optional frontend development server: port **5174**, in the same WSL instance. Vite proxies `/api`, `/media`, and `/ws` to `http://127.0.0.1:8010` unless `BACKEND_URL` explicitly overrides it.
- USB: A1's USB-to-UART adapter and the IMU data cable plug into the same PC, then attach to WSL via `usbipd-win`. The backend uses Linux `/dev/…` paths, not Windows COM names. A2/A3 and the ball-internal tag only need power.
- The original multi-view/device-ingest service is a **separate dependency** on local port **8000**. Run it locally for camera/phone inputs, prepared multi-view cases and review APIs. Its full server is not bundled here; there is no automatic cloud fallback.
- Optional user service: edit the checkout paths in `deploy/refereelink-showcase.service` before enabling it. WSL systemd is not required for foreground startup.

Follow the self-contained [single-PC WSL2 CUDA setup and USB checklist](docs/LOCAL_WSL.md). CUDA-enabled PyTorch, authorized weights, media, calibration and external model code must also be installed on the PC; moving only the Git checkout is insufficient. Real-model startup still refuses unavailable CUDA and never silently falls back to CPU.

The [software validation record](docs/VALIDATION.md) summarizes 12 real CUDA ablation runs across four supplied videos. Full reports, organized data, and output videos remain in the original backend's `docs/night-validation-20261002/` and `assets/validation/20261002/ablation/` directories. These assets are not distributed with this repository; obtain authorized runtime assets separately. Model weights, media, and calibration bundles stay outside Git.

## Case video download

The six paired-camera field cases are distributed as a separate Google Drive ZIP. Download it to `runtime/packages/` and run `unzip -o runtime/packages/refereelink-showcase-field-cases-20261005-v1.zip` from the showcase repository root. See [the download and restore guide](docs/MULTIVIEW_ASSETS.md) for the Drive link, exact destination paths, asset scope and checksum verification. Videos and archives stay outside Git.

## Four demonstration workflows

1. **Multi-view refereeing:** switch between live inputs and prepared videos at the upper left. Video mode displays three selected paired-camera field cases (`实地测试案例1–3`) and five existing video tests (`视频测试案例1–5`) with their actual view media. Original field cases 1, 2 and 5 are displayed as 1, 2 and 3; original cases 3, 4 and 6 are hidden in the frontend. Original IDs and media archive paths remain stable. Live mode reads the current ingest, preview, and buffer status of two RTSP cameras and a phone; event capture is available when the required inputs are online. Video review displays every camera together with one synchronized playback clock, per-view attention timelines, source-labeled Grad-CAM focus regions, pitch location editing, guided fact selection, and rule explanations. This is a transient case demo: no review-status badges, filters, saving, archiving or history. Every case switch clears model results, manual choices and the guide step; returning to a case starts fresh and does not restore a saved judgment. Case titles come from the catalog and remain stable. New model suggestions receive a short, reduced-motion-aware highlight and remain unconfirmed until a reviewer accepts or edits them. CUDA analysis and human-review decisions are displayed separately. Missing input produces an offline state rather than an old frame presented as live. See [the multi-view review guide](docs/MULTIVIEW_REVIEW.md) for the workflow and migration validation.
2. **Player tracking and calibration:** the calibration tab automatically prepares the entire supplied video for a fresh active session, exposes native clickable tracks, collects team and role labels, and validates an independent practice bundle. It reuses the real backend calibration pipeline on CUDA. Practice sessions never activate or overwrite the prepared demonstration bundle. The match tab renders the original video with native detection boxes and a synchronized interactive pitch; select a target to inspect its team, role, field position, and recorded trajectory, or start an independent CUDA run. Unknown or unavailable observations remain explicit. See [the calibration guide](docs/CALIBRATION.md) and [the native tracking guide](docs/NATIVE_TRACKING.md).
3. **Foul detection:** a verified CUDA backend startup queues fresh `mvit-contact-v3` tasks for the two foul clips, provided that a valid private qualification record is available. The native page waits for the current boot's complete results, then loops the original video with frontend SVG contact markers and optional player boxes. Notifications, the timeline, and the inspector reveal candidates at their recorded emission PTS and clear when playback returns to the beginning. Selecting a candidate seeks to the actual interaction time and displays its action, severity, and model scores. Prepared contact markers follow the observed contact frame independently of the later notification. A model candidate is not a final referee decision. See [the detector guide](docs/REALTIME_FOUL.md) and [the native presentation guide](docs/NATIVE_FOUL_PRESENTATION.md).
4. **Football positioning:** configure three anchor coordinates, a tag ID, and UWB/IMU serial ports. The page displays 2D positions and trajectories, with XYZ acceleration waveforms at the lower right. An explicit simulation mode is available before hardware arrives. Simulated UART frames use the same parsers and remain labeled `simulation` with `hardware_verified=false`.

For BP-TWR-30 firmware selection, wiring, Linux backend serial placement and physical acceptance, follow the [TWR-30 setup guide](docs/hardware/TWR30_SETUP.md). UWB-only operation does not require an IMU port.
For the current Yahboom 6/9/10-axis module, follow the [IMU setup guide](docs/hardware/IMU_SETUP.md). Only sensor-axis acceleration including gravity is displayed; attitude and IMU/UWB fusion are not implemented.

The device panel supports two explicit IMU transports: independent USB (default) and experimental tag-STM32/UWB bridging via A1's single UART. The host parser and portable C codec are implemented; the required tag/A1 firmware integration and flashable HEX are **not**. Stock Location firmware cannot forward IMU. See the [wiring, custom protocol and staged bridge plan](docs/hardware/IMU_UWB_BRIDGE.md). Disconnect before changing transport; simulation remains separate.

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

## Local startup (WSL2 Ubuntu)

Node.js 22.12 or newer is required. CI uses Node.js 24; see `.nvmrc`.

```bash
cd refereelink-showcase
# First prepare .venv with Python 3.12+, CUDA-enabled PyTorch and the runtime
# dependencies/assets described in docs/LOCAL_WSL.md.
npm ci
npm run build
npm run start:backend
```

The startup command uses `.venv/bin/python`, or an absolute interpreter path in `SHOWCASE_PYTHON`, and optionally loads the ignored `deploy/showcase.env`. Relative runtime defaults follow the checkout, not `/srv` or a previous cloud machine. Open the local service in the Windows browser via WSL localhost forwarding. This is the shortest presentation path: one backend process serves the built frontend too.

For frontend development, run `npm run dev` in a second WSL terminal. No environment file is required: Vite defaults to the local backend. Remove or update old remote `BACKEND_URL`/`LIVE_BACKEND_URL` entries in `web/.env.local` or the shell; explicit overrides still win. To keep a remote deployment, set `BACKEND_URL` to its address. Client-only checks may still run on macOS; macOS backend/CUDA inference is not supported by this deployment.

## Software checks and contributions

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and validation commands. CI checks frontend types, tests, formatting, and builds, together with Linux API/serial software tests and extracted-source hashes. Hosted CI does not perform real GPU inference or physical-device acceptance. See [SECURITY.md](SECURITY.md) for deployment boundaries and private vulnerability reporting.

## CUDA backend (local by default)

On the CUDA Linux host, install Python 3.12 or newer, GPU-compatible PyTorch and TorchVision, FFmpeg/ffprobe, and the project dependencies. Model dependencies are listed in `backend_core/requirements-inference.txt`; the tested environment is recorded in `backend_core/runtime-environment.json`. The upstream VARS implementation and its checkpoint are separate runtime assets and are not redistributed here.

```bash
python -m pip install -e '.[test]'
python -m pip install -r backend_core/requirements-inference.txt
python -m uvicorn showcase.app:app --app-dir server --host 127.0.0.1 --port 8010
```

Run these commands on the backend's Linux CUDA host, by default the local PC's WSL2 Ubuntu. Production startup checks `torch.cuda.is_available()` and fails if CUDA is unavailable. Use a single Uvicorn worker: that process owns both serial readers and the GPU queue. The manual Uvicorn command does not load `deploy/showcase.env`; use `npm run start:backend` to load that optional configuration automatically.

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

手机有线 RTSP 接线、现场采集代理与软件验证边界见 [WIRED_RTSP.md](docs/WIRED_RTSP.md)。
