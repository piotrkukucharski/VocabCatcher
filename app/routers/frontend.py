import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.auth import require_auth
from app.config import STATIC_DIR, VITE_DEV_SERVER_URL, is_dev_mode

router = APIRouter()


async def proxy_vite_request(request: Request, path: str = ""):
    target_path = "/" + path.lstrip("/")
    target_url = f"{VITE_DEV_SERVER_URL}{target_path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    candidate_urls = [target_url]
    if "127.0.0.1" in target_url:
        candidate_urls.append(target_url.replace("127.0.0.1", "localhost"))
    elif "localhost" in target_url:
        candidate_urls.append(target_url.replace("localhost", "127.0.0.1"))

    content = await request.body()
    last_exc = None

    for url in candidate_urls:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                proxy_resp = await client.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    content=content,
                )

                excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
                resp_headers = {
                    k: v for k, v in proxy_resp.headers.items() if k.lower() not in excluded_headers
                }

                return Response(
                    content=proxy_resp.content,
                    status_code=proxy_resp.status_code,
                    headers=resp_headers,
                    media_type=proxy_resp.headers.get("content-type"),
                )
        except httpx.RequestError as exc:
            last_exc = exc
            continue

    return HTMLResponse(
        f"<h3>Vite dev server proxy error: {str(last_exc)}</h3><p>Make sure Vite dev server is running on {VITE_DEV_SERVER_URL} (e.g. <code>npm run dev</code> or <code>task dev</code>).</p>",
        status_code=502,
    )


@router.get("/", response_class=HTMLResponse)
async def serve_index(request: Request, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "index.html")

    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@router.get("/operations", response_class=HTMLResponse)
async def serve_operations_list(request: Request, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "operations.html")

    ops_file = STATIC_DIR / "operations.html"
    if not ops_file.exists():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=ops_file.read_text(encoding="utf-8"))


@router.get("/operation/{op_id}", response_class=HTMLResponse)
async def serve_operation(request: Request, op_id: str, user: str = Depends(require_auth)):
    if is_dev_mode():
        return await proxy_vite_request(request, "operation.html")

    op_file = STATIC_DIR / "operation.html"
    if not op_file.exists():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("Frontend not built. Please run `npm run build` inside /web.", status_code=503)
    return HTMLResponse(content=op_file.read_text(encoding="utf-8"))


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def dev_proxy_fallback(request: Request, full_path: str):
    if is_dev_mode():
        return await proxy_vite_request(request, full_path)
    raise HTTPException(status_code=404, detail="Not Found")
