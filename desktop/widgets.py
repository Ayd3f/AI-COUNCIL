"""Мелкие виджеты: индикаторы агентов и раскрывающиеся блоки."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme


def _role_labels(role: str) -> tuple[str, str]:
    """(короткая подпись, подсказка) для роли. Пустая роль — пустые строки."""
    if not role:
        return "", ""
    try:
        from backend.agents.roles import spec

        s = spec(role)
        return s.label_ru, f"{s.label_ru} — {s.short_ru}"
    except (KeyError, ValueError):
        return role, role


class Dot(QLabel):
    """Цветной кружок статуса."""

    def __init__(self, color: str, size: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"background: {color}; border-radius: {self._size // 2}px; border: none;"
        )


class AgentIndicator(QFrame):
    """Одна карточка агента: кружок, имя, короткий статус."""

    def __init__(
        self, agent: str, role: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(7)
        self.dot = Dot(theme.STATUS_COLORS["IDLE"])
        head.addWidget(self.dot)
        name = QLabel(agent)
        name.setStyleSheet(
            f"color: {theme.AGENT_COLORS.get(agent, theme.TEXT)};"
            "font-weight: 700; font-size: 11.5px; letter-spacing: 1px;"
        )
        head.addWidget(name)
        head.addStretch(1)
        layout.addLayout(head)

        # Роль видно сразу: иначе непонятно, почему один агент спорит,
        # а другой требует доказательств.
        self.role = QLabel(role)
        self.role.setVisible(bool(role))
        self.role.setStyleSheet(
            f"color: {theme.ACCENT_2}; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self.role)

        self.status = QLabel(theme.STATUS_TEXT["IDLE"])
        self.status.setObjectName("Faint")
        layout.addWidget(self.status)

        self._tooltip_base = f"{agent} · {role}" if role else agent

    def set_role(self, label: str, tooltip: str = "") -> None:
        self.role.setText(label)
        self.role.setVisible(bool(label))
        self._tooltip_base = f"{self.agent} · {label}" if label else self.agent
        if tooltip:
            self.setToolTip(tooltip)

    def set_state(self, status: str, note: str = "", tooltip: str = "") -> None:
        self.dot.set_color(theme.STATUS_COLORS.get(status, theme.TEXT_FAINT))
        text = theme.STATUS_TEXT.get(status, status.lower())
        if note:
            text = f"{text} · {note}"
        self.status.setText(text)
        colour = theme.ERR if status in {"ERROR", "INVALID_OUTPUT"} else theme.TEXT_FAINT
        self.status.setStyleSheet(f"color: {colour}; font-size: 12px;")
        self.setToolTip(tooltip or self._tooltip_base)


class AgentStrip(QWidget):
    """Полоса из индикаторов всех участников."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(9)
        self._indicators: dict[str, AgentIndicator] = {}

    def set_agents(self, agents: list[str], roles: dict[str, str] | None = None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._indicators.clear()
        roles = roles or {}
        for agent in agents:
            label, tip = _role_labels(roles.get(agent, ""))
            ind = AgentIndicator(agent, label)
            if tip:
                ind.setToolTip(f"{agent} · {tip}")
            self._indicators[agent] = ind
            self._layout.addWidget(ind, 1)

    def set_state(self, agent: str, status: str, note: str = "", tooltip: str = "") -> None:
        ind = self._indicators.get(agent)
        if ind is not None:
            ind.set_state(status, note, tooltip)

    def reset(self, status: str = "IDLE") -> None:
        for ind in self._indicators.values():
            ind.set_state(status)


class MarkdownView(QTextBrowser):
    """Только для чтения, растёт по содержимому — без вложенной прокрутки."""

    def __init__(self, object_name: str = "Details", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def _fit(self) -> None:
        # Высоту нужно мерить по фактической ширине области просмотра, иначе
        # текст переносится не так, как посчитано, и вылезает за карточку.
        doc = self.document()
        width = max(1, self.viewport().width())
        if abs(doc.textWidth() - width) > 1:
            doc.setTextWidth(width)
        height = int(doc.size().height()) + self.frameWidth() * 2 + 14
        height = max(24, height)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit()

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(text or "")
        self._fit()


class CollapsibleSection(QWidget):
    """Заголовок-кнопка со стрелкой и скрытым содержимым.

    Подробности спрятаны, но подпись всегда видна и говорит словами, что
    именно внутри.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.button = QToolButton()
        self.button.setObjectName("Disclosure")
        self.button.setCheckable(True)
        self.button.setChecked(False)
        self.button.setArrowType(Qt.ArrowType.RightArrow)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setText(title)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.toggled.connect(self._on_toggled)
        layout.addWidget(self.button)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(16, 4, 0, 10)
        self.content_layout.setSpacing(8)
        self.content.setVisible(False)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool) -> None:
        self.button.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(checked)

    def set_title(self, title: str) -> None:
        self._title = title
        self.button.setText(title)

    def add(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def clear(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def collapse(self) -> None:
        self.button.setChecked(False)


def card(*, spacing: int = 12, margins: tuple[int, int, int, int] = (18, 16, 18, 16)):
    """Готовая панель-карточка с вертикальной раскладкой."""
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label
