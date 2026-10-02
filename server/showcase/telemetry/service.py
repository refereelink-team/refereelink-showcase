from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import copy
import math
from pathlib import Path
import re
import stat
import time
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .parsers import IMUParser, UWBParser, encode_imu, encode_uwb, trilaterate


class SerialSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str | None = None
    baudrate: Literal[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600] = 115200


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    x_m: float = Field(ge=-30, le=30)
    y_m: float = Field(ge=-30, le=30)
    z_m: float = Field(default=0.3, ge=0, le=10)


class TelemetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    anchors: list[Anchor] = Field(default_factory=lambda: [
        Anchor(id="0x0001", x_m=0, y_m=0),
        Anchor(id="0x0002", x_m=8, y_m=0),
        Anchor(id="0x0003", x_m=4, y_m=7),
    ])
    tag_id: int = Field(default=5, ge=1, le=255)
    plane_height_m: float = Field(default=0.3, ge=0, le=10)
    history_limit: int = Field(default=240, ge=10, le=2000)
    stale_after_s: float = Field(default=2.0, ge=0.2, le=30)
    maximum_residual_m: float = Field(default=0.5, ge=0.02, le=2)
    serial: dict[str, SerialSettings] = Field(default_factory=lambda: {
        "uwb": SerialSettings(), "imu": SerialSettings()
    })

    @model_validator(mode="after")
    def validate_layout(self):
        if [a.id for a in self.anchors] != ["0x0001", "0x0002", "0x0003"]:
            raise ValueError("three anchors in vendor range order 0x0001,0x0002,0x0003 required")
        if any(abs(a.z_m - self.plane_height_m) > 1e-6 for a in self.anchors):
            raise ValueError("three-anchor 2D requires every anchor and tag at plane_height_m")
        if set(self.serial) != {"uwb", "imu"}:
            raise ValueError("serial must have exactly uwb and imu entries")
        points = [(a.x_m, a.y_m) for a in self.anchors]
        area4 = abs(4 * ((points[1][0] - points[0][0]) * (points[2][1] - points[0][1]) -
                         (points[1][1] - points[0][1]) * (points[2][0] - points[0][0])))
        largest = max(math.dist(points[i], points[j]) for i in range(3) for j in range(i + 1, 3))
        if min(math.dist(points[i], points[j]) for i in range(3) for j in range(i + 1, 3)) < 0.5:
            raise ValueError("anchor separation must be at least 0.5 m")
        if largest > 30 or area4 < largest * largest * 0.04:
            raise ValueError("anchor layout must be noncollinear within 30 m")
        return self


def _connection() -> dict:
    return {"state": "disconnected", "port": None, "last_error": None,
            "last_received_at": None, "last_valid_at": None, "stale": True}


