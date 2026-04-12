# CLAUDE.md — MCP Forge CLI: Full Project Context

This file is the authoritative context document for AI-assisted development in Cursor.
Read this before making any changes to the codebase.

---

## What This Project Is

**MCP Forge CLI** is a local CLI toolkit that replicates the core pipeline of
[MCP Blacksmith](https://mcpblacksmith.com) — a SaaS that converts OpenAPI specs into
production-ready MCP (Model Context Protocol) servers.

The goal: give any developer a **zero-cost, offline-capable** version of that pipeline.
You point it at an OpenAPI 3.x YAML or JSON file and get back a fully structured,
deployment-ready [FastMCP](https://github.com/jlowin/fastmcp) server with resilience
patterns, API Key authentication, Pydantic validation, and Docker support.

**Current scope: Phase 1 — CLI only. No web UI yet.**

---

## Reference: MCP Blacksmith Docs (what we are replicating)

| Doc page | What it covers |
|---|---|
| https://docs.mcpblacksmith.com/generation/validation | Validation rules, two-stage pipeline |
| https://docs.mcpblacksmith.com/generation/generation | Generation config, enhancement passes, auth overrides |
| https://docs.mcpblacksmith.com/server/structure | File layout: server.py, _models.py, _validators.py, _auth.py |
| https://docs.mcpblacksmith.com/server/authentication | Auth types: API Key, Bearer, OAuth2, JWT, mTLS |
| https://docs.mcpblacksmith.com/server/security | Circuit breaker, rate limit, retry, timeouts, sanitization |
| https://docs.mcpblacksmith.com/deployment/local | Virtual env setup, transport options, Docker |

---

## Repository Layout

```
mcp-forge/
├── CLAUDE.md                  ← YOU ARE HERE — full AI context
├── README.md                  ← Human-facing project readme
├── BUILD_STATE.md             ← What is done / what is next
├── scripts/
│   ├── validate_spec.py       ← Step 1: OpenAPI spec validator
│   ├── generate_server.py     ← Step 2: FastMCP server code generator
│   └── forge.py               ← Pipeline: validate → generate → deployment guide
├── templates/                 ← (empty, reserved for Jinja2 templates in Phase 2)
└── output/                    ← .gitignore this — generated servers land here
```

---

## Script Responsibilities

### `scripts/validate_spec.py`

**Purpose:** Validate an OpenAPI 3.x spec before generation. Two layers:

1. **Structural validation** via `openapi_spec_validator.validate()` — checks the spec
   is syntactically valid OpenAPI. IMPORTANT: uses `validate()` not `iter_errors()` —
   the `iter_errors()` API on `OpenAPIV30SpecValidator` throws `unhashable type: 'dict'`
   in current library versions. Do not change this.

2. **Quality / MCP-readiness checks** (custom, in `check_quality()`):
   - Missing `info.title`, `info.description`, `info.version` → warning
   - Missing `operationId` → warning
   - Duplicate `operationId` → **error**
   - No `summary` or `description` on operation → warning
   - Broken `$ref` references → **error**
   - Script injection in description fields (`<script`, `javascript:`) → **error**
   - `eval()` in descriptions → warning
   - Invalid `apiKey.in` value → warning
   - No paths defined → **error**

**Exit codes:** `0` = PASS (safe to generate), `1` = FAIL (blocked)

**CLI flags:**
```
validate_spec.py <spec_file>
  --strict          treat warnings as blocking errors
  --output FILE     write JSON validation report
  --quiet           suppress all output except PASS/FAIL line
```

**Key functions:**
- `ensure_deps()` — installs `pyyaml`, `openapi-spec-validator` if missing
- `load_spec(path)` — loads YAML or JSON
- `validate_structure(spec)` — calls openapi-spec-validator
- `check_quality(spec)` — custom rule engine, returns list of `{level, message, path, fix}`
- `print_summary(...)` — renders coloured report to stdout
- `main()` — argparse CLI entry

---

### `scripts/generate_server.py`

**Purpose:** Generate a complete FastMCP server package from a parsed OpenAPI spec.

**Output — 9 files:**

| File | How it's generated |
|---|---|
| `server.py` | `gen_server()` — FastMCP app, resilience layer, all tool functions |
| `_models.py` | `gen_models()` — one Pydantic `StrictModel` per operation, used by generated tools |
| `_validators.py` | `gen_validators()` — hardcoded, full OAS format registry |
| `_auth.py` | `gen_auth()` — `APIKeyAuth` class + `OPERATION_AUTH_MAP` |
| `.env` | `gen_env()` — all config keys with comments |
| `requirements.txt` | `gen_requirements()` — fastmcp, httpx, pydantic, python-dotenv |
| `Dockerfile` | `gen_dockerfile()` — python:3.12-slim, non-root user |
| `.mcp.json` | `gen_mcp_json()` — stdio config template |
| `README.md` | `gen_readme()` — setup steps, tool list, docker |

**Key architectural decisions in generated `server.py`:**

- Uses `FastMCP` from `fastmcp` package (not the low-level MCP SDK)
- HTTP client is `httpx.AsyncClient` with connection pooling
- Resilience stack is all in-process: `_TokenBucket` + `_CircuitBreaker` classes
- `_execute_with_resilience()` is the single HTTP dispatch function all tools call
- Tool functions are pure `async def` decorated with `@mcp.tool()`
- Auth injection happens inside each tool function before the HTTP call
- Transport is selected at runtime via `--transport` argparse flag
- All config via `os.getenv()` from `.env` (loaded by `python-dotenv`)

**Key functions:**
- `extract_operations(spec)` → list of op dicts with `{operation_id, method, path, parameters, request_body, security, ...}`
- `detect_auth(spec)` → `{has_auth, schemes, global_security}`
- `slugify(text)` → safe Python snake_case identifier
- `python_type(schema)` → maps JSON Schema type to Python type annotation string
- `resolve_ref(spec, ref)` → walks `$ref` paths within the spec dict
- `build_tool_function(spec, op, auth_info)` → returns one `@mcp.tool()` function as a string
- `gen_auth(spec, force_api_key)` → returns `_auth.py` content or `None`
- `generate(spec_path, output_dir, server_name, force_api_key)` → orchestrates all gen_ calls

**CLI flags:**
```
generate_server.py <spec_file>
  --output DIR      output directory (default: ./<server_name>)
  --name NAME       server name override
  --api-key         force API Key auth even if not in spec
```

---

### `scripts/forge.py`

**Purpose:** Single-command pipeline. Calls `validate_spec.py` and `generate_server.py`
as subprocesses (so each script remains independently usable), then prints the full
local deployment guide.

**Pipeline steps:**
1. `validate_spec.py <spec>` — exits 1 on failure, halts pipeline
2. `generate_server.py <spec> --output DIR --name NAME` — exits 1 on failure
3. `print_deployment_guide()` — prints venv setup, transport options, Docker, .mcp.json

**CLI flags:**
```
forge.py <spec_file>
  --output DIR          output directory
  --name NAME           server name
  --api-key             force API Key auth
  --strict              treat validation warnings as blocking
  --skip-validation     jump straight to generation
  --report FILE         save validation JSON report
```

---

## Generated Server: Resilience Patterns

### Circuit Breaker (`_CircuitBreaker` class in `server.py`)
Three states: CLOSED → OPEN → HALF_OPEN → CLOSED
- Opens after `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures (default 5)
- Stays open for `CIRCUIT_BREAKER_TIMEOUT_SECONDS` (default 60s)
- Returns `{"error": "circuit_breaker_open", ...}` when open
- Config: `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_TIMEOUT_SECONDS` in `.env`

### Retry with Exponential Backoff
- `MAX_RETRIES` attempts (default 3) on HTTP 429, 500, 502, 503, 504
- Delay = `RETRY_BACKOFF_FACTOR ^ attempt` + random jitter (0–0.5s)
- Config: `MAX_RETRIES`, `RETRY_BACKOFF_FACTOR`

### Token-Bucket Rate Limiter (`_TokenBucket` class)
- Refills at `RATE_LIMIT_REQUESTS_PER_SECOND` tokens/sec (default 10)
- Sleeps the calling coroutine if bucket is empty
- Config: `RATE_LIMIT_REQUESTS_PER_SECOND`

### Timeouts
- Multi-layer: connect / read / write / pool via `httpx.Timeout`
- Overall tool execution timeout via `asyncio.wait_for()`
- Config: `HTTPX_CONNECT_TIMEOUT`, `HTTPX_READ_TIMEOUT`, `HTTPX_WRITE_TIMEOUT`,
  `HTTPX_POOL_TIMEOUT`, `TOOL_EXECUTION_TIMEOUT`

---

## Generated Server: Authentication

Phase 1 supports **API Key only**.

`_auth.py` contains:
- `APIKeyAuth` class — reads `API_KEY` from env, injects into header/query/cookie
- `OPERATION_AUTH_MAP` — dict mapping `operation_id → list[list[str]]`
  - Outer list = OR (any one scheme works)
  - Inner list = AND (all schemes in group required)
- `build_auth_registry()` — instantiates auth handlers from env at call time

Example `OPERATION_AUTH_MAP`:
```python
OPERATION_AUTH_MAP = {
    "listPets":    [],           # public
    "addPet":      [["apiKey"]], # requires apiKey
    "getInventory":[["apiKey"]], # requires apiKey
}
```

**Auth types planned for future phases (not yet built):**
- Bearer Token (`Authorization: Bearer <token>`)
- HTTP Basic (`Authorization: Basic <base64>`)
- OAuth2 (client_credentials, authorization_code flows)
- OpenID Connect
- JWT
- Mutual TLS (mTLS)

---

## Generated Server: Validation Layers

### `_validators.py`
- `StrictModel(BaseModel)` — `extra="forbid"`, `frozen=True` — used for request params
- `PermissiveModel(BaseModel)` — `extra="allow"` — used for response parsing
- `FORMAT_VALIDATORS` dict — 25+ validators keyed by OAS format string
- `validate_format(value, fmt)` — applies the right validator
- `sanitize_response(data, level)` — redacts sensitive fields at LOW/MEDIUM/HIGH

### `_models.py`
- One `<OperationName>Params(StrictModel)` class per operation
- Fields derived from `parameters` array + `requestBody` schema properties
- Optional params use `Optional[T] = None`, required use `Field(...)`
- Generated tools instantiate the model at the top of each tool and use validated `params.*`
  fields for path, query, header, and supported request body construction

### Response validation in `server.py`
- Controlled by `RESPONSE_VALIDATION_MODE` env var
- `off` — skip, `warn` — log and return anyway, `strict` — block invalid responses

---

## Python Conventions Used

- `from __future__ import annotations` on every file
- Type hints throughout
- `dedent()` + `indent()` from `textwrap` for multi-line code generation
- All generated code is pure string generation (no AST manipulation, no Jinja2 yet)
- `subprocess.run()` for cross-script calls in `forge.py`
- ANSI escape codes for terminal colour (no external deps like `rich`)
- `ensure_deps()` in each script auto-installs missing packages via pip

---

## Dependencies

### CLI scripts (minimal)
```
pyyaml                  # spec parsing
openapi-spec-validator  # structural validation
```

### Generated server
```
fastmcp>=2.12.0,<3.0.0
httpx>=0.27.0,<1.0.0
pydantic>=2.0.0,<3.0.0
python-dotenv>=1.0.0,<2.0.0
```

---

## Known Issues / Gotchas

1. **`openapi-spec-validator` API bug** — `OpenAPIV30SpecValidator.iter_errors()` throws
   `unhashable type: 'dict'` in current versions. We use `validate(spec)` instead which
   works correctly. Do NOT revert to `iter_errors()`.

2. **String-based code generation** — generated code is built by string interpolation,
   not AST. This means complex nested schemas (deeply nested `$ref`, `allOf`, `oneOf`,
   `anyOf`) may produce incomplete `_models.py` fields. Phase 2 should introduce
   proper schema traversal with a recursive resolver.

3. **`requestBody` handling** — currently only handles `application/json` content type
   with `type: object` schemas. `multipart/form-data`, `application/x-www-form-urlencoded`,
   and array body schemas are not yet modelled.

4. **No `$ref` resolution across files** — only internal `#/` refs are resolved.
   External file refs (`./schemas/pet.yaml`) will silently produce empty schemas.

5. **Template dir is empty** — `templates/` is reserved for Jinja2 templates when we
   refactor code generation away from raw string interpolation.

---

## Phase Roadmap

### Phase 1 ✅ COMPLETE — CLI
- [x] `validate_spec.py` — structural + quality validation
- [x] `generate_server.py` — full 9-file server generation
- [x] `forge.py` — pipeline script
- [x] API Key auth in generated `_auth.py`
- [x] Circuit breaker + retry + rate limiter in generated `server.py`
- [x] Response sanitization in generated `_validators.py`
- [x] Dockerfile + `.mcp.json` + `.env` generation
- [x] Deployment guide in pipeline output

### Phase 2 — Quality & Robustness
- [ ] Jinja2 templates replacing string interpolation in `generate_server.py`
- [ ] Recursive `$ref` resolver (handle `allOf`, `oneOf`, `anyOf`, nested refs)
- [ ] `multipart/form-data` and `application/x-www-form-urlencoded` body support
- [x] Wire `_models.py` into tool functions for actual Pydantic validation
- [ ] External `$ref` file resolution
- [ ] `vacuum` integration for deeper linting (security patterns, naming conventions)
- [ ] Unit tests for validator + generator

### Phase 3 — Auth Expansion
- [ ] Bearer Token auth handler in `_auth.py`
- [ ] HTTP Basic auth handler
- [ ] OAuth2 client_credentials flow
- [ ] JWT handler
- [ ] mTLS handler

### Phase 4 — Enhancement Passes (replicating MCP Blacksmith paid features)
- [ ] Metadata Filter — strip deprecated params/operations
- [ ] Parameter Filter — remove low-value server-generated fields
- [ ] Tool Enhancer — rewrite operation names + descriptions via LLM

### Phase 5 — Web UI
- [ ] FastAPI backend wrapping the CLI scripts
- [ ] React frontend: spec upload, validation console, generation config, download
- [ ] Mirrors the MCP Blacksmith dashboard UX

---

## How to Run (development)

```bash
# From the mcp-forge/ directory
pip install pyyaml openapi-spec-validator

# Validate only
python scripts/validate_spec.py path/to/spec.yaml

# Generate only (assumes valid spec)
python scripts/generate_server.py path/to/spec.yaml --output ./output/myserver

# Full pipeline
python scripts/forge.py path/to/spec.yaml --output ./output/myserver --name myserver

# Test with the example petstore spec (create one or use any OAS 3.x file)
python scripts/forge.py examples/petstore.yaml
```

---

## Testing the Generated Server

```bash
cd output/<server_name>
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# edit .env: set API_KEY and BASE_URL
python server.py   # runs on stdio — connect via Claude Desktop or Claude Code
```

To test SSE mode:
```bash
python server.py --transport sse --port 8000
# Then connect an MCP client to http://localhost:8000/sse
```

---

## Author / Context

Built by Sathish — Lead Core Banking AI Design Architect and Lead AI Educator at a
Canadian retail bank. This project is part of a broader AI tooling initiative alongside
a multi-agent Proactive Overdraft Prevention system (Google ADK, LiteLLM, FastAPI,
PostgreSQL, Redis, Kafka).

MCP Forge is intended as both a practical tool and an educational artifact demonstrating
how MCP server generation pipelines work.
