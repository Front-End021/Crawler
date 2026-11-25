"""Comprehensive test suite for ContextTracker and NetworkSniffer."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from typing import Generator

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from src.config import Settings
from src.interceptor.context_tracker import ContextTracker
from src.interceptor.network_sniffer import NetworkSniffer


# ==========================================
# ContextTracker Unit Tests
# ==========================================

@pytest.mark.asyncio
async def test_context_tracker_defaults():
    """Verify default initialization values."""
    tracker = ContextTracker()
    assert tracker.current_tab == "General / Landing"
    assert tracker.current_action == "Initial Page Load"

    ctx = tracker.get_current_context()
    assert ctx.tab_name == "General / Landing"
    assert ctx.action_name == "Initial Page Load"


@pytest.mark.asyncio
async def test_context_tracker_lifecycle_hooks():
    """Verify before_action, after_action, set_tab, and history tracking."""
    tracker = ContextTracker()

    # Change tab
    await tracker.set_tab("User Management")
    assert tracker.current_tab == "User Management"
    assert "User Management" in tracker.current_action

    # Hook before action
    ctx = await tracker.before_action("Click Add User Button", tab_name="User Management")
    assert ctx.tab_name == "User Management"
    assert ctx.action_name == "Click Add User Button"
    assert tracker.current_action == "Click Add User Button"

    # Hook after action
    await tracker.after_action()

    # History verification
    history = tracker.get_history()
    assert len(history) >= 3
    assert history[-1].action_name == "Click Add User Button"

    # Reset
    tracker.reset()
    assert tracker.current_tab == "General / Landing"
    assert tracker.current_action == "Initial Page Load"


# ==========================================
# NetworkSniffer Unit & Parsing Tests
# ==========================================

def test_sniffer_api_filtering_rules():
    """Verify API request classification and rejection of static/analytics traffic."""
    sniffer = NetworkSniffer()

    # Allowed API requests
    assert sniffer.is_api_request("fetch", "https://api.example.com/v1/items") is True
    assert sniffer.is_api_request("xhr", "https://app.example.com/data.json") is True
    assert sniffer.is_api_request("fetch", "https://app.example.com/graphql") is True

    # Ignored non-XHR/fetch types
    assert sniffer.is_api_request("document", "https://example.com/dashboard") is False
    assert sniffer.is_api_request("stylesheet", "https://example.com/styles.css") is False
    assert sniffer.is_api_request("image", "https://example.com/avatar.png") is False
    assert sniffer.is_api_request("font", "https://example.com/font.woff2") is False

    # Ignored static extensions despite fetch type
    assert sniffer.is_api_request("fetch", "https://example.com/assets/app.min.js") is False
    assert sniffer.is_api_request("fetch", "https://example.com/assets/theme.css") is False
    assert sniffer.is_api_request("fetch", "https://example.com/img/icon.svg") is False

    # Ignored telemetry domains
    assert sniffer.is_api_request("fetch", "https://www.google-analytics.com/analytics.js") is False
    assert sniffer.is_api_request("xhr", "https://sentry.io/api/123/envelope/") is False


def test_sniffer_graphql_detection_and_operation_name():
    """Verify GraphQL identification and operationName extraction."""
    sniffer = NetworkSniffer()

    # Case 1: JSON payload with operationName
    payload1 = {
        "operationName": "GetBillingHistory",
        "query": "query GetBillingHistory { billing { id amount } }",
        "variables": {"limit": 10},
    }
    is_gql, op_name = sniffer._parse_graphql("https://api.example.com/graphql", payload1, json.dumps(payload1))
    assert is_gql is True
    assert op_name == "GetBillingHistory"

    # Case 2: JSON payload without operationName, query string regex match
    payload2 = {
        "query": "mutation UpdateUserStatus($id: ID!) { updateUser(id: $id) { success } }",
    }
    is_gql, op_name = sniffer._parse_graphql("https://api.example.com/api", payload2, json.dumps(payload2))
    assert is_gql is True
    assert op_name == "UpdateUserStatus"

    # Case 3: URL contains /graphql with raw query string
    raw_query = "query SearchProducts { products { id title } }"
    is_gql, op_name = sniffer._parse_graphql("https://api.example.com/graphql", None, raw_query)
    assert is_gql is True
    assert op_name == "SearchProducts"

    # Case 4: Anonymous GraphQL query
    is_gql, op_name = sniffer._parse_graphql("https://api.example.com/graphql", {"query": "{ ping }"}, None)
    assert is_gql is True
    assert op_name == "AnonymousGraphQL"

    # Case 5: Standard REST request
    is_gql, op_name = sniffer._parse_graphql("https://api.example.com/v1/users", {"name": "Alice"}, None)
    assert is_gql is False
    assert op_name is None


def test_sniffer_query_param_extraction():
    """Verify URL query parameters parsing and flattening."""
    sniffer = NetworkSniffer()
    url = "https://api.example.com/search?q=laptop&category=electronics&tags=sale&tags=featured&page=1"
    params = sniffer._parse_query_params(url)
    assert params["q"] == "laptop"
    assert params["category"] == "electronics"
    assert params["page"] == "1"
    assert isinstance(params["tags"], list)
    assert "sale" in params["tags"]


# ==========================================
# Live Playwright Integration Tests
# ==========================================

class MockServerHandler(BaseHTTPRequestHandler):
    """Local HTTP mock server providing HTML and mock API endpoints."""

    def log_message(self, format, *args):
        # Silence HTTP server logging during test run
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <link rel="stylesheet" href="/static/style.css">
                <script src="/static/app.js"></script>
            </head>
            <body>
                <h1>Mock Application</h1>
            </body>
            </html>
            """
            self.wfile.write(html.encode("utf-8"))
        elif self.path.startswith("/api/v1/users"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = [{"id": 1, "name": "Jane Doe", "role": "Engineer"}]
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/static/style.css":
            self.send_response(200)
            self.send_header("Content-Type", "text/css")
            self.end_headers()
            self.wfile.write(b"body { background: #fff; }")
        elif self.path == "/static/app.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(b"console.log('static asset');")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

        if self.path == "/api/v1/orders":
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"order_id": "ORD-999", "status": "created"}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        elif self.path == "/graphql":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"data": {"user": {"id": "100", "email": "test@example.com"}}}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def mock_server():
    """Spin up local HTTP mock server on random free port."""
    server = HTTPServer(("127.0.0.1", 0), MockServerHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_playwright_routed_sniffing():
    """Test Playwright sniffing using in-memory page.route fulfilling."""
    tracker = ContextTracker()
    sniffer = NetworkSniffer(context_tracker=tracker)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        sniffer.attach(page)

        # Mock route handler
        async def handle_route(route):
            url = route.request.url
            if "/api/v1/test" in url:
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"success": True, "count": 42}),
                )
            else:
                await route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body><h1>Test</h1></body></html>",
                )

        await page.route("http://local.test/**", handle_route)

        await tracker.before_action("Fetch Local Data", tab_name="Internal Tab")
        await page.goto("http://local.test/home")

        await page.evaluate(
            """
            fetch('http://local.test/api/v1/test?filter=enabled', {
                method: 'GET',
                headers: { 'Authorization': 'Bearer secret-abc' }
            })
            """
        )
        await asyncio.sleep(0.3)
        await browser.close()

    captured = sniffer.captured_endpoints
    assert len(captured) == 1
    assert captured[0].context.tab_name == "Internal Tab"
    assert captured[0].context.action_name == "Fetch Local Data"
    assert captured[0].request.method == "GET"
    assert captured[0].request.query_params["filter"] == "enabled"
    assert captured[0].request.headers.get("authorization") == "{{auth_token}}" or captured[0].request.headers.get("Authorization") == "{{auth_token}}"
    assert captured[0].request.response_status == 200
    assert "count" in (captured[0].request.response_body_preview or "")


