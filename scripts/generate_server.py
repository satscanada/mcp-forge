#!/usr/bin/env python3
"""
generate_server.py — Phase 1 Step 2
Generates a production-ready FastMCP server from a validated OpenAPI 3.x spec.

Outputs:
  <output_dir>/
  ├── server.py          # FastMCP server with all tools
  ├── _models.py         # Pydantic request/response models
  ├── _validators.py     # Format validators (OAS Format Registry)
  ├── _auth.py           # API Key auth handler (if spec declares securitySchemes)
  ├── .env               # Configuration template
  ├── requirements.txt   # Python dependencies
  ├── Dockerfile         # Production Docker build
  ├── .mcp.json          # MCP client config template
  └── README.md          # Setup guide

Usage:
    python generate_server.py <spec_file> [--output DIR] [--api-key] [--name NAME]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from textwrap import dedent, indent
from typing import Any

RESET = "\033[0m"; BOLD = "\033[1m"; RED = "\033[91m"
GREEN = "\033[92m"; YELLOW = "\033[93m"; CYAN = "\033[96m"; DIM = "\033[2m"

def ok(m):   print(f"  {GREEN}✓{RESET} {m}")
def warn(m): print(f"  {YELLOW}⚠{RESET}  {m}")
def err(m):  print(f"  {RED}✗{RESET} {m}")
def h1(m):   print(f"\n{BOLD}{m}{RESET}")
def h2(m):   print(f"\n{CYAN}{m}{RESET}")


# ──────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────

def ensure_deps():
    required = {"yaml": "pyyaml"}
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])

def load_spec(path: Path) -> dict:
    import yaml
    with open(path) as f:
        content = f.read()
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(content)
    return json.loads(content)

def slugify(text: str) -> str:
    """Convert text to snake_case Python identifier."""
    text = re.sub(r"[^a-zA-Z0-9_]", "_", str(text))
    text = re.sub(r"_+", "_", text).strip("_")
    if text and text[0].isdigit():
        text = "op_" + text
    return text.lower() or "operation"

def python_type(schema: dict, required: bool = True) -> str:
    if not schema:
        return "Any"
    t = schema.get("type", "")
    fmt = schema.get("format", "")
    if t == "integer":  return "int"
    if t == "number":   return "float"
    if t == "boolean":  return "bool"
    if t == "array":
        items = schema.get("items", {})
        return f"list[{python_type(items)}]"
    if t == "object":   return "dict[str, Any]"
    return "str"

def resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref within the spec."""
    if not ref.startswith("#/"):
        return {}
    parts = ref[2:].split("/")
    node = spec
    for p in parts:
        p = p.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            node = node.get(p, {})
        else:
            return {}
    return node if isinstance(node, dict) else {}

def get_schema(spec: dict, schema_obj: dict) -> dict:
    if "$ref" in schema_obj:
        return resolve_ref(spec, schema_obj["$ref"])
    return schema_obj

def extract_operations(spec: dict) -> list[dict]:
    """Extract all operations from the spec paths."""
    ops = []
    paths = spec.get("paths", {})
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get","post","put","patch","delete","head","options","trace"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or slugify(f"{method}_{path_str}")
            params = []
            # Path-level params
            for p in path_item.get("parameters", []):
                if "$ref" in p:
                    p = resolve_ref(spec, p["$ref"])
                params.append(p)
            # Operation-level params (override path-level)
            op_param_names = set()
            for p in op.get("parameters", []):
                if "$ref" in p:
                    p = resolve_ref(spec, p["$ref"])
                params = [pp for pp in params if pp.get("name") != p.get("name")]
                params.append(p)
                op_param_names.add(p.get("name"))

            # Request body
            body = op.get("requestBody", {})
            if "$ref" in body:
                body = resolve_ref(spec, body["$ref"])

            ops.append({
                "operation_id": op_id,
                "method": method.upper(),
                "path": path_str,
                "summary": op.get("summary", ""),
                "description": op.get("description", ""),
                "parameters": params,
                "request_body": body,
                "responses": op.get("responses", {}),
                "security": op.get("security"),  # None = inherit global
                "tags": op.get("tags", []),
            })
    return ops

def detect_auth(spec: dict) -> dict:
    """Return auth info: {has_auth, schemes, global_security}."""
    schemes = spec.get("components", {}).get("securitySchemes", {})
    global_sec = spec.get("security", [])
    return {
        "has_auth": bool(schemes),
        "schemes": schemes,
        "global_security": global_sec,
    }


# ──────────────────────────────────────────────────────────────
# Code generators
# ──────────────────────────────────────────────────────────────

