import asyncio
import math
import os
import pty
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from showcase.telemetry import TelemetryService, register_telemetry
from showcase.telemetry.parsers import encode_imu, encode_uwb


def activate_fixture(service, mode="serial"):
    service.source_mode = mode
    for state in service.connections.values():
        state["state"] = "connected" if mode == "serial" else "simulation"


def fixture_ranges(service):
    return tuple(math.hypot(3 - a.x_m, 2 - a.y_m) for a in service.config.anchors)


def test_service_freshness_rejections_sequence_wrap_and_disconnect():
    now = [1000.0]
    service = TelemetryService(clock=lambda: now[0])
    activate_fixture(service)
    service.ingest("uwb", encode_uwb(5, 65535, fixture_ranges(service)))
    service.ingest("uwb", encode_uwb(5, 0, fixture_ranges(service)))
    service.ingest("uwb", encode_uwb(5, 0, fixture_ranges(service)))
    service.ingest("uwb", encode_uwb(6, 1, fixture_ranges(service)))
    service.ingest("imu", encode_imu(1, -2, 9.80665))
    snapshot = service.snapshot()
    assert len(snapshot["uwb"]["history"]) == 2
    assert snapshot["uwb"]["position_valid"]
    assert snapshot["uwb"]["rejected_frames"] == 2
    assert snapshot["imu"]["acceleration"]["y"] < 0
    now[0] += 3
    # Bytes arriving alone must never refresh an expired valid sample.
    service.ingest("imu", b"bad UART bytes")
    assert not service.snapshot()["uwb"]["position_valid"]
    assert service.snapshot()["imu"]["acceleration"]["stale"]
    now[0] = 1000
    asyncio.run(service.disconnect())
    snapshot = service.snapshot()
    assert snapshot["connections"]["uwb"]["state"] == "disconnected"
    assert not snapshot["uwb"]["position_valid"] and len(snapshot["uwb"]["history"]) == 2
    with pytest.raises(ValueError, match="idle"):
        service.ingest("uwb", encode_uwb(5, 1, fixture_ranges(service)))


def test_bounded_history_and_slow_subscriber_keep_latest():
    service = TelemetryService()
    asyncio.run(service.configure({"history_limit": 10}))
    activate_fixture(service, "simulation")
    queue = service.subscribe()
    for sequence in range(100):
        service.ingest("uwb", encode_uwb(5, sequence, fixture_ranges(service)))
        service.ingest("imu", encode_imu(0, 0, 9.80665))
        service.publish()
    snapshot = queue.get_nowait()
    assert queue.empty() and snapshot["uwb"]["position"]["sequence"] == 99
    assert len(snapshot["uwb"]["history"]) == len(snapshot["imu"]["history"]) == 10
    assert service.dropped_snapshots == 100
    assert snapshot["stream"]["dropped_snapshots"] == 100
    service.unsubscribe(queue)
    assert not service.subscribers


def test_api_idle_demo_stop_config_and_ws_cleanup():
    app = FastAPI()
    service = register_telemetry(app)
    with TestClient(app) as client:
        initial = client.get("/api/telemetry/snapshot").json()
        assert initial["source_mode"] == "idle" and initial["uwb"]["position"] is None
        assert client.post("/api/telemetry/connect").status_code == 422
        assert client.post("/api/telemetry/config", json={"plane_height_m": 1}).status_code == 422
        assert client.post("/api/telemetry/config", json={"secret": "bad"}).status_code == 422
        with client.websocket_connect("/ws/telemetry") as ws:
            assert ws.receive_json()["source_mode"] == "idle"
            assert client.post("/api/telemetry/demo/start").status_code == 200
            for _ in range(10):
                snapshot = ws.receive_json()
                if snapshot["uwb"]["position"]:
                    break
            assert snapshot["source_mode"] == "simulation"
            assert snapshot["connections"]["uwb"]["state"] == "simulation"
            assert not snapshot["hardware_verified"]
            assert snapshot["imu"]["acceleration"]["source_mode"] == "simulation"
            assert client.post("/api/telemetry/config", json={"tag_id": 6}).status_code == 422
            assert client.post("/api/telemetry/connect").status_code == 422
        for _ in range(20):
            if not service.subscribers:
                break
            time.sleep(0.01)
        assert not service.subscribers
        assert client.post("/api/telemetry/demo/stop").json()["source_mode"] == "idle"
        stopped = client.get("/api/telemetry/snapshot").json()
        assert not stopped["uwb"]["position_valid"] and stopped["uwb"]["history"]
    assert service._heartbeat_task is None and service._demo_task is None


def test_config_anchor_order_degenerate_and_finite_rejected():
    service = TelemetryService()
    anchors = service.config.model_dump()["anchors"]
    flat = [{**a, "y_m": 0} for a in anchors]
    for patch in ({"anchors": flat}, {"anchors": list(reversed(anchors))}, {"stale_after_s": float("nan")}):
        with pytest.raises(ValueError):
            asyncio.run(service.configure(patch))
    assert service.config.anchors[2].y_m == 7


@pytest.mark.asyncio
async def test_real_pyserial_pty_fragmented_read_and_peer_disconnection():
    # A Linux PTY exercises actual pyserial and asynchronous read plumbing; no physical hardware claim.
    master, slave = pty.openpty()
    service = TelemetryService(allow_test_ports=True)
    try:
        await service.configure({"serial": {"uwb": {"port": os.ttyname(slave)}}})
        connected = await service.connect(["uwb"])
        assert connected["connections"]["uwb"]["state"] == "connected"
        frame = encode_uwb(5, 0, fixture_ranges(service))
        os.write(master, frame[:5])
        await asyncio.sleep(0.15)
        assert service.snapshot()["uwb"]["position"] is None
        os.write(master, frame[5:])
        for _ in range(30):
            if service.snapshot()["uwb"]["position"]:
                break
            await asyncio.sleep(0.02)
        assert service.snapshot()["uwb"]["position_valid"]
        assert not service.snapshot()["hardware_verified"]
        os.close(master)
        master = None
        for _ in range(30):
            if service.connections["uwb"]["state"] == "error":
                break
            await asyncio.sleep(0.02)
        assert service.connections["uwb"]["state"] == "error"
        assert service.connections["uwb"]["last_error"]
        assert not service.snapshot()["uwb"]["position_valid"]
    finally:
        await service.close()
        if master is not None:
            os.close(master)
        os.close(slave)


@pytest.mark.asyncio
async def test_imu_pty_and_idle_safety():
    master, slave = pty.openpty()
    service = TelemetryService(allow_test_ports=True)
    try:
        await service.start()
        assert service.source_mode == "idle" and not service._tasks
        await service.configure({"serial": {"imu": {"port": os.ttyname(slave)}}})
        await service.connect(["imu"])
        os.write(master, encode_imu(1, -2, 9.80665))
        for _ in range(30):
            if service.snapshot()["imu"]["acceleration_valid"]:
                break
            await asyncio.sleep(0.02)
        assert service.snapshot()["imu"]["acceleration"]["x"] == pytest.approx(1, abs=0.003)
        with pytest.raises(ValueError, match="disconnect"):
            await service.demo_start()
    finally:
        await service.close()
        os.close(master)
        os.close(slave)
    assert service._heartbeat_task is None


def test_serial_device_paths_controlled():
    service = TelemetryService()
    for path in ("/tmp/framefile", "socket://example.com:9000", "/dev/pts/1", "/dev/serial/by-id/../../etc/passwd"):
        with pytest.raises((ValueError, FileNotFoundError)):
            service._validate_port(path)