@pytest.mark.asyncio
async def test_playwright_live_sniffing_and_correlation(mock_server: str):
    """End-to-end integration test exercising Playwright network sniffer with live HTTP requests."""
    tracker = ContextTracker()
    sniffer = NetworkSniffer(context_tracker=tracker)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Attach sniffer to page
        sniffer.attach(page)

        # 1. Initial page load (loads HTML + static CSS + static JS)
        await tracker.before_action("Load Initial Page", tab_name="Landing Page")
        await page.goto(f"{mock_server}/")
        await page.wait_for_load_state("networkidle")

        # 2. Tab: Users -> Action: Search Users (REST GET with query params + Auth token)
        await tracker.before_action("Search Users Query", tab_name="User Directory")
        await page.evaluate(
            f"""
            fetch('{mock_server}/api/v1/users?role=Engineer&status=active', {{
                method: 'GET',
                headers: {{
                    'Authorization': 'Bearer test-secret-token-12345',
                    'Cookie': 'session=abc123xyz'
                }}
            }})
            """
        )
        # Give a small moment for async response event processing
        await asyncio.sleep(0.3)

        # 3. Tab: Orders -> Action: Submit New Order (REST POST with JSON body)
        await tracker.before_action("Create New Order", tab_name="Order Management")
        await page.evaluate(
            f"""
            fetch('{mock_server}/api/v1/orders', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer order-token-xyz'
                }},
                body: JSON.stringify({{ item: 'Laptop', quantity: 2 }})
            }})
            """
        )
        await asyncio.sleep(0.3)

        # 4. Tab: Analytics -> Action: Query User Details (GraphQL POST with operationName)
        await tracker.before_action("Fetch Profile Metrics", tab_name="Analytics Dashboard")
        await page.evaluate(
            f"""
            fetch('{mock_server}/graphql', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify({{
                    operationName: 'GetUserMetrics',
                    query: 'query GetUserMetrics {{ user {{ id email }} }}',
                    variables: {{ id: '100' }}
                }})
            }})
            """
        )
        await asyncio.sleep(0.3)

        await browser.close()

    captured = sniffer.captured_endpoints
    # Verify ONLY the 3 API calls were captured (static .css and .js MUST NOT be in captured)
    assert len(captured) == 3

    # Verification 1: REST GET /api/v1/users
    req1 = captured[0].request
    ctx1 = captured[0].context
    assert ctx1.tab_name == "User Directory"
    assert ctx1.action_name == "Search Users Query"
    assert req1.method == "GET"
    assert "/api/v1/users" in req1.url
    assert req1.query_params["role"] == "Engineer"
    assert req1.query_params["status"] == "active"
    assert req1.headers.get("authorization") == "{{auth_token}}" or req1.headers.get("Authorization") == "{{auth_token}}"
    assert req1.response_status == 200
    assert "Jane Doe" in (req1.response_body_preview or "")

    # Verification 2: REST POST /api/v1/orders
    req2 = captured[1].request
    ctx2 = captured[1].context
    assert ctx2.tab_name == "Order Management"
    assert ctx2.action_name == "Create New Order"
    assert req2.method == "POST"
    assert "/api/v1/orders" in req2.url
    assert req2.post_data_json == {"item": "Laptop", "quantity": 2}
    assert req2.response_status == 201
    assert "ORD-999" in (req2.response_body_preview or "")

    # Verification 3: GraphQL POST /graphql
    req3 = captured[2].request
    ctx3 = captured[2].context
    assert ctx3.tab_name == "Analytics Dashboard"
    assert ctx3.action_name == "Fetch Profile Metrics"
    assert req3.method == "POST"
    assert req3.is_graphql is True
    assert req3.graphql_operation_name == "GetUserMetrics"
    assert req3.response_status == 200
    assert "test@example.com" in (req3.response_body_preview or "")


