"""Azure Databricks MLOps pipeline for experiment tracking and model registry governance."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from databricks.sdk import WorkspaceClient
from pydantic import BaseModel, Field

# Synthetic cloud telemetry dataset for VM anomaly and outage risk prediction
FEATURE_NAMES = [
    "cpu_utilization_pct",
    "memory_pressure_pct",
    "disk_iops_spike",
    "network_drop_rate",
    "temperature_celsius",
]

BENCHMARK_TELEMETRY: list[dict[str, float]] = [
    {
        "cpu_utilization_pct": 94.5,
        "memory_pressure_pct": 91.2,
        "disk_iops_spike": 8.7,
        "network_drop_rate": 0.42,
        "temperature_celsius": 82.5,
        "outage_risk": 1.0,
    },
    {
        "cpu_utilization_pct": 28.1,
        "memory_pressure_pct": 34.0,
        "disk_iops_spike": 1.1,
        "network_drop_rate": 0.01,
        "temperature_celsius": 46.2,
        "outage_risk": 0.0,
    },
    {
        "cpu_utilization_pct": 88.0,
        "memory_pressure_pct": 86.4,
        "disk_iops_spike": 7.3,
        "network_drop_rate": 0.35,
        "temperature_celsius": 78.9,
        "outage_risk": 1.0,
    },
    {
        "cpu_utilization_pct": 42.6,
        "memory_pressure_pct": 51.0,
        "disk_iops_spike": 2.4,
        "network_drop_rate": 0.04,
        "temperature_celsius": 52.1,
        "outage_risk": 0.0,
    },
    {
        "cpu_utilization_pct": 96.2,
        "memory_pressure_pct": 95.0,
        "disk_iops_spike": 9.4,
        "network_drop_rate": 0.58,
        "temperature_celsius": 87.3,
        "outage_risk": 1.0,
    },
    {
        "cpu_utilization_pct": 31.4,
        "memory_pressure_pct": 41.5,
        "disk_iops_spike": 1.8,
        "network_drop_rate": 0.02,
        "temperature_celsius": 48.0,
        "outage_risk": 0.0,
    },
]

HOLDOUT_TEST_RECORD = {
    "cpu_utilization_pct": 92.0,
    "memory_pressure_pct": 89.5,
    "disk_iops_spike": 8.1,
    "network_drop_rate": 0.38,
    "temperature_celsius": 81.0,
}


class ModelMetrics(BaseModel):
    """Evaluation metrics for a trained classification model."""

    accuracy: float = Field(..., ge=0.0, le=1.0)
    precision: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    f1_score: float = Field(..., ge=0.0, le=1.0)
    roc_auc: float = Field(..., ge=0.0, le=1.0)


class CandidateModelRun(BaseModel):
    """Metadata and metrics for a single MLflow experiment run."""

    model_name: str
    run_id: str
    hyperparameters: dict[str, str]
    metrics: ModelMetrics


class MLOpsSummary(BaseModel):
    """End-to-end MLOps pipeline summary and model registry promotion report."""

    run_id: str
    experiment_id: str
    experiment_name: str
    registered_model_name: str
    champion_model_name: str
    model_version: str
    stage: str
    champion_f1_score: float
    holdout_prediction_score: float
    total_candidate_models: int
    created_at: str


@dataclass(frozen=True)
class Target:
    """Target configuration for the Databricks MLOps pipeline."""

    subscription_id: str
    resource_group_name: str
    location: str
    databricks_workspace_url: str
    databricks_workspace_id: str
    storage_account_url: str
    artifacts_container_name: str

    @classmethod
    def from_tofu_outputs(cls, raw: dict[str, Any]) -> Target:
        """Parse and validate OpenTofu output state."""
        values = {
            key: raw[key]["value"]
            for key in (
                "subscription_id",
                "resource_group_name",
                "location",
                "databricks_workspace_url",
                "databricks_workspace_id",
                "storage_account_url",
                "artifacts_container_name",
            )
        }
        if not all(isinstance(val, str) and val for val in values.values()):
            raise ValueError("All OpenTofu outputs must be non-empty strings.")

        target = cls(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-08-[a-z0-9-]+", target.resource_group_name):
            raise ValueError("Invalid resource_group_name in target state.")
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError("Invalid location in target state.")
        if not re.fullmatch(
            r"https://adb-[0-9]+\.[0-9]+\.azuredatabricks\.net/?", target.databricks_workspace_url
        ):
            raise ValueError("Invalid databricks_workspace_url in target state.")
        if not re.fullmatch(r"[0-9]+", target.databricks_workspace_id):
            raise ValueError("Invalid databricks_workspace_id in target state.")
        if not re.fullmatch(
            r"https://azpg08[a-z0-9]+\.blob\.core\.windows\.net/?", target.storage_account_url
        ):
            raise ValueError("Invalid storage_account_url in target state.")
        if target.artifacts_container_name != "mlops-artifacts":
            raise ValueError("Invalid artifacts_container_name in target state.")
        return target


def load_target_from_tofu_output() -> Target:
    """Read OpenTofu outputs from the local infrastructure directory."""
    result = subprocess.run(
        ["tofu", "-chdir=infra", "output", "-json"],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    return Target.from_tofu_outputs(raw)


def verify_target_with_azure_cli(target: Target) -> None:
    """Verify storage account and region with a read-only Azure CLI request."""
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
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    metadata = json.loads(result.stdout)
    if (
        not isinstance(metadata, dict)
        or metadata.get("location") != target.location
        or not isinstance(metadata.get("endpoint"), str)
        or metadata.get("endpoint", "").rstrip("/") != target.storage_account_url.rstrip("/")
    ):
        raise ValueError(
            "Azure storage account scope, region, or endpoint differs from experiment state."
        )


def sigmoid(z: float) -> float:
    """Compute standard sigmoid function for binary probabilities."""
    return 1.0 / (1.0 + math.exp(-max(min(z, 20.0), -20.0)))


def train_logistic_classifier(
    data: list[dict[str, float]],
    learning_rate: float,
    l2_penalty: float,
    epochs: int = 150,
) -> tuple[dict[str, float], float, ModelMetrics]:
    """Train a binary classifier and calculate evaluation metrics."""
    weights = {f: 0.01 for f in FEATURE_NAMES}
    bias = 0.0

    # Gradient descent optimization
    for _ in range(epochs):
        for row in data:
            z = sum(weights[f] * (row[f] / 100.0) for f in FEATURE_NAMES) + bias
            pred = sigmoid(z)
            actual = row["outage_risk"]
            err = pred - actual
            for f in FEATURE_NAMES:
                reg = l2_penalty * weights[f]
                weights[f] -= learning_rate * (err * (row[f] / 100.0) + reg)
            bias -= learning_rate * err

    # Compute classification metrics on training dataset
    tp = fp = tn = fn = 0
    scores: list[float] = []
    labels: list[float] = []

    for row in data:
        z = sum(weights[f] * (row[f] / 100.0) for f in FEATURE_NAMES) + bias
        prob = sigmoid(z)
        pred_label = 1.0 if prob >= 0.5 else 0.0
        actual = row["outage_risk"]
        scores.append(prob)
        labels.append(actual)

        if pred_label == 1.0 and actual == 1.0:
            tp += 1
        elif pred_label == 1.0 and actual == 0.0:
            fp += 1
        elif pred_label == 0.0 and actual == 0.0:
            tn += 1
        else:
            fn += 1

    total = tp + tn + fp + fn
    accuracy = round((tp + tn) / total, 4) if total else 0.0
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 1.0
    f1 = round(2.0 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0
    roc_auc = round(min(1.0, 0.5 + (tp * tn) / max(1, (tp + fn) * (tn + fp))), 4)

    metrics = ModelMetrics(
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1_score=f1,
        roc_auc=roc_auc,
    )
    return weights, bias, metrics


def predict_outage_risk(
    features: dict[str, float],
    weights: dict[str, float],
    bias: float,
) -> float:
    """Predict failure risk probability for a single telemetry record."""
    z = sum(weights[f] * (features[f] / 100.0) for f in FEATURE_NAMES) + bias
    return round(sigmoid(z), 4)


def execute_mlops_lifecycle(
    ws: WorkspaceClient,
    run_id: str,
) -> tuple[MLOpsSummary, list[CandidateModelRun]]:
    """Train candidate models, log to MLflow, and register winning champion model."""
    exp_name = f"/Shared/azpg08-anomaly-forecast-{run_id}"
    exp = ws.experiments.create_experiment(name=exp_name)
    experiment_id = exp.experiment_id or f"exp-{uuid4().hex[:8]}"

    candidates = [
        {
            "name": "baseline-linear-v1",
            "lr": 0.02,
            "l2": 0.1,
            "epochs": 100,
        },
        {
            "name": "tuned-elasticnet-v2",
            "lr": 0.08,
            "l2": 0.005,
            "epochs": 200,
        },
    ]

    model_runs: list[CandidateModelRun] = []
    best_f1 = -1.0
    champion_model_name = ""
    champion_weights: dict[str, float] = {}
    champion_bias = 0.0
    champion_run_id = ""

    for candidate in candidates:
        model_name = candidate["name"]
        lr = float(candidate["lr"])
        l2 = float(candidate["l2"])
        epochs = int(candidate["epochs"])

        weights, bias, metrics = train_logistic_classifier(
            data=BENCHMARK_TELEMETRY,
            learning_rate=lr,
            l2_penalty=l2,
            epochs=epochs,
        )

        run = ws.experiments.create_run(
            experiment_id=experiment_id,
            run_name=model_name,
        )
        run_record_id: str = (
            run.run.info.run_id
            if run.run and run.run.info and run.run.info.run_id
            else f"run-{uuid4().hex[:6]}"
        )

        ws.experiments.log_param(key="learning_rate", value=str(lr), run_id=run_record_id)
        ws.experiments.log_param(key="l2_penalty", value=str(l2), run_id=run_record_id)
        ws.experiments.log_param(key="epochs", value=str(epochs), run_id=run_record_id)

        timestamp_ms = int(time.time() * 1000)
        for metric_key, metric_val in [
            ("accuracy", metrics.accuracy),
            ("precision", metrics.precision),
            ("recall", metrics.recall),
            ("f1_score", metrics.f1_score),
            ("roc_auc", metrics.roc_auc),
        ]:
            ws.experiments.log_metric(
                key=metric_key,
                value=metric_val,
                timestamp=timestamp_ms,
                run_id=run_record_id,
            )

        candidate_run = CandidateModelRun(
            model_name=model_name,
            run_id=run_record_id,
            hyperparameters={
                "learning_rate": str(lr),
                "l2_penalty": str(l2),
                "epochs": str(epochs),
            },
            metrics=metrics,
        )
        model_runs.append(candidate_run)

        if metrics.f1_score > best_f1:
            best_f1 = metrics.f1_score
            champion_model_name = model_name
            champion_weights = weights
            champion_bias = bias
            champion_run_id = run_record_id

    # Register champion model into Databricks MLflow Model Registry
    registry_name = f"azpg08-vm-anomaly-model-{run_id}"
    ws.model_registry.create_model(
        name=registry_name,
        description="Production VM outage risk prediction model.",
    )
    mv = ws.model_registry.create_model_version(
        name=registry_name,
        source=f"runs:/{champion_run_id}/model",
        run_id=champion_run_id,
        description="Champion model promoted via automated CI/CD pipeline.",
    )
    version: str = (
        mv.model_version.version if mv.model_version and mv.model_version.version else "1"
    )

    ws.model_registry.transition_stage(
        name=registry_name,
        version=version,
        stage="Production",
        archive_existing_versions=True,
    )

    holdout_pred = predict_outage_risk(HOLDOUT_TEST_RECORD, champion_weights, champion_bias)

    summary = MLOpsSummary(
        run_id=run_id,
        experiment_id=experiment_id,
        experiment_name=exp_name,
        registered_model_name=registry_name,
        champion_model_name=champion_model_name,
        model_version=version,
        stage="Production",
        champion_f1_score=best_f1,
        holdout_prediction_score=holdout_pred,
        total_candidate_models=len(model_runs),
        created_at=datetime.now(UTC).isoformat(),
    )
    return summary, model_runs


def persist_mlops_records(
    blob_service: BlobServiceClient,
    container_name: str,
    run_id: str,
    summary: MLOpsSummary,
    runs: list[CandidateModelRun],
    uploaded_blobs: list[str],
) -> list[str]:
    """Persist structured MLOps summary and candidate run metrics to blob storage."""
    container_client = blob_service.get_container_client(container_name)
    try:
        container_client.create_container()
    except AzureError as err:
        status = getattr(err, "status_code", None)
        if status not in (403, 409) and "ContainerAlreadyExists" not in str(err):
            raise

    saved: list[str] = []
    summary_path = f"runs/{run_id}/mlops_summary.json"
    data = summary.model_dump_json(indent=2).encode("utf-8")
    container_client.upload_blob(name=summary_path, data=data, overwrite=True)
    uploaded_blobs.append(summary_path)
    saved.append(summary_path)

    runs_path = f"runs/{run_id}/candidate_runs.json"
    runs_data = json.dumps([r.model_dump() for r in runs], indent=2).encode("utf-8")
    container_client.upload_blob(name=runs_path, data=runs_data, overwrite=True)
    uploaded_blobs.append(runs_path)
    saved.append(runs_path)

    return saved


def verify_mlops_records(
    blob_service: BlobServiceClient,
    container_name: str,
    saved_blobs: list[str],
) -> None:
    """Verify persisted MLOps reports in blob storage for completeness and valid JSON."""
    if len(saved_blobs) != 2:
        raise ValueError(f"Expected exactly 2 persisted evaluation files, got {len(saved_blobs)}.")

    container_client = blob_service.get_container_client(container_name)
    for blob_name in saved_blobs:
        data = container_client.download_blob(blob_name).readall()
        if not data:
            raise ValueError(f"Evaluation report {blob_name} was empty.")
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, (dict, list)):
            raise ValueError(f"Invalid JSON format in {blob_name}.")


def teardown_mlops_artifacts(
    ws: WorkspaceClient,
    blob_service: BlobServiceClient,
    container_name: str,
    summary: MLOpsSummary,
    uploaded_blobs: list[str],
) -> None:
    """Clean up temporary Databricks experiments, registered models, and storage blobs."""
    try:
        ws.model_registry.delete_model(name=summary.registered_model_name)
    except Exception as err:
        print(f"Notice: Model registry deletion skipped: {err}", file=sys.stderr)

    try:
        ws.experiments.delete_experiment(experiment_id=summary.experiment_id)
    except Exception as err:
        print(f"Notice: Experiment deletion skipped: {err}", file=sys.stderr)

    container_client = blob_service.get_container_client(container_name)
    failures: list[str] = []
    for blob_name in uploaded_blobs:
        try:
            container_client.delete_blob(blob_name)
        except AzureError as err:
            failures.append(f"{blob_name} ({err})")
    if failures:
        joined = ", ".join(failures)
        raise RuntimeError(f"Failed to delete temporary MLOps blobs: {joined}")


def display_mlops_report(summary: MLOpsSummary, runs: list[CandidateModelRun]) -> None:
    """Print formatted MLOps tracking and model registry governance report to console."""
    print("\n" + "=" * 80)
    print(f"DATABRICKS MLOPS GOVERNANCE REPORT: RUN ID {summary.run_id}")
    print("=" * 80)
    print(f"Experiment Name       : {summary.experiment_name}")
    print(f"Registered Model Name : {summary.registered_model_name}")
    print(f"Promoted Champion     : {summary.champion_model_name} (v{summary.model_version})")
    print(f"Registry Stage        : {summary.stage}")
    print(f"Champion F1 Score     : {summary.champion_f1_score:.4f}")
    print(f"Holdout Anomaly Risk  : {summary.holdout_prediction_score * 100:.1f}%")
    print("-" * 80)
    print("CANDIDATE MODEL COMPARISON:")
    for r in runs:
        m = r.metrics
        champion_tag = " [CHAMPION]" if r.model_name == summary.champion_model_name else ""
        print(
            f"  * {r.model_name}{champion_tag}:\n"
            f"      Accuracy={m.accuracy:.4f}, Precision={m.precision:.4f}, "
            f"Recall={m.recall:.4f}, F1={m.f1_score:.4f}, ROC-AUC={m.roc_auc:.4f}"
        )
    print("=" * 80 + "\n")


def run_pipeline(target: Target) -> None:
    """Execute complete MLOps experiment tracking, model registry promotion, and cleanup."""
    print("\nTarget Experiment Scope:")
    print(f"Subscription ID           : {target.subscription_id}")
    print(f"Resource Group            : {target.resource_group_name}")
    print(f"Location                  : {target.location}")
    print(f"Databricks Workspace URL  : {target.databricks_workspace_url}")
    print(f"Databricks Workspace ID   : {target.databricks_workspace_id}")
    print(f"Storage Account URL       : {target.storage_account_url}")
    print(f"Artifacts Container       : {target.artifacts_container_name}")

    credential = DefaultAzureCredential()
    blob_service = BlobServiceClient(account_url=target.storage_account_url, credential=credential)
    ws = WorkspaceClient(
        host=target.databricks_workspace_url,
        azure_workspace_resource_id=target.databricks_workspace_id,
    )

    run_id = uuid4().hex[:8]
    uploaded_blobs: list[str] = []
    summary: MLOpsSummary | None = None

    try:
        print("\nStarting Databricks MLflow candidate training and experiment logging...")
        summary, runs = execute_mlops_lifecycle(ws=ws, run_id=run_id)
        display_mlops_report(summary, runs)

        print("Persisting MLOps report and candidate metrics to storage...")
        saved = persist_mlops_records(
            blob_service=blob_service,
            container_name=target.artifacts_container_name,
            run_id=run_id,
            summary=summary,
            runs=runs,
            uploaded_blobs=uploaded_blobs,
        )

        print(f"Verifying {len(saved)} persisted MLOps report files in lakehouse container...")
        verify_mlops_records(
            blob_service=blob_service,
            container_name=target.artifacts_container_name,
            saved_blobs=saved,
        )
        print("Verification passed.")

        if summary.champion_f1_score < 0.70:
            raise ValueError(
                f"Champion model F1 score {summary.champion_f1_score:.4f} is "
                "below required 0.70 threshold."
            )

    finally:
        if summary:
            print("\nTearing down temporary Databricks MLflow and storage artifacts...")
            teardown_mlops_artifacts(
                ws=ws,
                blob_service=blob_service,
                container_name=target.artifacts_container_name,
                summary=summary,
                uploaded_blobs=uploaded_blobs,
            )
            print("Cleaned up all temporary MLOps artifacts.")


def main() -> int:
    """Script entry point."""
    try:
        target = load_target_from_tofu_output()
        verify_target_with_azure_cli(target)
    except (subprocess.CalledProcessError, ValueError, KeyError) as err:
        print(f"Target validation failed: {err}", file=sys.stderr)
        return 1

    prompt = (
        f"Proceed with Azure Databricks MLOps execution against '{target.resource_group_name}'? "
        "Type the resource group name to confirm: "
    )
    try:
        confirmation = input(prompt)
    except KeyboardInterrupt, EOFError:
        print("\nAborted.", file=sys.stderr)
        return 1

    if confirmation.strip() != target.resource_group_name:
        print("Confirmation does not match resource group. Aborted.", file=sys.stderr)
        return 1

    try:
        run_pipeline(target)
    except Exception as err:
        print(f"MLOps pipeline failed: {err}", file=sys.stderr)
        return 1

    print("Azure Databricks MLOps and Model Registry pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
