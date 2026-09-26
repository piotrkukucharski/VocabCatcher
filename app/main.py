import asyncio
import io
import json
import os
import re
import secrets
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from uuid import uuid4

import aiofiles
import genanki
import pypandoc
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
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

load_dotenv()

BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "changeme123")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

security = HTTPBasic()


def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_username = secrets.compare_digest(credentials.username, BASIC_AUTH_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, BASIC_AUTH_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class OperationStatus(str, Enum):
    QUEUED = "Queued"
    PARSING = "Parsing"
    PHASE1_EXTRACTING = "Phase 1: Extracting Words"
    PHASE2_TRANSLATING = "Phase 2: Translating in Context"
    READY = "Ready"
    FAILED = "Failed"


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

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # Convert to plain text using pypandoc (pandoc must be installed)
        output = pypandoc.convert_file(tmp_path, "plain", format=ext)
        return output
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def extract_youtube_transcript(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL provided.")
    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
    return " ".join([entry["text"] for entry in transcript_list])


def split_into_sentences(text: str) -> List[str]:
    clean_text = re.sub(r"\s+", " ", text).strip()
    if not clean_text:
        return []
    # Sentence splitting regex matching punctuation followed by space or boundary
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

    response_text = await loop.run_in_executor(None, _call)
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
        response_text = await loop.run_in_executor(None, _call)
        result = json.loads(response_text)
        # Ensure schema conformance
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
        # Step 1: Parsing
        task.status = OperationStatus.PARSING
        task.status_detail = "Extracting plain text from source..."
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

        # Step 2: Chunking (chunks of exactly 6 sentences)
        chunks: List[List[str]] = []
        for i in range(0, len(sentences), 6):
            chunks.append(sentences[i : i + 6])

        # Step 3: Phase 1 Parallel Processing
        task.status = OperationStatus.PHASE1_EXTRACTING
        task.status_detail = f"Identifying unfamiliar words across {len(chunks)} text chunks..."
        await ws_manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

        phase1_tasks = [
            process_phase1_chunk(idx, chunk, task.target_language, task.cefr_level)
            for idx, chunk in enumerate(chunks)
        ]
        phase1_results = await asyncio.gather(*phase1_tasks, return_exceptions=True)

        # Aggregate raw words and locate sentence occurrences
        raw_words: Set[str] = set()
        for res in phase1_results:
            if isinstance(res, list):
                for w in res:
                    cleaned = w.strip()
                    if cleaned:
                        raw_words.add(cleaned)

        task.raw_extracted_words = sorted(list(raw_words))

        # Map each word to its first sentence appearance and track variant forms
        word_occurrence_map: Dict[str, Dict[str, Any]] = {}
        for w in task.raw_extracted_words:
            norm_key = w.lower()
            matched_idx = 0
            found_variants = set()

            for s_idx, sentence in enumerate(sentences):
                # Search word boundaries or substring
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

        # Step 4: Phase 2 Parallel Word Definition & Context Injection
        task.status = OperationStatus.PHASE2_TRANSLATING
        task.status_detail = f"Generating contextual definitions and leveled examples for {len(word_occurrence_map)} words..."
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
        await ws_manager.broadcast(
            task.id,
            {"status": task.status, "detail": task.status_detail, "error": task.error},
        )


async def worker_loop():
    while True:
        task = await task_queue.get()
        try:
            await run_pipeline(task)
        except Exception as e:
            task.status = OperationStatus.FAILED
            task.error = str(e)
        finally:
            task_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(worker_loop())
    yield
    worker_task.cancel()


app = FastAPI(title="VocabCatcher", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


# Endpoints
@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/api/tasks")
async def create_task(
    target_language: str = Form(...),
    cefr_level: str = Form(...),
    native_language: str = Form("English"),
    youtube_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: str = Depends(verify_basic_auth),
):
    op_id = str(uuid4())
    file_bytes = None
    file_name = None

    if file and file.filename:
        file_bytes = await file.read()
        file_name = file.filename

    task = OperationTask(
        id=op_id,
        target_language=target_language,
        cefr_level=cefr_level,
        native_language=native_language,
        file_bytes=file_bytes,
        file_name=file_name,
        youtube_url=youtube_url if youtube_url and youtube_url.strip() else None,
    )

    operations[op_id] = task
    await task_queue.put(task)

    return RedirectResponse(url=f"/operation/{op_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/tasks/{op_id}")
async def get_task_status(op_id: str, user: str = Depends(verify_basic_auth)):
    task = operations.get(op_id)
    if not task:
        raise HTTPException(status_code=404, detail="Operation not found")
    return {
        "id": task.id,
        "status": task.status,
        "detail": task.status_detail,
        "error": task.error,
        "items": task.final_items,
    }


class ExportRequest(BaseModel):
    format: str  # "json" or "anki"
    selected_indices: List[int]


@app.post("/api/tasks/{op_id}/export")
async def export_vocab(
    op_id: str,
    req: ExportRequest,
    user: str = Depends(verify_basic_auth),
):
    task = operations.get(op_id)
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

    else:
        raise HTTPException(status_code=400, detail="Invalid export format. Must be 'json' or 'anki'")


# WebSocket for Live Progress Updates
@app.websocket("/ws/operation/{op_id}")
async def websocket_operation(websocket: WebSocket, op_id: str):
    await ws_manager.connect(op_id, websocket)
    task = operations.get(op_id)
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
            # Keep socket alive and respond to client heartbeats
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(op_id, websocket)


# Frontend Serving Routes
@app.get("/", response_class=HTMLResponse)
async def serve_index(user: str = Depends(verify_basic_auth)):
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/operation/{op_id}", response_class=HTMLResponse)
async def serve_operation(op_id: str, user: str = Depends(verify_basic_auth)):
    op_file = STATIC_DIR / "operation.html"
    if not op_file.exists():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=op_file.read_text(encoding="utf-8"))


# Static asset mounting if dist exists
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")
