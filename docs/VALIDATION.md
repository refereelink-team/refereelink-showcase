# Software integration and publication validation

Validation date: **2026-10-02** (Asia/Taipei). Model inference, backend execution, UART protocol tests and C compilation were performed on a remote Linux CUDA host. The presentation Mac ran the Node/Vite client, browser checks, file editing and media playback. Host addresses, personal paths and deployment credentials are excluded from this public record.

This document separates recorded software results, historical deployment observations and pending physical-device acceptance. Raw recordings, calibration bundles, model checkpoints, logs and device snapshots remain outside this repository.

## Publication checks

The initial-publication source passed these checks:

| Check | Observed result | Scope |
| --- | --- | --- |
| Frontend types, unit tests, format and production build | Passed; 6 unit tests | Client errors, cancellation, media URL routing, hardware state and candidate media timestamps |
| Full backend suite on remote CUDA Linux | 33 passed | API, media boundaries, job lifecycle/process cleanup, configuration, telemetry and an actual CUDA startup check |
| API suite in a fresh remote Python 3.12 environment | 32 passed; 1 CUDA test deselected | Same dependency installation as hosted CI, without PyTorch or model dependencies |
| Ruff E4/E7/E9/F | Passed for `server` and `tests` | Static runtime-error checks |
| Extracted-core provenance | All 57 SHA-256 values matched | No algorithm file changed during publication preparation |
| Portable football gateway | Strict C11 build, both C assertion suites and 4 bridge tests passed | Parser, HAL stub, application example and binary transport |
| AddressSanitizer and UndefinedBehaviorSanitizer | Both football C suites passed | Exercised host memory and undefined-behavior checks; no actual MCU |

The Python suites emitted one Starlette/httpx deprecation warning. It did not affect these results; future dependency upgrades should revalidate TestClient behavior.

The public source uses configurable remote addresses, repository-relative asset defaults and example environment files. Existing deployments can retain asset paths through private overrides. Development proxies continue to target the configured remote backend. Model inputs and algorithms are unchanged. Multi-view media routing uses the API namespace rather than a fixed host address.

GitHub CI runs the frontend checks, Linux software-only API suite, core-hash verification and football software/sanitizer checks. Hosted CI does not claim GPU inference or physical-device acceptance. Exact commands and prerequisites are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Recorded model and integration results

The following results were obtained before publication. Evidence paths below are relative to the original backend workspace; those private runtime assets are not included in this repository.

| Scope | Recorded result | Evidence location |
| --- | --- | --- |
| Four user videos, three ablation modes | 12/12 completed; 4,875 frame-state records, 386 model windows, 402 CUDA forwards, 16 FP32 retries | `docs/night-validation-20261002/REPORT.zh-CN.md`, manifest and summary CSV |
| Rendered media | All 12 H.264 files existed and matched the CUDA report video hashes | `assets/validation/20261002/ablation/` |
| Independent extracted core | 57 source files; all 56 imported core modules resolved inside the new core; zero temporary night-source imports; 189/189 frames and 38 pitch-model calls | Public `backend_core/validation-summary.json` and `backend_core/VALIDATION.md` |
| Earlier backend regression run | 31 passed, with the same environment deprecation warning | `assets/validation/20261002/showcase-backend-tests/` |
| Service and media integration | CUDA RTX 5060 Ti on port 8010; four inputs, five prepared cases, Range 206 and HTML 200; original device service on port 8000 remained active | `assets/validation/20261002/showcase-integrated-validation.json` |
| Artifact registry | 130 artifact URLs plus eight input/poster URLs readable | `showcase-server-review/review-summary.json` under the original validation directory |
| Foul-button equivalent API run | Job `655e6245a5814e199d5a711b2738f70d` completed; 175/175 frames, 19 actual forwards, seven candidates, strict loading of 418 tensors | Recorded showcase job report, schema `refereelink-extracted-core-v1` |
| Browser tracking-button run | Job `2c9b478594794f7c98605787c4fdeac0` completed; 682/682 frames, zero processing drops, 18.677 offline FPS | Job report and tracking screenshot |
| Prepared-case multi-view analysis | Explicit POST for `soccernet_action_183`, CUDA and MViT_V2_S; HTTP 200, 361.3 ms inference, confidence 0.7759 | `multiview-analysis-receipt.json` and multi-view screenshot |
| Telemetry WebSocket | Explicit simulation produced at least three valid two-channel snapshots; stop returned idle with invalid position/acceleration; `hardware_verified` stayed false | Backend requests and browser start/stop checks |
| UART software lifecycle | 15 tests included real pySerial over separate Linux PTYs, fragmented reads, disconnect errors, staleness and bounded queues | [Hardware software validation](hardware/VALIDATION.md) |
| Portable STM32 layer | Strict C11, parser/HAL assertions, four bridge tests and sanitizer checks passed | The football repository's `docs/VALIDATION.md` |

