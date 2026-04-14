# MCP Forge Quickstart

This guide shows the fastest path to:

1. Validate [`examples/banking_api.yaml`](./examples/banking_api.yaml)
2. Run the full MCP Forge pipeline with [`scripts/forge.py`](./scripts/forge.py)
3. Set up and run the generated server
4. Validate it with the built-in HTTP smoke test

## Prerequisites

- A Python 3.10+ interpreter
- In this workspace, `pyenv` interpreters `3.12.12` and `3.13.3` are available
- For best compatibility, prefer `3.12.12`

If your default `python3` is older, use:

```bash
export PYTHON_BIN="$HOME/.pyenv/versions/3.12.12/bin/python3.12"
```

## 1. Validate the Banking OpenAPI Spec

From the repo root:

```bash
python3 scripts/validate_spec.py examples/banking_api.yaml
```

If you want warnings to block generation too:

```bash
python3 scripts/validate_spec.py examples/banking_api.yaml --strict
```

## 2. Run the Full Pipeline with `forge.py`

This is the recommended happy path because it runs:

1. validation
2. generation
3. local deployment guidance

From the repo root:

```bash
python3 scripts/forge.py examples/banking_api.yaml --output ./output/banking_server --name banking_server
```

If you want strict validation in the pipeline:

```bash
python3 scripts/forge.py examples/banking_api.yaml --output ./output/banking_server --name banking_server --strict
```

This generates:

```text
output/banking_server/
├── server.py
├── _models.py
├── _validators.py
├── _auth.py
├── .env
├── requirements.txt
├── Dockerfile
├── .mcp.json
└── README.md
```

### Optional: Generate Only

If you already validated separately and only want the generator step:

```bash
python3 scripts/generate_server.py examples/banking_api.yaml --output ./output/banking_server --name banking_server
```

## 3. Set Up the Generated Server

Use the helper script:

```bash
./scripts/setup_generated_server.sh ./output/banking_server
```

The setup script will:

- Prefer a compatible Python 3.10+ interpreter
- Create `.venv`
- Install the generated server dependencies from `requirements.txt`

Then activate the environment:

```bash
cd output/banking_server
source .venv/bin/activate
```

## 4. Configure the Generated Server

Edit the generated `.env` file:

```bash
BASE_URL=https://api.bank.example.ca/v1
API_KEY=your-real-api-key
```

For local experimentation, you can keep the other defaults as-is.

## 5. Run the Generated MCP Server

### Stdio mode

```bash
python server.py
```

Use this for local MCP clients like Claude Desktop, Claude Code, or Cursor.

### Streamable HTTP mode

```bash
python server.py --transport streamable-http --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

### SSE mode

```bash
python server.py --transport sse --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## 6. Run the Banking HTTP Smoke Test

The repo includes a full end-to-end test for the banking example:

```bash
python3 scripts/check_banking_http_mode.py
```

What it does:

- Generates the banking server into `output/banking_http_server`
- Bootstraps the generated server with a compatible Python
- Starts a tiny mock Banking REST API locally
- Starts the generated MCP server in `streamable-http`
- Verifies the generated server responds on `/health`
- Connects with an MCP HTTP client
- Runs `initialize`, `tools/list`, and real tool calls like `listaccounts` and `createtransfer`

If you want to force a specific interpreter:

```bash
PYTHON_BIN="$HOME/.pyenv/versions/3.12.12/bin/python3.12" python3 scripts/check_banking_http_mode.py
```

## 7. Quick Commands

Validate only:

```bash
python3 scripts/validate_spec.py examples/banking_api.yaml
```

Pipeline:

```bash
python3 scripts/forge.py examples/banking_api.yaml --output ./output/banking_server --name banking_server
```

Generate only:

```bash
python3 scripts/generate_server.py examples/banking_api.yaml --output /tmp/banking_out
```

Syntax check generated Python files:

```bash
python3 -m py_compile /tmp/banking_out/server.py /tmp/banking_out/_auth.py /tmp/banking_out/_models.py /tmp/banking_out/_validators.py
```

Run the demo flow:

```bash
python3 demo.py
```

## Notes

- Generated tools now instantiate their `_models.py` Pydantic models before each HTTP call.
- The generated server expects a real upstream API unless you use the built-in banking HTTP smoke test.
- If setup fails because the wrong Python is used, export `PYTHON_BIN` to a `pyenv` 3.12.12 or 3.13.3 interpreter and rerun.
