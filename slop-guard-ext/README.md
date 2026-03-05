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
4. Firefox removes temporary add-ons on restart, so use the self-hosted signed flow below for a durable install

### Use it

- Click the extension icon to open the popup
- Paste text, or use `Grab selection` / `Grab page text`
- Press `Analyze` or use `Ctrl+Enter` / `Cmd+Enter`
- Use `Copy instructions` to turn the current advice list into a writer-facing edit prompt
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

## Firefox self-hosted auto-updates

Temporary Firefox installs from `about:debugging` are for development only. For a durable install that survives restarts and auto-updates, you need:

- a signed Firefox build
- `browser_specific_settings.gecko.update_url` baked into that signed build before distribution
- an HTTPS-hosted `updates.json`
- an HTTPS-hosted signed `.xpi`

Build the Firefox artifacts with the final public update URL:

```bash
uv run build.py --target firefox --firefox-update-base-url https://downloads.example.com/slop-guard/firefox/
```

That creates:

- `dist/firefox/` - unpacked Firefox build with `update_url` embedded
- `dist/slop-guard-firefox.zip` - AMO upload package
- `dist/slop-guard-source.zip` - source archive for AMO review
- `dist/firefox-selfhost/slop-guard-firefox.xpi` - unsigned XPI matching the built manifest
- `dist/firefox-selfhost/updates.json` - update manifest pointing at `https://downloads.example.com/slop-guard/firefox/slop-guard-firefox.xpi`

Sign the Firefox build through AMO's unlisted/self-distribution flow. Two workable paths:

1. Upload `dist/slop-guard-firefox.zip` in the AMO Developer Hub as an unlisted add-on.
2. Or sign the built directory directly:

```bash
npx web-ext sign --channel=unlisted --source-dir dist/firefox --artifacts-dir dist/firefox-signed
```

After Mozilla returns the signed XPI:

1. Upload the signed file to the exact XPI URL referenced by `dist/firefox-selfhost/updates.json`.
2. Upload `dist/firefox-selfhost/updates.json` to the exact `updates.json` URL embedded in the manifest.
3. Serve the XPI over HTTPS with `Content-Type: application/x-xpinstall`.
4. On each release, bump the extension `version`, rerun the same build command, sign again, and replace the hosted XPI plus `updates.json`.

Firefox checks for extension updates roughly once per day. For local testing, temporarily lower `extensions.update.interval` in `about:config`.

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
