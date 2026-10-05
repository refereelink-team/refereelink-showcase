# Contributing

Use a focused branch and pull request with the problem, change and relevant validation. Preserve upstream attribution and the extracted source manifest. Do not submit videos, weights, calibration bundles, device identifiers, private host configuration or credentials. Use synthetic fixtures for tests and identify simulated results clearly.

## Case media and Git submissions

For case-media changes, archive the required videos separately with repository-relative paths and upload them to the user's Google Drive. Record the actual download link, archive name, designated download directory, extraction command and checksums in [the media restore guide](docs/MULTIVIEW_ASSETS.md). Never stage videos or media archives, even with a force-add. Case definitions and hashes may be committed; raw provenance containing private source paths stays inside the ignored media archive.

Before a requested commit, inspect the staged file list and run the applicable client checks. This demo does not save case review states or restore earlier judgments; case-switch acceptance must cover model suggestions, human answers, location, guide step and stale analysis responses. Commit after the authorized work and validation are complete; do not infer permission to publish or push.

## Client checks

Node.js 22.12+ is required; CI uses Node.js 24. From the repository root:

```sh
npm ci
npm run lint
npm test
npm run format:check
npm run build
```

For client development, copy `web/.env.example` to ignored `web/.env.local`, set `BACKEND_URL` to your remote CUDA server and run `npm run dev`.

## Remote Linux API checks

Run Python checks on the remote Linux host, not the local presentation Mac. In an isolated Python 3.12+ virtual environment:

```sh
python -m pip install -c requirements-ci.txt -e '.[test]' ruff
ruff check server tests --select E4,E7,E9,F
python -m pytest -q -m 'not cuda'
```

These tests exercise the API, process cleanup, synthetic UART frames and Linux PTYs. They do not require or prove actual inference. Hosted CI uses this suite.

On the remote CUDA machine with compatible PyTorch installed, run the full suite with `python -m pytest -q`. The `cuda` test verifies an actual CUDA device; production startup fails when CUDA is unavailable. Model runs additionally require separately obtained checkpoints, authorized media, calibration and the upstream VARS implementation documented in `backend_core/SOURCE.md`. Never substitute CPU or scripted output for a real-model result.

Before changing extracted model code, retain source attribution and update its manifest and documented validation deliberately. For UART or board changes, identify the exact device/protocol and distinguish software tests from physical acceptance.
