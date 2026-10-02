# UWB / IMU UART evidence and implementation boundary

Verified against public primary sources on 2026-10-02. These are protocol/software checks, not physical-device acceptance. Vendor application code, screenshots, firmware and PDFs are not redistributed in this repository.

## BP-TWR-30 three-anchor positioning

Sources: [requested manual](https://doc.51uwb.cn/user_manual/twr-30/twr-30/) and [official host manual, §10.2](https://doc.51uwb.cn/tools/Landian_UWB_TWR_Host_USER_MANUAL/#102-mr). The second source resolves distance units and byte order absent from the first page's data table.

| Offset | Contents |
| --- | --- |
| 0–2 | ASCII `mr`, binary version `02` |
| 3 | tag ID (one byte; supplied default tag 05) |
| 4–5 | unsigned LE16 sequence |
| 6–7, 8–9, 10–11 | unsigned LE16 ranges, centimetres, anchor addresses 0001/0002/0003 |
| 12–13 | copy of the first range in the three-anchor profile |
| 14–15 | CRLF; the host manual also accepts LFCR |

UART: 115200 baud. There is **no checksum field**. Frame boundary checks cannot detect every bit flip; the parser also rejects missing/repeated range mismatches and ranges outside 0.2–30 m, then the service rejects wrong tags, repeated/reversed sequences, inconsistent triangle geometry and excessive residuals. The raw fourth range never becomes a fourth anchor. Reported rejection counts are software diagnostics, not radio RSSI or vendor accuracy estimates.

Three anchors and the tag must share one physical plane. Coordinates are configured in metres in that local plane, with IDs corresponding to UART range order. The common `plane_height_m` is installation metadata; reported `z_m` is that configuration, **not measured altitude**. Noncoplanar or nearly collinear layouts are refused. `x_m/y_m` result from three range circles; the residual is the maximum range-model mismatch. A position outside the anchor triangle or with a residual above 0.2 m is marked `degraded`; a residual above the configured maximum (default 0.5 m) is rejected. This validates tag tracking in a small controlled area, not full-field football ground truth.

## Current Yahboom IMU-Sensor, not the older 55 protocol

The requested [study page](https://www.yahboom.com/study/IMU_Sensor/) is the current 6/9/10-axis module. Its [official repository](https://github.com/YahboomTechnology/IMU-Sensor) was inspected at `61eda112c249cab6947d1ac1313a26749aa9f593`.

Primary evidence:

- [Official STM32 serial tutorial PDF](https://github.com/YahboomTechnology/IMU-Sensor/blob/61eda112c249cab6947d1ac1313a26749aa9f593/2.%20Multi-master%20communication%20case/2.%20Serial%20communication/1.STM32.pdf) shows total-frame length, sum8 calculation, first three signed acceleration values and scaling `16/32767` in g.
- [Public PC communication tutorial](https://www.yahboom.com/build.html?id=15173&cid=725) and its [raw serial screenshot](https://admin.yahboom.com/public/upload/upload-html/1761275864/pc.png) establish actual header, function byte, little-endian order and a checksum-consistent sample.
- [Current module parameter diagram](https://admin.yahboom.com/public/upload/upload-html/1761275770/image-20251016174439021.png) documents 115200 baud, a default output rate of 25 Hz, and an adjustable rate of 10–100 Hz. The PC screenshot confirms 8N1.

Transcribed sample (23 bytes):

```text
7E 23 17 04 51 00 99 FF F7 07 F5 FF 00 00 00 00 1B 01 40 FA 5D FD 47
```

| Offset | Contents |
| --- | --- |
| 0–1 | `7E 23` |
| 2 | total length (raw sample `17` hex = 23 bytes) |
| 3 | raw acceleration/gyro/magnetic function `04` |
| 4–9 | signed LE16 acceleration X/Y/Z |
| 10–21 | gyro and magnetometer data; consumed, not presented in this scope |
| 22 | sum of all preceding bytes, modulo 256 (`47` in the sample) |

Sample XYZ counts are `81, -103, 2039`. Acceleration is `count × 16/32767 × 9.80665 m/s²`. The server reports vendor sensor-axis components including gravity. Installation orientation and sensor-to-ball/field rotation have not been measured, so it does not transform to field axes or integrate acceleration into ball position. Other valid functions are consumed without becoming acceleration data. Impossible lengths, raw payload length mismatches and checksum failures are rejected; buffering is bounded with byte-wise resynchronization.

The full communication download is vendor-password gated; the public primary tutorial and checksum-consistent screenshot were used instead. The older Yahboom 10-axis `55 51` frame is a different product profile and is deliberately unsupported here.

## State, transport and hardware acceptance

`idle`, `simulation`, `serial` are distinct `source_mode` values. Simulation is explicit and passes generated UART frames through the same parsers. It never marks hardware connected. Serial opening marks a port `connected`; freshness separately depends on accepted samples, not arbitrary received bytes. A peer unplug becomes `error`, last error remains visible, and old history remains with latest sample marked stale/invalid. No HTTP endpoint accepts arbitrary telemetry injection.

[pySerial's API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html) supports the exclusive POSIX port opens used here. Linux `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*`, `/dev/ttyAMA*` and `/dev/serial/by-id/*` character devices are allowed; URLs and ordinary files are refused. `/dev/pts/*` is constructor-enabled only in tests. The public API cannot enable it. Each device uses its own port, 8N1, 100 ms read timeout, no writes or auto-reconnect. Physical cable placement, UART voltage compatibility, USB identity, anchors/antenna delays and RF accuracy still require device arrival and measurement.
