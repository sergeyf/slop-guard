"""Regression tests for build helpers and background behavior."""

import importlib.util
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

EXT_DIR = Path(__file__).parent.parent
DIST_DIR = EXT_DIR / "dist"


def _load_module(module_name: str, path: Path):
    """Import a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundle_default_repo_path_points_to_checkout_root():
    """bundle.py should default to the current checkout root."""
    module = _load_module("slop_guard_ext_bundle_test", EXT_DIR / "bundle.py")
    assert module.default_repo_path() == EXT_DIR.parent.resolve()


def test_bundle_needs_refresh_when_source_is_newer(tmp_path, monkeypatch):
    """build.py should rebuild bundles when source files are newer than outputs."""
    module = _load_module("slop_guard_ext_build_test", EXT_DIR / "build.py")

    ext_dir = tmp_path / "ext"
    repo_dir = tmp_path / "repo"
    src_dir = repo_dir / "src" / "slop_guard"
    ext_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    bundle_script = ext_dir / "bundle.py"
    bundle_script.write_text("# bundle helper\n", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text(
        '[project]\nversion = "0.0.1"\n', encoding="utf-8"
    )
    (src_dir / "__init__.py").write_text(
        "PACKAGE_VERSION = '0.0.1'\n", encoding="utf-8"
    )

    bundle_js = ext_dir / "python_bundle.js"
    pyodide_js = ext_dir / "pyodide.js"
    version_marker = ext_dir / ".pyodide-version"
    bundle_js.write_text("// bundle\n", encoding="utf-8")
    pyodide_js.write_text("// pyodide\n", encoding="utf-8")
    version_marker.write_text(module.PYODIDE_VERSION, encoding="utf-8")

    monkeypatch.setattr(module, "EXT_DIR", ext_dir)
    monkeypatch.setattr(module, "DEFAULT_REPO", repo_dir)

    old_timestamp = 1_700_000_000
    new_timestamp = old_timestamp + 100
    for path in (bundle_js, pyodide_js, version_marker):
        os.utime(path, (old_timestamp, old_timestamp))
    for path in (bundle_script, repo_dir / "pyproject.toml", src_dir / "__init__.py"):
        os.utime(path, (new_timestamp, new_timestamp))

    assert module.bundle_needs_refresh(repo_dir) is True


def test_build_outputs_drop_remote_font_dependencies(build_extensions):
    """Built popup assets and manifests should not reference Google Fonts."""
    popup_html = (DIST_DIR / "chrome" / "popup.html").read_text(encoding="utf-8")
    chrome_manifest = json.loads(
        (DIST_DIR / "chrome" / "manifest.json").read_text(encoding="utf-8")
    )
    firefox_manifest = json.loads(
        (DIST_DIR / "firefox" / "manifest.json").read_text(encoding="utf-8")
    )

    assert "fonts.googleapis.com" not in popup_html
    assert "fonts.gstatic.com" not in popup_html
    assert (
        "fonts.googleapis.com"
        not in chrome_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "fonts.gstatic.com"
        not in chrome_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "fonts.googleapis.com"
        not in firefox_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "fonts.gstatic.com"
        not in firefox_manifest["content_security_policy"]["extension_pages"]
    )
    assert "blob:" not in chrome_manifest["content_security_policy"]["extension_pages"]
    assert "blob:" not in firefox_manifest["content_security_policy"]["extension_pages"]
    assert (
        "cdn.jsdelivr.net"
        not in chrome_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "files.pythonhosted.org"
        not in chrome_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "cdn.jsdelivr.net"
        not in firefox_manifest["content_security_policy"]["extension_pages"]
    )
    assert (
        "files.pythonhosted.org"
        not in firefox_manifest["content_security_policy"]["extension_pages"]
    )
    assert "alarms" in chrome_manifest["permissions"]
    assert "alarms" in firefox_manifest["permissions"]
    assert "update_url" not in firefox_manifest["browser_specific_settings"]["gecko"]


def test_normalize_https_base_url_requires_https():
    """Self-hosted Firefox updates must use an HTTPS base URL."""
    module = _load_module("slop_guard_ext_build_https_test", EXT_DIR / "build.py")

    with pytest.raises(ValueError, match="https://"):
        module.normalize_https_base_url("http://updates.example.test/slop-guard/")


def test_write_firefox_manifest_embeds_update_url(tmp_path, monkeypatch):
    """build.py should inject update_url only into the built Firefox manifest."""
    module = _load_module("slop_guard_ext_build_manifest_test", EXT_DIR / "build.py")

    ext_dir = tmp_path / "ext"
    ext_dir.mkdir(parents=True)
    manifest_path = ext_dir / "manifest.firefox.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "version": "1.2.3",
                "browser_specific_settings": {
                    "gecko": {
                        "id": "slop-guard@example.com",
                        "strict_min_version": "109.0",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "EXT_DIR", ext_dir)
    dest = tmp_path / "dist" / "firefox"
    dest.mkdir(parents=True)

    manifest = module.write_firefox_manifest(
        dest,
        update_base_url="https://updates.example.test/slop-guard/firefox",
    )
    written = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))

    expected_url = "https://updates.example.test/slop-guard/firefox/updates.json"
    assert (
        manifest["browser_specific_settings"]["gecko"]["update_url"] == expected_url
    )
    assert (
        written["browser_specific_settings"]["gecko"]["update_url"] == expected_url
    )


def test_create_firefox_selfhost_artifacts_writes_updates_manifest(tmp_path):
    """Self-hosted Firefox mode should emit an unsigned XPI plus updates.json."""
    module = _load_module("slop_guard_ext_build_selfhost_test", EXT_DIR / "build.py")

    firefox_dir = tmp_path / "firefox"
    firefox_dir.mkdir(parents=True)
    (firefox_dir / "popup.html").write_text("<html></html>\n", encoding="utf-8")
    (firefox_dir / "manifest.json").write_text("{}", encoding="utf-8")

    selfhost_dir = module.create_firefox_selfhost_artifacts(
        tmp_path,
        firefox_dir,
        manifest={
            "version": "1.2.3",
            "browser_specific_settings": {
                "gecko": {
                    "id": "slop-guard@example.com",
                    "strict_min_version": "109.0",
                }
            },
        },
        update_base_url="https://updates.example.test/slop-guard/firefox/",
    )

    xpi_path = selfhost_dir / "slop-guard-firefox.xpi"
    updates_path = selfhost_dir / "updates.json"
    updates = json.loads(updates_path.read_text(encoding="utf-8"))

    assert xpi_path.exists()
    assert updates == {
        "addons": {
            "slop-guard@example.com": {
                "updates": [
                    {
                        "version": "1.2.3",
                        "update_link": (
                            "https://updates.example.test/slop-guard/firefox/"
                            "slop-guard-firefox.xpi"
                        ),
                        "applications": {
                            "gecko": {
                                "strict_min_version": "109.0",
                            }
                        },
                    }
                ]
            }
        }
    }


def test_background_context_menu_badge_clears_via_alarm():
    """The pending-selection badge should clear itself without opening the popup."""
    background_js = EXT_DIR / "background.js"

    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=True)
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        page.evaluate(
            """
