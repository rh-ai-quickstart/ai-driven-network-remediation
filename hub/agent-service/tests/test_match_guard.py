import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from helpers import make_rca

from agent_service.nodes.match_guard import MatchVerdict, verify_playbook_match


def _llm_returning(payload: dict):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload)))
    return llm


async def test_verify_returns_match():
    llm = _llm_returning({"reasoning": "fits root cause", "verdict": "MATCH"})
    with patch("agent_service.nodes.match_guard.get_llm", return_value=llm):
        verdict = await verify_playbook_match(make_rca(), "restart-nginx")

    assert verdict == MatchVerdict.MATCH


async def test_verify_returns_no_match():
    llm = _llm_returning({"reasoning": "only masks symptom", "verdict": "NO_MATCH"})
    with patch("agent_service.nodes.match_guard.get_llm", return_value=llm):
        verdict = await verify_playbook_match(make_rca(), "restart-nginx")

    assert verdict == MatchVerdict.NO_MATCH


async def test_verify_returns_error_on_exception():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
    with patch("agent_service.nodes.match_guard.get_llm", return_value=llm):
        verdict = await verify_playbook_match(make_rca(), "restart-nginx")

    assert verdict == MatchVerdict.ERROR


async def test_verify_returns_error_on_bad_verdict():
    llm = _llm_returning({"reasoning": "confused", "verdict": "MAYBE"})
    with patch("agent_service.nodes.match_guard.get_llm", return_value=llm):
        verdict = await verify_playbook_match(make_rca(), "restart-nginx")

    assert verdict == MatchVerdict.ERROR
