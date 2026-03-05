#!/usr/bin/env python3
"""Build browser-specific extension packages for Chrome and Firefox.

Usage:
    uv run build.py [--target chrome|firefox|all] [--repo PATH]

Produces dist/chrome/ and/or dist/firefox/ with the correct manifest
and Pyodide configuration for each browser. Creates submission-ready
ZIP files in dist/.

Both Chrome and Firefox bundle Pyodide locally.
"""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PYODIDE_VERSION = "0.27.7"
PYODIDE_CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full"

# Minimal Pyodide files needed to run offline.
PYODIDE_ASSETS = [
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]

EXT_DIR = Path(__file__).parent
DEFAULT_REPO = EXT_DIR.parent
SHARED_FILES = [
    "popup.html",
    "popup.js",
    "background.js",
    "python_bundle.js",
    "pyodide.js",
]
SHARED_DIRS = ["icons"]
FIREFOX_SELFHOST_DIR = "firefox-selfhost"
FIREFOX_SELFHOST_XPI = "slop-guard-firefox.xpi"
FIREFOX_UPDATES_MANIFEST = "updates.json"


def default_repo_path() -> Path:
    """Return the default repository root for local builds."""
    return DEFAULT_REPO


def _bundle_inputs(repo: Path) -> list[Path]:
    """Return the files that affect the generated Python bundle."""
    inputs = [
        EXT_DIR / "bundle.py",
        repo / "pyproject.toml",
    ]
    src_dir = repo / "src" / "slop_guard"
    if src_dir.is_dir():
        inputs.extend(
            path
            for path in src_dir.rglob("*")
            if path.is_file() and path.suffix in {".py", ".jsonl"}
        )
    return inputs


def bundle_needs_refresh(repo: Path) -> bool:
    """Return whether python_bundle.js or pyodide.js should be regenerated."""
    bundle_js = EXT_DIR / "python_bundle.js"
    pyodide_js = EXT_DIR / "pyodide.js"
    version_marker = EXT_DIR / ".pyodide-version"
    outputs = [bundle_js, pyodide_js, version_marker]

    if any(not path.exists() for path in outputs):
        return True
    if version_marker.read_text(encoding="utf-8").strip() != PYODIDE_VERSION:
        return True

    inputs = _bundle_inputs(repo)
    if not inputs:
        return True

    newest_input = max(path.stat().st_mtime for path in inputs if path.exists())
    oldest_output = min(path.stat().st_mtime for path in outputs)

    # Custom repositories should always regenerate the bundle instead of
    # reusing whatever was last built from the default checkout.
    return (
        repo.resolve() != default_repo_path().resolve() or newest_input > oldest_output
    )


def ensure_bundle(repo: Path) -> None:
    """Run bundle.py when the generated bundle is missing or stale."""
    if not bundle_needs_refresh(repo):
        return

    print("Refreshing python bundle...")
    subprocess.check_call(
        [sys.executable, str(EXT_DIR / "bundle.py"), str(repo)],
        cwd=EXT_DIR,
    )


def download_pyodide_assets(dest: Path) -> None:
    """Download the Pyodide runtime files for local/offline use."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in PYODIDE_ASSETS:
        target = dest / name
        if target.exists():
            print(f"  Already present: {name}")
            continue
        url = f"{PYODIDE_CDN}/{name}"
        print(f"  Downloading {name} ...")
        urllib.request.urlretrieve(url, str(target))
        size_kb = target.stat().st_size // 1024
        print(f"    {size_kb:,} KB")


def copy_shared(dest: Path) -> None:
    """Copy shared extension files into a target build directory."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in SHARED_FILES:
        src = EXT_DIR / name
        if not src.exists():
            raise FileNotFoundError(f"Missing required file: {src}")
        shutil.copy2(src, dest / name)
    for name in SHARED_DIRS:
        src = EXT_DIR / name
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)


def read_json(path: Path) -> dict:
    """Return a JSON object loaded from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    """Write a JSON object with a trailing newline."""
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def normalize_https_base_url(value: str) -> str:
    """Return a normalized HTTPS base URL for self-hosted Firefox updates."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("--firefox-update-base-url cannot be empty")

    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("--firefox-update-base-url must be an https:// URL")
    if parsed.query or parsed.fragment:
        raise ValueError("--firefox-update-base-url cannot contain a query or fragment")

    path = parsed.path.rstrip("/")
    normalized_path = f"{path}/" if path else "/"
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, normalized_path, "", "", "")
    )


