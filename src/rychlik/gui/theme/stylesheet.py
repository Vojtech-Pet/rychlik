"""QSS generation from the approved tokens.

All colors and dimensions come from rychlik.gui.theme.tokens; widgets never
hard-code a color. Variants are selected with dynamic properties:
    QPushButton[variant="primary|secondary|tertiary|danger|dangerText"]
    QPushButton[size="xs"]
    QLabel[role="caption|muted|heading|dialogTitle|kbd"]
    QFrame#Sidebar, QFrame#Toolbar, QFrame#StatusBar, QFrame#BulkBar
"""

from __future__ import annotations

from rychlik.gui.theme.tokens import Palette, metrics, palette


def build_stylesheet(theme: str) -> str:
    p: Palette = palette(theme)
    m = metrics()
    return f"""
* {{ font-size: {m.font_body}px; }}
QWidget {{ color: {p.text}; }}
QMainWindow, QDialog, QMessageBox {{ background: {p.background}; }}
QDialog {{ background: {p.elevated}; }}
QToolTip {{ background: {p.text}; color: {p.background}; border: none; border-radius: 6px; padding: 5px 8px; font-size: {m.font_caption}px; }}

QMenuBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; min-height: {m.menu_height}px; padding: 0 4px; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; color: {p.text2}; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {p.accent_tint}; color: {p.text}; }}
QMenu {{ background: {p.elevated}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 5px; color: {p.text}; }}
QMenu::item:selected {{ background: {p.accent_tint}; }}
QMenu::item:disabled {{ color: {p.text_disabled}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 8px; }}

QFrame#Toolbar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
QFrame#Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
QFrame#StatusBar {{ background: {p.surface}; border-top: 1px solid {p.border}; }}
QFrame#StatusBar QLabel {{ color: {p.text2}; font-size: {m.font_caption}px; }}
QWidget#Content {{ background: {p.background}; }}
QFrame#BulkBar {{ background: {p.accent_tint}; border: 1px solid {p.accent}; border-radius: {m.radius_medium}px; }}
QFrame#Card {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; }}
QFrame#Card[selected="true"] {{ border-color: {p.accent}; background: {p.accent_tint}; }}
QFrame#Banner {{ border-radius: 8px; }}

QLabel[role="caption"] {{ color: {p.text2}; font-size: {m.font_caption}px; }}
QLabel[role="muted"] {{ color: {p.text2}; }}
QLabel[role="heading"] {{ font-size: {m.font_heading}px; font-weight: 600; }}
QLabel[role="dialogTitle"] {{ font-size: {m.font_dialog_title}px; font-weight: 600; }}
QLabel[role="section"] {{ color: {p.text2}; font-size: {m.font_caption - 0.5}px; font-weight: 700; letter-spacing: 1px; }}
QLabel[tone="success"] {{ color: {p.success_text}; }}
QLabel[tone="warning"] {{ color: {p.warning_text}; }}
QLabel[tone="error"] {{ color: {p.error_text}; }}
QLabel[tone="info"] {{ color: {p.info_text}; }}

QPushButton {{
    min-height: {m.control_height - 2}px; padding: 0 14px; border-radius: {m.radius_medium}px;
    border: 1px solid {p.border}; background: {p.surface2}; color: {p.text}; font-weight: 500;
}}
QPushButton:hover {{ background: {p.elevated}; border-color: {p.border_strong}; }}
QPushButton:pressed {{ background: {p.surface}; }}
QPushButton:focus {{ border: 2px solid {p.focus}; padding: 0 13px; }}
QPushButton:disabled {{ color: {p.text_disabled}; background: transparent; border-color: {p.border}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; border-color: {p.accent}; color: {p.on_accent}; }}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_pressed}; border-color: {p.accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.surface2}; color: {p.text_disabled}; border-color: {p.border}; }}
QPushButton[variant="tertiary"] {{ background: transparent; border-color: transparent; color: {p.accent_text}; }}
QPushButton[variant="tertiary"]:hover {{ background: {p.accent_tint}; }}
QPushButton[variant="tertiary"]:disabled {{ color: {p.text_disabled}; }}
QPushButton[variant="danger"] {{ background: {p.error_solid}; border-color: {p.error_solid}; color: #FFFFFF; }}
QPushButton[variant="dangerText"] {{ background: transparent; border-color: transparent; color: {p.error_text}; }}
QPushButton[variant="dangerText"]:hover {{ background: {p.surface2}; }}
QPushButton[size="xs"] {{ min-height: 20px; padding: 0 8px; font-size: {m.font_caption}px; border-radius: 5px; }}
QPushButton[toolbar="true"] {{ background: transparent; border-color: transparent; min-height: 32px; padding: 0 12px; }}
QPushButton[toolbar="true"]:hover {{ background: {p.surface2}; }}
QPushButton[toolbar="true"][variant="primary"] {{ background: {p.accent}; }}
QPushButton[toolbar="true"][variant="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[toolbar="true"]:disabled {{ color: {p.text_disabled}; background: transparent; }}
QPushButton[sidebarItem="true"] {{
    background: transparent; border: none; border-radius: {m.radius_medium}px; text-align: left;
    min-height: 28px; padding: 0 8px 0 8px; font-weight: 400;
}}
QPushButton[sidebarItem="true"]:hover {{ background: {p.surface2}; }}
QPushButton[sidebarItem="true"][active="true"] {{ background: {p.accent_tint}; color: {p.accent_text}; font-weight: 600; border-left: 2px solid {p.accent}; border-radius: {m.radius_medium}px; }}
QPushButton[sidebarItem="true"]:focus {{ border: 2px solid {p.focus}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    min-height: {m.control_height - 2}px; padding: 0 8px; border-radius: {m.radius_medium}px;
    border: 1px solid {p.border}; background: {p.surface2}; color: {p.text};
    selection-background-color: {p.accent}; selection-color: {p.on_accent};
}}
QPlainTextEdit, QTextEdit {{ padding: 6px 8px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 2px solid {p.focus}; }}
QLineEdit:disabled {{ color: {p.text_disabled}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {p.elevated}; border: 1px solid {p.border}; selection-background-color: {p.accent_tint}; selection-color: {p.text}; outline: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1.5px solid {p.border_strong}; border-radius: 4px; background: transparent; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QCheckBox:focus {{ outline: none; }}

QTableView {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: {m.radius_medium}px;
    gridline-color: transparent; outline: none; selection-background-color: transparent;
}}
QTableView::item {{ border: none; padding: 0 4px; }}
QHeaderView {{ background: {p.surface2}; }}
QHeaderView::section {{
    background: {p.surface2}; color: {p.text2}; padding: 0 8px; border: none;
    border-bottom: 1px solid {p.border}; font-size: {m.font_caption}px; font-weight: 600; min-height: {m.table_header_height}px;
}}
QTableCornerButton::section {{ background: {p.surface2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text2}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{ padding: 8px 14px; color: {p.text2}; border: none; border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {p.border}; }}

QProgressBar {{ border: none; background: {p.border}; border-radius: 4px; max-height: 8px; min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p.info}; border-radius: 4px; }}

QStatusBar {{ background: {p.surface}; color: {p.text2}; }}
QSplitter::handle {{ background: {p.border}; }}
QAbstractItemView:focus {{ outline: none; }}
"""
