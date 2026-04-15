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
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

RESET = "\033[0m"; BOLD = "\033[1m"; RED = "\033[91m"
GREEN = "\033[92m"; YELLOW = "\033[93m"; CYAN = "\033[96m"; DIM = "\033[2m"

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_TEMPLATE_ENV = None


def ok(m):   print(f"  {GREEN}✓{RESET} {m}")
def warn(m): print(f"  {YELLOW}⚠{RESET}  {m}")
def err(m):  print(f"  {RED}✗{RESET} {m}")
def h1(m):   print(f"\n{BOLD}{m}{RESET}")
def h2(m):   print(f"\n{CYAN}{m}{RESET}")


# ──────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────

def ensure_deps():
    required = {"yaml": "pyyaml", "jinja2": "jinja2"}
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
    if schema.get("nullable"):
        base_type = python_type({k: v for k, v in schema.items() if k != "nullable"}, required=True)
        return f"{base_type} | None"
    for combiner in ("oneOf", "anyOf"):
        if schema.get(combiner):
            branch_types: list[str] = []
            for subschema in schema[combiner]:
                branch_type = python_type(subschema, required=True)
                if branch_type not in branch_types:
                    branch_types.append(branch_type)
            return " | ".join(branch_types) if branch_types else "Any"
    if schema.get("allOf"):
        merged_schema = copy.deepcopy(schema)
        merged_schema.pop("allOf", None)
        return python_type(merged_schema, required=required)
    t = schema.get("type", "")
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        items = schema.get("items", {})
        return f"list[{python_type(items)}]"
    if t == "object":
        return "dict[str, Any]"
    return "str"


def python_literal(value: Any) -> str:
    return repr(value)


