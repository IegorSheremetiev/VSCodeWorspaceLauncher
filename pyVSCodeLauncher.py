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
    "meta": {"description": "New workspace"}
}

@dataclass
class Settings:
    root_folder: Path
    code_path: Optional[Path]

    def to_json(self) -> dict:
        return {
            "root_folder": str(self.root_folder) if self.root_folder else "",
            "code_path": str(self.code_path) if self.code_path else ""
        }

    @staticmethod
    def from_json(data: dict) -> "Settings":
        root = Path(data.get("root_folder") or str(Path.home()))
        codep = data.get("code_path")
        return Settings(root_folder=root, code_path=Path(codep) if codep else None)

    @staticmethod
    def default() -> "Settings":
        # default root to home directory
        return Settings(root_folder=Path.home(), code_path=None)


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


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.settings = settings

        self.root_edit = QtWidgets.QLineEdit(str(self.settings.root_folder))
        self.code_edit = QtWidgets.QLineEdit(str(self.settings.code_path) if self.settings.code_path else "")

        browse_root_btn = QtWidgets.QPushButton("Browse…")
        browse_root_btn.clicked.connect(self._browse_root)
        browse_code_btn = QtWidgets.QPushButton("Browse…")
        browse_code_btn.clicked.connect(self._browse_code)

        form = QtWidgets.QFormLayout()
        h1 = QtWidgets.QHBoxLayout(); h1.addWidget(self.root_edit); h1.addWidget(browse_root_btn)
        h2 = QtWidgets.QHBoxLayout(); h2.addWidget(self.code_edit); h2.addWidget(browse_code_btn)
        form.addRow("Root folder:", self._wrap(h1))
        form.addRow("VS Code binary (optional):", self._wrap(h2))

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
        return Settings(root_folder=root, code_path=codep)


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

        # Split view: left list of workspaces, right details
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget(); left_layout = QtWidgets.QVBoxLayout(left)
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
        self.tree.setHeaderHidden(True)

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

        # Initial data
        self.ws_model = WorkspaceListModel([])
        self.ws_list.setModel(self.ws_model)
        self.ws_list.selectionModel().selectionChanged.connect(self.on_row_selected)
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
        scanner = WorkspaceScanner(self.settings.root_folder)
        rows = scanner.scan()
        self.ws_model.update_rows(rows)
        self.status.showMessage(f"Found {len(rows)} workspaces under {self.settings.root_folder}")

        # adjust tree root for both modes
        if getattr(self, "folder_model_mode", "qfile") == "qfile":
            try:
                self.fs_model.setRootPath(str(self.settings.root_folder))
                self.tree.setRootIndex(self.fs_model.index(str(self.settings.root_folder)))
            except Exception:
                pass
        else:
            self.populate_manual_tree(str(self.settings.root_folder))

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

    def on_row_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            self.btn_open.setEnabled(False)
            return
        idx = idxs[0]
        ws = self.ws_model.workspace_at(idx.row())
        self.lbl_name.setText(ws.name)
        self.lbl_path.setText(str(ws.path))
        self.txt_desc.setPlainText(ws.description or "")
        self.btn_open.setEnabled(True)

    def on_open_selected(self):
        idxs = self.ws_list.selectionModel().selectedIndexes()
        if not idxs:
            return
        ws = self.ws_model.workspace_at(idxs[0].row())
        ok, msg = self._launch_code([str(ws.path)])
        self.status.showMessage(msg, 5000)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Error", msg)

    
    def on_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.settings = dlg.get_settings()
            save_settings(self.settings)
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
        # 3) Create workspace file
        ws_path = folder / f"{name}.code-workspace"
        data = DEFAULT_WS_CONTENT.copy()
        data = json.loads(json.dumps(data))  # deep copy
        if desc:
            data.setdefault("meta", {})["description"] = desc
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
    # High DPI on Linux/Windows
    # High DPI handling (Qt6: scaling is enabled by default). If available, set rounding policy.
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
