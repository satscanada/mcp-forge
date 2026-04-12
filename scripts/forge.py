#!/usr/bin/env python3
"""
forge.py — MCP Forge Pipeline (Phase 1 CLI)
Runs the full pipeline: validate → generate → local deployment instructions.

Usage:
    python forge.py <spec_file>
    python forge.py <spec_file> --output ./my-server
    python forge.py <spec_file> --api-key --name my_server --strict
    python forge.py <spec_file> --skip-validation --output ./out
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RESET = "\033[0m"; BOLD = "\033[1m"; RED = "\033[91m"
GREEN = "\033[92m"; YELLOW = "\033[93m"; CYAN = "\033[96m"
DIM = "\033[2m"; BLUE = "\033[94m"

SCRIPTS_DIR = Path(__file__).parent


def banner():
    print(f"""
{CYAN}{BOLD}
  ███╗   ███╗ ██████╗██████╗     ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
  ████╗ ████║██╔════╝██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
  ██╔████╔██║██║     ██████╔╝    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗
  ██║╚██╔╝██║██║     ██╔═══╝     ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
  ██║ ╚═╝ ██║╚██████╗██║         ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
  ╚═╝     ╚═╝ ╚═════╝╚═╝         ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
{RESET}{DIM}  OpenAPI → Production MCP Server — Phase 1 CLI{RESET}
""")


def step_header(n: int, title: str):
    bar = "─" * 58
    print(f"\n{CYAN}┌{bar}┐{RESET}")
    print(f"{CYAN}│{RESET}  {BOLD}Step {n}: {title}{RESET}")
    print(f"{CYAN}└{bar}┘{RESET}")


def print_deployment_guide(output_dir: Path, server_name: str, has_auth: bool):
    print(f"""
{CYAN}{BOLD}┌──────────────────────────────────────────────────────────┐
│  Step 3: Local Deployment                                │
└──────────────────────────────────────────────────────────┘{RESET}

  {BOLD}1. Enter the server directory{RESET}
     cd {output_dir}

  {BOLD}2. Create a virtual environment{RESET}
     python -m venv .venv
     source .venv/bin/activate    {DIM}# Linux / macOS{RESET}
     {DIM}# .venv\\Scripts\\activate  # Windows{RESET}

  {BOLD}3. Install dependencies{RESET}
     pip install -r requirements.txt
""")

    if has_auth:
        print(f"""  {BOLD}4. Configure authentication{RESET}
     Edit {output_dir}/.env and set:
       {YELLOW}API_KEY=<your_api_key>{RESET}
""")

    print(f"""  {BOLD}{'5' if has_auth else '4'}. Run the server{RESET}

     {GREEN}# stdio — for Claude Desktop / Claude Code{RESET}
     python server.py

     {GREEN}# SSE — for network/Docker deployments{RESET}
     python server.py --transport sse --port 8000

     {GREEN}# Streamable HTTP — newer MCP clients{RESET}
     python server.py --transport streamable-http --port 8000

  {BOLD}Transport guide:{RESET}
  ┌────────────────────┬───────────────────────────────────────┐
  │ stdio (default)    │ Local MCP clients (Claude Desktop,    │
  │                    │ Claude Code, Cursor)                   │
  │ SSE                │ Remote or Docker-based deployments    │
  │ streamable-http    │ Newer HTTP-streaming MCP clients      │
  └────────────────────┴───────────────────────────────────────┘

  {BOLD}MCP client config{RESET}
  Copy .mcp.json into your MCP client's configuration dir.
  Adjust the path to server.py as needed.

  {BOLD}Docker{RESET}
  docker build -t {server_name} {output_dir}
  docker run -p 8000:8000 --env-file {output_dir}/.env {server_name}
""")


def run_step(cmd: list[str], label: str) -> tuple[int, str, str]:
    """Run a subprocess step, streaming output. Returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd,
        capture_output=False,  # let output stream to terminal
        text=True,
    )
    return proc.returncode, "", ""


