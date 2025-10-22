#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VSCode Workspace Launcher (PyQt6) — HARD RESET

Cross‑platform launcher for managing and opening VS Code workspaces and folders.

Requirements:
    - Python 3.9+
    - pip install PyQt6 platformdirs

Highlights in this build:
    - Settings with persistent options (root scan folder, VS Code binary path, UI scale, left pane width, folders column width)
    - Async recursive scan for *.code-workspace (with progress counter)
    - Live search + quick filters: mode (All / Pinned / Recent) and by tag (meta.tags)
    - MRU list + Pin/Unpin with star in list
    - Workspaces tab: left (fixed width) list, right details (path wraps + description) and Open
    - Folders tab: native QFileSystemModel if available, otherwise lazy fallback; open folder in VS Code
    - Add new workspace (with meta.description + meta.tags), Fix Workspaces utility, Help
    - Keyboard shortcuts: Enter=Open (on list), Ctrl+N=Add, Ctrl+F=Focus filter, F5=Rescan
    - Status bar shows native/fallback and scan progress
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
import shlex
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
# QFileSystemModel location can differ across PyQt6 builds
try:
    from PyQt6.QtWidgets import QFileSystemModel as Qt_QFileSystemModel  # type: ignore
except Exception:
    try:
        from PyQt6.QtGui import QFileSystemModel as Qt_QFileSystemModel  # type: ignore
    except Exception:
        Qt_QFileSystemModel = None

from platformdirs import user_config_dir

from pathlib import Path
import sys
from PyQt6 import QtCore, QtGui, QtWidgets

def _assets_base() -> Path:
    # PyInstaller support (sys._MEIPASS)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent

def _asset_path(name: str) -> str:
    return str((_assets_base() / "assets" / name).resolve())

APP_ICON_PNG = _asset_path("app.png")
APP_TRAY_PNG = _asset_path("app_tray.png")
# ----------------------------- Config -----------------------------
APP_NAME = "vscode-workspace-launcher"
ORG_NAME = "yegor-tools"
CONFIG_DIR = Path(user_config_dir(APP_NAME, ORG_NAME))
CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXT = ".code-workspace"
DEFAULT_WS_CONTENT = {
    "folders": [{"path": "."}],
    "settings": {},
    # Custom fields for this app (safe for VS Code to ignore)
    "meta": {
        "description": "New workspace",
        "tags": []
    }
}

# ----------------------------- Data Classes -----------------------------
@dataclass
class Settings:
    root_folder: Path
    code_path: Optional[Path]
    ui_scale: float = 1.0  # multiplicator for font sizes (1.0 = 100%)
    folders_column_width: int = 360  # pixels for folders tree first column
    left_pane_width: int = 300       # fixed width for workspaces list pane
    pinned: List[str] = field(default_factory=list)  # list of workspace paths
    mru: List[str] = field(default_factory=list)     # most recently used workspace paths

    def to_json(self) -> dict:
        return {
            "root_folder": str(self.root_folder) if self.root_folder else "",
            "code_path": str(self.code_path) if self.code_path else "",
            "ui_scale": self.ui_scale,
            "folders_column_width": self.folders_column_width,
            "left_pane_width": self.left_pane_width,
            "pinned": list(self.pinned),
            "mru": list(self.mru),
        }

    @staticmethod
    def from_json(data: dict) -> "Settings":
        root = Path(data.get("root_folder") or str(Path.home()))
        codep = data.get("code_path")
        ui_scale = float(data.get("ui_scale", 1.0))
        folders_w = int(data.get("folders_column_width", 360))
        left_w = int(data.get("left_pane_width", 300))
        pinned = list(data.get("pinned", []))
        mru = list(data.get("mru", []))
        return Settings(
            root_folder=root,
            code_path=Path(codep) if codep else None,
            ui_scale=ui_scale,
            folders_column_width=folders_w,
            left_pane_width=left_w,
            pinned=pinned,
            mru=mru,
        )

    @staticmethod
    def default() -> "Settings":
        return Settings(
            root_folder=Path.home(),
            code_path=None,
            ui_scale=1.0,
            folders_column_width=360,
            left_pane_width=300,
            pinned=[],
            mru=[],
        )


@dataclass
class WorkspaceInfo:
    name: str
    path: Path
    description: str
    mtime: float
    tags: List[str] = field(default_factory=list)

    def mtime_dt(self) -> datetime:
        return datetime.fromtimestamp(self.mtime)