Reported job FPS measures sequential inference, rendering and JSON output. The rendered video's 30 fps is its playback rate. Neither proves lossless simultaneous phone/camera ingestion, referee decision accuracy or generalization to another match. Calibration leave-one-out results do not establish cross-match team accuracy.

## Regression fixes verified

Independent review reproduced three defects and verified their fixes:

1. A child that ignored SIGTERM could survive after its parent exited. Shutdown now cleans the entire process group, covering a live or already-exited group leader.
2. An input removed after queueing could raise before the worker's error handler and stop later jobs. Preparation now runs inside the unified try/finally path; failed jobs persist and the next job proceeds.
3. The optional multi-view upstream's 180-second timeout could delay the catalog beyond the client's 15-second limit. Catalog lookup now has a separate five-second timeout. A loopback upstream delayed by 6.5 seconds returned all four local clips and a warning after 5.010 seconds.

The three focused regression tests passed and are included in the full suite. Original reproductions, source hashes and URL read-back records remain in the private `showcase-server-review/` evidence directory.

## Browser and visual checks

The four design references and screenshots are retained as local validation media, outside Git. Desktop checks used a 1536 × 1024 viewport; mobile checks used 390 × 844, with full-page screenshots at that width. [web/QA.md](../web/QA.md) records the detailed comparison and interaction results.

The implemented interface uses a shared four-tab layout, open inspection panels, white/cool-gray/ink/lime colors, consistent spacing and borders, original video proportions, the tracking/projection composite and sensor XYZ plots. Localized Chinese interface text is intentional; repository documentation and code commentary use English.

Browser checks covered keyboard tab navigation, loading and actual advancing video frames, pause, seeking, playback speed, fullscreen, candidate timestamp selection, model/human decision separation, actual job submission/progress/completion, three offline device states, configuration saving, simulated position/acceleration and invalid values after stop. Mobile routes had no horizontal overflow. User-saved review decisions were not overwritten; the historical review-bound analysis and a fresh model POST were recorded separately.

The screenshots show software behavior. The simulated trajectory does not establish physical positioning accuracy, and these checks do not measure model decision accuracy frame by frame.

## Historical deployment read-back

Recorded read-back time: `2026-10-01T19:29:22.068119+00:00`.

The deployed build at that time was `index-DHcXE8tP.js` / `index-CLvj18jO.css`. Returned HTML, JS and CSS matched the deployed hashes; all 57 core source hashes matched the manifest; five prepared cases and four inputs were readable; both real CUDA jobs were completed. All 138 artifact/input/poster URLs were checked.

Projection-only output intentionally contains empty foul/candidate files. Range requests for an empty file returned 416; ordinary GET returned 200 with zero bytes. Empty output was recorded explicitly rather than converted into fabricated model events. The four original MP4 input hashes also matched the tested inputs.

Snapshots are retained in the original backend's `assets/validation/20261002/showcase-final-deployment-audit.json` and `current-input-validation.json`; the task audit is in `docs/night-validation-20261002/GOAL_AUDIT.zh-CN.md`. These files contain private device information and are not published. This historical read-back does not imply that publication preparation redeployed the running service.

## Pending physical acceptance

At the recorded final read-back, both RTSP cameras and the phone were not receiving frames and had empty buffers. The host lacked the camera network address; both camera pings failed. Network repair required interactive administrator authentication that was unavailable, so no network configuration change occurred. The software's offline behavior passed, but restoring the network and phone capture is required before accepting three changing live streams, event capture and analysis.

The UWB/IMU devices had not arrived. Radio positioning accuracy, real UART wiring, installation axes, target cross-compilation/linking, MCU flashing and simultaneous hardware/video timing remain unverified. Protocol implementation follows the primary [BP-TWR-30 manual](https://doc.51uwb.cn/user_manual/twr-30/twr-30/) and [Yahboom IMU-Sensor materials](https://www.yahboom.com/study/IMU_Sensor/); see [hardware/PROTOCOLS.md](hardware/PROTOCOLS.md). Three anchors provide same-plane 2D positioning. The displayed residual measures model consistency, not measured position error or altitude.
