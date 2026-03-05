# Slop Guard Browser Extension

Score any text 0-100 for formulaic AI writing patterns directly in your browser.
The extension runs [slop-guard](https://github.com/eric-tramel/slop-guard) through Pyodide (Python in WebAssembly). There is no server and no API traffic. Both browser builds bundle Pyodide locally.

## Quick start

### Build

```bash
cd slop-guard-ext
uv run build.py
```

That rebuilds the Python bundle from the current repo, refreshes Pyodide if needed, and creates `dist/chrome/` plus `dist/firefox/`.

Build a single target:

```bash
uv run build.py --target chrome
uv run build.py --target firefox
```

Build from another checkout:

```bash
uv run build.py --repo /path/to/slop-guard
```

### Load the extension

Chrome / Edge / Brave:
1. Open `chrome://extensions`
2. Enable Developer mode
3. Click `Load unpacked`
4. Select `dist/chrome/`

Firefox:
1. Open `about:debugging#/runtime/this-firefox`
2. Click `Load Temporary Add-on`
3. Select `dist/firefox/manifest.json`

### Use it

- Click the extension icon to open the popup
- Paste text, or use `Grab selection` / `Grab page text`
- Press `Analyze` or use `Ctrl+Enter` / `Cmd+Enter`
- Reopening the popup reuses the already-initialized background runtime
- The last report is restored when the popup opens again
- Capture prefers selection, then the focused editor, then page text

## Updating slop-guard

When upstream rules or scoring change:

```bash
uv run build.py
```

Or use a different checkout:

```bash
uv run build.py --repo /path/to/slop-guard
```

## Testing

Install browser binaries once:

```bash
uv run --with playwright playwright install chromium firefox
```

Run the extension tests:

```bash
uv run --with pytest --with playwright pytest -q slop-guard-ext/tests
```

## Architecture

```text
User input -> popup.js -> background.js runtime bridge -> Pyodide -> slop_guard.analyze(text) -> JSON -> UI
```

- `bundle.py` reads Python source from `src/slop_guard`, generates `python_bundle.js`, and downloads `pyodide.js`
- `build.py` creates browser-specific builds in `dist/chrome/` and `dist/firefox/`
- `background.js` owns the long-lived Pyodide runtime and context-menu integration
- `popup.js` manages the UI, capture helpers, persistence, and rendering
- Both Chrome and Firefox bundle Pyodide locally

## File structure

```text
manifest.chrome.json   Chrome / Edge / Brave manifest (MV3)
manifest.firefox.json  Firefox manifest (MV3 + Gecko settings)
popup.html             Extension UI
popup.js               Popup controller and rendering
python_bundle.js       Generated slop-guard Python source
pyodide.js             Downloaded Pyodide loader
background.js          Background runtime and context menu support
bundle.py              Generate python_bundle.js and refresh pyodide.js
build.py               Build browser-specific packages in dist/
update.sh              Pull upstream and regenerate the bundle (Unix)
update.ps1             Pull upstream and regenerate the bundle (Windows)
tests/                 Playwright and build regression tests
dist/                  Build output (git-ignored)
```

## AMO submission

```bash
uv run build.py
```

That creates:

- `dist/slop-guard-firefox.zip`
- `dist/slop-guard-source.zip`

The source archive is needed because `python_bundle.js` is generated code.

## Score bands

| Score | Band |
|-------|------|
| 80-100 | Clean |
| 60-79 | Light |
| 40-59 | Moderate |
| 20-39 | Heavy |
| 0-19 | Saturated |

## License

Extension wrapper: MIT  
slop-guard: MIT (Eric Tramel)