class SchemaResolver:
    """Resolve internal and external refs, and normalize composed schemas."""

    def __init__(self, root_spec: dict, root_path: Path):
        self.root_spec = root_spec
        self.root_path = root_path.resolve()
        self._doc_cache: dict[Path, dict] = {self.root_path: root_spec}

    def _load_document(self, path: Path) -> dict:
        path = path.resolve()
        cached = self._doc_cache.get(path)
        if cached is not None:
            return cached
        document = load_spec(path)
        self._doc_cache[path] = document
        return document

    def _split_ref(self, ref: str, current_file: Path) -> tuple[Path, str]:
        if "#" in ref:
            ref_path, fragment = ref.split("#", 1)
            fragment = f"#{fragment}"
        else:
            ref_path, fragment = ref, ""

        if not ref_path:
            return current_file.resolve(), fragment

        candidate = Path(ref_path)
        if not candidate.is_absolute():
            candidate = (current_file.parent / candidate).resolve()
        return candidate, fragment

    def _resolve_pointer(self, document: Any, fragment: str) -> Any:
        if not fragment or fragment == "#":
            return document
        if not fragment.startswith("#/"):
            return {}

        node = document
        for part in fragment[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict):
                node = node.get(part, {})
            elif isinstance(node, list):
                try:
                    node = node[int(part)]
                except (ValueError, IndexError):
                    return {}
            else:
                return {}
        return node

    def resolve_ref_target(
        self,
        ref: str,
        current_file: Path,
        seen_refs: set[tuple[Path, str]] | None = None,
    ) -> tuple[Any, Path]:
        seen_refs = seen_refs or set()
        target_file, fragment = self._split_ref(ref, current_file)
        marker = (target_file, fragment or "#")
        if marker in seen_refs:
            return {}, target_file

        document = self._load_document(target_file)
        target = self._resolve_pointer(document, fragment)
        return copy.deepcopy(target), target_file

    def resolve_node(
        self,
        node: Any,
        current_file: Path | None = None,
        seen_refs: set[tuple[Path, str]] | None = None,
    ) -> Any:
        current_file = (current_file or self.root_path).resolve()
        seen_refs = seen_refs or set()

        if isinstance(node, list):
            return [self.resolve_node(item, current_file, seen_refs) for item in node]

        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref = node["$ref"]
            target_file, fragment = self._split_ref(ref, current_file)
            marker = (target_file, fragment or "#")
            if marker in seen_refs:
                return {}

            resolved_target, resolved_file = self.resolve_ref_target(ref, current_file, seen_refs)
            resolved_data = self.resolve_node(resolved_target, resolved_file, seen_refs | {marker})
            sibling_overlay = {
                key: self.resolve_node(value, current_file, seen_refs)
                for key, value in node.items()
                if key != "$ref"
            }
            if isinstance(resolved_data, dict):
                merged = copy.deepcopy(resolved_data)
                merged.update(sibling_overlay)
                return merged
            return sibling_overlay or resolved_data

        return {
            key: self.resolve_node(value, current_file, seen_refs)
            for key, value in node.items()
        }

    def _merge_all_of(self, schemas: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        merged_required: list[str] = []
        merged_properties: dict[str, Any] = {}

        for schema in schemas:
            if not isinstance(schema, dict):
                continue

            for prop_name, prop_schema in schema.get("properties", {}).items():
                merged_properties[prop_name] = prop_schema

            for required_name in schema.get("required", []):
                if required_name not in merged_required:
                    merged_required.append(required_name)

            for key, value in schema.items():
                if key in {"properties", "required"}:
                    continue
                if key == "type":
                    existing = merged.get("type")
                    if existing and existing != value:
                        merged["type"] = "object"
                    else:
                        merged["type"] = value
                    continue
                if key == "enum" and key in merged and isinstance(merged[key], list) and isinstance(value, list):
                    merged[key] = [item for item in merged[key] if item in value]
                    continue
                if key == "description" and merged.get("description"):
                    continue
                merged[key] = value

        if merged_properties:
            merged["properties"] = merged_properties
            merged.setdefault("type", "object")
        if merged_required:
            merged["required"] = merged_required
        return merged

    def _normalize_schema_dict(
        self,
        schema: dict[str, Any],
        current_file: Path,
        seen_refs: set[tuple[Path, str]] | None = None,
    ) -> dict[str, Any]:
        seen_refs = seen_refs or set()
        schema = self.resolve_node(schema, current_file, seen_refs)
        if not isinstance(schema, dict):
            return {}

        normalized = copy.deepcopy(schema)

        if "allOf" in normalized:
            all_of = [
                self._normalize_schema_dict(subschema, current_file, seen_refs)
                for subschema in normalized.get("allOf", [])
            ]
            merged = self._merge_all_of(all_of)
            extras = {k: v for k, v in normalized.items() if k != "allOf"}
            merged.update(extras)
            normalized = merged

        for combiner in ("oneOf", "anyOf"):
            if combiner in normalized:
                normalized[combiner] = [
                    self._normalize_schema_dict(subschema, current_file, seen_refs)
                    for subschema in normalized.get(combiner, [])
                ]

        if "properties" in normalized and isinstance(normalized["properties"], dict):
            normalized["properties"] = {
                key: self._normalize_schema_dict(value, current_file, seen_refs)
                if isinstance(value, dict) else value
                for key, value in normalized["properties"].items()
            }
            normalized.setdefault("type", "object")

        if "items" in normalized and isinstance(normalized["items"], dict):
            normalized["items"] = self._normalize_schema_dict(normalized["items"], current_file, seen_refs)
            normalized.setdefault("type", "array")

        if "additionalProperties" in normalized and isinstance(normalized["additionalProperties"], dict):
            normalized["additionalProperties"] = self._normalize_schema_dict(
                normalized["additionalProperties"], current_file, seen_refs
            )

        if "type" not in normalized:
            if "properties" in normalized:
                normalized["type"] = "object"
            elif "items" in normalized:
                normalized["type"] = "array"

        return normalized

    def get_schema(self, schema_obj: dict, current_file: Path | None = None) -> dict:
        current_file = (current_file or self.root_path).resolve()
        return self._normalize_schema_dict(schema_obj or {}, current_file)


def operation_class_name(operation_id: str) -> str:
    return "".join(word.capitalize() for word in slugify(operation_id).split("_")) + "Params"


def clean_description(value: str | None, limit: int = 200) -> str:
    return (value or "").replace('"', '\\"')[:limit]


def module_doc(value: str | None, limit: int = 300) -> str:
    return (value or "").replace('"""', '\\"\\"\\"').strip()[:limit]


def get_template_env():
    global _TEMPLATE_ENV
    if _TEMPLATE_ENV is None:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        _TEMPLATE_ENV = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        _TEMPLATE_ENV.filters["pystr"] = lambda value: json.dumps("" if value is None else str(value))
        _TEMPLATE_ENV.filters["pyrepr"] = python_literal
        _TEMPLATE_ENV.filters["tojson"] = json.dumps
    return _TEMPLATE_ENV


def render_template(template_name: str, context: dict[str, Any]) -> str:
    template = get_template_env().get_template(template_name)
    return template.render(**context)


def extract_operations(spec: dict, resolver: SchemaResolver) -> list[dict]:
    """Extract all operations from the spec paths."""
    ops = []
    paths = spec.get("paths", {})
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options", "trace"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or slugify(f"{method}_{path_str}")
            params = []
            for p in path_item.get("parameters", []):
                params.append(resolver.resolve_node(p))
            for p in op.get("parameters", []):
                p = resolver.resolve_node(p)
                params = [pp for pp in params if pp.get("name") != p.get("name")]
                params.append(p)

            body = resolver.resolve_node(op.get("requestBody", {}))

            ops.append(
                {
                    "operation_id": op_id,
                    "method": method.upper(),
                    "path": path_str,
                    "summary": op.get("summary", ""),
                    "description": op.get("description", ""),
                    "parameters": params,
                    "request_body": body,
                    "responses": op.get("responses", {}),
                    "security": op.get("security"),
                    "tags": op.get("tags", []),
                }
            )
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


def build_operation_auth_map(ops: list[dict], global_security: list[dict]) -> dict[str, list[list[str]]]:
    op_auth_map: dict[str, list[list[str]]] = {}
    for op in ops:
        op_sec = op["security"]
        if op_sec is None:
            op_sec = global_security
        schemes_needed = []
        for sec_req in (op_sec or []):
            schemes_needed.append(list(sec_req.keys()))
        op_auth_map[op["operation_id"]] = schemes_needed
    return op_auth_map


def env_key_for_scheme(scheme_name: str, suffix: str) -> str:
    prefix = slugify(scheme_name).upper() or "AUTH"
    return f"{prefix}_{suffix}"


def build_auth_scheme_contexts(schemes: dict[str, dict[str, Any]], force_api_key: bool) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []

    if force_api_key and not any(defn.get("type") == "apiKey" for defn in schemes.values()):
        contexts.append(
            {
                "name": "apiKey",
                "type": "apiKey",
                "class_name": "APIKeyAuth",
                "env_vars": [{"name": "APIKEY_API_KEY", "example": "", "description": "API key credential"}],
                "registry_args": [
                    {"name": "env_var", "value": "APIKEY_API_KEY"},
                    {"name": "location", "value": "header"},
                    {"name": "param_name", "value": "X-API-Key"},
                ],
                "summary": "API Key",
            }
        )

    for scheme_name, scheme_def in schemes.items():
        scheme_type = scheme_def.get("type")
        if scheme_type == "apiKey":
            contexts.append(
                {
                    "name": scheme_name,
                    "type": "apiKey",
                    "class_name": "APIKeyAuth",
                    "env_vars": [
                        {
                            "name": env_key_for_scheme(scheme_name, "API_KEY"),
                            "example": "",
                            "description": f"API key for scheme {scheme_name}",
                        }
                    ],
                    "registry_args": [
                        {"name": "env_var", "value": env_key_for_scheme(scheme_name, "API_KEY")},
                        {"name": "location", "value": scheme_def.get("in", "header")},
                        {"name": "param_name", "value": scheme_def.get("name", "X-API-Key")},
                    ],
                    "summary": f"API Key ({scheme_name})",
                }
            )
            continue

        if scheme_type == "http":
            http_scheme = (scheme_def.get("scheme") or "").lower()
            if http_scheme == "bearer":
                contexts.append(
                    {
                        "name": scheme_name,
                        "type": "http_bearer",
                        "class_name": "BearerTokenAuth",
                        "env_vars": [
                            {
                                "name": env_key_for_scheme(scheme_name, "BEARER_TOKEN"),
                                "example": "",
                                "description": f"Bearer token for scheme {scheme_name}",
                            }
                        ],
                        "registry_args": [
                            {"name": "env_var", "value": env_key_for_scheme(scheme_name, "BEARER_TOKEN")},
                        ],
                        "summary": f"Bearer Token ({scheme_name})",
                    }
                )
            elif http_scheme == "basic":
                contexts.append(
                    {
                        "name": scheme_name,
                        "type": "http_basic",
                        "class_name": "BasicAuth",
                        "env_vars": [
                            {
                                "name": env_key_for_scheme(scheme_name, "USERNAME"),
                                "example": "",
                                "description": f"Basic auth username for scheme {scheme_name}",
                            },
                            {
                                "name": env_key_for_scheme(scheme_name, "PASSWORD"),
                                "example": "",
                                "description": f"Basic auth password for scheme {scheme_name}",
                            },
                        ],
                        "registry_args": [
                            {"name": "username_env", "value": env_key_for_scheme(scheme_name, "USERNAME")},
                            {"name": "password_env", "value": env_key_for_scheme(scheme_name, "PASSWORD")},
                        ],
                        "summary": f"HTTP Basic ({scheme_name})",
                    }
                )
            continue

        if scheme_type == "oauth2":
            flows = scheme_def.get("flows", {})
            client_credentials = flows.get("clientCredentials", {})
            token_url = client_credentials.get("tokenUrl")
            if token_url:
                scopes = " ".join(client_credentials.get("scopes", {}).keys())
                contexts.append(
                    {
                        "name": scheme_name,
                        "type": "oauth2_client_credentials",
                        "class_name": "OAuth2ClientCredentialsAuth",
                        "env_vars": [
                            {
                                "name": env_key_for_scheme(scheme_name, "CLIENT_ID"),
                                "example": "",
                                "description": f"OAuth2 client ID for scheme {scheme_name}",
                            },
                            {
                                "name": env_key_for_scheme(scheme_name, "CLIENT_SECRET"),
                                "example": "",
                                "description": f"OAuth2 client secret for scheme {scheme_name}",
                            },
                            {
                                "name": env_key_for_scheme(scheme_name, "TOKEN_URL"),
                                "example": token_url,
                                "description": f"OAuth2 token URL for scheme {scheme_name}",
                            },
                            {
                                "name": env_key_for_scheme(scheme_name, "SCOPES"),
                                "example": scopes,
                                "description": f"OAuth2 scopes (space-delimited) for scheme {scheme_name}",
                            },
                        ],
                        "registry_args": [
                            {"name": "client_id_env", "value": env_key_for_scheme(scheme_name, "CLIENT_ID")},
                            {"name": "client_secret_env", "value": env_key_for_scheme(scheme_name, "CLIENT_SECRET")},
                            {"name": "token_url_env", "value": env_key_for_scheme(scheme_name, "TOKEN_URL")},
                            {"name": "scopes_env", "value": env_key_for_scheme(scheme_name, "SCOPES")},
                            {"name": "default_token_url", "value": token_url},
                            {"name": "default_scopes", "value": scopes},
                        ],
                        "summary": f"OAuth2 Client Credentials ({scheme_name})",
                    }
                )

    return contexts


def summarize_auth_schemes(schemes: dict[str, dict[str, Any]], force_api_key: bool = False) -> str:
    contexts = build_auth_scheme_contexts(schemes, force_api_key)
    if not contexts:
        return "none"
    return ", ".join(ctx["summary"] for ctx in contexts)


def build_model_field(
    name: str,
    py_type: str,
    required: bool,
    description: str,
    default: Any = None,
    use_field_wrapper: bool = True,
) -> dict[str, str]:
    if required:
        return {
            "name": name,
            "type_annotation": py_type,
            "default_expr": f"Field(..., description={json.dumps(description)})" if use_field_wrapper else "...",
        }

    optional_type = f"Optional[{py_type}]"
    if use_field_wrapper:
        default_expr = f"Field({python_literal(default)}, description={json.dumps(description)})"
    else:
        default_expr = python_literal(default)

    return {
        "name": name,
        "type_annotation": optional_type,
        "default_expr": default_expr,
    }


def is_object_like_schema(schema: dict[str, Any]) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "object" or "properties" in schema:
        return True
    for combiner in ("oneOf", "anyOf"):
        branches = schema.get(combiner, [])
        if branches and all(is_object_like_schema(branch) for branch in branches):
            return True
    return False


def flatten_body_schema(schema: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]] | None:
    if not isinstance(schema, dict):
        return None

    if schema.get("type") == "object" or "properties" in schema:
        return dict(schema.get("properties", {})), set(schema.get("required", []))

    for combiner in ("oneOf", "anyOf"):
        branches = schema.get(combiner, [])
        if not branches or not all(is_object_like_schema(branch) for branch in branches):
            continue

        merged_properties: dict[str, dict[str, Any]] = {}
        required_intersection: set[str] | None = None
        for branch in branches:
            flattened = flatten_body_schema(branch)
            if not flattened:
                return None
            branch_properties, branch_required = flattened
            merged_properties.update(branch_properties)
            if required_intersection is None:
                required_intersection = set(branch_required)
            else:
                required_intersection &= set(branch_required)

        top_level_required = set(schema.get("required", []))
        if required_intersection is None:
            required_intersection = set()
        return merged_properties, required_intersection | top_level_required

    return None


