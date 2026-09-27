import asyncio
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

import app.database as db
from app.auth import (
    get_current_user_optional,
    require_auth,
)
from app.config import (
    AUTH_PASSWORD,
    AUTH_USERNAME,
    CORRELATION_ID_HEADER,
    SESSION_COOKIE_NAME,
    STATIC_DIR,
    is_dev_mode,
)
from app.extractor import (
    extract_text_from_file,
    extract_youtube_id,
    extract_youtube_transcript,
    split_into_sentences,
)
from app.logging_config import (
    CorrelationIdFormatter,
    correlation_id_ctx,
    logger,
)
from app.models import (
    ExportRequest,
    OperationStatus,
    OperationTask,
    Phase1ChunkOutput,
    WordDetailOutput,
)
from app.pipeline import (
    get_ai_client,
    get_lower_cefr_level,
    process_phase1_chunk,
    process_phase2_word,
    run_pipeline,
)
from app.routers import auth, frontend, tasks, websocket
from app.state import (
    ConnectionManager,
    load_task_from_db,
    operations,
    running_tasks,
    session_cleanup_loop,
    task_queue,
    worker_loop,
    ws_manager,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.clean_expired_sessions()
    worker_task = asyncio.create_task(worker_loop())
    cleanup_task = asyncio.create_task(session_cleanup_loop())
    yield
    worker_task.cancel()
    cleanup_task.cancel()


app = FastAPI(title="VocabCatcher", lifespan=lifespan)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-correlation-id") or request.headers.get("x-request-id")
    if not correlation_id:
        correlation_id = str(uuid4())

    token = correlation_id_ctx.set(correlation_id)
    request.state.correlation_id = correlation_id

    start_time = time.time()
    try:
        response = await call_next(request)
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            f'{request.method} "{request.url.path}" {response.status_code} ({elapsed_ms:.1f}ms)'
        )
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
    except Exception as exc:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.error(
            f'{request.method} "{request.url.path}" 500 ({elapsed_ms:.1f}ms) - Exception: {exc}'
        )
        raise
    finally:
        correlation_id_ctx.reset(token)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Include Modular Routers
app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(websocket.router)

# Static asset mounting if dist exists in production
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

# Include Frontend Fallback Router (must be included last)
app.include_router(frontend.router)


__all__ = [
    "app",
    "operations",
    "running_tasks",
    "task_queue",
    "ws_manager",
    "load_task_from_db",
    "worker_loop",
    "run_pipeline",
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
    "SESSION_COOKIE_NAME",
    "CORRELATION_ID_HEADER",
    "correlation_id_ctx",
    "OperationStatus",
    "OperationTask",
    "extract_text_from_file",
    "extract_youtube_transcript",
    "extract_youtube_id",
    "split_into_sentences",
    "get_current_user_optional",
    "require_auth",
    "is_dev_mode",
    "STATIC_DIR",
]
