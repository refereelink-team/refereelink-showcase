"""Synthetic wire/PTY and host-compiled C evidence, never STM32 or radio acceptance."""
import asyncio
import binascii
import ctypes
import math
import os
from pathlib import Path
import pty
import shutil
import struct
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from showcase.telemetry import TelemetryService, register_telemetry
from showcase.telemetry.parsers import BridgeParser, BridgedIMUFrame, encode_bridge_imu, encode_uwb


RAW = bytes.fromhex('7E 23 17 04 51 00 99 FF F7 07 F5 FF 00 00 00 00 1B 01 40 FA 5D FD 47')


def packet(sequence=1, tag=5, payload=RAW):
    return encode_bridge_imu(tag, sequence, 123456, payload)


def ranges(service):
    return tuple(math.hypot(3 - a.x_m, 2 - a.y_m) for a in service.config.anchors)


def active_bridge(clock=lambda: 1000):
    service = TelemetryService(clock=clock)
    asyncio.run(service.configure({'imu_transport': 'uwb_bridge_v1'}))
    service.source_mode = 'serial'
    for state in service.connections.values():
        state['state'] = 'connected'
    return service


@pytest.mark.parametrize('split', range(1, 36))
def test_shared_stream_splits_and_adjacent_frames(split):
    parser = BridgeParser()
    uwb = encode_uwb(5, 1, (5, 5, 4))
    assert parser.feed(b'init pass\r\n' + packet()[:split]) == []
    assert parser.feed(packet()[split:] + uwb + packet(2)) == [
        BridgedIMUFrame(5, 1, 123456, RAW), uwb, BridgedIMUFrame(5, 2, 123456, RAW)]
    assert not parser.buffer


def test_bridge_preserves_header_prefixes_and_bounds_memory():
    parser = BridgeParser()
    assert not parser.feed(b'x' * 100000 + b'm')
    assert parser.rejected_frames == 1 and len(parser.buffer) == 1
    assert parser.feed(packet()[1:]) == [BridgedIMUFrame(5, 1, 123456, RAW)]


def test_crc_and_length_failures_never_parse_nested_mr_payload():
    nested = encode_uwb(5, 100, (5, 5, 4))
    raw = b'\x7e\x23\x17\x04' + nested + b'\x00\x00'
    raw += bytes([sum(raw) & 255])
    for offset in (2, 10, 35):
        invalid = bytearray(packet(payload=raw))
        invalid[offset] ^= 1
        parser = BridgeParser()
        assert parser.feed(invalid + packet()) == [BridgedIMUFrame(5, 1, 123456, RAW)]
        assert parser.rejected_frames == 1
    assert BridgeParser().feed(packet(payload=raw)) == [BridgedIMUFrame(5, 1, 123456, raw)]


def test_version_errors_recover_without_accepting_unknown_format():
    parser = BridgeParser()
    invalid = bytearray(packet())
    invalid[2] = 2
    assert parser.feed(invalid + packet()) == [BridgedIMUFrame(5, 1, 123456, RAW)]
    assert parser.last_rejection == 'invalid_bridge_version'


def test_both_channels_have_independent_sequences_freshness_and_rejections():
    now = [1000]
    service = active_bridge(clock=lambda: now[0])
    service.ingest('uwb', encode_uwb(5, 10, ranges(service)) + packet(65535))
    assert service.snapshot()['imu']['acceleration_valid']
    assert service.snapshot()['uwb']['position_valid']
    service.ingest('uwb', packet(0))
    service.ingest('uwb', packet(0))
    service.ingest('uwb', packet(1, tag=6))
    invalid_raw = bytearray(RAW)
    invalid_raw[-1] ^= 1
    service.ingest('uwb', packet(30000, payload=invalid_raw))
    service.ingest('uwb', packet(1))
    snap = service.snapshot()
    assert snap['imu']['rejected_frames'] == 3
    assert snap['imu']['acceleration']['sequence'] == 1
    assert snap['imu']['acceleration']['sample_ms'] == 123456
    assert snap['uwb']['position']['sequence'] == 10
    now[0] += 3
    service.ingest('uwb', encode_uwb(5, 11, ranges(service)))
    assert service.snapshot()['uwb']['position_valid'] and not service.snapshot()['imu']['acceleration_valid']
    now[0] += 3
    service.ingest('uwb', packet(2))
    assert service.snapshot()['imu']['acceleration_valid'] and not service.snapshot()['uwb']['position_valid']
    with pytest.raises(ValueError, match='A1'):
        service.ingest('imu', RAW)
    assert not service.snapshot()['hardware_verified']


