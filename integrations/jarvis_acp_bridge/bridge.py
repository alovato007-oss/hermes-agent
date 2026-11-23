"""Small, fail-closed ACP stdio bridge for one JARVIS pre-test mission.

This service is intentionally restricted to a single configured mission and a fixed
no-tools handshake prompt. It is not a general-purpose remote Hermes prompt endpoint.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import shlex
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


def extract_agent_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for message in messages:
        if message.get("method") != "session/update":
            continue
        update = (message.get("params") or {}).get("update") or {}
        if update.get("sessionUpdate") != "agent_message_chunk":
            continue
        content = update.get("content") or {}
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            chunks.append(content["text"])
    return "".join(chunks).strip()


def validate_dispatch_payload(payload: dict[str, Any], allowed_mission_id: str) -> tuple[str, str]:
    mission_id = payload.get("mission_id")
    idempotency_key = payload.get("idempotency_key")
    if not allowed_mission_id or mission_id != allowed_mission_id:
        raise ValueError("mission_id is not the configured pre-test mission")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    return mission_id, idempotency_key.strip()


class DispatchRequest(BaseModel):
    mission_id: str
    idempotency_key: str


app = FastAPI(title="JARVIS Hermes ACP Pre-test Bridge", docs_url=None, redoc_url=None)
_cache: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


async def _rpc(proc: asyncio.subprocess.Process, request_id: int, method: str, params: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    assert proc.stdin and proc.stdout
    request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    proc.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
    await proc.stdin.drain()
    notifications: list[dict[str, Any]] = []
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=180)
        if not line:
            raise RuntimeError("Hermes ACP process exited before replying")
        message = json.loads(line)
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"Hermes ACP {method} failed: {message['error']}")
            return message.get("result"), notifications
        notifications.append(message)


async def _run_fixed_pretest() -> dict[str, Any]:
    command = shlex.split(os.getenv("HERMES_ACP_COMMAND", "python -m acp_adapter.entry"))
    cwd = os.getenv("HERMES_WORKSPACE", os.getcwd())
    env = os.environ.copy()
    env["HERMES_ACP_SKIP_CONFIGURED_MCP"] = "1"
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _rpc(proc, 1, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "jarvis-pretest-bridge", "version": "0.1.0"},
        })
        session, _ = await _rpc(proc, 2, "session/new", {"cwd": cwd, "mcpServers": []})
        session_id = session.get("sessionId") if isinstance(session, dict) else None
        if not session_id:
            raise RuntimeError("Hermes ACP did not return a session ID")
        prompt = (
            "This is a connectivity-only JARVIS pre-test. Reply with exactly "
            "JARVIS_HERMES_ACP_PRETEST_OK. Do not call tools, run commands, modify files, "
            "browse, or contact external services."
        )
        _, notifications = await _rpc(proc, 3, "session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": prompt}],
        })
        response_text = extract_agent_text(notifications)
        if response_text != "JARVIS_HERMES_ACP_PRETEST_OK":
            raise RuntimeError("Hermes ACP response did not match the fixed pre-test token")
        return {
            "ok": True,
            "mission_id": os.environ["JARVIS_PRETEST_MISSION_ID"],
            "protocol": "ACP stdio",
            "session_id": session_id,
            "response": response_text,
            "evidence": {"handshake": True, "fixed_prompt": True, "tool_calls_requested": False},
        }
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


@app.get("/health")
async def health() -> dict[str, str]:
    configured = bool(os.getenv("JARVIS_DISPATCH_TOKEN") and os.getenv("JARVIS_PRETEST_MISSION_ID"))
    return {"status": "ready" if configured else "unconfigured"}


@app.post("/v1/pretest")
async def pretest(body: DispatchRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = os.getenv("JARVIS_DISPATCH_TOKEN", "")
    allowed_mission = os.getenv("JARVIS_PRETEST_MISSION_ID", "")
    if not token or not allowed_mission:
        raise HTTPException(status_code=503, detail="Pre-test bridge is not configured")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        mission_id, key = validate_dispatch_payload(body.model_dump(), allowed_mission)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with _lock:
        if key in _cache:
            return _cache[key]
        result = await _run_fixed_pretest()
        result["mission_id"] = mission_id
        result["idempotency_key"] = key
        _cache[key] = result
        return result
