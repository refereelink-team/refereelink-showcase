from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import APIRouter, Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .service import TelemetryService


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    rate_hz: float = Field(default=10, ge=1, le=20)


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    devices: list[Literal["uwb", "imu"]] | None = None


def create_router(service: TelemetryService | None = None) -> APIRouter:
    service = service or TelemetryService()
    router = APIRouter(tags=["telemetry"])
    router.telemetry_service = service

    @router.get("/api/telemetry/snapshot")
    async def snapshot():
        return service.snapshot()

    @router.post("/api/telemetry/config")
    async def config(patch: dict = Body(...)):
        try:
            return await service.configure(patch)
        except (ValueError, ValidationError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/api/telemetry/connect")
    async def connect(request: ConnectRequest | None = Body(default=None)):
        try:
            return await service.connect(request.devices if request else None)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/api/telemetry/disconnect")
    async def disconnect():
        return await service.disconnect()

    @router.post("/api/telemetry/demo/start")
    async def demo_start(request: DemoRequest | None = Body(default=None)):
        try:
            return await service.demo_start(request.rate_hz if request else 10)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/api/telemetry/demo/stop")
    async def demo_stop():
        return await service.demo_stop()

    @router.websocket("/ws/telemetry")
    async def stream(websocket: WebSocket):
        await websocket.accept()
        try:
            queue = service.subscribe()
        except ValueError:
            await websocket.close(code=1013, reason="subscriber capacity reached")
            return
        async def send():
            while True:
                await websocket.send_json(await queue.get())
                await asyncio.sleep(0.1)
        async def receive():
            while True:
                await websocket.receive_text()
        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.unsubscribe(queue)
            for task in (sender, receiver):
                task.cancel()
            for task in (sender, receiver):
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await task
    return router


def register_telemetry(app: FastAPI, service: TelemetryService | None = None) -> TelemetryService:
    service = service or TelemetryService()
    app.include_router(create_router(service))
    app.state.telemetry = service
    previous_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            await service.start()
            try:
                yield state
            finally:
                await service.close()
    app.router.lifespan_context = lifespan
    return service
