#!/usr/bin/env python
"""Запуск десктопного приложения AI Council из любого каталога.

    python "D:\\AICouncil\\ai_council.py"

Python всегда кладёт каталог запускаемого скрипта первым в sys.path, поэтому
пакеты `desktop` и `backend` находятся независимо от текущего каталога. У
`python -m desktop` этого свойства нет: модуль ищется до того, как отработает
код внутри него, — поэтому такой запуск возможен только из корня проекта.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from desktop.__main__ import main as run
    except ImportError as exc:  # pragma: no cover - подсказка вместо трейсбека
        missing = getattr(exc, "name", "") or str(exc)
        sys.stderr.write(
            f"Не удалось загрузить зависимости ({missing}).\n"
            "Установите их один раз:\n"
            f"    {sys.executable} -m pip install -r "
            f"{ROOT / 'desktop' / 'requirements.txt'}\n"
        )
        return 2
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