# ----------------------------- Settings I/O -----------------------------

def load_settings() -> Settings:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return Settings.from_json(data)
        except Exception:
            pass
    return Settings.default()


def save_settings(settings: Settings) -> None:
    CONFIG_PATH.write_text(json.dumps(settings.to_json(), indent=2), encoding="utf-8")


# ----------------------------- Utils -----------------------------

def which_code(custom: Optional[Path]) -> Optional[str]:
    if custom and custom.exists():
        return str(custom)
    found = shutil.which("code")
    if found:
        return found
    # Typical extra places
    candidates = [
        "/usr/bin/code",
        "/snap/bin/code",
        str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


# ----------------------------- Models -----------------------------
class WorkspaceListModel(QtCore.QAbstractListModel):
    def __init__(self, rows: List[WorkspaceInfo]):
        super().__init__()
        self.rows: List[WorkspaceInfo] = rows
        self.pinned_set = set()

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return len(self.rows)

    def data(self, index: QtCore.QModelIndex, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            star = "★ " if str(row.path) in self.pinned_set else ""
            return f"{star}{row.name}"
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return str(row.path)
        return None

    def workspace_at(self, row: int) -> WorkspaceInfo:
        return self.rows[row]

    def update_rows(self, rows: List[WorkspaceInfo]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def set_pinned(self, pinned_paths: List[str]):
        self.pinned_set = set(pinned_paths or [])
        if self.rowCount() > 0:
            tl = self.index(0, 0)
            br = self.index(self.rowCount() - 1, 0)
            self.dataChanged.emit(tl, br, [QtCore.Qt.ItemDataRole.DisplayRole])


class WorkspaceFilterProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._pattern = ""
        self._mode = "All"  # All / Pinned / Recent
        self._tag = ""       # empty = any
        self._pinned = set()
        self._mru = set()
        self.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)

    @QtCore.pyqtSlot(str)
    def setFilterText(self, text: str):
        self._pattern = (text or "").strip().lower()
        self.invalidateFilter()

    def setFilterMode(self, mode: str):
        self._mode = mode or "All"
        self.invalidateFilter()

    def setTagFilter(self, tag: str):
        self._tag = tag or ""
        self.invalidateFilter()

    def setPinnedSet(self, pinned_paths: List[str]):
        self._pinned = set(pinned_paths or [])
        self.invalidateFilter()

    def setMruSet(self, mru_paths: List[str]):
        self._mru = set(mru_paths or [])
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QtCore.QModelIndex) -> bool:
        src: WorkspaceListModel = self.sourceModel()  # type: ignore
        if not src or source_row < 0 or source_row >= src.rowCount():
            return True
        ws = src.workspace_at(source_row)

        # Text pattern
        if self._pattern:
            pat = self._pattern
            if not (pat in ws.name.lower() or pat in (ws.description or "").lower() or pat in str(ws.path).lower()):
                return False
        # Mode filter
        if self._mode == "Pinned" and str(ws.path) not in self._pinned:
            return False
        if self._mode == "Recent" and str(ws.path) not in self._mru:
            return False
        # Tag filter
        if self._tag:
            tags_l = [t.lower() for t in (ws.tags or [])]
            if self._tag.lower() not in tags_l:
                return False
        return True


# ----------------------------- Async Scanner -----------------------------
class ScanWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(list, int)   # rows, generation
    error = QtCore.pyqtSignal(str, int)       # message, generation
    progress = QtCore.pyqtSignal(int, int)    # count, generation

    def __init__(self, root: Path, generation: int):
        super().__init__()
        self.root = Path(root)
        self.gen = generation
        self._cancel = False
        self._skip_dirs = {".git", ".hg", ".svn", "node_modules", "venv", ".tox", ".mypy_cache"}

    @QtCore.pyqtSlot()
    def run(self):
        try:
            if not self.root.exists():
                self.finished.emit([], self.gen)
                return
            rows: List[WorkspaceInfo] = []
            count = 0
            for dirpath, dirnames, filenames in os.walk(self.root):
                # prune heavy/irrelevant dirs
                dirnames[:] = [d for d in dirnames if d not in self._skip_dirs]
                for name in filenames:
                    if name.endswith(SUPPORTED_EXT):
                        p = Path(dirpath) / name
                        try:
                            text = p.read_text(encoding="utf-8")
                            data = json.loads(text)
                            desc = ""
                            tags: List[str] = []
                            if isinstance(data, dict):
                                meta = data.get("meta")
                                if isinstance(meta, dict):
                                    desc = str(meta.get("description", ""))
                                    if isinstance(meta.get("tags"), list):
                                        tags = [str(t) for t in meta.get("tags")]
                                if not desc:
                                    desc = str(data.get("description", ""))
                            rows.append(WorkspaceInfo(name=p.stem, path=p, description=desc, mtime=p.stat().st_mtime, tags=tags))
                            count += 1
                            if count % 10 == 0:
                                self.progress.emit(count, self.gen)
                        except Exception:
                            continue
                if self._cancel:
                    self.finished.emit([], self.gen)
                    return
            rows.sort(key=lambda w: (w.name.lower(), str(w.path).lower()))
            self.finished.emit(rows, self.gen)
        except Exception as e:
            self.error.emit(str(e), self.gen)

    @QtCore.pyqtSlot()
    def cancel(self):
        self._cancel = True


# ----------------------------- Settings Dialog -----------------------------
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.settings = settings

        self.root_edit = QtWidgets.QLineEdit(str(self.settings.root_folder))
        self.code_edit = QtWidgets.QLineEdit(str(self.settings.code_path) if self.settings.code_path else "")
        self.scale_spin = QtWidgets.QDoubleSpinBox(); self.scale_spin.setRange(0.75, 2.0); self.scale_spin.setSingleStep(0.05); self.scale_spin.setDecimals(2); self.scale_spin.setSuffix(" ×")
        self.scale_spin.setValue(float(self.settings.ui_scale))
        self.folders_w_spin = QtWidgets.QSpinBox(); self.folders_w_spin.setRange(200, 1200); self.folders_w_spin.setSingleStep(20); self.folders_w_spin.setValue(int(self.settings.folders_column_width))
        self.left_w_spin = QtWidgets.QSpinBox(); self.left_w_spin.setRange(200, 800); self.left_w_spin.setSingleStep(10); self.left_w_spin.setValue(int(self.settings.left_pane_width))

        browse_root_btn = QtWidgets.QPushButton("Browse…")
        browse_root_btn.clicked.connect(self._browse_root)
        browse_code_btn = QtWidgets.QPushButton("Browse…")
        browse_code_btn.clicked.connect(self._browse_code)

        form = QtWidgets.QFormLayout()
        h1 = QtWidgets.QHBoxLayout(); h1.addWidget(self.root_edit); h1.addWidget(browse_root_btn)
        h2 = QtWidgets.QHBoxLayout(); h2.addWidget(self.code_edit); h2.addWidget(browse_code_btn)
        form.addRow("Root folder:", self._wrap(h1))
        form.addRow("VS Code binary (optional):", self._wrap(h2))
        form.addRow("UI scale:", self.scale_spin)
        form.addRow("Folders first column width (px):", self.folders_w_spin)
        form.addRow("Workspaces left pane width (px):", self.left_w_spin)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btns)

    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); w.setLayout(layout); return w

    def _browse_root(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Root Folder", str(self.settings.root_folder))
        if d:
            self.root_edit.setText(d)

    def _browse_code(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select VS Code CLI (code / code.cmd)", str(Path.home()))
        if f:
            self.code_edit.setText(f)

    def get_settings(self) -> Settings:
        root = Path(self.root_edit.text().strip() or str(Path.home()))
        code = self.code_edit.text().strip()
        codep = Path(code) if code else None
        ui_scale = float(self.scale_spin.value())
        folders_w = int(self.folders_w_spin.value())
        left_w = int(self.left_w_spin.value())
        return Settings(root_folder=root, code_path=codep, ui_scale=ui_scale,
                        folders_column_width=folders_w, left_pane_width=left_w,
                        pinned=self.settings.pinned, mru=self.settings.mru)


# ----------------------------- Main Window -----------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VSCode Workspace Launcher")

        # App icon
        try:
            self.setWindowIcon(QtGui.QIcon(APP_ICON_PNG))
        except Exception:
            pass

        self.resize(1150, 720)
        self.settings = load_settings()

        # Status bar
        self.status = self.statusBar()

        # Status icon
        self.status_icon = QtWidgets.QLabel()
        try:
            pm = QtGui.QPixmap(APP_TRAY_PNG)
            if not pm.isNull():
                self.status_icon.setPixmap(
                    pm.scaled(
                        16, 16,
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation
                    )
                )
                self.status.addPermanentWidget(self.status_icon)
        except Exception:
            pass

        self.mode_label = QtWidgets.QLabel("")
        self.status.addPermanentWidget(self.mode_label)
        self.scan_progress = QtWidgets.QProgressBar(); self.scan_progress.setFixedWidth(150); self.scan_progress.setTextVisible(False); self.scan_progress.setVisible(False)
        self.status.addPermanentWidget(self.scan_progress)

        # Central tabs
        tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(tabs)

        # --- Workspaces tab ---
        ws_widget = QtWidgets.QWidget(); ws_layout = QtWidgets.QVBoxLayout(ws_widget)

        # Toolbar
        toolbar = QtWidgets.QToolBar(); self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        act_settings = QtGui.QAction("Settings", self); act_settings.triggered.connect(self.on_settings)
        act_rescan = QtGui.QAction("Rescan", self); act_rescan.triggered.connect(self.refresh)
        act_add = QtGui.QAction("Add", self); act_add.triggered.connect(self.on_add)
        act_pin = QtGui.QAction("Pin/Unpin", self); act_pin.triggered.connect(self.on_toggle_pin)
        act_fix = QtGui.QAction("Fix Workspaces", self); act_fix.triggered.connect(self.on_fix_workspaces)
        act_help = QtGui.QAction("Help", self); act_help.triggered.connect(self.on_help)
        toolbar.addAction(act_settings)
        toolbar.addAction(act_rescan)
        toolbar.addSeparator()
        toolbar.addAction(act_add)
        toolbar.addAction(act_pin)
        toolbar.addSeparator()
        toolbar.addAction(act_fix)
        toolbar.addAction(act_help)

        # Splitter
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # Left pane (fixed width)
        left = QtWidgets.QWidget(); left_layout = QtWidgets.QVBoxLayout(left)
        left.setFixedWidth(self.settings.left_pane_width)

        # Quick filters
        filters_row = QtWidgets.QHBoxLayout()
        self.mode_combo = QtWidgets.QComboBox(); self.mode_combo.addItems(["All", "Pinned", "Recent"])
        self.tag_combo = QtWidgets.QComboBox(); self.tag_combo.addItem("Any tag")
        filters_row.addWidget(QtWidgets.QLabel("Mode:")); filters_row.addWidget(self.mode_combo)
        filters_row.addWidget(QtWidgets.QLabel("Tag:")); filters_row.addWidget(self.tag_combo); filters_row.addStretch(1)
        left_layout.addLayout(filters_row)

        # Live search
        self.search_edit = QtWidgets.QLineEdit(); self.search_edit.setPlaceholderText("Filter by name, path, or description…")
        try: self.search_edit.setClearButtonEnabled(True)
        except Exception: pass
        left_layout.addWidget(self.search_edit)

        # Workspaces list
        self.ws_list = QtWidgets.QListView()
        self.ws_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.ws_list.doubleClicked.connect(self.on_open_selected)
        self.ws_list.activated.connect(self.on_open_selected)
        left_layout.addWidget(self.ws_list)

        split.addWidget(left)

        # Right pane (details)
        right = QtWidgets.QWidget(); right_layout = QtWidgets.QVBoxLayout(right)
        details = QtWidgets.QGroupBox("Details")
        form = QtWidgets.QFormLayout(details)
        self.lbl_name = QtWidgets.QLabel("–")
        self.txt_path = QtWidgets.QPlainTextEdit(); self.txt_path.setReadOnly(True); self.txt_path.setMaximumHeight(64)
        try: self.txt_path.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        except Exception: pass
        self.txt_desc = QtWidgets.QPlainTextEdit(); self.txt_desc.setReadOnly(True); self.txt_desc.setFixedHeight(120)
        form.addRow("Name:", self.lbl_name)
        form.addRow("Path:", self.txt_path)
        form.addRow("Description:", self.txt_desc)
        right_layout.addWidget(details)

        self.btn_open = QtWidgets.QPushButton("Open in VS Code")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.on_open_selected)
        right_layout.addWidget(self.btn_open)
        right_layout.addStretch(1)

        split.addWidget(right)
        split.setStretchFactor(0, 0)  # left is fixed
        split.setStretchFactor(1, 1)  # right expands

        ws_layout.addWidget(split)

        tabs.addTab(ws_widget, "Workspaces")

        # --- Folders tab ---
        folders_widget = QtWidgets.QWidget(); f_layout = QtWidgets.QVBoxLayout(folders_widget)
        self.tree = QtWidgets.QTreeView(); self.tree.setHeaderHidden(False); self.tree.setAlternatingRowColors(True); self.tree.setExpandsOnDoubleClick(True); self.tree.setUniformRowHeights(True)
        if Qt_QFileSystemModel is not None:
            self.folder_model_mode = "native"
            self.fs_model = Qt_QFileSystemModel()
            try:
                self.fs_model.setFilter(QtCore.QDir.Filter.AllDirs | QtCore.QDir.Filter.NoDotAndDotDot)
            except Exception:
                pass
            self.fs_model.setRootPath(str(self.settings.root_folder))
            self.tree.setModel(self.fs_model)
            self.tree.setRootIndex(self.fs_model.index(str(self.settings.root_folder)))
            try:
                self.tree.setColumnWidth(0, self.settings.folders_column_width)
                self.tree.setColumnWidth(1, max(120, int(self.settings.folders_column_width * 0.5)))
            except Exception:
                pass
            self.tree.doubleClicked.connect(self.on_open_folder)
            self.tree.selectionModel().selectionChanged.connect(self.on_folder_selected)
        else:
            self.folder_model_mode = "fallback"
            self.std_model = QtGui.QStandardItemModel(); self.std_model.setHorizontalHeaderLabels(["Folders"])
            self.tree.setModel(self.std_model)
            self.populate_manual_tree(str(self.settings.root_folder))
            self.tree.expanded.connect(self.on_manual_expand)
            self.tree.selectionModel().selectionChanged.connect(self.on_folder_selected)
            try: self.tree.setMinimumWidth(self.settings.folders_column_width)
            except Exception: pass

        f_layout.addWidget(self.tree)
        self.btn_open_folder = QtWidgets.QPushButton("Open Folder in VS Code"); self.btn_open_folder.setEnabled(False); self.btn_open_folder.clicked.connect(self.on_open_folder)
        f_layout.addWidget(self.btn_open_folder)

        tabs.addTab(folders_widget, "Folders")

        # --- Models and async scan wiring ---
        self.ws_model = WorkspaceListModel([])
        self.ws_model.set_pinned(self.settings.pinned)
        self.ws_proxy = WorkspaceFilterProxyModel(); self.ws_proxy.setSourceModel(self.ws_model)
        self.ws_proxy.setPinnedSet(self.settings.pinned)
        self.ws_proxy.setMruSet(self.settings.mru)
        self.ws_list.setModel(self.ws_proxy)
        self.ws_list.selectionModel().selectionChanged.connect(self.on_row_selected)
        self.search_edit.textChanged.connect(self.ws_proxy.setFilterText)
        self.mode_combo.currentTextChanged.connect(self.ws_proxy.setFilterMode)
        self.tag_combo.currentTextChanged.connect(lambda t: self.ws_proxy.setTagFilter("") if t == "Any tag" else self.ws_proxy.setTagFilter(t))

        # --- Keyboard shortcuts ---
        QtGui.QShortcut(QtGui.QKeySequence("F5"), self, activated=self.refresh)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+N"), self, activated=self.on_add)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self, activated=lambda: self.search_edit.setFocus())
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Return), self.ws_list, activated=self.on_open_selected, context=QtCore.Qt.ShortcutContext.WidgetShortcut)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Enter), self.ws_list, activated=self.on_open_selected, context=QtCore.Qt.ShortcutContext.WidgetShortcut)

        # Initial refresh
        self.refresh()

    # ----------------- Manual tree (fallback) -----------------
    def populate_manual_tree(self, root_path: str):
        try:
            self.std_model.removeRows(0, self.std_model.rowCount())
        except Exception:
            pass
        root_item = QtGui.QStandardItem(Path(root_path).name)
        root_item.setEditable(False)
        root_item.setData(root_path, QtCore.Qt.ItemDataRole.UserRole)
        root_item.appendRow(QtGui.QStandardItem("…"))
        self.std_model.appendRow(root_item)
        self.tree.expand(self.std_model.index(0, 0))

    def _add_children_lazy(self, parent_item: QtGui.QStandardItem, parent_path: str):
        try:
            with os.scandir(parent_path) as it:
                for entry in it:
                    if entry.is_dir():
                        child = QtGui.QStandardItem(entry.name)
                        child.setEditable(False)
                        child.setData(entry.path, QtCore.Qt.ItemDataRole.UserRole)
                        child.appendRow(QtGui.QStandardItem("…"))
                        parent_item.appendRow(child)
        except Exception:
            pass

    def on_manual_expand(self, index: QtCore.QModelIndex):
        item = self.std_model.itemFromIndex(index)
        if not item:
            return
        if item.hasChildren():
            first = item.child(0)
            if first and first.data(QtCore.Qt.ItemDataRole.UserRole) is None:
                item.removeRows(0, item.rowCount())
                self._add_children_lazy(item, item.data(QtCore.Qt.ItemDataRole.UserRole))

    # ----------------- UI helpers -----------------
    def set_scanning(self, on: bool):
        if on:
            self.scan_progress.setVisible(True)
            self.scan_progress.setRange(0, 0)
            self.status.showMessage("Scanning workspaces…")
        else:
            self.scan_progress.setVisible(False)
            self.scan_progress.setRange(0, 1)

    def apply_ui_scale(self, old_scale: float, new_scale: float):
        try:
            app = QtWidgets.QApplication.instance()
            if not app:
                return
            f = app.font(); base = f.pointSizeF(); base = base if base > 0 else 9.0
            if old_scale and old_scale > 0:
                base = base / old_scale
            f.setPointSizeF(base * max(new_scale, 0.5))
            app.setFont(f)
        except Exception:
            pass
        # Re-apply folder tree widths
        try:
            if getattr(self, "folder_model_mode", "native") == "native":
                self.tree.setColumnWidth(0, self.settings.folders_column_width)
                self.tree.setColumnWidth(1, max(120, int(self.settings.folders_column_width * 0.5)))
            else:
                self.tree.setMinimumWidth(self.settings.folders_column_width)
        except Exception:
            pass

    def apply_layout_widths(self):
        # enforce left pane fixed width (Workspaces tab)
        try:
            # Find the left pane by walking children
            tabs = self.centralWidget()
            ws_widget = tabs.widget(0)
            split: QtWidgets.QSplitter = ws_widget.findChild(QtWidgets.QSplitter)
            if split:
                left = split.widget(0)
                left.setFixedWidth(self.settings.left_pane_width)
        except Exception:
            pass

    # ----------------- Refresh & Scan -----------------
    def refresh(self):
        # Update Folders tab root + widths
        if getattr(self, "folder_model_mode", "native") == "native":
            try:
                self.fs_model.setRootPath(str(self.settings.root_folder))
                self.tree.setRootIndex(self.fs_model.index(str(self.settings.root_folder)))
                self.tree.setColumnWidth(0, self.settings.folders_column_width)
                self.tree.setColumnWidth(1, max(120, int(self.settings.folders_column_width * 0.5)))
            except Exception:
                pass
        else:
            self.populate_manual_tree(str(self.settings.root_folder))
            try:
                self.tree.setMinimumWidth(self.settings.folders_column_width)
            except Exception:
                pass

        # Update native/fallback indicator
        self.mode_label.setText(f"Folders: {'native' if getattr(self, 'folder_model_mode', 'native') == 'native' else 'fallback'}")

        # Clear details
        self.lbl_name.setText("–"); self.txt_path.setPlainText(""); self.txt_desc.setPlainText("")
        self.btn_open.setEnabled(False)

        # Apply fixed left pane width (in case it changed)
        self.apply_layout_widths()

        # Start async scan
        self.start_scan()

    def start_scan(self):
        try:
            if getattr(self, "_scan_worker", None) is not None:
                self._scan_worker.cancel()
        except Exception:
            pass
        self._scan_gen = getattr(self, "_scan_gen", 0) + 1
        gen = self._scan_gen
        self.set_scanning(True)
        thread = QtCore.QThread(self)
        worker = ScanWorker(self.settings.root_folder, gen)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.on_scan_finished)
        worker.error.connect(self.on_scan_error)
        worker.progress.connect(self.on_scan_progress)
        worker.finished.connect(lambda *_: thread.quit())
        worker.finished.connect(lambda *_: worker.deleteLater())
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._scan_thread = thread
        self._scan_worker = worker

    @QtCore.pyqtSlot(list, int)
    def on_scan_finished(self, rows: List[WorkspaceInfo], gen: int):
        if gen != getattr(self, "_scan_gen", 0):
            return
        self.ws_model.update_rows(rows)
        self.ws_model.set_pinned(self.settings.pinned)
        self.ws_proxy.setPinnedSet(self.settings.pinned)
        self.ws_proxy.setMruSet(self.settings.mru)
        # Rebuild tag filter options
        all_tags = sorted({t for w in rows for t in (w.tags or [])}, key=lambda s: s.lower())
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear(); self.tag_combo.addItem("Any tag")
        for t in all_tags:
            self.tag_combo.addItem(t)
        self.tag_combo.blockSignals(False)

        self.status.showMessage(f"Found {len(rows)} workspaces under {self.settings.root_folder}")
        self.set_scanning(False)

    @QtCore.pyqtSlot(str, int)
    def on_scan_error(self, message: str, gen: int):
        if gen != getattr(self, "_scan_gen", 0):
            return
        self.set_scanning(False)
        QtWidgets.QMessageBox.warning(self, "Scan error", message)

    @QtCore.pyqtSlot(int, int)
    def on_scan_progress(self, count: int, gen: int):
        if gen != getattr(self, "_scan_gen", 0):
            return
        self.status.showMessage(f"Scanning workspaces… {count} found")

    # ----------------- Workspaces interactions -----------------
    def on_row_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            self.btn_open.setEnabled(False)
            return
        pidx = idxs[0]
        sidx = self.ws_proxy.mapToSource(pidx)
        ws = self.ws_model.workspace_at(sidx.row())
        self.lbl_name.setText(ws.name)
        self.txt_path.setPlainText(str(ws.path))
        self.txt_desc.setPlainText(ws.description or "")
        self.btn_open.setEnabled(True)

    def _update_mru(self, ws_path: str, max_items: int = 100):
        m = [p for p in self.settings.mru if p != ws_path]
        m.insert(0, ws_path)
        self.settings.mru = m[:max_items]
        save_settings(self.settings)
        self.ws_proxy.setMruSet(self.settings.mru)

    def on_open_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            return
        pidx = idxs[0]
        sidx = self.ws_proxy.mapToSource(pidx)
        ws = self.ws_model.workspace_at(sidx.row())
        ok, msg = self._launch_code([str(ws.path)])
        self.status.showMessage(msg, 5000)
        if ok:
            self._update_mru(str(ws.path))
        else:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    def on_toggle_pin(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            return
        sidx = self.ws_proxy.mapToSource(idxs[0])
        ws = self.ws_model.workspace_at(sidx.row())
        p = str(ws.path)
        if p in self.settings.pinned:
            self.settings.pinned = [x for x in self.settings.pinned if x != p]
        else:
            self.settings.pinned = [p] + [x for x in self.settings.pinned if x != p]
        save_settings(self.settings)
        self.ws_model.set_pinned(self.settings.pinned)
        self.ws_proxy.setPinnedSet(self.settings.pinned)

    def on_add(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Project", "New folder name:")
        if not ok or not name.strip():
            return
        folder = self.settings.root_folder / name.strip()
        if folder.exists():
            QtWidgets.QMessageBox.warning(self, "Exists", f"Folder already exists: {folder}")
            return
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Cannot create folder: {e}")
            return
        desc, _ = QtWidgets.QInputDialog.getText(self, "Description", "Workspace description (optional):")
        tags_str, _ = QtWidgets.QInputDialog.getText(self, "Tags", "Tags (comma-separated, optional):")
        tags = [t.strip() for t in (tags_str or "").split(',') if t.strip()]
        ws_path = folder / f"{name}.code-workspace"
        data = json.loads(json.dumps(DEFAULT_WS_CONTENT))  # deep copy
        if desc:
            data.setdefault("meta", {})["description"] = desc
        if tags:
            data.setdefault("meta", {}).setdefault("tags", tags)
        try:
            ws_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Cannot write workspace file: {e}")
            return
        self.refresh()
        ok, msg = self._launch_code([str(ws_path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    def on_fix_workspaces(self):
        # backfill missing fields across all workspaces
        rows = [self.ws_model.workspace_at(i) for i in range(self.ws_model.rowCount())]
        fixed = 0
        for ws in rows:
            try:
                p = Path(ws.path)
                data = json.loads(p.read_text(encoding="utf-8"))
                changed = False
                if not isinstance(data, dict):
                    continue
                if "folders" not in data or not data.get("folders"):
                    data["folders"] = [{"path": "."}]; changed = True
                if "settings" not in data or not isinstance(data.get("settings"), dict):
                    data["settings"] = {}; changed = True
                meta = data.get("meta")
                if not isinstance(meta, dict):
                    meta = {}; data["meta"] = meta; changed = True
                if "description" not in meta:
                    meta["description"] = ws.name; changed = True
                if "tags" not in meta or not isinstance(meta.get("tags"), list):
                    meta["tags"] = []; changed = True
                if changed:
                    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    fixed += 1
            except Exception:
                continue
        QtWidgets.QMessageBox.information(self, "Fix Workspaces", f"Updated {fixed} workspace file(s).")

    # ----------------- Folders interactions -----------------
    def on_folder_selected(self):
        has = bool(self.tree.selectionModel().selectedRows())
        self.btn_open_folder.setEnabled(has)

    def on_open_folder(self):
        idxs = self.tree.selectionModel().selectedRows()
        if not idxs:
            return
        index = idxs[0]
        if getattr(self, "folder_model_mode", "native") == "native":
            folder_path = Path(self.fs_model.filePath(index))
        else:
            item = self.std_model.itemFromIndex(index)
            folder_path = Path(item.data(QtCore.Qt.ItemDataRole.UserRole))
        ok, msg = self._launch_code([str(folder_path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    # ----------------- Settings & Help -----------------
    def on_settings(self):
        old_scale = float(getattr(self.settings, 'ui_scale', 1.0))
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.settings = dlg.get_settings()
            save_settings(self.settings)
            self.apply_ui_scale(old_scale, float(self.settings.ui_scale))
            self.apply_layout_widths()
            self.refresh()

    def on_help(self):
        text = (
            "<h3>VSCode Workspace Launcher — Help</h3>"
            "<p><b>Main features:</b></p>"
            "<ul>"
            "<li>Scan a root folder (recursively) for <code>*.code-workspace</code>.</li>"
            "<li>Open a workspace by double-click or the Open button.</li>"
            "<li>Add new project: creates a folder and a workspace file; optionally add description & tags.</li>"
            "<li>Folders tab: open any folder directly in VS Code (native or fallback view).</li>"
            "<li>Fix Workspaces: backfill missing fields in workspace JSON.</li>"
            "<li>Live search + filters: Mode (All/Pinned/Recent) and Tag (meta.tags).</li>"
            "</ul>"
            "<p><b>Workspace JSON fields used:</b></p>"
            "<ul>"
            "<li><code>folders</code> — array of folders; the app seeds it with <code>[{ 'path': '.' }]</code>.</li>"
            "<li><code>settings</code> — VS Code settings object (left as-is).</li>"
            "<li><code>meta.description</code> — custom description shown in the details pane.</li>"
            "<li><code>meta.tags</code> — custom list of tags (free-form strings).</li>"
            "</ul>"
            "<p><b>Tips:</b> Set VS Code CLI path in Settings if not in PATH. Adjust UI scale, left pane width, and folders width there too.</p>"
        )
        QtWidgets.QMessageBox.information(self, "Help", text)

    # ----------------- VS Code launcher -----------------
    def _launch_code(self, args: List[str]) -> tuple[bool, str]:
        exe = which_code(self.settings.code_path)
        if not exe:
            return False, "VS Code CLI not found. Set path in Settings."
        try:
            # Use Popen without waiting
            subprocess.Popen([exe] + args)
            return True, f"Launched: {shlex.join([exe] + args)}"
        except Exception as e:
            return False, f"Failed to launch VS Code: {e}"


# ----------------------------- App bootstrap -----------------------------
def main():
    # High DPI handling (Qt6: scaling is enabled by default). If available, set rounding policy.
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    
    # Set app icon
    try:
        app.setWindowIcon(QtGui.QIcon(APP_ICON_PNG)) 
    except Exception:
        pass 

    # Apply UI scale from stored settings at startup
    try:
        s = load_settings()
        ui_scale = float(getattr(s, 'ui_scale', 1.0))
        f = app.font()
        base = f.pointSizeF() or 9.0
        f.setPointSizeF(base * ui_scale)
        app.setFont(f)
    except Exception:
        pass

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
