from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models import OperationStatus
from app.state import load_task_from_db, operations, ws_manager

router = APIRouter()


@router.websocket("/ws/operation/{op_id}")
async def websocket_operation(websocket: WebSocket, op_id: str):
    await ws_manager.connect(op_id, websocket)
    task = operations.get(op_id) or load_task_from_db(op_id)
    if task:
        await websocket.send_json(
            {
                "status": task.status,
                "detail": task.status_detail,
                "items": task.final_items if task.status == OperationStatus.READY else [],
                "error": task.error,
            }
        )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(op_id, websocket)
