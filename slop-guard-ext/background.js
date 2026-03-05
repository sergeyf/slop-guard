/* Slop Guard background runtime and context menu support. */

const ext = globalThis.browser ?? globalThis.chrome;
const PYODIDE_CDN_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const PENDING_TEXT_STORAGE_KEY = "pendingText";
const PENDING_TAB_ID_STORAGE_KEY = "pendingTextTabId";
const BADGE_CLEAR_ALARM_PREFIX = "slop-guard-clear-badge:";
const BADGE_TTL_MINUTES = 1;

const runtimeState = {
  pyodide: null,
  version: null,
  initPromise: null,
  ready: false,
  lastInitDurationMs: null,
  pendingBadgeTabId: null,
};

function toErrorMessage(error) {
  if (!error) {
    return "Unknown error";
  }
  if (typeof error === "string") {
    return error;
  }
  return error.message || String(error);
}

function fireAndForget(fn, ...args) {
  try {
    const result = fn(...args);
    if (result && typeof result.catch === "function") {
      result.catch(() => {});
    }
  } catch (_) {
    // Ignore best-effort cleanup failures.
  }
}

function safeImportScripts(...scripts) {
  if (typeof importScripts !== "function") {
    return;
  }
  for (const script of scripts) {
    try {
      importScripts(script);
    } catch (_) {
      // Ignore missing optional scripts (for example an unbuilt source tree).
    }
  }
}

function bootstrapBackgroundAssets() {
  if (typeof importScripts !== "function") {
    return;
  }
  if (typeof EXT_CONFIG === "undefined") {
    safeImportScripts("config.js");
  }
  if (typeof loadPyodide === "undefined") {
    safeImportScripts("pyodide.js");
  }
  if (typeof PYTHON_FILES === "undefined") {
    safeImportScripts("python_bundle.js");
  }
}

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
  return PYODIDE_CDN_INDEX_URL;
}

function writePythonFiles(pyodideInstance) {
  if (typeof PYTHON_FILES === "undefined") {
    throw new Error("python_bundle.js not available in background");
  }

  const pyVer = pyodideInstance.runPython(
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
      pyodideInstance.FS.mkdirTree(`${sitePackages}/${dir}`);
    } catch (_) {
      // Directory already exists.
    }
  }

  for (const [relPath, content] of Object.entries(PYTHON_FILES)) {
    pyodideInstance.FS.writeFile(`${sitePackages}/${relPath}`, content, {
      encoding: "utf8",
    });
  }
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

function badgeAlarmName(tabId) {
  return `${BADGE_CLEAR_ALARM_PREFIX}${tabId}`;
}

function parseBadgeAlarmTabId(name) {
  if (typeof name !== "string" || !name.startsWith(BADGE_CLEAR_ALARM_PREFIX)) {
    return null;
  }
  const tabId = Number.parseInt(name.slice(BADGE_CLEAR_ALARM_PREFIX.length), 10);
  return Number.isInteger(tabId) ? tabId : null;
}

function clearPendingBadge(tabId = runtimeState.pendingBadgeTabId) {
  if (!ext?.action?.setBadgeText) {
    return;
  }
  const details = Number.isInteger(tabId) ? { tabId, text: "" } : { text: "" };
  fireAndForget(ext.action.setBadgeText.bind(ext.action), details);
}

function clearPendingIndicator(tabId = runtimeState.pendingBadgeTabId) {
  clearPendingBadge(tabId);
  if (Number.isInteger(tabId) && ext?.alarms?.clear) {
    fireAndForget(ext.alarms.clear.bind(ext.alarms), badgeAlarmName(tabId));
  }
  if (runtimeState.pendingBadgeTabId === tabId) {
    runtimeState.pendingBadgeTabId = null;
  }
}

function scheduleBadgeClear(tabId) {
  if (!Number.isInteger(tabId) || !ext?.alarms?.create) {
    return;
  }
  fireAndForget(ext.alarms.create.bind(ext.alarms), badgeAlarmName(tabId), {
    delayInMinutes: BADGE_TTL_MINUTES,
  });
}

async function ensureRuntime() {
  if (runtimeState.ready && runtimeState.pyodide) {
    return {
      coldStart: false,
      version: runtimeState.version,
      initDurationMs: runtimeState.lastInitDurationMs,
    };
  }

  if (runtimeState.initPromise) {
    await runtimeState.initPromise;
    return {
      coldStart: false,
      version: runtimeState.version,
      initDurationMs: runtimeState.lastInitDurationMs,
    };
  }

  const startedAt = Date.now();
  runtimeState.initPromise = (async () => {
    bootstrapBackgroundAssets();
    if (typeof loadPyodide === "undefined") {
      throw new Error("pyodide.js is unavailable in background context");
    }

    const pyodide = await loadPyodide({ indexURL: resolvePyodideIndexURL() });
    writePythonFiles(pyodide);
    await pyodide.runPythonAsync(`
import slop_guard
_sg_version = slop_guard.PACKAGE_VERSION
    `);

    const versionHandle = pyodide.globals.get("_sg_version");
    const version = String(versionHandle);
    if (versionHandle && typeof versionHandle.destroy === "function") {
      versionHandle.destroy();
    }

    runtimeState.pyodide = pyodide;
    runtimeState.version = version;
    runtimeState.ready = true;
    runtimeState.lastInitDurationMs = Date.now() - startedAt;
  })()
    .catch((error) => {
      runtimeState.pyodide = null;
      runtimeState.version = null;
      runtimeState.ready = false;
      throw error;
    })
    .finally(() => {
      runtimeState.initPromise = null;
    });

  await runtimeState.initPromise;
  return {
    coldStart: true,
    version: runtimeState.version,
    initDurationMs: runtimeState.lastInitDurationMs,
  };
}