@pytest.mark.asyncio
async def test_playwright_live_public_api_sniffing():
    """Verify network sniffing against live public API site (jsonplaceholder.typicode.com)."""
    tracker = ContextTracker()
    sniffer = NetworkSniffer(context_tracker=tracker)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        sniffer.attach(page)

        await tracker.before_action("Fetch Public Posts", tab_name="JSONPlaceholder")
        await page.goto("about:blank")
        await page.evaluate(
            """
            fetch('https://jsonplaceholder.typicode.com/posts/1', {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Authorization': 'Bearer sample-public-token'
                }
            })
            """
        )
        await asyncio.sleep(0.8)
        await browser.close()

    captured = sniffer.captured_endpoints
    assert len(captured) >= 1
    post_req = next((c for c in captured if "posts/1" in c.request.url), None)
    assert post_req is not None
    assert post_req.context.tab_name == "JSONPlaceholder"
    assert post_req.context.action_name == "Fetch Public Posts"
    assert post_req.request.method == "GET"
    assert post_req.request.response_status == 200
    assert post_req.request.headers.get("authorization") == "{{auth_token}}" or post_req.request.headers.get("Authorization") == "{{auth_token}}"
    assert "userId" in (post_req.request.response_body_preview or "")


@pytest.mark.asyncio
async def test_sniffer_idempotent_attach_and_response_header_sanitization():
    """Regression: double attach() does not duplicate endpoints, and sensitive response headers are masked."""
    tracker = ContextTracker()
    sniffer = NetworkSniffer(context_tracker=tracker)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Attach twice on the same page
        sniffer.attach(page)
        sniffer.attach(page)

        async def handle_route(route):
            if "/api/v1/session" in route.request.url:
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={
                        "X-Api-Key": "secret-resp-key",
                        "X-Auth-Token": "secret-resp-token",
                        "X-Custom": "ok",
                    },
                    body=json.dumps({"ok": True}),
                )
            else:
                await route.fulfill(status=200, content_type="text/html", body="<html></html>")

        await page.route("http://local.test/**", handle_route)
        await page.goto("http://local.test/")
        await page.evaluate("fetch('http://local.test/api/v1/session')")
        await sniffer.wait_for_pending(timeout=1.0)
        await asyncio.sleep(0.1)
        await browser.close()

    captured = sniffer.captured_endpoints
    assert len(captured) == 1
    resp_headers = {k.lower(): v for k, v in captured[0].request.response_headers.items()}
    assert resp_headers.get("x-api-key") == "{{auth_token}}"
    assert resp_headers.get("x-auth-token") == "{{auth_token}}"
    assert resp_headers.get("x-custom") == "ok"

