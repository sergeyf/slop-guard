/* Slop Guard – popup controller */

const ext = globalThis.browser ?? globalThis.chrome;
const CIRC = 2 * Math.PI * 34; // circumference of score ring (r=34)
const LAST_REPORT_STORAGE_KEY = "lastReport";
const DEFAULT_CAPTURE_MAX_CHARS = 100000;

const BAND_COLORS = {
  clean: "#4ade80",
  light: "#a3e635",
  moderate: "#fbbf24",
  heavy: "#f97316",
  saturated: "#ef4444",
};

// Resolve Pyodide index URL from build config (config.js).
// Falls back to CDN if config.js is not present (dev/unbundled mode).
function resolvePyodideIndexURL() {
  if (typeof EXT_CONFIG !== "undefined" && EXT_CONFIG.PYODIDE_INDEX_URL) {
    const url = EXT_CONFIG.PYODIDE_INDEX_URL;
    if (url.startsWith("__EXTENSION_URL__")) {
      if (ext?.runtime?.getURL) {
        return ext.runtime.getURL(url.replace("__EXTENSION_URL__", ""));
      }
      return url.replace("__EXTENSION_URL__", "");
    }
    return url;
  }
  return "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
}

// DOM refs
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const versionLabel = document.getElementById("versionLabel");
const inputText = document.getElementById("inputText");
const analyzeBtn = document.getElementById("analyzeBtn");
const grabSelBtn = document.getElementById("grabSelBtn");
const grabPageBtn = document.getElementById("grabPageBtn");
const clearBtn = document.getElementById("clearBtn");
const scoreSection = document.getElementById("scoreSection");
const scoreArc = document.getElementById("scoreArc");
const scoreNumber = document.getElementById("scoreNumber");
const scoreBand = document.getElementById("scoreBand");
const wordCount = document.getElementById("wordCount");
const totalPenalty = document.getElementById("totalPenalty");
const density = document.getElementById("density");
const violationsSection = document.getElementById("violationsSection");
const adviceList = document.getElementById("adviceList");
const countsGrid = document.getElementById("countsGrid");
const detailToggle = document.getElementById("detailToggle");
const violationDetail = document.getElementById("violationDetail");

let pyodide = null;
let ready = false;
let runtimeMode = "popup";
let latestSource = null;

// ── Initialization ──────────────────────────────────────────────────────────

async function init() {
  try {
    const connectedToBackground = await tryInitBackgroundRuntime();
    if (connectedToBackground) {
      ready = true;
      runtimeMode = "background";
      enableButtons();
      setStatus("ready", "Ready");
      await restoreLastReport();
      await checkPendingText();
      return;
    }

    await initPopupRuntime();
  } catch (err) {
    console.error("Init failed:", err);
    setStatus("error", `Init failed: ${err.message}`);
  }
}

async function tryInitBackgroundRuntime() {
  if (!hasExtensionRuntime()) {
    return false;
  }

  setStatus("loading", "Connecting runtime…");
  const response = await sendBackgroundMessage({ type: "SG_INIT" });
  if (!response || !response.ok || !response.version) {
    return false;
  }

  versionLabel.textContent = `v${response.version}`;
  return true;
}

async function initPopupRuntime() {
  if (typeof loadPyodide === "undefined") {
    setStatus("error", "pyodide.js not found — run update.sh or update.ps1 first");
    versionLabel.textContent = "setup needed";
    return;
  }

  const indexURL = resolvePyodideIndexURL();
  setStatus("loading", "Loading Pyodide runtime…");
  pyodide = await loadPyodide({ indexURL });

  if (typeof PYTHON_FILES === "undefined") {
    setStatus("error", "python_bundle.js not found — run update.sh or update.ps1 first");
    versionLabel.textContent = "setup needed";
    return;
  }

  setStatus("loading", "Writing slop-guard to filesystem…");
  writePythonFiles();

  setStatus("loading", "Importing slop-guard…");
  await pyodide.runPythonAsync(`
import slop_guard
_sg_version = slop_guard.PACKAGE_VERSION
  `);

  const versionHandle = pyodide.globals.get("_sg_version");
  const version = String(versionHandle);
  if (versionHandle && typeof versionHandle.destroy === "function") {
    versionHandle.destroy();
  }
  versionLabel.textContent = `v${version}`;

  ready = true;
  runtimeMode = "popup";
  enableButtons();
  setStatus("ready", "Ready");
  await restoreLastReport();
  await checkPendingText();
}

