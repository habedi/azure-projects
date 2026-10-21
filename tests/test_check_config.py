"""Offline tests for the live configuration checker."""

import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError

from scripts import check_config
from tests.support import OfflineAzure

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
ACCOUNT_URL = "https://azprojects.blob.core.windows.net"


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary configuration without reading the user's .env."""
    for name in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_STORAGE_ACCOUNT_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        f"AZURE_SUBSCRIPTION_ID={SUBSCRIPTION}\n"
        "AZURE_RESOURCE_GROUP=rg-azprojects\n"
        f"AZURE_STORAGE_ACCOUNT_URL={ACCOUNT_URL}/\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def checker_offline(offline: OfflineAzure) -> OfflineAzure:
    """Connect the checker to the shared offline SDK and CLI boundaries."""
    offline.install(check_config)
    offline.cli.outputs["az"] = ACCOUNT_URL + "/\n"
    offline.cli.sync()
    return offline


def test_valid_settings(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer .env values and normalize a trailing slash."""
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "different-group")
    assert check_config.load_settings(env_file) == (SUBSCRIPTION, "rg-azprojects", ACCOUNT_URL)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AZURE_SUBSCRIPTION_ID", ""),
        ("AZURE_SUBSCRIPTION_ID", "   "),
        ("AZURE_SUBSCRIPTION_ID", "not-a-uuid"),
        ("AZURE_RESOURCE_GROUP", ""),
        ("AZURE_RESOURCE_GROUP", "<YOUR_RESOURCE_GROUP_NAME>"),
        ("AZURE_RESOURCE_GROUP", "-invalid"),
        ("AZURE_STORAGE_ACCOUNT_URL", ""),
        ("AZURE_STORAGE_ACCOUNT_URL", "http://azprojects.blob.core.windows.net"),
        ("AZURE_STORAGE_ACCOUNT_URL", ACCOUNT_URL + "?sig=private"),
        ("AZURE_STORAGE_ACCOUNT_URL", ACCOUNT_URL + "/container"),
        ("AZURE_STORAGE_ACCOUNT_URL", ACCOUNT_URL + "#private"),
        ("AZURE_STORAGE_ACCOUNT_URL", "https://user:private@azprojects.blob.core.windows.net"),
        ("AZURE_STORAGE_ACCOUNT_URL", "https://[invalid"),
        ("AZURE_STORAGE_ACCOUNT_URL", ACCOUNT_URL + ":443"),
        ("AZURE_STORAGE_ACCOUNT_URL", "https://ab.blob.core.windows.net"),
        ("AZURE_STORAGE_ACCOUNT_URL", "https://" + "a" * 25 + ".blob.core.windows.net"),
    ],
)
def test_invalid_settings(
    env_file: Path,
    name: str,
    value: str,
) -> None:
    """Reject invalid settings using only the explicit temporary configuration."""
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")
    with pytest.raises(ValueError):
        check_config.load_settings(env_file)


def test_missing_settings(env_file: Path) -> None:
    """Fail clearly when neither .env nor environment settings exist."""
    with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
        check_config.load_settings(env_file.parent / "missing.env")


def test_environment_settings(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use environment values when the explicitly selected file does not exist."""
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", f" {SUBSCRIPTION} ")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", " rg-environment ")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", f" {ACCOUNT_URL}/ ")
    assert check_config.load_settings(env_file.parent / "missing.env") == (
        SUBSCRIPTION,
        "rg-environment",
        ACCOUNT_URL,
    )


