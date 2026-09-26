import asyncio
import csv
import io
import json
import logging
import os
import re
import secrets
import tempfile
import time
import urllib.parse
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from uuid import uuid4

import aiofiles
import genanki
import httpx
import pypandoc
import pypdf
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi

import app.database as db

load_dotenv()

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "changeme123")
SESSION_COOKIE_NAME = "session_token"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
APP_ENV = os.getenv("APP_ENV", "production").lower()
VITE_DEV_SERVER_URL = os.getenv("VITE_DEV_SERVER_URL", "http://127.0.0.1:5173").rstrip("/")


def get_current_user_optional(request: Request) -> Optional[str]:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        return None
    session = db.get_session(session_token)
    if not session:
        return None
    return session.get("username")


def require_auth(request: Request) -> str:
    user = get_current_user_optional(request)
    if not user:
        # If it's an API request, return 401 Unauthorized
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated. Please log in.",
            )
        # Otherwise redirect to login page preserving next URL
        redirect_url = f"/login?next={urllib.parse.quote(str(request.url.path))}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": redirect_url},
        )
    return user


class OperationStatus(str, Enum):
    QUEUED = "Queued"
    PARSING = "Parsing"
    PHASE1_EXTRACTING = "Phase 1: Extracting Words"
    PHASE2_TRANSLATING = "Phase 2: Translating in Context"
    READY = "Ready"
    FAILED = "Failed"
    STOPPED = "Stopped"


@dataclass
class OperationTask:
    id: str
    target_language: str
    cefr_level: str
    native_language: str = "English"
    file_bytes: Optional[bytes] = None
    file_name: Optional[str] = None
    youtube_url: Optional[str] = None
    status: OperationStatus = OperationStatus.QUEUED
    status_detail: str = "Waiting in queue"
    sentences: List[str] = field(default_factory=list)
    raw_extracted_words: List[str] = field(default_factory=list)
    final_items: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    correlation_id: str = ""


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


task_queue: asyncio.Queue = asyncio.Queue()
operations: Dict[str, OperationTask] = {}
running_tasks: Dict[str, asyncio.Task] = {}
ws_manager = ConnectionManager()
ai_client: Optional[genai.Client] = None

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)


def get_ai_client() -> genai.Client:
    global ai_client
    if ai_client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        ai_client = genai.Client(api_key=key)
    return ai_client


def get_lower_cefr_level(current_level: str) -> str:
    hierarchy = ["A1", "A2", "B1", "B2", "C1", "C2"]
    lvl = current_level.strip().upper()
    if lvl not in hierarchy:
        return "A1"
    idx = hierarchy.index(lvl)
    return hierarchy[max(0, idx - 1)]


def extract_youtube_id(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    if "youtube.com" in parsed.netloc:
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            return query["v"][0]
    elif "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/")
    return None


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "txt":
        return file_bytes.decode("utf-8", errors="ignore")

    if ext == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
        return "\n".join(pages_text)

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        output = pypandoc.convert_file(tmp_path, "plain", format=ext)
        return output
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def extract_youtube_transcript(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL provided.")

    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            snippets = YouTubeTranscriptApi.get_transcript(video_id)
        else:
            ytt = YouTubeTranscriptApi()
            snippets = ytt.fetch(video_id)
    except Exception as exc:
        raise ValueError(f"Failed to fetch YouTube transcript: {str(exc)}") from exc

    texts = []
    for entry in snippets:
        if hasattr(entry, "text"):
            texts.append(entry.text)
        elif isinstance(entry, dict) and "text" in entry:
            texts.append(entry["text"])

    return " ".join(texts)


def split_into_sentences(text: str) -> List[str]:
    clean_text = re.sub(r"\s+", " ", text).strip()
    if not clean_text:
        return []
    pattern = r"(?<=[.!?])\s+"
    raw_sentences = re.split(pattern, clean_text)
    return [s.strip() for s in raw_sentences if s.strip()]


class Phase1ChunkOutput(BaseModel):
    words: List[str] = Field(
        default_factory=list,
        description="List of words or expressions likely unknown at the target CEFR level.",
    )


class WordDetailOutput(BaseModel):
    infinitive: Optional[str] = None
    phrasal_verb: Optional[str] = None
    native_language_definition: str
    from_source: List[str]
    example_sentence: str


async def process_phase1_chunk(
    chunk_index: int,
    sentences_chunk: List[str],
    target_language: str,
    cefr_level: str,
) -> List[str]:
    client = get_ai_client()
    chunk_text = " ".join(sentences_chunk)
    prompt = (
        f"You are a linguistic expert analyzing {target_language} text for a student at CEFR {cefr_level} level.\n"
        f"Identify words, phrasal verbs, or idioms from the text below that are likely UNKNOWN to someone at CEFR {cefr_level} level.\n"
        f"Do NOT define them. Return ONLY the raw list of words/expressions.\n\n"
        f"Text:\n{chunk_text}"
    )

    loop = asyncio.get_running_loop()

    def _call():
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Phase1ChunkOutput,
                temperature=0.1,
            ),
        )
        return response.text

    ctx = copy_context()
    response_text = await loop.run_in_executor(None, ctx.run, _call)
    data = json.loads(response_text)
    return data.get("words", [])


