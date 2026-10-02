# Contributing

Use a focused branch and pull request with the problem, change and relevant validation. Preserve upstream attribution and the extracted source manifest. Do not submit videos, weights, calibration bundles, device identifiers, private host configuration or credentials. Use synthetic fixtures for tests and identify simulated results clearly.

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
