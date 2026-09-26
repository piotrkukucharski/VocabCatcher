import io
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, operations, extract_text_from_file, extract_youtube_transcript

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_operations():
    operations.clear()
    yield
    operations.clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- File Extraction & Pandoc/pypdf tests for all formats ---

def test_extract_text_txt():
    content = b"Hello world! This is a simple vocabulary test."
    text = extract_text_from_file(content, "test.txt")
    assert "Hello world!" in text


def test_extract_text_epub():
    epub_path = FIXTURES_DIR / "sample.epub"
    assert epub_path.exists(), "sample.epub fixture missing"
    data = epub_path.read_bytes()
    text = extract_text_from_file(data, "sample.epub")
    assert len(text) > 500
    assert "Eric Weiner" in text or "Geography" in text or "Copyright" in text


def test_extract_text_docx():
    docx_path = FIXTURES_DIR / "sample.docx"
    assert docx_path.exists(), "sample.docx fixture missing"
    data = docx_path.read_bytes()
    text = extract_text_from_file(data, "sample.docx")
    assert len(text) > 500
    assert "Eric Weiner" in text or "Geography" in text or "Copyright" in text


def test_extract_text_pdf():
    pdf_path = FIXTURES_DIR / "sample.pdf"
    assert pdf_path.exists(), "sample.pdf fixture missing"
    data = pdf_path.read_bytes()
    text = extract_text_from_file(data, "sample.pdf")
    assert len(text) > 500
    assert "Geography" in text or "Bliss" in text or "Weiner" in text


def test_extract_youtube_transcript_live():
    # Scenario 1: YouTube link requested by user
    yt_url = "https://www.youtube.com/watch?v=rbfIn53a1N8"
    transcript = extract_youtube_transcript(yt_url)
    assert len(transcript) > 100
    assert "climate" in transcript.lower() or "video" in transcript.lower()


# --- API Integration Scenarios ---

