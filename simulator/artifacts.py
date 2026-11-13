import hashlib
import platform
import tempfile
from importlib.metadata import version
from pathlib import Path

from simulator.config import GENERATOR_VERSION, GeneratorConfig
from simulator.generator import canonical, generate
from simulator.report import render_incidents, summarize

OPERATIONAL_FILES = ("customers", "methods", "payments", "attempts", "features")


def export_dataset(config: GeneratorConfig, output: Path) -> dict:
    """Write a complete new directory, then atomically publish it. Never overwrite a run."""
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    data = generate(config)
    baseline = generate(config.model_copy(update={"incident_strength": 0.0}))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".payrevive-dataset-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for directory in ["operational", "truth", "reports"]:
            (stage / directory).mkdir()
        for name in OPERATIONAL_FILES:
            write_records(stage / f"operational/{name}.jsonl", getattr(data, name))
        write_records(stage / "truth/labels.jsonl", data.labels)
        write_records(stage / "truth/incidents.jsonl", data.incidents)
        summary = summarize(data, baseline)
        (stage / "reports/summary.json").write_bytes(canonical(summary))
        (stage / "config.json").write_bytes(canonical(config.model_dump(mode="json")))
        render_incidents(data, baseline, stage / "reports/incidents.png")
        files = {
            str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "dataset_id": data.dataset_id,
            "generator_version": GENERATOR_VERSION,
            "python_version": platform.python_version(),
            "matplotlib_version": version("matplotlib"),
            "numpy_version": version("numpy"),
            "synthetic_only": True,
            "model_input_contract": "Features.model_inputs() only; IDs are join metadata",
            "files": files,
        }
        (stage / "manifest.json").write_bytes(canonical(manifest))
        # The caller must choose an unused path; the final rename exposes only a complete export.
        stage.rename(output)
    return summary


def write_records(path: Path, records) -> None:
    with path.open("wb") as handle:
        for record in records:
            handle.write(canonical(record.model_dump(mode="json")))
