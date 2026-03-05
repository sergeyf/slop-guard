"""Test the Slop Guard popup UI and analysis in Firefox.

Uses the Firefox build (with locally bundled Pyodide) served via HTTP
to verify the extension works correctly in Firefox's rendering engine.
"""

import re

import pytest
from playwright.sync_api import sync_playwright, expect

PYODIDE_TIMEOUT = 120_000


@pytest.fixture()
def page(extension_server):
    """Launch Firefox and navigate to the popup page."""
    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=True)
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(f"{extension_server}/popup.html", wait_until="domcontentloaded")
        yield pg
        browser.close()


class TestFirefoxInit:
    """Verify Pyodide loads and initializes in Firefox."""

    def test_status_shows_ready(self, page):
        status_text = page.locator("#statusText")
        expect(status_text).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)

    def test_version_label_populated(self, page):
        version = page.locator("#versionLabel")
        expect(version).not_to_have_text("loading…", timeout=PYODIDE_TIMEOUT)
        assert version.text_content().startswith("v")

    def test_analyze_button_enabled(self, page):
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)
        expect(page.locator("#analyzeBtn")).to_be_enabled()


class TestFirefoxAnalysis:
    """Test core analysis in Firefox."""

    def _wait_ready(self, page):
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)

    def test_analyze_clean_text(self, page):
        self._wait_ready(page)
        page.fill("#inputText", "The cat sat on the mat. It was a warm day.")
        page.click("#analyzeBtn")

        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )
        score = int(page.locator("#scoreNumber").text_content())
        assert score >= 70, f"Clean text scored {score}, expected >= 70"

    def test_analyze_sloppy_text(self, page):
        self._wait_ready(page)
        sloppy = (
            "It's important to note that this is a comprehensive overview "
            "of the various aspects of this multifaceted topic. "
            "In today's rapidly evolving landscape, it's crucial to "
            "delve into the intricacies that underscore the broader "
            "implications. Furthermore, it's worth noting that this "
            "represents a paradigm shift in how we navigate the "
            "complexities of this nuanced subject. Let's dive in and "
            "explore the transformative potential of these key insights. "
            "At the end of the day, this is a testament to the power of "
            "leveraging cutting-edge solutions in our ever-changing world."
        )
        page.fill("#inputText", sloppy)
        page.click("#analyzeBtn")

        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )
        score = int(page.locator("#scoreNumber").text_content())
        assert score < 60, f"Sloppy text scored {score}, expected < 60"

    def test_violations_shown(self, page):
        self._wait_ready(page)
        sloppy = (
            "It's important to note that we must delve into these "
            "intricacies and navigate the complexities. Furthermore, "
            "this comprehensive overview showcases the multifaceted "
            "nature of leveraging key insights in this landscape."
        )
        page.fill("#inputText", sloppy)
        page.click("#analyzeBtn")

        expect(page.locator("#violationsSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )
        advice_items = page.locator("#adviceList li")
        assert advice_items.count() > 0

    def test_clear_button(self, page):
        self._wait_ready(page)
        page.fill("#inputText", "Some text to analyze.")
        page.click("#analyzeBtn")
        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )

        page.click("#clearBtn")
        assert page.locator("#inputText").input_value() == ""
        expect(page.locator("#scoreSection")).not_to_have_class(re.compile("visible"))

    def test_ctrl_enter_shortcut(self, page):
        self._wait_ready(page)
        page.fill("#inputText", "The cat sat on the mat. Simple sentence here.")
        page.locator("#inputText").press("Control+Enter")
        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )
