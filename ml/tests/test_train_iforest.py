import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import pytest

from ml.train_iforest import (
    FEATURE_COLUMNS,
    REQUIRED_COLUMNS,
    TrainingExample,
    load_dataset,
    train_and_evaluate,
)


def _write_dataset(path: Path, rows: int = 100) -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for index in range(rows):
            writer.writerow(
                {
                    "observed_at": (start + timedelta(days=index)).isoformat(),
                    "txid": f"{index:064x}",
                    "input_count": 1 if index % 3 else 100,
                    "output_count": 2 if index % 4 else 100,
                    "output_total_satoshis": 1_000 if index % 5 else 10_000_000_000,
                    "fee_satoshis": 500 if index % 7 else 5_000_000,
                    "label": 1 if index % 5 == 0 else 0,
                }
            )


def test_load_dataset_validates_rows_and_hashes_source_file(tmp_path: Path) -> None:
    dataset = tmp_path / "transactions.csv"
    _write_dataset(dataset)
    rows, digest = load_dataset(dataset)
    assert len(rows) == 100
    assert len(digest) == 64
    assert rows[0].observed_at < rows[-1].observed_at
    assert len(FEATURE_COLUMNS) == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_at", "2024-01-01T00:00:00"),
        ("txid", "invalid"),
        ("fee_satoshis", "-1"),
        ("label", "unknown"),
    ],
)
def test_load_dataset_rejects_malformed_rows(tmp_path: Path, field: str, value: str) -> None:
    dataset = tmp_path / "transactions.csv"
    _write_dataset(dataset)
    with dataset.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames
    rows[0][field] = value
    with dataset.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="Invalid dataset row"):
        load_dataset(dataset)


def test_load_dataset_rejects_duplicate_transaction_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "transactions.csv"
    _write_dataset(dataset)
    with dataset.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames
    rows[1]["txid"] = rows[0]["txid"]
    with dataset.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="duplicate txid"):
        load_dataset(dataset)


def test_training_uses_chronological_split_and_writes_provenance(tmp_path: Path) -> None:
    dataset = tmp_path / "transactions.csv"
    _write_dataset(dataset)
    rows, digest = load_dataset(dataset)
    output = tmp_path / "artifacts"

    report = train_and_evaluate(
        rows,
        digest,
        "Licensed research dataset reference",
        "CC BY 4.0",
        "iforest-test-1",
        output,
    )

    assert report["calibrated"] is False
    assert report["training"]["uses_labels_for_fitting"] is False
    assert report["training"]["train_end"] < report["training"]["test_start"]
    assert report["evaluation"]["holdout_rows"] == 20
    assert report["dataset"]["sha256"] == digest
    assert (output / "isolation-forest-iforest-test-1.joblib").is_file()
    persisted_report = (output / "isolation-forest-iforest-test-1.json").read_text(encoding="utf-8")
    assert '"score_interpretation": "Anomaly ranking only; not a fraud probability or proof of wrongdoing."' in persisted_report
    assert joblib.load(output / "isolation-forest-iforest-test-1.joblib") is not None


def test_training_does_not_split_rows_with_identical_timestamps(tmp_path: Path) -> None:
    dataset = tmp_path / "transactions.csv"
    _write_dataset(dataset)
    with dataset.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames
    rows[80]["observed_at"] = rows[79]["observed_at"]
    with dataset.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    examples, digest = load_dataset(dataset)
    report = train_and_evaluate(examples, digest, "source", "license", "iforest-grouped-time", tmp_path / "models")
    assert report["training"]["train_rows"] == 81
    assert report["training"]["train_end"] < report["training"]["test_start"]


def test_training_requires_provenance_and_both_holdout_label_classes(tmp_path: Path) -> None:
    rows = [
        TrainingExample(
            observed_at=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
            txid=f"{index:064x}",
            input_count=1,
            output_count=1,
            output_total_satoshis=100,
            fee_satoshis=10,
            label=0,
        )
        for index in range(20)
    ]
    with pytest.raises(ValueError, match="source and license"):
        train_and_evaluate(rows, "0" * 64, "", "", "model-1", tmp_path)
    with pytest.raises(ValueError, match="both label classes"):
        train_and_evaluate(rows, "0" * 64, "dataset", "license", "model-1", tmp_path)
