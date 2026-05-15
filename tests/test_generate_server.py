"""test_generate_server.py — Unit tests for scripts/generate_server.py."""
from __future__ import annotations

import ast
import py_compile
import sys
import tempfile
from pathlib import Path

import pytest

import generate_server as gs
import validate_spec

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parent.parent / "examples"


def _resolver(spec: dict, root_path: Path | None = None) -> gs.SchemaResolver:
    if root_path is None:
        root_path = FIXTURES / "_dummy.yaml"
    return gs.SchemaResolver(spec, root_path)


def _load(fixture_name: str):
    spec = validate_spec.load_spec(FIXTURES / fixture_name)
    root_path = FIXTURES / fixture_name
    resolver = gs.SchemaResolver(spec, root_path)
    return spec, resolver


# ──────────────────────────────────────────────────────────────
# slugify
# ──────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert gs.slugify("listItems") == "listitems"

    def test_camel_case_lowercased(self):
        assert gs.slugify("GetUser").islower()

    def test_special_chars_replaced(self):
        result = gs.slugify("foo-bar baz.qux")
        assert result.isidentifier()

    def test_leading_digit_prefixed(self):
        result = gs.slugify("123abc")
        assert result.startswith("op_")
        assert result.isidentifier()

    def test_empty_string_returns_operation(self):
        assert gs.slugify("") == "operation"

    def test_underscores_deduplicated(self):
        result = gs.slugify("foo__bar---baz")
        assert "__" not in result

    def test_all_special_becomes_operation(self):
        result = gs.slugify("---")
        assert result == "operation"


# ──────────────────────────────────────────────────────────────
# python_type
# ──────────────────────────────────────────────────────────────

class TestPythonType:
    def test_string(self):
        assert gs.python_type({"type": "string"}) == "str"

    def test_integer(self):
        assert gs.python_type({"type": "integer"}) == "int"

    def test_number(self):
        assert gs.python_type({"type": "number"}) == "float"

    def test_boolean(self):
        assert gs.python_type({"type": "boolean"}) == "bool"

    def test_array_of_string(self):
        assert gs.python_type({"type": "array", "items": {"type": "string"}}) == "list[str]"

    def test_object(self):
        assert gs.python_type({"type": "object"}) == "dict[str, Any]"

    def test_empty_schema_returns_any(self):
        assert gs.python_type({}) == "Any"

    def test_nullable_wraps_base(self):
        result = gs.python_type({"type": "string", "nullable": True})
        assert "None" in result
        assert "str" in result

    def test_one_of_union(self):
        result = gs.python_type({"oneOf": [{"type": "string"}, {"type": "integer"}]})
        assert "str" in result
        assert "int" in result

    def test_any_of_union(self):
        result = gs.python_type({"anyOf": [{"type": "boolean"}, {"type": "number"}]})
        assert "bool" in result
        assert "float" in result

    def test_all_of_falls_through(self):
        # allOf with no merged type info → Any or str fallback
        result = gs.python_type({"allOf": [{"type": "object"}]})
        assert isinstance(result, str)


# ──────────────────────────────────────────────────────────────
# extract_operations
# ──────────────────────────────────────────────────────────────

