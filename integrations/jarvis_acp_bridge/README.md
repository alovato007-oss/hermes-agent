# JARVIS ↔ Hermes ACP pre-test bridge

This is a narrowly scoped bridge for validating the Hermes Agent Client Protocol (ACP) stdio connection. It is **not** a general remote prompt API and is not yet wired to JARVIS mission persistence.

## Why it exists

Hermes' current ACP entry point runs the ACP server over stdin/stdout. A Supabase Edge Function cannot directly attach to that local stdio transport. This small FastAPI sidecar runs next to Hermes, starts the ACP process, performs initialize → new session → prompt, and returns only the exact pre-test response.

## Safety gates

The endpoint remains disabled unless all three variables are set:

- `JARVIS_PRETEST_ENABLED=true`
- `JARVIS_DISPATCH_TOKEN=<long random secret>`
- `JARVIS_PRETEST_MISSION_ID=<one exact pre-test mission key>`

It accepts only the configured mission key and a non-empty idempotency key. The prompt is fixed in code; callers cannot submit arbitrary instructions. Configured MCP servers are skipped for the spawned ACP process.

Important limitation: the prompt asks Hermes not to call tools, but this bridge does not mechanically remove every built-in Hermes tool. Run it only in a disposable Hermes profile/workspace with no production tool credentials until a hard tool allowlist is added. The idempotency cache is process-local and is not a durable production deduplication mechanism.

## Run locally

From this directory, with Hermes installed and configured for a test-only profile:

```bash
export JARVIS_PRETEST_ENABLED=true
export JARVIS_DISPATCH_TOKEN='<generate-a-long-random-secret>'
export JARVIS_PRETEST_MISSION_ID='jarvis-hermes-pretest-v1'
export HERMES_WORKSPACE='/path/to/disposable/workspace'
uvicorn bridge:app --host 127.0.0.1 --port 8765
```

Health check:

```bash
curl http://127.0.0.1:8765/health
```

Dispatch the fixed pre-test:

```bash
curl -X POST http://127.0.0.1:8765/v1/pretest \
  -H 'Authorization: Bearer <same-secret>' \
  -H 'Content-Type: application/json' \
  -d '{"mission_id":"jarvis-hermes-pretest-v1","idempotency_key":"jarvis-hermes-pretest-v1:task-1"}'
```

Expected response token: `JARVIS_HERMES_ACP_PRETEST_OK`.

Do not expose this service directly to the public internet. Put it behind authenticated HTTPS networking before any remote caller is configured.

## Tests

```bash
python -m unittest discover -s integrations/jarvis_acp_bridge -p 'test_*.py' -v
```

The tests cover message extraction, mission allowlisting, required idempotency keys, disabled-by-default behavior, bearer authentication, and same-process duplicate suppression. They do not yet prove a live Hermes ACP handshake.
