# VocabCatcher

CEFR-targeted vocabulary extractor with two-step Gemini LLM pipeline and session-based authentication.

---

## Szybki start (Jak uruchomić projekt)

### 1. Przygotowanie środowiska
Jedna komenda **`task install`** przygotowuje całe środowisko robocze od zera:
```bash
cp .env.example .env     # uzupełnij GEMINI_API_KEY
task install             # instaluje uv (Python), npm (Frontend) oraz przeglądarkę Chromium do testów E2E
```

### 2. Uruchomienie aplikacji

- **Tryb deweloperski (rekomendowany)**:
  ```bash
  task dev
  ```
  Uruchamia równolegle backend FastAPI na porcie `8000` (z filtrowaniem i logami zawierającymi `[corr_id=...]`) oraz serwer Vite w tle na porcie `5173`. Wejdź na [http://localhost:8000](http://localhost:8000).

- **Tryb produkcyjny**:
  ```bash
  task start
  ```
  Kompiluje frontend (`npm run build`) i serwuje produkcyjne assety bezpośrednio z FastAPI.

- **Docker**:
  ```bash
  task docker:build
  task docker:run
  ```

---

## Testy

Projekt posiada pełne pokrycie testami jednostkowymi, integracyjnymi oraz **testami E2E z prawdziwą przeglądarką Chromium**:

```bash
task test
```

Testy obejmują:
- [tests/test_browser_e2e.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/tests/test_browser_e2e.py): Pełny flow użytkownika w przeglądarce (logowanie, formularz, tworzenie zadania, wylogowanie).
- [tests/test_integration.py](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/tests/test_integration.py): Parsery plików (PDF, EPUB, DOCX, TXT), YouTube transcripts, integracja sesji, autoryzacji i propagacji `correlation_id`.

---

## Struktura projektu

Architektura backendu została podzielona na wyspecjalizowane moduły:

```
├── app/
│   ├── main.py            # Entrypoint aplikacji, middleware i rejestracja routerów
│   ├── config.py          # Zmienne środowiskowe, stałe i konfiguracja
│   ├── logging_config.py  # Formatter logów i correlation_id ContextVar
│   ├── models.py          # Enums (OperationStatus), dataclass (OperationTask), Pydantic schemas
│   ├── database.py        # Baza danych SQLite (operacje i sesje)
│   ├── auth.py            # Zależności autentykacji (require_auth)
│   ├── extractor.py       # Parsery plików (PDF/EPUB/DOCX/TXT) i transkrypcji YouTube
│   ├── pipeline.py        # Integracja z modelem Google Gemini (Phase 1 & Phase 2)
│   ├── state.py           # Stan in-memory (kolejka zadań, worker_loop, WebSocket manager)
│   └── routers/
│       ├── auth.py        # Trasy logowania i wylogowywania
│       ├── tasks.py       # API zadań, statusów, zatrzymywania i eksportu (JSON/Anki)
│       ├── websocket.py   # WebSocket do live streamingu postępów
│       └── frontend.py    # Serwowanie stron HTML i proxy deweloperskie do Vite
├── web/                   # Frontend Vite + TypeScript + Tailwind CSS + DaisyUI
├── tests/                 # Testy integracyjne i E2E (Playwright)
└── Taskfile.yml           # Definicje zadań automatyzacji (install, dev, test, build, docker)
```

---

## Ścieżki HTTP / Endpointy

| Ścieżka | Metoda | Opis |
| :--- | :--- | :--- |
| `/health` | `GET` | Healthcheck serwera |
| `/login` | `GET`, `POST` | Strona i endpoint logowania (HTML i JSON) |
| `/logout` | `GET`, `POST` | Wylogowanie użytkownika i usunięcie ciasteczka sesji |
| `/api/tasks` | `POST` | Utworzenie nowego zadania ekstrakcji słówek |
| `/api/operations` | `GET` | Pobranie listy historycznych zadań z SQLite |
| `/api/tasks/{id}` | `GET` | Pobranie szczegółów zadania i wyekstrahowanych słówek |
| `/api/tasks/{id}/stop` | `POST` | Zatrzymanie działającego zadania |
| `/api/tasks/{id}/export` | `POST` | Eksport zaznaczonych słówek do JSON lub pliku Anki `.apkg` |
| `/ws/operation/{id}` | `WS` | Połączenie WebSocket do podglądu postępu na żywo |
| `/`, `/operations`, `/operation/{id}` | `GET` | Widoki interfejsu webowego |

---

## Automatyzacja CI/CD (GitHub Actions)

W repozytorium skonfigurowany jest workflow [.github/workflows/ci.yml](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/.github/workflows/ci.yml):
1. **Gating przed mergem do `main`**:
   - Każdy Pull Request oraz push do `main` uruchamia zadanie testowe `Run Tests & Browser E2E`.
   - W środowisku GitHub Actions instalowany jest Pandoc, Node 20, Python 3.11, `uv`, `task install` oraz przeglądarka Playwright Chromium.
   - Uruchamiany jest pełny zestaw testów (`task test`).
2. **Budowanie i publikacja kontenera (Publish)**:
   - Po pomyślnym zaliczeniu testów na gałęzi `main` (lub tagach `v*.*.*`), następuje automatyczne zbudowanie multi-stage obrazu z [infrastructure/Dockerfile](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/infrastructure/Dockerfile) i publikacja do GitHub Container Registry (`ghcr.io`).

---

## Zasady Version Control

W repozytorium **zawsze używamy Jujutsu (`jj`)** zamiast zwykłego `git`. Zobacz [AGENTS.md](file:///home/ptr/Codes/VocabCatcher/VocabCatcher-auth/AGENTS.md).
