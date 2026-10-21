from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from k8s_guardrail.validator import ManifestVerificationEngine

INDEX = (Path(__file__).parent / "static" / "index.html").read_text()
MAX_CHARS = 100_000


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(INDEX)


async def api_check(request: Request) -> JSONResponse:
    manifest = str((await request.json()).get("manifest", ""))
    if len(manifest) > MAX_CHARS:
        return JSONResponse({"error": "Manifest too large (max 100,000 characters)."}, 400)
    return JSONResponse(ManifestVerificationEngine().verify_string(manifest).to_dict())


app = Starlette(routes=[Route("/", index), Route("/api/check", api_check, methods=["POST"])])


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
