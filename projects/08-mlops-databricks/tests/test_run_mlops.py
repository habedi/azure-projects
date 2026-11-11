"""Deterministic offline tests for the Databricks MLOps and Model Registry pipeline."""

import importlib
import json
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest
from azure.core.exceptions import AzureError, HttpResponseError, ResourceExistsError

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.08-mlops-databricks.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-08-sandbox",
    location="swedencentral",
    databricks_workspace_url="https://adb-1234567890123456.12.azuredatabricks.net",
    databricks_workspace_id="1234567890123456",
    storage_account_url="https://azpg08sandboxstore.blob.core.windows.net/",
    artifacts_container_name="mlops-artifacts",
)


@dataclass
class MockRunInfo:
    """Mock run info from MLflow."""

    run_id: str = "run-001"
    artifact_uri: str = "dbfs:/databricks/mlflow-tracking/exp-1/run-001/artifacts"


@dataclass
class MockRun:
    """Mock run record from MLflow."""

    info: MockRunInfo = field(default_factory=MockRunInfo)


@dataclass
class MockModelVersion:
    """Mock model version metadata from MLflow."""

    version: str = "1"
    status: str = "READY"


class MockMlflowClient:
    """In-memory MlflowClient test double."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize experiment and model registry trackers."""
        self.experiments: dict[str, str] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.params: dict[str, dict[str, str]] = {}
        self.metrics: dict[str, dict[str, float]] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.registered_models: dict[str, dict[str, Any]] = {}
        self.model_versions: dict[str, list[dict[str, Any]]] = {}
        self.stages: dict[str, str] = {}
        self.deleted_experiments: list[str] = []
        self.deleted_models: list[str] = []

    def create_experiment(self, name: str, **kwargs: Any) -> str:
        """Create mock experiment record."""
        exp_id = f"exp-{len(self.experiments) + 1}"
        self.experiments[exp_id] = name
        return exp_id

    def create_run(self, experiment_id: str, run_name: str, **kwargs: Any) -> MockRun:
        """Create mock experiment run."""
        run_id = f"run-{run_name}"
        self.runs[run_id] = {"experiment_id": experiment_id, "name": run_name}
        return MockRun(
            info=MockRunInfo(
                run_id=run_id,
                artifact_uri=f"dbfs:/databricks/mlflow-tracking/{experiment_id}/{run_id}/artifacts",
            )
        )

    def log_param(self, run_id: str, key: str, value: str, **kwargs: Any) -> None:
        """Record hyperparameter logged to an experiment run."""
        self.params.setdefault(run_id, {})[key] = value

    def log_metric(self, run_id: str, key: str, value: float, **kwargs: Any) -> None:
        """Record evaluation metric logged to an experiment run."""
        self.metrics.setdefault(run_id, {})[key] = value

    def log_text(self, run_id: str, text: str, artifact_file: str, **kwargs: Any) -> None:
        """Record text artifact logged to an experiment run."""
        self.artifacts.setdefault(run_id, {})[artifact_file] = text

    def log_dict(
        self, run_id: str, dictionary: dict[str, Any], artifact_file: str, **kwargs: Any
    ) -> None:
        """Record JSON dictionary artifact logged to an experiment run."""
        self.artifacts.setdefault(run_id, {})[artifact_file] = dictionary

    def create_registered_model(self, name: str, **kwargs: Any) -> None:
        """Create registered model in catalog."""
        self.registered_models[name] = {}

    def create_model_version(
        self,
        name: str,
        source: str,
        run_id: str,
        description: str = "",
        **kwargs: Any,
    ) -> MockModelVersion:
        """Create new version of registered model."""
        version = str(len(self.model_versions.get(name, [])) + 1)
        self.model_versions.setdefault(name, []).append(
            {"version": version, "source": source, "run_id": run_id, "description": description}
        )
        return MockModelVersion(version=version)

    def transition_model_version_stage(
        self,
        name: str,
        version: str,
        stage: str,
        archive_existing_versions: bool = True,
        **kwargs: Any,
    ) -> None:
        """Promote model version to target governance stage."""
        self.stages[f"{name}:{version}"] = stage

    def delete_registered_model(self, name: str, **kwargs: Any) -> None:
        """Delete registered model from catalog."""
        self.deleted_models.append(name)

    def delete_experiment(self, experiment_id: str, **kwargs: Any) -> None:
        """Mark experiment as deleted."""
        self.deleted_experiments.append(experiment_id)


