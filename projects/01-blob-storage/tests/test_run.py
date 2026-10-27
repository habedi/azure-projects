"""Test the Blob experiment without credentials, network requests, or cloud resources."""

import importlib
import json
import subprocess
from dataclasses import asdict
from io import StringIO
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.01-blob-storage.run")
TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-01-test",
    location="northeurope",
    storage_account_url="https://azpg01test.blob.core.windows.net/",
)


@pytest.fixture(autouse=True)
def sandbox(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read local CLI output and confirmation while using the real Blob SDK."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))


def test_success(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Transfer actual bytes through the SDK and preserve unrelated local containers."""
    offline.transport.containers["unrelated"] = {"keep.txt": b"keep me"}
    assert experiment.main() == 0
    assert offline.cli.calls[0] == [
        "tofu",
        f"-chdir={experiment.INFRA_DIR}",
        "output",
        "-json",
    ]
    args = offline.cli.calls[1]
    assert args[args.index("--subscription") + 1] == TARGET.subscription_id
    assert args[args.index("--resource-group") + 1] == TARGET.resource_group_name
    assert args[args.index("--name") + 1] == "azpg01test"
    create, upload, download, delete = offline.transport.requests
    assert [request.method for request in (create, upload, download, delete)] == [
        "PUT",
        "PUT",
        "GET",
        "DELETE",
    ]
    name = urlsplit(create.url).path.strip("/")
    assert name.startswith("azpg-01-")
    assert len(name.removeprefix("azpg-01-")) == 32
    assert "x-ms-blob-public-access" not in create.headers
    assert upload.body == experiment.PAYLOAD
    assert upload.headers["If-None-Match"] == "*"
    assert urlsplit(upload.url).path == urlsplit(download.url).path == f"/{name}/hello.txt"
    assert urlsplit(delete.url).path == f"/{name}"
    assert offline.transport.containers == {"unrelated": {"keep.txt": b"keep me"}}
    assert offline.transport.closed
    assert offline.credential.closed
    output = capsys.readouterr().out
    assert "Blob round trip succeeded" in output
    assert "Temporary container removed" in output


@pytest.mark.parametrize(
    "outputs",
    [
        "not-json",
        "{}",
        "[]",
        '{"subscription_id": null}',
        json.dumps({name: {"value": 123} for name in asdict(TARGET)}),
    ],
)
def test_missing_outputs(outputs: str, offline: OfflineAzure) -> None:
    """Reject missing or malformed state before contacting Azure."""
    offline.cli.outputs["tofu"] = outputs
    offline.cli.sync()
    assert experiment.main() == 1
    assert len(offline.cli.calls) == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group_name", "production"),
        ("location", ""),
        ("location", "North Europe"),
        ("storage_account_url", "https://production.blob.core.windows.net"),
        ("storage_account_url", TARGET.storage_account_url + "?sig=private"),
        ("storage_account_url", "http://azpg01test.blob.core.windows.net"),
    ],
)
def test_invalid_target(name: str, value: str, offline: OfflineAzure) -> None:
    """Reject out-of-experiment names and credential-bearing URLs."""
    values = asdict(TARGET)
    values[name] = value
    offline.cli.outputs["tofu"] = json.dumps({key: {"value": val} for key, val in values.items()})
    offline.cli.sync()
    assert experiment.main() == 1
    assert len(offline.cli.calls) == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


@pytest.mark.parametrize(
    "metadata",
    [
        {"location": "westus", "endpoint": TARGET.storage_account_url},
        {"location": TARGET.location, "endpoint": "https://other.blob.core.windows.net/"},
        {"location": TARGET.location, "endpoint": None},
        [],
        None,
    ],
)
def test_wrong_scope(metadata: object, offline: OfflineAzure) -> None:
    """Stop before writes when Azure metadata differs from the planned target."""
    offline.cli.outputs["az"] = json.dumps(metadata)
    offline.cli.sync()
    assert experiment.main() == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


def test_cancel(monkeypatch: pytest.MonkeyPatch, offline: OfflineAzure) -> None:
    """Cancel without creating clients when the target is not confirmed."""
    monkeypatch.setattr("sys.stdin", StringIO("no\n"))
    assert experiment.main() == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_confirmation_interrupted(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    offline: OfflineAzure,
) -> None:
    """Leave Blob data untouched when confirmation input is unavailable or interrupted."""

    def interrupted(prompt: str) -> str:
        raise error

    monkeypatch.setattr("builtins.input", interrupted)
    assert experiment.main() == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


def test_transfer_interrupted(offline: OfflineAzure) -> None:
    """Attempt data cleanup and close clients even after a keyboard interrupt."""
    offline.transport.failures["upload"] = KeyboardInterrupt()
    assert experiment.main() == 1
    assert offline.transport.requests[-1].method == "DELETE"
    assert not offline.transport.containers
    assert offline.transport.closed
    assert offline.credential.closed


@pytest.mark.parametrize("stage", ["upload", "download", "mismatch"])
def test_round_trip_failure(stage: str, offline: OfflineAzure) -> None:
    """Remove the newly created container after transfer or verification failure."""
    if stage == "mismatch":
        offline.transport.download_override = b"wrong"
    else:
        offline.transport.failures[stage] = 500
    assert experiment.main() == 1
    assert not offline.transport.containers
    assert offline.transport.requests[-1].method == "DELETE"
    assert offline.transport.closed
    assert offline.credential.closed


@pytest.mark.parametrize(
    "error",
    [401, 403, 409, ServiceRequestError("private-response")],
)
def test_create_failure(
    error: int | BaseException,
    offline: OfflineAzure,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never remove a container whose creation failed or expose raw Azure errors."""
    offline.transport.failures["create"] = error
    assert experiment.main() == 1
    assert len(offline.transport.requests) == 1
    assert not offline.transport.containers
    assert offline.transport.closed
    assert offline.credential.closed
    output = capsys.readouterr()
    assert "private-response" not in output.out + output.err


def test_cleanup_failure(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Return failure and explicit recovery instructions when cleanup fails."""
    offline.transport.failures["delete"] = 403
    assert experiment.main() == 1
    assert list(offline.transport.containers.values()) == [{"hello.txt": experiment.PAYLOAD}]
    output = capsys.readouterr()
    assert "Cleanup failed for azpg-01-" in output.err
    assert "make down" in output.err
    assert "Temporary container removed" not in output.out
    assert offline.transport.closed
    assert offline.credential.closed


@pytest.mark.parametrize(
    ("tool", "missing"),
    [("az", False), ("tofu", False), ("az", True), ("tofu", True)],
)
def test_command_failure(tool: str, missing: bool, offline: OfflineAzure) -> None:
    """Fail without SDK access if tooling, authentication, or state reads fail."""
    if missing:
        (offline.cli.bin_dir / tool).unlink()
    else:
        offline.cli.errors[tool] = 1
        offline.cli.sync()
    assert experiment.main() == 1
    assert not offline.transport.requests
    assert offline.credential.requests == 0


def test_existing_container_preserved(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave an existing container untouched when a generated name collides."""
    identifier = UUID("12345678-1234-1234-1234-123456789abc")
    name = f"azpg-01-{identifier.hex}"
    offline.transport.containers[name] = {"original.txt": b"do not delete"}
    monkeypatch.setattr(experiment, "uuid4", lambda: identifier)
    assert experiment.main() == 1
    assert offline.transport.containers == {name: {"original.txt": b"do not delete"}}
    assert len(offline.transport.requests) == 1
    assert offline.credential.closed
    assert offline.transport.closed


def test_command_timeout(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise timeout reporting without waiting for the production timeout."""

    def timed_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("tofu", 30)

    monkeypatch.setattr(experiment.subprocess, "run", timed_out)
    assert experiment.main() == 1
    assert not offline.transport.requests


def test_authentication_failure(offline: OfflineAzure) -> None:
    """Close resources when the real SDK cannot get an access token."""
    offline.credential.error = ClientAuthenticationError("private-response")
    assert experiment.main() == 1
    assert not offline.transport.requests
    assert offline.transport.closed
    assert offline.credential.closed


def test_repeated_runs(offline: OfflineAzure) -> None:
    """Use a different temporary container for each round trip."""
    experiment.round_trip(TARGET)
    experiment.round_trip(TARGET)
    first, second = offline.transport.requests[0], offline.transport.requests[4]
    assert urlsplit(first.url).path != urlsplit(second.url).path
    assert not offline.transport.containers
