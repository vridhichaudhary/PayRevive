"""Execute a notebook in a fresh, explicitly selected environment; never modify its source."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from ml.data.loading import load_operational
from simulator.generator import canonical


def execute_notebook(
    dataset: Path,
    output: Path,
    notebook: Path | None = None,
    until: str | None = None,
    *,
    challenge: Path | None = None,
) -> dict:
    # Optional analysis dependencies are deliberately imported only in this executable path.
    import nbformat
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager
    from nbclient import NotebookClient
    from nbconvert import HTMLExporter

    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    root = Path(__file__).resolve().parents[2]
    notebook = (notebook or root / "notebooks/01_data_and_feature_audit.ipynb").resolve()
    dataset, output = dataset.resolve(), output.absolute()
    verified = load_operational(dataset)
    source = notebook.read_bytes()
    nb = nbformat.reads(source.decode(), as_version=4)
    report_kind = nb.metadata.get("payrevive_report", "audit")
    if report_kind not in ("audit", "detection", "diagnosis"):
        raise ValueError(f"Unsupported notebook report: {report_kind}")
    if report_kind != "audit" and until is not None:
        raise ValueError("--until applies only to the data audit notebook")
    if challenge is not None and report_kind != "diagnosis":
        raise ValueError("--challenge applies only to the diagnosis notebook")
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
            cell.metadata.pop("execution", None)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".payrevive-kernel-", dir=output.parent) as kernel_tmp:
        with (
            tempfile.TemporaryDirectory(
                prefix=".payrevive-notebook-", dir=output.parent
            ) as temporary,
            tempfile.TemporaryDirectory(
                prefix="prv-ipc-", dir="/tmp" if os.name == "posix" else None
            ) as ipc_tmp,
        ):
            stage, kernel_root = Path(temporary), Path(kernel_tmp)
            name = "payrevive-analysis"
            spec_dir = kernel_root / "kernels" / name
            spec_dir.mkdir(parents=True)
            (spec_dir / "kernel.json").write_text(
                json.dumps(
                    {
                        "argv": [
                            sys.executable,
                            "-m",
                            "ipykernel_launcher",
                            "-f",
                            "{connection_file}",
                        ],
                        "display_name": "PayRevive locked analysis",
                        "language": "python",
                    }
                )
            )
            manager = KernelManager(
                kernel_name=name,
                transport="ipc" if os.name == "posix" else "tcp",
                ip=str(Path(ipc_tmp) / "channels") if os.name == "posix" else "127.0.0.1",
                connection_file=str(kernel_root / "connection.json"),
                kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(kernel_root / "kernels")]),
            )
            environment = {
                **os.environ,
                "PAYREVIVE_NOTEBOOK_DATASET": str(dataset),
                "PAYREVIVE_NOTEBOOK_OUTPUT": str(stage / report_kind),
                "PAYREVIVE_EXPECTED_MANIFEST": verified.manifest_sha256,
                "PAYREVIVE_NOTEBOOK_UNTIL": until or "",
                "PAYREVIVE_NOTEBOOK_CHALLENGE": str(challenge.resolve()) if challenge else "",
                "IPYTHONDIR": str(kernel_root / "ipython"),
                "JUPYTER_RUNTIME_DIR": str(kernel_root / "runtime"),
                "JUPYTER_CONFIG_DIR": str(kernel_root / "config"),
                "MPLCONFIGDIR": str(kernel_root / "mpl"),
                "XDG_CACHE_HOME": str(kernel_root / "cache"),
                "PYDEVD_DISABLE_FILE_VALIDATION": "1",
            }
            client = NotebookClient(
                nb,
                km=manager,
                kernel_name=name,
                timeout=120,
                allow_errors=False,
                record_timing=False,
                resources={"metadata": {"path": str(root)}},
            )
            try:
                client.execute(env=environment, cleanup_kc=True)
            finally:
                if client.kc is not None:
                    client.kc.stop_channels()
                    client.kc = None
                if manager.has_kernel:
                    manager.shutdown_kernel(now=True)
                manager.cleanup_resources()
            report = json.loads((stage / report_kind / f"{report_kind}.json").read_text())
            if report["environment"]["python_executable"] != sys.executable:
                raise RuntimeError("Notebook ran in a different Python environment")
            if report["provenance"]["source_manifest_sha256"] != verified.manifest_sha256:
                raise RuntimeError("Notebook report refers to a different dataset")
            nb.metadata["payrevive"] = {
                "source_manifest_sha256": verified.manifest_sha256,
                "source_notebook_sha256": hashlib.sha256(source).hexdigest(),
                "execution": "fresh locked Python kernel",
                "synthetic_only": True,
            }
            nbformat.write(nb, stage / "executed.ipynb")
            html, _ = HTMLExporter(template_name="classic").from_notebook_node(nb)
            (stage / "report.html").write_text(html)
            manifest = {
                "source_dataset_directory": str(dataset),
                "source_manifest_sha256": verified.manifest_sha256,
                "source_notebook_sha256": hashlib.sha256(source).hexdigest(),
                "source_notebook": notebook.name,
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "kernel_python": sys.executable,
                "report_kind": report_kind,
                **(
                    {"explore_until_exclusive": report["provenance"]["explore_until_exclusive"]}
                    if report_kind == "audit"
                    else {}
                ),
                "fresh_kernel": True,
                "synthetic_only": True,
                "files": {
                    str(file.relative_to(stage)): hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in sorted(stage.rglob("*"))
                    if file.is_file()
                },
            }
            (stage / "run_manifest.json").write_bytes(canonical(manifest))
            stage.rename(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute and render a PayRevive analysis notebook")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--notebook", type=Path)
    parser.add_argument("--until")
    parser.add_argument("--challenge", type=Path)
    args = parser.parse_args()
    result = execute_notebook(
        args.dataset, args.output, args.notebook, args.until, challenge=args.challenge
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_manifest_sha256": result["source_manifest_sha256"],
                "fresh_kernel": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
