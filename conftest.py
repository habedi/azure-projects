"""Shared offline fixtures for root and experiment tests."""

import socket

import pytest

from tests.support import offline as offline


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if a test attempts a real network connection."""

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Automated tests must not open network connections.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
