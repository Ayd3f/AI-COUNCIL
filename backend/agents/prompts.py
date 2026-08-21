"""All prompt construction lives here.

Design rules encoded in these prompts:

* Independence (#3, #22, #23): agents are explicitly told not to agree unless
  they can name a concrete reason, to say why another agent is wrong, and
  never to switch sides because the majority did.
* No hidden reasoning leaks (#25): agents are asked for conclusions,
  arguments, evidence, objections, confidence and position changes — not for
  their internal chain of thought.
* Structured output (#12): the exact JSON shape is restated in every prompt,
  which also acts as the repair instruction on retry.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..models.enums import AgentName

# --------------------------------------------------------------------------- #
# Shared council charter (requirement #24)
# --------------------------------------------------------------------------- #

SHARED_SYSTEM = """You are a participant at a round table of independent AI models, each built by a
different lab. You have your own assigned role in this debate.

The council's job is to arrive at ONE answer that every participant can honestly
sign. Your job is to argue for the most accurate answer and to actively persuade
the others — not to sit beside them stating opinions in parallel.

You must:
1. Analyze the original question.
2. Analyze the other agents' arguments and address them by name.
3. Identify correct arguments, adopt them, and say whose they were.
4. Identify incorrect arguments and explain precisely why they fail.
5. Actively try to move the agents who disagree with you: answer their objection
   in their own terms, and say what you are willing to concede in exchange.
6. State what evidence or argument would change your own position.
7. Explicitly state uncertainty.
8. Change your position only when a specific argument moved you — and name it.

Hard rules:
- Persuading is your job. Capitulating is not. Never agree in order to end the debate.
- Do not agree with another agent unless you can identify a specific reason why their
  argument is correct.
- If another agent is wrong, explicitly explain why.
- A bare "I agree" with no specific reason is treated as a non-answer and will be discarded.
- If everyone else holds position X and you hold position Y on better evidence, keep Y
  and keep arguing for it. A minority position with evidence is worth more to this
  council than a unanimous wrong answer.
- If you do change your position, `persuaded_by` must name the agent whose argument
  moved you and `why_changed` must state that argument. An unexplained switch is
  recorded as capitulation and discarded.
- Report conclusions, arguments, evidence, objections and confidence. Do not narrate your
  internal step-by-step reasoning; give the substance, not the monologue.
