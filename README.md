# VSCode Workspace Launcher (PyQt6)

A fast, friendly **entry point for VS Code**: recursively scans a root directory for `*.code-workspace`, gives you live search and quick filters, pin & MRU, and lets you open any folder directly in VS Code. Built with **PyQt6**. Cross‑platform. Async scanning. Clean UX.

> Requires **Python 3.9+**. Dependencies: `PyQt6`, `platformdirs`.

---

## Features

- **Workspaces tab**
  - Async recursive scan for `*.code-workspace` (non‑blocking, progress in status bar)
  - **Live search** across *name / path / description*, quick filters: **All / Pinned / Recent**, filter **by tag** (`meta.tags`)
  - **Pin/Unpin** (⭐ in the list) and **MRU** tracking
  - Details pane with **wrapping multi‑line Path** and description
  - **Add**: creates a folder + workspace file with `meta.description` & `meta.tags`
  - **Fix Workspaces**: backfills missing JSON fields (`folders`, `settings`, `meta.description`, `meta.tags`)

- **Folders tab**
  - Native `QFileSystemModel` (when available) or lazy fallback tree
  - Open any folder directly in VS Code

- **Nice UX touches**
  - **UI scale** multiplier for the app
  - Fixed **left pane width** (configurable); the right pane expands
  - Shortcuts: **Enter**=Open, **Ctrl+N**=Add, **Ctrl+F**=Focus search, **F5**=Rescan
  - Status bar shows native/fallback mode + scan progress counter

---

## Installation & Development Run

```bash
pip install -r requirements.txt
python pyVSCodeLauncher.py
```

`requirements.txt`:
```
PyQt6
platformdirs
```

> On Windows you may prefer a venv: `python -m venv venv && venv\Scripts\activate`.

---

## Settings

- **Root folder** — scan root (recursive)
- **VS Code binary** — path to `code` / `code.cmd` if not in `PATH`
- **UI scale** — multiplier for the app UI font
- **Folders first column width (px)** — first column width in *Folders*
- **Workspaces left pane width (px)** — fixed width of the left list pane
- *(optional, future toggle)* **Apply UI scale to VS Code** + **VS Code base editor font (px)** — propagate scale/font to newly generated workspaces

---

## How it works (quick tour)

1. **Workspaces:** left = list (search/filters), right = details + **Open in VS Code**.
2. **Add:** enter folder name → optional description & tags → `.code-workspace` is created and opened in VS Code.
3. **Fix Workspaces:** adds missing `folders`, `settings`, `meta.description`, `meta.tags` back into workspace JSONs.
4. **Folders:** browse a tree (native or fallback), double‑click / Open to launch VS Code in that folder.

---

## Workspace JSON Schema

We read and (safely) write the following fields:

```json
{
  "folders": [{ "path": "." }],
  "settings": {},
  "meta": {
    "description": "Your description here",
    "tags": ["python", "embedded", "lab"]
  }
}
```

> `meta.*` is ignored by VS Code—handy metadata for the launcher.

---

## Icons (window, Windows taskbar, status bar)

Add an `assets/` folder:
```
assets/
  app.png       # 512x512 — used in runtime (window/taskbar in dev)
  app_tray.png  # 16–32 px — small icon for status bar / tray
  app.ico       # multi‑resolution (16..256) — Windows EXE icon
```
Create ICO from PNG (ImageMagick):
```bash
magick convert app.png -define icon:auto-resize=256,128,64,48,32,16 app.ico
```

> In dev runs, `setWindowIcon(QIcon(app.png))` sets window/taskbar icon. When packaged for Windows, the taskbar icon comes from the EXE’s **icon resource** (see PyInstaller `--icon`).

---

## Packaging

### Windows (PyInstaller)

```powershell
python -m venv venv
./venv/Scripts/activate
pip install -r requirements.txt pyinstaller

pyinstaller ^
  --noconsole ^
  --windowed ^
  --name "VSCodeWorkspaceLauncher" ^
  --icon assets\app.ico ^
  --add-data "assets;assets" ^
  pyVSCodeLauncher.py
```
Artifacts: `dist/VSCodeWorkspaceLauncher/`. The taskbar icon is taken from `--icon`.

> `--onefile` is possible, but first launch is slower due to unpacking.

### Linux (PyInstaller + .desktop)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt pyinstaller

pyinstaller \
  --noconsole \
  --name "vscode-workspace-launcher" \
  --icon assets/app.png \
  --add-data "assets:assets" \
  pyVSCodeLauncher.py
```
Create a user desktop entry at `~/.local/share/applications/vscode-workspace-launcher.desktop`:
```ini
[Desktop Entry]
Type=Application
Version=1.0
Name=VSCode Workspace Launcher
Exec=/absolute/path/to/dist/vscode-workspace-launcher/vscode-workspace-launcher
Icon=/absolute/path/to/assets/app.png
Terminal=false
Categories=Development;Utility;
```

---

## GitHub: structure & first push

Suggested layout:
```
.
├─ assets/
│  ├─ app.png
│  ├─ app_tray.png
│  └─ app.ico
├─ pyVSCodeLauncher.py
├─ requirements.txt
├─ README.md
├─ LICENSE        # e.g., MIT
└─ .gitignore
```
`.gitignore` (minimal):
```
__pycache__/
*.pyc
.venv/
venv/
build/
/dist/
*.spec
.DS_Store
```
Commands:
```bash
git init
git remote add origin https://github.com/<your-handle>/vscode-workspace-launcher.git
git add .
git commit -m "Initial release: PyQt6 VSCode workspace launcher"
git push -u origin main
```

### CI (optional): GitHub Actions

Save as `.github/workflows/build.yml` to build Windows + Linux and upload artifacts:
```yaml
name: build
on: [push, pull_request]
jobs:
  windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt pyinstaller
      - run: pyinstaller --noconsole --windowed --name "VSCodeWorkspaceLauncher" --icon assets\app.ico --add-data "assets;assets" pyVSCodeLauncher.py
      - uses: actions/upload-artifact@v4
        with: { name: windows-dist, path: dist/VSCodeWorkspaceLauncher/** }

  linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt pyinstaller
      - run: pyinstaller --noconsole --name "vscode-workspace-launcher" --icon assets/app.png --add-data "assets:assets" pyVSCodeLauncher.py
      - uses: actions/upload-artifact@v4
        with: { name: linux-dist, path: dist/vscode-workspace-launcher/** }
```

---

## Troubleshooting VS Code launch differences

If VS Code looks different when started from this launcher vs your desktop shortcut, check:
- You’re launching the **same binary** (`Code.exe` vs Insiders; PATH might point elsewhere).
- The **user data dir / profile** is the same (`code --status` shows `userDataDir`, `appName`).
- `argv.json` flags (e.g., `--force-device-scale-factor`) aren’t diverging between installs.
- Workspace JSON doesn’t override `window.zoomLevel` / `editor.fontSize` unexpectedly.

---

## Roadmap

- Profile‑aware launch (optional `--profile`, `--user-data-dir`)
- Live tag editor & bulk tagging
- Async folder tree population for ultra‑large trees
- AppImage / .deb packaging

---

## Authors

- **Idea & product direction:** Yegor Sheremetiev
- **Implementation:** GPT‑5 Thinking (assistant)

> You provided the concept and constraints; I assembled and polished the code.

---

## License

MIT. Free to use, modify, and distribute with attribution.
