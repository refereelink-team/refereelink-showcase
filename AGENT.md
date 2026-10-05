# RefereeLink Showcase

This repository is a focused presentation surface, not the complete administration dashboard.
Deployed Python services and model inference run on a remote CUDA host. Local macOS runs
only the web client, static checks, file editing and media playback. Linux CI may run the
software-only API suite; it never claims real CUDA or physical-device acceptance.
Never enable silent CPU or scripted fallback for a real-model experiment. Preserve source
attribution for extracted code. User videos, weights, calibration files, device identifiers
and runtime credentials stay outside Git. Keep demonstration replay visibly distinct from
hardware data. UWB/IMU hardware is not yet available; parser, serial lifecycle and WebSocket
tests are software evidence, not physical-device acceptance.

## Git submissions and case media

- Commit source, case manifests, asset checksums and operator documentation. Never commit videos or media ZIP/TAR archives, including with `git add -f`.
- Package new case media with paths relative to this repository, upload the archive to the user's Google Drive, and verify the uploaded file before committing. Do not alter sharing permissions without authorization.
- Document the observed Drive link, exact archive name, download directory, one-command extraction from the repository root, target media paths and asset scope in `docs/MULTIVIEW_ASSETS.md`. Keep the checksums and bundle metadata under `cases/`.
- Before each requested commit, inspect staged paths for video/archive files and run the checks appropriate to the changes. Commit when the user requests it; publishing or pushing is a separate action.
