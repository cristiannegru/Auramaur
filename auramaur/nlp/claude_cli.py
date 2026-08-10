"""Shared safety lane and availability circuit for Claude CLI subprocesses.

Claude Code uses one account/session directory. Every in-process caller must
serialize through this lane, and quota/session-limit failures must stop new
subprocesses until a short probe cooldown expires. The CLI reports some quota
failures on stdout with an empty stderr, so callers must classify both streams.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass

import structlog

log = structlog.get_logger()

_LANE = asyncio.Semaphore(1)
_blocked_until = 0.0
_blocked_reason = ""


class ClaudeCLIUnavailable(RuntimeError):
    """Claude CLI is known unavailable; callers should use a fallback."""


@dataclass(frozen=True)
class ClaudeCLIResult:
    stdout: str
    stderr: str


def _quota_failure(detail: str) -> bool:
    text = detail.casefold()
    return any(marker in text for marker in (
        "session limit", "usage limit", "weekly limit", "rate limit",
        "hit your limit", "quota exceeded",
    ))


def reset_circuit_for_tests() -> None:
    global _blocked_until, _blocked_reason
    _blocked_until = 0.0
    _blocked_reason = ""


async def run_claude_cli(
    *args: str,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> ClaudeCLIResult:
    """Run one Claude CLI call through the process-wide lane."""
    global _blocked_until, _blocked_reason

    now = time.monotonic()
    if now < _blocked_until:
        remaining = max(1, round(_blocked_until - now))
        raise ClaudeCLIUnavailable(
            f"Claude CLI circuit open for {remaining}s: {_blocked_reason}")

    async with _LANE:
        now = time.monotonic()
        if now < _blocked_until:
            remaining = max(1, round(_blocked_until - now))
            raise ClaudeCLIUnavailable(
                f"Claude CLI circuit open for {remaining}s: {_blocked_reason}")

        proc = await asyncio.create_subprocess_exec(
            "claude", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            killed = proc.kill()
            if inspect.isawaitable(killed):
                await killed
            await proc.wait()
            raise

        stdout = stdout_raw.decode(errors="replace").strip()
        stderr = stderr_raw.decode(errors="replace").strip()
        if proc.returncode != 0:
            detail = (stderr or stdout or f"exit {proc.returncode}")[:500]
            if _quota_failure(detail):
                _blocked_reason = detail
                _blocked_until = time.monotonic() + 15 * 60
                log.warning(
                    "claude_cli.circuit_open",
                    reason=detail,
                    cooldown_seconds=15 * 60,
                )
                raise ClaudeCLIUnavailable(detail)
            raise RuntimeError(
                f"Claude CLI failed (rc={proc.returncode}): {detail}")

        _blocked_until = 0.0
        _blocked_reason = ""
        return ClaudeCLIResult(stdout=stdout, stderr=stderr)
