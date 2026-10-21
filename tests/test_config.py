"""Offline smoke tests for the Azure projects environment."""

import os
import socket
from pathlib import Path

import pytest
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv


@pytest.mark.unit
def test_blob_client_configuration() -> None:
    """Construct and close Azure clients without requesting tokens or making API calls."""
    account_url = "https://azprojects.blob.core.windows.net"
    with DefaultAzureCredential() as credential:
        with BlobServiceClient(account_url=account_url, credential=credential) as client:
            assert client.account_name == "azprojects"
            assert client.get_container_client("example").url == f"{account_url}/example"


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_network_blocked(method: str) -> None:
    """Reject socket connections before any local or remote service can be reached."""
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="must not open network"):
            getattr(connection, method)(("127.0.0.1", 9))


@pytest.mark.unit
def test_local_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Load local settings without replacing an existing environment variable."""
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "existing-subscription")
    monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
    configuration = tmp_path / ".env"
    configuration.write_text(
        "AZURE_SUBSCRIPTION_ID=local-subscription\nAZURE_RESOURCE_GROUP=azprojects-rg\n",
        encoding="utf-8",
    )

    assert load_dotenv(configuration)
    assert os.environ["AZURE_SUBSCRIPTION_ID"] == "existing-subscription"
    assert os.environ["AZURE_RESOURCE_GROUP"] == "azprojects-rg"
