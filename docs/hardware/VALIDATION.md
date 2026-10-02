# Software validation, 2026-10-02

All backend Python execution, protocol tests and C compilation in this task ran via SSH on **the remote CUDA host**. No local Python backend was started. Temporary remote directories were `/tmp/refereelink-telemetry-test` and `/tmp/refereelink-football-test`.

| Check | Observed result | Boundary |
| --- | --- | --- |
| Showcase telemetry pytest | 15 passed | Frame parsing, 2D solve/rejection, freshness, bounded history/WS queue, idle/simulation separation and API lifecycle |
| Actual pyserial through Linux PTY | Included in 15 passed | Separate UWB and IMU reader paths, fragmented reads, peer disconnect/error, stale latest values; no physical device |
| Strict C11 build | Passed with `-Wall -Wextra -Werror -pedantic` | Core parser, HAL adapter against explicit stub, application integration example, binary bridge |
| Core C assertion suite | Passed | Official raw IMU sample, signed little-endian units, bad checksum/boundaries/ranges/tags, two channels, queue overflow recovery and 200,000 deterministic noise bytes |
| HAL stub assertion suite | Passed | ISR deferral, receive rearm, unrelated UART, deferred abort/restart, partial-frame reset and retry flags |
| Bridge black-box Python tests | 4 passed | Byte-preserving output, diagnostics on stderr, invalid packets withheld, channel isolation |
| AddressSanitizer + UndefinedBehaviorSanitizer | Both core/HAL suites passed | No reported host memory/undefined-behavior findings in those exercised cases |

Commands used on the remote host:

```sh
cd /tmp/refereelink-telemetry-test
PYTHONPATH=server /path/to/cuda-venv/bin/python -m pytest -q tests/test_telemetry_parsers.py tests/test_telemetry_service.py

make -C /tmp/refereelink-football-test test PYTHON=/path/to/cuda-venv/bin/python
```

For sanitizer checks, both C suites were compiled separately with `-std=c11 -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer` and executed successfully. The HAL suite defines `RF_HAL_HEADER="fake_hal.h"` and adds the test include directory; it **does not link or exercise real STM32 peripherals**.

The Python run emits one environment warning: installed Starlette reports its `httpx` TestClient integration as deprecated. Tests still passed; no dependency migration was performed as part of this hardware scope. pySerial 3.5 was installed into the existing remote virtual environment for the PTY reader checks.

Not validated: board model, pinout, STM32 cross-compilation/linking, actual interrupt or DMA timing, firmware flashing, physical wiring/voltage, vendor RF ranging/accuracy, sensor installation axes, live full-field football motion or simultaneous camera+hardware timing. The deliverable is a complete portable software layer and a tested showcase API, with actual board integration explicitly pending arrival.
