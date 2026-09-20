"""Native file-tree browsing for completed logs and durable recovery artifacts."""

from pathlib import Path
import shutil

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QLineEdit, QPushButton, QFileSystemModel, QTreeView,
                             QFileDialog, QMessageBox)


class LibraryBrowser(QDialog):
    def __init__(self, parent, area, roots):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(area)
        self.resize(850, 550)
        self.roots = [root.resolve() for root in roots if root.is_dir()]
        layout = QVBoxLayout(self)
        self.location = QComboBox()
        for root in self.roots:
            self.location.addItem(str(root), root)
        layout.addWidget(self.location)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter filenames (wildcards supported)")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setVisible(bool(self.roots))
        self.tree.setColumnWidth(0, 360)
        self.tree.setSortingEnabled(True)
        layout.addWidget(self.tree, 1)
        self.notice = QLabel("No files are available yet." if not self.roots else
                            "Select a completed file to view or export a copy.")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.view = QPushButton("View CSV")
        self.view.setVisible(area == "Logs")
        self.view.clicked.connect(self.view_log)
        self.export = QPushButton("Export copy…")
        self.export.clicked.connect(self.export_copy)
        folder = QPushButton("Open folder")
        folder.clicked.connect(self.open_folder)
        folder.setEnabled(bool(self.roots))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for button in (self.view, self.export, folder, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search.textChanged.connect(
            lambda text: self.model.setNameFilters([f"*{text}*" if text else "*"]))
        self.location.currentIndexChanged.connect(self.change_root)
        self.tree.selectionModel().selectionChanged.connect(self.update_actions)
        self.tree.doubleClicked.connect(lambda: self.view_log() if area == "Logs" else None)
        self.change_root()

    def change_root(self, _index=None):
        root = self.location.currentData()
        if root:
            self.tree.setRootIndex(self.model.setRootPath(str(root)))
        self.update_actions()

    def selected_path(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        path = Path(self.model.filePath(index)).resolve()
        root = self.location.currentData()
        return path if root and root in path.parents and path.is_file() else None

    def update_actions(self, *_args):
        path = self.selected_path()
        idle = not getattr(self.parent(), "_task_busy", False)
        self.export.setEnabled(path is not None and idle)
        self.view.setEnabled(path is not None and path.suffix.lower() == ".csv" and idle)

    def view_log(self):
        path = self.selected_path()
        if path and path.suffix.lower() == ".csv" and not getattr(self.parent(), "_task_busy", False):
            from live_data_view import show_log
            show_log(self, path)

    def export_copy(self):
        path = self.selected_path()
        if path is None or getattr(self.parent(), "_task_busy", False):
            return
        name, _filter = QFileDialog.getSaveFileName(self, "Export Copy", path.name, "All Files (*)")
        if name:
            try:
                shutil.copyfile(path, name)
            except OSError as error:
                QMessageBox.warning(self, "Export Failed", str(error))

    def open_folder(self):
        root = self.location.currentData()
        if root and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))):
            QMessageBox.warning(self, "Folder Unavailable", str(root))