function writePythonFiles() {
  // Detect Python version to find site-packages path
  const pyVer = pyodide.runPython(
    'import sys; f"{sys.version_info.major}.{sys.version_info.minor}"',
  );
  const sitePackages = `/lib/python${pyVer}/site-packages`;

  // Ensure directory structure
  const dirs = new Set();
  for (const relPath of Object.keys(PYTHON_FILES)) {
    const parts = relPath.split("/");
    for (let i = 1; i < parts.length; i += 1) {
      dirs.add(parts.slice(0, i).join("/"));
    }
  }
  for (const dir of [...dirs].sort()) {
    const fullDir = `${sitePackages}/${dir}`;
    try {
      pyodide.FS.mkdirTree(fullDir);
    } catch (_) {
      // Directory already exists.
    }
  }

  // Write files
  for (const [relPath, content] of Object.entries(PYTHON_FILES)) {
    pyodide.FS.writeFile(`${sitePackages}/${relPath}`, content, {
      encoding: "utf8",
    });
  }
}

// ── Analysis ────────────────────────────────────────────────────────────────

async function analyze(text, source = null) {
  if (!ready || !text.trim()) {
    return;
  }

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing…";
  setStatus("loading", "Analyzing…");

  try {
    const result = runtimeMode === "background"
      ? await analyzeViaBackground(text)
      : await analyzeViaPopup(text);

    renderResult(result);
    await setLastReport({
      capturedAt: new Date().toISOString(),
      source: normalizeSource(source ?? latestSource),
      result,
    });
    setStatus("ready", "Ready");
  } catch (err) {
    console.error("Analysis error:", err);
    setStatus("error", `Error: ${err.message}`);
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
  }
}

async function analyzeViaPopup(text) {
  pyodide.globals.set("_input_text", text);
  await pyodide.runPythonAsync(`
import json as _json
_result = _json.dumps(slop_guard.analyze(_input_text))
  `);
  const resultHandle = pyodide.globals.get("_result");
  const result = JSON.parse(String(resultHandle));
  if (resultHandle && typeof resultHandle.destroy === "function") {
    resultHandle.destroy();
  }
  try {
    pyodide.globals.delete("_input_text");
    pyodide.globals.delete("_result");
  } catch (_) {
    // Ignore cleanup failures.
  }
  return result;
}

async function analyzeViaBackground(text) {
  const response = await sendBackgroundMessage({ type: "SG_ANALYZE", text });
  if (!response || !response.ok) {
    const message = response && response.error
      ? response.error
      : "Background runtime unavailable";
    throw new Error(message);
  }
  return response.result;
}

// ── Rendering ───────────────────────────────────────────────────────────────

function renderResult(result) {
  const color = BAND_COLORS[result.band] || BAND_COLORS.moderate;

  // Score ring
  const offset = CIRC * (1 - result.score / 100);
  scoreArc.style.strokeDashoffset = offset;
  scoreArc.style.stroke = color;
  scoreNumber.textContent = result.score;
  scoreNumber.style.color = color;

  // Band label
  scoreBand.textContent = result.band;
  scoreBand.style.color = color;

  // Stats
  wordCount.textContent = result.word_count;
  totalPenalty.textContent = result.total_penalty;
  density.textContent = result.density;

  scoreSection.classList.add("visible");

  // Advice
  adviceList.innerHTML = "";
  if (result.advice && result.advice.length) {
    for (const tip of result.advice) {
      const li = document.createElement("li");
      li.textContent = tip;
      adviceList.appendChild(li);
    }
  } else {
    const li = document.createElement("li");
    li.textContent = "No issues found.";
    li.style.color = "#4ade80";
    adviceList.appendChild(li);
  }

  // Category counts
  countsGrid.innerHTML = "";
  if (result.counts) {
    const entries = Object.entries(result.counts).sort((a, b) => b[1] - a[1]);
    for (const [key, val] of entries) {
      const row = document.createElement("div");
      row.className = "count-row";
      const label = document.createElement("span");
      label.className = "count-label";
      label.textContent = key.replace(/_/g, " ");
      const value = document.createElement("span");
      value.className = `count-value${val === 0 ? " zero" : ""}`;
      value.textContent = val;
      row.appendChild(label);
      row.appendChild(value);
      countsGrid.appendChild(row);
    }
  }

  // Violation details
  violationDetail.innerHTML = "";
  violationDetail.classList.remove("visible");
  detailToggle.textContent = "Show all violations";
  if (result.violations && result.violations.length) {
    detailToggle.style.display = "";
    for (const violation of result.violations) {
      const item = document.createElement("div");
      item.className = "violation-item";
      item.innerHTML = `<span class="v-match">${esc(violation.match)}</span> `
        + `<span class="v-penalty">${violation.penalty}</span>`
        + `<div class="v-context">${esc(violation.context)}</div>`;
      violationDetail.appendChild(item);
    }
  } else {
    detailToggle.style.display = "none";
  }

  violationsSection.classList.add("visible");
}

function esc(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
}

// ── UI Helpers ──────────────────────────────────────────────────────────────

function setStatus(state, msg) {
  statusDot.className = `status-dot ${state}`;
  statusText.textContent = msg;
}

