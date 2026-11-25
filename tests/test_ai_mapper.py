"""Comprehensive test suite for EndpointProcessor, deduplication, and Gemini enrichment."""

from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai_mapper.endpoint_processor import (
    BatchEnrichmentResponse,
    EndpointEnrichment,
    EndpointProcessor,
)
from src.config import Settings
from src.models import ActionContext, CapturedRequest, CorrelatedEndpoint, MappedEndpoint


# ==========================================
# Helpers & Fixtures
# ==========================================

def create_sample_correlated(
    method: str,
    url: str,
    tab: str,
    action: str,
    is_graphql: bool = False,
    graphql_op: str = None,
    status: int = 200,
) -> CorrelatedEndpoint:
    req = CapturedRequest(
        method=method,
        url=url,
        resource_type="fetch",
        is_graphql=is_graphql,
        graphql_operation_name=graphql_op,
        response_status=status,
    )
    ctx = ActionContext(tab_name=tab, action_name=action)
    return CorrelatedEndpoint(request=req, context=ctx)


# ==========================================
# Deduplication Tests
# ==========================================

def test_deduplicate_repeated_pagination_calls():
    """Verify that repeated polling or pagination requests are merged into one entry with occurrences count."""
    processor = EndpointProcessor()

    calls = [
        create_sample_correlated("GET", "https://example.com/api/v1/users?page=1", "Users", "Search Filter: 'John'"),
        create_sample_correlated("GET", "https://example.com/api/v1/users?page=2", "Users", "Click Next Page"),
        create_sample_correlated("GET", "https://example.com/api/v1/users?page=3", "Users", "Click Next Page"),
    ]

    deduped = processor.deduplicate(calls)
    assert len(deduped) == 1
    assert deduped[0].occurrences == 3
    assert deduped[0].method == "GET"
    assert "/api/v1/users" in deduped[0].url
    assert deduped[0].folder_tab == "Users"


def test_deduplicate_distinct_methods_and_paths():
    """Verify distinct HTTP methods and paths remain separate."""
    processor = EndpointProcessor()

    calls = [
        create_sample_correlated("GET", "https://example.com/api/v1/users", "Users", "Load Users"),
        create_sample_correlated("POST", "https://example.com/api/v1/users", "Users", "Create User"),
        create_sample_correlated("GET", "https://example.com/api/v1/billing", "Billing", "View Billing"),
    ]

    deduped = processor.deduplicate(calls)
    assert len(deduped) == 3
    methods = [e.method for e in deduped]
    assert methods.count("GET") == 2
    assert methods.count("POST") == 1


def test_deduplicate_graphql_by_operation_name():
    """Verify identical /graphql URLs are separated by operationName."""
    processor = EndpointProcessor()

    calls = [
        create_sample_correlated("POST", "https://example.com/graphql", "Dashboard", "View Metrics", is_graphql=True, graphql_op="GetMetrics"),
        create_sample_correlated("POST", "https://example.com/graphql", "Dashboard", "View Metrics Again", is_graphql=True, graphql_op="GetMetrics"),
        create_sample_correlated("POST", "https://example.com/graphql", "Settings", "Update Profile", is_graphql=True, graphql_op="UpdateProfile"),
    ]

    deduped = processor.deduplicate(calls)
    assert len(deduped) == 2
    ops = [e.graphql_operation_name for e in deduped]
    assert "GetMetrics" in ops
    assert "UpdateProfile" in ops
    get_metrics_ep = next(e for e in deduped if e.graphql_operation_name == "GetMetrics")
    assert get_metrics_ep.occurrences == 2


# ==========================================
# Heuristic Naming Fallback Tests
# ==========================================

def test_heuristic_name_generation():
    """Verify sensible, non-hallucinated fallback names from action and URL."""
    processor = EndpointProcessor()

    # 1. Action-derived name
    req1 = CapturedRequest(method="GET", url="https://example.com/api/v1/employees")
    name1 = processor._generate_heuristic_name(req1, "Click View Employees")
    assert name1 == "View Employees"

    # 2. GraphQL operation name
    req2 = CapturedRequest(method="POST", url="https://example.com/graphql", is_graphql=True, graphql_operation_name="GetUserBilling")
    name2 = processor._generate_heuristic_name(req2, "UI Interaction")
    assert name2 == "GetUserBilling"

    # 3. Path & method derived name when action is generic
    req3 = CapturedRequest(method="POST", url="https://example.com/api/v2/orders/checkout")
    name3 = processor._generate_heuristic_name(req3, "Initial Page Load")
    assert name3 == "Create Orders Checkout"


# ==========================================
# Gemini Structured Output & Retry Tests
# ==========================================

def test_batch_enrichment_response_schema():
    """Verify Pydantic schema validation for Gemini structured output."""
    raw_json = """
    {
        "endpoints": [
            {
                "endpoint_index": 0,
                "clean_name": "Fetch Dashboard Metrics",
                "folder_tab": "Dashboard",
                "description": "Triggered when viewing the main dashboard. Retrieves system performance KPIs."
            },
            {
                "endpoint_index": 1,
                "clean_name": "Search Active Employees",
                "folder_tab": "User Management",
                "description": "Triggered when user types in search bar. Returns matching user profiles."
            }
        ]
    }
    """
    parsed = BatchEnrichmentResponse.model_validate_json(raw_json)
    assert len(parsed.endpoints) == 2
    assert parsed.endpoints[0].clean_name == "Fetch Dashboard Metrics"
    assert parsed.endpoints[0].folder_tab == "Dashboard"
    assert parsed.endpoints[1].endpoint_index == 1