def build_model_context(resolver: SchemaResolver, op: dict) -> dict[str, Any]:
    fields: list[dict[str, str]] = []

    for p in op["parameters"]:
        if not isinstance(p, dict):
            continue
        schema = resolver.get_schema(p.get("schema", {}))
        field = build_model_field(
            name=slugify(p.get("name", "param")),
            py_type=python_type(schema),
            required=p.get("required", False),
            description=clean_description(p.get("description")),
            default=p.get("default", schema.get("default")),
            use_field_wrapper=True,
        )
        fields.append(field)

    body = op.get("request_body", {})
    if body:
        content = body.get("content", {})
        for media in content.values():
            body_schema = resolver.get_schema(media.get("schema", {}))
            flattened_body = flatten_body_schema(body_schema)
            if flattened_body:
                props, required_props = flattened_body
                for prop_name, prop_schema in props.items():
                    resolved_prop_schema = resolver.get_schema(prop_schema)
                    fields.append(
                        build_model_field(
                            name=slugify(prop_name),
                            py_type=python_type(resolved_prop_schema),
                            required=prop_name in required_props,
                            description=clean_description(resolved_prop_schema.get("description")),
                            default=resolved_prop_schema.get("default"),
                            use_field_wrapper=prop_name in required_props,
                        )
                    )
            else:
                fields.append(
                    {
                        "name": "body",
                        "type_annotation": "Optional[Any]",
                        "default_expr": "None",
                    }
                )
            break

    return {
        "class_name": operation_class_name(op["operation_id"]),
        "docstring": clean_description(op.get("summary") or op.get("description") or op["operation_id"]),
        "fields": fields,
    }


