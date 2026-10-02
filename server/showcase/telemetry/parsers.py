"""Original parsers for vendor UART frames; evidence in docs/hardware/PROTOCOLS.md."""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

STANDARD_GRAVITY = 9.80665


@dataclass(frozen=True)
class UWBFrame:
    tag_id: int
    sequence: int
    ranges_m: tuple[float, float, float]


@dataclass(frozen=True)
class AccelerationFrame:
    x: float
    y: float
    z: float
    raw_xyz: tuple[int, int, int]


class BufferedParser:
    """Byte-wise resynchronization survives split reads and corrupt candidates."""
    header: bytes
    maximum_buffer = 4096

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.rejected_frames = 0
        self.last_rejection: str | None = None

    def reject(self, reason: str) -> None:
        self.rejected_frames += 1
        self.last_rejection = reason

    def append(self, data: bytes) -> None:
        self.buffer.extend(data)
        if len(self.buffer) > self.maximum_buffer:
            del self.buffer[:-self.maximum_buffer]
            self.reject("buffer_overflow")

    def seek(self) -> bool:
        index = self.buffer.find(self.header)
        if index < 0:
            # Preserve the longest possible partial header across reads.
            keep = len(self.header) - 1
            del self.buffer[:max(0, len(self.buffer) - keep)]
            return False
        if index:
            del self.buffer[:index]
        return True


class UWBParser(BufferedParser):
    """BP-TWR-30 mr v2: 16 bytes, LE cm distances, no checksum field."""
    header = b"mr"

    def __init__(self, minimum_range_m: float = 0.2, maximum_range_m: float = 30.0) -> None:
        super().__init__()
        self.minimum_range_m = minimum_range_m
        self.maximum_range_m = maximum_range_m

    def feed(self, data: bytes) -> list[UWBFrame]:
        self.append(data)
        frames: list[UWBFrame] = []
        while self.seek() and len(self.buffer) >= 16:
            candidate = bytes(self.buffer[:16])
            if candidate[2] != 2 or candidate[-2:] not in (b"\r\n", b"\n\r"):
                self.reject("invalid_uwb_framing")
                del self.buffer[0]
                continue
            del self.buffer[:16]
            tag = candidate[3]
            sequence, d0, d1, d2, d3 = struct.unpack_from("<5H", candidate, 4)
            if d3 != d0:
                self.reject("expected_three_anchor_duplicate")
                continue
            ranges = (d0 * 0.01, d1 * 0.01, d2 * 0.01)
            if not all(self.minimum_range_m <= d <= self.maximum_range_m for d in ranges):
                self.reject("uwb_range_out_of_bounds")
                continue
            frames.append(UWBFrame(tag, sequence, ranges))
        return frames


class IMUParser(BufferedParser):
    """Current Yahboom IMU-Sensor 7E23 frames, total length, sum8, raw function04."""
    header = b"\x7e\x23"

    def feed(self, data: bytes) -> list[AccelerationFrame]:
        self.append(data)
        frames: list[AccelerationFrame] = []
        while self.seek() and len(self.buffer) >= 4:
            length = self.buffer[2]
            # Vendor parser has a 64-byte data+checksum buffer, excluding 4 header bytes.
            if length < 5 or length > 68:
                self.reject("invalid_imu_length")
                del self.buffer[0]
                continue
            if len(self.buffer) < length:
                break
            candidate = bytes(self.buffer[:length])
            if sum(candidate[:-1]) & 0xff != candidate[-1]:
                self.reject("imu_bad_checksum")
                del self.buffer[0]
                continue
            del self.buffer[:length]
            if candidate[3] != 0x04:
                continue
            if length != 23:
                self.reject("invalid_imu_raw_payload_length")
                continue
            raw = struct.unpack_from("<3h", candidate, 4)
            xyz = tuple(v * 16.0 / 32767.0 * STANDARD_GRAVITY for v in raw)
            frames.append(AccelerationFrame(*xyz, raw))
        return frames


def trilaterate(anchors: list[dict], ranges_m: tuple[float, float, float],
                 maximum_residual_m: float = 0.5) -> dict:
    """Closed-form 2D solution plus physical consistency and residual rejection."""
    if len(anchors) != 3 or len(ranges_m) != 3:
        raise ValueError("exactly_three_anchors_required")
    p = [(float(a["x_m"]), float(a["y_m"])) for a in anchors]
    if not all(math.isfinite(v) for point in p for v in point):
        raise ValueError("nonfinite_anchor")
    if not all(math.isfinite(d) and d > 0 for d in ranges_m):
        raise ValueError("invalid_distance")
    for i in range(3):
        for j in range(i + 1, 3):
            baseline = math.dist(p[i], p[j])
            if abs(ranges_m[i] - ranges_m[j]) > baseline + maximum_residual_m or (
                    ranges_m[i] + ranges_m[j] < baseline - maximum_residual_m):
                raise ValueError("inconsistent_range_geometry")
    (x0, y0), (x1, y1), (x2, y2) = p
    a, b, c, d = 2 * (x1 - x0), 2 * (y1 - y0), 2 * (x2 - x0), 2 * (y2 - y0)
    determinant = a * d - b * c
    scale = max(math.dist(p[0], p[1]), math.dist(p[0], p[2]), math.dist(p[1], p[2]))
    if scale < 0.5 or abs(determinant) < max(1e-8, scale * scale * 0.04):
        raise ValueError("degenerate_anchor_geometry")
    r0, r1, r2 = ranges_m
    e = r0 * r0 - r1 * r1 + x1 * x1 - x0 * x0 + y1 * y1 - y0 * y0
    f = r0 * r0 - r2 * r2 + x2 * x2 - x0 * x0 + y2 * y2 - y0 * y0
    x, y = (e * d - b * f) / determinant, (a * f - e * c) / determinant
    residual = max(abs(math.dist((x, y), anchor) - distance) for anchor, distance in zip(p, ranges_m))
    if not math.isfinite(x + y + residual) or residual > maximum_residual_m:
        raise ValueError("position_residual_exceeded")
    signs = [(p[(i + 1) % 3][0] - p[i][0]) * (y - p[i][1]) -
             (p[(i + 1) % 3][1] - p[i][1]) * (x - p[i][0]) for i in range(3)]
    inside = all(s >= -1e-6 for s in signs) or all(s <= 1e-6 for s in signs)
    return {"x_m": x, "y_m": y, "residual_m": residual,
            "inside_anchor_triangle": inside,
            "quality": "good" if residual <= 0.2 and inside else "degraded"}


def encode_uwb(tag_id: int, sequence: int, ranges_m: tuple[float, float, float]) -> bytes:
    """Synthetic fixture encoder. Generated values are never device evidence."""
    distances = [round(d * 100) for d in ranges_m]
    return b"mr\x02" + bytes([tag_id]) + struct.pack("<5H", sequence & 0xffff, *distances, distances[0]) + b"\r\n"


def encode_imu(x: float, y: float, z: float) -> bytes:
    raw = [max(-32768, min(32767, round(v / STANDARD_GRAVITY * 32767 / 16))) for v in (x, y, z)]
    data = b"\x7e\x23\x17\x04" + struct.pack("<9h", *raw, 0, 0, 0, 0, 0, 0)
    return data + bytes([sum(data) & 0xff])
