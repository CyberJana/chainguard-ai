"""Reproducible, offline Isolation Forest training and evaluation."""

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

FEATURE_COLUMNS = (
    "input_count",
    "output_count",
    "output_total_satoshis",
    "fee_satoshis",
)
REQUIRED_COLUMNS = ("observed_at", "txid", *FEATURE_COLUMNS, "label")
TXID_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
RANDOM_STATE = 42
TRAIN_FRACTION = 0.8


@dataclass(frozen=True)
class TrainingExample:
    observed_at: datetime
    txid: str
    input_count: int
    output_count: int
    output_total_satoshis: int
    fee_satoshis: int
    label: int


def load_dataset(path: Path) -> tuple[list[TrainingExample], str]:
    with path.open("rb") as file:
        dataset_hash = hashlib.file_digest(file, "sha256").hexdigest()
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None or not set(REQUIRED_COLUMNS).issubset(reader.fieldnames):
            raise ValueError(f"Dataset must contain columns: {', '.join(REQUIRED_COLUMNS)}")
        rows: list[TrainingExample] = []
        seen_txids: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("observed_at must include a timezone")
                txid = row["txid"].lower()
                if not TXID_PATTERN.fullmatch(txid):
                    raise ValueError("txid must be a 64-character hexadecimal transaction ID")
                if txid in seen_txids:
                    raise ValueError("duplicate txid")
                values = {
                    column: int(row[column])
                    for column in FEATURE_COLUMNS
                }
                if any(value < 0 for value in values.values()):
                    raise ValueError("feature values must be non-negative integers")
                if values["input_count"] > 2**31 - 1 or values["output_count"] > 2**31 - 1:
                    raise ValueError("input_count and output_count must fit signed 32-bit database columns")
                if values["output_total_satoshis"] > 2**63 - 1 or values["fee_satoshis"] > 2**63 - 1:
                    raise ValueError("satoshi values must fit signed 64-bit database columns")
                if row["label"] not in {"0", "1"}:
                    raise ValueError("label must be 0 (not labeled suspicious) or 1 (labeled suspicious)")
                rows.append(
                    TrainingExample(
                        observed_at=timestamp.astimezone(timezone.utc),
                        txid=txid,
                        **values,
                        label=int(row["label"]),
                    )
                )
                seen_txids.add(txid)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid dataset row {line_number}: {exc}") from exc

    rows.sort(key=lambda example: (example.observed_at, example.txid))
    if len(rows) < 20:
        raise ValueError("At least 20 unique observations are required for a chronological holdout.")
    return rows, dataset_hash


def feature_matrix(rows: Sequence[TrainingExample]) -> np.ndarray:
    return np.asarray(
        [
            [
                np.log1p(row.input_count),
                np.log1p(row.output_count),
                np.log1p(row.output_total_satoshis),
                np.log1p(row.fee_satoshis),
            ]
            for row in rows
        ],
        dtype=np.float64,
    )


def train_and_evaluate(
    rows: Sequence[TrainingExample],
    dataset_sha256: str,
    dataset_source: str,
    dataset_license: str,
    model_version: str,
    output_directory: Path,
) -> dict:
    if not dataset_source.strip() or not dataset_license.strip():
        raise ValueError("Dataset source and license must be provided for provenance.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", model_version):
        raise ValueError("Model version must be 1-80 characters using letters, digits, dot, underscore, or hyphen.")
    if len(rows) < 20:
        raise ValueError("At least 20 unique observations are required for a chronological holdout.")

    ordered_rows = sorted(rows, key=lambda example: (example.observed_at, example.txid))
    split_index = int(len(ordered_rows) * TRAIN_FRACTION)
    while (
        0 < split_index < len(ordered_rows)
        and ordered_rows[split_index - 1].observed_at == ordered_rows[split_index].observed_at
    ):
        split_index += 1
    if split_index >= len(ordered_rows):
        raise ValueError("A chronological holdout cannot split rows sharing the same observation timestamp.")
    train_rows, test_rows = ordered_rows[:split_index], ordered_rows[split_index:]
    y_test = np.asarray([row.label for row in test_rows], dtype=np.int64)
    if len(set(y_test.tolist())) != 2:
        raise ValueError("Chronological holdout must contain both label classes to calculate evaluation metrics.")

    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    model.fit(feature_matrix(train_rows))

    anomaly_scores = -model.score_samples(feature_matrix(test_rows))
    predictions = model.predict(feature_matrix(test_rows)) == -1
    tn, fp, fn, tp = confusion_matrix(y_test, predictions, labels=[0, 1]).ravel()
    metrics = {
        "average_precision": float(average_precision_score(y_test, anomaly_scores)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1": float(f1_score(y_test, predictions, zero_division=0)),
        "confusion_matrix": {"true_negative": int(tn), "false_positive": int(fp), "false_negative": int(fn), "true_positive": int(tp)},
        "holdout_rows": len(test_rows),
        "holdout_labeled_suspicious_rows": int(y_test.sum()),
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    artifact_path = output_directory / f"isolation-forest-{model_version}.joblib"
    report_path = output_directory / f"isolation-forest-{model_version}.json"
    if artifact_path.exists() or report_path.exists():
        raise FileExistsError(f"Model version {model_version} already has an artifact or report.")
    joblib.dump(model, artifact_path, compress=3)
    report = {
        "model_type": "IsolationForest",
        "model_version": model_version,
        "calibrated": False,
        "score_interpretation": "Anomaly ranking only; not a fraud probability or proof of wrongdoing.",
        "dataset": {
            "sha256": dataset_sha256,
            "source": dataset_source.strip(),
            "license": dataset_license.strip(),
            "rows": len(ordered_rows),
            "first_observation_at": ordered_rows[0].observed_at.isoformat(),
            "last_observation_at": ordered_rows[-1].observed_at.isoformat(),
        },
        "training": {
            "random_state": RANDOM_STATE,
            "train_fraction": TRAIN_FRACTION,
            "train_rows": len(train_rows),
            "train_start": train_rows[0].observed_at.isoformat(),
            "train_end": train_rows[-1].observed_at.isoformat(),
            "test_start": test_rows[0].observed_at.isoformat(),
            "test_end": test_rows[-1].observed_at.isoformat(),
            "feature_columns": list(FEATURE_COLUMNS),
            "feature_transform": "log1p for non-negative numeric features",
            "uses_labels_for_fitting": False,
            "scikit_learn_version": sklearn.__version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "evaluation": metrics,
        "artifact": artifact_path.name,
        "limitations": [
            "Evaluation metrics depend on the supplied labeled dataset and its provenance.",
            "The Isolation Forest is an anomaly baseline and is not empirically calibrated.",
            "This artifact is not loaded by the application API and must not be used for operational decisions.",
            "Only load model artifacts from trusted sources; joblib files can execute code when loaded.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and chronologically evaluate a Bitcoin Isolation Forest baseline.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dataset-source", required=True, help="Source name and citation or acquisition reference.")
    parser.add_argument("--dataset-license", required=True, help="Dataset license or permitted-use terms.")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--output-directory", type=Path, default=Path("artifacts/ml"))
    arguments = parser.parse_args(argv)

    try:
        rows, dataset_hash = load_dataset(arguments.dataset)
        report = train_and_evaluate(
            rows,
            dataset_hash,
            arguments.dataset_source,
            arguments.dataset_license,
            arguments.model_version,
            arguments.output_directory,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"model_version": report["model_version"], "evaluation": report["evaluation"], "report": str(arguments.output_directory / f"isolation-forest-{report['model_version']}.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
