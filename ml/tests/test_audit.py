import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from ml.analysis.audit import run_audit, summarize
from ml.data.evaluation import EvaluationTruth, load_truth
from ml.data.loading import load_operational
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig


@pytest.fixture(scope="module")
def audit_dataset(tmp_path_factory):
    directory = tmp_path_factory.mktemp("audit-input") / "dataset"
    export_dataset(GeneratorConfig(customers=60), directory)
    return directory


def test_audit_counts_are_genuine_and_cutoff_is_exclusive(audit_dataset, tmp_path):
    report = run_audit(audit_dataset, tmp_path / "audit")
    assert report["population"]["initial_observations_total"] == 180
    assert report["population"]["explored_observations"] == 120
    assert report["population"]["withheld_observations"] == 60
    assert report["monthly_explored_counts"] == {"2026-01": 60, "2026-02": 60}
    assert (
        sum(row["count"] for row in report["class_distribution"])
        == report["population"]["explored_failures"]
    )
    assert len(report["feature_catalog"]) == 12
    assert not report["final_test_claim"]
    for name in ["overview.png", "temporal.png"]:
        with Image.open(tmp_path / "audit" / name) as image:
            image.verify()


def test_post_cutoff_labels_cannot_change_exploration_statistics(audit_dataset):
    data = load_operational(audit_dataset)
    truth = load_truth(data)
    cutoff = datetime(2026, 3, 1, tzinfo=UTC)
    changed = EvaluationTruth(
        tuple(
            label
            if feature.observed_at < cutoff
            else label.model_copy(update={"true_failure_category": "risk_block"})
            for label, feature in zip(truth.labels, data.features, strict=True)
        ),
        truth.incidents,
    )
    before, _ = summarize(data, truth, cutoff)
    after, _ = summarize(data, changed, cutoff)
    assert before == after


@pytest.mark.parametrize("until", ["2026-03-01", "2025-01-01T00:00:00Z", "2027-01-01T00:00:00Z"])
def test_invalid_cutoff_never_publishes_output(audit_dataset, tmp_path, until):
    with pytest.raises(ValueError):
        run_audit(audit_dataset, tmp_path / "audit", until=until)
    assert not (tmp_path / "audit").exists()


def test_mismatched_manifest_binding_and_existing_paths_are_rejected(audit_dataset, tmp_path):
    with pytest.raises(ValueError, match="declared source manifest"):
        run_audit(audit_dataset, tmp_path / "audit", expected_manifest_sha256="0" * 64)
    (tmp_path / "audit").mkdir()
    with pytest.raises(FileExistsError):
        run_audit(audit_dataset, tmp_path / "audit")


def test_committed_notebook_has_no_outputs_or_duplicate_implementations():
    path = Path(__file__).parents[2] / "notebooks/01_data_and_feature_audit.ipynb"
    notebook = json.loads(path.read_text())
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert code
    for cell in code:
        assert cell["outputs"] == [] and cell["execution_count"] is None
        tree = ast.parse("".join(cell["source"]))
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"fit", "fit_transform"}
            for node in ast.walk(tree)
        )