class TestExtractOperations:
    def test_extracts_from_minimal(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        assert len(ops) >= 1
        assert ops[0]["operation_id"] == "listItems"

    def test_auto_generates_missing_operation_id(self):
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/no-id": {
                    "get": {"responses": {"200": {"description": "OK"}}}
                }
            }
        }
        resolver = _resolver(spec)
        ops = gs.extract_operations(spec, resolver)
        assert ops[0]["operation_id"] != ""
        assert ops[0]["operation_id"].isidentifier() or ops[0]["operation_id"].replace("_", "").isalnum()

    def test_extracts_method_and_path(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = ops[0]
        assert op["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        assert op["path"].startswith("/")

    def test_multiple_operations(self):
        spec, resolver = _load("with_auth.yaml")
        ops = gs.extract_operations(spec, resolver)
        op_ids = [o["operation_id"] for o in ops]
        assert "getProtected" in op_ids
        assert "getPublic" in op_ids

    def test_security_per_operation_captured(self):
        spec, resolver = _load("with_auth.yaml")
        ops = gs.extract_operations(spec, resolver)
        public_op = next((o for o in ops if o["operation_id"] == "getPublic"), None)
        assert public_op is not None
        # Public endpoint has security: [] — override explicitly
        assert public_op["security"] == []

    def test_body_kind_json(self):
        """body_kind is computed by extract_body_info from request_body."""
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = next(o for o in ops if o["operation_id"] == "jsonBody")
        body_info = gs.extract_body_info(op.get("request_body", {}), resolver)
        assert body_info["kind"] == "json_object"

    def test_body_kind_multipart(self):
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = next(o for o in ops if o["operation_id"] == "multipartBody")
        body_info = gs.extract_body_info(op.get("request_body", {}), resolver)
        assert body_info["kind"] == "multipart"

    def test_body_kind_form(self):
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = next(o for o in ops if o["operation_id"] == "formBody")
        body_info = gs.extract_body_info(op.get("request_body", {}), resolver)
        assert body_info["kind"] == "form"

    def test_body_kind_array(self):
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = next(o for o in ops if o["operation_id"] == "arrayBody")
        body_info = gs.extract_body_info(op.get("request_body", {}), resolver)
        assert body_info["kind"] == "json_array"

    def test_body_kind_binary(self):
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        op = next(o for o in ops if o["operation_id"] == "binaryBody")
        body_info = gs.extract_body_info(op.get("request_body", {}), resolver)
        assert body_info["kind"] == "binary"


# ──────────────────────────────────────────────────────────────
# detect_auth
# ──────────────────────────────────────────────────────────────

class TestDetectAuth:
    def test_no_auth(self):
        spec, _ = _load("minimal.yaml")
        result = gs.detect_auth(spec)
        assert result["has_auth"] is False
        assert result["schemes"] == {}

    def test_detects_apikey(self):
        spec, _ = _load("with_auth.yaml")
        result = gs.detect_auth(spec)
        assert result["has_auth"] is True
        assert "apiKeyAuth" in result["schemes"]

    def test_global_security_captured(self):
        spec, _ = _load("with_auth.yaml")
        result = gs.detect_auth(spec)
        assert isinstance(result["global_security"], list)
        assert any("apiKeyAuth" in req for req in result["global_security"])


# ──────────────────────────────────────────────────────────────
# build_auth_scheme_contexts
# ──────────────────────────────────────────────────────────────

class TestBuildAuthSchemeContexts:
    def test_apikey(self):
        schemes = {"myKey": {"type": "apiKey", "in": "header", "name": "X-Key"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert len(ctxs) == 1
        assert ctxs[0]["class_name"] == "APIKeyAuth"

    def test_bearer_plain(self):
        schemes = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert ctxs[0]["class_name"] == "BearerTokenAuth"

    def test_bearer_jwt(self):
        schemes = {"jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert ctxs[0]["class_name"] == "JWTAuth"

    def test_basic(self):
        schemes = {"basicAuth": {"type": "http", "scheme": "basic"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert ctxs[0]["class_name"] == "BasicAuth"

    def test_oauth2_client_credentials(self):
        schemes = {
            "oauth2": {
                "type": "oauth2",
                "flows": {"clientCredentials": {"tokenUrl": "https://t.co/token", "scopes": {}}}
            }
        }
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert ctxs[0]["class_name"] == "OAuth2ClientCredentialsAuth"

    def test_mutual_tls(self):
        schemes = {"mtlsAuth": {"type": "mutualTLS"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=False)
        assert ctxs[0]["class_name"] == "MTLSAuth"

    def test_force_api_key_adds_entry(self):
        schemes = {}  # no schemes in spec
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=True)
        assert any(c["class_name"] == "APIKeyAuth" for c in ctxs)

    def test_force_api_key_no_duplicate_when_already_present(self):
        schemes = {"myKey": {"type": "apiKey", "in": "header", "name": "X-Key"}}
        ctxs = gs.build_auth_scheme_contexts(schemes, force_api_key=True)
        api_key_contexts = [c for c in ctxs if c["class_name"] == "APIKeyAuth"]
        assert len(api_key_contexts) == 1


# ──────────────────────────────────────────────────────────────
# SchemaResolver
# ──────────────────────────────────────────────────────────────

class TestSchemaResolver:
    def test_resolve_internal_ref(self):
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Item": {"type": "object", "properties": {"id": {"type": "string"}}}
                }
            }
        }
        resolver = gs.SchemaResolver(spec, Path("/tmp/test_spec.yaml"))
        result, _ = resolver.resolve_ref_target("#/components/schemas/Item", Path("/tmp/test_spec.yaml"))
        assert result.get("type") == "object"

    def test_nonexistent_ref_returns_empty_dict(self):
        spec = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": {}}
        resolver = gs.SchemaResolver(spec, Path("/tmp/test_spec.yaml"))
        result, _ = resolver.resolve_ref_target("#/components/schemas/Missing", Path("/tmp/test_spec.yaml"))
        assert result == {}


# ──────────────────────────────────────────────────────────────
# gen_validators
# ──────────────────────────────────────────────────────────────

class TestGenValidators:
    def test_returns_string(self):
        result = gs.gen_validators()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_valid_python_syntax(self):
        result = gs.gen_validators()
        ast.parse(result)  # raises SyntaxError if invalid

    def test_contains_strict_model(self):
        result = gs.gen_validators()
        assert "StrictModel" in result

    def test_contains_format_validators(self):
        result = gs.gen_validators()
        assert "FORMAT_VALIDATORS" in result

    def test_contains_sanitize_response(self):
        result = gs.gen_validators()
        assert "sanitize_response" in result


# ──────────────────────────────────────────────────────────────
# gen_auth
# ──────────────────────────────────────────────────────────────

class TestGenAuth:
    def test_returns_none_without_auth(self):
        spec, resolver = _load("minimal.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert result is None

    def test_returns_string_with_apikey(self):
        spec, resolver = _load("with_auth.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert isinstance(result, str)
        assert "APIKeyAuth" in result

    def test_returns_valid_python_with_auth(self):
        spec, resolver = _load("with_auth.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        ast.parse(result)

    def test_jwt_auth_generated_for_jwt_spec(self):
        spec = validate_spec.load_spec(EXAMPLES / "jwt_auth_api.yaml")
        resolver = gs.SchemaResolver(spec, EXAMPLES / "jwt_auth_api.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert result is not None
        assert "JWTAuth" in result

    def test_mtls_auth_generated_for_mtls_spec(self):
        spec = validate_spec.load_spec(EXAMPLES / "mtls_api.yaml")
        resolver = gs.SchemaResolver(spec, EXAMPLES / "mtls_api.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert result is not None
        assert "MTLSAuth" in result

    def test_operation_auth_map_present(self):
        spec, resolver = _load("with_auth.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert "OPERATION_AUTH_MAP" in result

    def test_build_auth_registry_present(self):
        spec, resolver = _load("with_auth.yaml")
        result = gs.gen_auth(spec, resolver, force_api_key=False)
        assert "build_auth_registry" in result


# ──────────────────────────────────────────────────────────────
# gen_models
# ──────────────────────────────────────────────────────────────

class TestGenModels:
    def test_returns_string(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        result = gs.gen_models(resolver, ops)
        assert isinstance(result, str)

    def test_valid_python_syntax(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        result = gs.gen_models(resolver, ops)
        ast.parse(result)

    def test_contains_model_class(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        result = gs.gen_models(resolver, ops)
        assert "Params" in result or "BaseModel" in result

    def test_models_for_body_kinds(self):
        spec, resolver = _load("with_all_body_kinds.yaml")
        ops = gs.extract_operations(spec, resolver)
        result = gs.gen_models(resolver, ops)
        ast.parse(result)  # must be valid Python


# ──────────────────────────────────────────────────────────────
# gen_server
# ──────────────────────────────────────────────────────────────

class TestGenServer:
    def test_returns_string(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "test_server", auth_info)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_valid_python_syntax(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "test_server", auth_info)
        ast.parse(result)

    def test_contains_fastmcp(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "test_server", auth_info)
        assert "FastMCP" in result or "fastmcp" in result

    def test_contains_tool_decorator(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "test_server", auth_info)
        assert "@mcp.tool" in result

    def test_contains_circuit_breaker(self):
        spec, resolver = _load("minimal.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "test_server", auth_info)
        assert "_CircuitBreaker" in result or "circuit_breaker" in result.lower()

    def test_server_with_auth(self):
        spec, resolver = _load("with_auth.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "auth_server", auth_info)
        ast.parse(result)

    def test_banking_spec(self):
        spec = validate_spec.load_spec(EXAMPLES / "banking_api.yaml")
        resolver = gs.SchemaResolver(spec, EXAMPLES / "banking_api.yaml")
        ops = gs.extract_operations(spec, resolver)
        auth_info = gs.detect_auth(spec)
        result = gs.gen_server(spec, resolver, ops, "banking_server", auth_info)
        ast.parse(result)


# ──────────────────────────────────────────────────────────────
# gen_license
# ──────────────────────────────────────────────────────────────

class TestGenLicense:
    def test_returns_string(self):
        result = gs.gen_license("my_server")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_mit(self):
        result = gs.gen_license("my_server")
        assert "MIT" in result

    def test_contains_server_name(self):
        result = gs.gen_license("my_server")
        assert "my_server" in result

    def test_contains_current_year(self):
        import datetime
        year = str(datetime.date.today().year)
        result = gs.gen_license("any_name")
        assert year in result