def gen_validators() -> str:
    """Generate _validators.py with OAS format validators."""
    return dedent('''\
        #!/usr/bin/env python3
        """
        _validators.py — Format validators for OAS Format Registry compliance.
        Covers integer, number, date/time, identifiers, network, text, encoding formats.
        """
        from __future__ import annotations
        import re, uuid, ipaddress, base64
        from datetime import date, datetime, time, timedelta
        from typing import Any
        from pydantic import BaseModel, ConfigDict

        # ── Base model classes ──────────────────────────────────────────────────────

        class StrictModel(BaseModel):
            """Rejects unknown/extra fields — used for strict-mode operations."""
            model_config = ConfigDict(
                frozen=True, extra="forbid",
                str_strip_whitespace=True, validate_assignment=True
            )

        class PermissiveModel(BaseModel):
            """Accepts additional fields — used for lenient response parsing."""
            model_config = ConfigDict(
                extra="allow",
                str_strip_whitespace=True, validate_assignment=True
            )

        # ── Format validators ──────────────────────────────────────────────────────

        _INT8_RANGE   = (-128, 127)
        _INT16_RANGE  = (-32768, 32767)
        _INT32_RANGE  = (-2**31, 2**31 - 1)
        _INT64_RANGE  = (-2**63, 2**63 - 1)
        _UINT8_RANGE  = (0, 255)
        _UINT16_RANGE = (0, 65535)
        _UINT32_RANGE = (0, 2**32 - 1)
        _UINT64_RANGE = (0, 2**64 - 1)

        def validate_int8(v):    assert _INT8_RANGE[0] <= int(v) <= _INT8_RANGE[1];    return int(v)
        def validate_int16(v):   assert _INT16_RANGE[0] <= int(v) <= _INT16_RANGE[1];  return int(v)
        def validate_int32(v):   assert _INT32_RANGE[0] <= int(v) <= _INT32_RANGE[1];  return int(v)
        def validate_int64(v):   assert _INT64_RANGE[0] <= int(v) <= _INT64_RANGE[1];  return int(v)
        def validate_uint8(v):   assert _UINT8_RANGE[0] <= int(v) <= _UINT8_RANGE[1];  return int(v)
        def validate_uint16(v):  assert _UINT16_RANGE[0] <= int(v) <= _UINT16_RANGE[1];return int(v)
        def validate_uint32(v):  assert _UINT32_RANGE[0] <= int(v) <= _UINT32_RANGE[1];return int(v)
        def validate_uint64(v):  assert _UINT64_RANGE[0] <= int(v) <= _UINT64_RANGE[1];return int(v)
        def validate_float(v):   return float(v)
        def validate_double(v):  return float(v)
        def validate_decimal(v):
            from decimal import Decimal; return Decimal(str(v))

        _DATE_RE      = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")
        _TIME_RE      = re.compile(r"^\\d{2}:\\d{2}:\\d{2}(Z|[+-]\\d{2}:\\d{2})?$")
        _DATETIME_RE  = re.compile(r"^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}")
        _DURATION_RE  = re.compile(r"^P(\\d+Y)?(\\d+M)?(\\d+D)?(T(\\d+H)?(\\d+M)?(\\d+S)?)?$")
        _EMAIL_RE     = re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")
        _HOSTNAME_RE  = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(\\.[a-zA-Z0-9]([a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)*$")
        _URI_RE       = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\\-.]*:")
        _UUID_RE      = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
        _JSON_POINTER_RE = re.compile(r"^(/[^/]*)*$")

        def validate_date(v):
            assert _DATE_RE.match(str(v)), f"Invalid date format: {v}"; return str(v)
        def validate_time(v):
            assert _TIME_RE.match(str(v)), f"Invalid time format: {v}"; return str(v)
        def validate_datetime(v):
            assert _DATETIME_RE.match(str(v)), f"Invalid datetime: {v}"; return str(v)
        def validate_duration(v):
            assert _DURATION_RE.match(str(v)), f"Invalid duration: {v}"; return str(v)
        def validate_email(v):
            assert _EMAIL_RE.match(str(v)), f"Invalid email: {v}"; return str(v)
        def validate_hostname(v):
            assert _HOSTNAME_RE.match(str(v)), f"Invalid hostname: {v}"; return str(v)
        def validate_ipv4(v):
            ipaddress.IPv4Address(str(v)); return str(v)
        def validate_ipv6(v):
            ipaddress.IPv6Address(str(v)); return str(v)
        def validate_uri(v):
            assert _URI_RE.match(str(v)), f"Invalid URI: {v}"; return str(v)
        def validate_uuid(v):
            assert _UUID_RE.match(str(v)), f"Invalid UUID: {v}"; return str(v)
        def validate_json_pointer(v):
            assert _JSON_POINTER_RE.match(str(v)), f"Invalid JSON pointer: {v}"; return str(v)
        def validate_byte(v):
            base64.b64decode(str(v)); return str(v)
        def validate_base64url(v):
            base64.urlsafe_b64decode(str(v) + "=="); return str(v)
        def validate_binary(v):
            return v  # raw bytes or string, pass through
        def validate_password(v):
            return str(v)  # treat as opaque string
        def validate_commonmark(v):
            return str(v)  # no structural validation needed
        def validate_html(v):
            return str(v)

        FORMAT_VALIDATORS: dict[str, Any] = {
            "int8": validate_int8, "int16": validate_int16,
            "int32": validate_int32, "int64": validate_int64,
            "uint8": validate_uint8, "uint16": validate_uint16,
            "uint32": validate_uint32, "uint64": validate_uint64,
            "float": validate_float, "double": validate_double,
            "decimal": validate_decimal, "decimal128": validate_decimal,
            "date": validate_date, "time": validate_time,
            "date-time": validate_datetime, "duration": validate_duration,
            "email": validate_email, "idn-email": validate_email,
            "hostname": validate_hostname, "idn-hostname": validate_hostname,
            "ipv4": validate_ipv4, "ipv6": validate_ipv6,
            "uri": validate_uri, "iri": validate_uri,
            "uuid": validate_uuid,
            "json-pointer": validate_json_pointer,
            "byte": validate_byte, "base64url": validate_base64url,
            "binary": validate_binary, "password": validate_password,
            "commonmark": validate_commonmark, "html": validate_html,
        }

        def validate_format(value: Any, fmt: str) -> Any:
            """Apply format validator; raises AssertionError if invalid."""
            validator = FORMAT_VALIDATORS.get(fmt)
            if validator:
                return validator(value)
            return value

        # Sanitization helpers
        _SANITIZE_LOW    = {"password", "token", "secret", "private_key"}
        _SANITIZE_MEDIUM = _SANITIZE_LOW | {"access_token", "credentials", "authorization"}
        _SANITIZE_HIGH   = _SANITIZE_MEDIUM | {"session_id", "cookie", "api_key", "ip_address", "hostname"}

        def sanitize_response(data: Any, level: str = "DISABLED") -> Any:
            """Redact sensitive fields from API response before returning to agent."""
            if level == "DISABLED" or not isinstance(data, dict):
                return data
            fields = {"LOW": _SANITIZE_LOW, "MEDIUM": _SANITIZE_MEDIUM, "HIGH": _SANITIZE_HIGH}.get(level, set())
            return {
                k: ("[REDACTED]" if k.lower() in fields else
                    sanitize_response(v, level) if isinstance(v, dict) else v)
                for k, v in data.items()
            }
    ''')