@pytest.mark.asyncio
async def test_enrichment_with_mocked_gemini():
    """Verify that Gemini enrichment enriches names/folders while keeping URLs and methods 100% unaltered."""
    processor = EndpointProcessor(settings=Settings(gemini_api_key="mock-api-key"))

    calls = [
        create_sample_correlated("GET", "https://example.com/api/v1/metrics", "Home", "Click View Metrics"),
        create_sample_correlated("POST", "https://example.com/api/v1/users", "Admin", "Click Add User"),
    ]

    mock_gemini_response = MagicMock()
    mock_gemini_response.parsed = BatchEnrichmentResponse(
        endpoints=[
            EndpointEnrichment(
                endpoint_index=0,
                clean_name="Get Dashboard Performance Metrics",
                folder_tab="Executive Dashboard",
                description="Fetches high-level metrics for dashboard cards.",
            ),
            EndpointEnrichment(
                endpoint_index=1,
                clean_name="Create New User Account",
                folder_tab="User Administration",
                description="Submits new user profile registration.",
            ),
        ]
    )

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_gemini_response

        results = await processor.process_and_enrich(calls)

        assert len(results) == 2

        # Verification: Clean name and folder updated
        assert results[0].clean_name == "Get Dashboard Performance Metrics"
        assert results[0].folder_tab == "Executive Dashboard"
        assert results[0].description == "Fetches high-level metrics for dashboard cards."

        # Verification: Zero hallucination - URLs and methods remain exact
        assert results[0].url == "https://example.com/api/v1/metrics"
        assert results[0].method == "GET"

        assert results[1].clean_name == "Create New User Account"
        assert results[1].folder_tab == "User Administration"
        assert results[1].url == "https://example.com/api/v1/users"
        assert results[1].method == "POST"


@pytest.mark.asyncio
async def test_enrichment_rate_limit_retry():
    """Verify exponential backoff retry when encountering 429 RESOURCE_EXHAUSTED."""
    processor = EndpointProcessor(settings=Settings(gemini_api_key="mock-api-key"))

    calls = [
        create_sample_correlated("GET", "https://example.com/api/v1/test", "TestTab", "TestAction"),
    ]

    mock_gemini_response = MagicMock()
    mock_gemini_response.parsed = BatchEnrichmentResponse(
        endpoints=[
            EndpointEnrichment(
                endpoint_index=0,
                clean_name="Enriched After Retry",
                folder_tab="TestTab",
                description="Success after 429 backoff.",
            )
        ]
    )

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # First call raises rate limit error, second succeeds
        mock_client.models.generate_content.side_effect = [
            Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded"),
            mock_gemini_response,
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            results = await processor.process_and_enrich(calls)

            # Verified retry happened
            assert mock_client.models.generate_content.call_count == 2
            assert mock_sleep.call_count == 1
            assert results[0].clean_name == "Enriched After Retry"


def test_deduplicate_cross_domain_and_body_merge():
    """Regression: distinct domains do not collide, and post_data merges without response_body_preview."""
    processor = EndpointProcessor()
    c1 = create_sample_correlated("GET", "https://api.first.com/v1/users", "Users", "Load Users")
    c2 = create_sample_correlated("GET", "https://api.second.com/v1/users", "Users", "Load Users")

    c3 = CorrelatedEndpoint(
        request=CapturedRequest(method="POST", url="https://api.first.com/v1/items", post_data=None, response_body_preview=None),
        context=ActionContext(tab_name="Items", action_name="Click Save"),
    )
    c4 = CorrelatedEndpoint(
        request=CapturedRequest(
            method="POST",
            url="https://api.first.com/v1/items",
            post_data='{"name":"Widget"}',
            post_data_json={"name": "Widget"},
            response_body_preview=None,
        ),
        context=ActionContext(tab_name="Items", action_name="Click Save"),
    )

    deduped = processor.deduplicate([c1, c2, c3, c4])
    assert len(deduped) == 3
    post_ep = next(ep for ep in deduped if ep.method == "POST")
    assert post_ep.body == '{"name":"Widget"}'
    assert post_ep.body_json == {"name": "Widget"}


def test_heuristic_name_skips_generic_navigation_and_auth_labels():
    """Regression: generic Navigation/Auth action strings fall back to descriptive URL-based names."""
    processor = EndpointProcessor()
    r1 = CapturedRequest(method="GET", url="https://example.com/api/v2/admin/users")
    r2 = CapturedRequest(method="GET", url="https://example.com/api/v2/dashboard/shortcuts")
    r3 = CapturedRequest(method="GET", url="https://example.com/web/core/i18n/messages")

    assert processor._generate_heuristic_name(r1, "Navigate to Admin") == "Fetch Admin Users"
    assert processor._generate_heuristic_name(r2, "Authentication") == "Fetch Dashboard Shortcuts"
    assert processor._generate_heuristic_name(r3, "Initial Navigation") == "Fetch I18N Messages"