def build_signature_entry(name: str, py_type: str, required: bool, default: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "type_annotation": py_type if required else f"{py_type} | None",
        "required": required,
        "default_expr": None if required else python_literal(default),
    }


def order_signature_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required_entries = [entry for entry in entries if entry.get("required")]
    optional_entries = [entry for entry in entries if not entry.get("required")]
    return required_entries + optional_entries


def build_tool_context(resolver: SchemaResolver, op: dict, auth_info: dict) -> dict[str, Any]:
    func_name = slugify(op["operation_id"])
    params = [p for p in op["parameters"] if isinstance(p, dict)]

    path_params = []
    query_params = []
    header_params = []
    signature_params: list[dict[str, Any]] = []

    for p in params:
        name = slugify(p.get("name", "param"))
        schema = resolver.get_schema(p.get("schema", {}))
        entry = build_signature_entry(
            name=name,
            py_type=python_type(schema),
            required=p.get("required", False),
            default=p.get("default", schema.get("default")),
        )
        signature_params.append(entry)

        location = p.get("in")
        mapping = {"original_name": p.get("name", name), "attr_name": name}
        if location == "path":
            path_params.append(mapping)
        elif location == "query":
            query_params.append(mapping)
        elif location == "header":
            header_params.append(mapping)

    request_body = op.get("request_body", {})
    has_body = bool(request_body)
    uses_raw_body = False
    body_props: list[dict[str, str]] = []
    body_signature_params: list[dict[str, Any]] = []

    if has_body:
        content = request_body.get("content", {})
        for media in content.values():
            body_schema = resolver.get_schema(media.get("schema", {}))
            flattened_body = flatten_body_schema(body_schema)
            if flattened_body:
                props, required_props = flattened_body
                for prop_name, prop_schema in props.items():
                    resolved_prop_schema = resolver.get_schema(prop_schema)
                    attr_name = slugify(prop_name)
                    body_props.append({"original_name": prop_name, "attr_name": attr_name})
                    body_signature_params.append(
                        build_signature_entry(
                            name=attr_name,
                            py_type=python_type(resolved_prop_schema),
                            required=prop_name in required_props,
                            default=resolved_prop_schema.get("default"),
                        )
                    )
            else:
                uses_raw_body = True
                body_signature_params.append(
                    {"name": "body", "type_annotation": "Any | None", "required": False, "default_expr": "None"}
                )
            break

    path_format = op["path"]
    for path_param in path_params:
        path_format = path_format.replace(
            "{" + path_param["original_name"] + "}",
            "{params." + path_param["attr_name"] + "}",
        )

    op_sec = op.get("security")
    if op_sec is None:
        op_sec = auth_info.get("global_security", [])
    needs_auth = bool(op_sec) or auth_info.get("has_auth")

    model_kwargs = [entry["name"] for entry in signature_params] + [entry["name"] for entry in body_signature_params]
    description = module_doc(op.get("description"))
    summary = module_doc(op.get("summary") or op["operation_id"])
    docstring = summary
    if description and description != summary:
        docstring = f"{summary}\n\n{description}"

    ordered_signature_params = order_signature_entries(signature_params + body_signature_params)

    return {
        "function_name": func_name,
        "class_name": operation_class_name(op["operation_id"]),
        "operation_id": op["operation_id"],
        "method": op["method"],
        "summary": summary,
        "description": description,
        "docstring_literal": python_literal(docstring),
        "signature_params": ordered_signature_params,
        "model_kwargs": model_kwargs,
        "path_format": path_format,
        "query_params": query_params,
        "header_params": header_params,
        "has_body": has_body,
        "uses_raw_body": uses_raw_body,
        "body_props": body_props,
        "needs_auth": needs_auth,
    }


