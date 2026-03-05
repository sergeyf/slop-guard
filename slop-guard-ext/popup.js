/* Slop Guard popup controller. */

const ext = globalThis.browser ?? globalThis.chrome;
const CIRC = 2 * Math.PI * 34;
const LAST_REPORT_STORAGE_KEY = "lastReport";
const PENDING_TEXT_STORAGE_KEY = "pendingText";
const PENDING_TAB_ID_STORAGE_KEY = "pendingTextTabId";
const DEFAULT_CAPTURE_MAX_CHARS = 100000;
const DEFAULT_ANALYSIS_TIMEOUT_MS = 30000;
const MIN_TIMEOUT_MS = 1000;
const MAX_TIMEOUT_MS = 120000;

const BAND_COLORS = {
  clean: "#4ade80",
  light: "#a3e635",
  moderate: "#fbbf24",
  heavy: "#f97316",
  saturated: "#ef4444",
};

const VALID_BANDS = new Set(Object.keys(BAND_COLORS));
const ANALYSIS_TIMEOUT_MS = resolveAnalysisTimeoutMs();

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const versionLabel = document.getElementById("versionLabel");
const inputText = document.getElementById("inputText");
const analyzeBtn = document.getElementById("analyzeBtn");
const grabSelBtn = document.getElementById("grabSelBtn");
const grabPageBtn = document.getElementById("grabPageBtn");
const clearBtn = document.getElementById("clearBtn");
const captureNotice = document.getElementById("captureNotice");
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

function sanitizeTimeoutMs(value) {
  const parsed = Number.parseInt(String(value ?? DEFAULT_ANALYSIS_TIMEOUT_MS), 10);
  if (!Number.isFinite(parsed) || parsed < MIN_TIMEOUT_MS) {
    return DEFAULT_ANALYSIS_TIMEOUT_MS;
  }
  return Math.min(parsed, MAX_TIMEOUT_MS);
}

function resolveAnalysisTimeoutMs() {
  const search = new URLSearchParams(globalThis.location?.search || "");
  return sanitizeTimeoutMs(search.get("timeoutMs"));
}

function withTimeout(promise, timeoutMs, label) {
  return new Promise((resolve, reject) => {
    const timeoutId = globalThis.setTimeout(() => {
      reject(new Error(label));
    }, timeoutMs);

    Promise.resolve(promise).then(
      (value) => {
        globalThis.clearTimeout(timeoutId);
        resolve(value);
      },
      (error) => {
        globalThis.clearTimeout(timeoutId);
        reject(error);
      },
    );
  });
}

function cleanupPyodideGlobals(pyodideInstance, names) {
  if (!pyodideInstance?.globals) {
    return;
  }
  for (const name of names) {
    try {
      pyodideInstance.globals.delete(name);
    } catch (_) {
      // Ignore cleanup failures.
    }
  }
}

function setStatus(state, message) {
  statusDot.className = `status-dot ${state}`;
  statusText.textContent = message;
}

function showNotice(message, tone = "info") {
  if (!message) {
    clearNotice();
    return;
  }
  captureNotice.textContent = message;
  captureNotice.className = `notice ${tone} visible`;
}

function clearNotice() {
  captureNotice.textContent = "";
  captureNotice.className = "notice";
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

function requireFiniteNumber(value, fieldName) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new Error(`Analysis result has invalid ${fieldName}`);
  }
  return number;
}

function normalizeCounts(value) {
  if (value == null) {
    return {};
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Analysis result has invalid counts");
  }

  const counts = {};
  for (const [key, rawValue] of Object.entries(value)) {
    counts[key] = Math.max(0, Math.trunc(requireFiniteNumber(rawValue, `counts.${key}`)));
  }
  return counts;
}

function normalizeViolations(value) {
  if (value == null) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error("Analysis result has invalid violations");
  }

  return value.map((violation, index) => {
    if (!violation || typeof violation !== "object") {
      throw new Error(`Analysis result has invalid violations.${index}`);
    }
    return {
      match: String(violation.match ?? ""),
      context: String(violation.context ?? ""),
      penalty: requireFiniteNumber(violation.penalty ?? 0, `violations.${index}.penalty`),
    };
  });
}

function normalizeAdvice(value) {
  if (value == null) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error("Analysis result has invalid advice");
  }
  return value.map((item) => String(item));
}

