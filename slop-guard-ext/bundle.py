#!/usr/bin/env python3
"""Bundle slop-guard Python source into a JS module for the browser extension.

Usage:
    uv run bundle.py [path-to-slop-guard-repo]

If no path is given, the local repository root is used.
Re-run after pulling upstream updates to regenerate python_bundle.js.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

PYODIDE_VERSION = "0.27.7"
PYODIDE_JS_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/pyodide.js"
SKIP_FILES = {"server.py", "cli.py", "fit_cli.py", "__main__.py"}

CUSTOM_INIT = '''\
"""Browser-compatible slop-guard interface (no MCP server)."""

import json

from .analysis import (
    AnalysisDocument,
    HYPERPARAMETERS,
    band_for_score,
    compute_weighted_sum,
    deduplicate_advice,
    initial_counts,
    score_from_density,
    short_text_result,
)
from .rules import Pipeline
from .version import PACKAGE_VERSION

DEFAULT_PIPELINE = Pipeline.from_jsonl()

def analyze(text, hyperparameters=None):
    """Run all configured rules and return score, diagnostics, and advice."""
    if hyperparameters is None:
        hyperparameters = HYPERPARAMETERS
    document = AnalysisDocument.from_text(text)
    if document.word_count < hyperparameters.short_text_word_count:
        return short_text_result(
            document.word_count,
            initial_counts(),
            hyperparameters,
        )
    state = DEFAULT_PIPELINE.forward(document)
    total_penalty = sum(violation.penalty for violation in state.violations)
    weighted_sum = compute_weighted_sum(
        list(state.violations),
        state.counts,
        hyperparameters,
    )
    density = (
        weighted_sum / (document.word_count / hyperparameters.density_words_basis)
        if document.word_count > 0
        else 0.0
    )
    score = score_from_density(density, hyperparameters)
    band = band_for_score(score, hyperparameters)
    return {
        "score": score,
        "band": band,
        "word_count": document.word_count,
        "violations": [v.to_payload() for v in state.violations],
        "counts": state.counts,
        "total_penalty": total_penalty,
        "weighted_sum": round(weighted_sum, 2),
        "density": round(density, 2),
        "advice": deduplicate_advice(list(state.advice)),
    }

def check_slop(text):
    """Analyze text and return a JSON string."""
    return json.dumps(analyze(text), indent=2)
'''


def default_repo_path() -> Path:
    """Return the local repository root."""
    return Path(__file__).resolve().parent.parent


def extract_version(repo: Path) -> str:
    """Pull the version string from pyproject.toml."""
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def collect_files(src: Path) -> dict[str, str]:
    """Walk slop_guard source and collect file contents."""
    files: dict[str, str] = {}
    for path in sorted(src.rglob("*")):
        if path.suffix not in {".py", ".jsonl"}:
            continue
        rel = path.relative_to(src.parent).as_posix()
        if path.name in SKIP_FILES:
            print(f"  Skipping: {rel}")
            continue
        if rel == "slop_guard/__init__.py":
            print(f"  Skipping: {rel} (custom override)")
            continue
        print(f"  Bundling: {rel}")
        files[rel] = path.read_text(encoding="utf-8")
    return files


def download_pyodide() -> None:
    """Download pyodide.js into the extension directory if needed."""
    ext_dir = Path(__file__).parent
    target = ext_dir / "pyodide.js"
    marker = ext_dir / ".pyodide-version"

    if target.exists() and marker.exists():
        if marker.read_text(encoding="utf-8").strip() == PYODIDE_VERSION:
            print(f"pyodide.js v{PYODIDE_VERSION} already present, skipping download.")
            return

    print(f"Downloading pyodide.js v{PYODIDE_VERSION} ...")
    try:
        urllib.request.urlretrieve(PYODIDE_JS_URL, str(target))
        marker.write_text(PYODIDE_VERSION, encoding="utf-8")
        size_kb = target.stat().st_size // 1024
        print(f"  Saved pyodide.js ({size_kb} KB)")
    except Exception as error:
        if target.exists():
            print(f"  Download failed ({error}), using existing pyodide.js")
        else:
            print(f"  WARNING: Could not download pyodide.js: {error}")
            print(f"  Download manually from: {PYODIDE_JS_URL}")
            print(f"  Save to: {target}")


def main() -> None:
    """Build python_bundle.js and ensure pyodide.js is present."""
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default_repo_path()
    src = repo / "src" / "slop_guard"

    if not src.is_dir():
        print(f"Error: slop_guard source not found at {src}", file=sys.stderr)
        print(
            f"Usage: uv run {Path(sys.argv[0]).name} /path/to/slop-guard",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Bundling slop-guard from {src} ...")
    files = collect_files(src)

    version = extract_version(repo)
    print(f"  Package version: {version}")

    files["slop_guard/version.py"] = (
        f'PACKAGE_NAME = "slop-guard"\nPACKAGE_VERSION = "{version}"\n'
    )
    files["slop_guard/__init__.py"] = CUSTOM_INIT

    out = Path(__file__).parent / "python_bundle.js"
    with out.open("w", encoding="utf-8") as handle:
        handle.write("// Auto-generated by bundle.py - do not edit by hand.\n")
        handle.write("// Re-run bundle.py after upstream updates.\n")
        handle.write("const PYTHON_FILES = ")
        json.dump(files, handle, ensure_ascii=False, indent=0)
        handle.write(";\n")

    print(f"Done. Generated {out} ({len(files)} files)")
    download_pyodide()


if __name__ == "__main__":
    main()