# ──────────────────────────────────────────────────────────────
# Code generators
# ──────────────────────────────────────────────────────────────

def gen_validators() -> str:
    """Generate _validators.py with OAS format validators."""
    return render_template("_validators.py.j2", {})


def gen_auth(spec: dict, resolver: SchemaResolver, force_api_key: bool) -> str | None:
    """Generate _auth.py. Returns None if no auth needed."""
    auth_info = detect_auth(spec)
    schemes = auth_info["schemes"]

    if not schemes and not force_api_key:
        return None

    ops = extract_operations(spec, resolver)
    scheme_contexts = build_auth_scheme_contexts(schemes, force_api_key)

    context = {
        "scheme_contexts": scheme_contexts,
        "class_names": sorted({ctx["class_name"] for ctx in scheme_contexts}),
        "operation_auth_map": build_operation_auth_map(ops, auth_info["global_security"]),
    }
    return render_template("_auth.py.j2", context)


def gen_models(resolver: SchemaResolver, ops: list[dict]) -> str:
    """Generate _models.py with Pydantic request parameter models."""
    context = {
        "operations": [build_model_context(resolver, op) for op in ops],
    }
    return render_template("_models.py.j2", context)


def gen_server(spec: dict, resolver: SchemaResolver, ops: list[dict], server_name: str, auth_info: dict) -> str:
    """Generate server.py."""
    info = spec.get("info", {})
    servers = spec.get("servers", [])
    base_url = ""
    if servers and isinstance(servers[0], dict):
        base_url = servers[0].get("url", "")

    context = {
        "server_name": server_name,
        "api_title": info.get("title", server_name),
        "api_version": info.get("version", "1.0.0"),
        "base_url": base_url,
        "has_auth": auth_info["has_auth"],
        "operations": [build_tool_context(resolver, op, auth_info) for op in ops],
    }
    return render_template("server.py.j2", context)