@pytest.fixture(autouse=True)
def sandbox(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> tuple[MockMlflowClient, OfflineAzure]:
    """Configure offline test doubles for Azure CLI, OpenTofu, and MLflow Client."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)

    mlflow_client = MockMlflowClient()
    monkeypatch.setattr(experiment, "MlflowClient", lambda **kw: mlflow_client)
    monkeypatch.setattr("builtins.input", lambda prompt="": TARGET.resource_group_name)

    return mlflow_client, offline


def test_success(
    sandbox: tuple[MockMlflowClient, OfflineAzure],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run full MLOps pipeline through training, MLflow tracking, registry, and cleanup."""
    ws_client, offline = sandbox
    exit_code = experiment.main()
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "DATABRICKS MLOPS GOVERNANCE REPORT: RUN ID" in out
    assert "Promoted Champion     : baseline-linear-v1" in out
    assert "Registry Stage        : Production" in out
    assert "Holdout Anomaly Risk  :" in out
    assert "Verifying 2 persisted MLOps report files in lakehouse container..." in out
    assert "Cleaned up all temporary MLOps artifacts." in out
    assert "completed successfully." in out

    # Verify model was transitioned to Archived stage during teardown in Databricks
    assert any(stage == "Archived" for stage in ws_client.stages.values())

    # Verify teardown cleaned up the Databricks experiment and model
    assert len(ws_client.deleted_experiments) == 1
    assert len(ws_client.deleted_models) == 1

    # Verify that test blobs were deleted from storage container
    assert all(not blobs for blobs in offline.transport.containers.values())


def test_invalid_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched confirmation input and exit cleanly."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "wrong-resource-group")
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
        ("databricks_workspace_url", "https://invalid-workspace.databricks.azure.com"),
        ("databricks_workspace_id", "not-a-number"),
        ("storage_account_url", "http://insecure.blob.core.windows.net"),
        ("artifacts_container_name", "wrong-container"),
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


def test_existing_container(
    offline: OfflineAzure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle pre-existing storage container without crashing."""
    original_client = experiment.BlobServiceClient

    def client_with_existing_container(*args: Any, **kwargs: Any) -> Any:
        srv = original_client(*args, **kwargs)

        def create_container_fail(name: str) -> Any:
            raise ResourceExistsError("Container already exists.")

        srv.create_container = create_container_fail  # type: ignore[method-assign]
        return srv

    monkeypatch.setattr(experiment, "BlobServiceClient", client_with_existing_container)
    assert experiment.main() == 0


def test_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail the pipeline when temporary blob deletion fails during teardown."""
    original_client = experiment.BlobServiceClient

    def client_with_failing_delete(*args: Any, **kwargs: Any) -> Any:
        srv = original_client(*args, **kwargs)
        orig_get_container = srv.get_container_client

        def get_container(name: str) -> Any:
            cont = orig_get_container(name)

            def failing_delete(blob_name: str) -> None:
                raise AzureError("Permission denied during teardown.")

            cont.delete_blob = failing_delete  # type: ignore[method-assign]
            return cont

        srv.get_container_client = get_container  # type: ignore[method-assign]
        return srv

    monkeypatch.setattr(experiment, "BlobServiceClient", client_with_failing_delete)
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "Failed to delete temporary MLOps blobs" in err


def test_low_f1_score_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Trigger validation error if champion model fails minimum 0.70 F1 threshold."""
    low_metrics = experiment.ModelMetrics(
        accuracy=0.50,
        precision=0.40,
        recall=0.45,
        f1_score=0.42,
        roc_auc=0.51,
    )
    fake_weights = {f: 0.1 for f in experiment.FEATURE_NAMES}

    def mock_train(*args: Any, **kwargs: Any) -> tuple[dict[str, float], float, Any]:
        return fake_weights, 0.0, low_metrics

    monkeypatch.setattr(experiment, "train_logistic_classifier", mock_train)
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "below required 0.70 threshold" in err


def test_verify_mlops_records_invalid_count() -> None:
    """Reject verification if saved blob count is not exactly 2."""
    mock_service = MagicMock()
    with pytest.raises(ValueError, match="Expected exactly 2 persisted evaluation files"):
        experiment.verify_mlops_records(
            blob_service=mock_service,
            container_name="test-container",
            saved_blobs=["only_one.json"],
        )


def test_verify_mlops_records_empty_blob() -> None:
    """Reject verification if an uploaded blob is empty."""
    mock_service = MagicMock()
    mock_container = MagicMock()
    mock_blob = MagicMock()
    mock_blob.readall.return_value = b""
    mock_container.download_blob.return_value = mock_blob
    mock_service.get_container_client.return_value = mock_container

    with pytest.raises(ValueError, match="was empty"):
        experiment.verify_mlops_records(
            blob_service=mock_service,
            container_name="test-container",
            saved_blobs=["file1.json", "file2.json"],
        )


def test_verify_mlops_records_invalid_json() -> None:
    """Reject verification if an uploaded blob does not contain a JSON dict or list."""
    mock_service = MagicMock()
    mock_container = MagicMock()
    mock_blob = MagicMock()
    mock_blob.readall.return_value = b"12345"
    mock_container.download_blob.return_value = mock_blob
    mock_service.get_container_client.return_value = mock_container

    with pytest.raises(ValueError, match="Invalid JSON format"):
        experiment.verify_mlops_records(
            blob_service=mock_service,
            container_name="test-container",
            saved_blobs=["file1.json", "file2.json"],
        )


def test_teardown_error_handling(capsys: pytest.CaptureFixture[str]) -> None:
    """Handle exceptions when deleting Databricks model or experiment gracefully."""
    mock_client = MagicMock()
    mock_client.transition_model_version_stage.side_effect = Exception("Archive error")
    mock_client.delete_registered_model.side_effect = Exception("Model registry error")
    mock_client.delete_experiment.side_effect = Exception("Experiment error")

    mock_blob_service = MagicMock()
    mock_container = MagicMock()
    mock_blob_service.get_container_client.return_value = mock_container

    summary = experiment.MLOpsSummary(
        run_id="test-run",
        experiment_id="exp-123",
        experiment_name="/Shared/test-exp",
        registered_model_name="test-model",
        champion_model_name="model-v1",
        model_version="1",
        stage="Production",
        champion_f1_score=0.92,
        holdout_prediction_score=0.88,
        total_candidate_models=3,
        created_at="2026-11-06T00:00:00Z",
    )

    experiment.teardown_mlops_artifacts(
        client=mock_client,
        blob_service=mock_blob_service,
        container_name="test-container",
        summary=summary,
        uploaded_blobs=[],
    )

    err = capsys.readouterr().err
    assert "Notice: Model transition to Archived skipped: Archive error" in err
    assert "Notice: Model registry deletion skipped: Model registry error" in err
    assert "Notice: Experiment deletion skipped: Experiment error" in err


def test_sigmoid_extremes() -> None:
    """Verify numeric stability of sigmoid activation helper."""
    assert experiment.sigmoid(100.0) > 0.9999
    assert experiment.sigmoid(-100.0) < 0.0001
    assert abs(experiment.sigmoid(0.0) - 0.5) < 1e-6


def test_train_logistic_classifier() -> None:
    """Verify that logistic classifier trains and produces valid metrics."""
    weights, bias, metrics = experiment.train_logistic_classifier(
        data=experiment.BENCHMARK_TELEMETRY,
        learning_rate=0.05,
        l2_penalty=0.01,
        epochs=50,
    )
    assert len(weights) == len(experiment.FEATURE_NAMES)
    assert isinstance(bias, float)
    assert 0.0 <= metrics.accuracy <= 1.0
    assert 0.0 <= metrics.f1_score <= 1.0


def test_train_logistic_classifier_inverted() -> None:
    """Verify training branches with false positive and false negative predictions."""
    inverted_data = [
        {
            "cpu_utilization_pct": 95.0,
            "memory_pressure_pct": 95.0,
            "disk_iops_spike": 9.0,
            "network_drop_rate": 0.5,
            "temperature_celsius": 85.0,
            "outage_risk": 0.0,
        },
        {
            "cpu_utilization_pct": -90.0,
            "memory_pressure_pct": -90.0,
            "disk_iops_spike": -9.0,
            "network_drop_rate": -0.5,
            "temperature_celsius": -85.0,
            "outage_risk": 1.0,
        },
    ]
    _, _, metrics = experiment.train_logistic_classifier(
        data=inverted_data,
        learning_rate=0.001,
        l2_penalty=0.0,
        epochs=1,
    )
    assert metrics.accuracy >= 0.0


def test_predict_outage_risk() -> None:
    """Verify inference prediction score calculation."""
    weights = {f: 1.0 for f in experiment.FEATURE_NAMES}
    score = experiment.predict_outage_risk(
        features=experiment.HOLDOUT_TEST_RECORD,
        weights=weights,
        bias=0.0,
    )
    assert 0.0 <= score <= 1.0


def test_target_from_tofu_empty_string() -> None:
    """Reject OpenTofu outputs containing empty strings."""
    raw = {name: {"value": value} for name, value in asdict(TARGET).items()}
    raw["location"] = {"value": ""}
    with pytest.raises(ValueError, match="must be non-empty strings"):
        experiment.Target.from_tofu_outputs(raw)


def test_persist_mlops_records_unexpected_azure_error() -> None:
    """Re-raise unexpected AzureError during container creation."""
    mock_service = MagicMock()
    mock_container = MagicMock()
    custom_err = HttpResponseError("Internal server error")
    custom_err.status_code = 500
    mock_container.create_container.side_effect = custom_err
    mock_service.get_container_client.return_value = mock_container

    summary = experiment.MLOpsSummary(
        run_id="test-run",
        experiment_id="exp-123",
        experiment_name="/Shared/test-exp",
        registered_model_name="test-model",
        champion_model_name="model-v1",
        model_version="1",
        stage="Production",
        champion_f1_score=0.92,
        holdout_prediction_score=0.88,
        total_candidate_models=3,
        created_at="2026-11-06T00:00:00Z",
    )

    with pytest.raises(AzureError, match="Internal server error"):
        experiment.persist_mlops_records(
            blob_service=mock_service,
            container_name="test-container",
            run_id="test-run",
            summary=summary,
            runs=[],
            uploaded_blobs=[],
        )


def test_check_databricks_workspace_readiness_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise actionable ValueError when workspace requires initial Single Sign-On signup."""
    error_payload = b'{"error":"azure_pending_signup_redirect_required"}'
    fake_http_error = HTTPError(
        url="https://adb-123.azuredatabricks.net",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(error_payload),
    )

    def mock_urlopen(*args: Any, **kwargs: Any) -> Any:
        raise fake_http_error

    monkeypatch.setattr(experiment, "urlopen", mock_urlopen)
    with pytest.raises(ValueError, match="one-time Single Sign-On initialization"):
        experiment.check_databricks_workspace_readiness("https://adb-123.azuredatabricks.net")


def test_check_databricks_workspace_readiness_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass cleanly when workspace is ready."""

    def mock_urlopen(*args: Any, **kwargs: Any) -> Any:
        return MagicMock()

    monkeypatch.setattr(experiment, "urlopen", mock_urlopen)
    # Should not raise
    experiment.check_databricks_workspace_readiness("https://adb-123.azuredatabricks.net")


def test_pipeline_catches_pending_signup_runtime(
    sandbox: tuple[MockMlflowClient, OfflineAzure],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Catch Databricks SDK missing workspace exception and provide actionable guidance."""

    def mock_execute(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Did not find workspace with specified org ID: 1234567890123456")

    monkeypatch.setattr(experiment, "execute_mlops_lifecycle", mock_execute)
    exit_code = experiment.main()
    assert exit_code == 1

    err = capsys.readouterr().err
    assert "one-time Single Sign-On initialization" in err


@pytest.mark.parametrize("operation", ["log_param", "create_model_version", "predict_outage_risk"])
def test_partial_lifecycle_cleanup(
    sandbox: tuple[MockMlflowClient, OfflineAzure],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Remove resources created before a lifecycle failure."""
    client, _ = sandbox

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Injected failure")

    target = experiment if operation == "predict_outage_risk" else client
    monkeypatch.setattr(target, operation, fail)
    assert experiment.main() == 1
    assert client.deleted_experiments == ["exp-1"]
    assert client.deleted_models == list(client.registered_models)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([(-2, 0), (-1, 0), (1, 1), (2, 1)], 1.0),
        ([(-2, 1), (-1, 1), (1, 0), (2, 0)], 0.0),
        ([(0, 0), (0, 1)], 0.5),
        ([(-2, 0), (-1, 1), (1, 0), (2, 1)], 0.75),
    ],
)
def test_auc_uses_probability_rankings(values: list[tuple[int, int]], expected: float) -> None:
    """Calculate AUC across thresholds, including tied scores."""
    data = [
        {**{name: float(value) for name in experiment.FEATURE_NAMES}, "outage_risk": float(label)}
        for value, label in values
    ]
    _, _, metrics = experiment.train_logistic_classifier(data, 0.0, 0.0, epochs=0)
    assert metrics.roc_auc == expected


@pytest.mark.parametrize("labels", [[], [0.0], [1.0], [0.0, 2.0]])
def test_auc_rejects_undefined_labels(labels: list[float]) -> None:
    """Reject datasets for which binary AUC is undefined."""
    with pytest.raises(ValueError, match="both classes"):
        experiment.train_logistic_classifier(
            [{"outage_risk": label} for label in labels], 0.0, 0.0, epochs=0
        )
