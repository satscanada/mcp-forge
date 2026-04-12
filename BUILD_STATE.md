# BUILD_STATE.md — MCP Forge CLI

**Last updated:** 2026-04-11
**Session:** Built with Claude Sonnet 4.6 in claude.ai
**Status:** Phase 2 P0 complete. `_models.py` is now wired into generated tools.

---

## What Was Built This Session

### Starting point
- Reference: MCP Blacksmith (https://mcpblacksmith.com) screenshots showing the dashboard
- Reference docs: validation, generation, server structure, auth, security, local deployment
- Sample: A generated `cat_server.zip` from MCP Blacksmith (The Cat API) — used to study
  the real output format

### What we produced

Three CLI scripts, all tested end-to-end against a Petstore OpenAPI spec:

| File | Lines | Status |
|---|---|---|
| `scripts/validate_spec.py` | 383 | ✅ Working |
| `scripts/generate_server.py` | 1,092 | ✅ Working |
| `scripts/forge.py` | 242 | ✅ Working |
| `README.md` | ~120 | ✅ Written |
| `CLAUDE.md` | ~280 | ✅ Written |
| `BUILD_STATE.md` | this file | ✅ Written |

### Test run result (Petstore spec, 6 operations)

```
validate_spec.py  → PASS (structural valid + all quality checks passed)
generate_server.py → 9 files generated, all Python files syntax-valid (ast.parse)
forge.py           → Full pipeline in 1.1s total
```

Generated files for petstore confirmed syntax-valid:
- `server.py` ✅
- `_auth.py` ✅
- `_models.py` ✅
- `_validators.py` ✅

---

## Exact Current State of Each Script

### `scripts/validate_spec.py`

**What works:**
- Loads YAML and JSON specs
- Calls `openapi_spec_validator.validate(spec)` for structural validation
- Runs 10 custom quality checks (see CLAUDE.md for full list)
- Coloured terminal output with errors/warnings/fixes
- `--strict`, `--output FILE`, `--quiet` flags
- Exit code 0 = PASS, 1 = FAIL

**Known fix applied:**
- Uses `validate()` not `iter_errors()` — the latter throws `unhashable type: 'dict'`
  in the current version of `openapi-spec-validator`. DO NOT change this back.

**What's missing / could be improved:**
- Only reports the first structural error (validate() raises on first hit)
- No `vacuum` integration for deeper linting rules
- `check_quality()` doesn't handle `allOf`/`oneOf`/`anyOf` schema patterns

---

### `scripts/generate_server.py`

**What works:**
- Parses the spec and extracts all operations via `extract_operations()`
- Detects auth schemes via `detect_auth()`
- Resolves internal `$ref` references via `resolve_ref()`
- Maps JSON Schema types to Python types via `python_type()`
- Generates all 9 output files via individual `gen_*()` functions
- `build_tool_function()` generates one `@mcp.tool()` async function per operation
- `--output`, `--name`, `--api-key` flags

**Architecture of generated `server.py`:**
- `FastMCP` from `fastmcp` package
- `httpx.AsyncClient` with connection pooling
- `_TokenBucket` class — token bucket rate limiter
- `_CircuitBreaker` class — three-state circuit breaker (CLOSED/OPEN/HALF_OPEN)
- `_execute_with_resilience()` — single HTTP dispatch function for all tools
- `get_client()` — lazy singleton httpx client
- `main()` with argparse for `--transport`, `--port`, `--host`
- All config from `os.getenv()` loaded via `python-dotenv`

**Known limitations (Phase 2 targets):**
1. `requestBody` only handles `application/json` with `type: object`
2. External `$ref` files not resolved (only `#/` internal refs)
3. Deeply nested schemas (`allOf`, `oneOf`, `anyOf`) produce `Any` type fallbacks
4. Code generation is raw string interpolation — Jinja2 templating is Phase 2
5. `gen_models()` only processes top-level `parameters` and flat `requestBody` object schemas

---

### `scripts/forge.py`

**What works:**
- Calls `validate_spec.py` and `generate_server.py` as subprocesses
- Streams output from each subprocess directly to terminal
- Detects `--strict`, `--skip-validation`, `--report`, `--api-key`, `--output`, `--name`
- Prints formatted deployment guide after generation (venv, transport options, Docker, .mcp.json)
- Total elapsed time display

**Known limitations:**
- No `--dry-run` flag
- No `--format json` for CI/CD pipeline output

---

## File Structure Right Now

```
mcp-forge/
├── CLAUDE.md                  ← Full AI context (read first)
├── README.md                  ← Human-facing docs
├── BUILD_STATE.md             ← This file
├── scripts/
│   ├── validate_spec.py       ← Step 1 (383 lines)
│   ├── generate_server.py     ← Step 2 (1,092 lines)
│   └── forge.py               ← Pipeline (242 lines)
├── templates/                 ← Empty, reserved for Jinja2
└── output/                    ← .gitignore this
```

No `tests/` directory yet. No `examples/` directory yet. No `setup.py` / `pyproject.toml` yet.

---

## What To Build Next (Phase 2 priorities in order)

### P0 — Fix _models.py wiring (completed)

Generated `server.py` now imports `_models`, instantiates the per-operation
`<OpName>Params` model at the top of each tool function, and uses validated
`params.<field>` values for path, query, header, and supported request-body assembly.

Example generated pattern:
```python
@mcp.tool()
async def list_pets(status: str | None = None) -> dict[str, Any]:
    """List all pets."""
    params = _models.ListPetsParams(status=status)
    ...
```

Impact:
- Pydantic validation now actually runs for generated tools
- Invalid inputs fail fast before the outbound HTTP call
- Generated `_models.py` is no longer dead code

### P1 — Jinja2 code generation

Replace all the `gen_*()` functions in `generate_server.py` that do string interpolation
with Jinja2 templates stored in `templates/`:

```
templates/
├── server.py.j2
├── _auth.py.j2
├── _models.py.j2
├── _validators.py.j2
├── .env.j2
├── Dockerfile.j2
├── .mcp.json.j2
└── README.md.j2
```

This will make the generation code much more maintainable and testable.

### P2 — Recursive $ref resolver

The current `resolve_ref()` only does one-level `#/` resolution. Build a proper
recursive resolver that handles:
- `allOf` — merge all schemas
- `oneOf` / `anyOf` — union types
- Nested `$ref` within schemas
- External file refs (`./schemas/pet.yaml#/Pet`)

### P3 — requestBody expansion

Handle the full set of content types:
- `multipart/form-data` — generate individual file/field params
- `application/x-www-form-urlencoded` — generate individual field params
- Array bodies (`type: array`)
- Binary/file uploads

### P4 — Bearer Token auth (next simplest after API Key)

Add `BearerTokenAuth` class to `_auth.py` generation:
```python
class BearerTokenAuth:
    def __init__(self, env_var: str = "BEARER_TOKEN"):
        self.token = os.getenv(env_var, "").strip()
    def inject(self, headers, params, cookies):
        headers["Authorization"] = f"Bearer {self.token}"
    def is_available(self):
        return bool(self.token)
```

Detect `type: http, scheme: bearer` in `securitySchemes`.

### P5 — Tests

Create `tests/` directory with:
- `test_validate_spec.py` — unit tests for each quality check rule
- `test_generate_server.py` — snapshot tests for each `gen_*()` function
- `test_pipeline.py` — integration test running the full pipeline against a fixture spec
- `fixtures/` — sample YAML specs: minimal, with auth, with complex schemas, with errors

### P6 — vacuum linting integration

`vacuum` (https://quobix.com/vacuum/) is a Go binary. Options:
a) Shell out to `vacuum` if it's installed (check with `shutil.which("vacuum")`)
b) Use the `vacuum` Python bindings if available
c) Implement a subset of vacuum rules in pure Python in `check_quality()`

