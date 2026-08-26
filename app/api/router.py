"""
Main REST API Router Aggregator for API v1.
============================================
Target Path: app/api/router.py
"""

from fastapi import APIRouter

from app.api.extract import router as extract_router
from app.api.stream import router as stream_router
from app.api.process import router as process_router
from app.api.jobs import router as jobs_router
from app.api.outputs import router as outputs_router
from app.api.channels import router as channels_router
from app.api.studio import router as studio_router
from app.api.bgm import router as bgm_router
from app.api.facebook import router as facebook_router
from app.api.tiktok import router as tiktok_router
from app.api.content import router as content_router

api_router = APIRouter()

api_router.include_router(extract_router, tags=["Extract"])
api_router.include_router(stream_router, tags=["Stream"])
api_router.include_router(process_router, tags=["Process"])
api_router.include_router(jobs_router, tags=["Jobs"])
api_router.include_router(outputs_router, tags=["Outputs"])
api_router.include_router(channels_router, tags=["Channels"])
api_router.include_router(studio_router, tags=["Studio"])
api_router.include_router(bgm_router, tags=["BGM"])
api_router.include_router(facebook_router, tags=["Facebook"])
api_router.include_router(tiktok_router, tags=["TikTok"])
api_router.include_router(content_router, tags=["Content"])
