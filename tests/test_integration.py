import io
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import clear_operations_db
from app.main import app, operations, extract_text_from_file, extract_youtube_transcript

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_operations():
    operations.clear()
    clear_operations_db()
    yield
    operations.clear()
    clear_operations_db()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def auth_cookie():
    import app.database as db
    from app.main import AUTH_USERNAME, SESSION_COOKIE_NAME
    token = db.create_session(AUTH_USERNAME)
    return {SESSION_COOKIE_NAME: token}


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
async def test_api_unauthenticated_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/operations without auth
        resp = await client.get("/api/operations")
        assert resp.status_code == 401
        assert "Not authenticated" in resp.text

        # POST /api/tasks without auth
        task_resp = await client.post("/api/tasks", data={"target_language": "English", "cefr_level": "B1"})
        assert task_resp.status_code == 401

        # GET /api/tasks/{id} without auth
        status_resp = await client.get("/api/tasks/fake-id")
        assert status_resp.status_code == 401

        # POST /api/tasks/{id}/stop without auth
        stop_resp = await client.post("/api/tasks/fake-id/stop")
        assert stop_resp.status_code == 401

        # POST /api/tasks/{id}/export without auth
        export_resp = await client.post("/api/tasks/fake-id/export", json={"format": "json", "selected_indices": []})
        assert export_resp.status_code == 401


@pytest.mark.asyncio
async def test_scenario_1_youtube_video_task(auth_cookie):
    """
    Scenario 1:
    YouTube video: https://www.youtube.com/watch?v=rbfIn53a1N8
    Source language: English
    Native language: Polish
    CEFR level: B1
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_2_epub_file_upload_task(auth_cookie):
    """
    Scenario 2:
    E-Book upload: sample.epub
    Source: English, Native: Polish, Level: B2
    """
    epub_path = FIXTURES_DIR / "sample.epub"
    data = epub_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_3_docx_file_upload_task(auth_cookie):
    """
    Scenario 3:
    Word document upload: sample.docx
    Source: English, Native: Polish, Level: A2
    """
    docx_path = FIXTURES_DIR / "sample.docx"
    data = docx_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_4_pdf_file_upload_task(auth_cookie):
    """
    Scenario 4:
    PDF document upload: sample.pdf
    Source: English, Native: Spanish, Level: C1
    """
    pdf_path = FIXTURES_DIR / "sample.pdf"
    data = pdf_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_5_txt_file_upload_task(auth_cookie):
    """
    Scenario 5:
    Plain text file upload: sample.txt
    Source: English, Native: German, Level: B1
    """
    txt_path = FIXTURES_DIR / "sample.txt"
    data = txt_path.read_bytes()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_6_stop_running_task(auth_cookie):
    """
    Scenario 6:
    Stop an operation via POST /api/tasks/{op_id}/stop
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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
async def test_scenario_7_export_vocab_json_and_anki(auth_cookie):
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
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
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

        # Export as Brainscape CSV
        csv_resp = await client.post(
            f"/api/tasks/{op_id}/export",
            json={"format": "csv", "selected_indices": [0, 1]},
        )
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        csv_text = csv_resp.content.decode("utf-8-sig")
        lines = [line.strip() for line in csv_text.strip().split("\n") if line.strip()]
        # Check Brainscape header columns
        assert lines[0] == "Q. Body,Q. Clarifier,A. Body,A. Footnote"
        assert "controversial" in lines[1]
        assert "kontrowersyjny" in lines[1]
        assert "pick fights" in lines[2]


