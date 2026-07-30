#!/usr/bin/env python3
from __future__ import annotations

import os
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from downloader import Download, DownloadWorker, filename_from_url, load_items, reload_download_modules, save_items


APP_ID = "sk.rychlik.Downloader"
STATE_FILE = Path(GLib.get_user_data_dir()) / "rychlik" / "downloads.json"
SETTINGS_FILE = Path(GLib.get_user_config_dir()) / "rychlik" / "settings.json"


def human_size(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


class DownloadRow(Gtk.ListBoxRow):
    def __init__(self, item: Download, app_window: "MainWindow"):
        super().__init__()
        self.add_css_class("download-card")
        self.item = item
        self.app_window = app_window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(14); box.set_margin_end(14)
        top = Gtk.Box(spacing=10)
        self.name = Gtk.Label(label=item.filename, xalign=0, hexpand=True)
        self.name.add_css_class("heading")
        self.status = Gtk.Label(xalign=1)
        top.append(self.name); top.append(self.status)
        self.progress = Gtk.ProgressBar(show_text=True)
        bottom = Gtk.Box(spacing=8)
        self.details = Gtk.Label(xalign=0, hexpand=True)
        self.pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.pause_button.set_tooltip_text("Pozastaviť alebo pokračovať")
        self.pause_button.add_css_class("icon-action")
        self.pause_button.connect("clicked", self._pause_resume)
        cancel = Gtk.Button(icon_name="process-stop-symbolic")
        cancel.set_tooltip_text("Zastaviť sťahovanie")
        cancel.add_css_class("icon-action")
        cancel.connect("clicked", lambda *_: app_window.cancel(item))
        remove = Gtk.Button(icon_name="user-trash-symbolic")
        remove.set_tooltip_text("Odstrániť zo zoznamu")
        remove.add_css_class("icon-action")
        remove.connect("clicked", lambda *_: app_window.remove_item(item))
        folder = Gtk.Button(icon_name="folder-open-symbolic")
        folder.set_tooltip_text("Otvoriť priečinok")
        folder.add_css_class("icon-action")
        folder.connect("clicked", lambda *_: app_window.open_folder(item))
        bottom.append(self.details); bottom.append(folder); bottom.append(self.pause_button); bottom.append(cancel); bottom.append(remove)
        box.append(top); box.append(self.progress); box.append(bottom)
        self.set_child(box)
        self.refresh()

    def _pause_resume(self, *_args) -> None:
        if self.item.status == "Sťahuje sa":
            self.app_window.pause(self.item)
        else:
            self.app_window.resume(self.item)

    def refresh(self) -> None:
        item = self.item
        self.name.set_text(item.filename)
        fraction = item.downloaded / item.total if item.total else 0
        self.progress.set_fraction(min(1, fraction))
        self.progress.set_text(f"{fraction * 100:.1f} %" if item.total else human_size(item.downloaded))
        total = human_size(item.total) if item.total else "neznáma veľkosť"
        speed = f" • {human_size(item.speed)}/s" if item.speed else ""
        self.details.set_text(f"{human_size(item.downloaded)} / {total}{speed}")
        self.status.set_text(item.status if not item.error else f"{item.status}: {item.error}")
        self.pause_button.set_icon_name(
            "media-playback-pause-symbolic" if item.status in ("Sťahuje sa", "Sťahuje video")
            else "media-playback-start-symbolic"
        )
        self.pause_button.set_sensitive(item.status not in ("Dokončené", "Zrušené"))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Rýchlik")
        self.set_default_size(860, 560)
        self.items = load_items(STATE_FILE)
        self.settings = self._load_settings()
        self.filter_mode = "all"
        self.workers: dict[int, DownloadWorker] = {}
        self.rows: dict[int, DownloadRow] = {}

        header = Gtk.HeaderBar()
        header.add_css_class("main-header")
        add = Gtk.Button(label="+ Pridať sťahovanie")
        add.add_css_class("suggested-action")
        add.add_css_class("add-button")
        add.connect("clicked", self.show_add_dialog)
        header.pack_start(add)
        self.search = Gtk.SearchEntry(placeholder_text="Hľadať sťahovanie…")
        self.search.set_size_request(260, -1)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        header.set_title_widget(self.search)
        settings_button = Gtk.Button(label="Nastavenia")
        settings_button.set_icon_name("emblem-system-symbolic")
        settings_button.connect("clicked", self.show_settings_dialog)
        header.pack_end(settings_button)
        self.set_titlebar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        outer.add_css_class("app-shell")
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.add_css_class("sidebar")
        sidebar.set_size_request(190, -1)
        sidebar.set_margin_top(18); sidebar.set_margin_bottom(18)
        sidebar.set_margin_start(12); sidebar.set_margin_end(12)
        brand = Gtk.Label(label="RÝCHLIK", xalign=0)
        brand.add_css_class("brand")
        brand.set_margin_bottom(12)
        sidebar.append(brand)
        for label, mode in (("Všetky sťahovania", "all"), ("Aktívne", "active"),
                            ("Dokončené", "done"), ("Pozastavené", "paused"),
                            ("Chyby", "error")):
            button = Gtk.Button(label=label)
            button.add_css_class("sidebar-button")
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", self.set_filter, mode)
            sidebar.append(button)
        sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        pause_all = Gtk.Button(label="Pozastaviť všetko")
        pause_all.add_css_class("sidebar-button")
        pause_all.connect("clicked", self.pause_all)
        resume_all = Gtk.Button(label="Pokračovať vo všetkom")
        resume_all.add_css_class("sidebar-button")
        resume_all.connect("clicked", self.resume_all)
        sidebar.append(pause_all); sidebar.append(resume_all)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.add_css_class("content-area")
        content_box.set_hexpand(True)
        section = Gtk.Box(spacing=10)
        section.set_margin_top(18); section.set_margin_start(18); section.set_margin_end(18)
        heading = Gtk.Label(label="Sťahovania", xalign=0, hexpand=True)
        heading.add_css_class("page-title")
        section.append(heading)
        content_box.append(section)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.set_margin_top(16); self.listbox.set_margin_bottom(16)
        self.listbox.set_margin_start(16); self.listbox.set_margin_end(16)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.listbox)
        content_box.append(scroll)
        self.empty = Gtk.Label(label="Zatiaľ tu nič nie je. Pridaj URL adresu súboru.")
        self.empty.set_margin_bottom(16)
        content_box.append(self.empty)
        self.summary = Gtk.Label(xalign=0)
        self.summary.set_margin_start(18); self.summary.set_margin_bottom(12)
        self.summary.add_css_class("dim-label")
        content_box.append(self.summary)
        outer.append(sidebar)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        outer.append(content_box)
        self.set_child(outer)
        for item in self.items:
            self._add_row(item)
        self._update_empty()
        self._update_summary()
        self._start_browser_bridge()

    def set_filter(self, _button, mode: str) -> None:
        self.filter_mode = mode
        self.listbox.invalidate_filter()

    def _filter_row(self, row: DownloadRow) -> bool:
        item = row.item
        query = self.search.get_text().strip().casefold() if hasattr(self, "search") else ""
        if query and query not in item.filename.casefold() and query not in item.url.casefold():
            return False
        status = item.status
        return {
            "all": True,
            "active": status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video", "Čaká"),
            "done": status == "Dokončené",
            "paused": status == "Pozastavené",
            "error": status in ("Chyba", "Zrušené"),
        }.get(self.filter_mode, True)

    def pause_all(self, *_args) -> None:
        for item in self.items:
            if item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video"):
                self.pause(item)

    def resume_all(self, *_args) -> None:
        for item in self.items:
            if item.status in ("Čaká", "Pozastavené", "Chyba"):
                self.resume(item)

    def _start_browser_bridge(self) -> None:
        window = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self.send_response(204); self._headers(); self.end_headers()

            def do_POST(self):
                if self.path != "/download":
                    self.send_error(404); return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 64 * 1024)
                    data = json.loads(self.rfile.read(length))
                    url = str(data.get("url", "")).strip()
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("Neplatná URL")
                    media = bool(data.get("media", False))
                    browser = str(data.get("browser", ""))
                    GLib.idle_add(window.add_from_browser, url, media, browser)
                    self.send_response(202); self._headers(); self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except Exception as exc:
                    self.send_response(400); self._headers(); self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode())

            def _headers(self):
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def log_message(self, *_args):
                pass

        try:
            self.bridge = ThreadingHTTPServer(("127.0.0.1", 17654), Handler)
            threading.Thread(target=self.bridge.serve_forever, daemon=True).start()
        except OSError:
            self.bridge = None

    def add_from_browser(self, url: str, media: bool = False, browser: str = "") -> bool:
        folder = Path(self.settings["download_directory"]).expanduser()
        if media:
            item = Download(url=url, destination=str(folder), media=True,
                            display_name="Video zo stránky", auth_browser=browser)
        else:
            item = Download(url=url, destination=str(folder / filename_from_url(url)))
        self.items.append(item); self._add_row(item); self._save(); self.resume(item)
        self.present()
        return GLib.SOURCE_REMOVE

    def show_add_dialog(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Nové sťahovanie", transient_for=self, modal=True)
        dialog.add_button("Zrušiť", Gtk.ResponseType.CANCEL)
        dialog.add_button("Stiahnuť", Gtk.ResponseType.OK)
        content = dialog.get_content_area(); content.set_spacing(10)
        content.set_margin_top(16); content.set_margin_bottom(16)
        content.set_margin_start(16); content.set_margin_end(16)
        url = Gtk.Entry(placeholder_text="https://example.com/subor.zip")
        url.set_hexpand(True)
        destination = Gtk.Entry(text=self.settings["download_directory"])
        content.append(Gtk.Label(label="URL adresa", xalign=0)); content.append(url)
        content.append(Gtk.Label(label="Cieľový priečinok", xalign=0)); content.append(destination)
        dialog.connect("response", self._add_response, url, destination)
        dialog.present(); url.grab_focus()

    def _add_response(self, dialog, response, url_entry, destination_entry) -> None:
        url = url_entry.get_text().strip()
        folder = Path(destination_entry.get_text()).expanduser()
        if response == Gtk.ResponseType.OK and url.startswith(("http://", "https://")):
            item = Download(url=url, destination=str(folder / filename_from_url(url)))
            self.items.append(item); self._add_row(item); self._save(); self.resume(item)
        dialog.destroy()

    def show_settings_dialog(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Nastavenia", transient_for=self, modal=True)
        dialog.add_button("Zrušiť", Gtk.ResponseType.CANCEL)
        dialog.add_button("Uložiť", Gtk.ResponseType.OK)
        content = dialog.get_content_area(); content.set_spacing(10)
        content.set_margin_top(16); content.set_margin_bottom(16)
        content.set_margin_start(16); content.set_margin_end(16)
        content.append(Gtk.Label(label="Predvolený priečinok sťahovania", xalign=0))
        directory = Gtk.Entry(text=self.settings["download_directory"], hexpand=True)
        content.append(directory)
        note = Gtk.Label(label="Priečinok môžeš zmeniť aj pri každom novom sťahovaní.", xalign=0)
        note.add_css_class("dim-label")
        content.append(note)
        module_button = Gtk.Button(label="Pridať modul")
        module_button.set_icon_name("list-add-symbolic")
        module_button.connect("clicked", self._choose_download_module)
        content.append(module_button)
        dialog.connect("response", self._settings_response, directory)
        dialog.present()

    def _choose_download_module(self, *_args) -> None:
        dialog = Gtk.FileChooserNative(
            title="Pridať modul",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Pridať",
            cancel_label="Zrušiť",
        )
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Moduly")
        file_filter.add_pattern("*.py")
        file_filter.add_pattern("*.sh")
        dialog.add_filter(file_filter)
        dialog.connect("response", self._module_file_chosen)
        dialog.show()

    def _module_file_chosen(self, dialog, response) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            source = Path(file.get_path()) if file and file.get_path() else None
            if source:
                try:
                    self._install_download_module(source)
                except Exception as exc:
                    self._show_message("Modul sa nepodarilo pridať", str(exc))
                else:
                    self._show_message("Modul pridaný", f"Modul bol pridaný: {source.name}")
        dialog.destroy()

    def _install_download_module(self, source: Path) -> None:
        if source.suffix not in {".py", ".sh"}:
            raise ValueError("Vyber súbor .py alebo .sh.")
        modules_dir = Path(__file__).with_name("download_modules")
        modules_dir.mkdir(parents=True, exist_ok=True)
        target = modules_dir / source.name
        shutil.copy2(source, target)
        if target.suffix == ".sh":
            target.chmod(target.stat().st_mode | 0o111)
        reload_download_modules()

    def _show_message(self, title: str, text: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, text=title, secondary_text=text)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.connect("response", lambda dlg, _response: dlg.destroy())
        dialog.present()

    def _settings_response(self, dialog, response, directory_entry) -> None:
        if response == Gtk.ResponseType.OK:
            text = directory_entry.get_text().strip()
            if text:
                directory = Path(text).expanduser()
                directory.mkdir(parents=True, exist_ok=True)
                self.settings["download_directory"] = str(directory)
                self._save_settings()
        dialog.destroy()

    def _add_row(self, item: Download) -> None:
        row = DownloadRow(item, self)
        self.rows[id(item)] = row
        self.listbox.append(row)
        self._update_empty()
        if hasattr(self, "summary"):
            self._update_summary()

    def _worker(self, item: Download) -> DownloadWorker:
        worker = self.workers.get(id(item))
        if worker is None:
            worker = DownloadWorker(item, self._thread_update)
            self.workers[id(item)] = worker
        return worker

    def resume(self, item: Download) -> None:
        self._worker(item).start()

    def pause(self, item: Download) -> None:
        self._worker(item).pause(); self._save()

    def cancel(self, item: Download) -> None:
        self._worker(item).cancel()

    def remove_item(self, item: Download) -> None:
        worker = self.workers.pop(id(item), None)
        if worker:
            worker.cancel()
        row = self.rows.pop(id(item), None)
        if row:
            self.listbox.remove(row)
        if item in self.items:
            self.items.remove(item)
        self._save()
        self._update_empty()
        self._update_summary()

    def _thread_update(self, item: Download) -> None:
        GLib.idle_add(self._refresh, item)

    def _refresh(self, item: Download) -> bool:
        row = self.rows.get(id(item))
        if row: row.refresh()
        self.listbox.invalidate_filter()
        self._update_summary()
        self._save()
        return GLib.SOURCE_REMOVE

    def _save(self) -> None:
        save_items(STATE_FILE, self.items)

    def _load_settings(self) -> dict:
        settings = {"download_directory": str(Path.home() / "Downloads")}
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("download_directory"), str):
                settings.update(loaded)
        except (OSError, ValueError):
            pass
        return settings

    def _save_settings(self) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, SETTINGS_FILE)

    def _update_empty(self) -> None:
        if hasattr(self, "empty"):
            self.empty.set_visible(not self.items)

    def _update_summary(self) -> None:
        if not hasattr(self, "summary"):
            return
        active = sum(item.status in ("Sťahuje sa", "Sťahuje video", "Analyzuje video") for item in self.items)
        done = sum(item.status == "Dokončené" for item in self.items)
        speed = sum(item.speed for item in self.items)
        self.summary.set_text(
            f"Spolu: {len(self.items)}  •  Aktívne: {active}  •  Dokončené: {done}  •  {human_size(speed)}/s"
        )

    def open_folder(self, item: Download) -> None:
        Gio.AppInfo.launch_default_for_uri(Path(item.destination).parent.as_uri(), None)


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()


if __name__ == "__main__":
    raise SystemExit(Application().run())
