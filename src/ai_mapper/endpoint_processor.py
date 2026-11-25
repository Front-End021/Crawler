"""AI-powered endpoint deduplication, renaming, and folder sorting using Google Gemini."""

import asyncio
import json
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from rich.console import Console

from src.config import Settings, get_settings
from src.models import CapturedRequest, CorrelatedEndpoint, MappedEndpoint

console = Console()


class EndpointEnrichment(BaseModel):
    """Enrichment metadata generated for a single endpoint."""
    endpoint_index: int = Field(description="Zero-based index of the endpoint from the batch input")
    clean_name: str = Field(description="Action-oriented, human-readable name for the Postman request")
    folder_tab: str = Field(description="Clean, standardized Postman folder name matching the UI tab")
    description: str = Field(description="Concise 1-2 sentence description of UI trigger and payload purpose")


class BatchEnrichmentResponse(BaseModel):
    """Structured response container for batched endpoint enrichment."""
    endpoints: List[EndpointEnrichment] = Field(description="List of enriched endpoints")


class EndpointProcessor:
    """Deduplicates captured requests and enriches them via Gemini or heuristic fallback."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings: Settings = settings or get_settings()

    def deduplicate(self, correlated_list: List[CorrelatedEndpoint]) -> List[MappedEndpoint]:
        """Group identical (Method + Normalized Path + GraphQL operationName) requests."""
        dedup_map: Dict[str, MappedEndpoint] = {}

        for item in correlated_list:
            req: CapturedRequest = item.request
            ctx = item.context
            domain = urlparse(req.url).netloc.lower()
            key = f"{domain}:{req.get_dedup_key()}"

            if key in dedup_map:
                existing = dedup_map[key]
                existing.occurrences += 1
                # Update with request body if previous was empty
                if not existing.body and (req.post_data or req.post_data_json is not None):
                    existing.body = req.post_data
                    existing.body_json = req.post_data_json
                if existing.response_status is None and req.response_status is not None:
                    existing.response_status = req.response_status
            else:
                # Initial mapping before AI enrichment
                mapped = MappedEndpoint(
                    clean_name=self._generate_heuristic_name(req, ctx.action_name),
                    folder_tab=ctx.tab_name.strip() or "General",
                    description=f"Triggered by '{ctx.action_name}' on tab '{ctx.tab_name}'.",
                    method=req.method,
                    url=req.url,
                    headers=req.headers,
                    query_params=req.query_params,
                    body=req.post_data,
                    body_json=req.post_data_json,
                    response_status=req.response_status,
                    graphql_operation_name=req.graphql_operation_name,
                    original_tab=ctx.tab_name,
                    original_action=ctx.action_name,
                    occurrences=1,
                )
                dedup_map[key] = mapped

        return list(dedup_map.values())

    async def process_and_enrich(
        self,
        correlated_list: List[CorrelatedEndpoint],
        batch_size: int = 15,
    ) -> List[MappedEndpoint]:
        """Deduplicate endpoints and enrich with Gemini structured output."""
        deduplicated = self.deduplicate(correlated_list)
        if not deduplicated:
            return []

        console.print(f"[cyan]🔄 Deduplicated {len(correlated_list)} captured requests into {len(deduplicated)} distinct endpoints.[/cyan]")

        # Check if Gemini API key is configured
        api_key = self.settings.gemini_api_key
        if not api_key:
            console.print("[yellow]ℹ️ GEMINI_API_KEY not configured. Using heuristic naming and tab sorting.[/yellow]")
            return deduplicated

        # Enrich in batches using Gemini structured outputs
        enriched_results: List[MappedEndpoint] = []
        for i in range(0, len(deduplicated), batch_size):
            batch = deduplicated[i : i + batch_size]
            enriched_batch = await self._enrich_batch_with_retry(batch, api_key=api_key)
            enriched_results.extend(enriched_batch)

        return enriched_results

    async def _enrich_batch_with_retry(
        self,
        batch: List[MappedEndpoint],
        api_key: str,
        max_retries: int = 3,
    ) -> List[MappedEndpoint]:
        """Call Gemini API with structured schema and exponential backoff on rate limits."""
        for attempt in range(max_retries):
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)

                # Prepare concise prompt payload
                prompt_items = []
                for idx, ep in enumerate(batch):
                    prompt_items.append({
                        "index": idx,
                        "method": ep.method,
                        "url": ep.url,
                        "tab": ep.original_tab,
                        "action": ep.original_action,
                        "is_graphql": bool(ep.graphql_operation_name),
                        "graphql_operation": ep.graphql_operation_name,
                        "status": ep.response_status,
                    })

                prompt_text = (
                    "You are an API Documentation and Postman Collection architect.\n"
                    "Rename each API endpoint into a professional, action-oriented Postman request name based on the UI action that triggered it.\n"
                    "Standardize the folder name to match the website Tab.\n"
                    "Provide a concise 1-2 sentence description explaining what UI interaction triggers this API call and what data it manages.\n"
                    "CRITICAL: Do NOT invent or alter any URLs or methods.\n\n"
                    f"Endpoints to enrich:\n{json.dumps(prompt_items, indent=2)}"
                )

                response = client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=prompt_text,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=BatchEnrichmentResponse,
                        temperature=0.2,
                    ),
                )

                parsed_response: Optional[BatchEnrichmentResponse] = None
                if hasattr(response, "parsed") and response.parsed:
                    parsed_response = response.parsed
                elif hasattr(response, "text") and response.text:
                    parsed_response = BatchEnrichmentResponse.model_validate_json(response.text)

                if parsed_response and parsed_response.endpoints:
                    index_map = {item.endpoint_index: item for item in parsed_response.endpoints}
                    for idx, ep in enumerate(batch):
                        if idx in index_map:
                            enrichment = index_map[idx]
                            if enrichment.clean_name and enrichment.clean_name.strip():
                                ep.clean_name = enrichment.clean_name.strip()
                            if enrichment.folder_tab and enrichment.folder_tab.strip():
                                ep.folder_tab = enrichment.folder_tab.strip()
                            if enrichment.description and enrichment.description.strip():
                                ep.description = enrichment.description.strip()
                    console.print(f"[bold green]✓ Gemini successfully enriched {len(batch)} endpoints.[/bold green]")
                    return batch

            except Exception as exc:
                error_str = str(exc)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "rate" in error_str.lower():
                    wait_time = (2 ** attempt) * 2
                    console.print(f"[yellow]⏳ Gemini rate limit encountered. Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...[/yellow]")
                    await asyncio.sleep(wait_time)
                else:
                    console.print(f"[yellow]⚠️ Gemini enrichment failed ({exc}). Falling back to heuristic names.[/yellow]")
                    break

        return batch

    def _generate_heuristic_name(self, req: CapturedRequest, action_name: str) -> str:
        """Construct descriptive action-oriented name without hallucinating URLs."""
        if req.graphql_operation_name:
            return req.graphql_operation_name

        clean_action = re.sub(r"^(Click|Navigate to|Execute|Search Filter:)\s*", "", action_name, flags=re.IGNORECASE).strip()
        generic_actions = {
            "ui interaction",
            "interaction",
            "initial page load",
            "initial navigation",
            "authentication",
        }
        is_tab_navigation = action_name.strip().lower().startswith("navigate to ")
        if clean_action and clean_action.lower() not in generic_actions and not is_tab_navigation:
            return clean_action

        # Fallback from URL path and method
        parsed = urlparse(req.url)
        segments = [s for s in parsed.path.split("/") if s and not s.isdigit() and s not in ("api", "v1", "v2", "v3")]
        path_desc = " ".join(segments[-2:]).title() if segments else "Resource"

        method_prefixes = {
            "GET": "Fetch",
            "POST": "Create",
            "PUT": "Update",
            "PATCH": "Modify",
            "DELETE": "Delete",
        }
        prefix = method_prefixes.get(req.method.upper(), req.method.upper())
        return f"{prefix} {path_desc}"