def gen_auth(spec: dict, force_api_key: bool) -> str | None:
    """Generate _auth.py. Returns None if no auth needed."""
    auth_info = detect_auth(spec)
    schemes = auth_info["schemes"]

    if not schemes and not force_api_key:
        return None

    lines = [
        '#!/usr/bin/env python3',
        '"""_auth.py — Authentication handlers for this MCP server."""',
        'from __future__ import annotations',
        'import logging, os',
        'logger = logging.getLogger(__name__)',
        '',
        '__all__ = ["APIKeyAuth", "OPERATION_AUTH_MAP"]',
        '',
        '# ════════════════════════════════════════════════════',
        '# Authentication Classes',
        '# ════════════════════════════════════════════════════',
        '',
        'class APIKeyAuth:',
        '    """API Key authentication — injects key into header, query, or cookie."""',
        '    def __init__(self, env_var: str = "API_KEY", location: str = "header",',
        '                 param_name: str = "X-API-Key", prefix: str = ""):',
        '        self.location = location',
        '        self.param_name = param_name',
        '        self.prefix = prefix',
        '        self.api_key = os.getenv(env_var, "").strip()',
        '        if not self.api_key:',
        '            raise ValueError(',
        '                f"{env_var} environment variable not set. "',
        '                "Set it in .env to enable API Key auth."',
        '            )',
        '        _bad = ["placeholder", "your-", "example", "change-me", "todo"]',
        '        if any(b in self.api_key.lower() for b in _bad):',
        '            logger.warning("API_KEY looks like a placeholder — did you set a real key?")',
        '',
        '    def inject(self, headers: dict, params: dict, cookies: dict) -> None:',
        '        """Inject the API key into the appropriate location."""',
        '        value = f"{self.prefix} {self.api_key}".strip() if self.prefix else self.api_key',
        '        if self.location == "header":',
        '            headers[self.param_name] = value',
        '        elif self.location == "query":',
        '            params[self.param_name] = value',
        '        elif self.location == "cookie":',
        '            cookies[self.param_name] = value',
        '',
        '    def is_available(self) -> bool:',
        '        return bool(self.api_key)',
        '',
    ]

    # Build OPERATION_AUTH_MAP from spec
    ops = extract_operations(spec)
    global_sec = auth_info["global_security"]

    op_auth_map: dict[str, list] = {}
    for op in ops:
        op_sec = op["security"]  # None = inherit, [] = public, [...] = explicit
        if op_sec is None:
            op_sec = global_sec
        schemes_needed = []
        for sec_req in (op_sec or []):
            schemes_needed.append(list(sec_req.keys()))
        op_auth_map[op["operation_id"]] = schemes_needed

    lines.append('# ════════════════════════════════════════════════════')
    lines.append('# Operation → Auth mapping')
    lines.append('# Outer list = OR (any one scheme works)')
    lines.append('# Inner list = AND (all schemes in group required)')
    lines.append('# ════════════════════════════════════════════════════')
    lines.append('OPERATION_AUTH_MAP: dict[str, list[list[str]]] = {')
    for op_id, auth_reqs in op_auth_map.items():
        lines.append(f'    {json.dumps(op_id)}: {json.dumps(auth_reqs)},')
    lines.append('}')
    lines.append('')
    lines.append('# ════════════════════════════════════════════════════')
    lines.append('# Auth registry — keyed by scheme name')
    lines.append('# ════════════════════════════════════════════════════')
    lines.append('def build_auth_registry() -> dict:')
    lines.append('    """Build auth handlers from environment. Missing creds = scheme disabled."""')
    lines.append('    registry: dict[str, object] = {}')

    if schemes or force_api_key:
        lines.append('    try:')

        if force_api_key or any(
            v.get("type") == "apiKey" for v in schemes.values()
        ):
            # Find header name from spec
            api_key_schemes = {k: v for k, v in schemes.items() if v.get("type") == "apiKey"} if schemes else {}
            if api_key_schemes:
                for sname, sdef in api_key_schemes.items():
                    loc        = sdef.get("in", "header")
                    param_name = sdef.get("name", "X-API-Key")
                    lines.append(f'        registry[{json.dumps(sname)}] = APIKeyAuth(')
                    lines.append(f'            env_var="API_KEY", location={json.dumps(loc)},')
                    lines.append(f'            param_name={json.dumps(param_name)})')
            else:
                lines.append('        registry["apiKey"] = APIKeyAuth()')

        lines.append('    except ValueError as e:')
        lines.append('        logger.warning(f"Auth setup: {e}")')

    lines.append('    return registry')
    lines.append('')

    return "\n".join(lines)


