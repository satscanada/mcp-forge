#!/usr/bin/env python3
"""
check_banking_http_mode.py

Generate the banking example server, start a tiny local upstream Banking API,
run the generated MCP server in streamable HTTP mode, and validate it through
an MCP client handshake plus a couple of tool calls.

Usage:
    python scripts/check_banking_http_mode.py
    python scripts/check_banking_http_mode.py --output /tmp/banking_http_check
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT_DIR / "examples" / "banking_api.yaml"
DEFAULT_OUTPUT = ROOT_DIR / "output" / "banking_http_server"
SETUP_SCRIPT = ROOT_DIR / "scripts" / "setup_generated_server.sh"
GENERATE_SCRIPT = ROOT_DIR / "scripts" / "generate_server.py"

PROTOCOL_VERSION = "2025-11-25"
API_KEY = "banking-demo-key"

ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
FROM_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
TO_ACCOUNT_ID = "33333333-3333-3333-3333-333333333333"


def info(message: str) -> None:
    print(f"[info] {message}")


def ok(message: str) -> None:
    print(f"[ok]   {message}")


def fail(message: str) -> None:
    print(f"[fail] {message}", file=sys.stderr)


class MockBankingHandler(BaseHTTPRequestHandler):
    server_version = "MockBankingAPI/1.0"
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _require_api_key(self) -> bool:
        if self.headers.get("X-API-Key") != API_KEY:
            self._send_json(401, {"error": "unauthorized"})
            return False
        return True

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/accounts":
            if not self._require_api_key():
                return
            query = parse_qs(parsed.query)
            account_type = query.get("account_type", ["all"])[0]
            status = query.get("status", ["active"])[0]
            self._send_json(
                200,
                [
                    {
                        "account_id": ACCOUNT_ID,
                        "account_number": "****1234",
                        "account_type": "chequing" if account_type == "all" else account_type,
                        "currency": "CAD",
                        "status": status,
                        "nickname": "Daily Banking",
                        "opened_date": "2024-01-10",
                    }
                ],
            )
            return

        if parsed.path == f"/v1/accounts/{ACCOUNT_ID}/balance":
            if not self._require_api_key():
                return
            self._send_json(
                200,
                {
                    "account_id": ACCOUNT_ID,
                    "currency": "CAD",
                    "current_balance": 1875.55,
                    "available_balance": 1675.55,
                },
            )
            return

        self._send_json(404, {"error": "not_found", "path": parsed.path})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/transfers":
            if not self._require_api_key():
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
            body = json.loads(raw.decode("utf-8") or "{}")
            self._send_json(
                201,
                {
                    "transfer_id": "44444444-4444-4444-4444-444444444444",
                    "status": "completed",
                    "from_account_id": body.get("from_account_id"),
                    "to_account_id": body.get("to_account_id"),
                    "amount_cad": body.get("amount_cad"),
                    "memo": body.get("memo"),
                },
            )
            return

        self._send_json(404, {"error": "not_found", "path": parsed.path})


def start_mock_banking_api(port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", port), MockBankingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def python_is_compatible(python_bin: Path) -> bool:
    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
            text=True,
            capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def detect_setup_python() -> Path:
    explicit = os.environ.get("PYTHON_BIN")
    if explicit:
        path = Path(explicit).expanduser()
        if python_is_compatible(path):
            return path
        raise RuntimeError(f"PYTHON_BIN is not a usable Python 3.10+ interpreter: {path}")

    pyenv_versions = Path.home() / ".pyenv" / "versions"
    pyenv_candidates = [
        pyenv_versions / "3.12.12" / "bin" / "python3.12",
        pyenv_versions / "3.12.12" / "bin" / "python",
        pyenv_versions / "3.13.3" / "bin" / "python3.13",
        pyenv_versions / "3.13.3" / "bin" / "python",
    ]
    for candidate in pyenv_candidates:
        if candidate.exists() and python_is_compatible(candidate):
            return candidate

    for candidate in ("python3.12", "python3.11", "python3.10", "python3"):
        result = subprocess.run(
            ["zsh", "-lc", f"command -v {candidate} >/dev/null 2>&1 && echo $(command -v {candidate})"],
            text=True,
            capture_output=True,
        )
        path_str = result.stdout.strip()
        if path_str:
            path = Path(path_str)
            if python_is_compatible(path):
                return path

    raise RuntimeError("No compatible Python 3.10+ interpreter found for generated server setup.")


def run_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def write_env_file(output_dir: Path, upstream_port: int) -> None:
    env_path = output_dir / ".env"
    env_lines = []
    for line in env_path.read_text().splitlines():
        if line.startswith("BASE_URL="):
            env_lines.append(f"BASE_URL=http://127.0.0.1:{upstream_port}/v1")
        elif line.startswith("API_KEY="):
            env_lines.append(f"API_KEY={API_KEY}")
        else:
            env_lines.append(line)
    env_path.write_text("\n".join(env_lines) + "\n")


def wait_for_http(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error: str | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.5) as client:
                client.get(url)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def wait_for_health(base_url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error: str | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.5) as client:
                response = client.get(f"{base_url}/health")
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "ok":
                        return
                last_error = f"unexpected health response: {response.status_code} {response.text[:200]}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for health endpoint at {base_url}/health: {last_error}")


class MCPHttpClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.session_id: str | None = None
        self.protocol_version = PROTOCOL_VERSION
        self._next_id = 1
        self._client = httpx.Client(timeout=20.0)

    def close(self) -> None:
        self._client.close()

    def _headers(self, include_protocol: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if include_protocol:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        return headers

    def _parse_sse_response(self, response: httpx.Response) -> dict[str, Any]:
        data_lines: list[str] = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    data_lines.append(payload)
        for payload in reversed(data_lines):
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
                return parsed
        raise RuntimeError(f"No JSON-RPC message found in SSE response: {response.text[:500]}")

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        response = self._client.post(
            self.endpoint,
            headers=self._headers(include_protocol=(method != "initialize")),
            json=payload,
        )
        response.raise_for_status()

        if method == "initialize":
            session_id = response.headers.get("MCP-Session-Id")
            if session_id:
                self.session_id = session_id

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = self._parse_sse_response(response)
        else:
            message = response.json()

        if "error" in message:
            raise RuntimeError(f"MCP error for {method}: {message['error']}")

        if method == "initialize":
            negotiated = message.get("result", {}).get("protocolVersion")
            if negotiated:
                self.protocol_version = negotiated

        return message

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp-forge-http-check",
                    "version": "1.0.0",
                },
            },
        )
        self._client.post(
            self.endpoint,
            headers=self._headers(include_protocol=True),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        ).raise_for_status()
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        return result.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        return result.get("result", {})


def discover_mcp_endpoint(base_url: str) -> str:
    candidates = [f"{base_url}/mcp", f"{base_url}/mcp/"]
    with httpx.Client(timeout=2.0) as client:
        for candidate in candidates:
            try:
                resp = client.post(
                    candidate,
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                )
                if resp.status_code not in {404, 405}:
                    return candidate
            except Exception:
                continue
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the generated banking MCP server over HTTP mode.")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC), help="Path to the banking OpenAPI spec.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Directory for the generated server.")
    parser.add_argument("--mcp-port", type=int, default=8011, help="Port for the generated MCP server.")
    parser.add_argument("--upstream-port", type=int, default=8021, help="Port for the mock banking upstream API.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for the generated MCP server.")
    args = parser.parse_args()

    spec_path = Path(args.spec).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    setup_python = detect_setup_python()

    info("Generating banking MCP server")
    run_command(
        [
            sys.executable,
            str(GENERATE_SCRIPT),
            str(spec_path),
            "--output",
            str(output_dir),
            "--name",
            output_dir.name,
        ],
        cwd=ROOT_DIR,
    )

    info(f"Bootstrapping generated server virtual environment with {setup_python}")
    run_command(
        [str(SETUP_SCRIPT), str(output_dir)],
        cwd=ROOT_DIR,
        env={**os.environ, "PYTHON_BIN": str(setup_python)},
    )

    write_env_file(output_dir, args.upstream_port)
    ok("Updated generated .env with local mock upstream and API key")

    info("Starting mock banking API")
    mock_server, _thread = start_mock_banking_api(args.upstream_port)

    generated_python = output_dir / ".venv" / "bin" / "python"
    server_cmd = [
        str(generated_python),
        "server.py",
        "--transport",
        "streamable-http",
        "--host",
        args.host,
        "--port",
        str(args.mcp_port),
    ]
    info("Starting generated MCP server in streamable HTTP mode")
    server_proc = subprocess.Popen(
        server_cmd,
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    try:
        base_url = f"http://{args.host}:{args.mcp_port}"
        wait_for_http(base_url, timeout_s=20.0)
        wait_for_health(base_url, timeout_s=20.0)
        endpoint = discover_mcp_endpoint(base_url)
        ok(f"Discovered MCP endpoint: {endpoint}")

        client = MCPHttpClient(endpoint)
        try:
            init = client.initialize()
            ok(f"Initialized MCP session using protocol {init['result']['protocolVersion']}")

            tools = client.list_tools()
            tool_names = {tool["name"] for tool in tools}
            if "listaccounts" not in tool_names or "createtransfer" not in tool_names:
                raise RuntimeError(f"Expected banking tools missing from tools/list: {sorted(tool_names)}")
            ok(f"tools/list returned {len(tools)} tools")

            accounts_result = client.call_tool(
                "listaccounts",
                {"account_type": "chequing", "status": "active"},
            )
            if "structuredContent" not in accounts_result:
                raise RuntimeError(f"Unexpected listaccounts response shape: {accounts_result}")
            ok("listaccounts completed over MCP HTTP mode")

            transfer_result = client.call_tool(
                "createtransfer",
                {
                    "from_account_id": FROM_ACCOUNT_ID,
                    "to_account_id": TO_ACCOUNT_ID,
                    "amount_cad": 42.5,
                    "memo": "HTTP smoke test",
                },
            )
            if "structuredContent" not in transfer_result:
                raise RuntimeError(f"Unexpected createtransfer response shape: {transfer_result}")
            ok("createtransfer completed over MCP HTTP mode")
        finally:
            client.close()
    finally:
        mock_server.shutdown()
        mock_server.server_close()
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=5)

        output = ""
        if server_proc.stdout is not None:
            output = server_proc.stdout.read()
        if output.strip():
            print("\n[server log]")
            print(output.strip())

    ok("Banking MCP HTTP smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        fail(str(exc))
        raise SystemExit(1)
