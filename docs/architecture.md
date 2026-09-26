# VocabCatcher

VocabCatcher extracts CEFR-targeted vocabulary and contextual definitions from files (epub, pdf, docx, odt, rtf, txt) and YouTube links using a two-step Gemini LLM pipeline, generating Anki decks or JSON exports.

## Monorepo Structure

- `/app`: FastAPI backend, Asyncio worker queue, Google GenAI SDK integration (`gemini-3.8-flash`), pypandoc parsing, YouTube transcript extraction, WebSocket/WebTransport status broadcasting. Managed by `uv` and `pyproject.toml`.
- `/web`: Vite + TypeScript + Tailwind CSS + DaisyUI + HTMX frontend.
- `/infrastructure`: Multi-stage Dockerfile and deployment configurations.
- `/.github/workflows`: CI/CD workflow pushing release images to GitHub Container Registry (`ghcr.io`) on semver tags.
