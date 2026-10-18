import sys
import threading

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

MOON_SIZE = 290
MOON_CRATERS = (
    (190, 170, 42, 35, -18), (110, 112, 27, 22, -25), (148, 218, 19, 15, 0), (82, 176, 12, 10, 0),
    (226, 104, 11, 9, 0), (146, 68, 9, 8, 0), (214, 226, 9, 8, 0), (62, 130, 6, 5, 0),
)
MOON_PATH = QPainterPath(QPointF(38, 158))
MOON_PATH.cubicTo(30, 104, 80, 52, 146, 42)
MOON_PATH.cubicTo(212, 34, 264, 70, 270, 132)
MOON_PATH.cubicTo(276, 196, 232, 256, 166, 264)
MOON_PATH.cubicTo(100, 272, 46, 216, 38, 158)
MOON_PATH.closeSubpath()

STYLE = """
QWidget { color: #d6d7de; font-size: 13px; }
QLabel#h1 { font-size: 16px; font-weight: 600; color: #fff; }
QLabel#muted { color: #8a8c98; font-size: 12px; }
QLabel#name { color: #fff; }
QWidget#side { background: #141519; border-right: 1px solid #26272d; }
QLabel#brand { font-weight: 600; color: #fff; padding: 0 18px; }
QLabel#version { color: #737580; font-size: 11px; padding: 0 18px 14px 18px; }
QWidget#footer { background: #141519; border-top: 1px solid #26272d; }
QListWidget#nav { background: transparent; border: 0; outline: 0; }
QListWidget#nav::item { padding: 8px 18px; color: #9a9ca8; border-left: 2px solid transparent; }
QListWidget#nav::item:hover { background: #1b1c21; color: #fff; }
QListWidget#nav::item:selected { background: #1b1c21; color: #fff; border-left: 2px solid #5b8def; }
QListWidget#rows { background: transparent; border: 0; outline: 0; }
QListWidget#rows::item { border-bottom: 1px solid #26272d; }
QComboBox, QLineEdit { background: #23242a; border: 1px solid #34353d; border-radius: 3px; padding: 5px 9px; }
QComboBox:focus, QLineEdit:focus { border-color: #5b8def; }
QComboBox QAbstractItemView { background: #23242a; selection-background-color: #5b8def; border: 1px solid #34353d; }
QPushButton { background: #2a2b32; border: 1px solid #3a3b44; border-radius: 3px; padding: 6px 16px; }
QPushButton:hover { background: #32333b; }
QPushButton:disabled { color: #666874; }
QPushButton#primary { background: #5b8def; border-color: #5b8def; color: #fff; }
QPushButton#primary:hover { background: #6d9aff; }
QPushButton#primary:disabled { background: #3b4f78; border-color: #3b4f78; color: #9aa6c0; }
QProgressBar { background: #26272d; border: 0; }
QProgressBar::chunk { background: #5b8def; }
QSlider::groove:horizontal { height: 3px; background: #34353d; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: #5b8def; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #3a3b44; border-radius: 2px; background: #23242a; }
QCheckBox::indicator:checked { background: #5b8def; border-color: #5b8def; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #3a3b44; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class Bridge(QObject):
    finished = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.finished.connect(lambda callback, value, error: callback(value, error))

    def run(self, func, callback):
        def job():
            try:
                value, error = func(), None
            except Exception as exc:
                value, error = None, exc
            self.finished.emit(callback, value, error)

        threading.Thread(target=job, daemon=True).start()


def field(text, widget):
    box = QVBoxLayout()
    box.setSpacing(5)
    label = QLabel(text)
    label.setObjectName("muted")
    box.addWidget(label)
    box.addWidget(widget)
    return box


def label(text, name=""):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def action(button):
    button.setProperty("act", True)
    return button


class Window(QWidget):
    def __init__(self, api):
        super().__init__()
        self.api = api
        self.bridge = Bridge()
        self.settings = api.get_settings()
        self.busy = self.was_busy = self.reload_after = False
        self.setWindowTitle("Deimos Launcher")
        self.resize(980, 620)
        self.setMinimumSize(860, 540)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(["Play", "Mods", "Settings"])
        side = QWidget()
        side.setObjectName("side")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(150)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 18, 0, 18)
        side_layout.setSpacing(0)
        for widget in (label("Deimos", "brand"), label("Launcher 0.1", "version"), self.nav):
            side_layout.addWidget(widget)

        self.pages = QStackedWidget()
        for build in (self.build_play, self.build_mods, self.build_settings):
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(28, 22, 28, 22)
            content = build()
            content.setFixedWidth(540)
            row.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
            row.addStretch(1)
            self.pages.addWidget(holder)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.status = QLabel("Ready")
        footer = QWidget()
        footer.setObjectName("footer")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(28, 7, 28, 8)
        footer_layout.addWidget(self.status)

        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self.pages, 1)
        right.addWidget(self.progress)
        right.addWidget(footer)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(side)
        root.addLayout(right, 1)

        self.nav.currentRowChanged.connect(self.switch_page)
        self.nav.setCurrentRow(0)
        self.apply_settings()
        self.load_versions()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)

    def build_play(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.version = QComboBox()
        self.loader = QComboBox()
        for title, value in (("Vanilla", "vanilla"), ("Fabric", "fabric"), ("Quilt", "quilt")):
            self.loader.addItem(title, value)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addLayout(field("Version", self.version))
        columns.addLayout(field("Loader", self.loader))
        self.snapshots = QCheckBox("Show snapshots")
        self.username = QLineEdit()
        self.username.setMaxLength(16)
        self.play_button = action(QPushButton("Play"))
        self.play_button.setObjectName("primary")
        self.play_button.setMinimumHeight(34)
        layout.addWidget(label("Play", "h1"))
        layout.addLayout(columns)
        layout.addWidget(self.snapshots)
        layout.addLayout(field("Username", self.username))
        layout.addWidget(self.play_button)
        layout.addStretch(1)
        self.version.currentIndexChanged.connect(self.save)
        self.loader.currentIndexChanged.connect(self.save)
        self.username.editingFinished.connect(self.save)
        self.snapshots.toggled.connect(self.snapshots_toggled)
        self.play_button.clicked.connect(self.play)
        return page

    def build_mods(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.mods_target = label("", "muted")
        self.mods_body = QWidget()
        body = QVBoxLayout(self.mods_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search Modrinth")
        search_button = QPushButton("Search")
        search = QHBoxLayout()
        search.addWidget(self.query, 1)
        search.addWidget(search_button)
        self.results = QListWidget()
        self.installed = QListWidget()
        for rows in (self.results, self.installed):
            rows.setObjectName("rows")
            rows.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            rows.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setFixedHeight(230)
        self.installed.setFixedHeight(130)
        body.addLayout(search)
        body.addWidget(self.results)
        body.addWidget(label("Installed", "muted"))
        body.addWidget(self.installed)
        layout.addWidget(label("Mods", "h1"))
        layout.addWidget(self.mods_target)
        layout.addWidget(self.mods_body)
        layout.addStretch(1)
        search_button.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        return page

    def build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.memory = QSlider(Qt.Orientation.Horizontal)
        self.memory.setRange(2, 32)
        self.memory_label = label("", "muted")
        self.java = QLineEdit()
        self.java.setPlaceholderText("Auto-detect")
        self.java_version = QComboBox()
        for major in (8, 17, 21, 25):
            self.java_version.addItem(f"Java {major}", major)
        self.java_version.setCurrentIndex(2)
        self.install_button = action(QPushButton("Install"))
        install = QHBoxLayout()
        install.addWidget(self.java_version)
        install.addWidget(self.install_button)
        install.addStretch(1)
        layout.addWidget(label("Settings", "h1"))
        layout.addWidget(self.memory_label)
        layout.addWidget(self.memory)
        layout.addLayout(field("Java path", self.java))
        layout.addWidget(label("Install Java", "muted"))
        layout.addLayout(install)
        open_root = QPushButton("Open launcher folder")
        open_versions = QPushButton("Open versions folder")
        folders = QHBoxLayout()
        folders.addWidget(open_root)
        folders.addWidget(open_versions)
        folders.addStretch(1)
        self.installed_versions = QComboBox()
        self.installed_versions.setMinimumWidth(200)
        self.delete_button = action(QPushButton("Delete"))
        delete = QHBoxLayout()
        delete.addWidget(self.installed_versions)
        delete.addWidget(self.delete_button)
        delete.addStretch(1)
        layout.addWidget(label("Folders", "muted"))
        layout.addLayout(folders)
        layout.addWidget(label("Delete version", "muted"))
        layout.addLayout(delete)
        open_root.clicked.connect(lambda: self.api.open_folder("root"))
        open_versions.clicked.connect(lambda: self.api.open_folder("versions"))
        self.delete_button.clicked.connect(self.delete_version)
        note = label("The launcher picks the Java version each game version needs, including installed runtimes.", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.memory.valueChanged.connect(self.update_memory)
        self.memory.sliderReleased.connect(self.save)
        self.java.editingFinished.connect(self.save)
        self.install_button.clicked.connect(lambda: self.api.install_java(self.java_version.currentData()))
        return page

    def apply_settings(self):
        self.username.setText(self.settings["username"])
        self.java.setText(self.settings["java_path"])
        self.memory.setValue(max(2, min(32, self.settings["memory_mb"] // 512)))
        self.loader.setCurrentIndex(max(0, self.loader.findData(self.settings["loader"])))
        self.snapshots.setChecked(self.settings["snapshots"])
        self.update_memory()

    def update_memory(self):
        self.memory_label.setText(f"Memory: {self.memory.value() * 512} MB")

    def collect(self):
        return {
            "username": self.username.text(),
            "memory_mb": self.memory.value() * 512,
            "java_path": self.java.text(),
            "version": self.version.currentData() or "",
            "loader": self.loader.currentData(),
            "snapshots": self.snapshots.isChecked(),
        }

    def save(self, *_):
        self.settings = self.collect()
        self.api.save_settings(self.settings)

    def snapshots_toggled(self):
        self.save()
        self.load_versions()

    def load_versions(self):
        wanted = self.version.currentData() or self.settings["version"]
        snapshots = self.snapshots.isChecked()
        self.bridge.run(lambda: self.api.get_versions(snapshots), lambda entries, error: self.fill_versions(entries, error, wanted))

    def fill_versions(self, entries, error, wanted):
        self.version.blockSignals(True)
        self.version.clear()
        if error or not entries:
            self.version.addItem("Failed to load versions" if error else "No versions available", "")
        else:
            for entry in entries:
                self.version.addItem(entry["id"] + ("  (installed)" if entry["installed"] else ""), entry["id"])
            self.version.setCurrentIndex(max(0, self.version.findData(wanted)))
        self.version.blockSignals(False)

    def play(self):
        self.save()
        s = self.settings
        self.api.play(s["version"], s["loader"], s["username"], s["memory_mb"], s["java_path"])

    def target(self):
        return self.version.currentData() or "", self.loader.currentData()

    def switch_page(self, index):
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.refresh_mods()
        if index == 2:
            self.load_installed()

    def refresh_mods(self):
        version, loader = self.target()
        usable = loader != "vanilla" and bool(version)
        self.mods_body.setVisible(usable)
        self.mods_target.setText(
            f"Minecraft {version} / {loader.title()}" if usable
            else "Select the Fabric or Quilt loader on the Play tab to manage mods."
        )
        if not usable:
            return
        self.installed.clear()
        entries = self.api.list_mods(version, loader)
        if not entries:
            self.placeholder(self.installed, "No mods installed")
        for mod in entries:
            handler = lambda checked=False, pid=mod["id"]: self.remove_mod(pid)
            self.add_row(self.installed, mod["title"], "Dependency" if mod["dependency"] else "", "", "Remove", handler)

    def remove_mod(self, project_id):
        self.api.remove_mod(project_id, *self.target())
        self.refresh_mods()

    def search(self):
        version, loader = self.target()
        query = self.query.text()
        self.results.clear()
        self.bridge.run(lambda: self.api.search_mods(query, version, loader), self.show_results)

    def show_results(self, hits, error):
        self.results.clear()
        if error or not hits:
            self.placeholder(self.results, "Search failed" if error else "Nothing found")
            return
        for hit in hits:
            handler = lambda checked=False, pid=hit["id"]: self.api.install_mod(pid, *self.target())
            self.add_row(self.results, f"{hit['title']}  by {hit['author']}", hit["description"], self.format_count(hit["downloads"]), "Install", handler)

    @staticmethod
    def format_count(value):
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        return f"{round(value / 1000)}K" if value >= 1000 else str(value)

    @staticmethod
    def placeholder(rows, text):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        rows.addItem(item)

    def add_row(self, rows, title, subtitle, count, button_text, handler):
        item = QListWidgetItem(rows)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 8, 6)
        text = QVBoxLayout()
        text.setSpacing(1)
        for content, name in ((title, "name"), (subtitle, "muted")):
            if content:
                line = label(content, name)
                line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                text.addWidget(line)
        layout.addLayout(text, 1)
        if count:
            layout.addWidget(label(count, "muted"))
        button = action(QPushButton(button_text))
        button.setEnabled(not self.busy)
        button.clicked.connect(handler)
        layout.addWidget(button)
        item.setSizeHint(row.sizeHint())
        rows.setItemWidget(item, row)

    def tick(self):
        state = self.api.get_state()
        self.busy = state["busy"]
        determinate = state["total"] > 0 and state["stage"].startswith("Downloading")
        percent = min(100, round(state["done"] * 100 / state["total"])) if determinate else 0
        if determinate or state["playing"] or not self.busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if state["playing"] and not determinate else percent)
        else:
            self.progress.setRange(0, 0)
        text = state["error"] or ("Ready" if state["stage"] == "Idle" else state["stage"])
        if determinate:
            text += f" - {state['done'] / 1048576:.1f} / {state['total'] / 1048576:.1f} MB ({percent}%)"
        self.status.setText(text)
        self.status.setStyleSheet("color: #ee7b88" if state["error"] else "")
        for button in self.findChildren(QPushButton):
            if button.property("act"):
                button.setEnabled(not self.busy)
        self.play_button.setText("Running" if state["playing"] else "Working" if self.busy else "Play")
        if self.was_busy and not self.busy:
            self.refresh_mods()
            if self.reload_after:
                self.reload_after = False
                self.load_versions()
                self.load_installed()
        self.was_busy = self.busy

    def load_installed(self):
        self.installed_versions.clear()
        for version_id in self.api.installed_versions():
            self.installed_versions.addItem(version_id)

    def delete_version(self):
        version_id = self.installed_versions.currentText()
        if not version_id:
            return
        question = (f"Delete {version_id} completely? This removes its files, loader profiles, mods and modded worlds. "
                    "Shared vanilla worlds are kept.")
        if QMessageBox.question(self, "Delete version", question) == QMessageBox.StandardButton.Yes:
            self.reload_after = True
            self.api.delete_version(version_id)

    def closeEvent(self, event):
        self.save()
        super().closeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1a1b1f"))
        painter.translate(self.width() - MOON_SIZE + 60, self.height() - MOON_SIZE - 80)
        painter.scale(MOON_SIZE / 300, MOON_SIZE / 300)
        painter.setPen(Qt.PenStyle.NoPen)
        surface = QRadialGradient(114, 111, 200)
        for position, color in ((0, "#b0a79a"), (0.55, "#82796e"), (1, "#47423b")):
            surface.setColorAt(position, QColor(color))
        painter.setBrush(QBrush(surface))
        painter.drawPath(MOON_PATH)
        for x, y, radius_x, radius_y, angle in MOON_CRATERS:
            crater = QRadialGradient(0, 0, 1, -0.28, -0.28)
            crater.setColorAt(0, QColor(38, 34, 30, 140))
            crater.setColorAt(0.75, QColor(56, 51, 46, 71))
            crater.setColorAt(1, QColor(224, 215, 202, 51))
            painter.save()
            painter.translate(x, y)
            painter.rotate(angle)
            painter.scale(radius_x, radius_y)
            painter.setBrush(QBrush(crater))
            painter.drawEllipse(QPointF(0, 0), 1, 1)
            painter.restore()
        shade = QLinearGradient(30, 42, 276, 272)
        shade.setColorAt(0.4, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor(0, 0, 0, 158))
        painter.setBrush(QBrush(shade))
        painter.drawPath(MOON_PATH)


def run(api):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window(api)
    window.show()
    sys.exit(app.exec())
