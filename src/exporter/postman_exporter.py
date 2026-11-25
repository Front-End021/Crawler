"""Postman Collection Format v2.1.0 exporter and JSON schema validator."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

import jsonschema
from rich.console import Console

from src.models import MappedEndpoint

console = Console()

SCHEMA_URL = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


class PostmanExporter:
    """Exports deduplicated and enriched endpoints to Postman Collection v2.1.0 format."""

    def build_collection(
        self,
        mapped_endpoints: List[MappedEndpoint],
        target_url: str,
        collection_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Construct strictly schema-compliant Postman Collection v2.1.0 data structure."""
        parsed_target = urlparse(target_url)
        origin = f"{parsed_target.scheme}://{parsed_target.netloc}"
        domain = parsed_target.netloc or "Target"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        name = collection_name or f"{domain} API Collection"

        # 1. Collection Info
        collection = {
            "info": {
                "_postman_id": str(uuid4()),
                "name": name,
                "description": f"Autonomously crawled and AI-enriched Postman Collection for {origin}. Intercepted on {timestamp} UTC.",
                "schema": SCHEMA_URL,
            },
            "variable": [
                {
                    "key": "baseUrl",
                    "value": origin,
                    "type": "string",
                },
                {
                    "key": "auth_token",
                    "value": "",
                    "type": "string",
                },
                {
                    "key": "auth_cookie",
                    "value": "",
                    "type": "string",
                },
            ],
            "item": [],
        }

        # 2. Group endpoints by folder_tab
        folders: Dict[str, List[Dict[str, Any]]] = {}
        for ep in mapped_endpoints:
            folder_name = ep.folder_tab.strip() or "General"
            if folder_name not in folders:
                folders[folder_name] = []

            postman_item = self._build_request_item(ep, origin)
            folders[folder_name].append(postman_item)

        # 3. Create sorted folder hierarchy
        for folder_name in sorted(folders.keys()):
            collection["item"].append({
                "name": folder_name,
                "item": folders[folder_name],
            })

        return collection

    def _build_request_item(self, ep: MappedEndpoint, origin: str) -> Dict[str, Any]:
        """Construct individual Postman request item with structured URL, headers, and body."""
        parsed_url = urlparse(ep.url)

        # Build templated raw URL with {{baseUrl}} only for exact same-origin URLs
        is_same_origin = (
            ep.url == origin
            or ep.url.startswith(origin + "/")
            or ep.url.startswith(origin + "?")
        )
        if is_same_origin:
            rel_url = ep.url[len(origin):]
            raw_url = f"{{{{baseUrl}}}}{rel_url}"
            host_parts = ["{{baseUrl}}"]
        else:
            raw_url = ep.url
            hostname = parsed_url.hostname or ""
            host_parts = hostname.split(".") if hostname else ([parsed_url.netloc] if parsed_url.netloc else ["{{baseUrl}}"])

        # Path segments
        path_segments = [s for s in parsed_url.path.split("/") if s]

        # Query items
        query_items = []
        for k, v in ep.query_params.items():
            if isinstance(v, list):
                for sub_v in v:
                    query_items.append({"key": k, "value": str(sub_v)})
            else:
                query_items.append({"key": k, "value": str(v)})

        # Headers
        headers = []
        for k, v in ep.headers.items():
            headers.append({
                "key": k,
                "value": str(v),
                "type": "text",
            })

        url_obj: Dict[str, Any] = {
            "raw": raw_url,
            "protocol": parsed_url.scheme or "https",
            "host": host_parts,
            "path": path_segments,
            "query": query_items,
        }
        if not is_same_origin and parsed_url.port:
            url_obj["port"] = str(parsed_url.port)

        # Request specification
        request_obj: Dict[str, Any] = {
            "method": ep.method.upper(),
            "header": headers,
            "url": url_obj,
            "description": ep.description,
        }

        # Request Body (if payload exists or mutating method)
        if ep.body or ep.body_json or ep.method.upper() in ("POST", "PUT", "PATCH"):
            raw_body = ""
            is_json = False
            if ep.body_json:
                raw_body = json.dumps(ep.body_json, indent=2)
                is_json = True
            elif ep.body:
                raw_body = str(ep.body)
                try:
                    json.loads(raw_body)
                    is_json = True
                except Exception:
                    is_json = False

            request_obj["body"] = {
                "mode": "raw",
                "raw": raw_body,
                "options": {
                    "raw": {
                        "language": "json" if is_json else "text"
                    }
                },
            }

        return {
            "name": ep.clean_name,
            "request": request_obj,
            "response": [],
        }

    def export_to_file(
        self,
        mapped_endpoints: List[MappedEndpoint],
        target_url: str,
        output_dir: Path,
        collection_name: Optional[str] = None,
        filename: Optional[str] = None,
        collection_data: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Serialize Postman Collection to disk and return destination Path."""
        collection_dict = collection_data or self.build_collection(
            mapped_endpoints=mapped_endpoints,
            target_url=target_url,
            collection_name=collection_name,
        )

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not filename:
            parsed = urlparse(target_url)
            clean_host = re.sub(r"[^\w\-_.]", "_", parsed.netloc or "api")
            filename = f"{clean_host}_postman_collection.json"

        file_path = output_dir / filename
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(collection_dict, f, indent=2, ensure_ascii=False)

        console.print(f"[bold green]✓ Postman Collection saved to:[/bold green] {file_path}")
        return file_path

    def validate_schema(self, collection_data: Dict[str, Any], schema_path: Optional[Path] = None) -> bool:
        """Validate collection data against Postman Collection v2.1.0 JSON Schema."""
        default_schema_path = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "postman_v2_1_schema.json"
        target_schema = schema_path or default_schema_path

        if not target_schema.exists():
            console.print("[yellow]⚠️ Postman v2.1 schema file not found locally. Skipping strict schema validation.[/yellow]")
            return True

        with open(target_schema, "r", encoding="utf-8") as f:
            schema = json.load(f)

        try:
            jsonschema.validate(instance=collection_data, schema=schema)
            return True
        except jsonschema.ValidationError as err:
            console.print(f"[bold red]Schema Validation Error:[/bold red] {err.message}", style="red")
            return False