def gen_env(spec: dict, server_name: str, auth_info: dict) -> str:
    servers = spec.get("servers", [])
    base_url = servers[0].get("url", "https://api.example.com") if servers else "https://api.example.com"
    scheme_contexts = build_auth_scheme_contexts(auth_info["schemes"], force_api_key=False)
    context = {
        "server_name": server_name,
        "base_url": base_url,
        "has_auth": auth_info["has_auth"],
        "scheme_contexts": scheme_contexts,
    }
    return render_template(".env.j2", context)


def gen_requirements(has_auth: bool) -> str:
    lines = [
        "fastmcp>=2.12.0,<3.0.0",
        "httpx>=0.27.0,<1.0.0",
        "pydantic>=2.0.0,<3.0.0",
        "python-dotenv>=1.0.0,<2.0.0",
    ]
    return "\n".join(lines) + "\n"


def gen_dockerfile(server_name: str) -> str:
    return render_template("Dockerfile.j2", {"server_name": server_name})


def gen_mcp_json(server_name: str) -> str:
    return render_template(".mcp.json.j2", {"server_name": server_name})


def gen_readme(spec: dict, server_name: str, ops: list[dict], auth_info: dict) -> str:
    info = spec.get("info", {})
    scheme_contexts = build_auth_scheme_contexts(auth_info["schemes"], force_api_key=False)
    context = {
        "server_name": server_name,
        "api_title": info.get("title", server_name),
        "operations": [
            {
                "operation_id": op["operation_id"],
                "summary": op.get("summary", ""),
            }
            for op in ops[:20]
        ],
        "tool_count": len(ops),
        "has_auth": auth_info["has_auth"],
        "auth_summaries": [ctx["summary"] for ctx in scheme_contexts],
    }
    return render_template("README.md.j2", context)


