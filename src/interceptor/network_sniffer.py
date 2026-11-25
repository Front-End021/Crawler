"""Playwright Network Sniffer for intercepting and correlating API traffic."""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

from playwright.async_api import BrowserContext, Page, Request, Response

from src.config import Settings, get_settings
from src.interceptor.context_tracker import ContextTracker
from src.models import ActionContext, CapturedRequest, CorrelatedEndpoint

GRAPHQL_OPERATION_REGEX = re.compile(r"(?:query|mutation|subscription)\s+([A-Za-z0-9_]+)", re.IGNORECASE)


class NetworkSniffer:
    """Intercepts, sanitizes, and correlates API traffic from Playwright sessions."""

    def __init__(
        self,
        context_tracker: Optional[ContextTracker] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings: Settings = settings or get_settings()
        self.context_tracker: ContextTracker = context_tracker or ContextTracker()
        self._correlated_endpoints: List[CorrelatedEndpoint] = []
        self._pending_requests: Dict[Request, CorrelatedEndpoint] = {}
        self._attached = False
        self._attached_target_ids: set[int] = set()

    @property
    def captured_endpoints(self) -> List[CorrelatedEndpoint]:
        """Return all successfully captured and correlated endpoints."""
        return list(self._correlated_endpoints)

    def attach(self, target: Union[Page, BrowserContext]) -> None:
        """Attach request/response event listeners to a Playwright Page or BrowserContext."""
        target_id = id(target)
        if target_id in self._attached_target_ids:
            return
        target.on("request", self._handle_request)
        target.on("response", self._handle_response)
        target.on("requestfailed", self._handle_request_failed)
        self._attached_target_ids.add(target_id)
        self._attached = True

    def is_api_request(self, resource_type: str, url: str) -> bool:
        """Strictly determine if a request represents backend API traffic."""
        # Must be XHR or Fetch
        if resource_type.lower() not in ("xhr", "fetch"):
            return False

        # Exclude static assets and telemetry domains
        if self.settings.is_ignored_resource(resource_type, url):
            return False

        return True

    def _parse_query_params(self, url: str) -> Dict[str, Any]:
        """Extract URL query parameters into flat or list values."""
        parsed = urlparse(url)
        raw_qs = parse_qs(parsed.query, keep_blank_values=True)
        # Flatten single-item lists for cleaner representation
        flattened: Dict[str, Any] = {}
        for k, v in raw_qs.items():
            flattened[k] = v[0] if len(v) == 1 else v
        return flattened

    def _parse_graphql(
        self, url: str, post_data_json: Optional[Any], post_data_raw: Optional[str]
    ) -> tuple[bool, Optional[str]]:
        """Detect GraphQL requests and extract operationName."""
        is_gql = "/graphql" in url.lower()
        operation_name: Optional[str] = None

        if isinstance(post_data_json, dict):
            if "query" in post_data_json or "operationName" in post_data_json:
                is_gql = True
                op_name = post_data_json.get("operationName")
                if op_name and isinstance(op_name, str) and op_name.strip():
                    operation_name = op_name.strip()
                elif "query" in post_data_json and isinstance(post_data_json["query"], str):
                    match = GRAPHQL_OPERATION_REGEX.search(post_data_json["query"])
                    if match:
                        operation_name = match.group(1)

        elif post_data_raw and is_gql:
            match = GRAPHQL_OPERATION_REGEX.search(post_data_raw)
            if match:
                operation_name = match.group(1)

        if is_gql and not operation_name:
            operation_name = "AnonymousGraphQL"

        return is_gql, operation_name

    def _handle_request(self, request: Request) -> None:
        """Handle Playwright request event synchronously."""
        try:
            resource_type = request.resource_type
            url = request.url

            if not self.is_api_request(resource_type, url):
                return

            # Sanitize request headers
            headers = self.settings.sanitize_headers(request.headers)

            # Query params
            query_params = self._parse_query_params(url)

            # Parse post data
            post_data_raw = request.post_data
            post_data_json = None
            if post_data_raw:
                try:
                    post_data_json = json.loads(post_data_raw)
                except Exception:
                    post_data_json = None

            # Detect GraphQL
            is_graphql, graphql_op = self._parse_graphql(url, post_data_json, post_data_raw)

            # Snapshot active UI context
            current_context = self.context_tracker.get_current_context()

            captured_req = CapturedRequest(
                method=request.method,
                url=url,
                resource_type=resource_type,
                headers=headers,
                query_params=query_params,
                post_data=post_data_raw,
                post_data_json=post_data_json,
                is_graphql=is_graphql,
                graphql_operation_name=graphql_op,
            )

            correlated = CorrelatedEndpoint(
                request=captured_req,
                context=current_context,
            )

            self._pending_requests[request] = correlated

        except Exception:
            # Defensive logging/silence to prevent sniffer crashes from breaking crawler
            pass

    async def _handle_response(self, response: Response) -> None:
        """Handle Playwright response event asynchronously to capture body preview."""
        request = response.request
        correlated = self._pending_requests.pop(request, None)
        if not correlated:
            return

        try:
            # Update response status and sanitized response headers
            correlated.request.response_status = response.status
            try:
                raw_resp_headers = await response.all_headers()
            except Exception:
                raw_resp_headers = dict(response.headers)
            correlated.request.response_headers = self.settings.sanitize_headers(raw_resp_headers)

            # Attempt safe text extraction
            body_preview: Optional[str] = None
            try:
                body_text = await response.text()
                if body_text:
                    max_chars = self.settings.response_preview_max_chars
                    body_preview = body_text[:max_chars]
            except Exception:
                body_preview = None

            correlated.request.response_body_preview = body_preview

            # Append to correlated endpoints
            self._correlated_endpoints.append(correlated)

        except Exception:
            # Still record with available data if response body fails
            self._correlated_endpoints.append(correlated)

    def _handle_request_failed(self, request: Request) -> None:
        """Handle request failure (network drop, abort, CORS error)."""
        correlated = self._pending_requests.pop(request, None)
        if correlated:
            correlated.request.response_status = None
            self._correlated_endpoints.append(correlated)

    async def wait_for_pending(self, timeout: float = 2.0) -> None:
        """Wait briefly for in-flight response body captures to finish."""
        elapsed = 0.0
        step = 0.05
        while self._pending_requests and elapsed < timeout:
            await asyncio.sleep(step)
            elapsed += step

    def clear(self) -> None:
        """Clear all pending and captured requests."""
        self._pending_requests.clear()
        self._correlated_endpoints.clear()
