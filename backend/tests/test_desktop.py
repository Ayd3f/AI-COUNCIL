"""Логика десктопного приложения, не требующая запуска Qt.

Виджеты здесь не создаются — проверяется то, что можно сломать молча:
разбор и запись `.env`, привязка пути к базе и сборка текстов подробностей.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="десктопная часть требует PySide6")

from ..config import Settings  # noqa: E402
from ..models.enums import AgentName, AgentStatus, RoundKind  # noqa: E402
from ..models.schemas import (  # noqa: E402
    AgentOutcome,
    ConsensusReport,
    DebateDetail,
    MinorityPosition,
    RoundResult,
    SynthesisResult,
    TokenUsage,
)
from desktop import env_file, report  # noqa: E402
from desktop.controller import resolve_database_url  # noqa: E402


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def test_read_env_ignores_comments_and_quotes(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "# комментарий\n"
        'OPENAI_API_KEY="sk-test"\n'
        "MAX_ROUNDS=4\n"
        "# OPENAI_MODEL=закомментировано\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    values = env_file.read_env(path)
    assert values["OPENAI_API_KEY"] == "sk-test"
    assert values["MAX_ROUNDS"] == "4"
    assert values["EMPTY"] == ""
    assert "OPENAI_MODEL" not in values


def test_write_env_preserves_comments_and_updates_in_place(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "# ключи\nOPENAI_API_KEY=old\n\n# правила\nMAX_ROUNDS=5\n",
        encoding="utf-8",
    )
    env_file.write_env({"OPENAI_API_KEY": "new", "MIN_ROUNDS": "2"}, path)

    text = path.read_text(encoding="utf-8")
    assert "# ключи" in text
    assert "# правила" in text
    assert "OPENAI_API_KEY=new" in text
    assert "OPENAI_API_KEY=old" not in text
    assert "MAX_ROUNDS=5" in text          # не тронуто
    assert "MIN_ROUNDS=2" in text          # дописано

    again = env_file.read_env(path)
    assert again["OPENAI_API_KEY"] == "new"
    assert again["MIN_ROUNDS"] == "2"


def test_write_env_does_not_duplicate_keys_on_repeated_saves(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("MAX_ROUNDS=1\n", encoding="utf-8")
    for value in ("2", "3", "4"):
        env_file.write_env({"MAX_ROUNDS": value}, path)
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if "MAX_ROUNDS" in l]
    assert lines == ["MAX_ROUNDS=4"]


# --------------------------------------------------------------------------- #
# путь к базе
# --------------------------------------------------------------------------- #


def test_relative_sqlite_path_is_anchored_to_the_project(tmp_path: Path):
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite:///./ai_council.db")  # type: ignore[call-arg]
    resolved = resolve_database_url(s)
    assert resolved.endswith("/ai_council.db")
    assert "./" not in resolved.split(":///")[1]


def test_absolute_and_non_sqlite_urls_are_left_alone():
    absolute = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="sqlite+aiosqlite:///D:/data/x.db"
    )
    assert resolve_database_url(absolute) == "sqlite+aiosqlite:///D:/data/x.db"

    postgres = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql+asyncpg://u:p@host/db"
    )
    assert resolve_database_url(postgres) == "postgresql+asyncpg://u:p@host/db"


# --------------------------------------------------------------------------- #
# тексты подробностей
# --------------------------------------------------------------------------- #


def _detail() -> DebateDetail:
    ok = AgentOutcome(
        agent=AgentName.OPENAI,
        status=AgentStatus.OK,
        model="gpt-4o",
        payload={
            "position": "Монолит.",
            "accepted_arguments": [
                {"agent": "CLAUDE", "argument": "PCI-периметр", "reason": "проверяемо"}
            ],
            "rejected_arguments": [],
            "uncertain_arguments": [],
            "changed_my_position": False,
            "why_changed": "нет новых данных",
            "confidence": 0.9,
        },
        latency_ms=1200,
        usage=TokenUsage(input_tokens=100, output_tokens=50),
    )
    broken = AgentOutcome(
        agent=AgentName.GEMINI,
        status=AgentStatus.ERROR,
        model="gemini-2.5-flash",
        error="API timeout",
        error_kind="timeout",
        attempts=3,
    )
    consensus = ConsensusReport(
        round_index=1,
        reached=False,
        score=0.72,
        threshold=0.8,
        agree_count=1,
        participant_count=2,
        rule="not_reached",
        reason="material objection from CLAUDE",
        agreement_by_agent={"OPENAI": 0.9, "CLAUDE": 0.35},
        agreeing_agents=["OPENAI"],
        dissenting_agents=["CLAUDE"],
        unsubstantiated_agents=["GROK"],
        material_objections=[
            {"agent": "CLAUDE", "objection": "PCI", "evidence": "PCI DSS 4.0"}
        ],
    )
    return DebateDetail(
        id="dbt_x",
        question="Монолит или микросервисы?",
        status="COMPLETED",  # type: ignore[arg-type]
        created_at="2026-01-01T00:00:00Z",
        rounds_used=1,
        max_rounds=3,
        consensus_reached=False,
        consensus_score=0.72,
        rounds=[
            RoundResult(
                index=1,
                kind=RoundKind.DEBATE,
                outcomes={"OPENAI": ok, "GEMINI": broken},
                consensus=consensus,
            )
        ],
        synthesis=SynthesisResult(
            consensus="Модульный монолит.",
            disagreements=["Границы PCI"],
            minority_positions=[
                MinorityPosition(
                    agent="CLAUDE", position="Выносить сразу", evidence="PCI DSS 4.0"
                )
            ],
        ),
    )


def test_rounds_markdown_shows_failures_with_their_reason():
    text = report.rounds_markdown(_detail())
    assert "### GEMINI" in text
    assert "API timeout" in text
    assert "timeout" in text
    assert "попыток: 3" in text
    assert "Принял" in text and "CLAUDE" in text
    assert "Сохранил позицию" in text
    assert "Проверка консенсуса: не достигнут" in text
    assert "Необоснованное согласие отброшено у: GROK" in text


def test_disagreements_markdown_keeps_the_minority():
    text = report.disagreements_markdown(_detail())
    assert "Мнение меньшинства" in text
    assert "CLAUDE" in text
    assert "PCI DSS 4.0" in text
    assert "Существенные возражения" in text
    assert "Кто с чем согласился" in text


def test_summary_line_names_the_dissenter():
    line = report.summary_line(_detail())
    assert "72%" in line
    assert "1 из 2" in line
    assert "CLAUDE" in line
    assert "консенсус не достигнут" in line


def test_failed_agents_excludes_disabled_providers():
    detail = _detail()
    detail.rounds[0].outcomes["GROK"] = AgentOutcome(
        agent=AgentName.GROK, status=AgentStatus.DISABLED, model=""
    )
    assert report.failed_agents(detail) == ["GEMINI"]


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "раундов"),
        (1, "раунд"),
        (2, "раунда"),
        (4, "раунда"),
        (5, "раундов"),
        (11, "раундов"),
        (12, "раундов"),
        (21, "раунд"),
        (22, "раунда"),
        (25, "раундов"),
        (101, "раунд"),
        (111, "раундов"),
    ],
)
def test_russian_plural(n: int, expected: str):
    assert report.plural(n, "раунд", "раунда", "раундов") == expected


def test_failure_summary_tells_the_user_what_to_do():
    detail = _detail()
    detail.synthesis = None
    detail.rounds[0].outcomes["DEEPSEEK"] = AgentOutcome(
        agent=AgentName.DEEPSEEK,
        status=AgentStatus.ERROR,
        model="deepseek-v4-flash",
        error="Error code: 402 - {'message': 'Insufficient Balance'}",
        error_kind="no_credits",
    )
    text = report.failure_summary(detail)
    assert "Обсуждение не состоялось" in text
    assert "DEEPSEEK" in text and "баланс" in text.lower()
    assert "GEMINI" in text          # таймаут-провайдер из фикстуры
    assert "OPENAI" not in text      # он ответил успешно — его тут быть не должно
    assert "и с двумя участниками" in text


def test_failure_summary_is_empty_when_everyone_answered():
    detail = _detail()
    detail.rounds[0].outcomes.pop("GEMINI")
    assert report.failure_summary(detail) == ""


def test_tokens_markdown_never_prices_an_unknown_model_at_zero():
    detail = _detail()
    from ..models.schemas import CostLine, CostReport

    detail.cost = CostReport(
        lines=[
            CostLine(
                agent="OPENAI",
                model="mystery-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                pricing_known=False,
            )
        ],
        total_tokens=15,
        estimated_cost_usd=0.0,
        complete=False,
        models_without_pricing=["mystery-model"],
    )
    text = report.tokens_markdown(detail)
    assert "не задана" in text
    assert "mystery-model" in text
    assert "никогда не считаются бесплатными" in text