def gen_models(spec: dict, ops: list[dict]) -> str:
    """Generate _models.py with Pydantic request parameter models."""
    lines = [
        '#!/usr/bin/env python3',
        '"""_models.py — Pydantic models for request parameters and responses."""',
        'from __future__ import annotations',
        'from typing import Any, Optional',
        'from pydantic import Field',
        'from _validators import StrictModel, PermissiveModel',
        '',
    ]

    for op in ops:
        class_name = "".join(w.capitalize() for w in slugify(op["operation_id"]).split("_")) + "Params"
        params = op["parameters"]

        fields = []
        for p in params:
            if not isinstance(p, dict):
                continue
            name     = slugify(p.get("name", "param"))
            required = p.get("required", False)
            schema   = p.get("schema", {})
            py_type  = python_type(schema, required)
            desc     = (p.get("description") or "").replace('"', '\\"')[:200]
            default  = p.get("default") or schema.get("default")

            if required:
                fields.append(f'    {name}: {py_type} = Field(..., description="{desc}")')
            else:
                default_repr = json.dumps(default) if default is not None else "None"
                fields.append(f'    {name}: Optional[{py_type}] = Field({default_repr}, description="{desc}")')

        # Request body fields
        body = op.get("request_body", {})
        if body:
            content = body.get("content", {})
            for content_type, media in content.items():
                body_schema = media.get("schema", {})
                if body_schema.get("type") == "object":
                    props    = body_schema.get("properties", {})
                    required = body_schema.get("required", [])
                    for prop_name, prop_schema in props.items():
                        name    = slugify(prop_name)
                        py_type = python_type(prop_schema)
                        desc    = prop_schema.get("description", "")[:200].replace('"', '\\"')
                        if prop_name in required:
                            fields.append(f'    {name}: {py_type} = Field(..., description="{desc}")')
                        else:
                            fields.append(f'    {name}: Optional[{py_type}] = None')
                else:
                    fields.append(f'    body: Optional[Any] = None')
                break

        lines.append(f'class {class_name}(StrictModel):')
        op_desc = (op.get("summary") or op.get("description") or op["operation_id"])[:200]
        lines.append(f'    """Parameters for {op_desc}."""')
        if fields:
            lines.extend(fields)
        else:
            lines.append('    pass')
        lines.append('')

    return "\n".join(lines)