function enableButtons() {
  analyzeBtn.disabled = false;
  grabSelBtn.disabled = false;
  grabPageBtn.disabled = false;
}

function normalizeSource(source) {
  return {
    kind: source?.kind || "manual",
    warning: source?.warning || null,
    title: source?.title || null,
    url: source?.url || null,
  };
}

function sanitizeMaxChars(value) {
  const parsed = Number.parseInt(String(value ?? DEFAULT_CAPTURE_MAX_CHARS), 10);
  if (!Number.isFinite(parsed) || parsed < 1000) {
    return DEFAULT_CAPTURE_MAX_CHARS;
  }
  return Math.min(parsed, 500000);
}

function callExtensionApi(fn, ...args) {
  return new Promise((resolve, reject) => {
    let settled = false;

    const settleFromPromise = (maybePromise) => {
      if (!maybePromise || typeof maybePromise.then !== "function") {
        return false;
      }
      maybePromise.then(
        (value) => {
          if (!settled) {
            settled = true;
            resolve(value);
          }
        },
        (error) => {
          if (!settled) {
            settled = true;
            reject(error);
          }
        },
      );
      return true;
    };

    try {
      const maybePromise = fn(...args, (value) => {
        if (settled) {
          return;
        }
        const runtimeError = ext?.runtime?.lastError;
        if (runtimeError) {
          settled = true;
          reject(new Error(runtimeError.message || String(runtimeError)));
          return;
        }
        settled = true;
        resolve(value);
      });
      if (settleFromPromise(maybePromise)) {
        return;
      }
      if (typeof maybePromise !== "undefined" && !settled) {
        settled = true;
        resolve(maybePromise);
      }
    } catch (callbackError) {
      try {
        const maybePromise = fn(...args);
        if (settleFromPromise(maybePromise)) {
          return;
        }
        if (!settled) {
          settled = true;
          resolve(maybePromise);
        }
      } catch (fallbackError) {
        if (!settled) {
          settled = true;
          reject(fallbackError || callbackError);
        }
      }
    }
  });
}

function hasExtensionRuntime() {
  return !!resolveRuntimeBridge();
}

function resolveRuntimeBridge() {
  if (
    typeof globalThis !== "undefined"
    && globalThis.__SG_BACKGROUND_RUNTIME__
    && typeof globalThis.__SG_BACKGROUND_RUNTIME__.sendMessage === "function"
  ) {
    return globalThis.__SG_BACKGROUND_RUNTIME__;
  }

  if (ext?.runtime?.sendMessage) {
    return ext.runtime;
  }

  return null;
}

function sendBackgroundMessage(message) {
  const runtimeBridge = resolveRuntimeBridge();
  if (!runtimeBridge) {
    return Promise.resolve(null);
  }

  return callExtensionApi(runtimeBridge.sendMessage.bind(runtimeBridge), message)
    .then((response) => response || null)
    .catch(() => null);
}

async function getLastReport() {
  if (!ext?.storage?.local?.get) {
    return null;
  }
  try {
    const data = await callExtensionApi(
      ext.storage.local.get.bind(ext.storage.local),
      { [LAST_REPORT_STORAGE_KEY]: null },
    );
    return data?.[LAST_REPORT_STORAGE_KEY] || null;
  } catch (_) {
    return null;
  }
}

async function setLastReport(payload) {
  if (!ext?.storage?.local?.set) {
    return;
  }
  try {
    await callExtensionApi(
      ext.storage.local.set.bind(ext.storage.local),
      { [LAST_REPORT_STORAGE_KEY]: payload },
    );
  } catch (_) {
    // Ignore storage errors in non-extension contexts.
  }
}

async function restoreLastReport() {
  const report = await getLastReport();
  if (report?.result) {
    renderResult(report.result);
    latestSource = report.source || null;
  }
}

