"""
FastAPI Backend Entrypoint and Real-Time WebSocket Server.
============================================================
Target Path: app/main.py
"""

import os
import json
import logging
import subprocess
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse
from typing import Optional
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.database import init_db
from app.core.ws_manager import ws_manager
from app.services.queue_manager import BatchQueueManager
from app.scraper.manager import ScraperManager
from app.services.sample_media import seed_sample_videos

logger = logging.getLogger("app.main")


def _seed_sample_media() -> None:
    """Seeds playable Douyin-style sample MP4s (logo + Chinese hardsub + speech)."""
    try:
        seed_sample_videos(settings.RAW_INPUT_DIR, force=False)
    except Exception as e:
        logger.warning(f"Sample media seeding failed: {e}")


# Call directory creation & sample media seeding on module import so files exist for test setups
settings.ensure_directories()
_seed_sample_media()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager handling application startup and shutdown events."""
    logger.info("Initializing application storage directories...")
    settings.ensure_directories()
    _seed_sample_media()

    logger.info(f"Initializing SQLite database schema at: {settings.DB_PATH}")
    init_db(settings.DB_PATH)

    logger.info("Starting Batch Queue Manager workers...")
    queue_mgr = BatchQueueManager(
        db_path=settings.DB_PATH,
        max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS
    )
    scraper_mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)

    # Register WebSocket broadcast callback
    queue_mgr.register_callback(ws_manager.on_queue_update)
    await queue_mgr.start()

    # Store singletons on app.state
    app.state.queue_manager = queue_mgr
    app.state.ws_manager = ws_manager
    app.state.scraper_manager = scraper_mgr

    yield

    logger.info("Stopping Batch Queue Manager workers...")
    await queue_mgr.stop()
    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Video Downloader, Watermark Remover & Reup Processing System",
    description="Backend API and WebSocket System for Video Reup Pipeline",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"],
)


# Include REST API v1 Router
from app.api.router import api_router
app.include_router(api_router, prefix="/api/v1")

# Mount React frontend dist assets if present
FRONTEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
FRONTEND_DIST = os.path.join(FRONTEND_ROOT, "dist")
FRONTEND_PUBLIC = os.path.join(FRONTEND_ROOT, "public")
VITE_ORIGIN = os.environ.get("VITE_ORIGIN", "http://127.0.0.1:5273")

if os.path.exists(FRONTEND_DIST):
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="react-assets")

# Mount Static Files Directory if available
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Health Check Endpoints
@app.get("/health")
@app.get("/api/v1/health")
async def health_check():
    return {"status": "ok", "system": "Reup Processing Engine"}


@app.get("/favicon.svg")
@app.get("/favicon.ico")
async def favicon():
    pub = os.path.join(FRONTEND_PUBLIC, "favicon.svg")
    if os.path.isfile(pub):
        return FileResponse(pub)
    return Response(status_code=204)


def _frontend_file(rel_path: str):
    rel = (rel_path or "index.html").lstrip("/")
    if not rel or rel.endswith("/"):
        rel = (rel + "index.html") if rel else "index.html"
    for root in (FRONTEND_DIST, FRONTEND_PUBLIC):
        cand = os.path.normpath(os.path.join(root, rel))
        if cand.startswith(root) and os.path.isfile(cand):
            return cand
    index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index) and "." not in os.path.basename(rel):
        return index
    return None


async def _proxy_vite(path: str, request: Request) -> Optional[Response]:
    """Serve the live Vite UI so preview on :8000 is the real Studio, not a stub."""
    import httpx

    url = f"{VITE_ORIGIN}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            r = await client.request(
                request.method,
                url,
                params=list(request.query_params.multi_items()),
                headers={"accept": request.headers.get("accept", "*/*")},
            )
        if r.status_code >= 500:
            return None
        skip = {"content-encoding", "transfer-encoding", "content-length", "connection"}
        headers = {k: v for k, v in r.headers.items() if k.lower() not in skip}
        return Response(
            content=r.content,
            status_code=r.status_code,
            headers=headers,
            media_type=r.headers.get("content-type"),
        )
    except Exception as e:
        logger.debug(f"Vite proxy {path}: {e}")
        return None


async def _serve_frontend(path: str, request: Request) -> Response:
    proxied = await _proxy_vite(path, request)
    if proxied is not None:
        return proxied
    local = _frontend_file(path)
    if local:
        return FileResponse(local)
    html = """<!DOCTYPE html>
<html lang="vi"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Reup Studio AI</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       font-family:Inter,system-ui,sans-serif;background:#0f172a;color:#e2e8f0}
  .card{max-width:420px;padding:32px;border-radius:20px;background:#1e293b;text-align:center}
  h1{font-size:22px;margin:0 0 8px}
  p{color:#94a3b8;line-height:1.5}
</style>
</head><body><div class="card">
<h1>Reup Studio AI</h1>
<p>Giao diện đang khởi động. Tải lại trang sau vài giây.</p>
</div></body></html>"""
    return HTMLResponse(content=html, status_code=200)


# Real-Time WebSocket Pipeline Progress Endpoint
@app.websocket("/ws/jobs")
@app.websocket("/api/v1/ws/jobs")
async def websocket_jobs_endpoint(websocket: WebSocket):
    ws_mgr = getattr(app.state, "ws_manager", ws_manager)
    await ws_mgr.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_json({"event": "pong", "timestamp": msg.get("timestamp")})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_mgr.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket client disconnected with exception: {e}")
        ws_mgr.disconnect(websocket)

# Root + SPA / Vite gateway (live preview often hits :8000, not :8080)
@app.get("/", response_class=HTMLResponse)
async def read_dashboard(request: Request):
    return await _serve_frontend("", request)


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def frontend_gateway(full_path: str, request: Request):
    blocked = ("api/", "api", "docs", "redoc", "openapi.json", "health", "ws/", "ws")
    if full_path.startswith("api") or full_path in blocked or full_path.startswith("ws"):
        raise HTTPException(status_code=404, detail="Not Found")
    return await _serve_frontend(full_path, request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)