def build_tool_function(spec: dict, op: dict, auth_info: dict) -> str:
    """Generate one @mcp.tool() async function."""
    func_name = slugify(op["operation_id"])
    class_name = "".join(w.capitalize() for w in func_name.split("_")) + "Params"
    method = op["method"]
    path   = op["path"]

    # Build docstring
    summary = op.get("summary") or op.get("description") or op["operation_id"]
    desc    = op.get("description") or ""
    docstring_parts = [summary]
    if desc and desc != summary:
        docstring_parts.append(desc)

    # Determine parameter names
    params = op["parameters"]
    path_params  = [slugify(p["name"]) for p in params if p.get("in") == "path"  and isinstance(p, dict)]
    query_params = [slugify(p["name"]) for p in params if p.get("in") == "query" and isinstance(p, dict)]
    header_params= [slugify(p["name"]) for p in params if p.get("in") == "header"and isinstance(p, dict)]
    request_body = op.get("request_body", {})
    has_body     = bool(request_body)

    body_props: list[tuple[str, dict, bool]] = []
    uses_raw_body = False
    if has_body:
        content = request_body.get("content", {})
        for _, media in content.items():
            body_schema = get_schema(spec, media.get("schema", {}))
            if body_schema.get("type") == "object":
                required_props = set(body_schema.get("required", []))
                for prop_name, prop_schema in body_schema.get("properties", {}).items():
                    body_props.append((prop_name, prop_schema, prop_name in required_props))
            else:
                uses_raw_body = True
            break

    # Build path substitution
    path_fmt = path
    for pp in path_params:
        orig = next((p["name"] for p in params if isinstance(p, dict) and slugify(p["name"]) == pp), pp)
        path_fmt = path_fmt.replace("{" + orig + "}", "{params." + pp + "}")

    # Auth injection
    op_sec = op.get("security")
    if op_sec is None:
        op_sec = auth_info.get("global_security", [])
    needs_auth = bool(op_sec) or auth_info.get("has_auth")

    lines = [
        f'@mcp.tool()',
        f'async def {func_name}(',
    ]

    # Build signature from parameter list
    sig_parts = []
    for p in params:
        if not isinstance(p, dict):
            continue
        name     = slugify(p.get("name", "param"))
        required = p.get("required", False)
        schema   = p.get("schema", {})
        py_type  = python_type(schema)
        if required:
            sig_parts.append(f'    {name}: {py_type}')
        else:
            default = p.get("default") or schema.get("default")
            default_repr = json.dumps(default) if default is not None else "None"
            sig_parts.append(f'    {name}: {py_type} | None = {default_repr}')

    if has_body:
        if uses_raw_body:
            sig_parts.append('    body: Any | None = None')
        else:
            for prop_name, prop_schema, required in body_props:
                name = slugify(prop_name)
                py_type = python_type(prop_schema)
                if required:
                    sig_parts.append(f'    {name}: {py_type}')
                else:
                    default = prop_schema.get("default")
                    default_repr = json.dumps(default) if default is not None else "None"
                    sig_parts.append(f'    {name}: {py_type} | None = {default_repr}')

    lines.extend([s + "," for s in sig_parts])
    lines.append(f') -> dict[str, Any]:')

    # Docstring
    doc = '    """' + summary
    if desc and desc.strip() != summary.strip():
        doc += "\n\n    " + desc.strip()[:300]
    doc += '    """'
    lines.append(doc)

    model_kwargs = [slugify(p.get("name", "param")) for p in params if isinstance(p, dict)]
    if has_body:
        if uses_raw_body:
            model_kwargs.append("body")
        else:
            model_kwargs.extend(slugify(prop_name) for prop_name, _, _ in body_props)
    kwargs_str = ", ".join(f"{name}={name}" for name in model_kwargs)
    lines.append(f'    params = _models.{class_name}({kwargs_str})')

    # Body
    lines.extend([
        f'    url = BASE_URL + f"{path_fmt}"',
        f'    headers: dict[str, str] = {{}}',
        f'    qparams: dict[str, Any]  = {{}}',
        f'    cookies: dict[str, str] = {{}}',
    ])

    # Query params
    for qp in query_params:
        orig = next((p["name"] for p in params if isinstance(p, dict) and slugify(p["name"]) == qp), qp)
        lines.append(f'    if params.{qp} is not None: qparams[{json.dumps(orig)}] = params.{qp}')

    # Header params
    for hp in header_params:
        orig = next((p["name"] for p in params if isinstance(p, dict) and slugify(p["name"]) == hp), hp)
        lines.append(f'    if params.{hp} is not None: headers[{json.dumps(orig)}] = str(params.{hp})')

    body_arg = "json=None"
    if has_body:
        if uses_raw_body:
            body_arg = "json=params.body"
        else:
            lines.append('    body_payload: dict[str, Any] = {}')
            for prop_name, _, _ in body_props:
                attr_name = slugify(prop_name)
                lines.append(
                    f'    if params.{attr_name} is not None: body_payload[{json.dumps(prop_name)}] = params.{attr_name}'
                )
            lines.append('    json_body = body_payload')
            body_arg = "json=json_body"

    # Auth injection
    if needs_auth:
        lines.extend([
            f'    auth_map = _auth.build_auth_registry()',
            f'    op_schemes = _auth.OPERATION_AUTH_MAP.get({json.dumps(op["operation_id"])}, [])',
            f'    for scheme_group in op_schemes:',
            f'        for scheme_name in scheme_group:',
            f'            handler = auth_map.get(scheme_name)',
            f'            if handler and handler.is_available():',
            f'                handler.inject(headers, qparams, cookies)',
            f'                break',
        ])

    # HTTP call with retry / circuit breaker
    lines.extend([
        f'    return await _execute_with_resilience(',
        f'        method={json.dumps(method)},',
        f'        url=url,',
        f'        headers=headers,',
        f'        params=qparams,',
        f'        {body_arg},',
        f'    )',
    ])
    lines.append('')
    return "\n".join(lines)


