#!/usr/bin/env python3
"""
demo.py — MCP Forge CLI: End-to-End Demo
=========================================
Runs the full pipeline against the bundled Retail Banking API spec:

  Step 1 — Validate the OpenAPI spec
  Step 2 — Generate the FastMCP server
  Step 3 — Syntax-check all generated Python files
  Step 4 — Print local deployment guide
  Step 5 — Show a preview of the generated server tools

Usage:
    python demo.py                          # default output: ./output/banking_server
    python demo.py --output ./my-out        # custom output dir
    python demo.py --clean                  # delete output dir first
    python demo.py --skip-preview           # skip the tool preview at the end
    python demo.py --spec path/to/spec.yaml # use your own spec
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Colour helpers ────────────────────────────────────────────────────────────
RESET  = "\033[0m";  BOLD   = "\033[1m";  DIM    = "\033[2m"
RED    = "\033[91m"; GREEN  = "\033[92m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BLUE   = "\033[94m"; WHITE  = "\033[97m"

def ok(m):    print(f"  {GREEN}✓{RESET}  {m}")
def warn(m):  print(f"  {YELLOW}⚠{RESET}  {m}")
def fail(m):  print(f"  {RED}✗{RESET}  {m}")
def info(m):  print(f"  {CYAN}ℹ{RESET}  {m}")
def h1(m):    print(f"\n{BOLD}{WHITE}{m}{RESET}")
def h2(m):    print(f"\n{CYAN}{BOLD}{m}{RESET}")
def rule(ch="─", n=62): print(f"  {DIM}{ch * n}{RESET}")
def blank():  print()


SCRIPTS_DIR  = Path(__file__).parent / "scripts"
EXAMPLES_DIR = Path(__file__).parent / "examples"
DEFAULT_SPEC = EXAMPLES_DIR / "banking_api.yaml"
DEFAULT_OUT  = Path(__file__).parent / "output" / "banking_server"


# ── Banner ────────────────────────────────────────────────────────────────────

def banner():
    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ███╗   ███╗ ██████╗██████╗     ███████╗ ██████╗ ██████╗  ║
║   ████╗ ████║██╔════╝██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗ ║
║   ██╔████╔██║██║     ██████╔╝    █████╗  ██║   ██║██████╔╝ ║
║   ██║╚██╔╝██║██║     ██╔═══╝     ██╔══╝  ██║   ██║██╔══██╗ ║
║   ██║ ╚═╝ ██║╚██████╗██║         ██║     ╚██████╔╝██║  ██║ ║
║   ╚═╝     ╚═╝ ╚═════╝╚═╝         ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ║
║                                                              ║
║          OpenAPI → Production MCP Server  •  Demo           ║
╚══════════════════════════════════════════════════════════════╝{RESET}
""")


# ── Step runners ──────────────────────────────────────────────────────────────

def step_box(n: int, title: str):
    rule("═")
    print(f"  {BOLD}{CYAN}STEP {n}{RESET}  {BOLD}{title}{RESET}")
    rule("═")


def run_validate(spec_path: Path) -> bool:
    """Run validate_spec.py and return True on PASS."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "validate_spec.py"), str(spec_path)],
        text=True,
    )
    return result.returncode == 0


def run_generate(spec_path: Path, output_dir: Path, server_name: str) -> bool:
    """Run generate_server.py and return True on success."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "generate_server.py"),
            str(spec_path),
            "--output", str(output_dir),
            "--name", server_name,
        ],
        text=True,
    )
    return result.returncode == 0


def syntax_check(output_dir: Path) -> dict[str, bool]:
    """Parse all generated .py files with ast.parse. Returns {filename: ok}."""
    results = {}
    for fname in ["server.py", "_auth.py", "_models.py", "_validators.py"]:
        fpath = output_dir / fname
        if not fpath.exists():
            continue
        try:
            ast.parse(fpath.read_text())
            results[fname] = True
        except SyntaxError as e:
            results[fname] = False
            fail(f"Syntax error in {fname}: {e}")
    return results


