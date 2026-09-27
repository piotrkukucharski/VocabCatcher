import csv
import io
import json
from typing import Optional
from uuid import uuid4
import genanki
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse

import app.database as db
from app.auth import require_auth
from app.logging_config import correlation_id_ctx
from app.models import ExportRequest, OperationStatus, OperationTask
from app.state import load_task_from_db, operations, running_tasks, task_queue, ws_manager

router = APIRouter(prefix="/api")


@router.post("/tasks")
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


@router.get("/operations")
async def list_operations(user: str = Depends(require_auth)):
    ops = db.list_operations_db()
    return ops


@router.post("/tasks/{op_id}/stop")
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


@router.get("/tasks/{op_id}")
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


@router.post("/tasks/{op_id}/export")
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
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Q. Body", "Q. Clarifier", "A. Body", "A. Footnote"])

        for item in selected_words:
            term = item.get("infinitive") or item.get("phrasal_verb") or "Unknown"
            defn = item.get("native_language_definition", "")
            example = item.get("example_sentence", "")
            variants = ", ".join(item.get("from_source", []))

            writer.writerow([term, f"Forms: {variants}" if variants else "", defn, f'"{example}"' if example else ""])

        csv_content = output.getvalue()
        return Response(
            content=csv_content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=vocabcatcher_brainscape_{op_id}.csv"},
        )

    else:
        raise HTTPException(status_code=400, detail="Invalid export format. Must be 'json', 'anki', or 'csv'")