function validateAnalysisResult(result) {
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    throw new Error("Analysis result is not an object");
  }

  const band = String(result.band ?? "").toLowerCase();
  if (!VALID_BANDS.has(band)) {
    throw new Error(`Analysis result has invalid band: ${band || "<missing>"}`);
  }

  const score = Math.round(requireFiniteNumber(result.score, "score"));
  if (score < 0 || score > 100) {
    throw new Error(`Analysis result has out-of-range score: ${score}`);
  }

  const validated = {
    score,
    band,
    word_count: Math.max(0, Math.trunc(requireFiniteNumber(result.word_count, "word_count"))),
    total_penalty: requireFiniteNumber(result.total_penalty, "total_penalty"),
    density: requireFiniteNumber(result.density, "density"),
    counts: normalizeCounts(result.counts),
    advice: normalizeAdvice(result.advice),
    violations: normalizeViolations(result.violations),
  };

  if (result.weighted_sum != null) {
    validated.weighted_sum = requireFiniteNumber(result.weighted_sum, "weighted_sum");
  }

  return validated;
}

function esc(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
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

async function clearActionBadge(tabId = null) {
  if (!ext?.action?.setBadgeText) {
    return;
  }
  const details = Number.isInteger(tabId) ? { tabId, text: "" } : { text: "" };
  try {
    await callExtensionApi(ext.action.setBadgeText.bind(ext.action), details);
  } catch (_) {
    // Ignore action badge cleanup failures.
  }
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
    return true;
  }
  try {
    await callExtensionApi(
      ext.storage.local.set.bind(ext.storage.local),
      { [LAST_REPORT_STORAGE_KEY]: payload },
    );
    return true;
  } catch (error) {
    console.error("Persisting the last report failed:", error);
    return false;
  }
}

function renderResult(result) {
  const color = BAND_COLORS[result.band] || BAND_COLORS.moderate;
  const offset = CIRC * (1 - result.score / 100);

  scoreArc.style.strokeDashoffset = offset;
  scoreArc.style.stroke = color;
  scoreNumber.textContent = result.score;
  scoreNumber.style.color = color;

  scoreBand.textContent = result.band;
  scoreBand.style.color = color;

  wordCount.textContent = result.word_count;
  totalPenalty.textContent = result.total_penalty;
  density.textContent = result.density;
  scoreSection.classList.add("visible");

  adviceList.innerHTML = "";
  if (result.advice.length > 0) {
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

  countsGrid.innerHTML = "";
  const entries = Object.entries(result.counts).sort((left, right) => right[1] - left[1]);
  for (const [key, value] of entries) {
    const row = document.createElement("div");
    row.className = "count-row";

    const label = document.createElement("span");
    label.className = "count-label";
    label.textContent = key.replace(/_/g, " ");

    const count = document.createElement("span");
    count.className = `count-value${value === 0 ? " zero" : ""}`;
    count.textContent = value;

    row.appendChild(label);
    row.appendChild(count);
    countsGrid.appendChild(row);
  }

  violationDetail.innerHTML = "";
  violationDetail.classList.remove("visible");
  detailToggle.textContent = "Show all violations";
  detailToggle.setAttribute("aria-expanded", "false");

  if (result.violations.length > 0) {
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

async function restoreLastReport() {
  const report = await getLastReport();
  if (!report?.result) {
    return;
  }

  try {
    const result = validateAnalysisResult(report.result);
    renderResult(result);
    latestSource = report.source || null;
    if (report.source?.warning) {
      showNotice(report.source.warning, "warning");
    }
  } catch (error) {
    console.error("Discarding invalid saved report:", error);
    latestSource = null;
    await setLastReport(null);
    showNotice("Saved report was invalid and has been discarded.", "info");
  }
}

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
  } catch (error) {
    console.error("Init failed:", error);
    setStatus("error", `Init failed: ${error.message}`);
  }
}

async function tryInitBackgroundRuntime() {
  if (!hasExtensionRuntime()) {
    return false;
  }

  setStatus("loading", "Connecting runtime...");
  const response = await sendBackgroundMessage({ type: "SG_INIT" });
  if (!response || !response.ok || !response.version) {
    return false;
  }

  versionLabel.textContent = `v${response.version}`;
  return true;
}

async function initPopupRuntime() {
  if (typeof loadPyodide === "undefined") {
    setStatus("error", "pyodide.js not found - run uv run build.py first");
    versionLabel.textContent = "setup needed";
    return;
  }

  setStatus("loading", "Loading Pyodide runtime...");
  pyodide = await loadPyodide({ indexURL: resolvePyodideIndexURL() });

  if (typeof PYTHON_FILES === "undefined") {
    setStatus("error", "python_bundle.js not found - run uv run build.py first");
    versionLabel.textContent = "setup needed";
    return;
  }

  setStatus("loading", "Writing slop-guard to the filesystem...");
  writePythonFiles();

  setStatus("loading", "Importing slop-guard...");
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
  const pyVer = pyodide.runPython(
    'import sys; f"{sys.version_info.major}.{sys.version_info.minor}"',
  );
  const sitePackages = `/lib/python${pyVer}/site-packages`;
  const dirs = new Set();

  for (const relPath of Object.keys(PYTHON_FILES)) {
    const parts = relPath.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      dirs.add(parts.slice(0, index).join("/"));
    }
  }

  for (const dir of [...dirs].sort()) {
    try {
      pyodide.FS.mkdirTree(`${sitePackages}/${dir}`);
    } catch (_) {
      // Directory already exists.
    }
  }

  for (const [relPath, content] of Object.entries(PYTHON_FILES)) {
    pyodide.FS.writeFile(`${sitePackages}/${relPath}`, content, {
      encoding: "utf8",
    });
  }
}

