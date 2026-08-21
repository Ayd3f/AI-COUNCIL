"""Тёмная тема окна. Цвета совпадают с веб-версией."""

from __future__ import annotations

BG = "#0b0e15"
PANEL = "#11161f"
PANEL_2 = "#151b26"
BORDER = "#222b3a"
TEXT = "#e6ebf5"
TEXT_DIM = "#97a3b8"
TEXT_FAINT = "#64708a"
ACCENT = "#6f8cff"
ACCENT_2 = "#a78bfa"
OK = "#34d399"
WARN = "#fbbf24"
ERR = "#f87171"
INFO = "#38bdf8"

AGENT_COLORS = {
    "OPENAI": "#10a37f",
    "CLAUDE": "#d97757",
    "GEMINI": "#4285f4",
    "GROK": "#9ca3af",
    "DEEPSEEK": "#7c6cf5",
    # Заметно другой цвет, чем у GROK: имена различаются одной буквой,
    # и цвет — вторая линия защиты от путаницы.
    "GROQCLOUD": "#f97316",
    "CEREBRAS": "#ef4444",
    "MISTRAL": "#facc15",
    "LOCAL": "#22d3ee",
}

STATUS_COLORS = {
    "IDLE": TEXT_FAINT,
    "THINKING": INFO,
    "OK": OK,
    "ERROR": ERR,
    "INVALID_OUTPUT": WARN,
    "DISABLED": "#3a4356",
}

STATUS_TEXT = {
    "IDLE": "ожидает",
    "THINKING": "думает…",
    "OK": "готово",
    "ERROR": "ошибка",
    "INVALID_OUTPUT": "плохой ответ",
    "DISABLED": "нет ключа",
}


STYLESHEET = f"""
/* Фон задаётся только контейнерам. Если повесить его на QWidget, каждая
   подпись внутри карточки рисует свой прямоугольник поверх её фона. */
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 14px;
}}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel, QCheckBox, QToolButton {{ background: transparent; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}

QFrame#Card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QLabel#Title {{
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 3px;
}}
QLabel#Subtitle {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#SectionLabel {{
    color: {TEXT_FAINT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Faint {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#Headline {{ font-size: 22px; font-weight: 700; }}
QLabel#Error {{
    color: #fca5a5;
    background: rgba(248, 113, 113, 0.08);
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 8px;
    padding: 9px 12px;
}}
QLabel#Notice {{
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.08);
    border: 1px solid rgba(251, 191, 36, 0.3);
    border-radius: 8px;
    padding: 9px 12px;
}}

QTextEdit, QTextBrowser, QLineEdit, QPlainTextEdit {{
    background: #080b12;
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 9px 11px;
    selection-background-color: rgba(111, 140, 255, 0.35);
}}
QTextEdit:focus, QLineEdit:focus, QPlainTextEdit:focus {{
    border-color: rgba(111, 140, 255, 0.7);
}}
QTextBrowser#Answer {{
    background: transparent;
    border: none;
    padding: 0;
    font-size: 15px;
}}
QTextBrowser#Details {{
    background: #080b12;
    border: 1px solid #1a2130;
    border-radius: 9px;
    font-size: 13px;
}}

QPushButton {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 8px 15px;
    font-size: 13px;
}}
QPushButton:hover {{ background: #1a2130; border-color: #33405a; }}
QPushButton:pressed {{ background: #131a26; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: #1a2130; }}

QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: #080b13;
    font-weight: 700;
    padding: 10px 26px;
    font-size: 13px;
}}
QPushButton#Primary:hover {{ background: #8099ff; }}
QPushButton#Primary:disabled {{ background: #2a3348; color: {TEXT_FAINT}; }}

QToolButton#Disclosure {{
    background: transparent;
    border: none;
    color: {TEXT};
    font-size: 13.5px;
    text-align: left;
    padding: 8px 4px;
}}
QToolButton#Disclosure:hover {{ color: {ACCENT}; }}

QProgressBar {{
    background: #161d29;
    border: none;
    border-radius: 4px;
    height: 7px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: #232c3c; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #303d52; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QDialog {{ background: {BG}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 9px; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 8px 16px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    background: {PANEL};
    border-color: {BORDER};
}}

QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #080b12;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 9px;
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #080b13;
}}
QCheckBox {{ spacing: 8px; }}
QMessageBox {{ background: {PANEL}; }}
"""