async def process_phase2_word(
    word_entry: Dict[str, Any],
    all_sentences: List[str],
    target_language: str,
    cefr_level: str,
    native_language: str,
) -> Optional[Dict[str, Any]]:
    client = get_ai_client()
    word = word_entry["word"]
    first_sentence_idx = word_entry["first_occurrence_idx"]
    source_variants = list(word_entry["variants"])

    start_idx = max(0, first_sentence_idx - 5)
    end_idx = min(len(all_sentences), first_sentence_idx + 2)

    preceding = " ".join(all_sentences[start_idx:first_sentence_idx])
    target_sentence = all_sentences[first_sentence_idx]
    following = " ".join(all_sentences[first_sentence_idx + 1:end_idx])

    lower_level = get_lower_cefr_level(cefr_level)

    prompt = f"""You are an expert lexicographer and teacher of {target_language}.
Analyze the word/expression: "{word}"
Found in text variant forms: {json.dumps(source_variants)}

Context from source:
- Preceding 5 sentences: {preceding}
- Exact sentence: {target_sentence}
- Following 1 sentence: {following}

Instructions:
1. Determine context-accurate definition in {native_language}.
2. If it is a phrasal verb, use the key "phrasal_verb". Otherwise, use "infinitive" (or dictionary lemma).
3. The "from_source" array MUST match: {json.dumps(source_variants)}.
4. Generate an example sentence illustrating the exact usage, but STRICTLY RESTRICT the vocabulary of this example sentence to CEFR {lower_level} level.
"""

    loop = asyncio.get_running_loop()

    def _call():
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WordDetailOutput,
                temperature=0.2,
            ),
        )
        return response.text

    try:
        ctx = copy_context()
        response_text = await loop.run_in_executor(None, ctx.run, _call)
        result = json.loads(response_text)
        if "from_source" not in result or not result["from_source"]:
            result["from_source"] = source_variants
        return result
    except Exception as err:
        return {
            "infinitive": word,
            "native_language_definition": f"Contextual translation error: {str(err)}",
            "from_source": source_variants,
            "example_sentence": target_sentence,
        }