@pytest.mark.asyncio
async def test_session_auth_flow():
    from app.main import AUTH_USERNAME, AUTH_PASSWORD, SESSION_COOKIE_NAME
    import app.database as db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated request to / redirects to /login
        unauth_resp = await client.get("/", follow_redirects=False)
        assert unauth_resp.status_code == 303
        assert "/login" in unauth_resp.headers["location"]

        # 2. Login page GET renders login HTML
        login_page_resp = await client.get("/login")
        assert login_page_resp.status_code == 200
        assert "Sign in to access your vocabulary manager" in login_page_resp.text

        # 3. Invalid credentials POST returns 401
        bad_login_resp = await client.post(
            "/login",
            data={"username": "wronguser", "password": "wrongpassword"},
        )
        assert bad_login_resp.status_code == 401
        assert "Invalid username or password" in bad_login_resp.text

        # 4. Successful login sets session cookie and redirects
        good_login_resp = await client.post(
            "/login",
            data={"username": AUTH_USERNAME, "password": AUTH_PASSWORD, "next": "/operations"},
            follow_redirects=False,
        )
        assert good_login_resp.status_code == 303
        assert good_login_resp.headers["location"] == "/operations"
        assert SESSION_COOKIE_NAME in good_login_resp.cookies
        session_token = good_login_resp.cookies[SESSION_COOKIE_NAME]

        # Verify session is in DB
        db_session = db.get_session(session_token)
        assert db_session is not None
        assert db_session["username"] == AUTH_USERNAME

        # 5. Access / with session cookie
        authed_resp = await client.get("/")
        assert authed_resp.status_code == 200
        assert "VocabCatcher" in authed_resp.text

        # 6. Logout clears cookie and removes from DB
        logout_resp = await client.get("/logout", follow_redirects=False)
        assert logout_resp.status_code == 303
        assert "/login" in logout_resp.headers["location"]
        assert db.get_session(session_token) is None

        # 7. Remember me for 30 days sets 30-day TTL in database and max_age
        remember_resp = await client.post(
            "/login",
            data={
                "username": AUTH_USERNAME,
                "password": AUTH_PASSWORD,
                "remember_me": "true",
            },
            follow_redirects=False,
        )
        assert remember_resp.status_code == 303
        rem_token = remember_resp.cookies[SESSION_COOKIE_NAME]
        rem_session = db.get_session(rem_token)
        assert rem_session is not None
        # Should expire in ~30 days (more than 28 days)
        import time
        assert rem_session["expires_at"] - time.time() > 28 * 86400


@pytest.mark.asyncio
async def test_clean_expired_sessions():
    import time
    import app.database as db
    from app.main import AUTH_USERNAME

    # Create one valid session and two expired sessions
    valid_token = db.create_session(AUTH_USERNAME, ttl_days=7)
    expired_token_1 = db.create_session(AUTH_USERNAME, ttl_days=-1)
    expired_token_2 = db.create_session(AUTH_USERNAME, ttl_days=-5)

    # Calling clean_expired_sessions should purge expired ones
    cleaned_count = db.clean_expired_sessions()
    assert cleaned_count >= 2

    # Expired tokens are gone
    assert db.get_session(expired_token_1) is None
    assert db.get_session(expired_token_2) is None

    # Valid token remains
    assert db.get_session(valid_token) is not None


@pytest.mark.asyncio
async def test_sqlite_persistence_and_prod_frontend():
    import app.database as db
    from app.main import AUTH_USERNAME, SESSION_COOKIE_NAME

    op_id = "test-sqlite-persistence-id"
    db.save_operation(
        op_id=op_id,
        target_language="French",
        native_language="Polish",
        cefr_level="B2",
        status="Ready",
        status_detail="Finished",
        created_at=123456789.0,
        youtube_url="https://youtube.com/watch?v=sample",
        final_items=[{"infinitive": "bonjour", "native_language_definition": "dzień dobry", "from_source": ["bonjour"], "example_sentence": "Bonjour!"}]
    )

    token = db.create_session(AUTH_USERNAME)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={SESSION_COOKIE_NAME: token}) as client:
        # Check operations list from DB
        resp = await client.get("/api/operations")
        assert resp.status_code == 200
        ops = resp.json()
        assert any(o["id"] == op_id for o in ops)

        # Check frontend serving with session auth
        auth_resp = await client.get("/")
        assert auth_resp.status_code == 200
        assert "VocabCatcher" in auth_resp.text


