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


class TestFirefoxBackgroundRuntime:
    """Verify popup uses background runtime bridge when available."""

    def test_background_runtime_bridge(self, firefox_server):
        with sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.add_init_script(
                """
window.__sgMessages = [];
window.__SG_BACKGROUND_RUNTIME__ = {
  sendMessage: async (msg) => {
    window.__sgMessages.push(msg.type);
    if (msg.type === "SG_INIT") {
      return { ok: true, version: "9.9.9" };
    }
    if (msg.type === "SG_ANALYZE") {
      return {
        ok: true,
        result: {
          score: 88,
          band: "clean",
          word_count: 4,
          total_penalty: 2,
          density: 0.5,
          advice: ["stub advice"],
          counts: { sentence_level: 1 },
          violations: []
        }
      };
    }
    return { ok: false, error: "Unknown message" };
  }
};
                """
            )
            page.goto(f"{firefox_server}/popup.html", wait_until="domcontentloaded")

            expect(page.locator("#statusText")).to_have_text("Ready", timeout=10_000)
            expect(page.locator("#versionLabel")).to_have_text("v9.9.9")

            page.fill("#inputText", "Background runtime path test text.")
            page.click("#analyzeBtn")
            expect(page.locator("#scoreNumber")).to_have_text("88")

            message_types = page.evaluate("window.__sgMessages")
            assert message_types.count("SG_INIT") == 1
            assert "SG_ANALYZE" in message_types

            browser.close()


