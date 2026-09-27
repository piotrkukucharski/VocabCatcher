import asyncio
import io
import json
import os
import re
from contextvars import copy_context
from typing import Any, Dict, List, Optional, Set
from google import genai
from google.genai import types

import app.database as db
from app.config import GEMINI_API_KEY
from app.logging_config import logger
from app.models import (
    OperationStatus,
    OperationTask,
    Phase1ChunkOutput,
    WordDetailOutput,
)
from app.extractor import (
    extract_text_from_file,
    extract_youtube_transcript,
    split_into_sentences,
)

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


async def run_pipeline(task: OperationTask, ws_manager: Any = None):
    from app.state import ws_manager as default_ws_manager
    manager = ws_manager or default_ws_manager

    try:
        task.status = OperationStatus.PARSING
        task.status_detail = "Extracting plain text from source..."
        db.update_operation_status(task.id, task.status, task.status_detail)
        await manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

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
        await manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

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
        await manager.broadcast(task.id, {"status": task.status, "detail": task.status_detail})

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
        await manager.broadcast(
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
        await manager.broadcast(
            task.id,
            {"status": task.status, "detail": task.status_detail, "error": task.error},
        )
