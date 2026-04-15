# MCP Forge CLI

**Convert any OpenAPI 3.x spec into a production-ready MCP server — locally, for free.**

Inspired by [MCP Blacksmith](https://mcpblacksmith.com). No account, no API key, no cost.
Point it at your OpenAPI YAML or JSON and get back a fully structured
[FastMCP](https://github.com/jlowin/fastmcp) server with resilience patterns,
authentication, Pydantic validation, and Docker support.

For the fastest banking example path, see [QUICKSTART.md](./QUICKSTART.md).

---

## Install

```bash
git clone <this-repo>
cd mcp-forge
pip install pyyaml openapi-spec-validator
pip install jinja2
```

That's it. No other dependencies for the CLI itself.

---

## Usage

### One command — full pipeline

```bash
python scripts/forge.py my_api.yaml
```

Runs validation → generation → prints local deployment steps.

### Options

```bash
# Custom output directory and server name
python scripts/forge.py my_api.yaml --output ./my-server --name my_server

# Force API Key auth even if the spec doesn't declare it
python scripts/forge.py my_api.yaml --api-key

# Strict mode — warnings block generation too
python scripts/forge.py my_api.yaml --strict

# Save a JSON validation report
python scripts/forge.py my_api.yaml --report validation.json

# Skip validation (e.g. already validated separately)
python scripts/forge.py my_api.yaml --skip-validation
```

### Individual steps

```bash
# Step 1 only — validate
python scripts/validate_spec.py my_api.yaml
python scripts/validate_spec.py my_api.yaml --strict
python scripts/validate_spec.py my_api.yaml --output report.json

# Step 2 only — generate
python scripts/generate_server.py my_api.yaml
python scripts/generate_server.py my_api.yaml --output ./out --name myserver --api-key
```

### Banking HTTP smoke test

The repo includes a full HTTP-mode smoke test for the bundled banking example.
It generates the server, bootstraps its venv, starts a tiny mock upstream Banking API,
runs the generated MCP server in `streamable-http`, and verifies it through an MCP client.

```bash
python scripts/check_banking_http_mode.py
```

---

## What Gets Generated

```
<server_name>/
├── server.py          # FastMCP server — one tool per API operation
├── _models.py         # Pydantic models for request parameters, wired into each tool
├── _validators.py     # 25+ OAS format validators + StrictModel / PermissiveModel
├── _auth.py           # API Key auth handler + per-operation auth map
├── .env               # All configuration (credentials, timeouts, resilience)
├── requirements.txt   # fastmcp, httpx, pydantic, python-dotenv
├── Dockerfile         # python:3.12-slim, non-root, SSE transport
├── .mcp.json          # Drop-in MCP client config template
└── README.md          # Setup guide for the generated server
```

---

## Running the Generated Server

```bash
cd <server_name>

# 1. Virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows

# 2. Install deps
pip install -r requirements.txt

# 3. Configure — edit .env, set BASE_URL and API_KEY (if needed)

# 4. Run
python server.py                                         # stdio — Claude Desktop / Code / Cursor
python server.py --transport sse --port 8000             # SSE — network / Docker
python server.py --transport streamable-http --port 8000 # Streamable HTTP
```

Or use the helper script from the repo root to bootstrap a generated server in one step:

```bash
./scripts/setup_generated_server.sh ./<server_name>
cd <server_name>
source .venv/bin/activate
```

### Docker

```bash
docker build -t <server_name> .
docker run -p 8000:8000 --env-file .env <server_name>
```

### MCP Client Config

Copy `.mcp.json` into your MCP client's config directory. Adjust the path to `server.py`.

---

## What the Generated Server Includes

### Resilience (built into every server)

| Feature | Default | `.env` key |
|---|---|---|
| Retry + exponential backoff | 3 attempts on 429/5xx | `MAX_RETRIES`, `RETRY_BACKOFF_FACTOR` |
| Circuit breaker | Opens after 5 failures, 60s timeout | `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_TIMEOUT_SECONDS` |
| Token-bucket rate limiter | 10 req/s | `RATE_LIMIT_REQUESTS_PER_SECOND` |
| Multi-layer timeouts | connect 10s / read 60s / tool 90s | `HTTPX_*_TIMEOUT`, `TOOL_EXECUTION_TIMEOUT` |
| Connection pool | 100 connections, 20 keepalive | `CONNECTION_POOL_SIZE`, `MAX_KEEPALIVE_CONNECTIONS` |

### Authentication

Auto-detected from `securitySchemes` in your spec. The generator now supports:
- API Key
- HTTP Bearer
- HTTP Basic
- OAuth2 client credentials

Per-operation routing is handled via `OPERATION_AUTH_MAP`. Generated auth handlers read
scheme-specific environment variables from `.env`, and operations return an
`auth_unavailable` error if their required credentials are not configured.

### Validation and Sanitization

- Input: Pydantic `StrictModel` — rejects unknown fields, enforces types and formats
- Response: configurable via `RESPONSE_VALIDATION_MODE` (`off` / `warn` / `strict`)
- Sanitization: `SANITIZATION_LEVEL` (`DISABLED` / `LOW` / `MEDIUM` / `HIGH`) redacts
  passwords, tokens, API keys, session IDs from responses before returning to the agent

Generated tools instantiate their corresponding `<OperationName>Params` model before
building the HTTP request, so invalid inputs fail fast with a clear Pydantic validation error.

---

## Validation Checks

### Structural (via `openapi-spec-validator`)
Verifies the spec is valid OpenAPI 3.0.x or 3.1.x.

### Quality and MCP-readiness

| Check | Severity |
|---|---|
| Missing `info.title` / `description` / `version` | Warning |
| Missing `operationId` on operation | Warning |
| Duplicate `operationId` | Error |
| No `summary` or `description` on operation | Warning |
| Broken `$ref` reference | Error |
| Script injection in description | Error |
| `eval()` in description | Warning |
| Invalid `apiKey.in` value | Warning |
| No paths defined | Error |

Errors always block generation. Warnings block only with `--strict`.

---

## Roadmap

- **Phase 2** — Jinja2 templates, recursive `$ref` resolution, richer request body support, `vacuum` linting
- **Phase 3** — JWT, mTLS, broader OAuth2 flows, and auth override controls
- **Phase 4** — Enhancement passes: metadata filter, parameter filter, LLM-powered tool enhancer
- **Phase 5** — Web UI (FastAPI + React) mirroring the MCP Blacksmith dashboard

---

## Requirements

- Python 3.10+
- CLI: `pyyaml`, `openapi-spec-validator`
- Generated server: `fastmcp`, `httpx`, `pydantic`, `python-dotenv` (auto-listed in generated `requirements.txt`)