- Output valid JSON only. No prose before or after the JSON. No markdown code fences."""


# Per-agent identity. Each agent must know exactly who it is so that
# cross-references between agents are never confused. Generated rather than
# hardcoded so that adding a provider cannot leave a stale roster behind.

#: Pairs that are easy to misread as the same participant. Spelled out
#: explicitly because a merged identity corrupts argument attribution for the
#: whole round.
CONFUSABLE: dict[AgentName, tuple[AgentName, str]] = {
    AgentName.GROK: (
        AgentName.GROQCLOUD,
        "GROK (you, the xAI model) and GROQCLOUD (a different participant, an "
        "open-weights model hosted by Groq) are two separate agents whose names "
        "differ by one letter. Never merge them or attribute one's argument to "
        "the other.",
    ),
    AgentName.GROQCLOUD: (
        AgentName.GROK,
        "GROQCLOUD (you, an open-weights model hosted by Groq) and GROK (a "
        "different participant, the xAI model) are two separate agents whose "
        "names differ by one letter. Never merge them or attribute one's "
        "argument to the other.",
    ),
}


def identity(agent: AgentName) -> str:
    others = ", ".join(a.value for a in AgentName if a is not agent)
    text = (
        f"Your identity in this council is {agent.value}. When other agents "
        f"cite '{agent.value}', they mean you. Never speak as, or on behalf "
        f"of, any other participant: {others}."
    )
    warning = CONFUSABLE.get(agent)
    if warning is not None:
        text += f"\n\n{warning[1]}"
    return text


#: Kept as a mapping for callers that want the raw text.
IDENTITY: dict[AgentName, str] = {a: identity(a) for a in AgentName}


def system_prompt(agent: AgentName, role: str | None = None) -> str:
    parts = [SHARED_SYSTEM, IDENTITY[agent]]
    if role:
        from .roles import spec

        try:
            parts.append(spec(role).instruction)
        except (KeyError, ValueError):
            pass  # неизвестная роль не должна ломать раунд
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# JSON shapes (restated verbatim in prompts)
# --------------------------------------------------------------------------- #

INITIAL_SHAPE = """{
  "answer": "your full answer, in markdown",
  "key_points": ["short claim 1", "short claim 2"],
  "confidence": 0.0,
  "assumptions": ["assumption you had to make"]
}"""

DEBATE_SHAPE = """{
  "position": "your current position after reading the others, in markdown",
  "accepted_arguments": [
    {"agent": "CLAUDE", "argument": "the argument you accept", "reason": "the specific reason it is correct"}
  ],
  "rejected_arguments": [
    {"agent": "GEMINI", "argument": "the argument you reject", "reason": "the specific reason it is wrong"}
  ],
  "uncertain_arguments": [
    {"agent": "GROK", "argument": "the argument you cannot settle", "reason": "what evidence would settle it"}
  ],
  "persuasion": [
    {"agent": "GROK", "their_objection": "the objection that keeps them from your answer",
     "my_counter": "your answer to it, in their terms", "concession": "what you grant them in exchange"}
  ],
  "persuaded_by": ["CLAUDE"],
  "what_would_change_my_mind": "the specific evidence or argument that would move you",
  "proposed_common_answer": "one wording of the answer you believe every agent could sign, or empty",
  "changed_my_position": false,
  "why_changed": "if you changed, name the argument that moved you; if not, why you kept your position",
  "confidence": 0.0
}"""

CONSENSUS_SHAPE = """{
  "main_conclusion": "one sentence: the conclusion the council appears to be converging on",
  "agrees_with_main_conclusion": false,
  "agreement_level": 0.0,
  "has_material_objection": false,
  "objection": "your objection, if any",
  "evidence_for_objection": "the concrete evidence or reasoning behind the objection"
}"""

SYNTHESIS_SHAPE = """{
  "consensus": "what the council collectively considers most likely correct, in markdown",
  "main_reasoning": "short explanation of why",
  "key_agreements": ["point everyone agreed on"],
  "disagreements": ["point where agents did not fully agree"],
  "strongest_arguments": ["the strongest argument that appeared during the debate"],
  "rejected_arguments": [
    {"argument": "argument that was rejected", "rejected_by": ["OPENAI"], "reason": "why it was rejected"}
  ],
  "minority_positions": [
    {"agent": "DEEPSEEK", "position": "the minority view", "evidence": "its evidence", "assessment": "a fair assessment of how strong it is"}
  ],
  "individual_positions": [
    {"agent": "OPENAI", "position": "one or two sentences summarising this agent's final position"}
  ],
  "confidence": 0
}"""


def _fmt_confidence_note() -> str:
    return "`confidence` is a number between 0.0 and 1.0."


# --------------------------------------------------------------------------- #
# Round 0
# --------------------------------------------------------------------------- #


def build_initial_prompt(question: str) -> str:
    return f"""ORIGINAL QUESTION FROM THE USER:
\"\"\"
{question}
\"\"\"

This is ROUND 0. You are answering independently. You have not seen, and will
not see, any other agent's answer yet. Do not speculate about what the others
might say.

Answer the question as accurately as you can. State any assumption you had to
make in order to answer. Be honest about your confidence: {_fmt_confidence_note()}

