"""In-memory HTTP services for the real Search and OpenAI SDK clients."""

import json
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx2
from azure.core.pipeline.transport import HttpRequest, HttpResponse, HttpTransport

from tests.support import MemoryResponse


class SearchTransport(HttpTransport):
    """Implement the Search REST operations used by the experiment."""

    def __init__(self) -> None:
        """Start with empty indexes and successful responses."""
        self.indexes: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[HttpRequest] = []
        self.failures: dict[str, int] = {}
        self.reject_document = False
        self.closed = False

    def open(self) -> None:
        """Open without network resources."""

    def close(self) -> None:
        """Record client cleanup."""
        self.closed = True

    def __enter__(self) -> SearchTransport:
        """Enter the transport context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the transport context."""
        self.close()

    def send(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        """Parse actual SDK requests and return service-shaped responses."""
        self.requests.append(request)
        path = unquote(urlsplit(request.url).path)
        if request.method == "POST" and path == "/indexes":
            operation = "create"
        elif request.method == "DELETE":
            operation = "delete"
        elif path.endswith("/search.index"):
            operation = "upload"
        elif path.endswith("/$count"):
            operation = "count"
        else:
            assert path.endswith("/search.post.search"), path
            operation = "search"
        status = self.failures.get(operation)
        if status:
            return self._response(request, status, {"error": {"message": "private-response"}})
        if operation == "create":
            body = request.body
            assert isinstance(body, (str, bytes))
            schema = json.loads(body)
            vector = next(field for field in schema["fields"] if field["name"] == "content_vector")
            assert vector["searchable"] is True
            assert vector["dimensions"] == 1536
            self.indexes[schema["name"]] = schema
            self.documents[schema["name"]] = []
            return self._response(request, 201, schema)
        name = path.split("'")[1]
        if operation == "delete":
            self.indexes.pop(name, None)
            self.documents.pop(name, None)
            return self._response(request, 204, None)
        if operation == "upload":
            body = request.body
            assert isinstance(body, (str, bytes))
            documents = json.loads(body)["value"]
            self.documents[name].extend(documents)
            results = [
                {
                    "key": doc["chunk_id"],
                    "status": not (self.reject_document and index == 0),
                    "statusCode": 400 if self.reject_document and index == 0 else 201,
                }
                for index, doc in enumerate(documents)
            ]
            return self._response(request, 200, {"value": results})
        if operation == "count":
            return self._response(request, 200, len(self.documents[name]))
        selected = sorted(
            self.documents[name], key=lambda doc: doc["doc_id"] != "doc-cost-control"
        )[:2]
        results = [
            {
                **{key: doc[key] for key in ("chunk_id", "doc_id", "title", "content")},
                "@search.score": 0.895 - index * 0.153,
            }
            for index, doc in enumerate(selected)
        ]
        return self._response(request, 200, {"value": results})

    @staticmethod
    def _response(request: HttpRequest, status: int, body: Any) -> MemoryResponse:
        return MemoryResponse(
            request, status, json.dumps(body).encode(), **{"content-type": "application/json"}
        )


class OpenAITransport(httpx2.BaseTransport):
    """Return JSON through the real OpenAI SDK's HTTP boundary."""

    def __init__(self) -> None:
        """Start with a cited response and no failures."""
        self.answer = "Run make down to remove temporary resources [Source: doc-cost-control]."
        self.failures: dict[str, int | Exception] = {}
        self.requests: list[httpx2.Request] = []
        self.closed = False

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        """Generate embeddings or a completion from serialized SDK requests."""
        self.requests.append(request)
        operation = "embeddings" if request.url.path.endswith("/embeddings") else "chat"
        failure = self.failures.get(operation)
        if isinstance(failure, Exception):
            raise failure
        if failure:
            return httpx2.Response(failure, json={"error": {"message": "private-response"}})
        payload = json.loads(request.content)
        if operation == "embeddings":
            assert isinstance(payload["input"], list)
            body = {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [0.05] * 1536}
                    for index in range(len(payload["input"]))
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        else:
            body = {
                "id": "offline-chat",
                "object": "chat.completion",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": self.answer},
                    }
                ],
            }
        return httpx2.Response(200, json=body)

    def close(self) -> None:
        """Record HTTP client cleanup."""
        self.closed = True
