"""Check infrastructure policies without Azure access or provider downloads."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rag_keyless_permissions() -> None:
    """Keep index management and document access scoped to the search service."""
    source = (ROOT / "projects/04-rag-ai-search/infra/main.tf").read_text()
    roles = {}
    for block in re.findall(r'resource "azurerm_role_assignment" "[^"]+" \{([^}]+)\}', source):
        fields = dict(re.findall(r"(\w+)\s*=\s*([^\n]+)", block))
        roles[fields["role_definition_name"].strip('"')] = fields
    for role in ("Search Service Contributor", "Search Index Data Contributor"):
        assert roles[role]["scope"] == "azurerm_search_service.experiment.id"
        assert roles[role]["principal_id"] == "var.principal_object_id"


def test_rag_search_tier_supports_keyless_access() -> None:
    """Prevent the unsupported Free tier from returning to this keyless experiment."""
    source = (ROOT / "projects/04-rag-ai-search/infra/main.tf").read_text()
    service = re.search(r'resource "azurerm_search_service" "experiment" \{(.*?)\n\}', source, re.S)
    assert service is not None
    assert re.search(r'\bsku\s*=\s*"basic"', service[1])
    variables = (ROOT / "projects/04-rag-ai-search/infra/variables.tf").read_text()
    example = (ROOT / "projects/04-rag-ai-search/infra/terraform.tfvars.example").read_text()
    assert "search_service_sku" not in variables + example


def test_cdc_serverless_cosmos_configuration() -> None:
    """Ensure Cosmos DB uses Serverless capacity to prevent fixed hourly costs."""
    source = (ROOT / "projects/05-change-data-capture/infra/main.tf").read_text()
    pattern = r'resource "azurerm_cosmosdb_account" "experiment" \{(.*?)\n\}'
    account = re.search(pattern, source, re.S)
    assert account is not None
    assert re.search(r'capabilities\s*\{\s*name\s*=\s*"EnableServerless"\s*\}', account[1])


def test_cdc_keyless_permissions() -> None:
    """Ensure Cosmos DB, storage tables, blobs, and OpenAI use keyless Entra permissions."""
    source = (ROOT / "projects/05-change-data-capture/infra/main.tf").read_text()
    roles = {}
    for block in re.findall(r'resource "azurerm_role_assignment" "[^"]+" \{([^}]+)\}', source):
        fields = dict(re.findall(r"(\w+)\s*=\s*([^\n]+)", block))
        roles[fields["role_definition_name"].strip('"')] = fields

    assert roles["Storage Table Data Contributor"]["principal_id"] == "var.principal_object_id"
    assert roles["Storage Blob Data Contributor"]["principal_id"] == "var.principal_object_id"
    assert roles["Cognitive Services OpenAI User"]["principal_id"] == "var.principal_object_id"

    sql_role = re.search(
        r'resource "azurerm_cosmosdb_sql_role_assignment" "contributor" \{(.*?)\n\}', source, re.S
    )
    assert sql_role is not None
    assert re.search(r"principal_id\s*=\s*var\.principal_object_id", sql_role[1])


def test_docintel_keyless_permissions() -> None:
    """Ensure Document Intelligence and storage blobs use keyless Entra permissions."""
    source = (ROOT / "projects/06-document-processing/infra/main.tf").read_text()
    roles = {}
    for block in re.findall(r'resource "azurerm_role_assignment" "[^"]+" \{([^}]+)\}', source):
        fields = dict(re.findall(r"(\w+)\s*=\s*([^\n]+)", block))
        roles[fields["role_definition_name"].strip('"')] = fields

    assert roles["Cognitive Services User"]["principal_id"] == "var.principal_object_id"
    assert roles["Storage Blob Data Contributor"]["principal_id"] == "var.principal_object_id"


def test_docintel_service_configuration() -> None:
    """Verify Document Intelligence account kind and tier."""
    source = (ROOT / "projects/06-document-processing/infra/main.tf").read_text()
    pattern = r'resource "azurerm_cognitive_account" "experiment" \{(.*?)\n\}'
    account = re.search(pattern, source, re.S)
    assert account is not None
    assert re.search(r'kind\s*=\s*"FormRecognizer"', account[1])
    assert re.search(r'sku_name\s*=\s*"S0"', account[1])


def test_provider_registrations_disabled() -> None:
    """Opt out of automated resource provider registration across all experiments."""
    for path in ROOT.glob("projects/*/infra/main.tf"):
        source = path.read_text()
        assert re.search(r'resource_provider_registrations\s*=\s*"none"', source), (
            f"Missing resource_provider_registrations in {path}"
        )