async def run_pipeline(task: OperationTask):
    try:
        task.status = OperationStatus.PARSING
        task.status_detail = "Extracting plain text from source..."
        db.update_operation_status(task.id, task.status, task.status_detail)
        await ws_manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

        extracted_text = ""
        if task.youtube_url:
            extracted_text = extract_youtube_transcript(task.youtube_url)
        elif task.file_bytes and task.file_name:
            extracted_text = extract_text_from_file(task.file_bytes, task.file_name)
        else:
            raise ValueError("No input source provided (file or YouTube link missing).")

        sentences = split_into_sentences(extracted_text)
        if not sentences:
            raise ValueError("No readable sentences could be extracted from input.")
        task.sentences = sentences
        db.update_operation_status(task.id, task.status, task.status_detail, sentences=task.sentences)

        chunks: List[List[str]] = []
        for i in range(0, len(sentences), 6):
            chunks.append(sentences[i : i + 6])

        task.status = OperationStatus.PHASE1_EXTRACTING
        task.status_detail = f"Identifying unfamiliar words across {len(chunks)} text chunks..."
        db.update_operation_status(task.id, task.status, task.status_detail)
        await ws_manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

        phase1_tasks = [
            process_phase1_chunk(idx, chunk, task.target_language, task.cefr_level)
            for idx, chunk in enumerate(chunks)
        ]
        phase1_results = await asyncio.gather(*phase1_tasks, return_exceptions=True)

        raw_words: Set[str] = set()
        for res in phase1_results:
            if isinstance(res, list):
                for w in res:
                    cleaned = w.strip()
                    if cleaned:
                        raw_words.add(cleaned)

        task.raw_extracted_words = sorted(list(raw_words))
        db.update_operation_status(
            task.id, task.status, task.status_detail, raw_extracted_words=task.raw_extracted_words
        )

        word_occurrence_map: Dict[str, Dict[str, Any]] = {}
        for w in task.raw_extracted_words:
            norm_key = w.lower()
            matched_idx = 0
            found_variants = set()

            for s_idx, sentence in enumerate(sentences):
                if re.search(rf"\b{re.escape(w)}\b", sentence, re.IGNORECASE):
                    found_matches = re.findall(rf"\b{re.escape(w)}\b", sentence, re.IGNORECASE)
                    found_variants.update(found_matches)
                    if matched_idx == 0:
                        matched_idx = s_idx

            if not found_variants:
                found_variants.add(w)

            word_occurrence_map[norm_key] = {
                "word": w,
                "first_occurrence_idx": matched_idx,
                "variants": list(found_variants),
            }

        task.status = OperationStatus.PHASE2_TRANSLATING
        task.status_detail = f"Generating contextual definitions and leveled examples for {len(word_occurrence_map)} words..."
        db.update_operation_status(task.id, task.status, task.status_detail)
        await ws_manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

        phase2_tasks = [
            process_phase2_word(
                entry,
                sentences,
                task.target_language,
                task.cefr_level,
                task.native_language,
            )
            for entry in word_occurrence_map.values()
        ]
        phase2_results = await asyncio.gather(*phase2_tasks, return_exceptions=True)

        valid_items: List[Dict[str, Any]] = []
        for item in phase2_results:
            if isinstance(item, dict):
                valid_items.append(item)

        task.final_items = valid_items
        task.status = OperationStatus.READY
        task.status_detail = "Extraction complete! Select vocabulary to export."
        db.update_operation_status(
            task.id,
            task.status,
            task.status_detail,
            final_items=task.final_items,
        )
        await ws_manager.broadcast(
            task.id,
            {
                "status": task.status,
                "detail": task.status_detail,
                "items": task.final_items,
            },
        )

    except Exception as exc:
        task.status = OperationStatus.FAILED
        task.error = str(exc)
        task.status_detail = f"Processing error: {str(exc)}"
        db.update_operation_status(
            task.id,
            task.status,
            task.status_detail,
            error=task.error,
        )
        await ws_manager.broadcast(
            task.id,
            {"status": task.status, "detail": task.status_detail, "error": task.error},
        )


async def worker_loop():
    while True:
        task = await task_queue.get()
        if task.status == OperationStatus.STOPPED:
            task_queue.task_done()
            continue

        cid_token = correlation_id_ctx.set(task.correlation_id or "-")
        current_coro = asyncio.create_task(run_pipeline(task))
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


async def session_cleanup_loop():
    while True:
        try:
            db.clean_expired_sessions()
        except Exception:
            pass
        # Run cleanup every hour
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.clean_expired_sessions()
    worker_task = asyncio.create_task(worker_loop())
    cleanup_task = asyncio.create_task(session_cleanup_loop())
    yield
    worker_task.cancel()
    cleanup_task.cancel()


correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFormatter(logging.Formatter):
    """Custom formatter ensuring %(correlation_id)s is always available on all log records."""
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = correlation_id_ctx.get("-")
        return super().format(record)


# Configure logging with custom formatter
_log_format = "%(asctime)s [%(levelname)s] [corr_id=%(correlation_id)s] %(name)s: %(message)s"
_formatter = CorrelationIdFormatter(_log_format)

# Set up root logger handler
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
if not _root_logger.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    _root_logger.addHandler(_console_handler)
else:
    for h in _root_logger.handlers:
        h.setFormatter(_formatter)

# Configure uvicorn loggers
for uvicorn_log_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    u_logger = logging.getLogger(uvicorn_log_name)
    for h in u_logger.handlers:
        h.setFormatter(_formatter)

logger = logging.getLogger("vocabcatcher")

app = FastAPI(title="VocabCatcher", lifespan=lifespan)