class TestFirefoxPopupUx:
    """Regression tests for popup persistence and capture UX helpers."""

    def test_restores_and_clears_last_report(self, firefox_server):
        with sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.add_init_script(
                """
window.__storage = {
  lastReport: {
    capturedAt: "2026-03-04T20:15:00.000Z",
    source: { kind: "manual", warning: null, title: "Prev", url: "https://example.com/prev" },
    result: {
      score: 77,
      band: "light",
      word_count: 12,
      total_penalty: 8,
      density: 0.67,
      advice: ["previous report"],
      counts: { sentence_level: 1 },
      violations: []
    }
  }
};

window.chrome = {
  runtime: {
    id: "slop-guard-test",
    sendMessage: async (msg) => {
      if (msg.type === "SG_INIT") {
        return { ok: true, version: "9.9.9" };
      }
      if (msg.type === "SG_ANALYZE") {
        return {
          ok: true,
          result: {
            score: 55,
            band: "moderate",
            word_count: 15,
            total_penalty: 20,
            density: 1.33,
            advice: ["fresh report"],
            counts: { sentence_level: 2 },
            violations: []
          }
        };
      }
      return { ok: false, error: "Unknown message" };
    }
  },
  storage: {
    local: {
      get: async (keys) => {
        if (typeof keys === "string") {
          return { [keys]: window.__storage[keys] };
        }
        if (keys && typeof keys === "object") {
          return Object.assign({}, keys, window.__storage);
        }
        return Object.assign({}, window.__storage);
      },
      set: async (payload) => { Object.assign(window.__storage, payload); },
      remove: async (key) => {
        const keys = Array.isArray(key) ? key : [key];
        for (const k of keys) {
          delete window.__storage[k];
        }
      }
    }
  },
  action: {
    setBadgeText: async () => {},
    setBadgeBackgroundColor: async () => {}
  },
  tabs: {
    query: async () => [{ id: 1 }]
  },
  scripting: {
    executeScript: async () => [{ result: { kind: "selection", text: "ignored", warning: null, title: "t", url: "u" } }]
  }
};
                """
            )
            page.goto(f"{firefox_server}/popup.html", wait_until="domcontentloaded")

            expect(page.locator("#statusText")).to_have_text("Ready", timeout=10_000)
            expect(page.locator("#scoreSection")).to_have_class(re.compile("visible"))
            expect(page.locator("#scoreNumber")).to_have_text("77")

            page.fill("#inputText", "new text")
            page.click("#analyzeBtn")
            expect(page.locator("#scoreNumber")).to_have_text("55")

            stored_score = page.evaluate("window.__storage.lastReport.result.score")
            assert stored_score == 55

            page.click("#clearBtn")
            cleared_report = page.evaluate("window.__storage.lastReport")
            assert cleared_report is None

            browser.close()

    def test_capture_buttons_use_smarter_capture_payload(self, firefox_server):
        with sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.add_init_script(
                """
window.__captureModes = [];
window.__storage = {};

window.chrome = {
  runtime: {
    id: "slop-guard-test",
    sendMessage: async (msg) => {
      if (msg.type === "SG_INIT") {
        return { ok: true, version: "9.9.9" };
      }
      if (msg.type === "SG_ANALYZE") {
        return {
          ok: true,
          result: {
            score: 88,
            band: "clean",
            word_count: 4,
            total_penalty: 2,
            density: 0.5,
            advice: ["stub advice"],
            counts: { sentence_level: 1 },
            violations: []
          }
        };
      }
      return { ok: false, error: "Unknown message" };
    }
  },
  storage: {
    local: {
      get: async () => ({}),
      set: async () => {},
      remove: async () => {}
    }
  },
  action: {
    setBadgeText: async () => {},
    setBadgeBackgroundColor: async () => {}
  },
  tabs: {
    query: async () => [{ id: 1 }]
  },
  scripting: {
    executeScript: async (injection) => {
      const mode = injection?.args?.[0];
      window.__captureModes.push(mode);
      if (mode === "selection") {
        return [{ result: { kind: "editor", text: "editor fallback text", warning: null, title: "Doc", url: "https://example.com/a" } }];
      }
      return [{ result: { kind: "page", text: "full page text", warning: null, title: "Doc", url: "https://example.com/a" } }];
    }
  }
};
                """
            )
            page.goto(f"{firefox_server}/popup.html", wait_until="domcontentloaded")
            expect(page.locator("#statusText")).to_have_text("Ready", timeout=10_000)

            page.click("#grabSelBtn")
            assert page.locator("#inputText").input_value() == "editor fallback text"

            page.click("#grabPageBtn")
            assert page.locator("#inputText").input_value() == "full page text"

            modes = page.evaluate("window.__captureModes")
            assert modes == ["selection", "page"]

            browser.close()

    def test_pending_text_overrides_restored_report(self, firefox_server):
        with sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.add_init_script(
                """
window.__messages = [];
window.__storage = {
  lastReport: {
    capturedAt: "2026-03-04T20:15:00.000Z",
    source: { kind: "manual", warning: null, title: "Prev", url: "https://example.com/prev" },
    result: {
      score: 77,
      band: "light",
      word_count: 12,
      total_penalty: 8,
      density: 0.67,
      advice: ["previous report"],
      counts: { sentence_level: 1 },
      violations: []
    }
  },
  pendingText: "pending context-menu text"
};

window.chrome = {
  runtime: {
    id: "slop-guard-test",
    sendMessage: async (msg) => {
      window.__messages.push(msg);
      if (msg.type === "SG_INIT") {
        return { ok: true, version: "9.9.9" };
      }
      if (msg.type === "SG_ANALYZE") {
        return {
          ok: true,
          result: {
            score: 22,
            band: "heavy",
            word_count: 3,
            total_penalty: 30,
            density: 10.0,
            advice: ["pending result"],
            counts: { sentence_level: 3 },
            violations: []
          }
        };
      }
      return { ok: false, error: "Unknown message" };
    }
  },
  storage: {
    local: {
      get: async (keys) => {
        if (typeof keys === "string") {
          return { [keys]: window.__storage[keys] };
        }
        if (keys && typeof keys === "object") {
          return Object.assign({}, keys, window.__storage);
        }
        return Object.assign({}, window.__storage);
      },
      set: async (payload) => { Object.assign(window.__storage, payload); },
      remove: async (key) => {
        const keys = Array.isArray(key) ? key : [key];
        for (const k of keys) {
          delete window.__storage[k];
        }
      }
    }
  },
  action: {
    setBadgeText: async () => {},
    setBadgeBackgroundColor: async () => {}
  },
  tabs: {
    query: async () => [{ id: 1 }]
  },
  scripting: {
    executeScript: async () => [{ result: { kind: "selection", text: "ignored", warning: null, title: "t", url: "u" } }]
  }
};
                """
            )
            page.goto(f"{firefox_server}/popup.html", wait_until="domcontentloaded")

            expect(page.locator("#statusText")).to_have_text("Ready", timeout=10_000)
            expect(page.locator("#scoreSection")).to_have_class(re.compile("visible"))

            # Restored score (77) should be replaced by pending-text analysis score (22).
            expect(page.locator("#scoreNumber")).to_have_text("22")
            assert page.locator("#inputText").input_value() == "pending context-menu text"

            pending_after = page.evaluate("window.__storage.pendingText")
            assert pending_after is None

            stored_score = page.evaluate("window.__storage.lastReport.result.score")
            assert stored_score == 22

            analyze_messages = page.evaluate(
                "window.__messages.filter((m) => m.type === 'SG_ANALYZE').map((m) => m.text)"
            )
            assert analyze_messages == ["pending context-menu text"]

            browser.close()
