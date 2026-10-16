"""Small boundary doubles that leave SDK and subprocess behavior intact."""

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

import pytest
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpRequest, HttpResponse, HttpTransport
from azure.core.utils import case_insensitive_dict
from azure.storage.blob import BlobServiceClient


class MemoryStream:
    """Yield response bytes and allow the SDK to attach download properties."""

    def __init__(self, body: bytes, response: HttpResponse) -> None:
        """Prepare a single response chunk."""
        self._chunks = iter([body])
        self.content_length = len(body)
        self.response = response

    def __iter__(self) -> MemoryStream:
        """Return the stream iterator."""
        return self

    def __next__(self) -> bytes:
        """Return the next response chunk."""
        return next(self._chunks)


class LocalCredential:
    """Supply a fixed test token without invoking any identity provider."""

    def __init__(self) -> None:
        """Start with no token requests or authentication errors."""
        self.closed = False
        self.requests = 0
        self.error: Exception | None = None

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        """Return a nonsecret token or an explicitly configured authentication error."""
        self.requests += 1
        if self.error is not None:
            raise self.error
        return AccessToken("offline-test-token", 4102444800)

    def __enter__(self) -> LocalCredential:
        """Enter the credential context without external authentication."""
        return self

    def __exit__(self, *args: object) -> None:
        """Record credential context cleanup."""
        self.closed = True


class MemoryResponse(HttpResponse):
    """Expose an in-memory HTTP response through Azure's public transport interface."""

    def __init__(
        self, request: HttpRequest, status: int, body: bytes = b"", **headers: str
    ) -> None:
        """Build a response with explicit status, headers, and payload."""
        super().__init__(request, None)
        self.status_code = status
        self.reason = "Offline response"
        self.headers = case_insensitive_dict({"content-length": str(len(body)), **headers})
        self.content_type = self.headers.get("content-type")
        self._body = body

    def body(self) -> bytes:
        """Return the response bytes for SDK deserialization."""
        return self._body

    def read(self) -> bytes:
        """Buffer an error response when the SDK switches out of streaming mode."""
        return self._body

    def stream_download(self, pipeline: Any, **kwargs: Any) -> Any:
        """Yield the same bytes through the SDK download path."""
        return MemoryStream(self._body, self)