CORRELATION_ID_HEADER = "X-Correlation-ID"


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    # Check incoming request for X-Correlation-ID (or X-Request-ID as fallback)
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

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


def is_dev_mode() -> bool:
    if APP_ENV == "development" or os.getenv("DEV_MODE", "").lower() in ("true", "1"):
        return True
    if not STATIC_DIR.exists():
        return True
    return False


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/api/tasks")
async def create_task(
    request: Request,
    target_language: str = Form(...),
    cefr_level: str = Form(...),
    native_language: str = Form("English"),
    youtube_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: str = Depends(require_auth),
):
    op_id = str(uuid4())
    file_bytes = None
    file_name = None

    if file and file.filename:
        file_bytes = await file.read()
        file_name = file.filename

    cid = getattr(request.state, "correlation_id", None) or correlation_id_ctx.get("-")

    task = OperationTask(
        id=op_id,
        target_language=target_language,
        cefr_level=cefr_level,
        native_language=native_language,
        file_bytes=file_bytes,
        file_name=file_name,
        youtube_url=youtube_url if youtube_url and youtube_url.strip() else None,
        correlation_id=cid,
    )

    operations[op_id] = task
    db.save_operation(
        op_id=task.id,
        target_language=task.target_language,
        native_language=task.native_language,
        cefr_level=task.cefr_level,
        status=task.status,
        status_detail=task.status_detail,
        created_at=task.created_at,
        youtube_url=task.youtube_url,
        file_name=task.file_name,
        source=task.youtube_url if task.youtube_url else (task.file_name or "Uploaded Text"),
    )

    await task_queue.put(task)

    return RedirectResponse(url=f"/operation/{op_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/operations")
async def list_operations(user: str = Depends(require_auth)):
    # Fetch from SQLite so operations persist across restarts
    ops = db.list_operations_db()
    return ops


@app.post("/api/tasks/{op_id}/stop")
async def stop_operation(op_id: str, user: str = Depends(require_auth)):
    task = operations.get(op_id) or load_task_from_db(op_id)
    if not task:
        raise HTTPException(status_code=404, detail="Operation not found")

    if task.status in (OperationStatus.READY, OperationStatus.FAILED, OperationStatus.STOPPED):
        return {"status": task.status, "message": "Operation already finished or stopped"}

    task.status = OperationStatus.STOPPED
    task.status_detail = "Operation stopped by user."
    db.update_operation_status(op_id, task.status, task.status_detail)

    if op_id in running_tasks:
        running_tasks[op_id].cancel()

    await ws_manager.broadcast(
        op_id,
        {"status": task.status, "detail": task.status_detail, "error": task.error},
    )

    return {"status": task.status, "message": "Operation stopped successfully"}


@app.get("/api/tasks/{op_id}")
async def get_task_status(op_id: str, user: str = Depends(require_auth)):
    task = operations.get(op_id) or load_task_from_db(op_id)
    if not task:
        raise HTTPException(status_code=404, detail="Operation not found")
    return {
        "id": task.id,
        "status": task.status,
        "detail": task.status_detail,
        "error": task.error,
        "target_language": task.target_language,
        "native_language": task.native_language,
        "cefr_level": task.cefr_level,
        "source": task.youtube_url if task.youtube_url else (task.file_name or "Uploaded Text"),
        "items": task.final_items,
    }


class ExportRequest(BaseModel):
    format: str  # "json" or "anki"
    selected_indices: List[int]