window.__storage = {};
window.__badgeCalls = [];
window.__badgeColors = [];
window.__alarms = [];
window.__clearedAlarms = [];

window.chrome = {
  runtime: {
    onInstalled: { addListener: (cb) => { window.__onInstalled = cb; } },
    onMessage: { addListener: () => {} },
    lastError: null
  },
  contextMenus: {
    create: () => {},
    onClicked: { addListener: (cb) => { window.__onClicked = cb; } }
  },
  storage: {
    local: {
      set: async (payload) => { Object.assign(window.__storage, payload); }
    }
  },
  action: {
    setBadgeText: async (payload) => { window.__badgeCalls.push(payload); },
    setBadgeBackgroundColor: async (payload) => { window.__badgeColors.push(payload); }
  },
  alarms: {
    create: (name, info) => { window.__alarms.push({ name, info }); },
    clear: (name) => { window.__clearedAlarms.push(name); },
    onAlarm: { addListener: (cb) => { window.__onAlarm = cb; } }
  },
  tabs: {
    onUpdated: { addListener: (cb) => { window.__onUpdated = cb; } },
    onRemoved: { addListener: (cb) => { window.__onRemoved = cb; } }
  }
};
            """
        )
        page.add_script_tag(path=str(background_js))

        page.evaluate(
            "window.__onClicked({ menuItemId: 'slop-guard-check', selectionText: 'sample' }, { id: 42 })"
        )

        storage = page.evaluate("window.__storage")
        assert storage["pendingText"] == "sample"
        assert storage["pendingTextTabId"] == 42

        badge_calls = page.evaluate("window.__badgeCalls")
        assert badge_calls[0] == {"tabId": 42, "text": "!"}

        alarms = page.evaluate("window.__alarms")
        assert alarms == [
            {"name": "slop-guard-clear-badge:42", "info": {"delayInMinutes": 1}}
        ]

        page.evaluate("window.__onAlarm({ name: 'slop-guard-clear-badge:42' })")
        badge_calls = page.evaluate("window.__badgeCalls")
        assert {"tabId": 42, "text": ""} in badge_calls

        browser.close()
