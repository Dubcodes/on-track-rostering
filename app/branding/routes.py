from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.branding.service import favicon_asset

router = APIRouter(prefix="/branding")


@router.get("/favicon", include_in_schema=False)
def favicon(request: Request) -> Response:
    asset = favicon_asset()
    etag = f'"{asset.digest}"'
    headers = {
        "Cache-Control": "no-cache, max-age=0, must-revalidate",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=asset.content, media_type=asset.media_type, headers=headers)
