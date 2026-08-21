"""Главное окно.

Принцип интерфейса — постепенное раскрытие: на виду один вопрос и один ответ,
всё остальное спрятано за подписями, которые прямо говорят, что внутри.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.agents import registry
from backend.config import get_settings
from backend.models.schemas import DebateDetail
from backend.services.diagnostics import explain

from . import report, theme
from .controller import DebateController
from .settings_dialog import SettingsDialog
from .widgets import AgentStrip, CollapsibleSection, MarkdownView, card, section_label

PLACEHOLDER = (
    "Задайте один вопрос. Пять моделей сначала ответят порознь, "
    "потом обсудят ответы друг друга."
)


class HistoryDialog(QDialog):
    def __init__(self, items: list[Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("История обсуждений")
        self.setMinimumSize(620, 420)
        self.selected_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.list = QListWidget()
        for item in items:
            reached = "консенсус" if item.consensus_reached else "без консенсуса"
            text = (
                f"{item.question}\n"
                f"{item.rounds_used}/{item.max_rounds} раундов · "
                f"{round(item.consensus_score * 100)}% · {reached} · {item.status.value}"
            )
            entry = QListWidgetItem(text)
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            entry.setSizeHint(QSize(0, 56))
            self.list.addItem(entry)
        self.list.itemDoubleClicked.connect(self._choose)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        clear = QPushButton("Очистить историю")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        row.addStretch(1)
        cancel = QPushButton("Закрыть")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        open_btn = QPushButton("Открыть")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(lambda: self._choose(self.list.currentItem()))
        row.addWidget(open_btn)
        layout.addLayout(row)

        self.clear_requested = False

    def _choose(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        self.selected_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _clear(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Очистить историю",
            "Удалить все сохранённые обсуждения? Отменить будет нельзя.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.clear_requested = True
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AI Council")
        self.resize(940, 800)
        self.setMinimumSize(720, 560)

        self.controller = DebateController(self)
        self.controller.event.connect(self._on_event)
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)

        self._detail: DebateDetail | None = None
        self._max_rounds = 0
        self._roles: dict[str, str] = {}

        self._build_ui()
        self._refresh_availability()

    # --------------------------------------------------------------- интерфейс
    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 28)
        root.setSpacing(16)

        root.addLayout(self._header())
        root.addWidget(self._ask_card())
        root.addWidget(self._status_card())
        root.addWidget(self._answer_card())
        root.addStretch(1)

        scroll.setWidget(page)
        self.setCentralWidget(scroll)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_ask)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._on_ask)

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("AI COUNCIL")
        title.setObjectName("Title")
        titles.addWidget(title)
        ready = len(registry.configured_agents(get_settings()))
        total = len(registry.ROSTER)
        self.subtitle = QLabel(
            f"Модели отвечают порознь, затем обсуждают · подключено "
            f"{ready} из {total}"
        )
        self.subtitle.setObjectName("Subtitle")
        titles.addWidget(self.subtitle)
        row.addLayout(titles)
        row.addStretch(1)

        self.history_button = QPushButton("История")
        self.history_button.clicked.connect(self._on_history)
        row.addWidget(self.history_button)

        # Без пиктограмм: символы вроде ⚙ есть не в каждом системном шрифте
        # и превращаются в пустой квадрат.
        self.settings_button = QPushButton("Настройки")
        self.settings_button.clicked.connect(self._on_settings)
        row.addWidget(self.settings_button)
        return row

    def _ask_card(self) -> QWidget:
        frame, layout = card()

        self.question = QTextEdit()
        self.question.setPlaceholderText(PLACEHOLDER)
        self.question.setFixedHeight(112)
        layout.addWidget(self.question)

        self.notice = QLabel()
        self.notice.setObjectName("Notice")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        row = QHBoxLayout()
        self.hint = QLabel("Ctrl+Enter — спросить")
        self.hint.setObjectName("Faint")
        row.addWidget(self.hint)
        row.addStretch(1)

        self.stop_button = QPushButton("Остановить")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.controller.cancel)
        row.addWidget(self.stop_button)

        self.ask_button = QPushButton("Спросить")
        self.ask_button.setObjectName("Primary")
        self.ask_button.clicked.connect(self._on_ask)
        row.addWidget(self.ask_button)
        layout.addLayout(row)

        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)
        return frame

    def _status_card(self) -> QWidget:
        frame, layout = card(spacing=10)
        self.status_frame = frame

        self.strip = AgentStrip()
        layout.addWidget(self.strip)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.phase_label = QLabel("Готово к запуску")
        self.phase_label.setObjectName("Dim")
        row.addWidget(self.phase_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        row.addWidget(self.progress, 1)
        layout.addLayout(row)

        frame.setVisible(False)
        return frame

    def _answer_card(self) -> QWidget:
        frame, layout = card()
        self.answer_frame = frame

        layout.addWidget(section_label("Ответ"))

        self.answer = MarkdownView("Answer")
        layout.addWidget(self.answer)

        self.summary = QLabel()
        self.summary.setObjectName("Dim")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.failed_note = QLabel()
        self.failed_note.setObjectName("Notice")
        self.failed_note.setWordWrap(True)
        self.failed_note.setVisible(False)
        layout.addWidget(self.failed_note)

        self.section_rounds = CollapsibleSection("Показать ход обсуждения")
        self.rounds_view = MarkdownView()
        self.section_rounds.add(self.rounds_view)
        layout.addWidget(self.section_rounds)

        self.section_disagreements = CollapsibleSection("Расхождения и мнение меньшинства")
        self.disagreements_view = MarkdownView()
        self.section_disagreements.add(self.disagreements_view)
        layout.addWidget(self.section_disagreements)

        self.section_tokens = CollapsibleSection("Расход токенов и стоимость")
        self.tokens_view = MarkdownView()
        self.section_tokens.add(self.tokens_view)
        layout.addWidget(self.section_tokens)

        row = QHBoxLayout()
        row.addStretch(1)
        copy = QPushButton("Копировать ответ")
        copy.clicked.connect(self._on_copy)
        row.addWidget(copy)
        save = QPushButton("Сохранить отчёт…")
        save.clicked.connect(self._on_save)
        row.addWidget(save)
        layout.addLayout(row)

        frame.setVisible(False)
        return frame

    # ---------------------------------------------------------------- действия
    def _refresh_availability(self) -> None:
        settings = get_settings()
        agents = registry.configured_agents(settings)
        ready = len(agents) >= 2
        self.ask_button.setEnabled(ready and not self.controller.busy)
        self.subtitle.setText(
            f"Модели отвечают порознь, затем обсуждают · подключено "
            f"{len(agents)} из {len(registry.ROSTER)}"
        )
        if ready:
            self.notice.setVisible(False)
        else:
            missing = ", ".join(
                registry.ENV_HINTS[a][0]
                for a in registry.ROSTER
                if not registry.is_configured(a, settings)
            )
            self.notice.setText(
                "Нужно как минимум два ключа. Нажмите «Настройки» и добавьте их — "
                f"не заданы: {missing}"
            )
            self.notice.setVisible(True)
        return None

    def _on_ask(self) -> None:
        if self.controller.busy:
            return
        text = self.question.toPlainText().strip()
        if not text:
            return

        self.error.setVisible(False)
        self.answer_frame.setVisible(False)
        self.failed_note.setVisible(False)
        for section in (
            self.section_rounds,
            self.section_disagreements,
            self.section_tokens,
        ):
            section.collapse()

        planned = self.controller.build_config()
        agents = [a.value for a in planned.agents]
        self._roles = dict(planned.roles)
        self._max_rounds = planned.max_rounds
        self.strip.set_agents(agents, self._roles)
        self.strip.reset("IDLE")
        self.status_frame.setVisible(True)
        self.progress.setValue(0)
        self.phase_label.setText("Запуск…")

        if self.controller.start(text) is None:
            return

        self.ask_button.setEnabled(False)
        self.stop_button.setVisible(True)
        self.question.setReadOnly(True)

    def _on_settings(self) -> None:
        dialog = SettingsDialog(self, list_models=self.controller.list_models)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.save()
            self._refresh_availability()

    def _on_history(self) -> None:
        items = self.controller.recent_debates(50)
        dialog = HistoryDialog(items, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.clear_requested:
            self.controller.clear_history()
            self.answer_frame.setVisible(False)
            self.status_frame.setVisible(False)
            return
        if dialog.selected_id:
            detail = self.controller.load_detail(dialog.selected_id)
            if detail is not None:
                self.question.setPlainText(detail.question)
                self._show_detail(detail, replay=True)

    def _on_copy(self) -> None:
        if self._detail is None:
            return
        # Если ответа нет, копируем разбор ошибок — его как раз и хочется
        # куда-нибудь переслать.
        text = (
            self._detail.synthesis.consensus
            if self._detail.synthesis
            else report.failure_summary(self._detail)
        )
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.phase_label.setText("Скопировано в буфер обмена")

    def _on_save(self) -> None:
        if self._detail is None:
            return
        from backend.api.service import export_markdown

        path, selected = QFileDialog.getSaveFileName(
            self,
            "Сохранить отчёт",
            str(Path.home() / f"ai-council-{self._detail.id}.md"),
            "Markdown (*.md);;JSON (*.json)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() == ".json" or "JSON" in selected:
            target.write_text(
                json.dumps(self._detail.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            target.write_text(export_markdown(self._detail), encoding="utf-8")
        self.phase_label.setText(f"Сохранено: {target}")

    # ----------------------------------------------------------------- события
    def _on_event(self, event_type: str, data: dict) -> None:
        if event_type == "debate_started":
            agents = list(data.get("agents") or [])
            self._roles = dict(data.get("roles") or {})
            self.strip.set_agents(agents, self._roles)
            config = data.get("config") or {}
            self._max_rounds = int(config.get("max_rounds") or self._max_rounds or 1)
            self.phase_label.setText("Раунд 0 — независимые ответы")

        elif event_type == "round_started":
            rnd = int(data.get("round", 0))
            self.strip.reset("IDLE")
            self.phase_label.setText(
                "Раунд 0 — независимые ответы"
                if rnd == 0
                else f"Раунд {rnd} из максимум {self._max_rounds} — обсуждение"
            )
            total = max(1, self._max_rounds + 1)
            self.progress.setValue(min(96, int(rnd / total * 100)))

        elif event_type == "agent_started":
            self.strip.set_state(str(data.get("agent")), "THINKING")

        elif event_type == "agent_finished":
            latency = int(data.get("latency_ms") or 0)
            self.strip.set_state(
                str(data.get("agent")), "OK", f"{latency} мс"
            )

        elif event_type == "agent_failed":
            agent = str(data.get("agent"))
            reason = str(data.get("reason") or "неизвестная ошибка")
            d = explain(agent, data.get("error_kind"), reason)
            self.strip.set_state(
                agent,
                str(data.get("status") or "ERROR"),
                d.title.lower(),
                f"{agent}: {d.title}\n{d.action}\n\nОтвет провайдера: {reason}",
            )

        elif event_type == "consensus_check":
            score = round(float(data.get("score") or 0) * 100)
            reached = "достигнут" if data.get("reached") else "пока нет"
            self.phase_label.setText(f"Проверка согласия: {score}% — консенсус {reached}")

        elif event_type == "synthesis_started":
            self.phase_label.setText("Сводим итог…")
            self.progress.setValue(97)

        elif event_type == "debate_error":
            self._on_failed(str(data.get("error") or "неизвестная ошибка"))

    def _on_finished(self, detail: DebateDetail) -> None:
        self._show_detail(detail)

    def _on_failed(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
        self.phase_label.setText("Остановлено")
        self.progress.setValue(0)
        self._reset_controls()

    def _reset_controls(self) -> None:
        self.stop_button.setVisible(False)
        self.question.setReadOnly(False)
        self._refresh_availability()

    def _show_detail(self, detail: DebateDetail, replay: bool = False) -> None:
        self._detail = detail
        self._max_rounds = detail.max_rounds or self._max_rounds

        if replay:
            agents = sorted(detail.rounds[0].outcomes) if detail.rounds else []
            self._roles = dict(detail.config.roles) if detail.config else {}
            self.strip.set_agents(agents, self._roles)
            for rnd in detail.rounds:
                for agent, outcome in rnd.outcomes.items():
                    note = f"{outcome.latency_ms} мс" if outcome.ok else ""
                    self.strip.set_state(
                        agent,
                        outcome.status.value,
                        note,
                        outcome.error or agent,
                    )
            self.status_frame.setVisible(True)

        self.progress.setValue(100)
        if detail.synthesis is None:
            self.phase_label.setText("Обсуждение не состоялось")
        elif detail.consensus_reached:
            self.phase_label.setText("Готово — консенсус достигнут")
        else:
            self.phase_label.setText("Готово — полного согласия нет")

        synthesis = detail.synthesis
        broken = report.failed_agents(detail)

        if synthesis is not None:
            self.answer.set_markdown(synthesis.consensus)
            self.summary.setText(report.summary_line(detail))
            self.summary.setVisible(True)
            if broken:
                self.failed_note.setText(
                    "Не ответили в последнем раунде: "
                    + ", ".join(broken)
                    + ". Причина — внутри «Показать ход обсуждения»."
                )
                self.failed_note.setVisible(True)
            else:
                self.failed_note.setVisible(False)
        else:
            # Ответа нет — вместо пустой заглушки показываем, что именно
            # сломалось у каждого провайдера и что с этим делать.
            summary = report.failure_summary(detail)
            self.answer.set_markdown(
                summary or "_Итоговый ответ не сформирован._"
            )
            self.summary.setVisible(False)
            self.failed_note.setVisible(False)

        rounds_count = max(0, len(detail.rounds) - 1)
        word = report.plural(rounds_count, "раунд", "раунда", "раундов")
        self.section_rounds.set_title(
            f"Показать ход обсуждения  ({rounds_count} {word})"
            if rounds_count
            else "Показать, что ответил каждый провайдер"
        )
        self.rounds_view.set_markdown(report.rounds_markdown(detail))

        # Пустые блоки не показываем: подпись без содержимого — шум.
        minority = len(synthesis.minority_positions) if synthesis else 0
        self.section_disagreements.set_title(
            "Расхождения и мнение меньшинства"
            + (f"  ({minority})" if minority else "")
        )
        self.disagreements_view.set_markdown(report.disagreements_markdown(detail))
        self.section_disagreements.setVisible(synthesis is not None)

        total = detail.cost.total_tokens if detail.cost else 0
        token_word = report.plural(total, "токен", "токена", "токенов")
        self.section_tokens.set_title(
            f"Расход токенов и стоимость  ({total:,} {token_word})".replace(",", " ")
        )
        self.tokens_view.set_markdown(report.tokens_markdown(detail))
        self.section_tokens.setVisible(total > 0)

        self.answer_frame.setVisible(True)
        self.error.setVisible(False)
        self._reset_controls()

    # ------------------------------------------------------------------ выход
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.controller.shutdown()
        super().closeEvent(event)


def build_window() -> MainWindow:
    window = MainWindow()
    window.setStyleSheet(theme.STYLESHEET)
    return window
