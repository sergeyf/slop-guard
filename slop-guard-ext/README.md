# Slop Guard — Browser Extension

Score any text 0–100 for formulaic AI writing patterns, directly in your browser.
Runs [slop-guard](https://github.com/eric-tramel/slop-guard) via Pyodide (Python-in-WebAssembly). No server, no API calls, fully offline after first load.

## Quick start

### Build

```bash
cd slop-guard-ext
python3 build.py            # builds dist/chrome/ and dist/firefox/
```

This generates both browser builds, downloads Pyodide, and bundles all slop-guard Python source. Requires git and Python 3 on PATH.

Build a single target:

```bash
python3 build.py --target chrome    # Chrome/Edge/Brave only
python3 build.py --target firefox   # Firefox only (bundles Pyodide locally)
```

### Load the extension

**Chrome / Edge / Brave:**
1. Navigate to `chrome://extensions`
2. Enable Developer mode
3. Click "Load unpacked" and select the `dist/chrome/` folder

**Firefox:**
1. Navigate to `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on"
3. Select `dist/firefox/manifest.json`

### Use it

- Click the extension icon to open the popup
- Paste text, or click **Grab selection** / **Grab page text**
- Press **Analyze** (or Ctrl+Enter / Cmd+Enter)
- First Chrome launch downloads Pyodide (~10 MB), cached after that
- Firefox build includes Pyodide locally — no downloads needed
- Runtime now lives in extension background context, so reopening the popup reuses an already-initialized Pyodide instance
- Last analysis report is restored when reopening the popup
- Capture buttons use smart fallback order (selection → focused editor → page text)

## Updating slop-guard

When the upstream repo gets new patterns or scoring changes:

```bash
python3 build.py                        # from slop-guard-ext/
python3 build.py --repo /path/to/repo   # use a local checkout
```

Then reload the extension in your browser.

## Testing

Tests use Playwright to run the popup in both Chromium and Firefox:

```bash
pip install playwright pytest
python -m playwright install chromium firefox
python -m pytest tests/ -v
```

## Architecture

```
User Input → popup.js → background.js runtime bridge → Pyodide (Python WASM) → slop_guard.analyze(text) → JSON → UI
```

- `bundle.py` reads all Python source from slop-guard, embeds them as JSON in `python_bundle.js`, and downloads `pyodide.js` locally
- `build.py` produces browser-specific builds in `dist/chrome/` and `dist/firefox/`
- Background runtime loads Pyodide once and serves popup analysis requests over extension messaging
- Firefox build bundles Pyodide runtime locally (AMO requires no remote code)
- Chrome build uses the jsDelivr CDN for Pyodide (cached after first load)
- No `mcp` dependency needed; the bundle replaces `server.py` with a thin `analyze()` wrapper

## File structure

```
manifest.chrome.json   ← Chrome/Edge/Brave manifest (MV3)
manifest.firefox.json  ← Firefox manifest (MV3 + gecko settings)
popup.html             ← Extension UI
popup.js               ← Pyodide bootstrap + UI controller
python_bundle.js       ← All slop-guard Python source as JS strings (generated)
pyodide.js             ← Pyodide loader (downloaded by bundle.py)
background.js          ← Context menu (right-click → Check with Slop Guard)
bundle.py              ← Generate python_bundle.js + download pyodide.js
build.py               ← Build browser-specific packages in dist/
update.sh              ← Legacy: pull upstream + bundle (Mac/Linux)
update.ps1             ← Legacy: pull upstream + bundle (Windows)
tests/                 ← Playwright browser tests
dist/                  ← Build output (git-ignored)
  chrome/              ← Ready-to-load Chrome extension
  firefox/             ← Ready-to-load Firefox extension + local Pyodide
  slop-guard-firefox.zip   ← AMO submission package
  slop-guard-source.zip    ← Source code for AMO review
```

## AMO submission

To submit to [addons.mozilla.org](https://addons.mozilla.org):

```bash
python3 build.py   # creates dist/slop-guard-firefox.zip + dist/slop-guard-source.zip
```

1. Go to https://addons.mozilla.org/developers/
2. Click "Submit a New Add-on"
3. Upload `dist/slop-guard-firefox.zip`
4. When asked for source code, upload `dist/slop-guard-source.zip`
5. Fill in listing details:
   - **Name:** Slop Guard
   - **Summary:** Score text 0–100 for formulaic AI writing patterns. Pure regex, no API calls, fully offline.
   - **Category:** Other Tools
   - **License:** MIT
   - **Homepage:** https://github.com/eric-tramel/slop-guard

The source archive is required because `python_bundle.js` is generated code.
Reviewers can reproduce it by running `python3 bundle.py` from the source.

## Score bands

| Score  | Band      |
|--------|-----------|
| 80–100 | Clean     |
| 60–79  | Light     |
| 40–59  | Moderate  |
| 20–39  | Heavy     |
| 0–19   | Saturated |

## License

Extension wrapper: MIT
slop-guard: MIT (Eric Tramel)
