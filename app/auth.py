import urllib.parse
from typing import Optional
from fastapi import HTTPException, Request, status

import app.database as db
from app.config import SESSION_COOKIE_NAME


def get_current_user_optional(request: Request) -> Optional[str]:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        return None
    session = db.get_session(session_token)
    if not session:
        return None
    return session.get("username")


def require_auth(request: Request) -> str:
    user = get_current_user_optional(request)
    if not user:
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated. Please log in.",
            )
        redirect_url = f"/login?next={urllib.parse.quote(str(request.url.path))}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": redirect_url},
        )
    return user
