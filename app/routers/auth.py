import secrets
import urllib.parse
from typing import Optional
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import app.database as db
from app.auth import get_current_user_optional
from app.config import AUTH_PASSWORD, AUTH_USERNAME, SESSION_COOKIE_NAME

router = APIRouter()

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="cupcake">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login - VocabCatcher</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@4.12.23/dist/full.min.css">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-base-200 flex items-center justify-center p-4">
  <div class="card w-full max-w-sm shadow-2xl bg-base-100 border border-base-300">
    <div class="card-body">
      <div class="text-center mb-4">
        <h1 class="text-3xl font-bold text-primary tracking-wide">VocabCatcher</h1>
        <p class="text-sm opacity-70 mt-1">Sign in to access your vocabulary manager</p>
      </div>
      {error_alert}
      <form action="/login" method="POST" class="space-y-4">
        <input type="hidden" name="next" value="{next_url}" />
        <div class="form-control">
          <label class="label"><span class="label-text font-medium">Username</span></label>
          <input type="text" name="username" required placeholder="admin" autofocus class="input input-bordered w-full" />
        </div>
        <div class="form-control">
          <label class="label"><span class="label-text font-medium">Password</span></label>
          <input type="password" name="password" required placeholder="••••••••" class="input input-bordered w-full" />
        </div>
        <div class="form-control">
          <label class="label cursor-pointer justify-start gap-3 py-1">
            <input type="checkbox" name="remember_me" value="true" class="checkbox checkbox-primary checkbox-sm" />
            <span class="label-text text-sm">Remember me for 30 days</span>
          </label>
        </div>
        <div class="form-control mt-4">
          <button type="submit" class="btn btn-primary w-full text-base">Sign In</button>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = "/"):
    current_user = get_current_user_optional(request)
    if current_user:
        target = next if next and next.startswith("/") else "/"
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

    safe_next = next if next and next.startswith("/") else "/"
    return HTMLResponse(
        LOGIN_HTML.format(error_alert="", next_url=urllib.parse.quote(safe_next))
    )


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    remember_me: Optional[str] = Form(None),
    next: Optional[str] = Form("/"),
):
    remember = bool(remember_me and remember_me.lower() in ("true", "1", "on", "yes"))
    if not username and not password:
        try:
            body = await request.json()
            username = body.get("username", "")
            password = body.get("password", "")
            if "remember_me" in body:
                val = body.get("remember_me")
                remember = bool(val is True or str(val).lower() in ("true", "1", "on", "yes"))
            if "next" in body:
                next = body.get("next")
        except Exception:
            pass

    correct_user = secrets.compare_digest(username, AUTH_USERNAME)
    correct_pass = secrets.compare_digest(password, AUTH_PASSWORD)

    if correct_user and correct_pass:
        ttl_days = 30 if remember else 7
        session_token = db.create_session(username=username, ttl_days=ttl_days)
        target = next if next and next.startswith("/") else "/"

        accept_header = request.headers.get("accept", "")
        if "application/json" in accept_header and not "text/html" in accept_header:
            response = JSONResponse({
                "status": "ok",
                "user": username,
                "token": session_token,
                "ttl_days": ttl_days,
            })
        else:
            response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=ttl_days * 86400,
            path="/",
        )
        return response

    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header and not "text/html" in accept_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    safe_next = next if next and next.startswith("/") else "/"
    error_html = """
    <div class="alert alert-error text-sm p-3 mb-2 shadow-sm rounded-lg flex items-center">
      <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5 mr-2" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
      <span>Invalid username or password.</span>
    </div>
    """
    return HTMLResponse(
        LOGIN_HTML.format(error_alert=error_html, next_url=urllib.parse.quote(safe_next)),
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        db.delete_session(session_token)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