def build_https_url(base_url: str, leaf: str) -> str:
    """Join a normalized HTTPS base URL with a single filename."""
    return urllib.parse.urljoin(base_url, leaf)


def write_config(dest: Path, *, local_pyodide: bool) -> None:
    """Write config.js with build-specific Pyodide settings."""
    if local_pyodide:
        config = {
            "PYODIDE_INDEX_URL": "__EXTENSION_URL__pyodide/",
            "PYODIDE_LOCAL": True,
        }
    else:
        config = {
            "PYODIDE_INDEX_URL": f"{PYODIDE_CDN}/",
            "PYODIDE_LOCAL": False,
        }

    js = (
        "// Auto-generated by build.py - do not edit.\n"
        f"const EXT_CONFIG = {json.dumps(config, indent=2)};\n"
    )
    (dest / "config.js").write_text(js, encoding="utf-8")


def write_firefox_manifest(dest: Path, *, update_base_url: str | None = None) -> dict:
    """Write Firefox's manifest.json, optionally embedding a self-hosted update URL."""
    manifest = read_json(EXT_DIR / "manifest.firefox.json")
    gecko = manifest.setdefault("browser_specific_settings", {}).setdefault("gecko", {})

    if update_base_url is None:
        gecko.pop("update_url", None)
    else:
        gecko["update_url"] = build_https_url(
            normalize_https_base_url(update_base_url),
            FIREFOX_UPDATES_MANIFEST,
        )

    write_json(dest / "manifest.json", manifest)
    return manifest


def patch_popup_html(dest: Path) -> None:
    """Inject config.js into popup.html before the runtime scripts."""
    html_path = dest / "popup.html"
    html = html_path.read_text(encoding="utf-8")
    html = html.replace(
        '<script src="pyodide.js"></script>',
        '<script src="config.js"></script>\n  <script src="pyodide.js"></script>',
    )
    html_path.write_text(html, encoding="utf-8")


def build_chrome(dist: Path) -> Path:
    """Build the Chrome/Edge/Brave extension with locally bundled Pyodide."""
    dest = dist / "chrome"
    if dest.exists():
        shutil.rmtree(dest)

    print("\n-- Building Chrome extension --")
    copy_shared(dest)
    shutil.copy2(EXT_DIR / "manifest.chrome.json", dest / "manifest.json")
    write_config(dest, local_pyodide=True)
    patch_popup_html(dest)
    print("  Downloading Pyodide assets for local bundling ...")
    download_pyodide_assets(dest / "pyodide")

    print(f"  Output: {dest}")
    return dest


def build_firefox(dist: Path, *, update_base_url: str | None = None) -> tuple[Path, dict]:
    """Build the Firefox extension with locally bundled Pyodide."""
    dest = dist / "firefox"
    if dest.exists():
        shutil.rmtree(dest)

    print("\n-- Building Firefox extension --")
    copy_shared(dest)
    manifest = write_firefox_manifest(dest, update_base_url=update_base_url)
    write_config(dest, local_pyodide=True)
    patch_popup_html(dest)

    print("  Downloading Pyodide assets for local bundling ...")
    download_pyodide_assets(dest / "pyodide")

    print(f"  Output: {dest}")
    return dest, manifest


