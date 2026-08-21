"""Consensus Engine rules (requirements #4, #22, #23)."""

from __future__ import annotations

from ..debate.consensus import ConsensusEngine, collect_minority
from ..models.schemas import ConsensusVote, DebateResponse
from .mock_provider import debate_payload, vote_payload

ENGINE = ConsensusEngine(threshold=0.8, min_rounds=1)


def _vote(**kw) -> ConsensusVote:
    return ConsensusVote.model_validate(vote_payload(**kw))


def _debate(**kw) -> DebateResponse:
    return DebateResponse.model_validate(debate_payload(**kw))


def _five(votes, debates=None):
    names = ["OPENAI", "CLAUDE", "GEMINI", "GROK", "DEEPSEEK"]
    debates = debates or {n: _debate() for n in names}
    return ENGINE.evaluate(
        round_index=1,
        votes={n: v for n, v in zip(names, votes)},
        debates=debates,
    )


def test_unanimous_agreement_reaches_consensus():
    report = _five([_vote(agrees=True, level=0.92)] * 5)
    assert report.reached is True
    assert report.rule == "unanimous_conclusion"
    assert report.agree_count == 5
    assert report.score >= 0.8


def test_four_of_five_without_material_objection_reaches_consensus():
    votes = [_vote(agrees=True, level=0.95)] * 4 + [
        _vote(agrees=False, level=0.55, objection=False)
    ]
    report = _five(votes)
    assert report.reached is True
    assert report.rule == "supermajority"
    assert report.agree_count == 4
    assert report.dissenting_agents == ["DEEPSEEK"]


def test_material_objection_blocks_supermajority():
    votes = [_vote(agrees=True, level=0.95)] * 4 + [
        _vote(agrees=False, level=0.2, objection=True)
    ]
    report = _five(votes)
    assert report.reached is False
    assert "material objection" in report.reason
    assert report.material_objections and report.material_objections[0]["agent"] == "DEEPSEEK"


def test_low_score_blocks_consensus_even_when_everyone_says_yes():
    report = _five([_vote(agrees=True, level=0.5)] * 5)
    assert report.reached is False
    assert "below threshold" in report.reason


def test_bare_agreement_is_not_consensus():
    """Requirement #22: reflexive 'I agree' must not count."""
    empty_debate = DebateResponse.model_validate(
        {
            "position": "I agree with everyone.",
            "accepted_arguments": [],
            "rejected_arguments": [],
            "uncertain_arguments": [],
            "changed_my_position": False,
            "why_changed": "",
            "confidence": 0.99,
        }
    )
    names = ["OPENAI", "CLAUDE", "GEMINI", "GROK", "DEEPSEEK"]
    report = ENGINE.evaluate(
        round_index=1,
        votes={n: _vote(agrees=True, level=0.99) for n in names},
        debates={n: empty_debate for n in names},
    )
    assert report.reached is False
    assert sorted(report.unsubstantiated_agents) == sorted(names)
    assert report.agree_count == 0


def test_short_restated_conclusion_is_unsubstantiated():
    names = ["OPENAI", "CLAUDE"]
    report = ENGINE.evaluate(
        round_index=1,
        votes={n: _vote(agrees=True, level=0.99, conclusion="yes") for n in names},
        debates={n: _debate() for n in names},
    )
    assert report.agree_count == 0
    assert report.reached is False


def test_min_rounds_not_met_never_reports_consensus():
    engine = ConsensusEngine(threshold=0.5, min_rounds=2)
    report = engine.evaluate(
        round_index=1,
        votes={"OPENAI": _vote(), "CLAUDE": _vote()},
        debates={"OPENAI": _debate(), "CLAUDE": _debate()},
    )
    assert report.reached is False
    assert report.rule == "min_rounds_not_met"


def test_single_participant_cannot_form_consensus():
    report = ENGINE.evaluate(
        round_index=1, votes={"OPENAI": _vote()}, debates={"OPENAI": _debate()}
    )
    assert report.reached is False
    assert report.rule == "insufficient_participants"


def test_no_participants():
    report = ENGINE.evaluate(round_index=1, votes={}, debates={})
    assert report.reached is False
    assert report.rule == "no_participants"
    assert report.participant_count == 0


def test_minority_positions_are_collected():
    votes = [_vote(agrees=True, level=0.95)] * 4 + [
        _vote(agrees=False, level=0.2, objection=True)
    ]
    report = _five(votes)
    minority = collect_minority(report, {"DEEPSEEK": debate_payload(position="Y is correct")})
    assert len(minority) == 1
    assert minority[0]["agent"] == "DEEPSEEK"
    assert "Y is correct" in minority[0]["position"]
    assert minority[0]["evidence"]


def test_agreement_by_agent_is_reported_for_the_ui():
    report = _five([_vote(agrees=True, level=0.9)] * 5)
    assert set(report.agreement_by_agent) == {
        "OPENAI",
        "CLAUDE",
        "GEMINI",
        "GROK",
        "DEEPSEEK",
    }
    assert all(0.0 <= v <= 1.0 for v in report.agreement_by_agent.values())