async function analyze(text, source = null) {
  if (!ready || !text.trim()) {
    return;
  }

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing...";
  setStatus("loading", "Analyzing...");

  try {
    const rawResult = await withTimeout(
      runtimeMode === "background" ? analyzeViaBackground(text) : analyzeViaPopup(text),
      ANALYSIS_TIMEOUT_MS,
      `Analysis timed out after ${Math.round(ANALYSIS_TIMEOUT_MS / 1000)}s.`,
    );
    const result = validateAnalysisResult(rawResult);

    renderResult(result);
    const normalizedSource = normalizeSource(source ?? latestSource);
    const saved = await setLastReport({
      capturedAt: new Date().toISOString(),
      source: normalizedSource,
      result,
    });

    latestSource = normalizedSource;
    if (normalizedSource.warning) {
      showNotice(normalizedSource.warning, "warning");
    }
    setStatus("ready", saved ? "Ready" : "Ready - result not saved");
  } catch (error) {
    console.error("Analysis error:", error);
    setStatus("error", `Error: ${error.message}`);
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
  }
}

async function analyzeViaPopup(text) {
  pyodide.globals.set("_input_text", text);
  try {
    await pyodide.runPythonAsync(`
import json as _json
_result = _json.dumps(slop_guard.analyze(_input_text))
  `);
    const resultHandle = pyodide.globals.get("_result");
    try {
      return JSON.parse(String(resultHandle));
    } finally {
      if (resultHandle && typeof resultHandle.destroy === "function") {
        resultHandle.destroy();
      }
    }
  } finally {
    cleanupPyodideGlobals(pyodide, ["_input_text", "_result"]);
  }
}

async function analyzeViaBackground(text) {
  const response = await sendBackgroundMessage({ type: "SG_ANALYZE", text });
  if (!response || !response.ok) {
    const message = response?.error || "Background runtime unavailable";
    throw new Error(message);
  }
  return response.result;
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
    setStatus("ready", "Cannot access the page - paste text manually");
    return;
  }

  setStatus("loading", mode === "selection" ? "Capturing selection..." : "Capturing page text...");

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
      if (payload.warning) {
        showNotice(payload.warning, "warning");
      } else {
        clearNotice();
      }
      setStatus("ready", `Captured ${payload.kind || mode} text.`);
      return;
    }

    clearNotice();
    setStatus(
      "ready",
      mode === "selection"
        ? "No selection, editor text, or page text found"
        : "No page text, selection, or editor text found",
    );
  } catch (error) {
    console.error("Capture failed:", error);
    setStatus("ready", "Cannot access the page - paste text manually");
  }
}

async function checkPendingText() {
  if (!ext?.storage?.local?.get) {
    return;
  }

  try {
    const data = await callExtensionApi(
      ext.storage.local.get.bind(ext.storage.local),
      {
        [PENDING_TEXT_STORAGE_KEY]: null,
        [PENDING_TAB_ID_STORAGE_KEY]: null,
      },
    );
    const pendingText = data?.[PENDING_TEXT_STORAGE_KEY];
    const pendingTabId = data?.[PENDING_TAB_ID_STORAGE_KEY];
    if (!pendingText) {
      return;
    }

    inputText.value = pendingText;
    latestSource = {
      kind: "context-menu",
      warning: null,
      title: null,
      url: null,
    };
    clearNotice();

    if (ext.storage.local.remove) {
      await callExtensionApi(
        ext.storage.local.remove.bind(ext.storage.local),
        [PENDING_TEXT_STORAGE_KEY, PENDING_TAB_ID_STORAGE_KEY],
      );
    }
    await clearActionBadge(pendingTabId);
    await analyze(pendingText, latestSource);
  } catch (_) {
    // Ignore storage errors in non-extension contexts.
  }
}

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
  clearNotice();
  void setLastReport(null).then((saved) => {
    setStatus("ready", saved ? "Ready" : "Ready - saved result could not be cleared");
  });
});

detailToggle.addEventListener("click", () => {
  const visible = violationDetail.classList.toggle("visible");
  detailToggle.textContent = visible ? "Hide violations" : "Show all violations";
  detailToggle.setAttribute("aria-expanded", visible ? "true" : "false");
});

inputText.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    const source = latestSource || { kind: "manual", warning: null, title: null, url: null };
    void analyze(inputText.value, source);
  }
});

inputText.addEventListener("input", (event) => {
  if (!event.isTrusted) {
    return;
  }
  latestSource = null;
  clearNotice();
});

void init();