class MemoryBlobTransport(HttpTransport):
    """Handle only the Blob REST operations used by these experiments."""

    def __init__(self) -> None:
        """Start with empty storage and no injected failures."""
        self.containers: dict[str, dict[str, bytes]] = {}
        self.requests: list[HttpRequest] = []
        self.failures: dict[str, int | BaseException] = {}
        self.closed = False
        self.next_marker = ""
        self.download_override: bytes | None = None

    def open(self) -> None:
        """Open without allocating network resources."""

    def close(self) -> None:
        """Record SDK context cleanup."""
        self.closed = True

    def __enter__(self) -> MemoryBlobTransport:
        """Enter the transport context without opening a socket."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the transport when its SDK context exits."""
        self.close()

    def send(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        """Apply a request to local state or fail loudly for unsupported operations."""
        self.requests.append(request)
        url = urlsplit(request.url)
        query = parse_qs(url.query)
        parts = url.path.strip("/").split("/")
        container = parts[0]
        if request.method == "GET" and query.get("comp") == ["list"]:
            operation = "list"
        elif query.get("restype") == ["container"]:
            operation = {"PUT": "create", "DELETE": "delete"}[request.method]
        else:
            operation = {"PUT": "upload", "GET": "download"}[request.method]
        failure = self.failures.get(operation)
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            return self._error(request, failure)
        if operation == "list":
            names = "".join(
                f"<Container><Name>{escape(name)}</Name><Properties>"
                "<Last-Modified>Fri, 16 Oct 2026 00:00:00 GMT</Last-Modified>"
                '<Etag>"offline-etag"</Etag></Properties></Container>'
                for name in self.containers
            )
            body = (
                f'<EnumerationResults ServiceEndpoint="{url.scheme}://{url.netloc}/">'
                f"<Containers>{names}</Containers>"
                f"<NextMarker>{escape(self.next_marker)}</NextMarker></EnumerationResults>"
            ).encode()
            return MemoryResponse(request, 200, body, **{"content-type": "application/xml"})
        if operation == "create":
            if container in self.containers:
                return self._error(request, 409)
            self.containers[container] = {}
            return MemoryResponse(request, 201)
        if container not in self.containers:
            return self._error(request, 404)
        if operation == "delete":
            del self.containers[container]
            return MemoryResponse(request, 202)
        blob = "/".join(parts[1:])
        if operation == "upload":
            if blob in self.containers[container] and request.headers.get("If-None-Match") == "*":
                return self._error(request, 409)
            body = request.body
            assert isinstance(body, bytes), "Unexpected upload body type"
            self.containers[container][blob] = body
            return MemoryResponse(request, 201, b"", **{"etag": '"offline-etag"'})
        if blob not in self.containers[container]:
            return self._error(request, 404)
        body = self.download_override
        if body is None:
            body = self.containers[container][blob]
        return MemoryResponse(
            request,
            206,
            body,
            **{
                "content-type": "application/octet-stream",
                "content-range": f"bytes 0-{len(body) - 1}/{len(body)}",
                "etag": '"offline-etag"',
            },
        )

    @staticmethod
    def _error(request: HttpRequest, status: int) -> HttpResponse:
        codes = {
            401: "AuthenticationFailed",
            403: "AuthorizationPermissionMismatch",
            404: "ContainerNotFound",
            409: "ContainerAlreadyExists",
            500: "InternalError",
        }
        code = codes[status]
        body = f"<Error><Code>{code}</Code><Message>private-response</Message></Error>".encode()
        return MemoryResponse(
            request,
            status,
            body,
            **{"content-type": "application/xml", "x-ms-error-code": code},
        )


class LocalCLI:
    """Execute temporary CLI programs using real process creation and JSON output."""

    def __init__(self, path: Path) -> None:
        """Write isolated executable programs and their response configuration."""
        self.bin_dir = path / "bin"
        self.bin_dir.mkdir()
        self.outputs: dict[str, str] = {}
        self.errors: dict[str, int] = {}
        self._configuration = path / "cli.json"
        self._log = path / "calls.jsonl"
        source = (
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "name = pathlib.Path(sys.argv[0]).name\n"
            f"config = json.loads(pathlib.Path({str(self._configuration)!r}).read_text())\n"
            f"with open({str(self._log)!r}, 'a') as log:\n"
            "    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
            "if config['errors'].get(name):\n"
            "    print('private-response', file=sys.stderr)\n"
            "    sys.exit(config['errors'][name])\n"
            "if name != 'az' or sys.argv[1:3] != ['group', 'show']:\n"
            "    print(config['outputs'].get(name, ''))\n"
        )
        for name in ("az", "tofu"):
            program = self.bin_dir / name
            program.write_text(source, encoding="utf-8")
            program.chmod(0o700)
        self.sync()

    def sync(self) -> None:
        """Persist configured responses for child processes."""
        self._configuration.write_text(
            json.dumps({"outputs": self.outputs, "errors": self.errors}), encoding="utf-8"
        )

    @property
    def calls(self) -> list[list[str]]:
        """Read commands actually received by the temporary executables."""
        if not self._log.exists():
            return []
        return [json.loads(line) for line in self._log.read_text().splitlines()]


class OfflineAzure:
    """Keep real application logic and SDK clients behind isolated boundaries."""

    def __init__(self, path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Create per-test state and hide installed Azure and OpenTofu commands."""
        self.cli = LocalCLI(path)
        self.transport = MemoryBlobTransport()
        self.credential = LocalCredential()
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("PATH", str(self.cli.bin_dir))

    def install(self, module: ModuleType) -> None:
        """Replace only credential acquisition and the SDK network transport."""

        def client(**kwargs: Any) -> BlobServiceClient:
            return BlobServiceClient(transport=self.transport, **kwargs)

        self._monkeypatch.setattr(module, "DefaultAzureCredential", lambda: self.credential)
        self._monkeypatch.setattr(module, "BlobServiceClient", client)


@pytest.fixture
def offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OfflineAzure:
    """Provide isolated CLI programs, a credential, and a stateful Blob transport."""
    return OfflineAzure(tmp_path, monkeypatch)