def gen_server(spec: dict, ops: list[dict], server_name: str, auth_info: dict) -> str:
    """Generate server.py."""
    info = spec.get("info", {})
    api_title   = info.get("title", server_name)
    api_version = info.get("version", "1.0.0")
    base_url    = ""
    servers     = spec.get("servers", [])
    if servers and isinstance(servers[0], dict):
        base_url = servers[0].get("url", "")

    has_auth = auth_info["has_auth"]
    auth_import = "import _auth" if has_auth else ""

    tool_functions = "\n".join(build_tool_function(spec, op, auth_info) for op in ops)

    return dedent(f'''\
        #!/usr/bin/env python3
        """
        {server_name} MCP Server

        API: {api_title} v{api_version}
        Generated by: MCP Forge CLI

        Run:
            python server.py                         # stdio (default)
            python server.py --transport sse --port 8000
            python server.py --transport streamable-http --port 8000
        """
        from __future__ import annotations
        import argparse, asyncio, json, logging, os, random, sys, time
        from pathlib import Path
        from typing import Any

        try:
            from dotenv import load_dotenv
            _env = Path(__file__).parent / ".env"
            if _env.exists(): load_dotenv(_env)
        except ImportError:
            pass

        {auth_import}
        import _models
        import _validators
        import httpx
        from fastmcp import FastMCP
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        BASE_URL    = os.getenv("BASE_URL", {json.dumps(base_url)})
        SERVER_NAME = {json.dumps(server_name)}

        # ── Timeouts ─────────────────────────────────────────────────────────────
        HTTPX_TIMEOUT = httpx.Timeout(
            connect=float(os.getenv("HTTPX_CONNECT_TIMEOUT", "10.0")),
            read=float(os.getenv("HTTPX_READ_TIMEOUT",    "60.0")),
            write=float(os.getenv("HTTPX_WRITE_TIMEOUT",  "30.0")),
            pool=float(os.getenv("HTTPX_POOL_TIMEOUT",    "5.0")),
        )
        TOOL_EXECUTION_TIMEOUT  = float(os.getenv("TOOL_EXECUTION_TIMEOUT", "90.0"))
        CONNECTION_POOL_SIZE    = int(os.getenv("CONNECTION_POOL_SIZE",   "100"))
        MAX_KEEPALIVE           = int(os.getenv("MAX_KEEPALIVE_CONNECTIONS","20"))

        # ── Resilience ────────────────────────────────────────────────────────────
        MAX_RETRIES              = int(os.getenv("MAX_RETRIES", "3"))
        RETRY_BACKOFF            = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))
        CB_FAILURE_THRESHOLD     = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        CB_TIMEOUT               = float(os.getenv("CIRCUIT_BREAKER_TIMEOUT_SECONDS", "60.0"))
        RATE_LIMIT_RPS           = int(os.getenv("RATE_LIMIT_REQUESTS_PER_SECOND", "10"))
        RESPONSE_VALIDATION_MODE = os.getenv("RESPONSE_VALIDATION_MODE", "warn").lower()
        SANITIZATION_LEVEL       = os.getenv("SANITIZATION_LEVEL", "DISABLED").upper()

        LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
        logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                            format="%(levelname)s: %(message)s")
        logger = logging.getLogger(SERVER_NAME)

        # ── HTTP client ───────────────────────────────────────────────────────────
        _client: httpx.AsyncClient | None = None

        async def get_client() -> httpx.AsyncClient:
            global _client
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=HTTPX_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=CONNECTION_POOL_SIZE,
                        max_keepalive_connections=MAX_KEEPALIVE,
                    ),
                    follow_redirects=True,
                )
            return _client

        # ── Token-bucket rate limiter ─────────────────────────────────────────────
        class _TokenBucket:
            def __init__(self, rate: float):
                self.rate = rate; self.tokens = float(rate); self.last = time.monotonic()
            async def acquire(self):
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens < 1:
                    await asyncio.sleep((1 - self.tokens) / self.rate)
                self.tokens -= 1

        _bucket = _TokenBucket(RATE_LIMIT_RPS)

        # ── Circuit breaker ───────────────────────────────────────────────────────
        class _CircuitBreaker:
            CLOSED = "CLOSED"; OPEN = "OPEN"; HALF_OPEN = "HALF_OPEN"
            def __init__(self):
                self.state = self.CLOSED
                self.failures = 0; self.opened_at: float | None = None
            def record_success(self): self.failures = 0; self.state = self.CLOSED
            def record_failure(self):
                self.failures += 1
                if self.failures >= CB_FAILURE_THRESHOLD:
                    self.state = self.OPEN; self.opened_at = time.monotonic()
            def allow_request(self) -> bool:
                if self.state == self.CLOSED: return True
                if self.state == self.OPEN:
                    if time.monotonic() - (self.opened_at or 0) > CB_TIMEOUT:
                        self.state = self.HALF_OPEN; return True
                    return False
                return True  # HALF_OPEN: let one through

        _cb = _CircuitBreaker()

        # ── Core execute ──────────────────────────────────────────────────────────
        async def _execute_with_resilience(
            method: str, url: str,
            headers: dict | None = None,
            params:  dict | None = None,
            json:    Any  = None,
        ) -> dict[str, Any]:
            if not _cb.allow_request():
                return {{"error": "circuit_breaker_open",
                         "message": "Upstream API is temporarily unavailable. Please retry later."}}

            await _bucket.acquire()
            client = await get_client()
            last_error: Exception | None = None
            RETRY_CODES = {{429, 500, 502, 503, 504}}

            for attempt in range(MAX_RETRIES + 1):
                if attempt > 0:
                    delay = RETRY_BACKOFF ** attempt + random.uniform(0, 0.5)
                    logger.info(f"Retry {{attempt}}/{{MAX_RETRIES}} after {{delay:.1f}}s")
                    await asyncio.sleep(delay)
                try:
                    resp = await asyncio.wait_for(
                        client.request(method, url, headers=headers or {{}},
                                       params=params or {{}}, json=json),
                        timeout=TOOL_EXECUTION_TIMEOUT,
                    )
                    if resp.status_code in RETRY_CODES and attempt < MAX_RETRIES:
                        last_error = Exception(f"HTTP {{resp.status_code}}")
                        continue

                    _cb.record_success()
                    content_type = resp.headers.get("content-type", "")
                    if "application/json" in content_type:
                        try:
                            data = resp.json()
                        except Exception:
                            data = {{"raw": resp.text}}
                    else:
                        data = {{"content": resp.text, "status_code": resp.status_code}}

                    # Response sanitization
                    if isinstance(data, dict):
                        data = _validators.sanitize_response(data, SANITIZATION_LEVEL)

                    if resp.status_code >= 400:
                        return {{"error": f"http_{{resp.status_code}}", "detail": data}}
                    return data

                except asyncio.TimeoutError:
                    _cb.record_failure()
                    return {{"error": "timeout", "message": f"Request timed out after {{TOOL_EXECUTION_TIMEOUT}}s"}}
                except httpx.RequestError as e:
                    _cb.record_failure(); last_error = e

            _cb.record_failure()
            return {{"error": "request_failed", "message": str(last_error)}}

        # ── FastMCP server ────────────────────────────────────────────────────────
        mcp = FastMCP(SERVER_NAME)

        @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
        async def health_check(request: Request) -> JSONResponse:
            """Simple readiness endpoint for HTTP transports."""
            return JSONResponse(
                {{
                    "status": "ok",
                    "server": SERVER_NAME,
                    "base_url_configured": bool(BASE_URL),
                }}
            )

        # ════════════════════════════════════════════════════════════════════════
        # Tool definitions
        # ════════════════════════════════════════════════════════════════════════
        {indent(tool_functions, "        ").lstrip()}

        # ── Entrypoint ────────────────────────────────────────────────────────────
        def main():
            parser = argparse.ArgumentParser(description=f"{{SERVER_NAME}} MCP Server")
            parser.add_argument("--transport", default="stdio",
                                choices=["stdio","sse","streamable-http"])
            parser.add_argument("--port", type=int, default=8000)
            parser.add_argument("--host", default="0.0.0.0")
            args = parser.parse_args()

            logger.info(f"Starting {{SERVER_NAME}} (transport={{args.transport}})")
            if args.transport == "stdio":
                mcp.run(transport="stdio")
            elif args.transport == "sse":
                mcp.run(transport="sse", host=args.host, port=args.port)
            else:
                mcp.run(transport="streamable-http", host=args.host, port=args.port)

        if __name__ == "__main__":
            main()
    ''')


