# Version Control System Guidelines

- **Always use Jujutsu (`jj`)** instead of `git` for all VCS operations in this repository (e.g. checking status, creating revisions/commits, inspecting history, and branching).
- The repository is configured as a colocated repo (`jj git init --colocate`), meaning changes made in `jj` are synchronized with git.
- Useful commands:
  - `jj status`: Inspect working copy status.
  - `jj describe -m "<message>"`: Set description / commit message for the current revision.
  - `jj new`: Start a new working-copy revision on top of the current change.
  - `jj bookmark create <name> -r @-` / `jj bookmark move <name> --to @`: Manage git branch pointers.
  - `jj git push`: Push bookmarks to git remotes.

# Project Development Rules

1. **`task install` must always be sufficient**:
   - Running `task install` must prepare everything required to run and test the application end-to-end from scratch (Python virtualenv via `uv`, frontend dependencies via `npm`, and Playwright headless browser binaries via `playwright install chromium`).
   - Never require manual hidden setup steps outside `task install` (except copying `.env.example` to `.env`).

2. **Testing Requirements**:
   - All functionality must have tests executed via `task test` (`pytest tests/`).
   - The test suite **must include real browser E2E tests** (using Playwright in [tests/test_browser_e2e.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/tests/test_browser_e2e.py)) covering the happy path (logging in, submitting tasks, inspecting results, logging out) alongside integration tests ([tests/test_integration.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/tests/test_integration.py)).

3. **CI/CD Automation (GitHub Actions)**:
   - [.github/workflows/ci.yml](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/.github/workflows/ci.yml) must run tests (unit, integration, and Playwright Chromium browser E2E) on all Pull Requests targeting `main`.
   - Merging to `main` is gated by passing tests.
   - Container images must only be built and published to GHCR (`ghcr.io`) upon successful test completion on `main` push or version tags (`v*.*.*`).

# Architecture & Project Structure

The project follows a clean, modular structure with clear separation of concerns:

### Python Backend (`app/`)
- [app/main.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/main.py): Application entrypoint, lifespan startup/shutdown, correlation ID middleware, router mounting.
- [app/config.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/config.py): Environment variables (`AUTH_USERNAME`, `AUTH_PASSWORD`, `GEMINI_API_KEY`, `APP_ENV`), constants, dev mode detection.
- [app/logging_config.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/logging_config.py): Structured logging formatter injecting `[corr_id=...]` via `ContextVar`.
- [app/models.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/models.py): Enums (`OperationStatus`), dataclass (`OperationTask`), Pydantic schemas (`ExportRequest`, LLM outputs).
- [app/database.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/database.py): SQLite persistence layer for operations and auth sessions.
- [app/auth.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/auth.py): Authentication dependencies (`require_auth`, `get_current_user_optional`).
- [app/extractor.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/extractor.py): Document parsers (TXT, PDF, EPUB, DOCX via pandoc/pypdf) and YouTube transcripts.
- [app/pipeline.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/pipeline.py): Google Gemini LLM pipeline (Phase 1 extraction, Phase 2 contextual definitions).
- [app/state.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/state.py): In-memory queues (`task_queue`), task registry, WebSocket connection manager (`ws_manager`), worker loops.
- `app/routers/`:
  - [app/routers/auth.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/routers/auth.py): `/login` (GET/POST), `/logout` (GET/POST).
  - [app/routers/tasks.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/routers/tasks.py): `/api/tasks` (POST), `/api/operations` (GET), `/api/tasks/{id}` (GET), `/api/tasks/{id}/stop` (POST), `/api/tasks/{id}/export` (POST).
  - [app/routers/websocket.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/routers/websocket.py): `/ws/operation/{op_id}` (live progress streaming).
  - [app/routers/frontend.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/app/routers/frontend.py): UI routes (`/`, `/operations`, `/operation/{id}`) and Vite reverse proxy in development.

### HTTP Endpoints Summary
- `GET /health`: Healthcheck endpoint.
- `GET /login` / `POST /login`: HTML & JSON authentication.
- `GET /logout` / `POST /logout`: Session termination & cookie invalidation.
- `POST /api/tasks`: Create new vocabulary extraction task (multipart form data: file or YouTube URL).
- `GET /api/operations`: List historical operations from SQLite.
- `GET /api/tasks/{op_id}`: Retrieve task details, status, and extracted vocabulary.
- `POST /api/tasks/{op_id}/stop`: Cancel/stop in-progress task.
- `POST /api/tasks/{op_id}/export`: Export selected words as JSON or Anki (`.apkg`) deck.
- `WS /ws/operation/{op_id}`: Real-time progress updates via WebSocket.
- `GET /`, `GET /operations`, `GET /operation/{op_id}`: Frontend single-page views (with dev proxy to Vite).