class TelemetryService:
    """One process owns UART readers; snapshots and subscriber queues are bounded."""
    def __init__(self, *, clock: Callable[[], float] = time.time,
                 serial_factory: Callable | None = None, allow_test_ports: bool = False):
        self.clock = clock
        self.config = TelemetryConfig()
        self.source_mode = "idle"
        self.connections = {"uwb": _connection(), "imu": _connection()}
        self.uwb_parser = UWBParser()
        self.imu_parser = IMUParser()
        self.position_history = deque(maxlen=self.config.history_limit)
        self.acceleration_history = deque(maxlen=self.config.history_limit)
        self.ranges_m: list[float] = []
        self.sequence: int | None = None
        self.rejects = {"uwb": 0, "imu": 0}
        self.last_rejection = {"uwb": None, "imu": None}
        self.subscribers: set[asyncio.Queue] = set()
        self.dropped_snapshots = 0
        self._tasks: dict[str, asyncio.Task] = {}
        self._serial_handles: dict = {}
        self._demo_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._serial_factory = serial_factory
        self._allow_test_ports = allow_test_ports
        self._control_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            self.publish()

    def snapshot(self) -> dict:
        now = self.clock()
        connections = copy.deepcopy(self.connections)
        for name, state in connections.items():
            last = state["last_valid_at"]
            state["stale"] = last is None or not 0 <= now - last <= self.config.stale_after_s or state["state"] not in ("connected", "simulation")
        position = copy.deepcopy(self.position_history[-1]) if self.position_history else None
        acceleration = copy.deepcopy(self.acceleration_history[-1]) if self.acceleration_history else None
        if position:
            position["stale"] = connections["uwb"]["stale"] or position["source_mode"] != self.source_mode
        if acceleration:
            acceleration["stale"] = connections["imu"]["stale"] or acceleration["source_mode"] != self.source_mode
        return {
            "schema_version": 1, "source_mode": self.source_mode,
            "hardware_verified": False, "updated_at": now,
            "config": self.config.model_dump(), "connections": connections,
            "uwb": {"position": position, "position_valid": bool(position and not position["stale"]),
                    "ranges_m": list(self.ranges_m), "history": list(self.position_history),
                    "rejected_frames": self.rejects["uwb"] + self.uwb_parser.rejected_frames,
                    "last_rejection": self.last_rejection["uwb"] or self.uwb_parser.last_rejection,
                    "protocol": "bp_twr30_mr_v2", "checksum_available": False},
            "imu": {"acceleration": acceleration, "acceleration_valid": bool(acceleration and not acceleration["stale"]),
                    "history": list(self.acceleration_history),
                    "rejected_frames": self.rejects["imu"] + self.imu_parser.rejected_frames,
                    "last_rejection": self.last_rejection["imu"] or self.imu_parser.last_rejection,
                    "protocol": "yahboom_imu_sensor_7e23", "checksum_available": True},
            "units": {"position": "m", "acceleration": "m/s²", "acceleration_frame": "vendor_sensor",
                      "gravity_included": True, "position_frame": "configured_anchor_plane"},
            "stream": {"subscriber_queue_limit": 1, "dropped_snapshots": self.dropped_snapshots,
                       "history_limit": self.config.history_limit},
            "evidence": {"notice": "模拟数据：仅验证展示与协议处理，设备尚未实测。" if self.source_mode == "simulation" else
                         "硬件未验收：串口打开和有效帧不证明定位精度。三基站仅同平面2D。",
                         "uwb_protocol_verified": True, "imu_protocol_verified": True,
                         "hardware_verified": False},
        }

    def publish(self) -> None:
        for queue in tuple(self.subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                self.dropped_snapshots += 1
        snapshot = self.snapshot()
        for queue in tuple(self.subscribers):
            queue.put_nowait(snapshot)

    def subscribe(self) -> asyncio.Queue:
        if len(self.subscribers) >= 32:
            raise ValueError("telemetry subscriber limit reached")
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(self.snapshot())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def _reject(self, channel: str, reason: str) -> None:
        self.rejects[channel] += 1
        self.last_rejection[channel] = reason

    def ingest(self, channel: str, data: bytes) -> None:
        """Internal UART/fixture entry point, deliberately absent from HTTP."""
        if self.source_mode not in ("serial", "simulation"):
            raise ValueError("cannot ingest while idle")
        if self.connections[channel]["state"] not in ("connected", "simulation"):
            raise ValueError("cannot ingest disconnected channel")
        now = self.clock()
        if data:
            self.connections[channel]["last_received_at"] = now
        if channel == "uwb":
            previous_rejections = self.uwb_parser.rejected_frames
            frames = self.uwb_parser.feed(data)
            if self.uwb_parser.rejected_frames != previous_rejections:
                self.last_rejection[channel] = self.uwb_parser.last_rejection
            for frame in frames:
                if frame.tag_id != self.config.tag_id:
                    self._reject(channel, "unexpected_tag_id")
                    continue
                if self.sequence is not None and not 0 < ((frame.sequence - self.sequence) & 0xffff) < 0x8000:
                    self._reject(channel, "duplicate_or_out_of_order_sequence")
                    continue
                self.sequence = frame.sequence
                try:
                    position = trilaterate(self.config.model_dump()["anchors"], frame.ranges_m, self.config.maximum_residual_m)
                except ValueError as error:
                    self._reject(channel, str(error))
                    continue
                position.update(tag_id=frame.tag_id, sequence=frame.sequence, timestamp=now,
                                source_mode=self.source_mode, z_m=self.config.plane_height_m)
                self.position_history.append(position)
                self.ranges_m = list(frame.ranges_m)
                self.connections[channel]["last_valid_at"] = now
        elif channel == "imu":
            previous_rejections = self.imu_parser.rejected_frames
            frames = self.imu_parser.feed(data)
            if self.imu_parser.rejected_frames != previous_rejections:
                self.last_rejection[channel] = self.imu_parser.last_rejection
            for frame in frames:
                self.acceleration_history.append({"x": frame.x, "y": frame.y, "z": frame.z,
                    "raw_xyz": list(frame.raw_xyz), "timestamp": now, "unit": "m/s²",
                    "source_mode": self.source_mode, "frame": "vendor_sensor", "gravity_included": True})
                self.connections[channel]["last_valid_at"] = now
        else:
            raise ValueError("unknown telemetry channel")

    async def configure(self, patch: dict) -> dict:
        async with self._control_lock:
            if self.source_mode != "idle":
                raise ValueError("disconnect/stop simulation before changing configuration")
            current = self.config.model_dump()
            for key, value in patch.items():
                if key == "serial" and isinstance(value, dict):
                    for channel, params in value.items():
                        if channel not in current["serial"]:
                            raise ValueError("unknown serial channel")
                        current["serial"][channel].update(params)
                else:
                    current[key] = value
            candidate = TelemetryConfig.model_validate(current)
            # Geometry changes invalidate the retained display, including old histories.
            self.config = candidate
            self.position_history = deque(maxlen=candidate.history_limit)
            self.acceleration_history = deque(maxlen=candidate.history_limit)
            self.ranges_m = []
            self.sequence = None
            for channel in self.connections:
                self.connections[channel] = _connection()
            self.publish()
            return self.snapshot()

    def _validate_port(self, port: str) -> None:
        if not re.fullmatch(r"/dev/(?:tty(?:USB|ACM|S|AMA)\d+|serial/by-id/[A-Za-z0-9_.:+-]+|pts/\d+)", port):
            raise ValueError("serial port must be a supported /dev UART or /dev/serial/by-id path")
        if port.startswith("/dev/pts/") and not self._allow_test_ports:
            raise ValueError("pseudo terminals are disabled outside tests")
        if not stat.S_ISCHR(Path(port).stat().st_mode):
            raise ValueError("serial port must be a character device")

    async def connect(self, devices: list[str] | None = None) -> dict:
        async with self._control_lock:
            if self.source_mode == "simulation":
                raise ValueError("stop simulation before connecting hardware")
            devices = devices or [name for name, settings in self.config.serial.items() if settings.port]
            if not devices or len(set(devices)) != len(devices) or any(n not in self.connections for n in devices):
                raise ValueError("configure a UART port and select uwb and/or imu")
            settings = {name: self.config.serial[name] for name in devices}
            # Validate the complete request before opening any port.
            for name, parameters in settings.items():
                if not parameters.port:
                    raise ValueError(f"{name} port is not configured")
                if self._serial_factory is None:
                    self._validate_port(parameters.port)
                if any(state["state"] == "connected" and state["port"] == parameters.port
                       for other, state in self.connections.items() if other != name):
                    raise ValueError("each telemetry channel must use a distinct UART")
            if len({p.port for p in settings.values()}) != len(settings):
                raise ValueError("each telemetry channel must use a distinct UART")
            if self.source_mode == "idle":
                self.position_history.clear()
                self.acceleration_history.clear()
                self.ranges_m = []
            self.source_mode = "serial"
            for name, parameters in settings.items():
                if name in self._tasks:
                    continue
                self.connections[name].update(state="connecting", port=parameters.port, last_error=None,
                                               last_received_at=None, last_valid_at=None)
                try:
                    factory = self._serial_factory
                    if factory is None:
                        import serial
                        factory = serial.Serial
                    handle = await asyncio.to_thread(factory, port=parameters.port, baudrate=parameters.baudrate,
                                                     timeout=0.1, bytesize=8, parity="N", stopbits=1, exclusive=True)
                    self._serial_handles[name] = handle
                    if name == "uwb":
                        self.sequence = None
                        self.uwb_parser.buffer.clear()
                    else:
                        self.imu_parser.buffer.clear()
                    self.connections[name]["state"] = "connected"
                    self._tasks[name] = asyncio.create_task(self._serial_reader(name, handle))
                except Exception as error:
                    self.connections[name].update(state="error", last_error=f"{type(error).__name__}: {error}")
            self.publish()
            return self.snapshot()

    async def _serial_reader(self, name: str, handle) -> None:
        try:
            while True:
                data = await asyncio.to_thread(handle.read, 512)
                if data:
                    self.ingest(name, data)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.connections[name].update(state="error", last_error=f"{type(error).__name__}: {error}")
            self.publish()
        finally:
            with suppress(Exception):
                await asyncio.to_thread(handle.close)
            self._serial_handles.pop(name, None)
            self._tasks.pop(name, None)

    async def _disconnect(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for state in self.connections.values():
            state["state"] = "disconnected"
        self.source_mode = "idle"
        self.sequence = None
        self.uwb_parser.buffer.clear()
        self.imu_parser.buffer.clear()

    async def disconnect(self) -> dict:
        async with self._control_lock:
            await self._stop_demo()
            await self._disconnect()
            self.publish()
            return self.snapshot()

    async def demo_start(self, rate_hz: float = 10) -> dict:
        if not math.isfinite(rate_hz) or not 1 <= rate_hz <= 20:
            raise ValueError("simulation rate must be between 1 and 20 Hz")
        async with self._control_lock:
            if self.source_mode == "serial":
                raise ValueError("disconnect UART before starting simulation")
            await self._stop_demo()
            self.position_history.clear()
            self.acceleration_history.clear()
            self.ranges_m = []
            self.sequence = None
            self.source_mode = "simulation"
            for name in self.connections:
                self.connections[name] = _connection()
                self.connections[name]["state"] = "simulation"
            self._demo_task = asyncio.create_task(self._demo(rate_hz))
            self.publish()
            return self.snapshot()

    async def _demo(self, rate_hz: float) -> None:
        tick = 0
        anchors = self.config.model_dump()["anchors"]
        # A barycentric orbit stays inside any configured triangle.
        while True:
            phase = tick / rate_hz
            w0 = 0.34 + 0.13 * math.sin(phase * 0.9)
            w1 = 0.33 + 0.12 * math.cos(phase * 0.9)
            w2 = 1 - w0 - w1
            x = sum(w * a["x_m"] for w, a in zip((w0, w1, w2), anchors))
            y = sum(w * a["y_m"] for w, a in zip((w0, w1, w2), anchors))
            ranges = tuple(math.hypot(x - a["x_m"], y - a["y_m"]) for a in anchors)
            self.ingest("uwb", encode_uwb(self.config.tag_id, tick, ranges))
            self.ingest("imu", encode_imu(0.9 * math.sin(phase * 2), 0.7 * math.cos(phase * 1.5),
                                          9.80665 + 0.2 * math.sin(phase * 3)))
            self.publish()
            tick += 1
            await asyncio.sleep(1 / rate_hz)

    async def _stop_demo(self) -> None:
        if self._demo_task:
            self._demo_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._demo_task
            self._demo_task = None

    async def demo_stop(self) -> dict:
        async with self._control_lock:
            await self._stop_demo()
            if self.source_mode == "simulation":
                self.source_mode = "idle"
                for state in self.connections.values():
                    state["state"] = "disconnected"
            self.publish()
            return self.snapshot()

    async def close(self) -> None:
        await self.disconnect()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
