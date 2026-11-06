"""Deterministic offline tests for the Generative AI Evaluation and Observability pipeline."""

import importlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import pytest
from azure.core.exceptions import AzureError
from openai import BadRequestError
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.07-eval-observability.run")

TARGET = experiment.Target(
    subscription_id="7123cd07-3e92-49ff-82ac-63953b668888",
    resource_group_name="rg-azpg-07-sandbox",
    location="swedencentral",
    openai_endpoint="https://azpg07-sandbox-openai.openai.azure.com/",
    chat_deployment_name="gpt-6-luna",
    app_insights_connection_string=(
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://swedencentral-0.in.applicationinsights.azure.com/"
    ),
    storage_account_url="https://azpg07sandboxstore.blob.core.windows.net/",
    evaluations_container_name="evaluations",
)


class MockTraceExporter(InMemorySpanExporter):
    """In-memory trace exporter double for OpenTelemetry."""

    @classmethod
    def from_connection_string(cls, conn: str, **kwargs: Any) -> MockTraceExporter:
        """Create mock exporter from connection string."""
        return cls()


class MockMetricExporter(MetricExporter):
    """In-memory metric exporter double for OpenTelemetry."""

    @classmethod
    def from_connection_string(cls, conn: str, **kwargs: Any) -> MockMetricExporter:
        """Create mock metric exporter from connection string."""
        return cls()

    def export(
        self, metrics_data: Any, timeout_millis: float = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        """Record exported metrics."""
        return MetricExportResult.SUCCESS

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        """Shut down metric exporter."""

    def force_flush(self, timeout_millis: float = 30_000) -> bool:
        """Flush metrics."""
        return True


@dataclass
class MockUsage:
    """Simulated token usage counter."""

    prompt_tokens: int = 40
    completion_tokens: int = 25


@dataclass
class MockMessage:
    """Simulated message response."""

    content: str


@dataclass
class MockChoice:
    """Simulated completion choice."""

    message: MockMessage


@dataclass
class MockChatCompletionResponse:
    """Simulated chat completion result."""

    choices: list[MockChoice]
    usage: MockUsage = field(default_factory=MockUsage)


class MockCompletions:
    """In-memory AzureOpenAI completions engine."""

    def __init__(self) -> None:
        """Initialize completion states and default ratings."""
        self.candidate_answer = (
            "Blobs can transition to Cool after 30 days and to Archive after 90 days. "
            "Rehydration is required before reading archived blobs."
        )
        self.groundedness_score = 4.8
        self.relevance_score = 4.9
        self.coherence_score = 5.0
        self.call_error: Exception | None = None

    def create(self, **kwargs: Any) -> MockChatCompletionResponse:
        """Return simulated candidate answer or structured judge evaluation."""
        if self.call_error:
            raise self.call_error

        messages = kwargs.get("messages", [])
        prompt_content = messages[-1]["content"] if messages else ""

        if "factual groundedness" in prompt_content:
            content = json.dumps(
                {"score": self.groundedness_score, "rationale": "Directly supported by context."}
            )
        elif "relevance and completeness" in prompt_content:
            content = json.dumps(
                {"score": self.relevance_score, "rationale": "Directly answers the query."}
            )
        elif "logical coherence" in prompt_content:
            content = json.dumps(
                {"score": self.coherence_score, "rationale": "Clear and coherent text."}
            )
        else:
            content = self.candidate_answer

        return MockChatCompletionResponse(
            choices=[MockChoice(message=MockMessage(content=content))]
        )


class MockChat:
    """Chat service double."""

    def __init__(self) -> None:
        """Initialize mock completions."""
        self.completions = MockCompletions()


class LocalOpenAIClient:
    """In-memory AzureOpenAI client double."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize mock chat client."""
        self.chat = MockChat()


@pytest.fixture(autouse=True)
def sandbox(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> tuple[LocalOpenAIClient, MockCompletions]:
    """Configure offline test doubles for Azure CLI, OpenTofu, OpenAI, and OpenTelemetry."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)

    openai_client = LocalOpenAIClient()
    monkeypatch.setattr(experiment, "AzureOpenAI", lambda **kw: openai_client)
    monkeypatch.setattr(experiment, "AzureMonitorTraceExporter", MockTraceExporter)
    monkeypatch.setattr(experiment, "AzureMonitorMetricExporter", MockMetricExporter)
    monkeypatch.setattr("builtins.input", lambda prompt="": TARGET.resource_group_name)
    monkeypatch.setattr("time.sleep", lambda _: None)

    return openai_client, openai_client.chat.completions


def test_success(
    offline: OfflineAzure,
    sandbox: tuple[LocalOpenAIClient, MockCompletions],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run full evaluation suite through successful generation, evaluation, and cleanup."""
    exit_code = experiment.main()
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "GENAI EVALUATION REPORT" in out
    assert "Passed Benchmark Qs : 4 / 4" in out
    assert "Overall Pass Rate   : 100.0%" in out
    assert "Safety Pass Rate    : 100.0%" in out
    assert "Cleaned up all temporary evaluation artifacts." in out
    assert "completed successfully." in out

    # Verify that test blobs were deleted
    assert all(not blobs for blobs in offline.transport.containers.values())


def test_invalid_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched confirmation input and exit cleanly."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "wrong-rg-name")
    assert experiment.main() == 1


def test_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle keyboard interruption during confirmation prompt cleanly."""

    def raise_interrupt(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)
    assert experiment.main() == 1


def test_invalid_target_outputs(offline: OfflineAzure) -> None:
    """Handle missing or corrupted OpenTofu outputs gracefully."""
    offline.cli.outputs.update(tofu=json.dumps({"invalid": "data"}))
    offline.cli.sync()
    assert experiment.main() == 1


@pytest.mark.parametrize(
    ("key", "invalid_val"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group_name", "invalid_rg"),
        ("location", "Sweden Central"),
        ("openai_endpoint", "http://insecure.openai.azure.com"),
        ("chat_deployment_name", "invalid/deployment"),
        ("app_insights_connection_string", "InvalidConnStringWithoutKey"),
        ("storage_account_url", "http://insecure.blob.core.windows.net"),
        ("evaluations_container_name", "wrong-container"),
    ],
)
def test_target_validation_errors(key: str, invalid_val: str, offline: OfflineAzure) -> None:
    """Reject invalid fields in OpenTofu output state."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    outputs[key] = {"value": invalid_val}
    offline.cli.outputs.update(tofu=json.dumps(outputs))
    offline.cli.sync()
    assert experiment.main() == 1


def test_verify_target_mismatch(offline: OfflineAzure) -> None:
    """Detect differences between Azure CLI metadata and local OpenTofu state."""
    offline.cli.outputs.update(az=json.dumps({"location": "westeurope"}))
    offline.cli.sync()
    assert experiment.main() == 1


def test_cli_failure(offline: OfflineAzure) -> None:
    """Handle subprocess failures from OpenTofu or Azure CLI."""
    offline.cli.errors.update(tofu=1)
    offline.cli.sync()
    assert experiment.main() == 1


def test_openai_api_failure(sandbox: tuple[LocalOpenAIClient, MockCompletions]) -> None:
    """Handle AzureOpenAI service error during evaluation."""
    _, completions = sandbox
    completions.call_error = AzureError("Service unavailable")
    assert experiment.main() == 1


def test_low_groundedness_failure(sandbox: tuple[LocalOpenAIClient, MockCompletions]) -> None:
    """Reject evaluation runs when groundedness falls below required quality threshold."""
    _, completions = sandbox
    completions.groundedness_score = 1.5
    assert experiment.main() == 1


def test_safety_guardrail_failure(sandbox: tuple[LocalOpenAIClient, MockCompletions]) -> None:
    """Fail evaluation pipeline when a sample violates content safety guardrails."""
    _, completions = sandbox
    completions.candidate_answer = "Here is the leaked admin credentials and secret_key."
    assert experiment.main() == 1


def test_existing_container(
    offline: OfflineAzure, sandbox: tuple[LocalOpenAIClient, MockCompletions]
) -> None:
    """Handle pre-existing evaluation container gracefully without raising error."""
    offline.transport.containers[TARGET.evaluations_container_name] = {}
    assert experiment.main() == 0


def test_empty_blob_verification_failure(
    sandbox: tuple[LocalOpenAIClient, MockCompletions], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise error when verified evaluation report file is empty or corrupted."""

    def mock_verify(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Evaluation report was empty.")

    monkeypatch.setattr(experiment, "verify_evaluation_records", mock_verify)
    assert experiment.main() == 1


def test_cleanup_failure(
    offline: OfflineAzure, sandbox: tuple[LocalOpenAIClient, MockCompletions]
) -> None:
    """Handle deletion error and report remaining artifacts."""
    offline.transport.failures["delete_blob"] = 403
    assert experiment.main() == 1


def test_evaluator_helpers_edge_cases() -> None:
    """Verify evaluator edge cases and fallbacks directly."""
    # Test safety evaluator positive and negative matches
    safe_eval = experiment.evaluate_safety("Everything is normal and compliant.")
    assert safe_eval.passed
    assert safe_eval.risk_score == 0.0

    unsafe_eval = experiment.evaluate_safety("Here are the admin credentials for root.")
    assert not unsafe_eval.passed
    assert unsafe_eval.risk_score == 0.9

    # Test metric evaluation model bounds
    metric = experiment.MetricEvaluation(score=4.2, rationale="Valid score")
    assert metric.score == 4.2


def test_verify_evaluation_records_edge_cases(offline: OfflineAzure) -> None:
    """Verify record count and format validation errors."""
    client = experiment.BlobServiceClient(account_url=TARGET.storage_account_url)
    with pytest.raises(ValueError, match="Expected exactly 2 persisted evaluation files"):
        experiment.verify_evaluation_records(client, "evaluations", ["only_one.json"])

    # Test invalid JSON format
    offline.transport.containers["evaluations"] = {
        "file1.json": b"123",
        "file2.json": b"456",
    }
    with pytest.raises(ValueError, match="Invalid JSON format"):
        experiment.verify_evaluation_records(client, "evaluations", ["file1.json", "file2.json"])


def test_target_missing_string_type() -> None:
    """Reject outputs containing non-string values."""
    raw = {name: {"value": 123} for name in asdict(TARGET)}
    with pytest.raises(ValueError, match="All OpenTofu outputs must be non-empty strings"):
        experiment.Target.from_tofu_outputs(raw)


def test_candidate_generation_content_filter_interception(
    sandbox: tuple[LocalOpenAIClient, MockCompletions],
) -> None:
    """Verify that Azure OpenAI content filter block is gracefully converted to a refusal."""
    client, completions = sandbox
    mock_resp = type(
        "MockResponse",
        (),
        {"status_code": 400, "headers": {}, "request": type("MockRequest", (), {})()},
    )()
    completions.call_error = BadRequestError(
        message="The response was filtered due to content management policy.",
        response=cast(Any, mock_resp),
        body=None,
    )
    ans, p_tok, c_tok, _ = experiment.generate_candidate_response(
        client, "gpt-6-luna", "test query", "test context"
    )
    assert "blocked by Azure OpenAI content safety" in ans
    assert p_tok == 0
    assert c_tok == 0