async function analyzeText(text) {
  const inputText = typeof text === "string" ? text : "";
  if (!inputText.trim()) {
    return null;
  }

  await ensureRuntime();
  if (!runtimeState.pyodide) {
    throw new Error("Runtime is not initialized");
  }

  runtimeState.pyodide.globals.set("_input_text", inputText);
  try {
    await runtimeState.pyodide.runPythonAsync(`
import json as _json
_result = _json.dumps(slop_guard.analyze(_input_text))
    `);
    const resultHandle = runtimeState.pyodide.globals.get("_result");
    try {
      return JSON.parse(String(resultHandle));
    } finally {
      if (resultHandle && typeof resultHandle.destroy === "function") {
        resultHandle.destroy();
      }
    }
  } finally {
    cleanupPyodideGlobals(runtimeState.pyodide, ["_input_text", "_result"]);
  }
}

function setupContextMenu() {
  if (!ext?.runtime || !ext?.contextMenus?.create) {
    return;
  }

  if (ext.runtime.onInstalled?.addListener) {
    ext.runtime.onInstalled.addListener(() => {
      ext.contextMenus.create(
        {
          id: "slop-guard-check",
          title: "Check with Slop Guard",
          contexts: ["selection"],
        },
        () => {
          void ext.runtime.lastError;
        },
      );
    });
  }

  if (ext.contextMenus.onClicked?.addListener) {
    ext.contextMenus.onClicked.addListener((info, tab) => {
      if (info.menuItemId !== "slop-guard-check" || !info.selectionText) {
        return;
      }

      const tabId = Number.isInteger(tab?.id) ? tab.id : null;
      if (runtimeState.pendingBadgeTabId !== null) {
        clearPendingIndicator();
      }
      runtimeState.pendingBadgeTabId = tabId;

      if (ext?.storage?.local?.set) {
        fireAndForget(ext.storage.local.set.bind(ext.storage.local), {
          [PENDING_TEXT_STORAGE_KEY]: info.selectionText,
          [PENDING_TAB_ID_STORAGE_KEY]: tabId,
        });
      }

      if (ext?.action?.setBadgeText) {
        const badgeDetails = Number.isInteger(tabId) ? { tabId, text: "!" } : { text: "!" };
        fireAndForget(ext.action.setBadgeText.bind(ext.action), badgeDetails);
      }
      if (ext?.action?.setBadgeBackgroundColor) {
        fireAndForget(ext.action.setBadgeBackgroundColor.bind(ext.action), {
          color: "#e94560",
        });
      }
      scheduleBadgeClear(tabId);
    });
  }

  if (ext?.alarms?.onAlarm?.addListener) {
    ext.alarms.onAlarm.addListener((alarm) => {
      const tabId = parseBadgeAlarmTabId(alarm?.name);
      if (tabId !== null) {
        clearPendingIndicator(tabId);
      }
    });
  }

  if (ext?.tabs?.onUpdated?.addListener) {
    ext.tabs.onUpdated.addListener((tabId, changeInfo) => {
      if (
        runtimeState.pendingBadgeTabId === tabId
        && changeInfo
        && changeInfo.status === "loading"
      ) {
        clearPendingIndicator(tabId);
      }
    });
  }

  if (ext?.tabs?.onRemoved?.addListener) {
    ext.tabs.onRemoved.addListener((tabId) => {
      if (runtimeState.pendingBadgeTabId === tabId) {
        clearPendingIndicator(tabId);
      }
    });
  }
}

setupContextMenu();

if (ext?.runtime?.onMessage?.addListener) {
  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message !== "object") {
      return undefined;
    }

    if (message.type === "SG_INIT") {
      ensureRuntime()
        .then((initInfo) => {
          sendResponse({
            ok: true,
            version: initInfo.version,
            coldStart: initInfo.coldStart,
            initDurationMs: initInfo.initDurationMs,
          });
        })
        .catch((error) => {
          sendResponse({ ok: false, error: toErrorMessage(error) });
        });
      return true;
    }

    if (message.type === "SG_ANALYZE") {
      analyzeText(message.text)
        .then((result) => {
          sendResponse({ ok: true, result });
        })
        .catch((error) => {
          sendResponse({ ok: false, error: toErrorMessage(error) });
        });
      return true;
    }

    return undefined;
  });
}
