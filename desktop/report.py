"""Сборка текстов для скрытых блоков «подробности».

Данные берутся из того же `DebateDetail`, что отдаёт веб-версия, поэтому
десктоп и сайт всегда показывают одно и то же.
"""

from __future__ import annotations

from backend.agents.roles import spec as role_spec
from backend.models.schemas import DebateDetail, RoundResult
from backend.services.diagnostics import explain

ARROW = "↳"


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 раунд, 2 раунда, 5 раундов, 11 раундов."""
    n = abs(n)
    if n % 100 in range(11, 15):
        return many
    last = n % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def _args_block(payload: dict, key: str, title: str, mark: str) -> list[str]:
    items = payload.get(key) or []
    if not items:
        return []
    out = [f"**{title}:**", ""]
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            f"- {mark} **{it.get('agent', '?')}** — {it.get('argument', '')}  "
        )
        out.append(f"  {ARROW} {it.get('reason', '')}")
    out.append("")
    return out


def _persuasion_block(payload: dict) -> list[str]:
    """Адресные попытки переубедить — то, ради чего затевался круглый стол."""
    items = payload.get("persuasion") or []
    if not items:
        return []
    out = ["**Убеждает:**", ""]
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(f"- → **{it.get('agent', '?')}**")
        if it.get("their_objection"):
            out.append(f"  {ARROW} их возражение: {it['their_objection']}")
        if it.get("my_counter"):
            out.append(f"  {ARROW} ответ: {it['my_counter']}")
        if it.get("concession"):
            out.append(f"  {ARROW} уступает взамен: {it['concession']}")
    out.append("")
    return out


def _role_title(role: str) -> str:
    if not role:
        return ""
    try:
        return f" — {role_spec(role).label_ru}"
    except (KeyError, ValueError):
        return f" — {role}"


def _round_block(rnd: RoundResult, roles: dict[str, str] | None = None) -> list[str]:
    is_initial = rnd.index == 0
    title = (
        "Раунд 0 — независимые ответы (агенты друг друга ещё не видели)"
        if is_initial
        else f"Раунд {rnd.index} — обсуждение"
    )
    out = [f"## {title}", ""]

    roles = roles or {}
    for agent, outcome in sorted(rnd.outcomes.items()):
        out.append(f"### {agent}{_role_title(roles.get(agent, ''))}")
        if not outcome.ok or not outcome.payload:
            d = explain(agent, outcome.error_kind, outcome.error)
            out.append(f"**{d.title}.** {d.action}")
            if d.url:
                out.append("")
                out.append(f"{d.url}")
            out.append("")
            out.append(f"Ответ провайдера: `{outcome.error or 'нет данных'}`")
            out.append("")
            out.append(
                f"*код: {outcome.error_kind or 'неизвестно'} · "
                f"попыток: {outcome.attempts}*"
            )
            out.append("")
            continue

        p = outcome.payload
        out.append(str(p.get("answer") if is_initial else p.get("position") or ""))
        out.append("")

        if is_initial:
            if p.get("key_points"):
                out.append("**Ключевые тезисы:**")
                out.append("")
                out.extend(f"- {k}" for k in p["key_points"])
                out.append("")
            if p.get("assumptions"):
                out.append("**Допущения:**")
                out.append("")
                out.extend(f"- {k}" for k in p["assumptions"])
                out.append("")
        else:
            out += _args_block(p, "accepted_arguments", "Принял", "✓")
            out += _args_block(p, "rejected_arguments", "Отверг", "✗")
            out += _args_block(p, "uncertain_arguments", "Не уверен", "?")
            out += _persuasion_block(p)

            if p.get("what_would_change_my_mind"):
                out.append(f"**Что меня переубедило бы:** {p['what_would_change_my_mind']}")
                out.append("")
            if p.get("proposed_common_answer"):
                out.append(f"**Предлагает общую формулировку:** {p['proposed_common_answer']}")
                out.append("")

            if p.get("changed_my_position"):
                by = ", ".join(p.get("persuaded_by") or []) or "не указано"
                out.append(f"**Изменил позицию** (переубедил: {by}). {p.get('why_changed', '')}")
            else:
                out.append(f"**Сохранил позицию.** {p.get('why_changed', '')}")
            out.append("")

        out.append(
            f"*Уверенность: {_pct(float(p.get('confidence') or 0))} · "
            f"{outcome.model} · {outcome.latency_ms} мс · "
            f"{outcome.usage.total_tokens} токенов*"
        )
        out.append("")

    if rnd.consensus is not None:
        c = rnd.consensus
        verdict = "достигнут" if c.reached else "не достигнут"
        out.append(
            f"> **Проверка консенсуса: {verdict}** — {_pct(c.score)} "
            f"при пороге {_pct(c.threshold)}, согласны {c.agree_count} из "
            f"{c.participant_count}, правило `{c.rule}`."
        )
        out.append(f"> {c.reason}")
        if c.unsubstantiated_agents:
            out.append(
                "> Необоснованное согласие отброшено у: "
                + ", ".join(c.unsubstantiated_agents)
            )
        out.append("")

    return out


def roles_markdown(roles: dict[str, str]) -> list[str]:
    if not roles:
        return []
    out = ["## Роли за столом", ""]
    for agent, role in sorted(roles.items()):
        try:
            sp = role_spec(role)
            out.append(f"- **{agent}** — {sp.label_ru}: {sp.short_ru}")
        except (KeyError, ValueError):
            out.append(f"- **{agent}** — {role}")
    out.append("")
    return out


def rounds_markdown(detail: DebateDetail) -> str:
    if not detail.rounds:
        return "_Раундов пока нет._"
    roles = dict(detail.config.roles) if detail.config else {}
    blocks: list[str] = roles_markdown(roles)
    for rnd in detail.rounds:
        blocks.extend(_round_block(rnd, roles))
    return "\n".join(blocks)


def disagreements_markdown(detail: DebateDetail) -> str:
    s = detail.synthesis
    out: list[str] = []

    last = next((r.consensus for r in reversed(detail.rounds) if r.consensus), None)
    if last is not None:
        out.append("## Кто с чем согласился")
        out.append("")
        for agent, level in sorted(last.agreement_by_agent.items()):
            bar = "█" * round(level * 10) + "░" * (10 - round(level * 10))
            mark = "✓" if agent in last.agreeing_agents else "✗"
            out.append(f"- `{bar}` **{agent}** {_pct(level)} {mark}")
        out.append("")
        if last.persuasion_edges:
            out.append("## Кто кого переубедил")
            out.append("")
            for e in last.persuasion_edges:
                out.append(f"- **{e['persuaded_by']}** → **{e['agent']}**")
            out.append("")
        if last.capitulated_agents:
            out.append("## Уступки без аргумента (отброшены)")
            out.append("")
            out.append(
                "Эти участники сменили позицию, но не назвали, кто и чем их "
                "переубедил. Движок не засчитывает такое как согласие — иначе "
                "получился бы консенсус, которого нет:"
            )
            out.extend(f"- {a}" for a in sorted(last.capitulated_agents))
            out.append("")
        if last.material_objections:
            out.append("## Существенные возражения")
            out.append("")
            for m in last.material_objections:
                out.append(f"- **{m['agent']}**: {m['objection']}")
                out.append(f"  {ARROW} доказательство: {m['evidence']}")
            out.append("")

    if s is None:
        return "\n".join(out) or "_Итог ещё не сформирован._"

    if s.disagreements:
        out.append("## В чём не сошлись")
        out.append("")
        out.extend(f"- ⚠ {d}" for d in s.disagreements)
        out.append("")

    if s.minority_positions:
        out.append("## Мнение меньшинства")
        out.append("")
        for m in s.minority_positions:
            out.append(f"### {m.agent}")
            out.append(m.position)
            out.append("")
            if m.evidence:
                out.append(f"**Доказательство:** {m.evidence}")
                out.append("")
            if m.assessment:
                out.append(f"**Оценка:** {m.assessment}")
                out.append("")

    if s.rejected_arguments:
        out.append("## Отвергнутые аргументы и причины")
        out.append("")
        for r in s.rejected_arguments:
            who = ", ".join(r.rejected_by) or "—"
            out.append(f"- **{r.argument}**")
            out.append(f"  {ARROW} отвергли: {who} — {r.reason}")
        out.append("")

    if s.strongest_arguments:
        out.append("## Самые сильные аргументы обсуждения")
        out.append("")
        out.extend(f"- {a}" for a in s.strongest_arguments)
        out.append("")

    if s.individual_positions:
        out.append("## Итоговая позиция каждого")
        out.append("")
        for p in s.individual_positions:
            out.append(f"- **{p.agent}**: {p.position}")
        out.append("")

    return "\n".join(out) or "_Расхождений не зафиксировано._"


def tokens_markdown(detail: DebateDetail) -> str:
    cost = detail.cost
    if cost is None or not cost.lines:
        return "_Расход не зафиксирован._"

    out = ["| Модель | Агент | Входящих | Исходящих | Всего | Оценка |", "|---|---|---:|---:|---:|---:|"]
    for line in cost.lines:
        price = (
            f"${line.estimated_cost_usd:.4f}" if line.pricing_known else "не задана"
        )
        out.append(
            f"| `{line.model}` | {line.agent} | {line.input_tokens:,} | "
            f"{line.output_tokens:,} | {line.total_tokens:,} | {price} |".replace(",", " ")
        )
    out.append(
        f"| | **ВСЕГО** | | | **{cost.total_tokens:,}** | "
        f"**${cost.estimated_cost_usd:.4f}** |".replace(",", " ")
    )
    out.append("")

    if not cost.complete:
        out.append(
            "> Оценка неполная: цена не задана для "
            + ", ".join(f"`{m}`" for m in cost.models_without_pricing)
            + ". Впишите её в `backend/pricing.json` — модели без цены "
            "никогда не считаются бесплатными."
        )
    return "\n".join(out)


def summary_line(detail: DebateDetail) -> str:
    """Одна строка под ответом — главное о состоянии совета."""
    last = next((r.consensus for r in reversed(detail.rounds) if r.consensus), None)
    parts = [f"Согласие: {_pct(detail.consensus_score)}"]
    if last is not None:
        parts.append(f"{last.agree_count} из {last.participant_count} моделей")
        dissent = [a for a in last.dissenting_agents]
        if dissent:
            parts.append("возражает: " + ", ".join(dissent))
    if not detail.consensus_reached:
        parts.append("консенсус не достигнут")
    parts.append(f"раундов: {detail.rounds_used} из {detail.max_rounds}")
    return "  ·  ".join(parts)


def failure_summary(detail: DebateDetail) -> str:
    """Что делать, когда обсуждение не состоялось.

    Показывается вместо ответа: по строке на провайдера с конкретным
    действием, а не с сырым JSON от API.
    """
    if not detail.rounds:
        return ""

    # Берём последнюю неудачу каждого участника.
    latest: dict[str, tuple[str | None, str | None]] = {}
    for rnd in detail.rounds:
        for agent, outcome in rnd.outcomes.items():
            if not outcome.ok:
                latest[agent] = (outcome.error_kind, outcome.error)
            else:
                latest.pop(agent, None)
    if not latest:
        return ""

    out = ["### Обсуждение не состоялось", ""]
    for agent, (kind, text) in sorted(latest.items()):
        d = explain(agent, kind, text)
        # Ссылка идёт в той же строке: перенос внутри пункта Qt превращает
        # продолжение в отдельный пункт списка.
        link = f" [{d.url}]({d.url})" if d.url else ""
        out.append(f"- **{agent}** — {d.title.lower()}. {d.action}{link}")
    out.append("")
    out.append(
        "Совет работает и с двумя участниками: достаточно, чтобы заработали "
        "любые два."
    )
    return "\n".join(out)


def failed_agents(detail: DebateDetail) -> list[str]:
    if not detail.rounds:
        return []
    last = detail.rounds[-1]
    return sorted(
        agent
        for agent, outcome in last.outcomes.items()
        if not outcome.ok and outcome.status.value != "DISABLED"
    )
