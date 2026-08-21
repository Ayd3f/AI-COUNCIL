"""Чтение и запись `.env` из окна настроек.

Запись сохраняет комментарии и порядок строк: обновляются только те ключи,
которые пользователь изменил, недостающие дописываются в конец.

Ключи API живут только в этом файле на машине пользователя. Приложение
никогда не отправляет их куда-либо, кроме соответствующего провайдера.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from backend.config import REPO_ROOT, get_settings

ENV_PATH = REPO_ROOT / ".env"
EXAMPLE_PATH = REPO_ROOT / ".env.example"

_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def write_env(updates: dict[str, str], path: Path | None = None) -> Path:
    """Обновляет `.env`, сохраняя комментарии. Возвращает путь к файлу."""
    path = path or ENV_PATH

    if not path.is_file() and EXAMPLE_PATH.is_file():
        # Первый запуск: берём .env.example как основу вместе с пояснениями.
        path.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(updates)
    out: list[str] = []

    for line in lines:
        m = _LINE_RE.match(line) if not line.lstrip().startswith("#") else None
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- добавлено окном «Настройки» --------------------------------")
        out.extend(f"{k}={v}" for k, v in remaining.items())

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def apply_to_process(updates: dict[str, str]) -> None:
    """Применяет новые значения к текущему процессу без перезапуска."""
    for key, value in updates.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    get_settings.cache_clear()
