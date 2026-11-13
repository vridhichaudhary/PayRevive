import hashlib
import importlib
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

MODULES = (
    "app.features.contracts",
    "ml.data.loading",
    "ml.data.evaluation",
    "ml.analysis.audit",
    "ml.analysis.plots",
    "ml.analysis.provenance",
)


def environment_record() -> dict:
    dependencies = {}
    for name in ("pydantic", "matplotlib", "numpy", "pandas", "nbclient", "nbconvert", "ipykernel"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    sources = {
        name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
        for name in MODULES
    }
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "dependencies": dependencies,
        "source_module_sha256": sources,
        "git_commit": commit,
        "working_tree_dirty": dirty,
    }
