import asyncio
from typing import Dict, Optional, Set
from fastapi import WebSocket

import app.database as db
from app.logging_config import correlation_id_ctx
from app.models import OperationStatus, OperationTask
from app.pipeline import run_pipeline


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, op_id: str, websocket: WebSocket):
        await websocket.accept()
        if op_id not in self.active_connections:
            self.active_connections[op_id] = set()
        self.active_connections[op_id].add(websocket)

    def disconnect(self, op_id: str, websocket: WebSocket):
        if op_id in self.active_connections:
            self.active_connections[op_id].discard(websocket)
            if not self.active_connections[op_id]:
                del self.active_connections[op_id]

    async def broadcast(self, op_id: str, message: dict):
        if op_id in self.active_connections:
            dead_sockets = set()
            for connection in self.active_connections[op_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead_sockets.add(connection)
            for dead in dead_sockets:
                self.active_connections[op_id].discard(dead)


# Global in-memory state
task_queue: asyncio.Queue = asyncio.Queue()
operations: Dict[str, OperationTask] = {}
running_tasks: Dict[str, asyncio.Task] = {}
ws_manager: ConnectionManager = ConnectionManager()


def load_task_from_db(op_id: str) -> Optional[OperationTask]:
    row = db.get_operation(op_id)
    if not row:
        return None
    task = OperationTask(
        id=row["id"],
        target_language=row["target_language"],
        native_language=row["native_language"],
        cefr_level=row["cefr_level"],
        youtube_url=row["youtube_url"],
        file_name=row["file_name"],
        status=OperationStatus(row["status"]),
        status_detail=row["status_detail"],
        sentences=row.get("sentences", []),
        raw_extracted_words=row.get("raw_extracted_words", []),
        final_items=row.get("final_items", []),
        error=row.get("error"),
        created_at=row.get("created_at", 0.0),
    )
    operations[op_id] = task
    return task


async def worker_loop():
    while True:
        task = await task_queue.get()
        if task.status == OperationStatus.STOPPED:
            task_queue.task_done()
            continue

        cid_token = correlation_id_ctx.set(task.correlation_id or "-")
        current_coro = asyncio.create_task(run_pipeline(task, ws_manager=ws_manager))
        running_tasks[task.id] = current_coro
        try:
            await current_coro
        except asyncio.CancelledError:
            task.status = OperationStatus.STOPPED
            task.status_detail = "Operation stopped by user."
            db.update_operation_status(task.id, task.status, task.status_detail)
            await ws_manager.broadcast(
                task.id,
                {"status": task.status, "detail": task.status_detail, "error": task.error},
            )
        except Exception as e:
            task.status = OperationStatus.FAILED
            task.error = str(e)
            task.status_detail = f"Processing error: {str(e)}"
            db.update_operation_status(task.id, task.status, task.status_detail, error=task.error)
        finally:
            running_tasks.pop(task.id, None)
            correlation_id_ctx.reset(cid_token)
            task_queue.task_done()


async def session_cleanup_loop():
    while True:
        try:
            db.clean_expired_sessions()
        except Exception:
            pass
        # Run cleanup every hour
        await asyncio.sleep(3600)