def gen_env(spec: dict, server_name: str, auth_info: dict) -> str:
    servers = spec.get("servers", [])
    base_url = servers[0].get("url", "https://api.example.com") if servers else "https://api.example.com"
    auth_section = ""
    if auth_info["has_auth"]:
        auth_section = dedent("""\
            # ── Authentication ──────────────────────────────────────────────────────────
            # Set this to your API key. Leave blank to disable API Key authentication.
            API_KEY=

        """)
    return dedent(f"""\
        # ════════════════════════════════════════════════════════════════════════════
        # {server_name.upper()} MCP SERVER CONFIGURATION
        # ════════════════════════════════════════════════════════════════════════════
        # IMPORTANT: Do not commit this file to version control after adding credentials.
        # Add .env to your .gitignore
        # ════════════════════════════════════════════════════════════════════════════

        {auth_section}
        # ── API Base URL ─────────────────────────────────────────────────────────────
        BASE_URL={base_url}

        # ── Timeouts (seconds) ────────────────────────────────────────────────────────
        HTTPX_CONNECT_TIMEOUT=10.0
        HTTPX_READ_TIMEOUT=60.0
        HTTPX_WRITE_TIMEOUT=30.0
        HTTPX_POOL_TIMEOUT=5.0
        TOOL_EXECUTION_TIMEOUT=90.0

        # ── Connection Pool ───────────────────────────────────────────────────────────
        CONNECTION_POOL_SIZE=100
        MAX_KEEPALIVE_CONNECTIONS=20

        # ── Resilience ────────────────────────────────────────────────────────────────
        MAX_RETRIES=3
        RETRY_BACKOFF_FACTOR=2.0
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
        CIRCUIT_BREAKER_TIMEOUT_SECONDS=60
        RATE_LIMIT_REQUESTS_PER_SECOND=10

        # ── Validation ────────────────────────────────────────────────────────────────
        # off | warn | strict
        RESPONSE_VALIDATION_MODE=warn

        # ── Response Sanitization ─────────────────────────────────────────────────────
        # DISABLED | LOW | MEDIUM | HIGH
        SANITIZATION_LEVEL=DISABLED

        # ── Logging ───────────────────────────────────────────────────────────────────
        LOG_LEVEL=INFO
        LOG_FORMAT=simple
    """)


def gen_requirements(has_auth: bool) -> str:
    lines = [
        "fastmcp>=2.12.0,<3.0.0",
        "httpx>=0.27.0,<1.0.0",
        "pydantic>=2.0.0,<3.0.0",
        "python-dotenv>=1.0.0,<2.0.0",
    ]
    return "\n".join(lines) + "\n"


def gen_dockerfile(server_name: str) -> str:
    return dedent(f"""\
        FROM python:3.12-slim

        WORKDIR /app

        # Install dependencies
        COPY requirements.txt .
        RUN pip install --no-cache-dir -r requirements.txt

        # Copy server files
        COPY . .

        # Never run as root
        RUN adduser --disabled-password --gecos "" mcpuser
        USER mcpuser

        EXPOSE 8000

        # Default: SSE transport for Docker deployments
        ENTRYPOINT ["python", "server.py", "--transport", "sse", "--port", "8000"]
    """)


def gen_mcp_json(server_name: str) -> str:
    return json.dumps({
        "mcpServers": {
            server_name: {
                "command": "python",
                "args": ["server.py"]
            }
        }
    }, indent=2) + "\n"


