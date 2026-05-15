#!/usr/bin/env python3
"""
validate_spec.py — Phase 1 Step 1
Validates an OpenAPI spec file (YAML or JSON) for structural correctness
and MCP generation readiness.

Usage:
    python validate_spec.py <spec_file>
    python validate_spec.py <spec_file> --strict
    python validate_spec.py <spec_file> --output report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────
# Colour helpers (no deps)
# ──────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD  = "\033[1m"
RED   = "\033[91m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
CYAN  = "\033[96m"
DIM   = "\033[2m"

def ok(msg):  print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg):print(f"  {YELLOW}⚠{RESET}  {msg}")
def err(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg):print(f"  {CYAN}ℹ{RESET} {msg}")
def h1(msg):  print(f"\n{BOLD}{msg}{RESET}")
def h2(msg):  print(f"\n{CYAN}{msg}{RESET}")
def rule():   print(f"  {DIM}{'─'*60}{RESET}")


# ──────────────────────────────────────────────────────────────
# Dependency bootstrap
# ──────────────────────────────────────────────────────────────

def ensure_deps():
    """Install required packages if missing."""
    required = {
        "yaml":                 "pyyaml",
        "openapi_spec_validator":"openapi-spec-validator",
        "jsonschema":            "jsonschema",
    }
    missing = []
    for module, pkg in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"{YELLOW}Installing missing packages: {', '.join(missing)}{RESET}")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing
        )
    return True


# ──────────────────────────────────────────────────────────────
# Spec loader
# ──────────────────────────────────────────────────────────────

def load_spec(path: Path) -> dict[str, Any]:
    import yaml
    with open(path) as f:
        content = f.read()
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(content)
    return json.loads(content)


# ──────────────────────────────────────────────────────────────
# Structural validation
# ──────────────────────────────────────────────────────────────

def validate_structure(spec: dict) -> list[dict]:
    """Run openapi-spec-validator structural checks."""
    from openapi_spec_validator import validate
    from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

    errors = []
    version = spec.get("openapi", "")

    if not version:
        errors.append({
            "level": "error",
            "message": "Missing 'openapi' version field. Must be '3.0.x' or '3.1.x'.",
            "path": "openapi"
        })
        return errors

    if not (version.startswith("3.0") or version.startswith("3.1")):
        errors.append({
            "level": "error",
            "message": f"Unsupported OpenAPI version: '{version}'. Require 3.0.x or 3.1.x",
            "path": "openapi"
        })
        return errors

    try:
        validate(spec)
        # validate() raises on first error; if it returns cleanly the spec is valid
    except OpenAPIValidationError as e:
        errors.append({
            "level": "error",
            "message": str(e).split("\n")[0][:300],
            "path": "root"
        })
    except Exception as e:
        errors.append({"level": "error", "message": f"Validation library error: {e}", "path": "root"})

    return errors


# ──────────────────────────────────────────────────────────────
# Quality / MCP-readiness checks
# ──────────────────────────────────────────────────────────────

def check_quality(spec: dict) -> list[dict]:
    """Run quality, MCP-readiness, and vacuum-style lint checks."""
    issues = []

    def issue(level, message, path="", fix=""):
        issues.append({"level": level, "message": message, "path": path, "fix": fix})

    # ── Info block ──────────────────────────────────────────
    info_block = spec.get("info", {})
    if not info_block.get("title"):
        issue("warning", "Missing info.title — MCP server will be unnamed",
              "info.title", "Add a descriptive title to info.title")
    if not info_block.get("description"):
        issue("warning", "Missing info.description — tools will lack context",
              "info.description", "Add a description explaining what this API does")
    if not info_block.get("version"):
        issue("warning", "Missing info.version", "info.version", "Add a version string e.g. '1.0.0'")

    # ── Servers ─────────────────────────────────────────────
    servers = spec.get("servers", [])
    if not servers:
        issue("warning",
              "No servers defined — BASE_URL will be empty in the generated .env",
              "servers",
              "Add at least one server with a 'url' field, e.g. https://api.example.com")

    # ── Paths ────────────────────────────────────────────────
    paths = spec.get("paths", {})
    if not paths:
        issue("error", "No paths defined — nothing to generate tools from", "paths")
        return issues

    components      = spec.get("components", {})
    sec_schemes     = components.get("securitySchemes", {})
    global_sec      = spec.get("security", [])
    defined_schemes = set(sec_schemes.keys())

    operation_ids: list[str] = []

    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Path-level parameters (inherited by all operations in this path)
        path_level_params = [
            p for p in path_item.get("parameters", [])
            if isinstance(p, dict)
        ]

        # Extract path template variables: /users/{userId}/items/{itemId}
        path_template_vars: set[str] = set(re.findall(r'\{([^}]+)\}', path_str))

        for method in ("get","post","put","patch","delete","head","options","trace"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue

            op_id   = op.get("operationId")
            op_path = f"paths.{path_str}.{method}"

            # Merge path-level + op-level parameters (op-level overrides by name)
            op_params_raw = [p for p in op.get("parameters", []) if isinstance(p, dict)]
            # Detect unresolved external $ref params BEFORE name-filtering
            all_raw_params = path_level_params + op_params_raw
            has_unresolved_refs = any("$ref" in p and "name" not in p for p in all_raw_params)
            by_name: dict[str, dict] = {p["name"]: p for p in path_level_params if "name" in p}
            for p in op_params_raw:
                if "name" in p:
                    by_name[p["name"]] = p
            all_params = list(by_name.values())

            # ── Existing checks ─────────────────────────────
            if not op_id:
                issue("warning",
                      f"Missing operationId on {method.upper()} {path_str} — a name will be auto-generated",
                      op_path,
                      "Add a unique operationId to each operation for better tool naming")
            else:
                if op_id in operation_ids:
                    issue("error", f"Duplicate operationId '{op_id}'", op_path,
                          "Each operationId must be unique across the entire spec")
                operation_ids.append(op_id)

            if not op.get("summary") and not op.get("description"):
                issue("warning",
                      f"No summary/description on {method.upper()} {path_str} — tool will be undocumented",
                      op_path,
                      "Add a summary or description; these become the MCP tool description")

            responses = op.get("responses", {})
            if not responses:
                issue("warning", f"No responses defined on {method.upper()} {path_str}",
                      op_path + ".responses")

            # ── Vacuum-style lint rules ──────────────────────

            # Tags
            if not op.get("tags"):
                issue("warning",
                      f"No tags on {method.upper()} {path_str} — tools won't be grouped in MCP clients",
                      op_path,
                      "Add one or more tags to group related operations")

            # Deprecated marker
            if op.get("deprecated"):
                issue("warning",
                      f"Operation '{op_id or method.upper() + ' ' + path_str}' is marked deprecated — consider excluding it",
                      op_path,
                      "Remove or replace deprecated operations before generating an MCP server")

            # Summary length
            summary_text = op.get("summary", "")
            if summary_text and len(summary_text) > 120:
                issue("warning",
                      f"Summary too long ({len(summary_text)} chars) on {method.upper()} {path_str} — may be truncated in MCP clients",
                      op_path + ".summary",
                      "Keep summaries under 120 characters for best MCP client compatibility")

            # Path parameter consistency: template vars vs declared params
            # Skip if any parameter is an unresolved external $ref (name not resolvable)
            if not has_unresolved_refs:
                declared_path_params = {p["name"] for p in all_params if p.get("in") == "path"}
                for tvar in path_template_vars:
                    if tvar not in declared_path_params:
                        issue("error",
                              f"Path template variable '{{{tvar}}}' in '{path_str}' is not declared in parameters",
                              op_path + ".parameters",
                              f"Add a parameter with name='{tvar}', in='path', required=true")
                for pname in declared_path_params:
                    if pname not in path_template_vars:
                        issue("warning",
                              f"Parameter '{pname}' declared as in='path' but '{{{pname}}}' is not in the path template '{path_str}'",
                              op_path + ".parameters",
                              "Remove this parameter or add the placeholder to the path string")

            # Too many parameters
            body = op.get("requestBody", {})
            body_count = 1 if body else 0
            total_param_count = len(all_params) + body_count
            if total_param_count > 10:
                issue("warning",
                      f"{method.upper()} {path_str} has {total_param_count} parameters — MCP tool signature will be unwieldy",
                      op_path,
                      "Consider splitting into smaller operations or grouping parameters into a request body object")

            # Per-parameter quality (skip unresolved $ref params)
            for p in all_params:
                if "$ref" in p and "name" not in p:
                    continue  # can't inspect external $ref params
                pname    = p.get("name", "(unnamed)")
                ploc     = p.get("in", "unknown")
                ppth     = f"{op_path}.parameters[{pname}]"
                if not p.get("schema"):
                    issue("warning",
                          f"Parameter '{pname}' ({ploc}) on {method.upper()} {path_str} has no schema — type defaults to Any",
                          ppth,
                          "Add a 'schema' block with at least a 'type' field")
                if not p.get("description"):
                    issue("warning",
                          f"Parameter '{pname}' ({ploc}) on {method.upper()} {path_str} has no description",
                          ppth,
                          "Add a 'description' to document what this parameter expects")

            # Response coverage
            has_4xx_or_default = any(
                str(code).startswith("4") or str(code) == "default"
                for code in responses
            )
            if responses and not has_4xx_or_default:
                issue("warning",
                      f"No 4xx/default response on {method.upper()} {path_str} — error cases are undocumented",
                      op_path + ".responses",
                      "Add at least a 400 or 'default' response to document error handling")

            for status_code, resp_def in responses.items():
                code_str = str(status_code)
                # 204/205 legitimately have no response body — skip them
                if code_str in ("204", "205"):
                    continue
                if isinstance(resp_def, dict) and code_str.startswith("2"):
                    if not resp_def.get("content"):
                        issue("warning",
                              f"2xx response '{status_code}' on {method.upper()} {path_str} has no content/schema — tool will return opaque text",
                              f"{op_path}.responses.{status_code}",
                              "Add a content block with an application/json schema for the success response")

            # Security reference integrity
            op_security = op.get("security")
            effective_security = op_security if op_security is not None else global_sec
            for sec_req in (effective_security or []):
                if not isinstance(sec_req, dict):
                    continue
                for scheme_ref in sec_req.keys():
                    if defined_schemes and scheme_ref not in defined_schemes:
                        issue("error",
                              f"Operation '{op_id or method.upper() + ' ' + path_str}' references undefined security scheme '{scheme_ref}'",
                              f"{op_path}.security",
                              f"Define '{scheme_ref}' in components.securitySchemes or correct the reference")

    # ── Security scheme quality ──────────────────────────────
    if sec_schemes:
        for scheme_name, scheme_def in sec_schemes.items():
            if not isinstance(scheme_def, dict):
                continue
            scheme_type = scheme_def.get("type", "")
            if scheme_type == "apiKey":
                loc = scheme_def.get("in", "")
                if loc not in ("header", "query", "cookie"):
                    issue("warning",
                          f"securityScheme '{scheme_name}': apiKey 'in' should be header, query, or cookie (got '{loc}')",
                          f"components.securitySchemes.{scheme_name}",
                          "Set 'in' to 'header', 'query', or 'cookie'")

    # ── Broken $ref ──────────────────────────────────────────
    spec_str = json.dumps(spec)
    refs     = re.findall(r'"#/([^"]+)"', spec_str)
    seen_broken: set[str] = set()
    for ref in refs:
        parts = ref.split("/")
        node  = spec
        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                if ref not in seen_broken:
                    seen_broken.add(ref)
                    issue("error", f"Broken $ref: #/{ref}", "$ref",
                          "Ensure all $ref paths point to existing definitions")
                break

    # ── Description security (injection patterns) ────────────
    desc_fields = re.findall(r'"description"\s*:\s*"([^"]*)"', spec_str)
    for desc in desc_fields:
        if "<script" in desc.lower() or "javascript:" in desc.lower():
            issue("error", "Potential script injection found in a description field",
                  "description", "Remove script tags and javascript: URIs from descriptions")
        if "eval(" in desc:
            issue("warning", "eval() found in a description field",
                  "description", "Avoid eval() patterns in documentation strings")

    return issues


# ──────────────────────────────────────────────────────────────
# Summary printer
# ──────────────────────────────────────────────────────────────

def print_summary(spec: dict, struct_errors: list, quality_issues: list, strict: bool) -> bool:
    """Print full validation report. Returns True if generation can proceed."""
    h1("═══ MCP Forge — OpenAPI Spec Validator ═══")

    # Basic spec info
    info_block = spec.get("info", {})
    h2("Spec Overview")
    print(f"  Title    : {info_block.get('title', '(none)')}")
    print(f"  Version  : {info_block.get('version', '(none)')}")
    print(f"  OpenAPI  : {spec.get('openapi', '(none)')}")

    paths = spec.get("paths", {})
    op_count = sum(
        1
        for p in paths.values() if isinstance(p, dict)
        for m in ("get","post","put","patch","delete","head","options","trace")
        if m in p
    )
    print(f"  Paths    : {len(paths)}")
    print(f"  Operations: {op_count}")

    sec_schemes = spec.get("components", {}).get("securitySchemes", {})
    if sec_schemes:
        print(f"  Auth     : {', '.join(sec_schemes.keys())}")
    else:
        print("  Auth     : none declared")

    # ── Structural errors ────────────────────────────────────
    h2("Structural Validation (openapi-spec-validator)")
    if not struct_errors:
        ok("Specification is structurally valid")
    else:
        for e in struct_errors:
            err(f"[{e['path']}]  {e['message']}")

    # ── Quality issues ───────────────────────────────────────
    errors   = [i for i in quality_issues if i["level"] == "error"]
    warnings = [i for i in quality_issues if i["level"] == "warning"]

    h2("Quality & MCP-Readiness Checks")
    if not quality_issues:
        ok("All quality checks passed")
    else:
        if errors:
            print(f"\n  {RED}{BOLD}Errors ({len(errors)}){RESET}")
            rule()
            for e in errors:
                err(f"{e['message']}")
                if e.get("path"): print(f"       {DIM}path: {e['path']}{RESET}")
                if e.get("fix"):  print(f"       {CYAN}fix:  {e['fix']}{RESET}")
        if warnings:
            print(f"\n  {YELLOW}{BOLD}Warnings ({len(warnings)}){RESET}")
            rule()
            for w in warnings:
                warn(f"{w['message']}")
                if w.get("path"): print(f"       {DIM}path: {w['path']}{RESET}")
                if w.get("fix"):  print(f"       {CYAN}fix:  {w['fix']}{RESET}")
        print(f"\n  {DIM}({len(errors)} error(s), {len(warnings)} warning(s) across {len(quality_issues)} total checks){RESET}")

    # ── Final verdict ────────────────────────────────────────
    h2("Result")
    blocking = struct_errors + errors  # warnings never block unless --strict
    if strict:
        blocking = struct_errors + errors + warnings

    can_generate = len(blocking) == 0
    if can_generate:
        print(f"\n  {GREEN}{BOLD}✓ PASS{RESET}  Specification is valid — ready for generation\n")
    else:
        count = len(blocking)
        label = "issue" if count == 1 else "issues"
        print(f"\n  {RED}{BOLD}✗ FAIL{RESET}  {count} blocking {label} found — fix before generating\n")
        if not strict and warnings:
            print(f"  {DIM}Tip: run with --strict to also treat warnings as blocking{RESET}\n")

    return can_generate


# ──────────────────────────────────────────────────────────────
# CLI entry
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate an OpenAPI 3.x spec for MCP server generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validate_spec.py my_api.yaml
  python validate_spec.py my_api.json --strict
  python validate_spec.py my_api.yaml --output report.json
        """
    )
    parser.add_argument("spec_file", help="Path to OpenAPI spec (YAML or JSON)")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as blocking errors")
    parser.add_argument("--output", metavar="FILE",
                        help="Write JSON validation report to this file")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress output except final PASS/FAIL line")
    args = parser.parse_args()

    ensure_deps()

    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        print(f"{RED}Error: file not found: {spec_path}{RESET}")
        sys.exit(2)

    try:
        spec = load_spec(spec_path)
    except Exception as e:
        print(f"{RED}Error: cannot parse spec file: {e}{RESET}")
        sys.exit(2)

    struct_errors  = validate_structure(spec)
    quality_issues = check_quality(spec)

    if not args.quiet:
        can_generate = print_summary(spec, struct_errors, quality_issues, args.strict)
    else:
        blocking = struct_errors + [i for i in quality_issues if i["level"] == "error"]
        if args.strict:
            blocking += [i for i in quality_issues if i["level"] == "warning"]
        can_generate = len(blocking) == 0
        status = "PASS" if can_generate else "FAIL"
        print(status)

    if args.output:
        report = {
            "spec_file": str(spec_path),
            "can_generate": can_generate,
            "structural_errors": struct_errors,
            "quality_issues": quality_issues,
        }
        Path(args.output).write_text(json.dumps(report, indent=2))
        if not args.quiet:
            print(f"  Report written to: {args.output}\n")

    sys.exit(0 if can_generate else 1)


if __name__ == "__main__":
    main()