def main():
    parser = argparse.ArgumentParser(
        description="MCP Forge Pipeline — OpenAPI spec → production FastMCP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline with validation
  python forge.py my_api.yaml

  # Custom output dir and server name
  python forge.py petstore.yaml --output ./petstore-server --name petstore

  # Force API key auth + strict validation
  python forge.py my_api.yaml --api-key --strict

  # Skip validation (if you've already run it)
  python forge.py my_api.yaml --skip-validation --output ./out
        """
    )
    parser.add_argument("spec_file",
                        help="Path to OpenAPI 3.x spec (YAML or JSON)")
    parser.add_argument("--output", "-o", metavar="DIR",
                        help="Output directory for generated server (default: ./<server_name>)")
    parser.add_argument("--name", metavar="NAME",
                        help="Server name override (default: derived from spec title)")
    parser.add_argument("--api-key", action="store_true",
                        help="Force API Key auth even if spec doesn't declare it")
    parser.add_argument("--strict", action="store_true",
                        help="Treat validation warnings as blocking errors")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Skip the validation step (proceed directly to generation)")
    parser.add_argument("--report", metavar="FILE",
                        help="Write validation report JSON to this file")
    args = parser.parse_args()

    banner()

    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        print(f"  {RED}✗ Spec file not found: {spec_path}{RESET}")
        sys.exit(1)

    total_start = time.monotonic()

    # ─────────────────────────────────────────────────
    # Step 1: Validation
    # ─────────────────────────────────────────────────
    if not args.skip_validation:
        step_header(1, "OpenAPI Spec Validation")

        validate_cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "validate_spec.py"),
            str(spec_path),
        ]
        if args.strict:
            validate_cmd.append("--strict")
        if args.report:
            validate_cmd += ["--output", args.report]

        t0 = time.monotonic()
        result = subprocess.run(validate_cmd)
        elapsed = time.monotonic() - t0

        if result.returncode != 0:
            print(f"\n  {RED}{BOLD}Pipeline halted — fix validation errors before generating.{RESET}")
            print(f"  {DIM}Run validate_spec.py --strict for exhaustive checks.{RESET}\n")
            sys.exit(1)

        print(f"\n  {DIM}Validation: {elapsed:.1f}s{RESET}")
    else:
        print(f"\n  {YELLOW}⚠  Validation skipped (--skip-validation){RESET}")

    # ─────────────────────────────────────────────────
    # Step 2: Generation
    # ─────────────────────────────────────────────────
    step_header(2, "MCP Server Generation")

    # Determine server name for output dir default
    server_name = args.name
    if not server_name:
        try:
            import sys as _sys
            _sys.path.insert(0, str(SCRIPTS_DIR))
            from generate_server import load_spec, slugify, ensure_deps
            ensure_deps()
            spec = load_spec(spec_path)
            server_name = slugify(spec.get("info", {}).get("title", "mcp_server"))
        except Exception:
            server_name = "mcp_server"

    output_dir = Path(args.output) if args.output else Path(server_name)

    generate_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "generate_server.py"),
        str(spec_path),
        "--output", str(output_dir),
        "--name", server_name,
    ]
    if args.api_key:
        generate_cmd.append("--api-key")

    t0 = time.monotonic()
    result = subprocess.run(generate_cmd)
    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        print(f"\n  {RED}{BOLD}Pipeline halted — generation failed.{RESET}\n")
        sys.exit(1)

    print(f"\n  {DIM}Generation: {elapsed:.1f}s{RESET}")

    # ─────────────────────────────────────────────────
    # Step 3: Deployment guide
    # ─────────────────────────────────────────────────
    # Detect auth from generated .env
    has_auth = False
    env_file = output_dir / ".env"
    if env_file.exists():
        env_content = env_file.read_text()
        has_auth = "API_KEY=" in env_content

    print_deployment_guide(output_dir, server_name, has_auth)

    total = time.monotonic() - total_start
    print(f"  {GREEN}{BOLD}✓ MCP Forge pipeline complete{RESET}  {DIM}({total:.1f}s total){RESET}\n")


if __name__ == "__main__":
    main()
