"""Comprehensive unit and schema validation tests for PostmanExporter."""

import json
from pathlib import Path
import pytest
from src.exporter.postman_exporter import SCHEMA_URL, PostmanExporter
from src.models import MappedEndpoint


@pytest.fixture
def sample_mapped_endpoints():
    """Fixture providing diverse REST and GraphQL mapped endpoints."""
    return [
        MappedEndpoint(
            clean_name="Fetch Dashboard KPI Metrics",
            folder_tab="Dashboard",
            description="Returns system metrics and uptime percentages for dashboard widgets.",
            method="GET",
            url="https://api.example.com/v1/metrics?interval=daily&limit=30",
            headers={"Accept": "application/json", "Authorization": "{{auth_token}}"},
            query_params={"interval": "daily", "limit": 30},
            response_status=200,
            original_tab="Dashboard",
            original_action="Click View Metrics",
            occurrences=1,
        ),
        MappedEndpoint(
            clean_name="Search Employees Directory",
            folder_tab="User Management",
            description="Searches active employees by query term.",
            method="GET",
            url="https://api.example.com/v1/users?query=engineer&department=tech",
            headers={"Accept": "application/json", "Authorization": "{{auth_token}}"},
            query_params={"query": "engineer", "department": "tech"},
            response_status=200,
            original_tab="User Management",
            original_action="Search Filter: 'engineer'",
            occurrences=2,
        ),
        MappedEndpoint(
            clean_name="Create Employee Profile",
            folder_tab="User Management",
            description="Creates new employee profile record.",
            method="POST",
            url="https://api.example.com/v1/users",
            headers={"Content-Type": "application/json", "Authorization": "{{auth_token}}"},
            body_json={"name": "Alice Smith", "role": "Engineer", "email": "alice@example.com"},
            response_status=201,
            original_tab="User Management",
            original_action="Click Add User",
            occurrences=1,
        ),
        MappedEndpoint(
            clean_name="Query User Details (GraphQL)",
            folder_tab="Analytics",
            description="GraphQL query fetching detailed analytics and profile metrics.",
            method="POST",
            url="https://api.example.com/graphql",
            headers={"Content-Type": "application/json", "Authorization": "{{auth_token}}"},
            body_json={
                "operationName": "GetAnalyticsData",
                "query": "query GetAnalyticsData { analytics { views clicks } }",
            },
            graphql_operation_name="GetAnalyticsData",
            response_status=200,
            original_tab="Analytics",
            original_action="Fetch Profile Metrics",
            occurrences=1,
        ),
    ]


def test_build_collection_structure(sample_mapped_endpoints):
    """Verify Postman Collection v2.1 structure and fields."""
    exporter = PostmanExporter()
    collection = exporter.build_collection(
        mapped_endpoints=sample_mapped_endpoints,
        target_url="https://api.example.com",
        collection_name="Example App API",
    )

    # 1. Info Block
    assert "info" in collection
    info = collection["info"]
    assert info["name"] == "Example App API"
    assert info["schema"] == SCHEMA_URL
    assert "_postman_id" in info

    # 2. Variable Block
    assert "variable" in collection
    vars_map = {v["key"]: v["value"] for v in collection["variable"]}
    assert "baseUrl" in vars_map
    assert vars_map["baseUrl"] == "https://api.example.com"
    assert "auth_token" in vars_map

    # 3. Item (Folders)
    assert "item" in collection
    folder_names = [folder["name"] for folder in collection["item"]]
    assert "Analytics" in folder_names
    assert "Dashboard" in folder_names
    assert "User Management" in folder_names

    # Check request inside User Management folder
    user_folder = next(f for f in collection["item"] if f["name"] == "User Management")
    assert len(user_folder["item"]) == 2

    # Check POST request with JSON body
    create_req = next(i for i in user_folder["item"] if i["name"] == "Create Employee Profile")
    req = create_req["request"]
    assert req["method"] == "POST"
    assert req["body"]["mode"] == "raw"
    assert req["body"]["options"]["raw"]["language"] == "json"
    assert "Alice Smith" in req["body"]["raw"]

    # Check templated URL raw
    assert req["url"]["raw"].startswith("{{baseUrl}}")


def test_strict_postman_v2_1_schema_validation(sample_mapped_endpoints, tmp_path):
    """Validate generated collection against official Postman Collection v2.1.0 JSON Schema."""
    exporter = PostmanExporter()
    collection = exporter.build_collection(
        mapped_endpoints=sample_mapped_endpoints,
        target_url="https://api.example.com",
    )

    # Validate in-memory structure
    assert exporter.validate_schema(collection) is True

    # Validate exported JSON file on disk
    file_path = exporter.export_to_file(
        mapped_endpoints=sample_mapped_endpoints,
        target_url="https://api.example.com",
        output_dir=tmp_path,
        filename="test_collection.json",
    )
    assert file_path.exists()

    with open(file_path, "r", encoding="utf-8") as f:
        disk_data = json.load(f)

    assert exporter.validate_schema(disk_data) is True


def test_third_party_host_port_and_auth_cookie_variable():
    """Regression: third-party URLs preserve actual host/port, and auth_cookie variable is declared."""
    endpoints = [
        MappedEndpoint(
            clean_name="Third Party Config",
            folder_tab="External",
            description="External config call",
            method="GET",
            url="https://cdn-api.otherdomain.io:8443/v2/config",
            headers={"Cookie": "{{auth_cookie}}"},
            original_tab="External",
            original_action="Initial Navigation",
        ),
        MappedEndpoint(
            clean_name="Prefix Spoofed Domain",
            folder_tab="External",
            description="Domain sharing prefix with target origin",
            method="GET",
            url="https://api.example.com.evil.test/v1/leak",
            original_tab="External",
            original_action="Initial Navigation",
        ),
    ]
    exporter = PostmanExporter()
    collection = exporter.build_collection(endpoints, target_url="https://api.example.com")
    assert exporter.validate_schema(collection) is True

    var_keys = {v["key"] for v in collection["variable"]}
    assert "auth_cookie" in var_keys

    items = collection["item"][0]["item"]
    third_party = next(i for i in items if i["name"] == "Third Party Config")["request"]["url"]
    assert third_party["raw"] == "https://cdn-api.otherdomain.io:8443/v2/config"
    assert third_party["host"] == ["cdn-api", "otherdomain", "io"]
    assert third_party["port"] == "8443"

    spoofed = next(i for i in items if i["name"] == "Prefix Spoofed Domain")["request"]["url"]
    assert spoofed["raw"] == "https://api.example.com.evil.test/v1/leak"
    assert spoofed["host"] == ["api", "example", "com", "evil", "test"]
