"""Мост между движком дебатов (asyncio) и интерфейсом (Qt).

Оркестратор целиком асинхронный, Qt — синхронный и однопоточный. Поэтому
приложение держит один рабочий поток с постоянным циклом asyncio на всё время
жизни программы, а результаты возвращает в интерфейс через сигналы Qt:
испускать сигнал из чужого потока безопасно — Qt сам ставит его в очередь
потока-получателя.

HTTP здесь нет вообще: `DebateOrchestrator` вызывается напрямую.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from backend.agents import registry
from backend.agents.roles import assign_roles
from backend.config import REPO_ROOT, Settings, get_settings
from backend.debate.orchestrator import DebateOrchestrator, new_id
from backend.models import db as dbm
from backend.models.enums import AgentName, EventType
from backend.models.schemas import DebateConfig, DebateDetail
from backend.services.events import Event, EventBus
from backend.services.logging import get_logger, setup_logging
from backend.services.storage import DebateRepository

log = get_logger(__name__)


def resolve_database_url(settings: Settings) -> str:
    """Привязывает относительный путь SQLite к корню проекта.

    У десктопного приложения рабочий каталог зависит от того, откуда его
    запустили (ярлык, меню «Пуск», позже — .exe). Без этого база создавалась
    бы каждый раз в новом месте.
    """
    url = settings.database_url
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return url
    raw = url[len(prefix) :]
    if raw.startswith(":memory:") or Path(raw).is_absolute():
        return url
    return prefix + (REPO_ROOT / raw.lstrip("./")).as_posix()


class AsyncWorker:
    """Постоянный поток с собственным циклом asyncio."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="ai-council-async", daemon=True
        )
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def submit(self, coro: Any) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self) -> None:
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


class QtEventBus(EventBus):
    """EventBus, который дополнительно отдаёт каждое событие в интерфейс."""

    def __init__(self, sink: Any) -> None:
        super().__init__()
        self._sink = sink

    def emit(
        self, debate_id: str, event_type: EventType | str, data: dict[str, Any] | None = None
    ) -> Event:
        event = super().emit(debate_id, event_type, data)
        try:
            self._sink(event)
        except Exception:  # noqa: BLE001 - интерфейс не должен ломать дебат
            log.exception("Не удалось передать событие в интерфейс")
        return event


