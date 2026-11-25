"""End-to-end integration test verifying full CLI execution, pipeline, and schema compliance."""

from http.server import HTTPServer
import json
from pathlib import Path
import threading
from typer.testing import CliRunner

import pytest

from main import app
from tests.test_crawler import MultiTabMockServer


@pytest.fixture(scope="module")
def e2e_server():
    """Spin up local test web app for full end-to-end CLI validation."""
    server = HTTPServer(("127.0.0.1", 0), MultiTabMockServer)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()


def test_full_pipeline_cli_e2e(e2e_server: str, tmp_path: Path):
    """Execute complete CLI command against web app, assert console output, and validate generated JSON."""
    runner = CliRunner()
    output_dir = tmp_path / "postman_out"

    result = runner.invoke(
        app,
        [
            "--url",
            f"{e2e_server}/dashboard",
            "--max-steps",
            "15",
            "--headless",
            "--output-dir",
            str(output_dir),
        ],
    )

    # 1. Verify CLI output and execution summary table
    assert result.exit_code == 0
    assert "Autonomous AI Web Crawler & Postman Exporter" in result.output
    assert "Crawl & Export Pipeline Summary" in result.output
    assert "Postman Collection v2.1 Schema" in result.output
    assert "PASS (100% Compliant)" in result.output

    # 2. Verify Output File Exists
    expected_files = list(output_dir.glob("*_postman_collection.json"))
    assert len(expected_files) == 1
    collection_file = expected_files[0]
    assert collection_file.is_file()

    # 3. Read and validate structure of generated Postman collection
    with open(collection_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert len(data["variable"]) >= 2
    assert len(data["item"]) >= 2  # Folders for tabs

    # Verify requests inside folders have valid method and templated raw URL
    for folder in data["item"]:
        assert "name" in folder
        assert "item" in folder
        for req_item in folder["item"]:
            assert "name" in req_item
            assert "request" in req_item
            req = req_item["request"]
            assert req["method"] in ("GET", "POST", "PUT", "DELETE", "PATCH")
            assert req["url"]["raw"].startswith("{{baseUrl}}")
