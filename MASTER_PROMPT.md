# MASTER_PROMPT.md — MCP Forge CLI: Cursor Session Starter

> **Copy everything below the line and paste it as your first message in a new Cursor
> conversation with Claude. It front-loads all context needed to continue development
> without re-explanation.**

---

```
═══════════════════════════════════════════════════════════════════
PROJECT CONTEXT — MCP FORGE CLI
═══════════════════════════════════════════════════════════════════

You are continuing development of MCP Forge CLI — a local Python CLI toolkit that
converts OpenAPI 3.x specs into production-ready FastMCP servers, replicating the
pipeline of https://mcpblacksmith.com.

IMPORTANT FILES TO READ FIRST (in this order):
  1. CLAUDE.md        — Full architectural context, all design decisions, known gotchas
  2. BUILD_STATE.md   — Exact current state, what works, what's broken, Phase 2 priorities
  3. README.md        — User-facing feature summary

═══════════════════════════════════════════════════════════════════
CODEBASE SUMMARY (what exists right now)
═══════════════════════════════════════════════════════════════════

mcp-forge/
├── scripts/
│   ├── validate_spec.py      (383 lines) — OpenAPI structural + quality validation
│   ├── generate_server.py  (1,092 lines) — FastMCP server code generator
│   └── forge.py              (242 lines) — Pipeline: validate → generate → deploy guide
├── templates/                            — Empty, reserved for Jinja2 (Phase 2)
├── CLAUDE.md                             — Full AI context
├── BUILD_STATE.md                        — Build state + next steps
└── README.md                             — Human docs

ALL THREE SCRIPTS ARE WORKING AND TESTED. Phase 1 is complete.

═══════════════════════════════════════════════════════════════════
CRITICAL KNOWN ISSUE — DO NOT CHANGE THIS
═══════════════════════════════════════════════════════════════════

In validate_spec.py, we use:

    from openapi_spec_validator import validate
    validate(spec)   ← THIS IS CORRECT

NOT:

    validator = OpenAPIV30SpecValidator(spec)
    for e in validator.iter_errors(spec):   ← THIS THROWS: unhashable type: 'dict'

The iter_errors() API is broken in the currently installed version.
Never revert to iter_errors(). Never "fix" this by switching back.

═══════════════════════════════════════════════════════════════════
ARCHITECTURE OF GENERATED server.py
═══════════════════════════════════════════════════════════════════

Every generated server.py contains:
- FastMCP from fastmcp package (NOT the low-level MCP SDK)
- httpx.AsyncClient with connection pooling (lazy singleton via get_client())
- _TokenBucket class — token bucket rate limiter
- _CircuitBreaker class — CLOSED/OPEN/HALF_OPEN states
- _execute_with_resilience() — single HTTP dispatch function all tools call
- One @mcp.tool() async function per OpenAPI operation
- Auth injection inside each tool via _auth.build_auth_registry()
- argparse main() with --transport stdio|sse|streamable-http, --port, --host
- All config via os.getenv() loaded from .env via python-dotenv

═══════════════════════════════════════════════════════════════════
GENERATED FILE SET (9 files)
═══════════════════════════════════════════════════════════════════

server.py        FastMCP app + resilience (circuit breaker, retry, rate limiter)
_models.py       Pydantic StrictModel per operation, used by generated tools
_validators.py   25+ OAS format validators + StrictModel/PermissiveModel base classes
_auth.py         APIKeyAuth class + OPERATION_AUTH_MAP (only if spec has securitySchemes)
.env             All config: BASE_URL, API_KEY, timeouts, resilience tuning, logging
requirements.txt fastmcp>=2.12, httpx>=0.27, pydantic>=2.0, python-dotenv>=1.0
Dockerfile       python:3.12-slim, non-root mcpuser, EXPOSE 8000, SSE default
.mcp.json        Drop-in MCP client config (stdio, adjust path to server.py)
README.md        Setup guide: venv, install, configure, run, Docker

═══════════════════════════════════════════════════════════════════
PHASE 2 PRIORITIES (what to build next, in order)
═══════════════════════════════════════════════════════════════════

P0 — Wire _models.py into server.py tool functions [CORRECTNESS] ✅
     Generated tools now instantiate their per-operation model and use validated
     params for path/query/header/body assembly before the HTTP call.

P1 — Jinja2 templates [MAINTAINABILITY]
     Replace gen_*() string interpolation with templates/ directory
     One .j2 file per output file. Install: pip install jinja2

P2 — Recursive $ref resolver [REAL-WORLD SPECS]
     Current resolve_ref() only does one-level #/ resolution
     Need: allOf merge, oneOf/anyOf union types, external file refs

P3 — requestBody expansion [COMPLETENESS]
     Currently only handles application/json + type:object
     Add: multipart/form-data, application/x-www-form-urlencoded, array bodies

P4 — Bearer Token auth [AUTH EXPANSION]
     Add BearerTokenAuth class (detect type:http, scheme:bearer in securitySchemes)
     After that: HTTP Basic, then OAuth2 client_credentials

P5 — Tests [QUALITY]
     tests/test_validate_spec.py — unit tests per quality rule
     tests/test_generate_server.py — snapshot tests per gen_*() function
     tests/test_pipeline.py — integration test end-to-end
     tests/fixtures/ — sample specs: minimal, with-auth, complex-schemas, with-errors

═══════════════════════════════════════════════════════════════════
DEPENDENCIES
═══════════════════════════════════════════════════════════════════

CLI scripts require:
  pyyaml
  openapi-spec-validator

Generated servers require:
  fastmcp>=2.12.0,<3.0.0
  httpx>=0.27.0,<1.0.0
  pydantic>=2.0.0,<3.0.0
  python-dotenv>=1.0.0,<2.0.0

═══════════════════════════════════════════════════════════════════
HOW TO TEST AFTER ANY CHANGE
═══════════════════════════════════════════════════════════════════

# Quick smoke test
python scripts/validate_spec.py examples/banking_api.yaml
python scripts/generate_server.py examples/banking_api.yaml --output /tmp/test_out
python scripts/forge.py examples/banking_api.yaml --output /tmp/test_pipeline

# Syntax check all generated Python files
python -c "
import ast
for f in ['/tmp/test_out/server.py', '/tmp/test_out/_auth.py',
          '/tmp/test_out/_models.py', '/tmp/test_out/_validators.py']:
    ast.parse(open(f).read())
    print(f'SYNTAX OK: {f}')
"

# Full end-to-end demo script
python demo.py

═══════════════════════════════════════════════════════════════════
TODAY'S TASK
═══════════════════════════════════════════════════════════════════

[REPLACE THIS LINE with what you want to build, e.g.:]

"Implement Phase 2 P0: wire _models.py into tool functions in generate_server.py"
"Add Bearer Token auth handler (Phase 2 P4)"
"Add Jinja2 templating to replace string interpolation in generate_server.py"
"Add tests — start with test_validate_spec.py fixtures and unit tests"
"Add vacuum-style linting rules to check_quality() in validate_spec.py"
"Build the FastAPI web UI wrapper (Phase 5 start)"

═══════════════════════════════════════════════════════════════════
AUTHOR CONTEXT
═══════════════════════════════════════════════════════════════════

Sathish — Lead Core Banking AI Design Architect and Lead AI Educator, Canadian retail bank.
Stack context: Google ADK, LiteLLM, FastAPI, PostgreSQL, Redis, Kafka, Spring Boot.
MCP Forge is a standalone tool + educational artifact about MCP server generation pipelines.
```