def create_zip(src_dir: Path, zip_path: Path) -> None:
    """Create a ZIP file from a directory for store submission."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  ZIP: {zip_path} ({size_mb:.1f} MB)")


def firefox_selfhost_update_manifest(
    manifest: dict,
    *,
    update_base_url: str,
) -> dict:
    """Return the Firefox updates.json payload for a self-hosted signed build."""
    gecko = manifest.get("browser_specific_settings", {}).get("gecko", {})
    addon_id = gecko.get("id")
    version = manifest.get("version")
    if not addon_id or not version:
        raise ValueError("Firefox manifest must contain browser_specific_settings.gecko.id and version")

    compat = {}
    for key in ("strict_min_version", "strict_max_version"):
        if gecko.get(key):
            compat[key] = str(gecko[key])

    update = {
        "version": str(version),
        "update_link": build_https_url(
            normalize_https_base_url(update_base_url),
            FIREFOX_SELFHOST_XPI,
        ),
    }
    if compat:
        update["applications"] = {"gecko": compat}

    return {
        "addons": {
            str(addon_id): {
                "updates": [update],
            }
        }
    }


def create_firefox_selfhost_artifacts(
    dist: Path,
    firefox_dir: Path,
    *,
    manifest: dict,
    update_base_url: str,
) -> Path:
    """Create the unsigned XPI and updates.json for self-hosted Firefox updates."""
    dest = dist / FIREFOX_SELFHOST_DIR
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    xpi_path = dest / FIREFOX_SELFHOST_XPI
    create_zip(firefox_dir, xpi_path)

    updates_path = dest / FIREFOX_UPDATES_MANIFEST
    write_json(
        updates_path,
        firefox_selfhost_update_manifest(
            manifest,
            update_base_url=update_base_url,
        ),
    )

    print(f"  Self-hosted Firefox XPI: {xpi_path}")
    print(f"  Firefox update manifest: {updates_path}")
    return dest


def create_source_archive(dist: Path, repo: Path) -> None:
    """Create a source code archive for AMO review."""
    zip_path = dist / "slop-guard-source.zip"
    print("\n-- Creating source archive for AMO review --")

    source_files = [
        "config.js",
        "bundle.py",
        "build.py",
        "popup.html",
        "popup.js",
        "background.js",
        "manifest.chrome.json",
        "manifest.firefox.json",
        "update.sh",
        "update.ps1",
        "README.md",
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in source_files:
            src = EXT_DIR / name
            if src.exists():
                zf.write(src, f"slop-guard-ext/{name}")

        for icon in (EXT_DIR / "icons").glob("*.png"):
            zf.write(icon, f"slop-guard-ext/icons/{icon.name}")

        repo_src = repo / "src" / "slop_guard"
        if repo_src.is_dir():
            for path in sorted(repo_src.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(repo)
                    zf.write(path, f"slop-guard/{rel}")

        pyproject = repo / "pyproject.toml"
        if pyproject.exists():
            zf.write(pyproject, "slop-guard/pyproject.toml")

    size_kb = zip_path.stat().st_size // 1024
    print(f"  Source archive: {zip_path} ({size_kb} KB)")


def main() -> None:
    """Build the requested browser targets."""
    parser = argparse.ArgumentParser(description="Build Slop Guard extension")
    parser.add_argument(
        "--target",
        choices=["chrome", "firefox", "all"],
        default="all",
        help="Which browser to build for (default: all)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=default_repo_path(),
        help="Path to slop-guard repository root",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip creating ZIP archives",
    )
    parser.add_argument(
        "--firefox-update-base-url",
        help=(
            "HTTPS base URL for a self-hosted Firefox update channel. "
            "When set, build.py embeds browser_specific_settings.gecko.update_url "
            "into the built Firefox manifest and emits dist/firefox-selfhost/."
        ),
    )
    args = parser.parse_args()

    repo = args.repo.resolve()
    ensure_bundle(repo)

    dist = EXT_DIR / "dist"
    dist.mkdir(exist_ok=True)
    targets = ["chrome", "firefox"] if args.target == "all" else [args.target]
    firefox_update_base_url = None
    if args.firefox_update_base_url is not None:
        if "firefox" not in targets:
            parser.error("--firefox-update-base-url requires --target firefox or --target all")
        firefox_update_base_url = normalize_https_base_url(args.firefox_update_base_url)

    firefox_selfhost_dir = None

    for target in targets:
        if target == "chrome":
            dest = build_chrome(dist)
        else:
            dest, firefox_manifest = build_firefox(
                dist,
                update_base_url=firefox_update_base_url,
            )
            if firefox_update_base_url is not None:
                firefox_selfhost_dir = create_firefox_selfhost_artifacts(
                    dist,
                    dest,
                    manifest=firefox_manifest,
                    update_base_url=firefox_update_base_url,
                )

        if not args.no_zip:
            create_zip(dest, dist / f"slop-guard-{target}.zip")

    if "firefox" in targets and not args.no_zip:
        create_source_archive(dist, repo)

    print("\n-- Done --")
    print("Chrome:  Load dist/chrome/ as unpacked extension")
    print("Firefox: Load dist/firefox/manifest.json as temporary add-on")
    if firefox_selfhost_dir is not None and firefox_update_base_url is not None:
        print(
            "Firefox: Upload a signed XPI to "
            f"{build_https_url(firefox_update_base_url, FIREFOX_SELFHOST_XPI)}"
        )
        print(
            "Firefox: Upload updates.json to "
            f"{build_https_url(firefox_update_base_url, FIREFOX_UPDATES_MANIFEST)}"
        )
    if not args.no_zip:
        print(
            "AMO:     Upload dist/slop-guard-firefox.zip + dist/slop-guard-source.zip"
        )


if __name__ == "__main__":
    main()
