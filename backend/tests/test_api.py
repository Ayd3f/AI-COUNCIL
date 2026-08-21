"""HTTP surface: settings, starting a debate, polling, SSE replay, export."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ..agents import registry
from ..config import get_settings
from ..models.enums import AgentName
from .mock_provider import MockAgent

KEY_ENVS = {
    "OPENAI_API_KEY": "test-openai",
    "ANTHROPIC_API_KEY": "test-anthropic",
    "GOOGLE_API_KEY": "test-google",
    "XAI_API_KEY": "test-xai",
    "DEEPSEEK_API_KEY": "test-deepseek",
    "GROQ_API_KEY": "test-groq",
    "CEREBRAS_API_KEY": "test-cerebras",
    "MISTRAL_API_KEY": "test-mistral",
    # У локального участника вместо ключа адрес сервера.
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
}
ROSTER_SIZE = len(registry.ROSTER)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for key, value in KEY_ENVS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path.as_posix()}/api.db")
    monkeypatch.setenv("MIN_ROUNDS", "1")
    monkeypatch.setenv("MAX_ROUNDS", "2")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "100")
    get_settings.cache_clear()

    def fake_council(agents, **_kwargs):
        return {a: MockAgent(a) for a in agents}

    monkeypatch.setattr(registry, "build_council", fake_council)

    from ..main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def wait_for_completion(client: TestClient, debate_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/debate/{debate_id}").json()
        if data["status"] in {"COMPLETED", "FAILED"}:
            return data
        time.sleep(0.1)
    raise AssertionError(f"debate {debate_id} did not finish in {timeout}s")


# --------------------------------------------------------------------------- #


def test_health(client: TestClient):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_settings_never_leaks_api_keys(client: TestClient):
    body = client.get("/api/settings").text
    for value in KEY_ENVS.values():
        assert value not in body
    data = json.loads(body)
    assert len(data["agents"]) == ROSTER_SIZE
    assert all(a["configured"] for a in data["agents"])
    assert {a["agent"] for a in data["agents"]} == {n.value for n in AgentName}
    # Бесплатные провайдеры помечены — интерфейс на это опирается.
    assert any(a["free_tier"] for a in data["agents"])
    assert all(a["signup_url"] for a in data["agents"])
    assert data["defaults"]["max_rounds"] == 2
    assert data["any_provider_configured"] is True


def test_empty_question_is_rejected(client: TestClient):
    r = client.post("/api/debate", json={"question": "   "})
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_overlong_question_is_rejected(client: TestClient):
    limit = client.get("/api/settings").json()["limits"]["max_question_length"]
    r = client.post("/api/debate", json={"question": "x" * (limit + 1)})
    assert r.status_code == 400
    assert "too long" in r.json()["detail"].lower()


def test_single_agent_selection_is_rejected(client: TestClient):
    r = client.post("/api/debate", json={"question": "hi", "agents": ["OPENAI"]})
    assert r.status_code == 400
    assert "at least two" in r.json()["detail"].lower()


def test_full_debate_lifecycle(client: TestClient):
    r = client.post(
        "/api/debate",
        json={"question": "Should we use X or Y?", "max_rounds": 1, "min_rounds": 1},
    )
    assert r.status_code == 202
    debate_id = r.json()["debate_id"]
    assert r.json()["config"]["max_rounds"] == 1
    assert len(r.json()["config"]["agents"]) == ROSTER_SIZE

    data = wait_for_completion(client, debate_id)
    assert data["status"] == "COMPLETED"
    assert data["consensus_reached"] is True
    assert data["synthesis"]["consensus"]
    assert len(data["rounds"]) == 2
    assert data["cost"]["total_tokens"] > 0

    rounds = client.get(f"/api/debate/{debate_id}/rounds").json()["rounds"]
    assert len(rounds) == 2
    assert set(rounds[0]["outcomes"]) == {n.value for n in AgentName}

    listing = client.get("/api/debates").json()["debates"]
    assert any(d["id"] == debate_id for d in listing)


def test_subset_of_agents_can_be_selected(client: TestClient):
    r = client.post(
        "/api/debate",
        json={
            "question": "X or Y?",
            "agents": ["OPENAI", "CLAUDE", "GROK"],
            "max_rounds": 1,
        },
    )
    debate_id = r.json()["debate_id"]
    data = wait_for_completion(client, debate_id)
    assert set(data["rounds"][0]["outcomes"]) == {"OPENAI", "CLAUDE", "GROK"}


def test_sse_replay_after_completion(client: TestClient):
    r = client.post("/api/debate", json={"question": "X or Y?", "max_rounds": 1})
    debate_id = r.json()["debate_id"]
    wait_for_completion(client, debate_id)

    with client.stream("GET", f"/api/debate/{debate_id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in resp.iter_text())

    assert "event: debate_started" in body
    assert "event: agent_finished" in body
    assert "event: consensus_check" in body
    assert "event: debate_finished" in body
    assert "event: stream_end" in body


def test_export_markdown_and_json(client: TestClient):
    r = client.post("/api/debate", json={"question": "X or Y?", "max_rounds": 1})
    debate_id = r.json()["debate_id"]
    wait_for_completion(client, debate_id)

    md = client.get(f"/api/debate/{debate_id}/export", params={"fmt": "markdown"})
    assert md.status_code == 200
    assert "AI COUNCIL — DEBATE REPORT" in md.text
    assert "## FINAL ANSWER" in md.text
    assert "## DEBATE TRANSCRIPT" in md.text
    assert "attachment" in md.headers["content-disposition"]

    js = client.get(f"/api/debate/{debate_id}/export", params={"fmt": "json"})
    assert js.status_code == 200
    payload = json.loads(js.text)
    assert payload["id"] == debate_id
    assert payload["rounds"]


def test_delete_and_clear_history(client: TestClient):
    first = client.post("/api/debate", json={"question": "A?", "max_rounds": 1}).json()
    wait_for_completion(client, first["debate_id"])
    assert client.delete(f"/api/debate/{first['debate_id']}").status_code == 200
    assert client.get(f"/api/debate/{first['debate_id']}").status_code == 404

    second = client.post("/api/debate", json={"question": "B?", "max_rounds": 1}).json()
    wait_for_completion(client, second["debate_id"])
    cleared = client.delete("/api/debates").json()
    assert cleared["deleted"] >= 1
    assert client.get("/api/debates").json()["debates"] == []


def test_unknown_debate_returns_404(client: TestClient):
    assert client.get("/api/debate/nope").status_code == 404
    assert client.get("/api/debate/nope/rounds").status_code == 404
    assert client.get("/api/debate/nope/events").status_code == 404


def test_rate_limit_returns_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key, value in KEY_ENVS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path.as_posix()}/rl.db")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    monkeypatch.setattr(
        registry, "build_council", lambda agents, **_k: {a: MockAgent(a) for a in agents}
    )

    from ..main import create_app

    with TestClient(create_app()) as c:
        codes = [
            c.post("/api/debate", json={"question": f"q{i}", "max_rounds": 1}).status_code
            for i in range(3)
        ]
    get_settings.cache_clear()
    assert codes[:2] == [202, 202]
    assert codes[2] == 429
