import hashlib
import json
import shutil

import pytest
from app.features.contracts import MODEL_INPUT_FIELDS, ObservationFeatures
from pydantic import ValidationError

from ml.data.evaluation import load_truth
from ml.data.loading import load_operational
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig
from simulator.generator import canonical, generate
from simulator.models import Features


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    path = tmp_path_factory.mktemp("verified") / "dataset"
    export_dataset(GeneratorConfig(customers=60), path)
    return path


@pytest.fixture
def editable(bundle, tmp_path):
    path = tmp_path / "dataset"
    shutil.copytree(bundle, path)
    return path


def rewrite(directory, name, rows):
    content = b"".join(canonical(row) for row in rows)
    (directory / name).write_bytes(content)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][name] = hashlib.sha256(content).hexdigest()
    manifest_path.write_bytes(canonical(manifest))


def test_simulator_and_analysis_share_one_feature_implementation():
    from app.features.contracts import FeatureValues

    assert set(FeatureValues.model_fields) == set(MODEL_INPUT_FIELDS)
    assert Features is ObservationFeatures
    row = generate(GeneratorConfig(customers=30)).features[0]
    assert tuple(row.model_inputs()) == MODEL_INPUT_FIELDS
    assert (
        not {"payment_ref", "org_id", "observed_at", "source_event_id", "reason", "status"}
        & row.model_inputs().keys()
    )


def test_operational_loader_has_no_dependency_on_hidden_truth_or_reports(editable):
    shutil.rmtree(editable / "truth")
    shutil.rmtree(editable / "reports")
    data = load_operational(editable)
    assert len(data.payments) == len(data.features) == 180
    assert not hasattr(data, "labels")
    assert (
        data.snapshot_input(data.payments[0].payment_ref).source_manifest_sha256
        == data.manifest_sha256
    )


def test_explicit_evaluation_join_uses_ids_and_is_disjoint_from_model_inputs(bundle):
    data = load_operational(bundle)
    truth = load_truth(data)
    assert len(truth.labels) == len(data.payments)
    assert len(truth.incidents) == 3
    assert [label.payment_ref for label in truth.labels] == [p.payment_ref for p in data.payments]


def test_reordered_feature_rows_are_joined_by_identity(editable):
    name = "operational/features.jsonl"
    rows = [json.loads(line) for line in (editable / name).read_text().splitlines()]
    rewrite(editable, name, list(reversed(rows)))
    data = load_operational(editable)
    assert [f.payment_ref for f in data.features] == [p.payment_ref for p in data.payments]


def test_checksum_corruption_is_rejected(editable):
    path = editable / "operational/features.jsonl"
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        load_operational(editable)


@pytest.mark.parametrize(
    "change,error",
    [
        ({"amount_minor": 1}, "metadata"),
        ({"gateway": "different_gateway"}, "metadata"),
        ({"prior_payment_count": 7, "prior_success_rate": 0.5}, "point-in-time"),
        ({"issuer": "other_issuer"}, "metadata"),
    ],
)
def test_semantic_feature_corruption_is_rejected_even_with_new_checksum(editable, change, error):
    name = "operational/features.jsonl"
    rows = [json.loads(line) for line in (editable / name).read_text().splitlines()]
    rows[0].update(change)
    rewrite(editable, name, rows)
    with pytest.raises(ValueError, match=error):
        load_operational(editable)


def test_hidden_label_cannot_be_smuggled_into_feature_columns(editable):
    name = "operational/features.jsonl"
    rows = [json.loads(line) for line in (editable / name).read_text().splitlines()]
    rows[0]["true_failure_category"] = "insufficient_funds"
    rewrite(editable, name, rows)
    with pytest.raises(ValidationError):
        load_operational(editable)


def test_manifest_path_escape_is_rejected(editable):
    manifest_path = editable / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../../private"] = "0" * 64
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="file set"):
        load_operational(editable)


def test_symlink_escape_is_rejected_before_reading_artifact(editable, tmp_path):
    path = editable / "operational/features.jsonl"
    external = tmp_path / "outside.jsonl"
    path.rename(external)
    path.symlink_to(external)
    with pytest.raises(ValueError, match="inside"):
        load_operational(editable)


def test_evaluation_labels_require_corresponding_observations(editable):
    name = "truth/labels.jsonl"
    rows = [json.loads(line) for line in (editable / name).read_text().splitlines()]
    rows[0]["source_event_id"] = "different_event"
    rewrite(editable, name, rows)
    with pytest.raises(ValueError, match="alignment"):
        load_truth(load_operational(editable))


@pytest.mark.parametrize(
    "changes",
    [
        {"gateway_latency_ms": -1},
        {"hour_utc": 24},
        {"amount_minor": True},
        {"prior_payment_count": 0, "prior_success_rate": 0.5},
        {"prior_payment_count": 1, "prior_success_rate": None},
        {"prior_success_rate": float("nan")},
    ],
)
def test_invalid_feature_values_are_rejected(changes):
    values = generate(GeneratorConfig(customers=30)).features[0].model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        ObservationFeatures(**values)
