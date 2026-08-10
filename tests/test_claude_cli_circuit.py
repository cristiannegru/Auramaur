import asyncio

import pytest

from auramaur.nlp import claude_cli


class _Proc:
    returncode = 1

    async def communicate(self):
        return (
            b"You've hit your session limit \xc2\xb7 resets 9:10am (UTC)",
            b"",
        )


@pytest.fixture(autouse=True)
def _reset():
    claude_cli.reset_circuit_for_tests()
    yield
    claude_cli.reset_circuit_for_tests()


@pytest.mark.asyncio
async def test_stdout_session_limit_opens_circuit_and_suppresses_retries(monkeypatch):
    calls = 0

    async def spawn(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(claude_cli.ClaudeCLIUnavailable, match="session limit"):
        await claude_cli.run_claude_cli(
            "-p", "hello", timeout=1, env={})
    with pytest.raises(claude_cli.ClaudeCLIUnavailable, match="circuit open"):
        await claude_cli.run_claude_cli(
            "-p", "hello again", timeout=1, env={})

    assert calls == 1
