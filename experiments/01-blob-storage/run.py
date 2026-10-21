"""Upload, download, verify, and remove a small blob in experiment-owned storage."""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

INFRA_DIR = Path(__file__).resolve().parent / "infra"
PAYLOAD = b"Hello from Azure projects experiment 01!\n"


@dataclass(frozen=True)
class Target:
    """Identify the sandbox resources recorded in this experiment's local state."""

    subscription_id: str
    resource_group_name: str
    location: str
    storage_account_url: str


def load_target() -> Target:
    """Read and validate OpenTofu outputs without loading the root .env."""
    result = subprocess.run(
        ["tofu", f"-chdir={INFRA_DIR}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        outputs = json.loads(result.stdout)
        values = {
            name: outputs[name]["value"]
            for name in (
                "subscription_id",
                "resource_group_name",
                "location",
                "storage_account_url",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-01-[a-z0-9-]+", target.resource_group_name):
            raise ValueError
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError
        if not re.fullmatch(
            r"https://azpg01[a-z0-9]{1,18}\.blob\.core\.windows\.net/?",
            target.storage_account_url,
        ):
            raise ValueError
    except ValueError, KeyError, TypeError:
        raise ValueError(
            "Invalid experiment outputs. Review local state and run make up."
        ) from None
    return target


def verify_target(target: Target) -> None:
    """Verify account ownership and region with a read-only Azure CLI request."""
    account = urlsplit(target.storage_account_url).netloc.split(".")[0]
    result = subprocess.run(
        [
            "az",
            "storage",
            "account",
            "show",
            "--subscription",
            target.subscription_id,
            "--resource-group",
            target.resource_group_name,
            "--name",
            account,
            "--query",
            "{location:location,endpoint:primaryEndpoints.blob}",
            "--output",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    metadata = json.loads(result.stdout)
    if (
        not isinstance(metadata, dict)
        or metadata.get("location") != target.location
        or not isinstance(metadata.get("endpoint"), str)
        or metadata.get("endpoint", "").rstrip("/") != target.storage_account_url.rstrip("/")
    ):
        raise ValueError("Azure account scope, region, or endpoint differs from experiment state.")


def round_trip(target: Target) -> None:
    """Use a unique private container and delete it only after successful creation."""
    name = f"azpg-01-{uuid4().hex}"
    print(f"Temporary container: {name}")
    with DefaultAzureCredential() as credential:
        with BlobServiceClient(
            account_url=target.storage_account_url,
            credential=credential,
            connection_timeout=10,
            read_timeout=30,
            retry_total=0,
        ) as service:
            with service.get_container_client(name) as container:
                try:
                    container.create_container()
                except AzureError:
                    print(
                        "Container creation failed. If the request timed out, inspect the named "
                        "container for leftover data; no existing container was deleted.",
                        file=sys.stderr,
                    )
                    raise
                try:
                    container.upload_blob(name="hello.txt", data=PAYLOAD, overwrite=False)
                    downloaded = container.download_blob("hello.txt").readall()
                    if downloaded != PAYLOAD:
                        raise ValueError("Downloaded content does not match the uploaded bytes.")
                    print("Blob round trip succeeded. Downloaded bytes match.")
                finally:
                    try:
                        container.delete_container()
                    except AzureError:
                        print(
                            f"Cleanup failed for {name}. Run make down to remove experiment "
                            "infrastructure and remaining data.",
                            file=sys.stderr,
                        )
                        raise
                    print("Temporary container removed.")


def main() -> int:
    """Confirm the verified target and report errors without exposing Azure responses."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Blob endpoint: {target.storage_account_url}")
        print("This uploads a small blob and removes its temporary container. Charges may apply.")
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No Blob data was changed.")
            return 1
        round_trip(target)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        print(
            "OpenTofu or Azure CLI failed. Check tool installation, make up, az login, "
            "Reader access, and the sandbox subscription.",
            file=sys.stderr,
        )
        return 1
    except AzureError:
        print(
            "Blob access failed. Check DefaultAzureCredential, Storage Blob Data Contributor, "
            "role propagation, and network rules. Run make down when finished.",
            file=sys.stderr,
        )
        return 1
    except EOFError, KeyboardInterrupt:
        print("Interrupted. Check cleanup output and run make down when finished.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
