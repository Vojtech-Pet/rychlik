#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QMenu, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from downloader import (
    Download, DownloadWorker, PremiumContentError, filename_from_url, list_video_formats,
    load_items, reload_download_modules, save_items,
)


STATE_FILE = Path.home() / ".local/share/rychlik/downloads.json"
SETTINGS_FILE = Path.home() / ".config/rychlik/settings.json"
LOG_FILE = Path.home() / ".local/share/rychlik/app.log"

ICONS_SVG_DIR = Path(__file__).with_name("icons") / "svg"

TEXT_MUTED = "#9AA4B2"
TEXT_ON_DARK = "#F3F4F6"
BLUE_ACCENT = "#3B82F6"
GREEN_SUCCESS = "#4ADE80"
RED_DANGER = "#F87171"

_svg_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def svg_icon(name: str, color: str = TEXT_MUTED, size: int = 18) -> QIcon:
    """Render one of the currentColor line icons from icons/svg at a fixed color.
    Rendered straight from SVG bytes (no temp file), so this sidesteps the
    non-ASCII install path issue that forces PNG icons through a disk cache."""
    key = (name, color, size)
    cached = _svg_icon_cache.get(key)
    if cached is not None:
        return cached
    svg_path = ICONS_SVG_DIR / f"{name}.svg"
    try:
        markup = svg_path.read_text(encoding="utf-8").replace("currentColor", color)
        renderer = QSvgRenderer(markup.encode("utf-8"))
        pixmap = QPixmap(QSize(size, size))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon = QIcon(pixmap)
    except OSError:
        icon = QIcon()
    _svg_icon_cache[key] = icon
    return icon