def test_stock_firmware_and_direct_raw_imu_cannot_unlock_bridge_imu():
    service = active_bridge()
    service.ingest('uwb', encode_uwb(5, 1, ranges(service)) + RAW)
    snap = service.snapshot()
    assert snap['uwb']['position_valid'] and not snap['imu']['acceleration_valid']
    assert not snap['imu']['history']
    assert snap['transport']['firmware_required']


def test_usb_mode_consumes_but_never_uses_imu_envelopes_from_custom_a1():
    service = TelemetryService()
    service.source_mode = 'serial'
    for state in service.connections.values():
        state['state'] = 'connected'
    nested = encode_uwb(5, 100, ranges(service))
    raw = b'\x7e\x23\x17\x04' + nested + b'\x00\x00'
    raw += bytes([sum(raw) & 255])
    service.ingest('uwb', packet(payload=raw))
    assert not service.position_history and not service.acceleration_history
    service.ingest('uwb', encode_uwb(5, 1, ranges(service)))
    assert service.snapshot()['uwb']['position_valid']
    assert not service.snapshot()['imu']['acceleration_valid']
    service.ingest('imu', RAW)
    assert service.snapshot()['imu']['acceleration_valid']


def test_unknown_inner_imu_function_does_not_advance_sample_sequence():
    service = active_bridge()
    payload = bytearray(RAW)
    payload[3] = 0x26
    payload[-1] = sum(payload[:-1]) & 255
    service.ingest('uwb', packet(30000, payload=payload))
    assert not service.acceleration_history and service.imu_sequence is None
    assert service.snapshot()['imu']['last_rejection'] == 'invalid_bridge_imu_payload'
    service.ingest('uwb', packet(1))
    assert service.imu_sequence == 1


@pytest.mark.asyncio
async def test_configuration_modes_and_shared_open_failure():
    opened = []
    def fail(**kwargs):
        opened.append(kwargs['port'])
        raise OSError('synthetic open failure')
    service = TelemetryService(serial_factory=fail)
    with pytest.raises(ValueError):
        await service.configure({'imu_transport': 'unknown'})
    await service.configure({'serial': {'imu': {'port': '/dev/ttyUSB1'}}})
    with pytest.raises(ValueError, match='clear'):
        await service.configure({'imu_transport': 'uwb_bridge_v1'})
    await service.configure({'imu_transport': 'uwb_bridge_v1', 'serial': {
        'uwb': {'port': '/dev/ttyUSB0'}, 'imu': {'port': None}}})
    with pytest.raises(ValueError, match='together'):
        await service.connect(['imu'])
    with pytest.raises(ValueError):
        await service.connect([])
    snap = await service.connect()
    assert opened == ['/dev/ttyUSB0']
    assert snap['connections']['uwb']['state'] == snap['connections']['imu']['state'] == 'error'
    with pytest.raises(ValueError, match='disconnect'):
        await service.configure({'imu_transport': 'usb'})
    await service.close()


async def until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


@pytest.mark.asyncio
async def test_one_real_pty_shared_lifecycle_mode_switch_and_reconnect():
    master, slave = pty.openpty()
    service = TelemetryService(allow_test_ports=True)
    try:
        port = os.ttyname(slave)
        await service.configure({'imu_transport': 'uwb_bridge_v1', 'serial': {'uwb': {'port': port}}})
        connected = await service.connect()
        assert list(service._serial_handles) == list(service._tasks) == ['uwb']
        assert connected['connections']['uwb']['port'] == connected['connections']['imu']['port'] == port
        os.write(master, encode_uwb(5, 1, ranges(service)) + packet()[:9])
        await until(lambda: service.snapshot()['uwb']['position_valid'])
        assert not service.snapshot()['imu']['acceleration_valid']
        os.write(master, packet()[9:])
        await until(lambda: service.snapshot()['imu']['acceleration_valid'])
        handle = service._serial_handles['uwb']
        await service.connect(['uwb', 'imu'])
        assert service._serial_handles == {'uwb': handle}
        with pytest.raises(ValueError, match='disconnect'):
            await service.configure({'imu_transport': 'usb'})
        os.write(master, packet(2)[:9])
        await until(lambda: len(service.bridge_parser.buffer) == 9)
        await service.disconnect()
        assert not service._tasks and not service._serial_handles
        assert all(c['stale'] for c in service.snapshot()['connections'].values())
        await service.connect()
        os.write(master, packet(2)[9:])
        await until(lambda: len(service.bridge_parser.buffer) == 1)
        assert not service.snapshot()['imu']['acceleration_valid']
        os.write(master, packet(0))
        await until(lambda: service.snapshot()['imu']['acceleration_valid'])
        assert service.snapshot()['imu']['acceleration']['sequence'] == 0
        os.close(master)
        master = None
        await until(lambda: service.connections['uwb']['state'] == 'error')
        await until(lambda: not service._tasks)
        assert service.connections['imu']['state'] == 'error'
        assert not service.snapshot()['imu']['acceleration_valid']
        await service.disconnect()
        snap = await service.configure({'imu_transport': 'usb', 'serial': {
            'uwb': {'port': None}, 'imu': {'port': port}}})
        assert not snap['uwb']['history'] and not snap['imu']['history']
    finally:
        await service.close()
        if master is not None:
            os.close(master)
        os.close(slave)