def count_tools(output_dir: Path) -> list[dict]:
    """Extract tool names and docstrings from generated server.py."""
    server_py = output_dir / "server.py"
    if not server_py.exists():
        return []

    tools = []
    lines = server_py.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "@mcp.tool()":
            # Next line is the function def
            j = i + 1
            if j < len(lines):
                func_line = lines[j].strip()
                if func_line.startswith("async def "):
                    name = func_line[len("async def "):].split("(")[0].strip()
                    # Look for docstring
                    doc = ""
                    k = j + 1
                    while k < len(lines):
                        dl = lines[k].strip()
                        if dl.startswith('"""'):
                            doc = dl.strip('"""').strip()
                            break
                        if dl and not dl.startswith("#"):
                            break
                        k += 1
                    tools.append({"name": name, "doc": doc})
        i += 1
    return tools


def show_file_tree(output_dir: Path):
    """Print the generated file tree with sizes."""
    blank()
    print(f"  {BOLD}Generated files:{RESET}")
    rule()
    for fpath in sorted(output_dir.iterdir()):
        size = fpath.stat().st_size
        if size > 1024:
            size_str = f"{size // 1024:>4}KB"
        else:
            size_str = f"{size:>4}B "
        icon = {
            ".py": "🐍", ".env": "⚙ ", ".json": "{}", ".txt": "📄",
            ".md": "📝", ""    : "🐳",
        }.get(fpath.suffix, "  ")
        print(f"    {icon}  {fpath.name:<22} {DIM}{size_str}{RESET}")


def show_tool_preview(tools: list[dict]):
    """Print the list of generated MCP tools."""
    blank()
    print(f"  {BOLD}MCP Tools generated ({len(tools)}):{RESET}")
    rule()
    for t in tools:
        doc_preview = (t["doc"][:65] + "…") if len(t["doc"]) > 65 else t["doc"]
        print(f"    {GREEN}●{RESET} {BOLD}{t['name']}{RESET}")
        if doc_preview:
            print(f"      {DIM}{doc_preview}{RESET}")
    print(f"\n    {CYAN}Validation:{RESET} generated tools instantiate `_models.*Params` before each HTTP call")
    blank()


def show_env_preview(output_dir: Path):
    """Show the key .env settings (redact any secret-looking values)."""
    env_file = output_dir / ".env"
    if not env_file.exists():
        return
    blank()
    print(f"  {BOLD}.env configuration keys:{RESET}")
    rule()
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
            display = f"{YELLOW}(set this){RESET}" if not val else f"{RED}[SET]{RESET}"
        else:
            display = f"{DIM}{val}{RESET}" if val else f"{DIM}(empty){RESET}"
        print(f"    {CYAN}{key:<40}{RESET} {display}")


def show_deployment_guide(output_dir: Path, server_name: str):
    """Print the local deployment steps."""
    setup_script = SCRIPTS_DIR / "setup_generated_server.sh"
    blank()
    print(f"  {BOLD}Local deployment steps:{RESET}")
    rule()

    steps = [
        ("Bootstrap the generated server",
         f"{setup_script} {output_dir}"),
        ("Enter server directory",
         f"cd {output_dir}"),
        ("Activate the virtual environment",
         "source .venv/bin/activate"),
        ("Configure credentials",
         "# Edit .env — set BASE_URL and API_KEY"),
        ("Run (stdio — Claude Desktop / Code / Cursor)",
         "python server.py"),
        ("Run (SSE — network / Docker)",
         "python server.py --transport sse --port 8000"),
        ("Run (Docker)",
         f"docker build -t {server_name} . && docker run -p 8000:8000 --env-file .env {server_name}"),
    ]
    for i, (label, cmd) in enumerate(steps, 1):
        print(f"\n  {BOLD}{i}. {label}{RESET}")
        print(f"     {GREEN}{cmd}{RESET}")

    blank()
    print(f"  {BOLD}MCP client config (.mcp.json):{RESET}")
    mcp_json = output_dir / ".mcp.json"
    if mcp_json.exists():
        content = json.loads(mcp_json.read_text())
        print(f"  {DIM}{json.dumps(content, indent=4)}{RESET}")

    blank()
    print(f"  {BOLD}Transport reference:{RESET}")
    rule()
    rows = [
        ("stdio",            "Local clients — Claude Desktop, Claude Code, Cursor"),
        ("sse",              "Remote / Docker deployments"),
        ("streamable-http",  "Newer HTTP-streaming MCP clients"),
    ]
    for mode, use in rows:
        print(f"    {CYAN}{mode:<20}{RESET}  {use}")