# ──────────────────────────────────────────────────────────────
# Main generator
# ──────────────────────────────────────────────────────────────

def generate(spec_path: Path, output_dir: Path, server_name: str, force_api_key: bool):
    h1("═══ MCP Forge — Server Generator ═══")

    spec = load_spec(spec_path)
    resolver = SchemaResolver(spec, spec_path)
    info = spec.get("info", {})
    if not server_name:
        server_name = slugify(info.get("title", "mcp_server"))

    ops = extract_operations(spec, resolver)
    auth_info = detect_auth(spec)

    if force_api_key and not auth_info["has_auth"]:
        auth_info["has_auth"] = True
        auth_info["schemes"] = {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}

    h2("Spec Info")
    print(f"  API      : {info.get('title', '(untitled)')}")
    print(f"  Version  : {info.get('version', '(none)')}")
    print(f"  Ops      : {len(ops)}")
    print(f"  Auth     : {summarize_auth_schemes(auth_info['schemes'], force_api_key)}")
    print(f"  Output   : {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    h2("Generating files")

    files = {
        "server.py": gen_server(spec, resolver, ops, server_name, auth_info),
        "_models.py": gen_models(resolver, ops),
        "_validators.py": gen_validators(),
        ".env": gen_env(spec, server_name, auth_info),
        "requirements.txt": gen_requirements(auth_info["has_auth"]),
        "Dockerfile": gen_dockerfile(server_name),
        ".mcp.json": gen_mcp_json(server_name),
        "README.md": gen_readme(spec, server_name, ops, auth_info),
    }

    auth_code = gen_auth(spec, resolver, force_api_key)
    if auth_code:
        files["_auth.py"] = auth_code

    for fname, content in files.items():
        (output_dir / fname).write_text(content)
        ok(f"Generated {fname}")

    h2("Summary")
    print(f"  Tools generated : {len(ops)}")
    print(f"  Auth            : {summarize_auth_schemes(auth_info['schemes'], force_api_key)}")
    print(f"  Output dir      : {output_dir}")
    print(f"\n  {GREEN}{BOLD}✓ Generation complete!{RESET}")
    print(f"\n  Next steps:")
    print(f"    cd {output_dir}")
    print(f"    python -m venv .venv && source .venv/bin/activate")
    print(f"    pip install -r requirements.txt")
    if auth_info["has_auth"]:
        print(f"    # Edit .env — set the auth variables for your selected security scheme(s)")
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
        """,
    )
    parser.add_argument("spec_file", help="Path to OpenAPI spec (YAML or JSON)")
    parser.add_argument("--output", "-o", metavar="DIR", help="Output directory (default: ./<server_name>)")
    parser.add_argument("--name", metavar="NAME", help="Server name override (default: derived from spec title)")
    parser.add_argument("--api-key", action="store_true", help="Force API Key auth even if not in spec")
    args = parser.parse_args()

    ensure_deps()

    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        print(f"{RED}Error: spec file not found: {spec_path}{RESET}")
        sys.exit(1)

    spec = load_spec(spec_path)
    info = spec.get("info", {})
    name = args.name or slugify(info.get("title", "mcp_server"))
    outdir = Path(args.output) if args.output else Path(name)

    generate(spec_path, outdir, name, args.api_key)


if __name__ == "__main__":
    main()