class DebateController(QObject):
    """Единственная точка, через которую окно общается с движком."""

    event = Signal(str, dict)        # тип события, данные
    finished = Signal(object)        # DebateDetail
    failed = Signal(str)
    ready = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        settings = get_settings()
        setup_logging(settings.log_level)

        self._worker = AsyncWorker()
        self._repo: DebateRepository | None = None
        self._bus = QtEventBus(self._on_engine_event)
        self._current: concurrent.futures.Future | None = None
        self._orchestrator: DebateOrchestrator | None = None
        self._debate_id: str | None = None
        self._closed = False

        self._worker.submit(self._init_db()).add_done_callback(
            lambda f: self.ready.emit() if not f.exception() else None
        )

    # ------------------------------------------------------------------ базa
    async def _init_db(self) -> None:
        settings = get_settings()
        sessionmaker = dbm.init_engine(resolve_database_url(settings))
        await dbm.create_all()
        self._repo = DebateRepository(sessionmaker)

    # --------------------------------------------------------------- события
    def _on_engine_event(self, event: Event) -> None:
        # Вызывается из потока asyncio; Qt поставит сигнал в очередь главного потока.
        self.event.emit(event.type, event.data)

    # ----------------------------------------------------------------- запуск
    @property
    def busy(self) -> bool:
        return self._current is not None and not self._current.done()

    @property
    def debate_id(self) -> str | None:
        return self._debate_id

    def available_agents(self) -> list[AgentName]:
        return registry.configured_agents(get_settings())

    def build_config(self, agents: list[AgentName] | None = None) -> DebateConfig:
        s = get_settings()
        chosen = agents or self.available_agents()
        return DebateConfig(
            agents=chosen,
            min_rounds=max(0, min(20, s.min_rounds)),
            max_rounds=max(1, min(20, s.max_rounds)),
            consensus_threshold=max(0.0, min(1.0, s.consensus_threshold)),
            timeout=max(5, min(600, s.request_timeout)),
            temperature=max(0.0, min(2.0, s.temperature)),
            max_retries=max(0, min(5, s.max_retries)),
            models={a.value: registry.default_model_for(a, s) for a in chosen},
            roles=assign_roles(chosen, s.advocate_agent) if s.debate_roles else {},
        )

    def start(self, question: str) -> str | None:
        if self.busy:
            return None
        if self._repo is None:
            # Никогда не проваливаемся молча: пользователь должен понять, почему
            # ничего не произошло.
            self.failed.emit(
                "Хранилище ещё открывается. Попробуйте ещё раз через секунду."
            )
            return None

        settings = get_settings()
        agents = self.available_agents()
        if len(agents) < 2:
            self.failed.emit(
                "Нужно как минимум два провайдера с ключом. Откройте «Настройки» "
                "и добавьте ключи."
            )
            return None

        question = question.strip()
        if not question:
            self.failed.emit("Вопрос не может быть пустым.")
            return None
        if len(question) > settings.max_question_length:
            self.failed.emit(
                f"Вопрос слишком длинный: {len(question)} символов при лимите "
                f"{settings.max_question_length}."
            )
            return None

        config = self.build_config(agents)
        debate_id = new_id("dbt")
        self._debate_id = debate_id
        self._current = self._worker.submit(self._run(debate_id, question, config))
        return debate_id

    async def _run(self, debate_id: str, question: str, config: DebateConfig) -> None:
        assert self._repo is not None
        settings = get_settings()
        try:
            await self._repo.create_debate(debate_id, new_id("cnv"), question, config)
            council = registry.build_council(
                config.agents,
                models=config.models,
                temperature=config.temperature,
                timeout=config.timeout,
                max_retries=config.max_retries,
                settings=settings,
            )
            self._orchestrator = DebateOrchestrator(
                debate_id=debate_id,
                question=question,
                config=config,
                council=council,
                repo=self._repo,
                bus=self._bus,
                settings=settings,
            )
            try:
                detail: DebateDetail = await self._orchestrator.run()
            finally:
                await self._orchestrator.aclose()
                self._orchestrator = None
            self.finished.emit(detail)
        except asyncio.CancelledError:
            self.failed.emit("Обсуждение остановлено.")
        except Exception as exc:  # noqa: BLE001 - показываем пользователю
            log.exception("Дебат %s завершился ошибкой", debate_id)
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def cancel(self) -> None:
        if self._current is not None and not self._current.done():
            self._worker.loop.call_soon_threadsafe(self._current.cancel)

    # ------------------------------------------------------------------ чтение
    def load_detail(self, debate_id: str) -> DebateDetail | None:
        if self._repo is None:
            return None
        from backend.api.service import DebateService

        service = DebateService(self._repo, self._bus, get_settings())
        return self._worker.submit(service.get_detail(debate_id)).result(timeout=30)

    def recent_debates(self, limit: int = 20) -> list[Any]:
        if self._repo is None:
            return []
        from backend.api.service import DebateService

        service = DebateService(self._repo, self._bus, get_settings())
        return self._worker.submit(service.list_debates(limit)).result(timeout=30)

    def list_models(self, agent: AgentName, timeout: float = 30.0) -> list[str]:
        """Модели, доступные ключу. Пустой список, если получить не удалось."""
        try:
            return self._worker.submit(
                registry.list_models_for(agent, get_settings())
            ).result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - показываем пустой список
            log.info("Не удалось получить список моделей %s: %s", agent.value, exc)
            return []

    def clear_history(self) -> int:
        if self._repo is None:
            return 0
        return self._worker.submit(self._repo.clear_all()).result(timeout=30)

    def shutdown(self) -> None:
        # Закрытие приходит и из closeEvent окна, и из явного вызова —
        # второй раз должен быть безвредным.
        if self._closed:
            return
        self._closed = True
        self.cancel()
        coro = dbm.dispose_engine()
        try:
            self._worker.submit(coro).result(timeout=5)
        except Exception:  # noqa: BLE001 - цикл мог уже остановиться
            coro.close()   # иначе Python ругается на неожиданную корутину
        self._worker.shutdown()
