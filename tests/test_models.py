"""Unit tests for Pydantic data models and serialization."""

from datetime import datetime
from src.models import ActionContext, CapturedRequest, CorrelatedEndpoint, MappedEndpoint


def test_action_context_defaults():
    """Verify default ActionContext creation."""
    ctx = ActionContext()
    assert ctx.tab_name == "General / Landing"
    assert ctx.action_name == "Initial Page Load"
    assert isinstance(ctx.timestamp, datetime)


def test_captured_request_creation_and_normalization():
    """Verify CapturedRequest normalization, serialization, and dedup key."""
    req = CapturedRequest(
        method="get",
        url="https://api.example.com/v1/users?limit=10&page=1",
        resource_type="fetch",
        headers={"Content-Type": "application/json"},
        query_params={"limit": 10, "page": 1},
        response_status=200,
        response_body_preview='[{"id": 1, "name": "Alice"}]',
    )
    assert req.method == "GET"  # Normalized to uppercase
    assert req.response_status == 200
    assert req.is_graphql is False
    assert req.graphql_operation_name is None
    assert req.get_dedup_key() == "GET:/v1/users"


def test_graphql_request_dedup_key():
    """Verify GraphQL specific deduplication key handling."""
    req = CapturedRequest(
        method="POST",
        url="https://api.example.com/graphql",
        resource_type="fetch",
        is_graphql=True,
        graphql_operation_name="GetUserProfile",
    )
    assert req.get_dedup_key() == "POST:/graphql:GetUserProfile"


def test_correlated_endpoint():
    """Verify correlation between CapturedRequest and ActionContext."""
    ctx = ActionContext(tab_name="Employees", action_name="Click Sort by Name")
    req = CapturedRequest(
        method="GET",
        url="https://api.example.com/employees?sort=name",
    )
    correlated = CorrelatedEndpoint(request=req, context=ctx)
    assert correlated.request.url == "https://api.example.com/employees?sort=name"
    assert correlated.context.tab_name == "Employees"
    assert correlated.context.action_name == "Click Sort by Name"
    assert correlated.correlation_id is not None


def test_mapped_endpoint():
    """Verify MappedEndpoint data structure for Postman export."""
    endpoint = MappedEndpoint(
        clean_name="Search Employees by Name",
        folder_tab="Employees",
        description="Retrieves a list of employees filtered or sorted by name.",
        method="GET",
        url="https://api.example.com/employees?sort=name",
        original_tab="Employees",
        original_action="Click Sort by Name",
        occurrences=3,
        response_status=200,
    )
    assert endpoint.clean_name == "Search Employees by Name"
    assert endpoint.folder_tab == "Employees"
    assert endpoint.occurrences == 3
    assert endpoint.response_status == 200
