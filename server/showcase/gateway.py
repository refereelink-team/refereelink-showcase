from __future__ import annotations
from urllib.parse import unquote
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask


def create_gateway(client: httpx.AsyncClient, upstream: str) -> APIRouter:
    router = APIRouter()

    @router.api_route('/api/multiview/{path:path}', methods=['GET', 'POST', 'PUT'])
    async def multiview(path: str, request: Request):
        decoded = unquote(path)
        if any(part in {'.', '..'} for part in decoded.split('/')) or '\\' in decoded:
            raise HTTPException(400, 'Invalid resource path')
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() in {'range', 'accept', 'content-type', 'if-none-match'}}
        headers['Accept-Encoding'] = 'identity'
        url = upstream + '/api/multiview/' + path
        try:
            response = await client.send(client.build_request(
                request.method, url, params=request.query_params,
                headers=headers, content=await request.body()), stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, 'Remote camera backend unavailable') from exc
        forwarded = {name: value for name, value in response.headers.items()
                     if name.lower() in {'content-type', 'content-length', 'content-range',
                                          'accept-ranges', 'cache-control', 'etag', 'last-modified'}}
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers=forwarded, background=BackgroundTask(response.aclose))
    return router
