from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
import socket
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from .catalog import MediaCatalog
from .gateway import create_gateway
from .jobs import JobManager
from .settings import Settings


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    case_id: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    catalog = MediaCatalog(settings.media_root)
    manager = JobManager(settings, catalog)
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=8))
    gpu = {'device': 'unverified', 'host': socket.gethostname(), 'cuda_available': False}

    @asynccontextmanager
    async def lifespan(app):
        if settings.enforce_cuda:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError('Showcase backend must run on the remote CUDA host')
            gpu.update(device='cuda', cuda_available=True, gpu_name=torch.cuda.get_device_name())
        telemetry = getattr(app.state, 'telemetry', None)
        if telemetry is not None:
            await telemetry.start()
        manager.start()
        try:
            yield
        finally:
            manager.stop()
            await client.aclose()
            telemetry = getattr(app.state, 'telemetry', None)
            if telemetry is not None:
                await telemetry.close()

    app = FastAPI(title='RefereeLink Showcase', lifespan=lifespan)
    app.state.catalog, app.state.jobs = catalog, manager
    app.include_router(create_gateway(client, settings.upstream))
    try:
        from .telemetry import TelemetryService
        from .telemetry.router import create_router
        app.state.telemetry = TelemetryService()
        app.include_router(create_router(app.state.telemetry))
    except ModuleNotFoundError as exc:
        if exc.name != 'showcase.telemetry':
            raise

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'backend': gpu, 'role': 'showcase'}

    @app.get('/api/catalog')
    async def get_catalog():
        cases, warning = [], None
        try:
            response = await client.get(settings.upstream + '/api/multiview/cases', timeout=5)
            response.raise_for_status()
            cases = response.json()['cases']
        except (httpx.HTTPError, ValueError, KeyError):
            warning = '现场多视角服务暂不可用；已准备的视频仍可展示。'
        return {'cases': cases, 'clips': catalog.clips(), 'backend': gpu,
                'upstream_warning': warning}

    @app.get('/media/{path:path}')
    def media(path: str):
        target = catalog.safe_path(path)
        if target.suffix.lower() not in {'.mp4', '.jpg', '.png'}:
            raise HTTPException(404, 'Media not found')
        return FileResponse(target, headers={'Cache-Control': 'no-cache'})

    @app.post('/api/experiments/jobs', status_code=202)
    def create_job(payload: JobRequest):
        return manager.submit(payload.kind, payload.case_id)

    @app.get('/api/experiments/jobs')
    def list_jobs():
        return {'jobs': manager.list()}

    @app.get('/api/experiments/jobs/{job_id}')
    def get_job(job_id: str):
        return manager.get(job_id)

    @app.get('/api/experiments/artifacts/{artifact_id}')
    def artifact(artifact_id: str):
        item = catalog.artifact(artifact_id)
        return FileResponse(catalog.safe_path(item['path']), headers={'Cache-Control': 'no-cache'})

    dist = Path(__file__).resolve().parents[2] / 'web/dist'
    if (dist / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='frontend-assets')

    @app.get('/', include_in_schema=False)
    def frontend():
        index = dist / 'index.html'
        if not index.exists():
            raise HTTPException(503, 'Build the showcase web frontend first')
        return FileResponse(index, media_type='text/html', headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