@app.post("/api/tasks/{op_id}/export")
async def export_vocab(
    op_id: str,
    req: ExportRequest,
    user: str = Depends(require_auth),
):
    task = operations.get(op_id) or load_task_from_db(op_id)
    if not task:
        raise HTTPException(status_code=404, detail="Operation not found")

    selected_words = []
    for idx in req.selected_indices:
        if 0 <= idx < len(task.final_items):
            selected_words.append(task.final_items[idx])

    if req.format.lower() == "json":
        json_str = json.dumps(selected_words, indent=2, ensure_ascii=False)
        return Response(
            content=json_str,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=vocabcatcher_{op_id}.json"},
        )

    elif req.format.lower() == "anki":
        model_id = 1607392319
        vocab_model = genanki.Model(
            model_id,
            "VocabCatcher CEFR Deck Model",
            fields=[
                {"name": "Expression"},
                {"name": "Definition"},
                {"name": "ExampleSentence"},
                {"name": "SourceVariants"},
            ],
            templates=[
                {
                    "name": "Card 1",
                    "qfmt": '<div style="font-family: Arial; font-size: 24px; text-align: center; color: #333;">{{Expression}}</div><div style="font-size: 14px; color: #777; text-align: center; margin-top: 6px;">Variants: {{SourceVariants}}</div>',
                    "afmt": '{{FrontSide}}<hr id="answer"><div style="font-size: 20px; color: #0284c7; text-align: center;">{{Definition}}</div><div style="font-style: italic; color: #475569; margin-top: 12px; text-align: center;">"{{ExampleSentence}}"</div>',
                },
            ],
        )

        deck_id = 2059401251
        deck = genanki.Deck(deck_id, f"VocabCatcher - {task.target_language} ({task.cefr_level})")

        for item in selected_words:
            term = item.get("infinitive") or item.get("phrasal_verb") or "Unknown"
            defn = item.get("native_language_definition", "")
            example = item.get("example_sentence", "")
            variants = ", ".join(item.get("from_source", []))

            note = genanki.Note(
                model=vocab_model,
                fields=[term, defn, example, variants],
            )
            deck.add_note(note)

        pkg = genanki.Package(deck)
        buf = io.BytesIO()
        pkg.write_to_file(buf)
        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename=vocabcatcher_{op_id}.apkg"},
        )

    elif req.format.lower() in ("csv", "brainscape"):
        # Brainscape multi-field CSV with standard column headers:
        # Q. Body, Q. Clarifier, A. Body, A. Footnote
        # Saved in UTF-8 with BOM or UTF-8 for Excel/Brainscape compatibility
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        # Brainscape recognized header row
        writer.writerow(["Q. Body", "Q. Clarifier", "A. Body", "A. Footnote"])

        for item in selected_words:
            term = item.get("infinitive") or item.get("phrasal_verb") or "Unknown"
            defn = item.get("native_language_definition", "")
            example = item.get("example_sentence", "")
            variants = ", ".join(item.get("from_source", []))
            
            # Question Body: The term
            # Question Clarifier: Text variants found in source
            # Answer Body: Definition
            # Answer Footnote: Leveled example sentence
            writer.writerow([term, f"Forms: {variants}" if variants else "", defn, f'"{example}"' if example else ""])

        csv_content = output.getvalue()
        return Response(
            content=csv_content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=vocabcatcher_brainscape_{op_id}.csv"},
        )

    else:
        raise HTTPException(status_code=400, detail="Invalid export format. Must be 'json', 'anki', or 'csv'")


# WebSocket for Live Progress Updates
@app.websocket("/ws/operation/{op_id}")
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


# Development Proxy to Vite dev server
async def proxy_vite_request(request: Request, path: str = ""):
    target_path = "/" + path.lstrip("/")
    target_url = f"{VITE_DEV_SERVER_URL}{target_path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    # Determine candidate URLs (e.g. 127.0.0.1 vs localhost)
    candidate_urls = [target_url]
    if "127.0.0.1" in target_url:
        candidate_urls.append(target_url.replace("127.0.0.1", "localhost"))
    elif "localhost" in target_url:
        candidate_urls.append(target_url.replace("localhost", "127.0.0.1"))

    content = await request.body()
    last_exc = None

    for url in candidate_urls:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                proxy_resp = await client.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    content=content,
                )

                excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
                resp_headers = {
                    k: v for k, v in proxy_resp.headers.items() if k.lower() not in excluded_headers
                }

                return Response(
                    content=proxy_resp.content,
                    status_code=proxy_resp.status_code,
                    headers=resp_headers,
                    media_type=proxy_resp.headers.get("content-type"),
                )
        except httpx.RequestError as exc:
            last_exc = exc
            continue

    return HTMLResponse(
        f"<h3>Vite dev server proxy error: {str(last_exc)}</h3><p>Make sure Vite dev server is running on {VITE_DEV_SERVER_URL} (e.g. <code>npm run dev</code> or <code>task dev</code>).</p>",
        status_code=502,
    )


