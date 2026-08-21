"""Точка входа: python -m desktop"""

from __future__ import annotations

import sys
from pathlib import Path

# Позволяет запускать программу из любого каталога (ярлык, меню «Пуск», .exe).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from desktop.app import build_window
    from desktop.theme import STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName("AI Council")
    app.setOrganizationName("AI Council")
    app.setStyleSheet(STYLESHEET)

    window = build_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
