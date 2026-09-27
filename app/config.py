import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

AUTH_USERNAME: str = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD: str = os.getenv("AUTH_PASSWORD", "changeme123")
SESSION_COOKIE_NAME: str = "session_token"
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
APP_ENV: str = os.getenv("APP_ENV", "production").lower()
VITE_DEV_SERVER_URL: str = os.getenv("VITE_DEV_SERVER_URL", "http://127.0.0.1:5173").rstrip("/")
CORRELATION_ID_HEADER: str = "X-Correlation-ID"

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "web" / "dist"


def is_dev_mode() -> bool:
    if APP_ENV == "development" or os.getenv("DEV_MODE", "").lower() in ("true", "1"):
        return True
    if not STATIC_DIR.exists():
        return True
    return False
