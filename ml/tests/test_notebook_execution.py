import hashlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.analysis


@pytest.mark.parametrize("kind", ["audit", "detection"])
def test_clean_kernel_matches_module_results_and_preserves_source(tmp_path, kind):
    from ml.analysis.audit import run_audit
    from ml.analysis.degradation import run_detection
    from ml.notebooks.run import execute_notebook
    from simulator.artifacts import export_dataset
    from simulator.config import GeneratorConfig

    dataset, output = tmp_path / "dataset", tmp_path / "notebook"
    export_dataset(GeneratorConfig(customers=60), dataset)
    name, run = {
        "audit": ("01_data_and_feature_audit.ipynb", run_audit),
        "detection": ("02_degradation_detection.ipynb", run_detection),
    }[kind]
    notebook = Path(__file__).parents[2] / "notebooks" / name
    source = notebook.read_bytes()
    expected = run(dataset, tmp_path / "cli-report")
    manifest = execute_notebook(dataset, output, notebook if kind == "detection" else None)
    actual = json.loads((output / kind / f"{kind}.json").read_text())
    assert manifest["report_kind"] == kind
    assert actual == expected
    assert actual["environment"]["python_executable"] == sys.executable
    assert notebook.read_bytes() == source
    assert manifest["fresh_kernel"]
    executed = json.loads((output / "executed.ipynb").read_text())
    cells = [cell for cell in executed["cells"] if cell["cell_type"] == "code"]
    assert [cell["execution_count"] for cell in cells] == list(range(1, len(cells) + 1))
    assert not any(item["output_type"] == "error" for cell in cells for item in cell["outputs"])
    assert any("image/png" in item.get("data", {}) for cell in cells for item in cell["outputs"])
    assert "PayRevive" in (output / "report.html").read_text()
    for name, checksum in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum
    assert not list(tmp_path.glob(".payrevive-kernel-*"))


def test_failing_notebook_does_not_publish_stale_or_partial_results(tmp_path):
    from nbclient.exceptions import CellExecutionError

    from ml.notebooks.run import execute_notebook
    from simulator.artifacts import export_dataset
    from simulator.config import GeneratorConfig

    dataset, output = tmp_path / "dataset", tmp_path / "notebook"
    export_dataset(GeneratorConfig(customers=30), dataset)
    original = Path(__file__).parents[2] / "notebooks/01_data_and_feature_audit.ipynb"
    notebook = json.loads(original.read_text())
    first_code = next(cell for cell in notebook["cells"] if cell["cell_type"] == "code")
    first_code["source"] = ['raise RuntimeError("deliberate notebook failure")']
    first_code["outputs"] = [
        {"output_type": "stream", "name": "stdout", "text": ["fabricated cached result"]}
    ]
    first_code["execution_count"] = 99
    source = tmp_path / "failing.ipynb"
    source.write_text(json.dumps(notebook))
    before = source.read_bytes()
    with pytest.raises(CellExecutionError, match="deliberate notebook failure"):
        execute_notebook(dataset, output, source)
    assert not output.exists()
    assert source.read_bytes() == before
    assert not list(tmp_path.glob(".payrevive-kernel-*"))
