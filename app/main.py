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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
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
frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
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
    return Response(status_code=204)


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

# Root Dashboard Route (SPA Entrypoint)
@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    # 1. Prefer compiled React dist/index.html
    react_index = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist", "index.html"))
    if os.path.exists(react_index):
        with open(react_index, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)

    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)

    # Fallback inline SPA shell if template file is not present yet
    html_shell = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Video Downloader, Watermark Remover & Reup Processing System</title>
        <link rel="stylesheet" href="/static/css/style.css">
    </head>
    <body>
        <div id="app">
            <header><h1>Reup Video Dashboard</h1></header>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_shell, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)