# ── Main demo ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MCP Forge CLI — End-to-End Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py
  python demo.py --output ./my-banking-server
  python demo.py --spec path/to/my_api.yaml --output ./my-server
  python demo.py --clean          # wipe output dir first
  python demo.py --skip-preview   # no tool listing at the end
        """
    )
    parser.add_argument("--spec",         default=str(DEFAULT_SPEC),
                        help=f"OpenAPI spec to use (default: examples/banking_api.yaml)")
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUT),
                        help=f"Output directory (default: output/banking_server)")
    parser.add_argument("--clean",        action="store_true",
                        help="Delete output directory before generating")
    parser.add_argument("--skip-preview", action="store_true",
                        help="Skip the tool listing preview")
    args = parser.parse_args()

    spec_path  = Path(args.spec)
    output_dir = Path(args.output)
    server_name = output_dir.name

    banner()

    # ── Pre-flight ──────────────────────────────────────────────────────────
    if not spec_path.exists():
        fail(f"Spec file not found: {spec_path}")
        sys.exit(1)

    if args.clean and output_dir.exists():
        info(f"Removing existing output: {output_dir}")
        shutil.rmtree(output_dir)

    print(f"  {BOLD}Spec file :{RESET}  {spec_path}")
    print(f"  {BOLD}Output dir:{RESET}  {output_dir}")
    print(f"  {BOLD}Server name:{RESET} {server_name}")
    blank()

    total_start = time.monotonic()
    passed = True

    # ── STEP 1: Validate ────────────────────────────────────────────────────
    step_box(1, "OpenAPI Spec Validation")
    t0 = time.monotonic()
    ok_validate = run_validate(spec_path)
    elapsed = time.monotonic() - t0

    if ok_validate:
        blank()
        ok(f"Validation passed  {DIM}({elapsed:.1f}s){RESET}")
    else:
        blank()
        fail("Validation failed — fix errors above before generating.")
        fail("Run:  python scripts/validate_spec.py <spec> --strict  for full details")
        sys.exit(1)

    # ── STEP 2: Generate ────────────────────────────────────────────────────
    blank()
    step_box(2, "FastMCP Server Generation")
    t0 = time.monotonic()
    ok_generate = run_generate(spec_path, output_dir, server_name)
    elapsed = time.monotonic() - t0

    if ok_generate:
        blank()
        ok(f"Generation passed  {DIM}({elapsed:.1f}s){RESET}")
        show_file_tree(output_dir)
    else:
        blank()
        fail("Generation failed — see errors above.")
        sys.exit(1)

    # ── STEP 3: Syntax check ────────────────────────────────────────────────
    blank()
    step_box(3, "Syntax Verification (ast.parse)")
    syntax_results = syntax_check(output_dir)
    all_ok = True
    for fname, result in syntax_results.items():
        if result:
            ok(f"{fname}")
        else:
            fail(f"{fname}  ← syntax error")
            all_ok = False
            passed = False

    if not all_ok:
        blank()
        fail("Generated files contain syntax errors — please file a bug.")
        sys.exit(1)
    else:
        blank()
        ok(f"All {len(syntax_results)} Python files are syntactically valid")

    # ── STEP 4: Tool preview ────────────────────────────────────────────────
    if not args.skip_preview:
        blank()
        step_box(4, "Generated Tools Preview")
        tools = count_tools(output_dir)
        if tools:
            show_tool_preview(tools)
        else:
            warn("No tools found in server.py (unexpected — check generate_server.py)")

        show_env_preview(output_dir)

    # ── STEP 5: Deployment guide ────────────────────────────────────────────
    blank()
    step_box(5, "Local Deployment Guide")
    show_deployment_guide(output_dir, server_name)

    # ── Final summary ───────────────────────────────────────────────────────
    total = time.monotonic() - total_start
    blank()
    rule("═")
    tools = count_tools(output_dir)
    setup_script = SCRIPTS_DIR / "setup_generated_server.sh"
    print(f"""
  {GREEN}{BOLD}✓  MCP Forge demo complete{RESET}

  {BOLD}Summary:{RESET}
    Spec          : {spec_path.name}
    Server name   : {server_name}
    Tools         : {len(tools)}
    Output        : {output_dir}
    Total time    : {total:.1f}s

  {BOLD}Next steps:{RESET}
    1.  {setup_script} {output_dir}
    2.  cd {output_dir}
    3.  source .venv/bin/activate
    4.  Edit .env — set {YELLOW}API_KEY=<your_key>{RESET} and {YELLOW}BASE_URL=<your_api_url>{RESET}
    5.  python server.py
""")
    rule("═")
    blank()


if __name__ == "__main__":
    main()