def log_event(message: str, level: str = "INFO"):
    """Log event to file with timestamp"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {level}: {message}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass  # Fail silently to not crash the app


def settings_row_label(text: str, icon_name: str | None = None) -> QWidget:
    wrap = QWidget()
    row = QHBoxLayout(wrap); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
    if icon_name:
        icon_label = QLabel(); icon_label.setObjectName("settingsRowIcon")
        icon_label.setPixmap(svg_icon(icon_name, "#9FAABC", 18).pixmap(18, 18))
        row.addWidget(icon_label)
    row.addWidget(QLabel(text))
    return wrap


def human_size(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def normalize_url(value: str) -> str:
    """Trim accidental duplicate URLs created by pasting the same link twice."""
    value = value.strip()
    second_http = value.find("http://", 8)
    second_https = value.find("https://", 8)
    positions = [position for position in (second_http, second_https) if position >= 0]
    return value[:min(positions)] if positions else value


def category_for_name(name: str, media: bool = False) -> str:
    if media: return "Videá"
    suffix = Path(name.lower().split("?", 1)[0]).suffix
    if suffix in {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"}: return "Videá"
    if suffix in {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac"}: return "Hudba"
    if suffix in {".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}: return "Dokumenty"
    if suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}: return "Archívy"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}: return "Obrázky"
    if suffix in {".iso", ".img", ".exe", ".msi", ".deb", ".rpm", ".appimage"}: return "Programy"
    return "Ostatné"


def themed_button(icon: str, text: str, tooltip: str, color: str = "#E5E7EB") -> QPushButton:
    button = QPushButton(svg_icon(icon, color), text)
    button.setToolTip(tooltip)
    button.setProperty("kind", "icon")
    button.setMinimumHeight(40)
    return button


class Signals(QObject):
    updated = Signal(object)
    browser_download = Signal(str, bool, str, str, str)
    ytdlp_update_done = Signal(bool, str)
    ytdlp_check_done = Signal(str)


class DownloadCard(QFrame):
    def __init__(self, item: Download, window: "MainWindow"):
        super().__init__()
        self.item = item
        self.window = window
        self.setObjectName("downloadCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.file_icon = QLabel()
        self.file_icon.setObjectName("fileTypeIcon")
        self.name = QLabel()
        self.name.setObjectName("downloadName")
        self.name.setMinimumWidth(120)
        self.category = QLabel()
        self.category.setObjectName("categoryBadge")
        self.completed_pill = QLabel("✓ Dokončené")
        self.completed_pill.setObjectName("completedPill")
        self.status = QLabel()
        self.status.setObjectName("downloadStatus")
        self.status.setMinimumWidth(90)
        self.status.setMaximumWidth(220)
        top.addWidget(self.file_icon); top.addWidget(self.name, 1)
        top.addWidget(self.category); top.addWidget(self.completed_pill); top.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        bottom = QHBoxLayout()
        self.details = QLabel()
        self.details.setObjectName("downloadDetails")
        self.details.setMinimumWidth(120)
        bottom.addWidget(self.details)
        folder = themed_button("folder", "Priečinok", "Otvoriť priečinok")
        self.pause_button = themed_button("paused", "Pauza", "Pozastaviť alebo pokračovať")
        cancel = themed_button("stop", "Stop", "Zastaviť sťahovanie")
        remove = themed_button("trash", "Vymazať", "Odstrániť zo zoznamu", color=RED_DANGER)
        remove.setProperty("kind", "danger")
        folder.clicked.connect(lambda: window.open_folder(item))
        self.pause_button.clicked.connect(self.pause_resume)
        cancel.clicked.connect(lambda: window.cancel(item))
        remove.clicked.connect(lambda: window.remove_item(item))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for button in (folder, self.pause_button, cancel, remove):
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            actions.addWidget(button)
        bottom.addStretch(1)
        bottom.addLayout(actions)
        layout.addLayout(top); layout.addWidget(self.progress); layout.addLayout(bottom)
        self.refresh()

    def pause_resume(self):
        if self.item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video"):
            self.window.pause(self.item)
        else:
            self.window.resume(self.item)

    def refresh(self):
        item = self.item
        self.name.setText(item.filename)
        self.name.setToolTip(item.filename)
        category = item.category or category_for_name(item.filename, item.media)
        self.category.setText(category)
        is_audio = category == "Hudba"
        self.file_icon.setPixmap(svg_icon("audio-file" if is_audio else "video-file",
                                          "#A78BFA" if is_audio else "#5D89BE", 22).pixmap(22, 22))
        is_done = item.status == "Dokončené"
        self.completed_pill.setVisible(is_done)
        self.status.setVisible(not is_done)
        self.status.setText(item.status if not item.error else f"{item.status}: {item.error}")
        self.status.setToolTip(self.status.text())
        percent = round(item.downloaded * 100 / item.total) if item.total else 0
        self.progress.setRange(0, 100 if item.total else 0)
        self.progress.setValue(percent)
        total = human_size(item.total) if item.total else "neznáma veľkosť"
        speed = f"  •  {human_size(item.speed)}/s" if item.speed else ""
        self.details.setText(f"{human_size(item.downloaded)} / {total}{speed}")
        active = item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video")
        self.pause_button.setIcon(svg_icon("paused" if active else "play", "#E5E7EB"))
        self.pause_button.setText("Pauza" if active else "Pokračovať")
        self.pause_button.setEnabled(item.status not in ("Dokončené", "Zrušené"))


class AddDialog(QDialog):
    def __init__(self, parent, default_folder: str, custom_categories: list[str], category_paths: dict[str, str],
                 initial_url: str = "", initial_category: str = "Automaticky", media: bool = False,
                 browser: str = "", referrer: str = ""):
        super().__init__(parent)
        self.default_folder = default_folder
        self.category_paths = category_paths
        self.media = media
        self.browser = browser
        self.referrer = referrer
        self.setWindowTitle("Nové sťahovanie")
        self.setObjectName("newDownloadDialog")
        self.setWindowFlags(Qt.Window | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        heading = QLabel("Nové sťahovanie")
        heading.setObjectName("dialogTitle")
        subtitle = QLabel("Pridaj odkaz a vyber, kam sa má súbor uložiť.")
        subtitle.setObjectName("dialogSubtitle")
        layout.addWidget(heading); layout.addWidget(subtitle)

        form_card = QFrame(); form_card.setObjectName("dialogCard")
        form = QVBoxLayout(form_card); form.setContentsMargins(18, 16, 18, 18); form.setSpacing(9)
        form.addWidget(QLabel("URL adresa"))
        self.url = QLineEdit(); self.url.setPlaceholderText("https://example.com/subor.zip")
        form.addWidget(self.url)
        form.addSpacing(4); form.addWidget(QLabel("Cieľový priečinok"))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(default_folder)
        browse = QPushButton(svg_icon("folder", "#E5E7EB"), "Vybrať…")
        browse.setObjectName("dialogSecondary")
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.folder, 1); folder_row.addWidget(browse)
        form.addLayout(folder_row)
        form.addSpacing(4); form.addWidget(QLabel("Kategória"))
        category_row = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(["Automaticky", "Videá", "Hudba", "Dokumenty", "Archívy", "Obrázky", "Programy", "Ostatné"])
        for name in custom_categories:
            if self.category.findText(name) < 0: self.category.addItem(name)
        self.category.currentTextChanged.connect(self.category_changed)
        remove_category = QPushButton("−")
        remove_category.setObjectName("categoryRemoveButton")
        remove_category.setFixedWidth(44)
        remove_category.setToolTip("Odstrániť vlastnú kategóriu")
        remove_category.clicked.connect(self.remove_category)
        add_category = QPushButton(svg_icon("add", BLUE_ACCENT), "+")
        add_category.setObjectName("categoryAddButton")
        add_category.setFixedWidth(44)
        add_category.setToolTip("Vytvoriť vlastnú kategóriu")
        add_category.clicked.connect(self.create_category)
        category_row.addWidget(self.category, 1)
        category_row.addWidget(remove_category); category_row.addWidget(add_category)
        form.addLayout(category_row)
        self.quality = QComboBox()
        self.quality.addItem("Najlepšia kvalita", "bestvideo+bestaudio/best")
        if False and media:
            form.addSpacing(4); form.addWidget(QLabel("Kvalita videa"))
            quality_row = QHBoxLayout()
            reload_quality = QPushButton(QIcon.fromTheme("view-refresh"), "Načítať")
            reload_quality.setObjectName("dialogSecondary")
            reload_quality.setToolTip("Načítať dostupné kvality videa")
            reload_quality.clicked.connect(self.load_qualities)
            quality_row.addWidget(self.quality, 1); quality_row.addWidget(reload_quality)
            form.addLayout(quality_row)
        layout.addWidget(form_card)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Stiahnuť")
        buttons.button(QDialogButtonBox.Ok).setIcon(svg_icon("all-downloads", "#FFFFFF"))
        buttons.button(QDialogButtonBox.Ok).setObjectName("dialogPrimary")
        buttons.button(QDialogButtonBox.Cancel).setObjectName("dialogSecondary")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(820, 460)
        self.url.setText(initial_url)
        if self.category.findText(initial_category) >= 0:
            self.category.setCurrentText(initial_category)
            self.category_changed(initial_category)
        if False and media and initial_url:
            QTimer.singleShot(250, self.load_qualities)
        self.url.setFocus()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Cieľový priečinok", self.folder.text())
        if folder: self.folder.setText(folder)

    def create_category(self):
        name, accepted = QInputDialog.getText(self, "Nová kategória", "Názov kategórie:")
        name = name.strip().replace("/", "_").replace("\\", "_")
        if accepted and name:
            if self.category.findText(name) < 0: self.category.addItem(name)
            self.category.setCurrentText(name)

    def remove_category(self):
        built_in = {"Automaticky", "Videá", "Hudba", "Dokumenty", "Archívy", "Obrázky", "Programy", "Ostatné"}
        name = self.category.currentText()
        if name in built_in:
            QMessageBox.information(self, "Vstavaná kategória", "Vstavané kategórie sa nedajú odstrániť.")
            return
        self.category.removeItem(self.category.currentIndex())
        self.category.setCurrentIndex(0)

    def category_changed(self, name: str):
        if name == "Automaticky":
            self.folder.setText(self.default_folder)
            return
        remembered = self.category_paths.get(name)
        self.folder.setText(remembered or str(Path(self.default_folder) / name))

    def load_qualities(self):
        if not self.media:
            return
        url = normalize_url(self.url.text())
        if not url:
            return
        current = self.quality.currentData() or "bestvideo+bestaudio/best"
        self.quality.clear()
        self.quality.addItem("Načítavam kvality…", current)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            formats = list_video_formats(url, self.browser, self.referrer)
            self.quality.clear()
            for item in formats:
                self.quality.addItem(item["label"], item["format"])
        except Exception as exc:
            self.quality.clear()
            self.quality.addItem("Najlepšia kvalita", "bestvideo+bestaudio/best")
            QMessageBox.warning(self, "Kvality sa nepodarilo načítať",
                                f"Rýchlik nenašiel zoznam kvalít.\nPoužije sa najlepšia dostupná kvalita.\n\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rýchlik")
        self.setWindowIcon(svg_icon("lightning", BLUE_ACCENT, 32))
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.resize(1080, 680)
        self.items = load_items(STATE_FILE)
        self.settings = self.load_settings()
        self.workers: dict[int, DownloadWorker] = {}
        self.cards: dict[int, DownloadCard] = {}
        self.filter_buttons: dict[str, QPushButton] = {}
        self.filter_mode = "all"
        self.allow_quit = False
        self.notified: set[int] = set()
        self.signals = Signals()
        self.signals.updated.connect(self.refresh_item)
        self.signals.browser_download.connect(self.add_from_browser)
        self.signals.ytdlp_update_done.connect(self.finish_ytdlp_update)
        self.signals.ytdlp_check_done.connect(self.finish_ytdlp_check)
        self.ytdlp_update_button = None
        self.ytdlp_status_label = None
        self.ytdlp_latest_version = ""
        self.ytdlp_update_silent = False
        self.build_ui()
        self.build_tray()
        for item in self.items: self.add_card(item)
        self.update_summary()
        self.start_browser_bridge()
        QTimer.singleShot(1500, self.check_ytdlp_update)

        # Kontrola či sú všetky downloads dokončené a notifikácia
        completed_count = sum(1 for item in self.items if item.status == "Dokončené")
        if self.items and completed_count == len(self.items):
            log_event(f"Všetky {len(self.items)} downloads sú dokončené")
            QTimer.singleShot(2000, self.notify_all_downloads_completed)

    def build_tray(self):
        icon = svg_icon("lightning", BLUE_ACCENT, 32)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Rýchlik – správca sťahovania")
        menu = QMenu()
        show_action = QAction(svg_icon("all-downloads", "#E5E7EB"), "Otvoriť Rýchlik", self)
        quit_action = QAction(svg_icon("stop", RED_DANGER), "Ukončiť", self)
        show_action.triggered.connect(self.show_from_tray)
        quit_action.triggered.connect(self.quit_from_tray)
        menu.addAction(show_action); menu.addSeparator(); menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_from_tray() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_from_tray(self):
        self.show(); self.showNormal(); self.raise_(); self.activateWindow()

    def notify_all_downloads_completed(self):
        """Notify user that all downloads are completed"""
        try:
            if self.tray and hasattr(self.tray, 'isVisible'):
                self.tray.showMessage(
                    "Rýchlik",
                    f"✓ Všetkých {len(self.items)} súborov je stiahnutých!",
                    QSystemTrayIcon.Information,
                    5000
                )
                log_event("Notifikácia poslana: Všetky downloads sú dokončené")
        except Exception as e:
            log_event(f"Chyba pri notifikácii: {str(e)}", level="WARNING")

    def quit_from_tray(self):
        self.allow_quit = True
        log_event("Aplikácia sa zatvára cez quit_from_tray()")
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent):
        if self.allow_quit:
            log_event("Aplikácia sa zatvára - quit_from_tray()")
            event.accept()
        else:
            event.ignore(); self.hide()
            log_event("Aplikácia minimalizovaná do systémovej lišty")
            self.tray.showMessage("Rýchlik", "Aplikácia pokračuje v systémovej lište.",
                                  QSystemTrayIcon.Information, 2500)

    def build_ui(self):
        root = QWidget(); shell = QHBoxLayout(root); shell.setContentsMargins(0, 0, 0, 0); shell.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(230)
        side = QVBoxLayout(sidebar); side.setContentsMargins(16, 20, 16, 18); side.setSpacing(7)
        brand_row = QHBoxLayout(); brand_row.setSpacing(9)
        brand_icon = QLabel(); brand_icon.setPixmap(svg_icon("lightning", BLUE_ACCENT, 24).pixmap(24, 24))
        brand = QLabel("RÝCHLIK"); brand.setObjectName("brand")
        brand_row.addWidget(brand_icon); brand_row.addWidget(brand); brand_row.addStretch()
        side.addLayout(brand_row)
        filters = (("Všetky sťahovania", "all", "all-downloads"), ("Aktívne", "active", "active"),
                   ("Dokončené", "done", "completed"), ("Pozastavené", "paused", "paused"),
                   ("Chyby", "error", "errors"))
        for label, mode, icon in filters:
            button = QPushButton(svg_icon(icon, "#D1D5DB"), label); button.setProperty("kind", "nav")
            button.setProperty("mode", mode)
            button.setProperty("selected", mode == self.filter_mode)
            button.clicked.connect(lambda _checked=False, value=mode: self.set_filter(value))
            self.filter_buttons[mode] = button
            side.addWidget(button)
        side.addStretch()
        quick_actions = QLabel("RÝCHLE AKCIE"); quick_actions.setObjectName("sidebarSectionLabel")
        side.addWidget(quick_actions)
        pause_all = QPushButton(svg_icon("paused", "#D1D5DB"), "Pozastaviť všetko"); pause_all.setProperty("kind", "nav")
        resume_all = QPushButton(svg_icon("play", "#D1D5DB"), "Pokračovať vo všetkom"); resume_all.setProperty("kind", "nav")
        pause_all.clicked.connect(self.pause_all); resume_all.clicked.connect(self.resume_all)
        side.addWidget(pause_all); side.addWidget(resume_all)

        content = QWidget(); content.setObjectName("content"); main = QVBoxLayout(content)
        main.setContentsMargins(24, 18, 24, 14); main.setSpacing(14)
        toolbar = QHBoxLayout()
        heading = QLabel("Sťahovania"); heading.setObjectName("pageTitle")
        self.search = QLineEdit(); self.search.setPlaceholderText("Hľadať sťahovanie…")
        self.search.setClearButtonEnabled(True); self.search.setFixedWidth(290)
        self.search.addAction(svg_icon("search", "#6B7280"), QLineEdit.TrailingPosition)
        self.search.textChanged.connect(self.apply_filter)
        settings = QPushButton(svg_icon("settings", "#E5E7EB"), "Nastavenia")
        settings.setProperty("kind", "secondary")
        settings.clicked.connect(self.show_settings)
        clear = QPushButton(svg_icon("trash", "#E5E7EB"), "Vymazať históriu")
        clear.setProperty("kind", "secondary")
        clear.clicked.connect(self.clear_history)
        add = QPushButton(svg_icon("add", "#FFFFFF"), "Pridať sťahovanie")
        add.setObjectName("addButton"); add.clicked.connect(self.show_add)
        toolbar.addWidget(heading); toolbar.addStretch(); toolbar.addWidget(self.search); toolbar.addWidget(clear); toolbar.addWidget(settings); toolbar.addWidget(add)
        main.addLayout(toolbar)
        scroll = QScrollArea(); scroll.setObjectName("downloadsScroll"); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget(); holder.setObjectName("downloadsHolder"); self.cards_layout = QVBoxLayout(holder)
        self.cards_layout.setContentsMargins(2, 2, 8, 2); self.cards_layout.setSpacing(10); self.cards_layout.addStretch()
        scroll.setWidget(holder); main.addWidget(scroll, 1)
        self.empty = QLabel("Zatiaľ tu nič nie je. Pridaj URL adresu súboru.")
        self.empty.setAlignment(Qt.AlignCenter); self.empty.setObjectName("empty")
        main.addWidget(self.empty)
        status_bar = QFrame(); status_bar.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_bar); status_layout.setContentsMargins(14, 10, 14, 10)
        self.summary = QLabel(); self.summary.setObjectName("summary")
        status_layout.addWidget(self.summary); status_layout.addStretch()
        main.addWidget(status_bar)
        shell.addWidget(sidebar); shell.addWidget(content, 1)
        self.setCentralWidget(root)

    def show_add(self):
        dialog = AddDialog(self, self.settings["download_directory"], self.settings["custom_categories"],
                           self.settings["category_paths"])
        if dialog.exec() != QDialog.Accepted: return
        current_categories = [dialog.category.itemText(i) for i in range(dialog.category.count())]
        built_in = {"Automaticky", "Videá", "Hudba", "Dokumenty", "Archívy", "Obrázky", "Programy", "Ostatné"}
        self.settings["custom_categories"] = sorted(set(current_categories) - built_in)
        url = normalize_url(dialog.url.text()); folder = Path(dialog.folder.text()).expanduser()
        if not url.startswith(("http://", "https://", "data:")):
            QMessageBox.warning(self, "Neplatná adresa", "Zadaj HTTP, HTTPS alebo dátovú adresu."); return
        duplicate = self.find_duplicate(url)
        if duplicate:
            QMessageBox.information(self, "Sťahovanie už existuje", f"Táto adresa už je v zozname:\n{duplicate.filename}")
            return
        filename = filename_from_url(url)
        selected_category = dialog.category.currentText()
        category = category_for_name(filename) if selected_category == "Automaticky" else selected_category
        if selected_category != "Automaticky":
            self.settings["category_paths"][category] = str(folder)
        elif self.settings["auto_categories"]:
            folder = Path(self.settings["category_paths"].get(category, str(folder / category)))
        self.save_settings()
        item = Download(url=url, source_url=url, destination=str(folder / filename), category=category)
        self.apply_download_settings(item)
        self.items.append(item); self.add_card(item); self.save(); self.resume(item)

    def show_settings(self):
        dialog = QDialog(self); dialog.setWindowTitle("Nastavenia sťahovania")
        dialog.setWindowFlags(Qt.Window | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        dialog.setSizeGripEnabled(True)
        dialog.resize(880, 640)
        outer = QVBoxLayout(dialog); outer.setContentsMargins(0, 22, 0, 18); outer.setSpacing(18)
        heading = QLabel("Nastavenia sťahovania"); heading.setObjectName("dialogTitle")
        heading.setAlignment(Qt.AlignCenter)
        outer.addWidget(heading)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        nav = QFrame(); nav.setObjectName("dialogNav"); nav.setFixedWidth(170)
        nav_layout = QVBoxLayout(nav); nav_layout.setContentsMargins(16, 26, 16, 18); nav_layout.setSpacing(10)
        gear = QLabel(); gear.setPixmap(svg_icon("settings", "#22C55E", 40).pixmap(40, 40)); gear.setAlignment(Qt.AlignCenter)
        nav_title = QLabel("Nastavenia"); nav_title.setObjectName("dialogNavTitle"); nav_title.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(gear); nav_layout.addWidget(nav_title); nav_layout.addStretch()
        nav_logo = QLabel(); nav_logo.setPixmap(svg_icon("lightning", BLUE_ACCENT, 20).pixmap(20, 20))
        nav_logo.setObjectName("dialogNavLogo"); nav_logo.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(nav_logo)
        body.addWidget(nav)

        content_card = QFrame(); content_card.setObjectName("dialogCard")
        content_layout = QVBoxLayout(content_card); content_layout.setContentsMargins(24, 24, 24, 24)
        form = QFormLayout(); form.setSpacing(14); form.setLabelAlignment(Qt.AlignLeft)
        content_layout.addLayout(form)
        body.addWidget(content_card, 1)
        outer.addLayout(body, 1)

        folder_row = QHBoxLayout(); folder_edit = QLineEdit(self.settings["download_directory"])
        browse = QPushButton(svg_icon("folder", "#E5E7EB"), "Vybrať…")
        browse.clicked.connect(lambda: self.choose_settings_folder(folder_edit))
        folder_row.addWidget(folder_edit, 1); folder_row.addWidget(browse)
        form.addRow(settings_row_label("Predvolený priečinok:", "folder"), folder_row)
        segments = QSpinBox(); segments.setRange(1, 16); segments.setValue(self.settings["max_segments"])
        form.addRow(settings_row_label("HTTP segmenty:", "all-downloads"), segments)
        concurrent = QSpinBox(); concurrent.setRange(1, 10); concurrent.setValue(self.settings["max_concurrent"])
        form.addRow(settings_row_label("Súčasné sťahovania:", "active"), concurrent)
        speed = QDoubleSpinBox(); speed.setRange(0, 10000); speed.setDecimals(1); speed.setSuffix(" MB/s")
        speed.setSpecialValueText("Bez limitu"); speed.setValue(self.settings["speed_limit_mbps"])
        form.addRow(settings_row_label("Celkový limit rýchlosti:"), speed)
        categories = QCheckBox("Triediť do priečinkov Videá, Hudba, Dokumenty…")
        categories.setChecked(self.settings["auto_categories"])
        form.addRow(settings_row_label("Automatické kategórie:"), categories)
        ytdlp_row = QHBoxLayout()
        ytdlp_status = QLabel(self.ytdlp_status_text())
        ytdlp_update = QPushButton(svg_icon("active", "#E5E7EB"), "Aktualizovať yt-dlp")
        ytdlp_update.setProperty("kind", "secondary")
        ytdlp_update.clicked.connect(self.update_ytdlp)
        self.ytdlp_update_button = ytdlp_update
        self.ytdlp_status_label = ytdlp_status
        dialog.finished.connect(lambda *_: setattr(self, "ytdlp_status_label", None))
        ytdlp_row.addWidget(ytdlp_status, 1)
        ytdlp_row.addWidget(ytdlp_update)
        form.addRow(settings_row_label("Video modul:", "play"), ytdlp_row)
        ytdlp_auto = QCheckBox("Automaticky sťahovať a inštalovať nové verzie yt-dlp")
        ytdlp_auto.setChecked(self.settings.get("ytdlp_auto_update", False))
        form.addRow(settings_row_label("Automatická aktualizácia:"), ytdlp_auto)
        modules_row = QHBoxLayout()
        modules_path = QLabel("Rozšírenia pre podporu webov a špecifických downloaderov")
        modules_path.setObjectName("settingsHint")
        add_module = QPushButton(svg_icon("settings", "#E5E7EB"), "Spravovať moduly")
        add_module.setProperty("kind", "secondary")
        add_module.clicked.connect(lambda: self.show_modules_dialog(dialog))
        modules_row.addWidget(modules_path, 1)
        modules_row.addWidget(add_module)
        form.addRow(settings_row_label("Moduly:"), modules_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Uložiť")
        buttons.button(QDialogButtonBox.Save).setObjectName("dialogPrimary")
        buttons.button(QDialogButtonBox.Cancel).setObjectName("dialogSecondary")
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        self.check_ytdlp_update()
        if dialog.exec() == QDialog.Accepted:
            folder = Path(folder_edit.text()).expanduser(); folder.mkdir(parents=True, exist_ok=True)
            self.settings.update({"download_directory": str(folder), "max_segments": segments.value(),
                "max_concurrent": concurrent.value(), "speed_limit_mbps": speed.value(),
                "auto_categories": categories.isChecked(), "ytdlp_auto_update": ytdlp_auto.isChecked()})
            self.save_settings(); self.start_queued()

    def installed_ytdlp_version(self):
        try:
            import yt_dlp
            return yt_dlp.version.__version__
        except Exception:
            return ""

    def ytdlp_status_text(self):
        current = self.installed_ytdlp_version()
        if not current:
            return "yt-dlp nie je dostupný"
        if self.ytdlp_latest_version and self.ytdlp_latest_version != current:
            return f"yt-dlp {current} (dostupná verzia {self.ytdlp_latest_version})"
        return f"yt-dlp {current} (aktuálny)" if self.ytdlp_latest_version else f"yt-dlp {current}"

    def check_ytdlp_update(self):
        def run_check():
            latest = ""
            try:
                with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=8) as response:
                    data = json.loads(response.read().decode())
                latest = str(data.get("info", {}).get("version", ""))
            except Exception:
                latest = ""
            self.signals.ytdlp_check_done.emit(latest)

        threading.Thread(target=run_check, daemon=True).start()

    def finish_ytdlp_check(self, latest: str):
        if latest:
            self.ytdlp_latest_version = latest
        if self.ytdlp_status_label is not None:
            try:
                self.ytdlp_status_label.setText(self.ytdlp_status_text())
            except RuntimeError:
                self.ytdlp_status_label = None
        current = self.installed_ytdlp_version()
        update_available = bool(latest) and bool(current) and latest != current
        if update_available and self.settings.get("ytdlp_auto_update"):
            self.update_ytdlp(silent=True)

    def update_ytdlp(self, silent: bool = False):
        self.ytdlp_update_silent = silent
        if self.ytdlp_update_button:
            self.ytdlp_update_button.setEnabled(False)
            self.ytdlp_update_button.setText("Aktualizujem…")

        def run_update():
            command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            try:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=180)
                output = (completed.stdout + "\n" + completed.stderr).strip()
                if completed.returncode == 0:
                    self.signals.ytdlp_update_done.emit(True, output or "yt-dlp bol aktualizovaný.")
                else:
                    self.signals.ytdlp_update_done.emit(False, output or "Aktualizácia zlyhala.")
            except Exception as exc:
                self.signals.ytdlp_update_done.emit(False, str(exc))

        threading.Thread(target=run_update, daemon=True).start()

    def finish_ytdlp_update(self, ok: bool, message: str):
        if self.ytdlp_update_button:
            self.ytdlp_update_button.setEnabled(True)
            self.ytdlp_update_button.setText("Aktualizovať yt-dlp")
        if ok:
            self.ytdlp_latest_version = ""
        title = "yt-dlp aktualizovaný" if ok else "Aktualizácia zlyhala"
        text = "Rýchlik aktualizoval yt-dlp. Reštartuj Rýchlik, aby sa načítala nová verzia." if ok else message
        if getattr(self, "ytdlp_update_silent", False):
            self.tray.showMessage(title, text if ok else message,
                                  QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning, 6000)
        else:
            QMessageBox.information(self, title, text) if ok else QMessageBox.warning(self, title, text)

    def choose_settings_folder(self, target: QLineEdit):
        folder = QFileDialog.getExistingDirectory(self, "Predvolený priečinok", target.text())
        if folder: target.setText(folder)

    def show_modules_dialog(self, parent):
        dialog = QDialog(parent)
        dialog.setWindowTitle("Moduly sťahovania")
        dialog.resize(1100, 720)
        layout = QVBoxLayout(dialog)
        title = QLabel("Načítané moduly")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Moduly rozširujú Rýchlik o podporu špecifických webov alebo spôsobov sťahovania.")
        subtitle.setObjectName("dialogSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setObjectName("modulesScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder.setObjectName("modulesHolder")
        cards = QVBoxLayout(holder)
        cards.setContentsMargins(2, 2, 8, 2)
        cards.setSpacing(10)
        self.populate_module_cards(cards)
        cards.addStretch()
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        add = QPushButton(svg_icon("add", "#E5E7EB"), "Pridať modul")
        add.setObjectName("dialogSecondary")
        reload_button = QPushButton("Načítať znova")
        reload_button.setObjectName("dialogSecondary")
        close = QPushButton("Zavrieť")
        close.setObjectName("dialogPrimary")
        add.clicked.connect(lambda: self.add_download_module(dialog, cards))
        reload_button.clicked.connect(lambda: (reload_download_modules(), self.populate_module_cards(cards), cards.addStretch()))
        close.clicked.connect(dialog.accept)
        buttons.addWidget(add)
        buttons.addStretch()
        buttons.addWidget(reload_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def populate_module_cards(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        modules_dir = Path(__file__).with_name("download_modules")
        modules_dir.mkdir(parents=True, exist_ok=True)
        python_modules = sorted(path for path in modules_dir.glob("*.py") if not path.name.startswith("_"))
        shell_helpers = sorted(modules_dir.glob("*.sh"))
        if not python_modules and not shell_helpers:
            empty = QLabel("Zatiaľ nie sú pridané žiadne moduly.")
            empty.setObjectName("empty")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty)
            return

        used_helpers: set[Path] = set()
        for path in python_modules:
            helper = self.match_module_helper(path, shell_helpers)
            if helper:
                used_helpers.add(helper)
            layout.addWidget(self.module_card(path, "Python modul", "Aktívny", helper))
        for path in shell_helpers:
            if path not in used_helpers:
                layout.addWidget(self.module_card(path, "Samostatný shell modul", "Dostupný"))

    def match_module_helper(self, module_path: Path, helpers: list[Path]) -> Path | None:
        try:
            text = module_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        for match in re.finditer(r'with_name\(["\']([^"\']+\.sh)["\']\)', text):
            referenced = module_path.with_name(match.group(1))
            if referenced in helpers:
                return referenced

        normalized = module_path.stem.replace("_", "-").lower()
        for helper in helpers:
            helper_name = helper.stem.lower()
            if helper_name == normalized or helper_name.startswith(normalized) or normalized in helper_name:
                return helper
        return None

    def module_card(self, path: Path, kind: str, status: str, helper: Path | None = None) -> QFrame:
        card = QFrame()
        card.setObjectName("moduleCard")
        card.setToolTip(str(path))
        box = QHBoxLayout(card)
        box.setContentsMargins(14, 11, 14, 11)
        active = status == "Aktívny"
        icon = QLabel("✓" if active else "•")
        icon.setObjectName("moduleStatusIcon" if active else "moduleIcon")
        name = QLabel(path.name)
        name.setObjectName("downloadName")
        name.setToolTip(str(path))
        helper_text = f" • Pomocný skript: {helper.name}" if helper else ""
        details_text = f"{kind} • {status}{helper_text} • {path.parent}"
        details = QLabel()
        details.setObjectName("downloadDetails")
        details.setText(details.fontMetrics().elidedText(details_text, Qt.ElideMiddle, 480))
        details.setToolTip(details_text)
        text = QVBoxLayout()
        text.addWidget(name)
        text.addWidget(details)
        box.addWidget(icon)
        box.addLayout(text, 1)
        return card

    def add_download_module(self, parent, cards_layout: QVBoxLayout | None = None):
        picker = QFileDialog(parent, "Pridať modul", str(Path.home()))
        picker.setFileMode(QFileDialog.ExistingFile)
        picker.setNameFilters(["Moduly (*.py *.sh)", "Všetky súbory (*)"])
        picker.setOption(QFileDialog.DontUseNativeDialog, True)
        picker.setLabelText(QFileDialog.Accept, "Pridať")
        picker.setLabelText(QFileDialog.Reject, "Zrušiť")
        if picker.exec() != QDialog.Accepted:
            return
        selected = picker.selectedFiles()
        if not selected:
            return
        source = selected[0]
        source_path = Path(source)
        if source_path.suffix not in {".py", ".sh"}:
            QMessageBox.warning(parent, "Nepodporovaný modul", "Vyber súbor .py alebo .sh.")
            return
        try:
            target = self.install_download_module(source_path)
        except Exception as exc:
            QMessageBox.warning(parent, "Modul sa nepodarilo pridať", str(exc))
            return
        if cards_layout is not None:
            self.populate_module_cards(cards_layout)
            cards_layout.addStretch()
        QMessageBox.information(parent, "Modul pridaný", f"Modul bol pridaný:\n{target}")

    def install_download_module(self, source_path: Path) -> Path:
        modules_dir = Path(__file__).with_name("download_modules")
        modules_dir.mkdir(parents=True, exist_ok=True)
        target = modules_dir / source_path.name
        shutil.copy2(source_path, target)
        if target.suffix == ".sh":
            target.chmod(target.stat().st_mode | 0o111)
        reload_download_modules()
        return target

    def add_card(self, item):
        card = DownloadCard(item, self); self.cards[id(item)] = card
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.apply_filter(); self.update_summary()

    def set_filter(self, mode):
        self.filter_mode = mode
        for value, button in self.filter_buttons.items():
            button.setProperty("selected", value == mode)
            button.style().unpolish(button)
            button.style().polish(button)
        self.apply_filter()

    def apply_filter(self):
        query = self.search.text().strip().casefold()
        for item in self.items:
            status = item.status
            matches_mode = {"all": True, "active": status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video", "Čaká"),
                "done": status == "Dokončené", "paused": status == "Pozastavené",
                "error": status in ("Chyba", "Zrušené")}.get(self.filter_mode, True)
            matches_text = not query or query in item.filename.casefold() or query in item.url.casefold()
            card = self.cards.get(id(item))
            if card:
                card.setVisible(matches_mode and matches_text)
        self.empty.setVisible(not self.items)

    def worker(self, item):
        if id(item) not in self.workers:
            self.workers[id(item)] = DownloadWorker(item, lambda value: self.signals.updated.emit(value))
        return self.workers[id(item)]

    def apply_download_settings(self, item):
        item.max_segments = self.settings["max_segments"]
        total_limit = int(self.settings["speed_limit_mbps"] * 1024 * 1024)
        item.speed_limit = total_limit // self.settings["max_concurrent"] if total_limit else 0

    def active_count(self):
        return sum(item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video") for item in self.items)

    def resume(self, item):
        if item.status not in ("Sťahuje sa", "Sťahuje video", "Analyzuje video") and self.active_count() >= self.settings["max_concurrent"]:
            item.status = "Čaká"; self.refresh_item(item); return
        self.apply_download_settings(item)
        item.status = "Sťahuje sa"
        self.worker(item).start()

    def start_queued(self):
        for item in self.items:
            if self.active_count() >= self.settings["max_concurrent"]: break
            if item.status == "Čaká":
                self.apply_download_settings(item); item.status = "Sťahuje sa"; self.worker(item).start()
    def pause(self, item): self.worker(item).pause(); self.save()
    def cancel(self, item): self.worker(item).cancel()
    def pause_all(self):
        for item in self.items:
            if item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video"): self.pause(item)
    def resume_all(self):
        for item in self.items:
            if item.status in ("Čaká", "Pozastavené", "Chyba"): self.resume(item)

    def remove_item(self, item):
        worker = self.workers.pop(id(item), None)
        if worker: worker.cancel()
        card = self.cards.pop(id(item)); self.cards_layout.removeWidget(card); card.deleteLater()
        self.items.remove(item); self.save(); self.apply_filter(); self.update_summary()

    def clear_history(self):
        answer = QMessageBox.question(
            self, "Vymazať históriu",
            "Odstrániť všetky položky zo zoznamu? Stiahnuté súbory zostanú na disku.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for worker in self.workers.values():
            worker.cancel()
        self.workers.clear()
        for card in self.cards.values():
            self.cards_layout.removeWidget(card); card.deleteLater()
        self.cards.clear(); self.items.clear(); self.save(); self.apply_filter(); self.update_summary()

    def refresh_item(self, item):
        card = self.cards.get(id(item))
        if card: card.refresh()
        self.apply_filter(); self.update_summary(); self.save()
        if item.status in ("Dokončené", "Chyba", "Zrušené"):
            self.start_queued()
        if item.status == "Dokončené" and id(item) not in self.notified:
            self.notified.add(id(item))
            self.tray.showMessage("Sťahovanie dokončené", item.filename,
                                  QSystemTrayIcon.Information, 5000)

    def open_folder(self, item):
        path = Path(item.destination)
        folder = path if path.is_dir() else path.parent
        QDesktopServices.openUrl(folder.as_uri())

    def update_summary(self):
        active = sum(i.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video") for i in self.items)
        done = sum(i.status == "Dokončené" for i in self.items)
        errors = sum(i.status == "Chyba" for i in self.items)
        speed = human_size(sum(i.speed for i in self.items))
        dot = "<span style='color:{color};font-size:14px;'>&#9679;</span>"
        self.summary.setText(
            f"Spolu: {len(self.items)}"
            f"   {dot.format(color=BLUE_ACCENT)} Aktívne: {active}"
            f"   {dot.format(color=GREEN_SUCCESS)} Dokončené: {done}"
            f"   {dot.format(color=RED_DANGER)} Chyby: {errors}"
            f"   •   Rýchlosť: {speed}/s"
        )

    def find_duplicate(self, url: str) -> Download | None:
        wanted = normalize_url(url)
        for item in self.items:
            original = normalize_url(item.source_url or item.url)
            if original == wanted:
                return item
        return None

    def load_settings(self):
        result = {"download_directory": str(Path.home() / "Downloads"), "max_segments": 4,
                  "max_concurrent": 3, "speed_limit_mbps": 0.0, "auto_categories": False,
                  "custom_categories": [], "category_paths": {}, "ytdlp_auto_update": False}
        try: result.update(json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError): pass
        return result
    def save_settings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2))
    def save(self): save_items(STATE_FILE, self.items)

    def add_from_browser(self, url, media, browser, referrer="", video_format="bestvideo+bestaudio/best"):
        url = normalize_url(url)
        duplicate = self.find_duplicate(url)
        if duplicate:
            QMessageBox.information(self, "Sťahovanie už existuje", f"Táto adresa už je v zozname:\n{duplicate.filename}")
            self.show(); self.raise_()
            return
        self.show_from_tray()
        dialog = AddDialog(self, self.settings["download_directory"], self.settings["custom_categories"],
                           self.settings["category_paths"], url, "Videá" if media else "Automaticky",
                           media, browser, referrer)
        if dialog.exec() != QDialog.Accepted:
            return
        url = normalize_url(dialog.url.text())
        folder = Path(dialog.folder.text()).expanduser()
        selected_category = dialog.category.currentText()
        filename = filename_from_url(url)
        category = category_for_name(filename, media) if selected_category == "Automaticky" else selected_category
        if selected_category != "Automaticky":
            self.settings["category_paths"][category] = str(folder)
        elif self.settings["auto_categories"]:
            folder = Path(self.settings["category_paths"].get(category, str(folder / category)))
        built_in = {"Automaticky", "Videá", "Hudba", "Dokumenty", "Archívy", "Obrázky", "Programy", "Ostatné"}
        all_categories = {dialog.category.itemText(i) for i in range(dialog.category.count())}
        self.settings["custom_categories"] = sorted(all_categories - built_in)
        self.save_settings()
        item = Download(url=url, destination=str(folder) if media else str(folder / filename),
                        media=media, display_name="Video zo stránky" if media else "", auth_browser=browser,
                        source_url=url, referrer=referrer, category=category,
                        video_format=video_format or "bestvideo+bestaudio/best")
        self.apply_download_settings(item)
        self.items.append(item); self.add_card(item); self.save(); self.resume(item); self.show(); self.raise_()

    def start_browser_bridge(self):
        if getattr(self, "server", None):
            return
        signals = self.signals
        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self): self.send_response(204); self.headers_out(); self.end_headers()
            def do_POST(self):
                try:
                    data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 65536)))
                    url = str(data.get("url", "")); media = bool(data.get("media")); browser = str(data.get("browser", ""))
                    referrer = str(data.get("referrer", ""))
                    video_format = str(data.get("format", "") or "bestvideo+bestaudio/best")
                    if not url.startswith(("http://", "https://")): raise ValueError("Neplatná URL")
                    if self.path == "/formats":
                        formats = list_video_formats(url, browser, referrer)
                        self.send_response(200); self.headers_out(); self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "formats": formats}, ensure_ascii=False).encode())
                        return
                    signals.browser_download.emit(url, media, browser, referrer, video_format)
                    self.send_response(202); self.headers_out(); self.end_headers(); self.wfile.write(b'{"ok":true}')
                except Exception as exc:
                    premium = isinstance(exc, PremiumContentError)
                    self.send_response(409 if premium else 400)
                    self.headers_out(); self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(exc), "premium": premium}, ensure_ascii=False).encode())
            def headers_out(self):
                self.send_header("Content-Type", "application/json"); self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Private-Network", "true")
            def log_message(self, *_): pass
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 17654), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        except OSError:
            self.server = None
            QTimer.singleShot(2000, self.start_browser_bridge)


def _stage_icons_ascii_safe(icons_src: Path) -> str:
    """Qt Style Sheets silently fail to load `url(...)` images whose path
    contains non-ASCII characters (confirmed: identical PNG loads fine from
    an ASCII path, renders nothing from one containing the accented "é" in
    this user's actual install path under ~/Stiahnuté/...) — neither percent-
    encoding nor a file:// prefix worked around it. Copying the small icon
    set into an ASCII-only cache dir once at startup sidesteps the bug
    regardless of where the app is installed."""
    cache_dir = Path.home() / ".cache" / "rychlik" / "icons"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for src_file in icons_src.glob("*.png"):
            dst_file = cache_dir / src_file.name
            if not dst_file.exists() or dst_file.stat().st_mtime < src_file.stat().st_mtime:
                shutil.copy2(src_file, dst_file)
        return cache_dir.resolve().as_posix()
    except OSError:
        return icons_src.resolve().as_posix()


def main():
    try:
        log_event(f"Aplikácia spustená s argumentami: {sys.argv}")
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("Rýchlik")
        app.setDesktopFileName("rychlik")
        icons_dir = _stage_icons_ascii_safe(Path(__file__).with_name("icons"))
        qss = Path(__file__).with_name("kde_style.qss").read_text().replace("__ICONS_DIR__", icons_dir)
        app.setStyleSheet(qss)
        window = MainWindow()

        # Nalogovať stav downloads
        total_downloads = len(window.items) if hasattr(window, 'items') else 0
        completed = sum(1 for item in window.items if item.status == "Dokončené") if hasattr(window, 'items') else 0
        log_event(f"Aplikácia inicializovaná - Total downloads: {total_downloads}, Completed: {completed}")

        if "--background" not in sys.argv:
            window.show()
            log_event("GUI okno zobrazené")
        else:
            log_event("Aplikácia spustená v background móde")

        exit_code = app.exec()
        log_event(f"Aplikácia skončila s exit code: {exit_code}")
        return exit_code
    except Exception as e:
        log_event(f"KRITICKÁ CHYBA: {str(e)}", level="ERROR")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