# Authentication Routes
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="cupcake">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login - VocabCatcher</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@4.12.23/dist/full.min.css">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-base-200 flex items-center justify-center p-4">
  <div class="card w-full max-w-sm shadow-2xl bg-base-100 border border-base-300">
    <div class="card-body">
      <div class="text-center mb-4">
        <h1 class="text-3xl font-bold text-primary tracking-wide">VocabCatcher</h1>
        <p class="text-sm opacity-70 mt-1">Sign in to access your vocabulary manager</p>
      </div>
      {error_alert}
      <form action="/login" method="POST" class="space-y-4">
        <input type="hidden" name="next" value="{next_url}" />
        <div class="form-control">
          <label class="label"><span class="label-text font-medium">Username</span></label>
          <input type="text" name="username" required placeholder="admin" autofocus class="input input-bordered w-full" />
        </div>
        <div class="form-control">
          <label class="label"><span class="label-text font-medium">Password</span></label>
          <input type="password" name="password" required placeholder="••••••••" class="input input-bordered w-full" />
        </div>
        <div class="form-control">
          <label class="label cursor-pointer justify-start gap-3 py-1">
            <input type="checkbox" name="remember_me" value="true" class="checkbox checkbox-primary checkbox-sm" />
            <span class="label-text text-sm">Remember me for 30 days</span>
          </label>
        </div>
        <div class="form-control mt-4">
          <button type="submit" class="btn btn-primary w-full text-base">Sign In</button>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = "/"):
    # If already logged in, redirect to next or /
    current_user = get_current_user_optional(request)
    if current_user:
        target = next if next and next.startswith("/") else "/"
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

    safe_next = next if next and next.startswith("/") else "/"
    return HTMLResponse(
        LOGIN_HTML.format(error_alert="", next_url=urllib.parse.quote(safe_next))
    )


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    remember_me: Optional[str] = Form(None),
    next: Optional[str] = Form("/"),
):
    # Support JSON requests as well for API convenience
    remember = bool(remember_me and remember_me.lower() in ("true", "1", "on", "yes"))
    if not username and not password:
        try:
            body = await request.json()
            username = body.get("username", "")
            password = body.get("password", "")
            if "remember_me" in body:
                val = body.get("remember_me")
                remember = bool(val is True or str(val).lower() in ("true", "1", "on", "yes"))
            if "next" in body:
                next = body.get("next")
        except Exception:
            pass

    correct_user = secrets.compare_digest(username, AUTH_USERNAME)
    correct_pass = secrets.compare_digest(password, AUTH_PASSWORD)

    if correct_user and correct_pass:
        ttl_days = 30 if remember else 7
        session_token = db.create_session(username=username, ttl_days=ttl_days)
        target = next if next and next.startswith("/") else "/"
        
        # If client requested JSON
        accept_header = request.headers.get("accept", "")
        if "application/json" in accept_header and not "text/html" in accept_header:
            response = JSONResponse({
                "status": "ok",
                "user": username,
                "token": session_token,
                "ttl_days": ttl_days,
            })
        else:
            response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=ttl_days * 86400,
            path="/",
        )
        return response

    # Failed login
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header and not "text/html" in accept_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    safe_next = next if next and next.startswith("/") else "/"
    error_html = """
    <div class="alert alert-error text-sm p-3 mb-2 shadow-sm rounded-lg flex items-center">
      <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5 mr-2" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
      <span>Invalid username or password.</span>
    </div>
    """
    return HTMLResponse(
        LOGIN_HTML.format(error_alert=error_html, next_url=urllib.parse.quote(safe_next)),
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        db.delete_session(session_token)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


# Frontend Serving Routes
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "index.html")

    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/operations", response_class=HTMLResponse)
async def serve_operations_list(request: Request, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "operations.html")

    ops_file = STATIC_DIR / "operations.html"
    if not ops_file.exists():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=ops_file.read_text(encoding="utf-8"))


@app.get("/operation/{op_id}", response_class=HTMLResponse)
async def serve_operation(request: Request, op_id: str, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "operation.html")

    op_file = STATIC_DIR / "operation.html"
    if not op_file.exists():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=op_file.read_text(encoding="utf-8"))


# Static asset mounting if dist exists for production
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")


# Fallback dev proxy for Vite HMR, scripts, node_modules, styles, etc.
@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def dev_proxy_fallback(request: Request, full_path: str):
    if is_dev_mode():
        return await proxy_vite_request(request, full_path)
    raise HTTPException(status_code=404, detail="Not Found")
