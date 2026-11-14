"""Offline smoke tests for the Azure projects environment."""

import os
import re
import socket
import subprocess
import tomllib
from pathlib import Path

import pytest
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv


def test_development_python_version() -> None:
    """Keep local Python selection and the Nix development shell consistent."""
    root = Path(__file__).resolve().parents[1]
    version = (root / ".python-version").read_text().strip()
    configuration = tomllib.loads((root / "pyproject.toml").read_text())
    assert version == configuration["tool"]["pyright"]["pythonVersion"]
    nix = (root / "flake.nix").read_text()
    package = "python" + version.replace(".", "")
    packages = re.search(r"packages\s*=\s*with pkgs;\s*\[([^]]+)\]", nix)
    assert packages is not None and package in packages[1].split()
    assert re.search(rf"UV_PYTHON\s*=\s*pkgs\.{package}\.interpreter;", nix)


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


def test_makefile_test_project_target(tmp_path: Path) -> None:
    """Verify the root Makefile test-project target runs plan, up, run, and down cleanly."""
    root = Path(__file__).resolve().parents[1]
    root_makefile = root / "Makefile"
    content = root_makefile.read_text(encoding="utf-8")
    assert "PROJECT    ?= 01-blob-storage" in content
    assert "test-project:" in content

    project_dir = tmp_path / "mock-project"
    project_dir.mkdir()
    log_file = tmp_path / "calls.log"
    fail_flag = tmp_path / "fail_target"
    (project_dir / "Makefile").write_text(
        ".PHONY: plan up run down\n"
        "plan up run down:\n"
        f"\t@echo $@ >> '{log_file}'\n"
        f"\t@if [ -f '{fail_flag}' ] && grep -qxF $@ '{fail_flag}'; then exit 1; fi\n",
        encoding="utf-8",
    )

    success = subprocess.run(
        ["make", "-f", str(root_makefile), "test-project", f"PROJECT={project_dir}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert success.returncode == 0
    assert log_file.read_text(encoding="utf-8").splitlines() == ["plan", "up", "run", "down"]

    log_file.unlink()
    fail_flag.write_text("run", encoding="utf-8")
    run_failure = subprocess.run(
        ["make", "-f", str(root_makefile), "test-project", f"PROJECT={project_dir}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run_failure.returncode != 0
    assert log_file.read_text(encoding="utf-8").splitlines() == ["plan", "up", "run", "down"]

    log_file.unlink()
    fail_flag.write_text("up", encoding="utf-8")
    up_failure = subprocess.run(
        ["make", "-f", str(root_makefile), "test-project", f"PROJECT={project_dir}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert up_failure.returncode != 0
    assert log_file.read_text(encoding="utf-8").splitlines() == ["plan", "up"]

    missing = subprocess.run(
        ["make", "-f", str(root_makefile), "test-project", "PROJECT=nonexistent-project"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "Project not found" in missing.stderr
