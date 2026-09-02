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
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse
from typing import Optional
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.database import init_db
from app.core.instance_lock import BackendInstanceLock
from app.core.ws_manager import ws_manager
from app.services.queue_manager import BatchQueueManager
from app.scraper.manager import ScraperManager
from app.services.sample_media import seed_sample_videos
from app.services.facebook_distribution import FacebookDistributionWorker
from app.services.tiktok_distribution import TikTokDistributionWorker

logger = logging.getLogger("app.main")


def _seed_sample_media() -> None:
    """Seeds playable Douyin-style sample MP4s (logo + Chinese hardsub + speech)."""
    try:
        seed_sample_videos(settings.RAW_INPUT_DIR, force=False)
    except Exception as e:
        logger.warning(f"Sample media seeding failed: {e}")


def _defer_sample_seed() -> bool:
    """Packaged Electron sets REUP_ROOT; skip blocking seed so /health binds immediately."""
    flag = os.environ.get("REUP_SKIP_SAMPLE_SEED", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    return bool(os.environ.get("REUP_ROOT"))


# Directories on import so tests and first requests have a writable tree.
# Sample MP4 seeding is deferred in packaged Electron (REUP_SKIP_SAMPLE_SEED=1)
# so uvicorn can bind /health before edge-tts/FFmpeg run.
settings.ensure_directories()
_stt_threads = str(max(1, int(getattr(settings, "STT_CPU_THREADS", 4) or 4)))
for _key in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_key, _stt_threads)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
if not _defer_sample_seed():
    _seed_sample_media()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager handling application startup and shutdown events."""
    instance_lock = BackendInstanceLock(settings.DB_PATH)
    if not instance_lock.acquire():
        raise RuntimeError(
            f"Another backend instance already owns the job database: {settings.DB_PATH}"
        )

    logger.info("Initializing application storage directories...")
    queue_mgr = None
    facebook_worker = None
    tiktok_worker = None
    try:
        settings.ensure_directories()
        skip_blocking_seed = _defer_sample_seed()
        if not skip_blocking_seed:
            _seed_sample_media()

        logger.info(f"Initializing SQLite database schema at: {settings.DB_PATH}")
        init_db(settings.DB_PATH)

        logger.info(
            "Runtime limits: jobs=%s gpu=%s onnx_threads=%s tts=%sx%s stt_threads=%s stt_workers=%s",
            settings.MAX_CONCURRENT_JOBS,
            settings.GPU_CONCURRENCY,
            settings.ONNX_INTRA_OP_THREADS,
            settings.TTS_CONCURRENCY,
            settings.TTS_ONNX_THREADS,
            settings.STT_CPU_THREADS,
            settings.STT_WORKERS,
        )
        logger.info("Starting Batch Queue Manager workers...")
        queue_mgr = BatchQueueManager(
            db_path=settings.DB_PATH,
            max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS
        )
        scraper_mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)

        # Register WebSocket broadcast callback
        queue_mgr.register_callback(ws_manager.on_queue_update)
        await queue_mgr.start()

        facebook_worker = FacebookDistributionWorker(settings.DB_PATH)
        await facebook_worker.start()
        tiktok_worker = TikTokDistributionWorker(settings.DB_PATH)
        await tiktok_worker.start()

        # Store singletons on app.state
        app.state.queue_manager = queue_mgr
        app.state.ws_manager = ws_manager
        app.state.scraper_manager = scraper_mgr
        app.state.facebook_distribution_worker = facebook_worker
        app.state.tiktok_distribution_worker = tiktok_worker

        yield
    finally:
        try:
            if tiktok_worker is not None:
                logger.info("Stopping TikTok distribution worker...")
                await tiktok_worker.stop()
            if facebook_worker is not None:
                logger.info("Stopping Facebook distribution worker...")
                await facebook_worker.stop()
            if queue_mgr is not None:
                logger.info("Stopping Batch Queue Manager workers...")
                await queue_mgr.stop()
        finally:
            instance_lock.release()
            logger.info("Application shutdown complete.")


app = FastAPI(
    title="Video Downloader, Watermark Remover & Reup Processing System",
    description="Backend API and WebSocket System for Video Reup Pipeline",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS Middleware. Never use allow_origins=["*"] with credentials —
# Chromium rejects that, and file:// sends Origin: null.
_cors_origins = settings.get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins != ["*"] else [
        "http://127.0.0.1:6000",
        "http://127.0.0.1:6001",
        "http://localhost:6000",
        "http://localhost:6001",
        "null",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
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
FRONTEND_DIST = os.path.abspath(
    os.environ.get("FRONTEND_DIST") or os.path.join(FRONTEND_ROOT, "dist")
)
FRONTEND_PUBLIC = os.path.join(FRONTEND_ROOT, "public")
VITE_ORIGIN = os.environ.get("VITE_ORIGIN", "http://127.0.0.1:6001")

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


def _frontend_media_type(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".json": "application/json",
        ".map": "application/json",
    }.get(ext)


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
    """Serve the live Vite UI so preview on :6000 is the real Studio, not a stub."""
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
    if not os.environ.get("FRONTEND_DIST"):
        proxied = await _proxy_vite(path, request)
        if proxied is not None:
            return proxied
    local = _frontend_file(path)
    if local:
        media = _frontend_media_type(local)
        return FileResponse(local, media_type=media) if media else FileResponse(local)
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

# Root + SPA / Vite gateway (live preview often hits :6000)
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