Rules to add regardless:
- Duplicate path detection (same path + method with different operationIds)
- Ambiguous path parameter detection (`/pets/{id}` vs `/pets/{petId}`)
- Missing license in `info`
- Missing `servers` block
- Parameter naming convention (camelCase vs snake_case consistency)

---

## How to Work on This in Cursor

1. Open `mcp-forge/` as the workspace root in Cursor
2. Read `CLAUDE.md` first — paste it as context if starting a new conversation
3. The three scripts in `scripts/` are self-contained — you can edit them independently
4. To test changes:
   ```bash
   python scripts/validate_spec.py test_petstore.yaml
   python scripts/generate_server.py test_petstore.yaml --output /tmp/test_out
   python scripts/forge.py test_petstore.yaml --output /tmp/test_pipeline
   ```
5. After any change to `generate_server.py`, always run the syntax check:
   ```bash
   python -c "
   import ast
   for f in ['/tmp/test_out/server.py', '/tmp/test_out/_auth.py',
             '/tmp/test_out/_models.py', '/tmp/test_out/_validators.py']:
       ast.parse(open(f).read())
       print(f'✓ {f}')
   "
   ```

---

## Suggested Cursor Prompt to Continue

When opening a new Cursor conversation, paste this:

```
I'm working on MCP Forge CLI — a local CLI toolkit that converts OpenAPI 3.x specs 
into production FastMCP servers, replicating MCP Blacksmith's pipeline.

Read CLAUDE.md for full project context. Read BUILD_STATE.md for exactly where we 
left off and what to build next.

The three scripts are in scripts/:
- validate_spec.py (Step 1 — validation)
- generate_server.py (Step 2 — code generation)  
- forge.py (pipeline)

All three work and are tested. Phase 1 is complete.

Today I want to work on: [Phase 2 / specific feature from BUILD_STATE.md]
```

---

## Dependencies Installed in Current Dev Environment

```
pyyaml                    # installed
openapi-spec-validator    # installed
```

Everything else (fastmcp, httpx, pydantic, python-dotenv) is only needed inside
generated server directories, not for running the CLI scripts themselves.