@pytest.mark.asyncio
async def test_dev_proxy_routing(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    import app.database as db
    from app.main import AUTH_USERNAME, SESSION_COOKIE_NAME

    monkeypatch.setenv("APP_ENV", "development")

    mock_resp = httpx.Response(
        status_code=200,
        content=b"<html><head><title>Vite App Dev</title></head><body>Dev Mode</body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )

    async def mock_request(*args, **kwargs):
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)

    token = db.create_session(AUTH_USERNAME)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={SESSION_COOKIE_NAME: token}) as client:
        # Requesting / in dev mode proxies to Vite
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Vite App Dev" in resp.text

        # Requesting Vite HMR/script asset in dev mode proxies to Vite
        asset_resp = await client.get("/@vite/client")
        assert asset_resp.status_code == 200


@pytest.mark.asyncio
async def test_e2e_happy_path_flow():
    """
    E2E Happy Path Test:
    1. Unauthenticated user visits '/' and is redirected to '/login?next=/'
    2. User submits login form with valid credentials and 'remember_me'
    3. Session cookie is received; user is redirected to '/' and sees index HTML
    4. User navigates to '/operations' and sees operations list HTML
    5. User submits a new YouTube task via POST /api/tasks with session cookie
    6. System creates operation and redirects to /operation/{op_id}
    7. User checks task status via GET /api/tasks/{op_id} and operations list via GET /api/operations
    8. Once pipeline generates final items, user exports selected vocabulary as JSON
    9. User logs out via GET /logout; cookie is cleared and user is redirected back to /login
    10. Subsequent requests to / or /api/operations are blocked (redirect to /login and 401 respectively)
    """
    from app.main import AUTH_USERNAME, AUTH_PASSWORD, SESSION_COOKIE_NAME, OperationStatus

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Unauthenticated request redirects to /login
        unauth_resp = await client.get("/", follow_redirects=False)
        assert unauth_resp.status_code == 303
        assert "/login" in unauth_resp.headers["location"]

        # Step 2: Perform login with valid credentials and remember_me
        login_resp = await client.post(
            "/login",
            data={
                "username": AUTH_USERNAME,
                "password": AUTH_PASSWORD,
                "remember_me": "true",
                "next": "/",
            },
            follow_redirects=False,
        )
        assert login_resp.status_code == 303
        assert login_resp.headers["location"] == "/"
        assert SESSION_COOKIE_NAME in login_resp.cookies

        # Step 3: Access protected '/' index page
        home_resp = await client.get("/")
        assert home_resp.status_code == 200
        assert "VocabCatcher" in home_resp.text

        # Step 4: Access protected '/operations' page
        ops_page_resp = await client.get("/operations")
        assert ops_page_resp.status_code == 200
        assert "VocabCatcher" in ops_page_resp.text

        # Step 5: Add a YouTube link task via POST /api/tasks
        yt_url = "https://www.youtube.com/watch?v=rbfIn53a1N8"
        create_resp = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "native_language": "Polish",
                "cefr_level": "B1",
                "youtube_url": yt_url,
            },
            follow_redirects=False,
        )
        assert create_resp.status_code == 303
        redirect_location = create_resp.headers["location"]
        assert redirect_location.startswith("/operation/")
        op_id = redirect_location.split("/")[-1]
        assert op_id in operations

        # Step 6: Access operation page in browser
        op_page_resp = await client.get(f"/operation/{op_id}")
        assert op_page_resp.status_code == 200

        # Step 7: Check task status via API
        status_resp = await client.get(f"/api/tasks/{op_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["id"] == op_id
        assert data["target_language"] == "English"
        assert data["source"] == yt_url

        # Check operations list via API
        list_resp = await client.get("/api/operations")
        assert list_resp.status_code == 200
        ops_list = list_resp.json()
        assert any(item["id"] == op_id for item in ops_list)

        # Step 8: Populate results and test export
        task = operations[op_id]
        task.status = OperationStatus.READY
        task.final_items = [
            {
                "infinitive": "mitigate",
                "native_language_definition": "łagodzić, minimalizować",
                "from_source": ["mitigate"],
                "example_sentence": "We must mitigate climate risks.",
            }
        ]
        import app.database as db
        db.update_operation_status(op_id, task.status, task.status_detail, final_items=task.final_items)

        # Verify results returned by API
        ready_status_resp = await client.get(f"/api/tasks/{op_id}")
        assert ready_status_resp.status_code == 200
        items = ready_status_resp.json()["items"]
        assert len(items) == 1
        assert items[0]["infinitive"] == "mitigate"

        # Export vocabulary as JSON
        export_resp = await client.post(
            f"/api/tasks/{op_id}/export",
            json={"format": "json", "selected_indices": [0]},
        )
        assert export_resp.status_code == 200
        assert export_resp.headers["content-type"].startswith("application/json")
        exported_items = export_resp.json()
        assert len(exported_items) == 1
        assert exported_items[0]["infinitive"] == "mitigate"

        # Step 9: Logout
        logout_resp = await client.get("/logout", follow_redirects=False)
        assert logout_resp.status_code == 303
        assert "/login" in logout_resp.headers["location"]

        # Step 10: Verify user is logged out
        post_logout_resp = await client.get("/", follow_redirects=False)
        assert post_logout_resp.status_code == 303
        assert "/login" in post_logout_resp.headers["location"]

        api_after_logout = await client.get("/api/operations")
        assert api_after_logout.status_code == 401


@pytest.mark.asyncio
async def test_correlation_id_header():
    from app.main import CORRELATION_ID_HEADER
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Request without header -> server generates and returns X-Correlation-ID
        resp_no_header = await client.get("/health")
        assert resp_no_header.status_code == 200
        assert CORRELATION_ID_HEADER in resp_no_header.headers
        generated_id = resp_no_header.headers[CORRELATION_ID_HEADER]
        assert len(generated_id) > 10

        # 2. Request with custom X-Correlation-ID -> server preserves and returns it
        custom_id = "test-custom-corr-12345"
        resp_with_header = await client.get("/health", headers={CORRELATION_ID_HEADER: custom_id})
        assert resp_with_header.status_code == 200
        assert resp_with_header.headers[CORRELATION_ID_HEADER] == custom_id

        # 3. Request with X-Request-ID fallback -> server adopts it as correlation id
        request_id = "test-req-id-67890"
        resp_req_id = await client.get("/health", headers={"X-Request-ID": request_id})
        assert resp_req_id.status_code == 200
        assert resp_req_id.headers[CORRELATION_ID_HEADER] == request_id


@pytest.mark.asyncio
async def test_task_correlation_id_propagation(auth_cookie, monkeypatch):
    import logging
    from app.main import CORRELATION_ID_HEADER, correlation_id_ctx, run_pipeline

    custom_corr_id = "test-task-corr-abc-999"
    captured_corr_ids = []

    # Mock extract_text_from_file and process_phase1/2 to intercept correlation_id in pipeline
    async def mock_run_pipeline(task):
        captured_corr_ids.append(correlation_id_ctx.get("-"))

    monkeypatch.setattr("app.main.run_pipeline", mock_run_pipeline)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as client:
        # Submit task with custom correlation ID
        resp = await client.post(
            "/api/tasks",
            data={
                "target_language": "English",
                "cefr_level": "B2",
                "native_language": "Polish",
            },
            files={"file": ("sample.txt", b"Hello world test content", "text/plain")},
            headers={CORRELATION_ID_HEADER: custom_corr_id},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        op_id = resp.headers["location"].split("/")[-1]

        # Verify task has correlation_id stored
        assert op_id in operations
        assert operations[op_id].correlation_id == custom_corr_id