function injectedCapture(mode, maxChars) {
  const safeMaxChars = Number.isFinite(maxChars) && maxChars > 0 ? maxChars : 100000;

  function isTextInput(element) {
    if (!element || element.tagName !== "INPUT") {
      return false;
    }
    const type = (element.type || "").toLowerCase();
    return ["text", "search", "url", "tel", "email", "password"].includes(type);
  }

  function readSelectionFromInput(element) {
    if (!element || (!isTextInput(element) && element.tagName !== "TEXTAREA")) {
      return "";
    }
    const value = element.value || "";
    const start = Number.isInteger(element.selectionStart) ? element.selectionStart : 0;
    const end = Number.isInteger(element.selectionEnd) ? element.selectionEnd : start;
    const slice = start !== end ? value.slice(start, end) : value;
    return slice.trim();
  }

  function readEditableText(element) {
    if (!element) {
      return "";
    }

    if (element.tagName === "TEXTAREA" || isTextInput(element)) {
      return readSelectionFromInput(element) || (element.value || "").trim();
    }

    if (element.isContentEditable) {
      const selection = (window.getSelection?.().toString() || "").trim();
      if (selection) {
        return selection;
      }
      return (element.innerText || element.textContent || "").trim();
    }

    return "";
  }

  function readPageText() {
    const bodyText = document.body?.innerText || document.documentElement?.innerText || "";
    if (bodyText && bodyText.trim()) {
      return bodyText.trim();
    }
    const fallback = document.body?.textContent || document.documentElement?.textContent || "";
    return fallback.trim();
  }

  const selectionText = (window.getSelection?.().toString() || "").trim();
  const activeText = readEditableText(document.activeElement);
  const pageText = readPageText();

  const candidates = mode === "page"
    ? [
      ["page", pageText],
      ["selection", selectionText],
      ["editor", activeText],
    ]
    : [
      ["selection", selectionText],
      ["editor", activeText],
      ["page", pageText],
    ];

  let kind = "";
  let text = "";
  for (const [candidateKind, candidateText] of candidates) {
    if (candidateText) {
      kind = candidateKind;
      text = candidateText;
      break;
    }
  }

  let warning = null;
  if (text.length > safeMaxChars) {
    text = text.slice(0, safeMaxChars);
    warning = `Clipped to ${safeMaxChars.toLocaleString()} characters.`;
  }

  return {
    kind,
    text,
    warning,
    title: document.title || "",
    url: location.href,
  };
}

async function captureFromActiveTab(mode) {
  if (!ext?.tabs?.query || !ext?.scripting?.executeScript) {
    setStatus("ready", "Cannot access page — paste text manually");
    return;
  }

  setStatus("loading", mode === "selection" ? "Capturing selection…" : "Capturing page text…");

  try {
    const tabs = await callExtensionApi(
      ext.tabs.query.bind(ext.tabs),
      { active: true, currentWindow: true },
    );
    const tab = tabs?.[0];
    if (!tab?.id) {
      setStatus("ready", "No active tab found");
      return;
    }

    const results = await callExtensionApi(
      ext.scripting.executeScript.bind(ext.scripting),
      {
        target: { tabId: tab.id },
        func: injectedCapture,
        args: [mode, sanitizeMaxChars(DEFAULT_CAPTURE_MAX_CHARS)],
      },
    );
    const payload = results?.[0]?.result;
    if (payload?.text) {
      inputText.value = payload.text;
      latestSource = payload;
      const warning = payload.warning ? ` ${payload.warning}` : "";
      const kind = payload.kind || mode;
      setStatus("ready", `Captured ${kind} text.${warning}`.trim());
      return;
    }

    setStatus(
      "ready",
      mode === "selection"
        ? "No selection, editor text, or page text found"
        : "No page text, selection, or editor text found",
    );
  } catch (err) {
    console.error("Capture failed:", err);
    setStatus("ready", "Cannot access page — paste text manually");
  }
}

async function checkPendingText() {
  if (!ext?.storage?.local?.get) {
    return;
  }

  try {
    const data = await callExtensionApi(
      ext.storage.local.get.bind(ext.storage.local),
      "pendingText",
    );
    const pendingText = data?.pendingText;
    if (!pendingText) {
      return;
    }

    inputText.value = pendingText;
    latestSource = { kind: "context-menu", warning: null, title: null, url: null };
    if (ext.storage.local.remove) {
      await callExtensionApi(ext.storage.local.remove.bind(ext.storage.local), "pendingText");
    }
    if (ext?.action?.setBadgeText) {
      await callExtensionApi(ext.action.setBadgeText.bind(ext.action), { text: "" });
    }
    await analyze(pendingText, latestSource);
  } catch (_) {
    // Ignore storage errors in non-extension contexts.
  }
}

// ── Events ──────────────────────────────────────────────────────────────────

analyzeBtn.addEventListener("click", () => {
  const source = latestSource || { kind: "manual", warning: null, title: null, url: null };
  void analyze(inputText.value, source);
});

grabSelBtn.addEventListener("click", () => {
  void captureFromActiveTab("selection");
});

grabPageBtn.addEventListener("click", () => {
  void captureFromActiveTab("page");
});

clearBtn.addEventListener("click", () => {
  inputText.value = "";
  latestSource = null;
  scoreSection.classList.remove("visible");
  violationsSection.classList.remove("visible");
  void setLastReport(null);
});

detailToggle.addEventListener("click", () => {
  const visible = violationDetail.classList.toggle("visible");
  detailToggle.textContent = visible ? "Hide violations" : "Show all violations";
});

inputText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    const source = latestSource || { kind: "manual", warning: null, title: null, url: null };
    void analyze(inputText.value, source);
  }
});

// ── Boot ────────────────────────────────────────────────────────────────────

void init();
