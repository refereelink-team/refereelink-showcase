# UWB / IMU UART evidence and implementation boundary

Initially verified against public primary sources on 2026-10-02; rechecked against the TWR-30 manuals and downloaded Yahboom communication workbook on 2026-10-16. These are protocol/software checks, not physical-device acceptance. Vendor application code, screenshots, firmware and documents are not redistributed in this repository.

## BP-TWR-30 three-anchor positioning

Sources: [requested manual](https://doc.51uwb.cn/user_manual/twr-30/twr-30/) and [official host manual, §10.2](https://doc.51uwb.cn/tools/Landian_UWB_TWR_Host_USER_MANUAL/#102-mr). The second source resolves distance units and byte order absent from the first page's data table.

The user-supplied vendor share was downloaded and extracted outside Git on 2026-10-16, with archive extraction reporting `All OK`:

| Archive | Bytes | SHA-256 |
| --- | --- | --- |
| `TWR-30-B-FW.rar` | 17559548 | `9416bf56fbbf5ffac7a0a381724ce45ba57ff2a5a3940aea94d7f23e9eefdf32` |
| `TWR上位机源码.rar` | 7132757 | `59067528a998b34780ac1970a3a7cc47e7d00d0db135ece521203b44bf4d8dd4` |

The firmware archive contains separate `BPHero_UWB_BP30_Location_SourceCode_HV2.0_SV2.1` and `BPHero_UWB_BP30_Distance_SourceCode_HV2.0_SV2.1` projects. In the **Location** project, `BP-UWB_LIB/Src/tx_main.c:301–356` constructs the 16-byte frame, sends it to anchor 0001 and increments a dedicated frame counter. `rx_main.c:135–140` forwards all 16 bytes to UART; `STM32F1_UWB/Core/Src/usart.c:60–65` selects 115200/8N1. `bphero_uwb.h:11–34` defaults to tag 0005, three anchors and LCD enabled for the tag. The **Distance** project instead prints text, so is not a compatible substitute. The nested `BpHero-UWB上位机源码V1.0.rar` was also extracted; `network/RTLSClient.cpp` independently confirms the little-endian range layout and centimetre-to-millimetre conversion. No vendor code was copied, executed, compiled or flashed.

The separate vendor directory `TWR-30-B资料 / 测试HEX 文件 / 定位` also supplied four prebuilt V1.2 HEX files. They were downloaded outside Git on 2026-10-16; the download ZIP passed CRC checks, and all Intel HEX records passed length/checksum, EOF, non-overlap and Flash-address checks. The original firmware bytes were not modified. These checks do **not** verify board compatibility, actual UART output, positioning or equivalence to the inspected `HV2.0_SV2.1` source projects. No firmware was flashed or committed.

| Firmware | Bytes | SHA-256 |
| --- | --- | --- |
| `BP30_STM32F1_RXNode_0x0001_Anthor1_LCDDisable_V1.2.hex` | 49003 | `4deb54acaf6dfb32a2e5d5802d62dc871b7c0a17c910ff110dae6c6cd18c7e58` |
| `BP30_STM32F1_RXNode_0x0002_Anthor2_LCDDisable_V1.2.hex` | 49011 | `8ddd7c84e3567afb5785e879c090d381df65ae45ef202c02d6358f4c769c8d38` |
| `BP30_STM32F1_RXNode_0x0003_Anthor3_LCDDisable_V1.2.hex` | 49011 | `08dfe7d59295f9de5b7288cd48b6adf60f2b16168e1fe1266b49166e9cffcc88` |
| `BP30_STM32F1_TXNode_0x0005_LCDEnable_Tag_V1.2.hex` | 50930 | `de2b3e15d3615d8c80319ec2b631d0314cb74a90bcb6f8250c8cc1aa54e5d08b` |

| Offset | Contents |
| --- | --- |
| 0–2 | ASCII `mr`, binary version `02` |
| 3 | tag ID (one byte; supplied default tag 05) |
| 4–5 | unsigned LE16 sequence |
| 6–7, 8–9, 10–11 | unsigned LE16 ranges, centimetres, anchor addresses 0001/0002/0003 |
| 12–13 | fourth range, or copy of the first range in the three-anchor firmware; ignored by this three-anchor application |
| 14–15 | CRLF in the manual; inspected firmware emits LFCR; both are accepted |

UART: 115200 baud, 8N1. There is **no checksum field**. Frame boundary checks cannot detect every bit flip; the parser rejects ranges outside 0.2–30 m in the three configured channels, then the service rejects wrong tags, repeated/reversed sequences, inconsistent triangle geometry and excessive residuals. Rejected geometry does not advance the accepted sequence. The raw fourth range never becomes a fourth anchor: the official host manual §10.2 permits using the first three distances when anchor 4 is disabled, even when Dis3 differs from Dis0. This is not an integrity check or support for 3D positioning. Reported rejection counts are software diagnostics, not radio RSSI or vendor accuracy estimates.

The inspected firmware uses `0xFFFF` for nonpositive/missing ranges; this is rejected by the configured range bounds in the first three channels. With `MAX_ANTHOR=3` it repeats the first range in the fourth slot; with more anchors it sends the last anchor's range there. The stream has no per-anchor measurement timestamps, so a recently received frame cannot by itself establish that every range was newly measured over RF.

See [TWR-30 setup and physical acceptance](TWR30_SETUP.md) for firmware selection, wiring, backend port placement and troubleshooting. This implementation supports the documented `mr` version 2 positioning stream, not the separate factory distance-test program, text protocol or `mri` fusion firmware.

Three anchors and the tag must share one physical plane. Coordinates are configured in metres in that local plane, with IDs corresponding to UART range order. The common `plane_height_m` is installation metadata; reported `z_m` is that configuration, **not measured altitude**. Noncoplanar or nearly collinear layouts are refused. `x_m/y_m` result from three range circles; the residual is the maximum range-model mismatch. A position outside the anchor triangle or with a residual above 0.2 m is marked `degraded`; a residual above the configured maximum (default 0.5 m) is rejected. This validates tag tracking in a small controlled area, not full-field football ground truth.

## Current Yahboom IMU-Sensor, not the older 55 protocol

The requested [study page](https://www.yahboom.com/study/IMU_Sensor/) is the current 6/9/10-axis module. Its [official repository](https://github.com/YahboomTechnology/IMU-Sensor) was inspected at `61eda112c249cab6947d1ac1313a26749aa9f593`.

Primary evidence:

- The study page's **下载专区 → 通讯协议** links to the vendor's [communication download](https://pan.baidu.com/e/1k3crKdVWxAMDUzeoFBRz8Q). Its `通信协议.xlsx`, downloaded on 2026-10-16, has SHA-256 `333c2d0264e290dcc5afcc6208f6fba83a9e14fe0b6fe713152e30edc122a138`. Sheet `串口通讯协议`, note A3 and rows 50–66 confirm framing, checksum, byte order, raw-data scaling, output rate and interleaved function types. No extraction code is stored here.
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

The downloaded workbook confirms the existing acceleration parser; no protocol replacement is needed. Quaternion (`0x16`, 21 bytes), Euler angle (`0x26`, 17 bytes) and barometer (`0x32`, 21 bytes, 10-axis only) frames may be interleaved with raw data. They are checksum-validated and consumed but not exposed as acceleration or as fresh acceleration samples. The workbook repeats the quaternion label on its Euler/barometer rows; the function bytes and field descriptions distinguish them. The older Yahboom 10-axis `55 51` frame is a different product profile and is deliberately unsupported here. See [IMU setup and physical acceptance](IMU_SETUP.md).

## State, transport and hardware acceptance

The optional `uwb_bridge_v1` transport is a **custom, experimental** extension, not an official vendor capability. Its host demultiplexer and portable C codec are implemented; tag/A1 firmware integration, HEX and radio acceptance are not. See [the bridge specification](IMU_UWB_BRIDGE.md). Only this explicit mode shares the A1 reader between logical UWB/IMU channels; default `usb` keeps separate physical ports. Simulation does not establish the bridge envelope or radio implementation.

`idle`, `simulation`, `serial` are distinct `source_mode` values. Simulation is explicit and passes generated UART frames through the same parsers. It never marks hardware connected. Serial opening marks a port `connected`; freshness separately depends on accepted samples, not arbitrary received bytes. A peer unplug becomes `error`, last error remains visible, and old history remains with latest sample marked stale/invalid. No HTTP endpoint accepts arbitrary telemetry injection.

[pySerial's API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html) supports the exclusive POSIX port opens used here. Linux `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*`, `/dev/ttyAMA*` and `/dev/serial/by-id/*` character devices are allowed; URLs and ordinary files are refused. `/dev/pts/*` is constructor-enabled only in tests. The public API cannot enable it. USB mode uses distinct ports; bridge mode opens only A1. Both use 8N1, 100 ms read timeout, no writes or auto-reconnect. Physical cable placement, UART voltage compatibility, USB identity, anchors/antenna delays and RF accuracy still require device arrival and measurement.
