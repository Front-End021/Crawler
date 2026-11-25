"""Comprehensive test suite for AuthHandler, AgentRunner, and Crawler safety guardrails."""

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
from typing import Generator

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from src.crawler.agent_runner import AgentRunner, CrawlerResult
from src.crawler.auth_handler import AuthHandler


# ==========================================
# Mock Multi-Tab Multi-Action Web Server
# ==========================================

class MultiTabMockServer(BaseHTTPRequestHandler):
    """Local multi-tab web application simulating authenticated and public apps."""

    def log_message(self, format, *args):
        # Silence HTTP logs in test output
        pass

    def do_GET(self):
        # Landing / Dashboard page
        if self.path in ("/", "/dashboard"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Test App - Dashboard</title></head>
            <body>
                <header>
                    <nav>
                        <a href="/dashboard" class="nav-link">Dashboard</a>
                        <a href="/users" class="nav-link">User Management</a>
                        <a href="/billing" class="nav-link">Billing & Plans</a>
                        <a href="/logout" class="nav-link">Log out</a>
                    </nav>
                </header>
                <main>
                    <h1>Dashboard</h1>
                    <button id="view-details" onclick="fetch('/api/v1/metrics')">View Metrics</button>
                </main>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/users":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Test App - Users</title></head>
            <body>
                <nav>
                    <a href="/dashboard" class="nav-link">Dashboard</a>
                    <a href="/users" class="nav-link">User Management</a>
                    <a href="/billing" class="nav-link">Billing & Plans</a>
                    <a href="/logout" class="nav-link">Log out</a>
                </nav>
                <main>
                    <h1>User Directory</h1>
                    <input type="search" placeholder="Search employees" onkeydown="if(event.key==='Enter') fetch('/api/v1/search?q=' + this.value)" />
                    <button onclick="fetch('/api/v1/users?sort=name')">Sort by Name</button>
                    <button class="danger" onclick="fetch('/api/v1/users/1', {method: 'DELETE'})">Delete Account</button>
                </main>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/billing":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Test App - Billing</title></head>
            <body>
                <nav>
                    <a href="/dashboard" class="nav-link">Dashboard</a>
                    <a href="/users" class="nav-link">User Management</a>
                    <a href="/billing" class="nav-link">Billing & Plans</a>
                    <a href="/logout" class="nav-link">Log out</a>
                </nav>
                <main>
                    <h1>Billing</h1>
                    <button onclick="fetch('/api/v1/invoices')">Refresh Invoices</button>
                    <button onclick="fetch('/api/v1/cancel', {method: 'POST'})">Cancel Subscription</button>
                </main>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/spa":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>SPA App</title></head>
            <body>
                <h1>Single Page App</h1>
                <button onclick="fetch('/api/v1/metrics')">Fetch SPA Metrics</button>
                <button onclick="fetch('/api/v1/reset', {method: 'DELETE'})">Reset to Default</button>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head><title>Login</title></head>
            <body>
                <form action="/login" method="POST">
                    <input type="text" name="username" placeholder="Enter username" />
                    <input type="password" name="password" placeholder="Enter password" />
                    <button type="submit">Log In</button>
                </form>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))

        # API Endpoints
        elif self.path.startswith("/api/v1/metrics"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"active_users": 150, "uptime": "99.9%"}).encode("utf-8"))

        elif self.path.startswith("/api/v1/search"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"id": 1, "match": "test"}]).encode("utf-8"))

        elif self.path.startswith("/api/v1/users"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]).encode("utf-8"))

        elif self.path.startswith("/api/v1/invoices"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"invoice_id": "INV-101", "total": 29.99}]).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/login":
            # Redirect to /dashboard upon login
            self.send_response(303)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", "session=valid_token_12345; Path=/")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))


@pytest.fixture(scope="module")
def multi_tab_server():
    """Spin up local multi-tab web application on random port."""
    server = HTTPServer(("127.0.0.1", 0), MultiTabMockServer)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()


# ==========================================
# Tests
# ==========================================

@pytest.mark.asyncio
async def test_auth_handler_element_detection(multi_tab_server: str):
    """Verify AuthHandler correctly detects username, password, and submit elements."""
    auth_handler = AuthHandler()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{multi_tab_server}/login")

        user_sel, pass_sel, submit_sel = await auth_handler.find_login_elements(page)
        assert user_sel is not None
        assert pass_sel is not None
        assert submit_sel is not None
        assert "user" in user_sel.lower()
        assert "pass" in pass_sel.lower()

        await browser.close()


@pytest.mark.asyncio
async def test_auth_handler_successful_authentication(multi_tab_server: str):
    """Verify AuthHandler submits credentials and confirms state transition to dashboard."""
    auth_handler = AuthHandler()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{multi_tab_server}/login")

        success = await auth_handler.authenticate(page, username="testadmin", password="password123")
        assert success is True
        assert "/dashboard" in page.url

        await browser.close()


