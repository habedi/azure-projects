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
