"""Check local Azure configuration using read-only requests."""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_settings(env_file: Path) -> tuple[str, str, str]:
    """Load and validate settings, preferring .env values over the environment."""
    values = {**os.environ, **dotenv_values(env_file, interpolate=False)}
    names = ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_STORAGE_ACCOUNT_URL")
    settings = tuple((values.get(name) or "").strip() for name in names)
    for name, value in zip(names, settings, strict=True):
        if not value or "YOUR_" in value or "<" in value or ">" in value:
            raise ValueError(f"Set {name} to a real sandbox value in .env.")
    subscription, group, account_url = settings
    try:
        UUID(subscription)
    except ValueError:
        raise ValueError("AZURE_SUBSCRIPTION_ID must be a subscription UUID.") from None
    if group.startswith("-"):
        raise ValueError("AZURE_RESOURCE_GROUP must not start with a hyphen.")
    url = urlsplit(account_url)
    if (
        url.scheme != "https"
        or not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.[a-z0-9.-]+", url.netloc)
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "AZURE_STORAGE_ACCOUNT_URL must be an HTTPS Blob service URL without credentials."
        )
    return subscription, group, account_url.rstrip("/")


def check_access(subscription: str, group: str, account_url: str) -> None:
    """Verify the resource group, storage account ownership, and Blob data access."""
    scope = ["--subscription", subscription, "--resource-group", group]
    subprocess.run(
        ["az", "group", "show", *scope, "--output", "none"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    print("Subscription and resource group access succeeded.")
    account = urlsplit(account_url).netloc.split(".")[0]
    result = subprocess.run(
        [
            "az",
            "storage",
            "account",
            "show",
            *scope,
            "--name",
            account,
            "--query",
            "primaryEndpoints.blob",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.stdout.strip().rstrip("/") != account_url:
        raise ValueError(
            "Storage URL does not match the account in this subscription and resource group."
        )
    print("Storage account scope and endpoint verified.")
    with DefaultAzureCredential() as credential:
        with BlobServiceClient(
            account_url=account_url,
            credential=credential,
            connection_timeout=10,
            read_timeout=30,
            retry_total=0,
        ) as client:
            next(iter(client.list_containers(results_per_page=1).by_page()), None)
    print("Blob Storage authentication and container listing succeeded.")


def main(env_file: Path = ENV_FILE) -> int:
    """Report configuration or access failures without exposing Azure response details."""
    try:
        settings = load_settings(env_file)
        print("All three settings are valid. Starting read-only Azure checks.")
        check_access(*settings)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("Azure CLI is missing. Enter nix develop or install Azure CLI.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError, subprocess.TimeoutExpired:
        print(
            "Azure CLI check failed. Run make login, then check the subscription, resource "
            "group, storage account, Reader access, and network connectivity.",
            file=sys.stderr,
        )
        return 1
    except AzureError:
        print(
            "Blob access failed. Check DefaultAzureCredential authentication, Storage Blob "
            "Data Reader access, role propagation, and storage firewall or private endpoint rules.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
