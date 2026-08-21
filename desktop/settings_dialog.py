"""Окно «Настройки»: ключи, модели, правила обсуждения.

Ключи вводятся здесь и сохраняются в локальный `.env` рядом с программой.
Они не покидают машину — уходят только в API соответствующего провайдера.

Список провайдеров строится из реестра агентов, а не дублируется здесь:
добавили провайдера в `backend/agents/registry.py` — он появился в окне.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from backend.agents import registry
from backend.config import get_settings
from backend.models.enums import AgentName

from . import env_file, theme

ListModels = Callable[[AgentName], list[str]]


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        list_models: ListModels | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        self.setMinimumWidth(720)
        self._list_models = list_models

        current = env_file.read_env()
        settings = get_settings()

        self.key_fields: dict[str, QLineEdit] = {}
        self.model_fields: dict[str, QComboBox] = {}
        #: ключи, заданные переменной окружения, а не файлом .env — их нельзя
        #: затирать пустым значением при сохранении
        self._external_keys: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._keys_tab(current, settings), "Ключи")
        tabs.addTab(self._models_tab(current, settings), "Модели")
        tabs.addTab(self._debate_tab(settings), "Обсуждение")
        root.addWidget(tabs)

        note = QLabel(
            "Ключи сохраняются в файл .env на этом компьютере и никуда больше не "
            "передаются. Файл исключён из git."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ вкладки
    def _keys_tab(self, current: dict[str, str], settings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        intro = QLabel(
            "Достаточно двух рабочих провайдеров. Провайдер без ключа просто не "
            "участвует. Отмеченные «бесплатно» не требуют карты при регистрации."
        )
        intro.setObjectName("Dim")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for agent in registry.ROSTER:
            key_env, _model_env = registry.ENV_HINTS[agent]
            url = registry.SIGNUP_URLS.get(agent, "")
            is_local = agent is AgentName.LOCAL
            in_file = current.get(key_env, "")
            configured = registry.is_configured(agent, settings)
            external = not in_file and configured

            row = QHBoxLayout()
            row.setSpacing(6)

            field = QLineEdit(in_file)
            if is_local:
                # У локального сервера вместо ключа — адрес, прятать нечего.
                field.setPlaceholderText("http://localhost:11434/v1 — пусто = выключен")
            else:
                field.setEchoMode(QLineEdit.EchoMode.Password)
                if external:
                    self._external_keys.add(key_env)
                    field.setPlaceholderText("задан переменной окружения — оставьте пустым")
                else:
                    field.setPlaceholderText("не задан")
            self.key_fields[key_env] = field
            row.addWidget(field, 1)

            if not is_local:
                eye = QPushButton("Показать")
                eye.setCheckable(True)
                eye.setToolTip("Показать или скрыть ключ")

                def _toggle(shown: bool, f=field, b=None) -> None:
                    f.setEchoMode(
                        QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
                    )
                    if b is not None:
                        b.setText("Скрыть" if shown else "Показать")

                eye.toggled.connect(lambda shown, f=field, b=eye: _toggle(shown, f, b))
                row.addWidget(eye)

            if url:
                link = QPushButton("Установить" if is_local else "Где взять")
                link.setToolTip(url)
                link.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(u))
                row.addWidget(link)

            holder = QWidget()
            holder.setLayout(row)

            colour = theme.OK if configured else theme.TEXT_FAINT
            suffix = "  ·  бесплатно" if agent in registry.FREE_TIER else ""
            label = QLabel(f"{agent.value}{suffix}")
            label.setStyleSheet(f"color: {colour}; font-weight: 600;")
            label.setToolTip(
                registry.AGENT_CLASSES[agent].provider
                + (" · есть бесплатный тариф" if agent in registry.FREE_TIER else " · платный")
            )
            form.addRow(label, holder)

        layout.addLayout(form)

        warn = QLabel(
            "Не ставьте одну и ту же открытую модель двум участникам: в совете "
            "окажется один голос дважды, и согласие будет фальшивым."
        )
        warn.setObjectName("Notice")
        warn.setWordWrap(True)
        layout.addWidget(warn)
        layout.addStretch(1)
        return page

    def _models_tab(self, current: dict[str, str], settings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        intro = QLabel(
            "Имена моделей у провайдеров меняются часто. «Проверить» спросит у "
            "провайдера, что доступно именно вашему ключу, и подставит список."
        )
        intro.setObjectName("Dim")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        for agent in registry.ROSTER:
            _key_env, model_env = registry.ENV_HINTS[agent]
            row = QHBoxLayout()
            row.setSpacing(6)

            box = QComboBox()
            box.setEditable(True)
            box.setCurrentText(
                current.get(model_env) or registry.default_model_for(agent, settings)
            )
            self.model_fields[model_env] = box
            row.addWidget(box, 1)

            check = QPushButton("Проверить")
            check.setToolTip("Запросить у провайдера список доступных моделей")
            check.clicked.connect(
                lambda _=False, a=agent, b=box, btn=check: self._refresh_models(a, b, btn)
            )
            check.setEnabled(self._list_models is not None)
            row.addWidget(check)

            holder = QWidget()
            holder.setLayout(row)
            form.addRow(QLabel(agent.value), holder)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _refresh_models(self, agent: AgentName, box: QComboBox, button: QPushButton) -> None:
        if self._list_models is None:
            return
        chosen = box.currentText()
        button.setEnabled(False)
        button.setText("…")
        try:
            names = self._list_models(agent)
        finally:
            button.setEnabled(True)
        if not names:
            button.setText("Недоступно")
            button.setToolTip(
                "Провайдер не отдал список: проверьте ключ, баланс и сеть."
            )
            return
        box.clear()
        box.addItems(names)
        # Возвращаем прежний выбор, если он ещё существует.
        box.setCurrentText(chosen if chosen in names else names[0])
        button.setText(f"{len(names)} шт.")

    def _debate_tab(self, settings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(9)

        self.min_rounds = QSpinBox()
        self.min_rounds.setRange(0, 20)
        self.min_rounds.setValue(settings.min_rounds)
        self.min_rounds.setToolTip(
            "Раньше этого числа раундов консенсус не объявляется никогда."
        )
        form.addRow("Минимум раундов", self.min_rounds)

        self.max_rounds = QSpinBox()
        self.max_rounds.setRange(1, 20)
        self.max_rounds.setValue(settings.max_rounds)
        self.max_rounds.setToolTip("Жёсткий предел: больше этого числа не будет.")
        form.addRow("Максимум раундов", self.max_rounds)

        self.threshold = QSpinBox()
        self.threshold.setRange(50, 100)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(round(settings.consensus_threshold * 100))
        self.threshold.setToolTip("Порог взвешенного согласия.")
        form.addRow("Порог консенсуса", self.threshold)

        self.timeout = QSpinBox()
        self.timeout.setRange(5, 600)
        self.timeout.setSuffix(" с")
        self.timeout.setValue(settings.request_timeout)
        form.addRow("Таймаут запроса", self.timeout)

        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.retries.setValue(settings.max_retries)
        self.retries.setToolTip("Дополнительные попытки при временных сбоях.")
        form.addRow("Повторов при сбое", self.retries)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setValue(settings.temperature)
        self.temperature.setToolTip(
            "Отправляется только тем провайдерам, которые её принимают."
        )
        form.addRow("Температура", self.temperature)

        layout.addLayout(form)

        self.thinking = QCheckBox("Claude сначала рассуждает (дороже, но точнее)")
        self.thinking.setChecked(settings.anthropic_thinking == "adaptive")
        layout.addWidget(self.thinking)

        hint = QLabel(
            "Стоимость раунда: два запроса на агента. Пять агентов и три раунда — "
            "примерно 36 запросов и около 40 тысяч токенов. Хотите дешевле — "
            "уменьшайте максимум раундов."
        )
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    # -------------------------------------------------------------- сохранение
    def collect(self) -> dict[str, str]:
        updates: dict[str, str] = {}
        for key_env, field in self.key_fields.items():
            value = field.text().strip()
            if not value and key_env in self._external_keys:
                continue  # ключ живёт в окружении — не затираем его пустотой
            updates[key_env] = value
        for model_env, box in self.model_fields.items():
            updates[model_env] = box.currentText().strip()

        min_rounds = self.min_rounds.value()
        max_rounds = self.max_rounds.value()
        if min_rounds > max_rounds:
            min_rounds = max_rounds

        updates["MIN_ROUNDS"] = str(min_rounds)
        updates["MAX_ROUNDS"] = str(max_rounds)
        updates["CONSENSUS_THRESHOLD"] = f"{self.threshold.value() / 100:.2f}"
        updates["REQUEST_TIMEOUT"] = str(self.timeout.value())
        updates["MAX_RETRIES"] = str(self.retries.value())
        updates["TEMPERATURE"] = f"{self.temperature.value():.2f}"
        updates["ANTHROPIC_THINKING"] = "adaptive" if self.thinking.isChecked() else "disabled"
        return updates

    def save(self) -> None:
        updates = self.collect()
        env_file.write_env(updates)
        env_file.apply_to_process(updates)
