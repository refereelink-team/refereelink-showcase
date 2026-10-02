# Native tracking workspace

The tracking route renders the original input video, browser SVG detection boxes, and an interactive SVG pitch. It does not play an annotated composite to represent the interface. Detection, tracking, role and team classification, pitch estimation, and field projection execute in the standalone Python pipeline on the remote CUDA host.

## Operator flow

1. Open **Player tracking** and select the match input. The separate calibration tab runs the [native practice workflow](CALIBRATION.md).
2. A complete, source-verified CUDA result loads automatically when available. Otherwise select **Start tracking**.
3. The queue reports frame-based progress. A new run hides the previous run's observations while it is pending or failed.
4. Play or seek the original video. Select a video box, pitch marker, or target row to inspect that observation and its recorded recent trajectory.
5. Toggle detection boxes or trajectories independently. Playback speed, frame-level arrow-key seeking, and fullscreen keep the original media aspect ratio.
6. Open **Run details** for CUDA processing speed, processed-frame count, and projection availability. Processing FPS is distinct from source-video playback FPS.

Unknown team and role values remain unknown. Targets without a usable projection stay in the image/target list and do not acquire invented field positions. Stale or unavailable pitch transforms hide current field positions and interrupt trails; fresh and scheduled reused transforms remain usable. Predicted ball observations are distinguished from fresh observations. A selection is reset when the immutable result or its revision changes, since a new tracker run may reuse numeric IDs.

## HTTP contract

All paths use the same-origin showcase gateway.

| Method and route | Response |
| --- | --- |
| GET `/api/tracking/cases/{case_id}` | Original media URL, latest valid result descriptor, active tracking job, and warnings for rejected stored outputs |
| POST `/api/tracking/cases/{case_id}/jobs` | A queued or deduplicated CUDA job; HTTP 202 |
| GET `/api/tracking/results/{result_id}` | Immutable result descriptor including source dimensions, pitch geometry, metrics, and revision |
| GET `/api/tracking/results/{result_id}/frames?offset=0&limit=240&revision={revision}` | A revision-bound page of FrameState observations joined to decoded source PTS |

The client fetches every page before publishing a result to the scene. It checks the result ID, SHA-256 revision, expected total, contiguous page offsets, unique frame IDs, and increasing source timestamps. Late responses from another case or a discarded result are ignored. The server validates source hashes, complete decoded-frame coverage, and recorded CUDA provenance; runtime source assets and frame records remain outside Git.

## Timing and coordinate conventions

The media element is the playback clock. Browsers that support `requestVideoFrameCallback` provide presented `mediaTime`; the fallback uses the media element's current playback time. A binary lookup chooses the most recent observation at or before that source timestamp and rejects observations held across a missing-data gap. Browser wall-clock time and capture timestamps are never used as the source timeline.

Detection boxes use the original image pixels and the result's original width/height as their SVG viewBox. The video and overlay preserve the same aspect ratio and use matching letterboxing in fullscreen. A media-size mismatch suppresses overlays and reports an error.

Field coordinates in the extracted pipeline are centimetres. The API converts field positions and velocities to metres once. Every immutable result supplies the geometry that actually produced its coordinates. Default, unknown, and calibration sources retain the legacy 120 × 70 m template and 20.15 m penalty-area depth. The configured match video opts into the source-informed 105 × 68 m template, 16.5 m penalty-area depth, and independent paint registration only when its actual SHA-256 matches catalog metadata. New reports record the profile, paint flag, and geometry; older reports without geometry retain legacy dimensions. Overall dimensions are configured assumptions, not independently surveyed stadium measurements. The frontend does not infer dimensions, clamp invalid coordinates into the field, interpolate new tracks, or alter saved observations.

## Verification boundaries

Frontend checks may run on macOS:

```bash
npm test
npm run lint
npm run format:check
npm run build
```

Python API tests, job execution, and model checks run on the remote CUDA host. Helper tests cover source-PTS selection, unavailable projections, original-pixel boxes, unknown identity, gap-preserving trajectories, revision integrity, pagination completeness, and late-response rejection. Browser acceptance separately verifies the visible playback, selection, seek, and responsive layout. A prepared-video CUDA run does not establish real-time multi-camera or physical-device acceptance.

## Initial native-tracking acceptance snapshot

On 2026-10-02, 80 frontend tests passed together with TypeScript, formatting, production build and whitespace checks. The remote Python software and CUDA-health suite passed 53 tests. Extracted model source was unchanged; deployment source and static-build checksums matched the checked workspace.

Fresh real-model runs on an RTX 5060 Ti processed both supplied inputs without dropped processing frames:

| Input | Decoded / processed frames | Processing FPS | Mean / p95 pipeline latency | Projected-person rate |
| --- | --- | --- | --- | --- |
| Calibration | 579 / 579 | 22.11 | 40.87 / 45.15 ms | 89.89% |
| Match tracking | 682 / 682 | 18.67 | 49.03 / 53.70 ms | 90.25% |

Each API record was compared with its actual pipeline observation and decoded source timestamp, including boxes, identities, roles, teams and metre conversion. A further job submitted from the native browser UI completed automatically without a page reload: 682 processed frames, zero drops and 18.69 processing FPS. Browser inspection verified original media playback, simultaneous image boxes and pitch markers, target selection with metre coordinates, and suppression of previous observations during a new run. Runtime reports and screenshots remain outside Git. These measurements describe prepared-video processing, not 30 FPS live ingestion.

The subsequent calibration migration and projection corrections are documented in
[CALIBRATION.md](CALIBRATION.md) and [PROJECTION_CORRECTIONS.md](PROJECTION_CORRECTIONS.md).
The initial availability figures above do not establish independent spatial accuracy.
