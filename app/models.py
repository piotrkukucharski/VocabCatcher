import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field as PydanticField


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


class Phase1ChunkOutput(BaseModel):
    words: List[str] = PydanticField(
        default_factory=list,
        description="List of words or expressions likely unknown at the target CEFR level.",
    )


class WordDetailOutput(BaseModel):
    infinitive: Optional[str] = None
    phrasal_verb: Optional[str] = None
    native_language_definition: str
    from_source: List[str]
    example_sentence: str


class ExportRequest(BaseModel):
    format: str  # "json" or "anki"
    selected_indices: List[int]
