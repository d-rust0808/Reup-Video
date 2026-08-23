"""
WebSocket Connection Manager and Live Progress Broadcaster Module.
==================================================================
Target Path: app/core/ws_manager.py
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts real-time task progress events."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket) -> None:
        """Accepts incoming WebSocket connection and registers client."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Removes WebSocket connection from active subscriber list."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Remaining connections: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcasting JSON payload to all active subscribers, removing dead connections."""
        if not self.active_connections:
            return

        disconnected: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Error broadcasting to WebSocket client: {e}")
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    # -----------------------------------------------------------------------
    # Helper Broadcast Methods for Specialized Job Events
    # -----------------------------------------------------------------------

    async def broadcast_stage_change(
        self,
        job_id: str,
        status: str,
        stage: str,
        progress: float,
        message: Optional[str] = None
    ) -> None:
        """Broadcasts job stage change / progress event."""
        payload = {
            "event": "job_progress",
            "job_id": job_id,
            "status": status,
            "stage": stage,
            "progress": progress,
            "message": message or f"Job stage changed to {stage}"
        }
        await self.broadcast(payload)

    async def broadcast_progress_update(
        self,
        job_id: str,
        progress: float,
        stage: str = "PROCESSING"
    ) -> None:
        """Broadcasts percentage progress update."""
        payload = {
            "event": "progress_update",
            "job_id": job_id,
            "progress": progress,
            "stage": stage
        }
        await self.broadcast(payload)

    async def broadcast_job_completed(
        self,
        job_id: str,
        output_path: str,
        progress: float = 100.0
    ) -> None:
        """Broadcasts job completed event."""
        payload = {
            "event": "job_completed",
            "job_id": job_id,
            "status": "COMPLETED",
            "stage": "COMPLETED",
            "progress": progress,
            "output_path": output_path
        }
        await self.broadcast(payload)

    async def broadcast_job_failed(
        self,
        job_id: str,
        error_message: str
    ) -> None:
        """Broadcasts job failed event."""
        payload = {
            "event": "job_failed",
            "job_id": job_id,
            "status": "FAILED",
            "stage": "FAILED",
            "progress": 0.0,
            "error": error_message
        }
        await self.broadcast(payload)

    # -----------------------------------------------------------------------
    # Queue Callback Integration Adapter
    # -----------------------------------------------------------------------

    def on_queue_update(self, job_dict: Dict[str, Any]) -> None:
        """
        Callback handler designed to be registered directly with BatchQueueManager.
        Converts SQLite job record dictionary into appropriate WebSocket JSON broadcast.
        Safely handles callers invoked without an active async event loop or off-thread.
        """
        status = str(job_dict.get("status", "PENDING")).upper()
        job_id = job_dict.get("job_id", "")
        progress = float(job_dict.get("progress_percent") or (job_dict.get("progress", 0.0) * 100.0))
        output_path = job_dict.get("output_file_path") or job_dict.get("output_path", "")
        message_str = job_dict.get("message") or f"Pipeline stage: {status}"
        logs_list = job_dict.get("logs") or []

        if status == "COMPLETED":
            payload = {
                "event": "job_completed",
                "job_id": job_id,
                "status": "COMPLETED",
                "stage": "COMPLETED",
                "progress": 100.0,
                "output_path": output_path,
                "message": message_str,
                "logs": logs_list
            }
        elif status == "FAILED":
            payload = {
                "event": "job_failed",
                "job_id": job_id,
                "status": "FAILED",
                "stage": "FAILED",
                "error": job_dict.get("error") or job_dict.get("error_message") or "Unknown pipeline processing error",
                "error_message": job_dict.get("error_message") or job_dict.get("error"),
                "message": message_str,
                "logs": logs_list
            }
        else:
            payload = {
                "event": "job_progress",
                "job_id": job_id,
                "status": status,
                "stage": status,
                "progress": progress,
                "message": message_str,
                "logs": logs_list
            }

        try:
            running_loop = None
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if running_loop and running_loop.is_running():
                running_loop.create_task(self.broadcast(payload))
            elif self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)
            else:
                try:
                    el = asyncio.get_event_loop()
                    if el and el.is_running():
                        asyncio.run_coroutine_threadsafe(self.broadcast(payload), el)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to schedule WS broadcast in on_queue_update: {e}")


# Global Singleton WS Manager Instance
ws_manager = ConnectionManager()