def test_empty_file_value_overrides_environment(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject an empty file value instead of silently taking the environment value."""
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-environment")
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("AZURE_RESOURCE_GROUP=\n")
    with pytest.raises(ValueError, match="AZURE_RESOURCE_GROUP"):
        check_config.load_settings(env_file)


def test_no_environment_interpolation(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dotenv interpolation disabled even when a referenced variable exists."""
    monkeypatch.setenv("SANDBOX_GROUP", "rg-expanded")
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("AZURE_RESOURCE_GROUP=${SANDBOX_GROUP}\n")
    assert check_config.load_settings(env_file)[1] == "${SANDBOX_GROUP}"


def test_invalid_settings_reported(
    env_file: Path, checker_offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report validation errors before invoking the CLI or authenticating."""
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("AZURE_SUBSCRIPTION_ID=invalid\n")
    assert check_config.main(env_file) == 1
    assert "AZURE_SUBSCRIPTION_ID" in capsys.readouterr().err
    assert checker_offline.cli.calls == []
    assert checker_offline.credential.requests == 0
    assert checker_offline.transport.requests == []


@pytest.mark.parametrize("has_container", [False, True])
@pytest.mark.parametrize("next_marker", ["", "another-page"])
def test_success(
    env_file: Path,
    checker_offline: OfflineAzure,
    has_container: bool,
    next_marker: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Use scoped CLI calls and one read-only SDK page, including empty listings."""
    if has_container:
        checker_offline.transport.containers["existing"] = {"keep.txt": b"unchanged"}
    checker_offline.transport.next_marker = next_marker
    assert check_config.main(env_file) == 0
    scope = ["--subscription", SUBSCRIPTION, "--resource-group", "rg-azprojects"]
    assert checker_offline.cli.calls == [
        ["az", "group", "show", *scope, "--output", "none"],
        [
            "az",
            "storage",
            "account",
            "show",
            *scope,
            "--name",
            "azprojects",
            "--query",
            "primaryEndpoints.blob",
            "--output",
            "tsv",
        ],
    ]
    assert checker_offline.credential.requests > 0
    assert len(checker_offline.transport.requests) == 1
    request = checker_offline.transport.requests[0]
    assert request.method == "GET"
    url = urlsplit(request.url)
    assert f"{url.scheme}://{url.netloc}" == ACCOUNT_URL
    assert url.path in ("", "/")
    query = parse_qs(url.query)
    assert query["comp"] == ["list"]
    assert query["maxresults"] == ["1"]
    assert "marker" not in query
    assert checker_offline.transport.containers == (
        {"existing": {"keep.txt": b"unchanged"}} if has_container else {}
    )
    assert checker_offline.transport.closed
    assert checker_offline.credential.closed
    output = capsys.readouterr()
    assert "container listing succeeded" in output.out
    assert output.err == ""


@pytest.mark.parametrize("failure", ["missing", "nonzero"])
def test_cli_failure(
    env_file: Path,
    checker_offline: OfflineAzure,
    failure: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report CLI failures without attempting Blob access."""
    if failure == "missing":
        (checker_offline.cli.bin_dir / "az").unlink()
    else:
        checker_offline.cli.errors["az"] = 7
        checker_offline.cli.sync()
    assert check_config.main(env_file) == 1
    assert "Azure CLI" in capsys.readouterr().err
    assert checker_offline.cli.calls == (
        []
        if failure == "missing"
        else [
            [
                "az",
                "group",
                "show",
                "--subscription",
                SUBSCRIPTION,
                "--resource-group",
                "rg-azprojects",
                "--output",
                "none",
            ],
        ]
    )
    assert checker_offline.credential.requests == 0
    assert checker_offline.transport.requests == []


def test_cli_timeout(
    env_file: Path,
    checker_offline: OfflineAzure,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report a timeout without waiting or attempting authentication."""

    def timeout(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 60, stderr="private-response")

    monkeypatch.setattr(check_config.subprocess, "run", timeout)
    assert check_config.main(env_file) == 1
    output = capsys.readouterr()
    assert "Azure CLI check failed" in output.err
    assert "private-response" not in output.out + output.err
    assert checker_offline.cli.calls == []
    assert checker_offline.credential.requests == 0
    assert checker_offline.transport.requests == []


@pytest.mark.parametrize("endpoint", ["https://different.blob.core.windows.net/", ""])
def test_wrong_endpoint(
    env_file: Path,
    checker_offline: OfflineAzure,
    endpoint: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop before Blob access if Azure reports a different or missing endpoint."""
    checker_offline.cli.outputs["az"] = endpoint
    checker_offline.cli.sync()
    assert check_config.main(env_file) == 1
    assert "Storage URL does not match" in capsys.readouterr().err
    assert len(checker_offline.cli.calls) == 2
    assert checker_offline.credential.requests == 0
    assert checker_offline.transport.requests == []


@pytest.mark.parametrize(
    "error",
    [HttpResponseError("private-response"), ServiceRequestError("private-response"), 403, 500],
)
def test_blob_failure(
    env_file: Path,
    checker_offline: OfflineAzure,
    error: int | Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Close clients after lazy listing failures and redact raw Azure errors."""
    checker_offline.transport.failures["list"] = error
    assert check_config.main(env_file) == 1
    output = capsys.readouterr()
    assert "Blob access failed" in output.err
    assert "private-response" not in output.out + output.err
    assert len(checker_offline.transport.requests) == 1
    assert checker_offline.transport.closed
    assert checker_offline.credential.closed


def test_authentication_failure(
    env_file: Path, checker_offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Close both contexts when authentication fails before the HTTP request."""
    checker_offline.credential.error = ClientAuthenticationError("private-response")
    assert check_config.main(env_file) == 1
    output = capsys.readouterr()
    assert "Blob access failed" in output.err
    assert "private-response" not in output.out + output.err
    assert checker_offline.credential.requests > 0
    assert checker_offline.transport.requests == []
    assert checker_offline.transport.closed
    assert checker_offline.credential.closed