Respond with a single JSON object in exactly this shape:
{INITIAL_SHAPE}"""


# --------------------------------------------------------------------------- #
# Rounds 1..N
# --------------------------------------------------------------------------- #


def _render_initial(agent: str, payload: dict[str, Any]) -> str:
    key_points = payload.get("key_points") or []
    assumptions = payload.get("assumptions") or []
    parts = [f"### {agent}", str(payload.get("answer", "")).strip()]
    if key_points:
        parts.append("Key points: " + "; ".join(str(k) for k in key_points))
    if assumptions:
        parts.append("Assumptions: " + "; ".join(str(a) for a in assumptions))
    parts.append(f"Stated confidence: {payload.get('confidence', 0)}")
    return "\n".join(parts)


def _render_debate(agent: str, payload: dict[str, Any]) -> str:
    parts = [f"### {agent}", str(payload.get("position", "")).strip()]

    def _refs(label: str, key: str) -> None:
        items = payload.get(key) or []
        if items:
            rendered = "; ".join(
                f"[{it.get('agent', '?')}] {it.get('argument', '')} — {it.get('reason', '')}"
                for it in items
                if isinstance(it, dict)
            )
            parts.append(f"{label}: {rendered}")

    _refs("Accepted", "accepted_arguments")
    _refs("Rejected", "rejected_arguments")
    _refs("Uncertain", "uncertain_arguments")
    if payload.get("changed_my_position"):
        parts.append(f"Changed position because: {payload.get('why_changed', '')}")
    else:
        parts.append(f"Kept position because: {payload.get('why_changed', '') or 'no reason given'}")
    parts.append(f"Stated confidence: {payload.get('confidence', 0)}")
    return "\n".join(parts)


def render_others(
    round_kind: str, others: dict[str, dict[str, Any]]
) -> str:
    if not others:
        return "(no other agent produced a valid response this round)"
    render = _render_initial if round_kind == "INITIAL" else _render_debate
    return "\n\n".join(render(a, p) for a, p in sorted(others.items()))


def render_own_history(history: list[dict[str, Any]]) -> str:
    """`history` = list of {"round": int, "kind": str, "payload": dict}."""
    if not history:
        return "(this is your first contribution)"
    blocks: list[str] = []
    for item in history:
        payload = item["payload"]
        if item["kind"] == "INITIAL":
            blocks.append(
                f"[Round {item['round']} — your independent answer]\n"
                f"{str(payload.get('answer', '')).strip()}\n"
                f"Confidence: {payload.get('confidence', 0)}"
            )
        else:
            accepted = payload.get("accepted_arguments") or []
            rejected = payload.get("rejected_arguments") or []
            blocks.append(
                f"[Round {item['round']} — your debate position]\n"
                f"{str(payload.get('position', '')).strip()}\n"
                f"You accepted: {json.dumps(accepted, ensure_ascii=False)}\n"
                f"You rejected: {json.dumps(rejected, ensure_ascii=False)}\n"
                f"changed_my_position={payload.get('changed_my_position')}, "
                f"confidence={payload.get('confidence', 0)}"
            )
    return "\n\n".join(blocks)


def build_debate_prompt(
    *,
    question: str,
    round_index: int,
    max_rounds: int,
    own_history: list[dict[str, Any]],
    others: dict[str, dict[str, Any]],
    others_kind: str,
    previous_consensus: dict[str, Any] | None,
    roles: dict[str, str] | None = None,
    agent: AgentName | None = None,
) -> str:
    consensus_block = ""
    if previous_consensus:
        consensus_block = f"""
RESULT OF THE PREVIOUS CONSENSUS CHECK (computed by the system, not by an agent):
- consensus reached: {previous_consensus.get('reached')}
- consensus score: {previous_consensus.get('score')} (threshold {previous_consensus.get('threshold')})
- agents currently agreeing: {', '.join(previous_consensus.get('agreeing_agents') or []) or 'none'}
- agents currently dissenting: {', '.join(previous_consensus.get('dissenting_agents') or []) or 'none'}
- reason: {previous_consensus.get('reason', '')}

This is information, not an instruction. Do NOT move toward the majority
because it is the majority.
"""

    roles_block = ""
    if roles:
        from .roles import render_roster

        mine = roles.get(agent.value) if agent is not None else None
        roles_block = f"""
WHO IS AT THE TABLE AND IN WHICH ROLE:
{render_roster(roles)}

{"Your role in this debate is " + mine + "." if mine else ""}
Address the others by name and take their role into account: an objection from
the EVIDENCE checker is answered with evidence, an objection from the PRAGMATIST
is answered with feasibility.
"""

    rounds_left = max(0, max_rounds - round_index)
    pressure = (
        f"\nThis is the LAST round ({round_index} of {max_rounds}). Whatever is not "
        "resolved now goes into the final answer as an open disagreement, so spend "
        "this round on the gap that actually matters.\n"
        if rounds_left == 0
        else f"\nRounds remaining after this one: {rounds_left}.\n"
    )

    return f"""ORIGINAL QUESTION FROM THE USER:
\"\"\"
{question}
\"\"\"

This is DEBATE ROUND {round_index} of at most {max_rounds}.
{pressure}
YOUR OWN PREVIOUS CONTRIBUTIONS:
{render_own_history(own_history)}

WHAT THE OTHER AGENTS SAID IN THE PREVIOUS ROUND:
{render_others(others_kind, others)}
{roles_block}{consensus_block}
Now analyse the other agents. For every argument you engage with, name the
agent it belongs to and give a specific reason.

- Put an argument in `accepted_arguments` only if you can state *why* it is correct.
- Put an argument in `rejected_arguments` and explain the concrete error.
- Put an argument in `uncertain_arguments` if you need evidence to settle it, and
  say what evidence would settle it.
- You are allowed to fully agree, partially agree, disagree, point out an error,
  demand proof, change your position, or keep your position.