def gen_readme(spec: dict, server_name: str, ops: list, auth_info: dict) -> str:
    info = spec.get("info", {})
    api_title = info.get("title", server_name)
    op_lines = "\n".join(f"- `{op['operation_id']}` — {op.get('summary','')}" for op in ops[:20])
    auth_note = ""
    if auth_info["has_auth"]:
        auth_note = dedent("""\
            ## Authentication

            This server uses **API Key** authentication.

            1. Open `.env`
            2. Set `API_KEY=<your_api_key>`
            3. Save and restart the server

        """)
    return dedent(f"""\
        # {server_name} MCP Server

        Auto-generated MCP server for **{api_title}** by [MCP Forge CLI](https://github.com/your-org/mcp-forge).

        ## Quick Start

        ```bash
        # 1. Create virtual environment
        python -m venv .venv
        source .venv/bin/activate    # Linux/macOS
        # .venv\\Scripts\\activate    # Windows

        # 2. Install dependencies
        pip install -r requirements.txt

        # 3. Configure credentials
        cp .env.example .env         # if provided, else edit .env directly
        # Edit .env and set BASE_URL (and API_KEY if required)

        # 4. Run the server
        python server.py             # stdio (for Claude Desktop / Claude Code)
        python server.py --transport sse --port 8000   # network
        ```

        {auth_note}
        ## Transport Options

        | Mode | Command |
        |------|---------|
        | stdio (default) | `python server.py` |
        | SSE | `python server.py --transport sse --port 8000` |
        | Streamable HTTP | `python server.py --transport streamable-http --port 8000` |

        ## Available Tools ({len(ops)})

        {op_lines}

        ## Docker

        ```bash
        docker build -t {server_name} .
        docker run -p 8000:8000 --env-file .env {server_name}
        ```

        ## MCP Client Config

        Copy `.mcp.json` into your MCP client configuration directory.
        Adjust the path to `server.py` as needed.

        ## Security

        - Circuit breaker prevents cascading failures
        - Token-bucket rate limiting
        - Exponential backoff retries
        - Pydantic input validation on all parameters
        - Response sanitization (configurable via `SANITIZATION_LEVEL`)

        ---
        *Generated by MCP Forge CLI — Phase 1*
    """)


# ──────────────────────────────────────────────────────────────
# Main generator
# ──────────────────────────────────────────────────────────────

def generate(spec_path: Path, output_dir: Path, server_name: str, force_api_key: bool):
    h1("═══ MCP Forge — Server Generator ═══")

    spec = load_spec(spec_path)
    info = spec.get("info", {})
    if not server_name:
        server_name = slugify(info.get("title", "mcp_server"))

    ops       = extract_operations(spec)
    auth_info = detect_auth(spec)

    if force_api_key and not auth_info["has_auth"]:
        auth_info["has_auth"]  = True
        auth_info["schemes"]   = {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}

    h2("Spec Info")
    print(f"  API      : {info.get('title', '(untitled)')}")
    print(f"  Version  : {info.get('version', '(none)')}")
    print(f"  Ops      : {len(ops)}")
    print(f"  Auth     : {'API Key' if auth_info['has_auth'] else 'none'}")
    print(f"  Output   : {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    h2("Generating files")

    files = {
        "server.py":       gen_server(spec, ops, server_name, auth_info),
        "_models.py":      gen_models(spec, ops),
        "_validators.py":  gen_validators(),
        ".env":            gen_env(spec, server_name, auth_info),
        "requirements.txt":gen_requirements(auth_info["has_auth"]),
        "Dockerfile":      gen_dockerfile(server_name),
        ".mcp.json":       gen_mcp_json(server_name),
        "README.md":       gen_readme(spec, server_name, ops, auth_info),
    }

    auth_code = gen_auth(spec, force_api_key)
    if auth_code:
        files["_auth.py"] = auth_code

    for fname, content in files.items():
        (output_dir / fname).write_text(content)
        ok(f"Generated {fname}")

    h2("Summary")
    print(f"  Tools generated : {len(ops)}")
    print(f"  Auth            : {'API Key (set API_KEY in .env)' if auth_info['has_auth'] else 'none'}")
    print(f"  Output dir      : {output_dir}")
    print(f"\n  {GREEN}{BOLD}✓ Generation complete!{RESET}")
    print(f"\n  Next steps:")
    print(f"    cd {output_dir}")
    print(f"    python -m venv .venv && source .venv/bin/activate")
    print(f"    pip install -r requirements.txt")
    if auth_info["has_auth"]:
        print(f"    # Edit .env — set API_KEY=<your key>")
    print(f"    python server.py")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Generate a FastMCP server from an OpenAPI 3.x spec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_server.py my_api.yaml
  python generate_server.py my_api.yaml --output ./my-server
  python generate_server.py my_api.yaml --api-key --name petstore_server
        """
    )
    parser.add_argument("spec_file", help="Path to OpenAPI spec (YAML or JSON)")
    parser.add_argument("--output", "-o", metavar="DIR",
                        help="Output directory (default: ./<server_name>)")
    parser.add_argument("--name", metavar="NAME",
                        help="Server name override (default: derived from spec title)")
    parser.add_argument("--api-key", action="store_true",
                        help="Force API Key auth even if not in spec")
    args = parser.parse_args()

    ensure_deps()

    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        print(f"{RED}Error: spec file not found: {spec_path}{RESET}")
        sys.exit(1)

    spec   = load_spec(spec_path)
    info   = spec.get("info", {})
    name   = args.name or slugify(info.get("title", "mcp_server"))
    outdir = Path(args.output) if args.output else Path(name)

    generate(spec_path, outdir, name, args.api_key)


if __name__ == "__main__":
    main()