def test_api_mode_validation_and_no_auto_simulation():
    app = FastAPI()
    service = register_telemetry(app)
    with TestClient(app) as client:
        assert client.get('/api/telemetry/snapshot').json()['config']['imu_transport'] == 'usb'
        assert client.post('/api/telemetry/config', json={'imu_transport': 'uwb_bridge_v1'}).status_code == 200
        assert client.post('/api/telemetry/connect').status_code == 422
        snap = client.get('/api/telemetry/snapshot').json()
        assert snap['source_mode'] == 'idle' and not service._tasks
        assert client.post('/api/telemetry/demo/start').json()['source_mode'] == 'simulation'
        assert client.post('/api/telemetry/config', json={'imu_transport': 'usb'}).status_code == 422
        assert client.post('/api/telemetry/demo/stop').status_code == 200
        assert client.post('/api/telemetry/config', json={'imu_transport': 'usb'}).status_code == 200


@pytest.fixture(scope='module')
def c_codec(tmp_path_factory):
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('host C compiler required for portable codec cross-check, not a hardware test')
    root = Path(__file__).resolve().parents[1]
    lib = tmp_path_factory.mktemp('bridge-c') / 'bridge.so'
    subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-pedantic', '-fPIC', '-shared',
                    str(root / 'firmware/uwb_imu_bridge/bridge_protocol.c'), '-o', str(lib)], check=True)
    codec = ctypes.CDLL(str(lib))
    u8p = ctypes.POINTER(ctypes.c_uint8)
    codec.rl_bridge_encode.argtypes = [u8p, ctypes.c_size_t, ctypes.c_uint8, ctypes.c_uint16,
                                      ctypes.c_uint32, u8p, ctypes.c_size_t]
    codec.rl_bridge_valid.argtypes = [u8p, ctypes.c_size_t]
    codec.rl_bridge_crc16.argtypes = [u8p, ctypes.c_size_t]
    codec.rl_bridge_crc16.restype = ctypes.c_uint16
    return codec


def array(data):
    return (ctypes.c_uint8 * len(data)).from_buffer_copy(data)


@pytest.mark.parametrize('sequence, sample_ms', [(0, 0), (65535, 0xffffffff), (1, 123456)])
def test_portable_c_codec_matches_python_byte_for_byte(c_codec, sequence, sample_ms):
    out = (ctypes.c_uint8 * 36)()
    assert c_codec.rl_bridge_crc16(array(b'123456789'), 9) == 0x29b1
    assert c_codec.rl_bridge_encode(out, 36, 5, sequence, sample_ms, array(RAW), 23) == 36
    expected = encode_bridge_imu(5, sequence, sample_ms, RAW)
    assert bytes(out) == expected
    assert c_codec.rl_bridge_valid(out, 36) == 1
    out[20] ^= 1
    assert c_codec.rl_bridge_valid(out, 36) == 0
    assert c_codec.rl_bridge_encode(out, 35, 5, sequence, sample_ms, array(RAW), 23) == 0


def test_c_codec_rejects_bad_inner_frame_even_with_recomputed_crc(c_codec):
    bad = bytearray(packet())
    bad[33] ^= 1
    bad[-2:] = struct.pack('<H', binascii.crc_hqx(bad[:-2], 0xffff))
    assert c_codec.rl_bridge_valid(array(bad), 36) == 0
    out = (ctypes.c_uint8 * 36)()
    assert c_codec.rl_bridge_encode(out, 36, 5, 1, 1, array(bad[11:34]), 23) == 0
    assert c_codec.rl_bridge_encode(out, 36, 0, 1, 1, array(RAW), 23) == 0