@pytest.mark.asyncio
async def test_crawler_tab_traversal_and_guardrails(multi_tab_server: str):
    """Verify crawler traverses tabs, skips destructive actions, and correlates API calls."""
    runner = AgentRunner()
    result: CrawlerResult = await runner.crawl(
        target_url=f"{multi_tab_server}/dashboard",
        max_steps=20,
        headless=True,
    )

    # 1. Verification of Tab Traversal
    visited = result.tabs_visited
    assert any("Dashboard" in t for t in visited)
    assert any("User Management" in t for t in visited)
    assert any("Billing & Plans" in t for t in visited)

    # 2. Verification of Safety Guardrail: "Log out" Tab MUST NOT be visited
    assert not any("Log out" in t or "logout" in t.lower() for t in visited)

    # 3. Verification of Blocked Destructive Actions:
    # "Delete Account", "Cancel Subscription", and "Log out" must be blocked
    assert result.destructive_actions_blocked >= 2

    # 4. Verification of Captured and Correlated API Calls
    endpoints = result.captured_endpoints
    assert len(endpoints) >= 2

    # Verify at least metrics and user search/sort are intercepted and correlated
    api_urls = [e.request.url for e in endpoints]
    assert any("/api/v1/metrics" in u for u in api_urls)

    # Verify context correlation (tab name stamped)
    metrics_call = next(e for e in endpoints if "/api/v1/metrics" in e.request.url)
    assert "Dashboard" in metrics_call.context.tab_name or "Landing" in metrics_call.context.tab_name


@pytest.mark.asyncio
async def test_crawler_authenticated_run(multi_tab_server: str):
    """Verify crawler with authentication flow discovers dashboard post-login."""
    runner = AgentRunner()
    result: CrawlerResult = await runner.crawl(
        target_url=f"{multi_tab_server}/login",
        username="admin_user",
        password="super_secret_password",
        max_steps=15,
        headless=True,
    )

    assert result.auth_success is True
    # Verify password is NEVER in any captured request description or URL
    for ep in result.captured_endpoints:
        assert "super_secret_password" not in ep.request.url
        assert "super_secret_password" not in str(ep.request.headers)
        assert "super_secret_password" not in (ep.request.post_data or "")


@pytest.mark.asyncio
async def test_crawler_single_page_mode_and_external_link_and_reset_guardrail():
    """Regression: single-page apps still run controls, block 'Reset to Default', and skip external links."""
    runner = AgentRunner()

    async with async_playwright() as p:
        # Exercise directly against routed local page with 0 nav tabs
        from src.crawler.agent_runner import CrawlerResult
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        runner.sniffer.attach(page)

        reset_called = False
        external_called = False

        async def handle_route(route):
            nonlocal reset_called, external_called
            url = route.request.url
            if "external.example.org" in url:
                external_called = True
                await route.fulfill(status=200, content_type="text/html", body="<html></html>")
            elif "/api/safe" in url:
                await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
            elif "/api/reset" in url:
                reset_called = True
                await route.fulfill(status=200, content_type="application/json", body='{"reset": true}')
            else:
                html = """
                <html><body>
                    <button onclick="fetch('/api/safe')">Fetch Safe Data</button>
                    <button onclick="fetch('/api/reset', {method: 'DELETE'})">Reset to Default</button>
                    <a class="btn" href="https://external.example.org/docs">External Docs</a>
                </body></html>
                """
                await route.fulfill(status=200, content_type="text/html", body=html)

        await page.route("**/*", handle_route)
        await page.goto("http://local.spa/app")

        result = CrawlerResult(tabs_visited=["General / Landing"], actions_executed=1)
        await runner._exercise_tab_functionalities(
            page=page,
            tab_name="General / Landing",
            result=result,
            max_steps=10,
            target_domain="local.spa",
        )
        await runner.sniffer.wait_for_pending(timeout=1.0)
        await browser.close()

    assert reset_called is False
    assert external_called is False
    assert result.destructive_actions_blocked == 1
    assert any("/api/safe" in ep.request.url for ep in runner.sniffer.captured_endpoints)


@pytest.mark.asyncio
async def test_crawler_single_page_crawl_e2e(multi_tab_server: str):
    """Regression: full AgentRunner.crawl() exercises controls in single-page mode (0 nav tabs)."""
    runner = AgentRunner()
    result: CrawlerResult = await runner.crawl(
        target_url=f"{multi_tab_server}/spa",
        max_steps=10,
        headless=True,
    )
    assert result.tabs_visited == ["General / Landing"]
    assert result.actions_executed >= 2
    assert result.destructive_actions_blocked == 1
    assert any("/api/v1/metrics" in ep.request.url for ep in result.captured_endpoints)
    assert not any("/api/v1/reset" in ep.request.url for ep in result.captured_endpoints)
