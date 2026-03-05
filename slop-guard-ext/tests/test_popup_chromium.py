"""Test the Slop Guard popup UI and analysis in Chromium.

Tests load the popup via HTTP server (same code as the extension popup)
to verify Pyodide initialization and text analysis work correctly.
"""

import re

import pytest
from playwright.sync_api import sync_playwright, expect

# Pyodide loads ~10 MB WASM + compiles Python — give it time.
PYODIDE_TIMEOUT = 120_000  # 2 minutes


@pytest.fixture()
def page(extension_server):
    """Launch Chromium and navigate to the popup page."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(f"{extension_server}/popup.html", wait_until="domcontentloaded")
        yield pg
        browser.close()


class TestPyodideInit:
    """Verify the Pyodide runtime loads and slop-guard initializes."""

    def test_status_shows_ready(self, page):
        """After init, the status dot should be green and say 'Ready'."""
        status_text = page.locator("#statusText")
        expect(status_text).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)

        status_dot = page.locator("#statusDot")
        expect(status_dot).to_have_class(re.compile("ready"))

    def test_version_label_populated(self, page):
        """The version label should show the slop-guard version."""
        version = page.locator("#versionLabel")
        expect(version).not_to_have_text("loading…", timeout=PYODIDE_TIMEOUT)
        assert version.text_content().startswith("v")

    def test_analyze_button_enabled(self, page):
        """The Analyze button should be enabled once ready."""
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)
        expect(page.locator("#analyzeBtn")).to_be_enabled()


class TestAnalysis:
    """Test text analysis end-to-end via the popup UI."""

    def _wait_ready(self, page):
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)

    def test_analyze_clean_text(self, page):
        """Clean text should score high (80-100)."""
        self._wait_ready(page)

        page.fill("#inputText", "The cat sat on the mat. It was a warm day.")
        page.click("#analyzeBtn")

        score_section = page.locator("#scoreSection")
        expect(score_section).to_have_class(re.compile("visible"), timeout=30_000)

        score = int(page.locator("#scoreNumber").text_content())
        assert score >= 70, f"Clean text scored {score}, expected >= 70"

        band = page.locator("#scoreBand").text_content()
        assert band in ("clean", "light"), f"Expected clean/light band, got {band}"

    def test_analyze_sloppy_text(self, page):
        """Text with AI writing patterns should score lower."""
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

        score_section = page.locator("#scoreSection")
        expect(score_section).to_have_class(re.compile("visible"), timeout=30_000)

        score = int(page.locator("#scoreNumber").text_content())
        assert score < 60, f"Sloppy text scored {score}, expected < 60"

    def test_violations_shown(self, page):
        """Analysis of sloppy text should display violations."""
        self._wait_ready(page)

        sloppy = (
            "It's important to note that we must delve into these "
            "intricacies and navigate the complexities. Furthermore, "
            "this comprehensive overview showcases the multifaceted "
            "nature of leveraging key insights in this landscape."
        )
        page.fill("#inputText", sloppy)
        page.click("#analyzeBtn")

        violations = page.locator("#violationsSection")
        expect(violations).to_have_class(re.compile("visible"), timeout=30_000)

        advice_items = page.locator("#adviceList li")
        assert advice_items.count() > 0, "Expected at least one advice item"

    def test_score_stats_populated(self, page):
        """After analysis, word count, penalty, and density should show values."""
        self._wait_ready(page)

        page.fill("#inputText", "The quick brown fox jumps over the lazy dog. " * 5)
        page.click("#analyzeBtn")

        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )

        word_count = page.locator("#wordCount").text_content()
        assert int(word_count) > 0, "Word count should be positive"

    def test_clear_button(self, page):
        """Clear button should reset the textarea and hide results."""
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
        """Ctrl+Enter should trigger analysis."""
        self._wait_ready(page)

        page.fill("#inputText", "The cat sat on the mat. Simple sentence here.")
        page.locator("#inputText").press("Control+Enter")

        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )

    def test_empty_text_no_crash(self, page):
        """Analyzing empty text should not crash."""
        self._wait_ready(page)

        page.fill("#inputText", "")
        page.click("#analyzeBtn")

        # Should stay on ready, no error
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=5_000)

    def test_short_text_handled(self, page):
        """Very short text should be handled gracefully."""
        self._wait_ready(page)

        page.fill("#inputText", "Hello world.")
        page.click("#analyzeBtn")

        # Should complete without error
        expect(page.locator("#scoreSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )


class TestViolationDetails:
    """Test the collapsible violation detail UI."""

    def _analyze_sloppy(self, page):
        expect(page.locator("#statusText")).to_have_text("Ready", timeout=PYODIDE_TIMEOUT)
        sloppy = (
            "It's important to note that we must delve into these "
            "intricacies and navigate the complexities of this "
            "comprehensive and multifaceted landscape. Furthermore, "
            "it's crucial to leverage key insights that underscore "
            "the transformative potential of these cutting-edge solutions."
        )
        page.fill("#inputText", sloppy)
        page.click("#analyzeBtn")
        expect(page.locator("#violationsSection")).to_have_class(
            re.compile("visible"), timeout=30_000
        )

    def test_toggle_violations(self, page):
        """Clicking the toggle should show/hide violation details."""
        self._analyze_sloppy(page)

        toggle = page.locator("#detailToggle")
        detail = page.locator("#violationDetail")

        # Initially hidden
        expect(detail).not_to_have_class(re.compile("visible"))

        # Show
        toggle.click()
        expect(detail).to_have_class(re.compile("visible"))
        assert "Hide" in toggle.text_content()

        # Hide
        toggle.click()
        expect(detail).not_to_have_class(re.compile("visible"))
        assert "Show" in toggle.text_content()
