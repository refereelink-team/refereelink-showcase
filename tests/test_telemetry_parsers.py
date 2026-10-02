import math
import struct

import pytest

from showcase.telemetry.parsers import IMUParser, UWBParser, encode_uwb, trilaterate
from showcase.telemetry.service import TelemetryConfig

# Transcribed from the current module's official PC communication screenshot.
VENDOR_RAW = bytes.fromhex("7E 23 17 04 51 00 99 FF F7 07 F5 FF 00 00 00 00 1B 01 40 FA 5D FD 47")


def test_current_imu_vendor_frame_checksum_and_units():
    assert len(VENDOR_RAW) == 23 and sum(VENDOR_RAW[:-1]) & 255 == VENDOR_RAW[-1]
    frame = IMUParser().feed(VENDOR_RAW)[0]
    assert frame.raw_xyz == (81, -103, 2039)
    assert frame.x == pytest.approx(81 / 32767 * 16 * 9.80665)
    assert frame.y < 0
    assert frame.z == pytest.approx(9.763889, abs=0.0001)


def test_imu_fragmented_noise_bad_checksum_recovers():
    parser = IMUParser()
    bad = bytearray(VENDOR_RAW)
    bad[8] ^= 1
    assert parser.feed(b"boot log\r\n" + bytes(bad) + VENDOR_RAW[:1]) == []
    assert parser.feed(VENDOR_RAW[1:11]) == []
    frames = parser.feed(VENDOR_RAW[11:])
    assert len(frames) == 1
    assert parser.rejected_frames == 1 and parser.last_rejection == "imu_bad_checksum"
    assert len(parser.buffer) <= 1


def test_imu_raw_length_rejected_and_other_functions_ignored():
    parser = IMUParser()
    frame = bytes.fromhex("7e2307040000")
    frame += bytes([sum(frame) & 255])
    assert parser.feed(frame) == []
    assert parser.last_rejection == "invalid_imu_raw_payload_length"
    other = bytes.fromhex("7e230526")
    assert parser.feed(other + bytes([sum(other) & 255])) == []


def test_uwb_fragmented_frame_little_endian_cm_sequence():
    raw = b"mr\x02\x05" + struct.pack("<5H", 513, 500, 500, 400, 500) + b"\r\n"
    parser = UWBParser()
    assert parser.feed(b"init pass\r\n" + raw[:4]) == []
    frame = parser.feed(raw[4:])[0]
    assert frame.tag_id == 5 and frame.sequence == 513
    assert frame.ranges_m == (5, 5, 4)
    assert UWBParser().feed(raw[:-2] + b"\n\r")[0] == frame


def test_uwb_corrupt_boundary_duplicate_and_out_of_range():
    parser = UWBParser()
    good = encode_uwb(5, 0, (5, 5, 4))
    duplicate = bytearray(good)
    duplicate[12] ^= 1
    assert parser.feed(good[:-2] + b"XX" + bytes(duplicate) + encode_uwb(5, 1, (31, 5, 4)) + good) == [UWBParser().feed(good)[0]]
    assert parser.rejected_frames == 3
    assert parser.last_rejection == "uwb_range_out_of_bounds"


def test_parser_input_buffers_bounded():
    for parser in (UWBParser(), IMUParser()):
        parser.feed(b"x" * 100_000)
        assert len(parser.buffer) <= 1
        assert parser.last_rejection == "buffer_overflow"


def test_trilateration_recovers_point_and_rejects_inconsistent_ranges():
    anchors = TelemetryConfig().model_dump()["anchors"]
    point = (3.2, 2.5)
    distances = tuple(math.hypot(point[0] - a["x_m"], point[1] - a["y_m"]) for a in anchors)
    result = trilaterate(anchors, distances)
    assert result["x_m"] == pytest.approx(point[0])
    assert result["y_m"] == pytest.approx(point[1])
    assert result["quality"] == "good" and result["residual_m"] < 1e-8
    with pytest.raises(ValueError, match="inconsistent_range_geometry"):
        trilaterate(anchors, (1, 1, 1))
    with pytest.raises(ValueError, match="position_residual_exceeded"):
        trilaterate(anchors, tuple(d + 2 for d in distances))


def test_trilateration_geometry_nonfinite_and_outside_quality():
    anchors = TelemetryConfig().model_dump()["anchors"]
    with pytest.raises(ValueError, match="invalid_distance"):
        trilaterate(anchors, (float("nan"), 1, 1))
    flat = [{"x_m": i, "y_m": 0} for i in range(3)]
    with pytest.raises(ValueError, match="degenerate_anchor_geometry"):
        trilaterate(flat, (3, 2, 1))
    point = (9, 1)
    ranges = tuple(math.hypot(point[0] - a["x_m"], point[1] - a["y_m"]) for a in anchors)
    assert trilaterate(anchors, ranges)["quality"] == "degraded"
