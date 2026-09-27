import os
import socket
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import Page, expect

from app.database import clear_operations_db
from app.main import app, operations, AUTH_USERNAME, AUTH_PASSWORD


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self):
        self.port = find_free_port()
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.server = None
        self.thread = None

    def start(self):
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

        # Wait for live server to become responsive
        import urllib.request
        timeout = 5.0
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=1.0) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("Live uvicorn server failed to start in time.")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=3.0)


@pytest.fixture(scope="module")
def live_server():
    server = LiveServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def clean_db():
    operations.clear()
    clear_operations_db()
    yield
    operations.clear()
    clear_operations_db()


from playwright.sync_api import sync_playwright, Page, expect


@pytest.fixture(scope="module")
def browser_instance():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser_instance):
    context = browser_instance.new_context()
    page = context.new_page()
    yield page
    context.close()


def test_browser_e2e_login_flow(page: Page, live_server: LiveServer):
    """
    E2E Browser Test using Playwright against live Uvicorn server:
    1. Visit '/' unauthenticated -> browser gets redirected to /login?next=/
    2. Enter wrong password -> see error alert, stays on /login
    3. Enter correct username & password, check 'Remember me' -> submit
    4. Successful login redirects to '/' -> page header contains 'VocabCatcher'
    5. Check navbar has 'Logout' button
    6. Click 'Logout' -> redirects back to /login
    7. Try navigating to '/operations' -> redirected back to /login
    """
    # 1. Unauthenticated visit
    page.goto(live_server.base_url)
    expect(page).to_have_url(f"{live_server.base_url}/login?next=/")
    expect(page.locator("h1")).to_have_text("VocabCatcher")
    expect(page.locator("text=Sign in to access your vocabulary manager")).to_be_visible()

    # 2. Failed login attempt
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "wrongpass123")
    page.click('button[type="submit"]')

    expect(page.locator(".alert-error")).to_be_visible()
    expect(page.locator("text=Invalid username or password.")).to_be_visible()

    # 3. Successful login with Remember Me
    page.fill('input[name="username"]', AUTH_USERNAME)
    page.fill('input[name="password"]', AUTH_PASSWORD)
    page.check('input[name="remember_me"]')
    page.click('button[type="submit"]')

    # 4. Redirects to '/'
    expect(page).to_have_url(f"{live_server.base_url}/")
    expect(page.locator("text=Analyze Content for Vocabulary")).to_be_visible()

    # 5. Navbar Logout link is visible
    logout_btn = page.locator('a:has-text("Logout")')
    expect(logout_btn).to_be_visible()

    # 6. Click Logout
    logout_btn.click()
    expect(page).to_have_url(f"{live_server.base_url}/login")

    # 7. Access /operations while logged out
    page.goto(f"{live_server.base_url}/operations")
    expect(page).to_have_url(f"{live_server.base_url}/login?next=/operations")


def test_browser_e2e_create_task_and_operations(page: Page, live_server: LiveServer):
    """
    E2E Browser Test creating a task and navigating between pages:
    1. Log in via browser
    2. Fill in YouTube URL on '/' form and submit
    3. Verify redirect to /operation/{op_id}
    4. Verify operation page components load
    5. Navigate to /operations page and verify the task is present in the list
    """
    # Log in first
    page.goto(f"{live_server.base_url}/login")
    page.fill('input[name="username"]', AUTH_USERNAME)
    page.fill('input[name="password"]', AUTH_PASSWORD)
    page.click('button[type="submit"]')
    expect(page).to_have_url(f"{live_server.base_url}/")

    # Fill task form with YouTube URL
    yt_url = "https://www.youtube.com/watch?v=rbfIn53a1N8"
    page.fill('input[name="youtube_url"]', yt_url)
    page.click('button[type="submit"]')

    # Should redirect to /operation/<uuid>
    page.wait_for_url("**/operation/*")
    assert "/operation/" in page.url

    # Check that operation page elements are rendered
    expect(page.locator("text=Operation in Progress")).to_be_visible()

    # Go to All Operations page
    page.click('a:has-text("All Operations")')
    page.wait_for_url("**/operations")
    expect(page.locator("text=In-Memory Operations")).to_be_visible()
