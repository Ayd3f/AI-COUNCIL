"""Роли за круглым столом.

Раньше все агенты вели себя одинаково: отвечали параллельно и высказывали
позицию. Это давало сравнение мнений, но не спор — никто не пытался никого
переубедить, и совет мог топтаться на месте до MAX_ROUNDS.

Теперь у каждого участника своя роль. Роль задаёт *как* агент спорит, а не
*что* он должен заключить: Адвокат тянет совет к одному ответу, Критик ищет
слабое место, Фактчекер требует доказательств и так далее. Ни одна роль не
обязывает согласиться — обязывает работать над расхождением.

Ключевое ограничение, без которого вся затея превращается в фабрику
фальшивого согласия: **убеждать можно, капитулировать нельзя**. Сменить
позицию разрешено только назвав агента и конкретный аргумент, который
подействовал. Проверяется это не промптом, а кодом — в `debate/consensus.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models.enums import AgentName


class DebateRole(str, Enum):
    ADVOCATE = "ADVOCATE"
    SKEPTIC = "SKEPTIC"
    EVIDENCE = "EVIDENCE"
    PRAGMATIST = "PRAGMATIST"
    BRIDGE = "BRIDGE"
    ANALYST = "ANALYST"


@dataclass(frozen=True)
class RoleSpec:
    role: DebateRole
    label_ru: str
    short_ru: str
    #: инструкция уходит в модель, поэтому по-английски — как и остальной промпт
    instruction: str


_SPECS: dict[DebateRole, RoleSpec] = {
    DebateRole.ADVOCATE: RoleSpec(
        DebateRole.ADVOCATE,
        "Адвокат",
        "тянет совет к одному ответу",
        """YOUR ROLE IS THE ADVOCATE.

You own the convergence of this council. After reading the other agents, decide
which single answer is currently best supported — it does not have to be your
own — and then actively work to bring the others to it.

In every round you must:
- name the agents who do not yet hold that answer;
- address the specific objection each of them raised, one by one, in their own terms;
- state what you would accept from them in exchange (which part of their position
  you are folding in);
- propose one concrete wording of the answer that they could sign.

You are persuading, not steamrolling. If an agent gives you a better argument,
adopt it and say whose it was. If the evidence says your championed answer is
wrong, switch the answer you champion and say why. Declaring agreement that does
not exist is the one failure mode you must never produce.""",
    ),
    DebateRole.SKEPTIC: RoleSpec(
        DebateRole.SKEPTIC,
        "Критик",
        "ищет самое слабое место",
        """YOUR ROLE IS THE SKEPTIC.

Find the weakest link in the position the council is converging on and attack it
precisely. Vague doubt is worthless — name the agent, quote the claim, and say
exactly why it does not hold.

Attack the argument, never the agent. If the answer survives your attack, say so
plainly: "I tried X and Y, both fail, the position holds." A skeptic who cannot
break a position and admits it is doing the job correctly.""",
    ),
    DebateRole.EVIDENCE: RoleSpec(
        DebateRole.EVIDENCE,
        "Фактчекер",
        "требует доказательств",
        """YOUR ROLE IS THE EVIDENCE CHECKER.

Separate what is actually known from what is being assumed. For every load-bearing
claim in the debate, ask: what would verify this?

Mark claims as supported, unsupported, or unverifiable, and name the agent who
made each one. When a disagreement turns out to be about an unverifiable claim,
say so — that usually dissolves the argument faster than winning it.""",
    ),
    DebateRole.PRAGMATIST: RoleSpec(
        DebateRole.PRAGMATIST,
        "Практик",
        "проверяет применимостью",
        """YOUR ROLE IS THE PRAGMATIST.

Test every proposed answer against reality: cost, effort, time, who has to do the
work, what breaks first at scale. An answer that is theoretically correct but
cannot be executed is not the answer to the user's question.

When two positions differ only in theory but produce the same action in practice,
say so — that collapses a fake disagreement into agreement.""",
    ),
    DebateRole.BRIDGE: RoleSpec(
        DebateRole.BRIDGE,
        "Примиритель",
        "ищет формулировку для всех",
        """YOUR ROLE IS THE BRIDGE.

Hunt for the formulation every agent could sign without lying. Find where two
agents are using different words for the same thing, and where they genuinely
disagree — and say which is which.

Do not paper over a real disagreement. If a gap cannot be bridged, state the gap
sharply instead of blurring it: naming an unbridgeable disagreement is a
legitimate result of your role.""",
    ),
    DebateRole.ANALYST: RoleSpec(
        DebateRole.ANALYST,
        "Аналитик",
        "разбирает структуру спора",
        """YOUR ROLE IS THE ANALYST.

Map the structure of the disagreement. Which claims does the answer actually
depend on? Which arguments are decorative? What is the single question that, if
settled, would settle the whole debate?

Point the council at that question instead of letting it argue in all directions
at once.""",
    ),
}

#: Порядок раздачи ролей. Адвокат всегда первый — он один на совет.
_ROTATION: list[DebateRole] = [
    DebateRole.SKEPTIC,
    DebateRole.EVIDENCE,
    DebateRole.PRAGMATIST,
    DebateRole.BRIDGE,
    DebateRole.ANALYST,
]


def spec(role: DebateRole | str) -> RoleSpec:
    if isinstance(role, str):
        role = DebateRole(role)
    return _SPECS[role]


def label(role: DebateRole | str) -> str:
    return spec(role).label_ru


def assign_roles(
    agents: list[AgentName], advocate: AgentName | str | None = None
) -> dict[str, str]:
    """Раздаёт роли детерминированно: одинаковый состав — одинаковые роли.

    Адвокат ровно один: двое «тянущих к одному ответу» тянули бы в разные
    стороны и превратили бы стол в перетягивание каната.
    """
    if not agents:
        return {}

    chosen: AgentName | None = None
    if advocate is not None:
        name = advocate.value if isinstance(advocate, AgentName) else str(advocate)
        name = name.strip().upper()
        if name and name != "AUTO":
            try:
                candidate = AgentName(name)
            except ValueError:
                candidate = None
            if candidate is not None and candidate in agents:
                chosen = candidate
    if chosen is None:
        chosen = agents[0]

    roles: dict[str, str] = {chosen.value: DebateRole.ADVOCATE.value}
    rest = [a for a in agents if a != chosen]
    for i, agent in enumerate(rest):
        roles[agent.value] = _ROTATION[i % len(_ROTATION)].value
    return roles


def render_roster(roles: dict[str, str]) -> str:
    """Строка «кто есть кто» для промпта: агенты должны знать роли друг друга."""
    if not roles:
        return ""
    lines = []
    for agent, role in sorted(roles.items()):
        s = spec(role)
        lines.append(f"- {agent}: {s.role.value}")
    return "\n".join(lines)
