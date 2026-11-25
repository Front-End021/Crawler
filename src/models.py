"""Pydantic data models for network requests, action contexts, and mapped endpoints."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator


class ActionContext(BaseModel):
    """Contextual state tracking the current UI tab and action being performed."""

    tab_name: str = Field(
        default="General / Landing",
        description="Name of the active tab, menu item, or navigation section",
    )
    action_name: str = Field(
        default="Initial Page Load",
        description="Description of the specific UI interaction executed",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the action was initiated",
    )


class CapturedRequest(BaseModel):
    """Normalized representation of an intercepted network request and response."""

    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for the captured request",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Capture timestamp",
    )
    method: str = Field(
        ...,
        description="HTTP method (GET, POST, PUT, DELETE, PATCH, etc.)",
    )
    url: str = Field(
        ...,
        description="Full request URL including query parameters",
    )
    resource_type: str = Field(
        default="fetch",
        description="Playwright resource type (typically xhr or fetch)",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Sanitized request headers with sensitive tokens masked",
    )
    query_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted URL query parameters",
    )
    post_data: Optional[str] = Field(
        default=None,
        description="Raw request payload",
    )
    post_data_json: Optional[Any] = Field(
        default=None,
        description="Parsed JSON payload if applicable",
    )
    response_status: Optional[int] = Field(
        default=None,
        description="HTTP response status code",
    )
    response_headers: Dict[str, str] = Field(
        default_factory=dict,
        description="HTTP response headers",
    )
    response_body_preview: Optional[str] = Field(
        default=None,
        description="Truncated preview of response body for AI mapping context",
    )
    is_graphql: bool = Field(
        default=False,
        description="Whether the request is a GraphQL query or mutation",
    )
    graphql_operation_name: Optional[str] = Field(
        default=None,
        description="Extracted GraphQL operation name if applicable",
    )

    @field_validator("method")
    @classmethod
    def normalize_method(cls, v: str) -> str:
        return v.upper().strip()

    def get_dedup_key(self) -> str:
        """Generate deduplication key based on method, normalized path, and GraphQL operation."""
        parsed = urlparse(self.url)
        path = parsed.path.rstrip("/") or "/"
        op = f":{self.graphql_operation_name}" if self.graphql_operation_name else ""
        return f"{self.method}:{path}{op}"


class CorrelatedEndpoint(BaseModel):
    """An intercepted API request bound to the UI context that triggered it."""

    correlation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for correlation record",
    )
    request: CapturedRequest = Field(
        ...,
        description="The captured network request",
    )
    context: ActionContext = Field(
        ...,
        description="The UI tab and action context active when the request fired",
    )


class MappedEndpoint(BaseModel):
    """AI-enriched API endpoint formatted for Postman Collection v2.1 export."""

    endpoint_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique endpoint identifier",
    )
    clean_name: str = Field(
        ...,
        description="Descriptive, human-readable action name (e.g., 'Fetch User Profile')",
    )
    folder_tab: str = Field(
        ...,
        description="Postman folder name corresponding to the UI Tab",
    )
    description: str = Field(
        ...,
        description="Detailed description of endpoint purpose and triggering action",
    )
    method: str = Field(
        ...,
        description="HTTP method",
    )
    url: str = Field(
        ...,
        description="Full request URL or templated Postman URL",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Request headers",
    )
    query_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Query parameters",
    )
    body: Optional[str] = Field(
        default=None,
        description="Request body",
    )
    body_json: Optional[Any] = Field(
        default=None,
        description="Parsed JSON body",
    )
    response_status: Optional[int] = Field(
        default=None,
        description="Observed HTTP response status code",
    )
    graphql_operation_name: Optional[str] = Field(
        default=None,
        description="GraphQL operation name if applicable",
    )
    original_tab: str = Field(
        ...,
        description="Original UI tab name captured during crawl",
    )
    original_action: str = Field(
        ...,
        description="Original UI action description captured during crawl",
    )
    occurrences: int = Field(
        default=1,
        description="Number of times this endpoint was triggered during exploration",
    )