@pytest.mark.asyncio
async def test_scenario_1_youtube_video_task():
    """
    Scenario 1:
    YouTube video: https://www.youtube.com/watch?v=rbfIn53a1N8
    Source language: English
    Native language: Polish
    CEFR level: B1
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Submit YouTube task
        response = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Polish",
                "cefr_level": "B1",
                "youtube_url": "https://www.youtube.com/watch?v=rbfIn53a1N8",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        redirect_url = response.headers["location"]
        op_id = redirect_url.split("/")[-1]
        assert op_id in operations

        task = operations[op_id]
        assert task.target_language == "English"
        assert task.native_language == "Polish"
        assert task.cefr_level == "B1"
        assert task.youtube_url == "https://www.youtube.com/watch?v=rbfIn53a1N8"

        # 2. Check task status via GET /api/tasks/{op_id}
        status_resp = await client.get(f"/api/tasks/{op_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["id"] == op_id
        assert "status" in data

        # 3. Check /api/operations list
        ops_resp = await client.get("/api/operations")
        assert ops_resp.status_code == 200
        ops_list = ops_resp.json()
        assert len(ops_list) >= 1
        assert ops_list[0]["id"] == op_id
        assert ops_list[0]["target_language"] == "English"
        assert ops_list[0]["native_language"] == "Polish"


@pytest.mark.asyncio
async def test_scenario_2_epub_file_upload_task():
    """
    Scenario 2:
    E-Book upload: sample.epub
    Source: English, Native: Polish, Level: B2
    """
    epub_path = FIXTURES_DIR / "sample.epub"
    data = epub_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Polish",
                "cefr_level": "B2",
            },
            files={"file": ("sample.epub", data, "application/epub+zip")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        op_id = response.headers["location"].split("/")[-1]
        assert op_id in operations

        task = operations[op_id]
        assert task.file_name == "sample.epub"
        assert task.file_bytes is not None
        assert len(task.file_bytes) == len(data)


@pytest.mark.asyncio
async def test_scenario_3_docx_file_upload_task():
    """
    Scenario 3:
    Word document upload: sample.docx
    Source: English, Native: Polish, Level: A2
    """
    docx_path = FIXTURES_DIR / "sample.docx"
    data = docx_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Polish",
                "cefr_level": "A2",
            },
            files={"file": ("sample.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        op_id = response.headers["location"].split("/")[-1]
        assert op_id in operations
        assert operations[op_id].file_name == "sample.docx"


@pytest.mark.asyncio
async def test_scenario_4_pdf_file_upload_task():
    """
    Scenario 4:
    PDF document upload: sample.pdf
    Source: English, Native: Spanish, Level: C1
    """
    pdf_path = FIXTURES_DIR / "sample.pdf"
    data = pdf_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Spanish",
                "cefr_level": "C1",
            },
            files={"file": ("sample.pdf", data, "application/pdf")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        op_id = response.headers["location"].split("/")[-1]
        assert op_id in operations
        assert operations[op_id].native_language == "Spanish"


@pytest.mark.asyncio
async def test_scenario_5_txt_file_upload_task():
    """
    Scenario 5:
    Plain text file upload: sample.txt
    Source: English, Native: German, Level: B1
    """
    txt_path = FIXTURES_DIR / "sample.txt"
    data = txt_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "German",
                "cefr_level": "B1",
            },
            files={"file": ("sample.txt", data, "text/plain")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        op_id = response.headers["location"].split("/")[-1]
        assert op_id in operations


@pytest.mark.asyncio
async def test_scenario_6_stop_running_task():
    """
    Scenario 6:
    Stop an operation via POST /api/tasks/{op_id}/stop
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit a task
        resp = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Polish",
                "cefr_level": "B1",
                "youtube_url": "https://www.youtube.com/watch?v=rbfIn53a1N8",
            },
            follow_redirects=False,
        )
        op_id = resp.headers["location"].split("/")[-1]

        # Call stop
        stop_resp = await client.post(f"/api/tasks/{op_id}/stop")
        assert stop_resp.status_code == 200
        stop_data = stop_resp.json()
        assert stop_data["status"] == "Stopped"

        task = operations[op_id]
        assert task.status == "Stopped"
        assert "stopped" in task.status_detail.lower()


@pytest.mark.asyncio
async def test_scenario_7_export_vocab_json_and_anki():
    """
    Scenario 7:
    Export ready vocabulary in JSON and Anki .apkg format
    """
    from app.main import OperationTask, OperationStatus

    op_id = "test-export-op"
    sample_items = [
        {
            "infinitive": "controversial",
            "native_language_definition": "kontrowersyjny, wywołujący spory",
            "from_source": ["controversial"],
            "example_sentence": "It was a controversial decision that surprised everyone.",
        },
        {
            "phrasal_verb": "pick fights",
            "native_language_definition": "zaczepiać, szukać zaczepki",
            "from_source": ["pick fights"],
            "example_sentence": "He likes to pick fights with his colleagues.",
        },
    ]

    task = OperationTask(
        id=op_id,
        target_language="English",
        native_language="Polish",
        cefr_level="B1",
        status=OperationStatus.READY,
        final_items=sample_items,
    )
    operations[op_id] = task

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Export as JSON
        json_resp = await client.post(
            f"/api/tasks/{op_id}/export",
            json={"format": "json", "selected_indices": [0, 1]},
        )
        assert json_resp.status_code == 200
        assert json_resp.headers["content-type"].startswith("application/json")
        exported_data = json_resp.json()
        assert len(exported_data) == 2
        assert exported_data[0]["infinitive"] == "controversial"

        # Export as Anki .apkg
        anki_resp = await client.post(
            f"/api/tasks/{op_id}/export",
            json={"format": "anki", "selected_indices": [0, 1]},
        )
        assert anki_resp.status_code == 200
        assert anki_resp.headers["content-type"].startswith("application/octet-stream")
        assert len(anki_resp.content) > 1000  # valid zip/sqlite apkg payload
