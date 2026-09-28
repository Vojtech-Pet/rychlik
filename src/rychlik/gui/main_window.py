"""Approved Rýchlik Desktop main window: menu bar, toolbar, sidebar, content pages, status bar.

Composition only: it owns no download state. All download behavior lives in DownloadManagerWidget
(backed by DownloadManagerService); Device Mode pages are provided by an optional controller.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rychlik.core.download_manager_service import DownloadManagerService
from rychlik.gui import presentation as P
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.gui.queue_view import QueueView
from rychlik.gui.theme import icons
from rychlik.gui.theme.manager import ThemeManager
from rychlik.gui.theme.tokens import metrics, palette

_STATUS_ICONS = {"All": "download", "Downloading": "arrow-down", "Waiting": "clock", "Paused": "pause", "Completed": "check-circle", "Failed": "x-circle"}
_CATEGORY_ICONS = {c: P.category_icon(c) for c in P.CATEGORIES}

PAGE_DOWNLOADS, PAGE_QUEUES, PAGE_DEVICES = 0, 1, 2


class SidebarItem(QPushButton):
    """Icon + label + optional count. Active state is a dynamic property styled by QSS."""

    def __init__(self, key: str, label: str, glyph: str, *, counted: bool = True) -> None:
        super().__init__()
        self.key = key
        self.label_text = label
        self.glyph = glyph
        self.setProperty("sidebarItem", True)
        self.setProperty("active", False)
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(10)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text_label = QLabel(label)
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "caption")
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.count_label.setVisible(counted)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label, 1)
        layout.addWidget(self.count_label)
        self.setFixedHeight(30)

    def set_active(self, active: bool, theme: str) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        p = palette(theme)
        color = p.accent_text if active else p.icon
        self.icon_label.setPixmap(icons.pixmap(self.glyph, metrics().icon_sidebar, color))
        self.text_label.setStyleSheet(f"color: {p.accent_text if active else p.text}; font-weight: {600 if active else 400}; background: transparent;")
        self.count_label.setStyleSheet(f"color: {p.accent_text if active else p.text2}; background: transparent;")

    def set_count(self, n: int | None) -> None:
        self.count_label.setText("" if n is None else str(n))

    def set_collapsed(self, collapsed: bool) -> None:
        self.text_label.setVisible(not collapsed)
        self.count_label.setVisible(not collapsed and self.count_label.text() != "")
        self.setToolTip(self.label_text if collapsed else "")


class MainWindow(QMainWindow):
    def __init__(
        self,
        manager: DownloadManagerService,
        widget: DownloadManagerWidget,
        *,
        theme_manager: ThemeManager | None = None,
        devices_page: QWidget | None = None,
        device_actions: dict | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._widget = widget
        self._themes = theme_manager
        self._theme = theme_manager.theme if theme_manager else "dark"
        self._devices_page = devices_page
        self._device_actions = device_actions or {}
        self._sidebar_items: dict[str, SidebarItem] = {}
        self._active_key = "status:All"
        self._collapsed = False
        self.setWindowTitle("Rýchlik")
        m = metrics()
        self.setMinimumSize(m.min_window_width - 120, m.min_window_height)
        self.resize(1366, 768)

        self.queue_view = QueueView(manager, widget, theme=self._theme)
        self._build_ui()
        self._build_menus()
        self._wire()
        self._select_sidebar("status:All")
        self._apply_width()
        self._widget.publish_state()

    # --- construction ----------------------------------------------------------------------------

    def _build_ui(self) -> None:
        m = metrics()
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.toolbar = QFrame()
        self.toolbar.setObjectName("Toolbar")
        self.toolbar.setFixedHeight(m.toolbar_height)
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(10, 0, 10, 0)
        tb.setSpacing(4)
        self.add_button = self._toolbar_button("Add", "plus", primary=True)
        self.queues_button = self._toolbar_button("Queues", "list")
        self.settings_button = self._toolbar_button("Settings", "gear")
        tb.addWidget(self.add_button)
        tb.addSpacing(12)
        tb.addWidget(self.queues_button)
        tb.addWidget(self.settings_button)
        tb.addStretch(1)
        outer.addWidget(self.toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(m.sidebar_width)
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(8, 6, 8, 6)
        sb.setSpacing(2)
        self._section_labels: list[QLabel] = []

        def section(title: str) -> None:
            label = QLabel(title.upper())
            label.setProperty("role", "section")
            label.setContentsMargins(8, 12, 0, 4)
            self._section_labels.append(label)
            sb.addWidget(label)

        section("Downloads")
        for name in P.SIDEBAR_FILTERS:
            self._add_sidebar_item(sb, f"status:{name}", name, _STATUS_ICONS[name])
        section("Categories")
        for name in P.CATEGORIES:
            self._add_sidebar_item(sb, f"category:{name}", name, _CATEGORY_ICONS[name])
        section("Tools")
        self._add_sidebar_item(sb, "tool:queues", "Queues", "list", counted=False)
        if self._devices_page is not None:
            self._add_sidebar_item(sb, "tool:devices", "Devices", "phone", counted=False)
        self._add_sidebar_item(sb, "tool:settings", "Settings", "gear", counted=False)
        sb.addStretch(1)
        body.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._widget)  # PAGE_DOWNLOADS
        self.pages.addWidget(self.queue_view)  # PAGE_QUEUES
        if self._devices_page is not None:
            self.pages.addWidget(self._devices_page)  # PAGE_DEVICES
        body.addWidget(self.pages, 1)
        wrapper = QWidget()
        wrapper.setLayout(body)
        outer.addWidget(wrapper, 1)

        self.status_bar = QFrame()
        self.status_bar.setObjectName("StatusBar")
        self.status_bar.setFixedHeight(m.statusbar_height)
        sl = QHBoxLayout(self.status_bar)
        sl.setContentsMargins(12, 0, 12, 0)
        sl.setSpacing(18)
        self.status_dot = QLabel()
        self.status_active = QLabel("Idle")
        self.status_waiting = QLabel("")
        self.status_speed = QLabel("")
        for w in (self.status_dot, self.status_active, self.status_waiting, self.status_speed):
            sl.addWidget(w)
        sl.addStretch(1)
        outer.addWidget(self.status_bar)
        self.setCentralWidget(central)
        self._refresh_static_icons()

    def _toolbar_button(self, text: str, glyph: str, *, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("toolbar", True)
        if primary:
            button.setProperty("variant", "primary")
        button.setProperty("glyph", glyph)
        button.setIconSize(QSize(metrics().icon_toolbar, metrics().icon_toolbar))
        button.setFixedHeight(34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _add_sidebar_item(self, layout: QVBoxLayout, key: str, label: str, glyph: str, *, counted: bool = True) -> None:
        item = SidebarItem(key, label, glyph, counted=counted)
        item.clicked.connect(lambda _=False, k=key: self._select_sidebar(k))
        self._sidebar_items[key] = item
        layout.addWidget(item)

    def _refresh_static_icons(self) -> None:
        p = palette(self._theme)
        for button in (self.add_button, self.queues_button, self.settings_button):
            color = p.on_accent if button.property("variant") == "primary" else p.icon
            button.setIcon(icons.icon(button.property("glyph"), metrics().icon_toolbar, color, disabled_color=p.text_disabled))
        self.status_dot.setPixmap(icons.pixmap("dot", 10, p.info))

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self.action_add = self._action("Add download…", self._widget.open_add_dialog, QKeySequence.StandardKey.New, file_menu)
        self.action_open_dest = self._action("Open download folder", self._open_destination, None, file_menu)
        file_menu.addSeparator()
        self.action_quit = self._action("Quit", self.close, QKeySequence.StandardKey.Quit, file_menu)

        self.tasks_menu = bar.addMenu("&Tasks")
        self._task_actions: dict[P.RowAction, QAction] = {}
        for action, text in ((P.RowAction.PAUSE, "Pause"), (P.RowAction.RESUME, "Resume"), (P.RowAction.HOLD, "Hold"),
                             (P.RowAction.RELEASE, "Release"), (P.RowAction.RETRY_NOW, "Retry now"), (P.RowAction.CANCEL, "Cancel")):
            act = self._action(text, lambda a=action: self._widget.run_action(a, self._widget.selected_queue_entry_ids()), None, self.tasks_menu)
            self._task_actions[action] = act
        self.tasks_menu.addSeparator()
        self.action_details = self._action("Details", lambda: self._for_single(self._widget.show_details), None, self.tasks_menu)
        self.action_details.setShortcut(QKeySequence("Alt+Return"))
        self.action_share = self._action("Share…", lambda: self._for_single(self._widget.share), None, self.tasks_menu)
        self.tasks_menu.aboutToShow.connect(self._update_task_menu)

        tools = bar.addMenu("&Tools")
        self._action("Queues", lambda: self._select_sidebar("tool:queues"), None, tools)
        if self._devices_page is not None:
            self._action("Devices", lambda: self._select_sidebar("tool:devices"), None, tools)
            for label, key in (("Pair FriendSend…", "pair"),):
                if key in self._device_actions:
                    self._action(label, self._device_actions[key], None, tools)
        self._action("Settings…", self.open_settings, None, tools)

        help_menu = bar.addMenu("&Help")
        self._action("About Rýchlik", self._about, None, help_menu)

    def _action(self, text, slot, shortcut, menu) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda _=False: slot())
        menu.addAction(act)
        return act

    def _wire(self) -> None:
        self.add_button.clicked.connect(self._widget.open_add_dialog)
        self.queues_button.clicked.connect(lambda: self._select_sidebar("tool:queues"))
        self.settings_button.clicked.connect(self.open_settings)
        self._widget.summary_changed.connect(self._on_summary)
        self._widget.counts_changed.connect(self._on_counts)
        self._widget.add_requested.connect(self._widget.open_add_dialog)
        if self._themes is not None:
            self._themes.theme_changed.connect(self._on_theme_changed)

    # --- behavior --------------------------------------------------------------------------------------

    def _for_single(self, fn) -> None:
        entry_id = self._widget._selected_queue_entry_id()  # noqa: SLF001
        if entry_id:
            fn(entry_id)

    def _update_task_menu(self) -> None:
        items = self._widget.selected_items()
        enabled = P.bulk_actions(items) if items else {}
        for action, act in self._task_actions.items():
            if action == P.RowAction.RETRY_NOW:
                act.setEnabled(any(P.available_actions(i)[P.RowAction.RETRY_NOW].visible for i in items))
            else:
                act.setEnabled(bool(enabled.get(action)))
        single = items[0] if len(items) == 1 else None
        self.action_details.setEnabled(single is not None)
        self.action_share.setEnabled(single is not None and P.available_actions(single)[P.RowAction.SHARE].visible)

    def _select_sidebar(self, key: str) -> None:
        if key == "tool:settings":
            self.open_settings()
            return
        if key == "tool:queues":
            self.pages.setCurrentIndex(PAGE_QUEUES)
        elif key == "tool:devices" and self._devices_page is not None:
            self.pages.setCurrentIndex(PAGE_DEVICES)
        else:
            self.pages.setCurrentIndex(PAGE_DOWNLOADS)
            if key.startswith("status:"):
                name = key.split(":", 1)[1]
                self._widget.set_status_filter(name)
            elif key.startswith("category:"):
                self._widget.set_category_filter(key.split(":", 1)[1])
        self._active_key = key
        for k, item in self._sidebar_items.items():
            item.set_active(k == key, self._theme)

    @property
    def active_sidebar_key(self) -> str:
        return self._active_key

    def _on_counts(self, counts: dict) -> None:
        for name, n in counts["status"].items():
            self._sidebar_items[f"status:{name}"].set_count(n)
        for name, n in counts["category"].items():
            self._sidebar_items[f"category:{name}"].set_count(n)

    def _on_summary(self, active: int, waiting: int, speed: str) -> None:
        self.status_active.setText(f"{active} active" if active or waiting else "Idle")
        self.status_waiting.setText(f"{waiting} waiting" if waiting else "")
        self.status_speed.setText(f"↓ {speed}" if speed else "")

    def _on_theme_changed(self, theme: str) -> None:
        self._theme = theme
        self._widget.set_theme(theme)
        self.queue_view.set_theme(theme)
        self._refresh_static_icons()
        for k, item in self._sidebar_items.items():
            item.set_active(k == self._active_key, theme)
        if self._devices_page is not None and hasattr(self._devices_page, "set_theme"):
            self._devices_page.set_theme(theme)

    def open_settings(self) -> None:
        from rychlik.gui.dialogs import SettingsDialog

        SettingsDialog(self._themes, self).exec()

    def _open_destination(self) -> None:
        self._widget._folder_opener(self._widget.destination_dir)  # noqa: SLF001

    def _about(self) -> None:
        QMessageBox.information(self, "About Rýchlik", "Rýchlik\nA compact download manager with optional FriendSend sharing.")

    def _apply_width(self) -> None:
        layout = self._widget.apply_width(self.width())
        if layout.sidebar_collapsed != self._collapsed:
            self._collapsed = layout.sidebar_collapsed
            self.sidebar.setFixedWidth(48 if self._collapsed else metrics().sidebar_width)
            for label in self._section_labels:
                label.setVisible(not self._collapsed)
            for item in self._sidebar_items.values():
                item.set_collapsed(self._collapsed)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_width()

    @property
    def widget(self) -> DownloadManagerWidget:
        return self._widget

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._widget.confirm_close():
            event.ignore()
            return
        self._widget.prepare_shutdown()
        self._widget.shutdown()
        self._manager.stop()
        event.accept()