Then do the part that actually ends debates — work on the people who disagree
with you:

- `persuasion`: pick the agents whose position differs from yours and, for each,
  restate THEIR objection in their own terms, answer it, and name what you are
  willing to concede in exchange. An entry that does not name a real objection of
  that agent is worthless.
- `what_would_change_my_mind`: the specific evidence or argument that would move
  you. If you cannot name one, your position is not a reasoned one.
- `proposed_common_answer`: one wording of the answer you believe every agent at
  this table could sign. Leave it empty if the gap is still real — do not invent
  agreement that has not happened.
- `changed_my_position` / `persuaded_by`: set these together. Change your position
  only when a named agent's specific argument moved you, and put that agent in
  `persuaded_by`. A switch with an empty `persuaded_by` is recorded as capitulation
  and thrown away — it will not count towards consensus.

{_fmt_confidence_note()}

Respond with a single JSON object in exactly this shape:
{DEBATE_SHAPE}"""


# --------------------------------------------------------------------------- #
# Consensus evaluation
# --------------------------------------------------------------------------- #


def build_consensus_prompt(
    *,
    question: str,
    round_index: int,
    positions: dict[str, dict[str, Any]],
) -> str:
    rendered = "\n\n".join(
        f"### {agent}\n{str(p.get('position') or p.get('answer') or '').strip()}"
        for agent, p in sorted(positions.items())
    )
    return f"""ORIGINAL QUESTION FROM THE USER:
\"\"\"
{question}
\"\"\"

CURRENT POSITIONS AFTER ROUND {round_index}:
{rendered}

Assess the state of the council honestly.

- `main_conclusion`: state, in one sentence, the conclusion the council appears to be
  converging on — even if you personally disagree with it.
- `agrees_with_main_conclusion`: true only if YOU actually agree with that conclusion.
- `agreement_level`: 0.0 = you fully disagree, 1.0 = you fully agree.
- `has_material_objection`: true only if you hold a substantive, evidence-backed
  objection that would change the answer if correct. Stylistic or wording
  differences are NOT material objections.
- Do not claim agreement you do not have in order to make the debate finish.

Respond with a single JSON object in exactly this shape:
{CONSENSUS_SHAPE}"""


# --------------------------------------------------------------------------- #
# Final synthesis (separate stage, neutral role)
# --------------------------------------------------------------------------- #

SYNTHESIS_SYSTEM = """You are the SYNTHESIS ENGINE of a multi-agent council. You are NOT one of
the debating agents and you do not have a side.

Your job is to compile the debate into a final answer:
1. Collect all positions.
2. Identify the strongest arguments.
3. Identify contradictions.
4. Identify which arguments were rejected.
5. Identify why they were rejected.
6. Report the degree of agreement.
7. Produce the final answer.

Rules:
- Do not invent agreement that did not happen.
- If a minority position exists, report it and assess it fairly. A minority
  position with strong evidence must be preserved, not erased, even if four
  agents disagreed with it.
- Attribute every position to the correct agent. Never mix agents up.
- Report conclusions, arguments, evidence and objections — not internal reasoning.
- Output valid JSON only. No prose before or after the JSON. No markdown code fences."""


def build_synthesis_prompt(
    *,
    question: str,
    transcript: str,
    consensus: dict[str, Any],
    failed_agents: Iterable[str],
) -> str:
    failed = ", ".join(failed_agents) or "none"
    return f"""ORIGINAL QUESTION FROM THE USER:
\"\"\"
{question}
\"\"\"

FULL DEBATE TRANSCRIPT:
{transcript}

DETERMINISTIC CONSENSUS METRICS (computed by the system, authoritative — do not contradict them):
{json.dumps(consensus, ensure_ascii=False, indent=2)}

AGENTS THAT FAILED AND DID NOT CONTRIBUTE TO THE FINAL ROUND: {failed}

Produce the final synthesis. `confidence` is an integer from 0 to 100 and must be
consistent with the consensus metrics above.

`individual_positions` must contain exactly one entry per agent that produced at
least one valid response.

Respond with a single JSON object in exactly this shape:
{SYNTHESIS_SHAPE}"""


REPAIR_INSTRUCTION = """Your previous reply was not valid JSON matching the required schema.

Return ONLY a single valid JSON object matching the schema that was given to you.
No explanation. No markdown. No code fences. No text before or after the object.
Start your reply with an opening curly brace and end it with a closing curly brace."""
