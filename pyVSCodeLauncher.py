#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VSCode Workspace Launcher

Cross‑platform launcher for managing and opening VS Code workspaces and folders.

Requirements:
    - Python 3.9+
    - pip install PyQt6 platformdirs

Features:
    - Settings dialog with persistent options (root scan folder, VS Code binary path).
    - Recursively scans for *.code-workspace files under the root folder.
    - Shows a list of workspace names on the left and a details pane on the right (path + description).
    - Single‑click shows details and enables "Open".
    - Double‑click (or Open button) launches workspace in VS Code.
    - "Add" lets you create a new project folder + prefilled .code-workspace, then opens it in VS Code.
    - A separate tab shows a folder tree; selecting a folder enables opening it directly in VS Code.
    - Reads optional description from workspace JSON at meta.description (custom key for this app).

Notes:
    - If the VS Code CLI ("code") is not in PATH, set its path in Settings.
    - On Linux it is often /usr/bin/code; on Windows it is typically C:\\Users\\<you>\\AppData\\Local\\Programs\\Microsoft VS Code\\bin\\code.cmd
"""

from __future__ import annotations
import json
import os
import sys
import subprocess
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
# Try to import QFileSystemModel from Widgets first, then Gui (PyQt6 distributions differ)
try:
    from PyQt6.QtWidgets import QFileSystemModel as Qt_QFileSystemModel  # type: ignore
except Exception:
    try:
        from PyQt6.QtGui import QFileSystemModel as Qt_QFileSystemModel  # type: ignore
    except Exception:
        Qt_QFileSystemModel = None  # Fallback to manual tree
from platformdirs import user_config_dir

APP_NAME = "vscode-workspace-launcher"
ORG_NAME = "yegor-tools"
CONFIG_DIR = Path(user_config_dir(APP_NAME, ORG_NAME))
CONFIG_PATH = CONFIG_DIR / "config.json"

SUPPORTED_EXT = ".code-workspace"
DEFAULT_WS_CONTENT = {
    "folders": [
        {"path": "."}
    ],
    "settings": {},
    # Custom field for this app:
    "meta": {
        "description": "New workspace",
        "tags": []
    }
}

@dataclass
class Settings:
    root_folder: Path
    code_path: Optional[Path]
    ui_scale: float = 1.0  # multiplicator for font sizes (1.0 = 100%)
    folders_column_width: int = 360  # pixels for folders tree first column

    def to_json(self) -> dict:
        return {
            "root_folder": str(self.root_folder) if self.root_folder else "",
            "code_path": str(self.code_path) if self.code_path else "",
            "ui_scale": self.ui_scale,
            "folders_column_width": self.folders_column_width,
        }

    @staticmethod
    def from_json(data: dict) -> "Settings":
        root = Path(data.get("root_folder") or str(Path.home()))
        codep = data.get("code_path")
        ui_scale = float(data.get("ui_scale", 1.0))
        folders_w = int(data.get("folders_column_width", 360))
        return Settings(root_folder=root, code_path=Path(codep) if codep else None,
                        ui_scale=ui_scale, folders_column_width=folders_w)

    @staticmethod
    def default() -> "Settings":
        # default root to home directory
        return Settings(root_folder=Path.home(), code_path=None, ui_scale=1.0, folders_column_width=360)



def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    ensure_config_dir()
    if CONFIG_PATH.exists():
        try:
            return Settings.from_json(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    s = Settings.default()
    save_settings(s)
    return s


def save_settings(s: Settings) -> None:
    ensure_config_dir()
    CONFIG_PATH.write_text(json.dumps(s.to_json(), indent=2), encoding="utf-8")


@dataclass
class WorkspaceInfo:
    name: str
    path: Path
    description: str
    mtime: float

    @property
    def mtime_dt(self) -> datetime:
        return datetime.fromtimestamp(self.mtime)


class WorkspaceScanner:
    def __init__(self, root: Path):
        self.root = root

    def scan(self) -> List[WorkspaceInfo]:
        workspaces: List[WorkspaceInfo] = []
        if not self.root.exists():
            return workspaces
        for p in self.root.rglob(f"*{SUPPORTED_EXT}"):
            try:
                info = self._read_workspace(p)
                workspaces.append(info)
            except Exception:
                # Skip unreadable files
                continue
        # sort by name, then by path
        workspaces.sort(key=lambda w: (w.name.lower(), str(w.path).lower()))
        return workspaces

    def _read_workspace(self, ws_path: Path) -> WorkspaceInfo:
        text = ws_path.read_text(encoding="utf-8")
        data = json.loads(text)
        # description: prefer meta.description, fallback to top-level description, else empty
        desc = ""
        if isinstance(data, dict):
            meta = data.get("meta")
            if isinstance(meta, dict):
                desc = str(meta.get("description", ""))
            if not desc:
                desc = str(data.get("description", ""))
        name = ws_path.stem
        mtime = ws_path.stat().st_mtime
        return WorkspaceInfo(name=name, path=ws_path, description=desc, mtime=mtime)


class WorkspaceListModel(QtCore.QAbstractListModel):
    def __init__(self, rows: List[WorkspaceInfo]):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self.rows)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return row.name
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return str(row.path)
        return None

    def workspace_at(self, row: int) -> WorkspaceInfo:
        return self.rows[row]

    def update_rows(self, rows: List[WorkspaceInfo]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class WorkspaceFilterProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._pattern = ""
        self.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)

    @QtCore.pyqtSlot(str)
    def setFilterText(self, text: str):
        self._pattern = (text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QtCore.QModelIndex) -> bool:
        if not self._pattern:
            return True
        src: WorkspaceListModel = self.sourceModel()  # type: ignore
        if not src or source_row < 0 or source_row >= src.rowCount():
            return True
        ws = src.workspace_at(source_row)
        pat = self._pattern
        return (
            pat in ws.name.lower()
            or pat in (ws.description or "").lower()
            or pat in str(ws.path).lower()
        )


class ScanWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(list, int)  # rows, generation
    error = QtCore.pyqtSignal(str, int)      # message, generation

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
                            if isinstance(data, dict):
                                meta = data.get("meta")
                                if isinstance(meta, dict):
                                    desc = str(meta.get("description", ""))
                                if not desc:
                                    desc = str(data.get("description", ""))
                            rows.append(WorkspaceInfo(name=p.stem, path=p, description=desc, mtime=p.stat().st_mtime))
                        except Exception:
                            continue
                if self._cancel:
                    # return empty to avoid overriding newer scans
                    self.finished.emit([], self.gen)
                    return
            rows.sort(key=lambda w: (w.name.lower(), str(w.path).lower()))
            self.finished.emit(rows, self.gen)
        except Exception as e:
            self.error.emit(str(e), self.gen)

    @QtCore.pyqtSlot()
    def cancel(self):
        self._cancel = True


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

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
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
        return Settings(root_folder=root, code_path=codep, ui_scale=ui_scale, folders_column_width=folders_w)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VSCode Workspace Launcher")
        self.resize(1100, 700)

        self.settings = load_settings()

        # Central: tabs
        tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(tabs)

        # Tab 1: Workspaces
        ws_widget = QtWidgets.QWidget()
        ws_layout = QtWidgets.QVBoxLayout(ws_widget)

        toolbar = QtWidgets.QToolBar()
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        act_settings = QtGui.QAction("Settings", self); act_settings.triggered.connect(self.on_settings)
        act_rescan = QtGui.QAction("Rescan", self); act_rescan.triggered.connect(self.refresh)
        act_add = QtGui.QAction("Add", self); act_add.triggered.connect(self.on_add)
        toolbar.addAction(act_settings)
        toolbar.addAction(act_rescan)
        toolbar.addSeparator()
        toolbar.addAction(act_add)
        toolbar.addSeparator()
        act_fix = QtGui.QAction("Fix Workspaces", self); act_fix.triggered.connect(self.on_fix_workspaces)
        toolbar.addAction(act_fix)
        toolbar.addSeparator()
        act_help = QtGui.QAction("Help", self); act_help.triggered.connect(self.on_help)
        toolbar.addAction(act_help)

        # Split view: left list of workspaces, right details
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget(); left_layout = QtWidgets.QVBoxLayout(left)
        # Live search box
        self.search_edit = QtWidgets.QLineEdit(); self.search_edit.setPlaceholderText("Filter by name, path, or description…")
        try:
            self.search_edit.setClearButtonEnabled(True)
        except Exception:
            pass
        left_layout.addWidget(self.search_edit)

        self.ws_list = QtWidgets.QListView()
        self.ws_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.ws_list.doubleClicked.connect(self.on_open_selected)
        left_layout.addWidget(self.ws_list)
        split.addWidget(left)

        right = QtWidgets.QWidget(); right_layout = QtWidgets.QVBoxLayout(right)
        details = QtWidgets.QGroupBox("Details")
        form = QtWidgets.QFormLayout(details)
        self.lbl_name = QtWidgets.QLabel("–")
        self.lbl_path = QtWidgets.QLabel("–"); self.lbl_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.txt_desc = QtWidgets.QPlainTextEdit(); self.txt_desc.setReadOnly(True); self.txt_desc.setFixedHeight(120)
        form.addRow("Name:", self.lbl_name)
        form.addRow("Path:", self.lbl_path)
        form.addRow("Description:", self.txt_desc)
        right_layout.addWidget(details)

        self.btn_open = QtWidgets.QPushButton("Open in VS Code")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.on_open_selected)
        right_layout.addWidget(self.btn_open)
        right_layout.addStretch(1)

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        ws_layout.addWidget(split)

        # Connect selection change to show details
        # (selection model will be available after setting model below)

        tabs.addTab(ws_widget, "Workspaces")

        # Tab 2: Folders
        folders_widget = QtWidgets.QWidget()
        f_layout = QtWidgets.QVBoxLayout(folders_widget)
        self.tree = QtWidgets.QTreeView()
        self.tree.setHeaderHidden(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(self.settings.folders_column_width)

        if Qt_QFileSystemModel is not None:
            self.folder_model_mode = "qfile"
            self.fs_model = Qt_QFileSystemModel()
            try:
                self.fs_model.setFilter(QtCore.QDir.Filter.AllDirs | QtCore.QDir.Filter.NoDotAndDotDot)
            except Exception:
                pass
            self.fs_model.setRootPath(str(self.settings.root_folder))
            self.tree.setModel(self.fs_model)
            self.tree.setRootIndex(self.fs_model.index(str(self.settings.root_folder)))
            # widen the first columns for visibility
            try:
                self.tree.setColumnWidth(0, self.settings.folders_column_width)
                self.tree.setColumnWidth(1, max(120, int(self.settings.folders_column_width * 0.5)))
            except Exception:
                pass
            self.tree.doubleClicked.connect(self.on_open_folder)
            self.tree.selectionModel().selectionChanged.connect(self.on_folder_selected)
        else:
            self.folder_model_mode = "manual"
            self.std_model = QtGui.QStandardItemModel()
            self.std_model.setHorizontalHeaderLabels(["Folders"])
            self.tree.setModel(self.std_model)
            self.populate_manual_tree(str(self.settings.root_folder))
            self.tree.expanded.connect(self.on_manual_expand)
            self.tree.selectionModel().selectionChanged.connect(self.on_folder_selected)

        f_layout.addWidget(self.tree)

        self.btn_open_folder = QtWidgets.QPushButton("Open Folder in VS Code")
        self.btn_open_folder.setEnabled(False)
        self.btn_open_folder.clicked.connect(self.on_open_folder)
        f_layout.addWidget(self.btn_open_folder)

        tabs.addTab(folders_widget, "Folders")

        # Status bar
        self.status = self.statusBar()
        # Show native/fallback mode indicator in the status bar
        self.mode_label = QtWidgets.QLabel("")
        try:
            self.status.addPermanentWidget(self.mode_label)
        except Exception:
            pass
        # Progress bar for async scan
        self.scan_progress = QtWidgets.QProgressBar()
        self.scan_progress.setFixedWidth(140)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setVisible(False)
        try:
            self.status.addPermanentWidget(self.scan_progress)
        except Exception:
            pass

        # Initial data
        self.ws_model = WorkspaceListModel([])
        self.ws_proxy = WorkspaceFilterProxyModel()
        self.ws_proxy.setSourceModel(self.ws_model)
        self.ws_list.setModel(self.ws_proxy)
        self.ws_list.selectionModel().selectionChanged.connect(self.on_row_selected)
        self.search_edit.textChanged.connect(self.ws_proxy.setFilterText)

        # Async scan state
        self._scan_gen = 0
        self._scan_thread = None
        self._scan_worker = None

        self.refresh()

    # --- Utilities ---
    def _guess_code_bin(self) -> Optional[str]:
        # If user specified a path, use it
        if self.settings.code_path and self.settings.code_path.exists():
            return str(self.settings.code_path)
        # Try PATH
        for candidate in ["code", "code-insiders"]:
            if shutil_which := shutil_which_fallback(candidate):
                return shutil_which
        # Try common locations
        common = []
        if sys.platform.startswith("win"):
            common += [
                str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"),
                r"C:\\Program Files\\Microsoft VS Code\\bin\\code.cmd",
                r"C:\\Program Files (x86)\\Microsoft VS Code\\bin\\code.cmd",
            ]
        else:
            common += ["/usr/bin/code", "/usr/local/bin/code", "/var/lib/snapd/snap/bin/code"]
        for p in common:
            if Path(p).exists():
                return p
        return None

    def _launch_code(self, args: List[str]) -> Tuple[bool, str]:
        code_bin = self._guess_code_bin()
        if not code_bin:
            return False, "VS Code CLI not found. Set the path in Settings."
        try:
            # Use shell=False; quote paths properly
            proc = subprocess.Popen([code_bin] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Don't wait; VS Code detaches
            return True, "Launched VS Code."
        except Exception as e:
            return False, f"Failed to launch VS Code: {e}"

    # --- Actions ---
    def refresh(self):
        # Adjust tree root for both modes immediately (reflect settings changes)
        if getattr(self, "folder_model_mode", "qfile") == "qfile":
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
        try:
            self.mode_label.setText(f"Folders: {'native' if getattr(self, 'folder_model_mode', 'qfile') == 'qfile' else 'fallback'}")
        except Exception:
            pass

        # clear details
        self.lbl_name.setText("–")
        self.lbl_path.setText("–")
        self.txt_desc.setPlainText("")
        self.btn_open.setEnabled(False)

        # Start async scan
        self.start_scan()

    def set_scanning(self, on: bool):
        try:
            if on:
                self.scan_progress.setVisible(True)
                self.scan_progress.setRange(0, 0)  # busy
                self.status.showMessage("Scanning workspaces…")
            else:
                self.scan_progress.setVisible(False)
                self.scan_progress.setRange(0, 1)
        except Exception:
            pass

    def start_scan(self):
        # cancel previous worker if any
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
        worker.finished.connect(lambda *_: thread.quit())
        worker.finished.connect(lambda *_: worker.deleteLater())
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._scan_thread = thread
        self._scan_worker = worker

    @QtCore.pyqtSlot(list, int)
    def on_scan_finished(self, rows: List[WorkspaceInfo], gen: int):
        if gen != getattr(self, "_scan_gen", 0):
            # stale result
            return
        self.ws_model.update_rows(rows)
        try:
            self.status.showMessage(f"Found {len(rows)} workspaces under {self.settings.root_folder}")
        except Exception:
            pass
        self.set_scanning(False)

    @QtCore.pyqtSlot(str, int)
    def on_scan_error(self, message: str, gen: int):
        if gen != getattr(self, "_scan_gen", 0):
            return
        self.set_scanning(False)
        QtWidgets.QMessageBox.warning(self, "Scan error", message)

    def on_row_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            self.btn_open.setEnabled(False)
            return
        pidx = idxs[0]
        try:
            sidx = self.ws_proxy.mapToSource(pidx)
            ws = self.ws_model.workspace_at(sidx.row())
        except Exception:
            self.btn_open.setEnabled(False)
            return
        self.lbl_name.setText(ws.name)
        self.lbl_path.setText(str(ws.path))
        self.txt_desc.setPlainText(ws.description or "")
        self.btn_open.setEnabled(True)

    def on_open_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            return
        pidx = idxs[0]
        try:
            sidx = self.ws_proxy.mapToSource(pidx)
            ws = self.ws_model.workspace_at(sidx.row())
        except Exception:
            return
        ok, msg = self._launch_code([str(ws.path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    def apply_ui_scale(self, old_scale: float, new_scale: float):
        """Apply UI scale immediately without app restart.
        Computes a baseline from the current app font divided by old_scale, then applies new_scale.
        Also reapplies folder tree width hints.
        """
        try:
            app = QtWidgets.QApplication.instance()
            if not app:
                return
            f = app.font()
            base = f.pointSizeF()
            if base <= 0:
                base = 9.0
            if old_scale and old_scale > 0:
                base = base / old_scale
            f.setPointSizeF(base * max(new_scale, 0.5))
            app.setFont(f)
        except Exception:
            pass
        # Re-apply folder tree widths
        try:
            self.tree.setMinimumWidth(self.settings.folders_column_width)
            if getattr(self, "folder_model_mode", "qfile") == "qfile":
                self.tree.setColumnWidth(0, self.settings.folders_column_width)
                self.tree.setColumnWidth(1, max(120, int(self.settings.folders_column_width * 0.5)))
        except Exception:
            pass

    
    def on_settings(self):
        old_scale = float(getattr(self.settings, 'ui_scale', 1.0))
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            new_settings = dlg.get_settings()
            self.settings = new_settings
            save_settings(self.settings)
            # Apply new UI scale immediately
            self.apply_ui_scale(old_scale, float(getattr(self.settings, 'ui_scale', 1.0)))
            self.refresh()

    def on_add(self):
        # 1) Ask folder name under root
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
        # 2) Optional description
        desc, _ = QtWidgets.QInputDialog.getText(self, "Description", "Workspace description (optional):")
        # 2b) Optional tags (comma-separated)
        tags_str, _ = QtWidgets.QInputDialog.getText(self, "Tags", "Tags (comma-separated, optional):")
        tags = [t.strip() for t in tags_str.split(',')] if tags_str else []
        tags = [t for t in tags if t]
        # 3) Create workspace file
        ws_path = folder / f"{name}.code-workspace"
        data = DEFAULT_WS_CONTENT.copy()
        data = json.loads(json.dumps(data))  # deep copy
        if desc:
            data.setdefault("meta", {})["description"] = desc
        if tags:
            data.setdefault("meta", {}).setdefault("tags", tags)
        try:
            ws_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Cannot write workspace file: {e}")
            return
        # 4) Refresh and open
        self.refresh()
        ok, msg = self._launch_code([str(ws_path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    # --- Folders tab ---
    def on_folder_selected(self):
        idxs = self.tree.selectionModel().selectedRows()
        self.btn_open_folder.setEnabled(bool(idxs))

    def on_open_folder(self):
        idxs = self.tree.selectionModel().selectedRows()
        if not idxs:
            return
        index = idxs[0]
        if getattr(self, "folder_model_mode", "qfile") == "qfile":
            folder_path = Path(self.fs_model.filePath(index))
        else:
            item = self.std_model.itemFromIndex(index)
            folder_path = Path(item.data(QtCore.Qt.ItemDataRole.UserRole))
        ok, msg = self._launch_code([str(folder_path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    def on_fix_workspaces(self):
        scanner = WorkspaceScanner(self.settings.root_folder)
        rows = scanner.scan()
        fixed = 0
        for ws in rows:
            try:
                data = json.loads(Path(ws.path).read_text(encoding="utf-8"))
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
                    Path(ws.path).write_text(json.dumps(data, indent=2), encoding="utf-8")
                    fixed += 1
            except Exception:
                continue
        QtWidgets.QMessageBox.information(self, "Fix Workspaces", f"Updated {fixed} workspace file(s).")

    def on_help(self):
        text = (
            "<h3>VSCode Workspace Launcher — Help</h3>"
            "<p><b>Main features:</b></p>"
            "<ul>"
            "<li>Scan a root folder (recursively) for <code>*.code-workspace</code>.</li>"
            "<li>Open a workspace by double-click or the Open button.</li>"
            "<li>Add new project: creates a folder and a workspace file; optionally add description & tags.</li>"
            "<li>Folders tab: open any folder directly in VS Code.</li>"
            "<li>Fix Workspaces: backfill missing fields in workspace JSON.</li>"
            "</ul>"
            "<p><b>Workspace JSON fields used:</b></p>"
            "<ul>"
            "<li><code>folders</code> — array of folders; the app seeds it with <code>[{ 'path': '.' }]</code>.</li>"
            "<li><code>settings</code> — VS Code settings object (left as-is).</li>"
            "<li><code>meta.description</code> — <i>custom</i> description shown in the details pane.</li>"
            "<li><code>meta.tags</code> — <i>custom</i> list of tags (free-form strings).</li>"
            "</ul>"
            "<p><b>Tips:</b> Set VS Code CLI path in Settings if not in PATH. Adjust UI scale and Folders width there too.</p>"
        )
        QtWidgets.QMessageBox.information(self, "Help", text)

# Manual tree helpers (fallback when QFileSystemModel is unavailable)
    def populate_manual_tree(self, root_path: str):
        try:
            self.std_model.removeRows(0, self.std_model.rowCount())
        except Exception:
            pass
        root_item = QtGui.QStandardItem(Path(root_path).name)
        root_item.setEditable(False)
        root_item.setData(root_path, QtCore.Qt.ItemDataRole.UserRole)
        self.std_model.appendRow(root_item)
        self._add_children_lazy(root_item, root_path)
        try:
            self.tree.expand(self.std_model.index(0, 0))
        except Exception:
            pass

    def _add_children_lazy(self, parent_item: QtGui.QStandardItem, parent_path: str):
        try:
            with os.scandir(parent_path) as it:
                for entry in it:
                    if entry.is_dir():
                        child = QtGui.QStandardItem(entry.name)
                        child.setEditable(False)
                        child.setData(entry.path, QtCore.Qt.ItemDataRole.UserRole)
                        # Placeholder so we can lazy-load on expand
                        child.appendRow(QtGui.QStandardItem("…"))
                        parent_item.appendRow(child)
        except Exception:
            pass

    def on_manual_expand(self, index: QtCore.QModelIndex):
        if getattr(self, "folder_model_mode", "qfile") != "manual":
            return
        item = self.std_model.itemFromIndex(index)
        if not item:
            return
        # If first child is a placeholder, replace with real children
        if item.hasChildren():
            first = item.child(0)
            if first and first.data(QtCore.Qt.ItemDataRole.UserRole) is None:
                item.removeRows(0, item.rowCount())
                self._add_children_lazy(item, item.data(QtCore.Qt.ItemDataRole.UserRole))


def shutil_which_fallback(cmd: str) -> Optional[str]:
    # minimal which implementation to avoid importing shutil on very old Pythons
    from shutil import which
    return which(cmd)


def main():
    # High DPI handling (Qt6: scaling is enabled by default). If available, set rounding policy.
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)

    # Apply UI scale from settings
    try:
        s = load_settings()
        ui_scale = float(getattr(s, 'ui_scale', 1.0))
        f = app.font()
        base = f.pointSizeF()
        if base <= 0:
            base = 9.0
        f.setPointSizeF(base * ui_scale)
        app.setFont(f)
    except Exception:
        pass
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
