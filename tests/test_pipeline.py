"""test_pipeline.py — Integration tests for the full MCP Forge generation pipeline."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import generate_server as gs
import validate_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
SCRIPTS = REPO_ROOT / "scripts"
FIXTURES = Path(__file__).parent / "fixtures"


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _run_generate(spec_path: Path, output_dir: Path, name: str, extra_args: list[str] | None = None) -> None:
    """Run generate_server.py as a subprocess."""
    cmd = [
        sys.executable,
        str(SCRIPTS / "generate_server.py"),
        str(spec_path),
        "--output", str(output_dir),
        "--name", name,
    ] + (extra_args or [])
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"generate_server.py failed:\n{result.stdout}\n{result.stderr}"


def _run_validate(spec_path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(SCRIPTS / "validate_spec.py"),
        str(spec_path),
    ] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True)


def _parse_py_files(directory: Path) -> None:
    """Assert all .py files in directory are valid Python syntax."""
    py_files = list(directory.glob("*.py"))
    assert py_files, f"No .py files found in {directory}"
    for f in py_files:
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            pytest.fail(f"SyntaxError in {f.name}: {e}")


# ──────────────────────────────────────────────────────────────
# Full pipeline: banking spec (all 10 output files)
# ──────────────────────────────────────────────────────────────

class TestFullPipelineBanking:
    EXPECTED_FILES = {
        "server.py", "_models.py", "_validators.py", "_auth.py",
        ".env", "requirements.txt", "Dockerfile", ".mcp.json", "README.md", "LICENSE",
    }

    def test_all_10_files_generated(self, tmp_path):
        _run_generate(EXAMPLES / "banking_api.yaml", tmp_path, "banking_server")
        generated = {f.name for f in tmp_path.iterdir()}
        missing = self.EXPECTED_FILES - generated
        assert not missing, f"Missing files: {missing}"

    def test_all_python_files_valid_syntax(self, tmp_path):
        _run_generate(EXAMPLES / "banking_api.yaml", tmp_path, "banking_server")
        _parse_py_files(tmp_path)

    def test_license_contains_mit(self, tmp_path):
        _run_generate(EXAMPLES / "banking_api.yaml", tmp_path, "banking_server")
        license_text = (tmp_path / "LICENSE").read_text()
        assert "MIT" in license_text

    def test_requirements_includes_fastmcp(self, tmp_path):
        _run_generate(EXAMPLES / "banking_api.yaml", tmp_path, "banking_server")
        reqs = (tmp_path / "requirements.txt").read_text()
        assert "fastmcp" in reqs

    def test_dockerfile_exists_and_has_python(self, tmp_path):
        _run_generate(EXAMPLES / "banking_api.yaml", tmp_path, "banking_server")
        dockerfile = (tmp_path / "Dockerfile").read_text()
        assert "python" in dockerfile.lower()


# ──────────────────────────────────────────────────────────────
# Full pipeline: auth expansion spec
# ──────────────────────────────────────────────────────────────

class TestFullPipelineAuthExpansion:
    def test_auth_file_generated(self, tmp_path):
        _run_generate(EXAMPLES / "auth_expansion_api.yaml", tmp_path, "auth_server")
        assert (tmp_path / "_auth.py").exists()

    def test_auth_file_valid_syntax(self, tmp_path):
        _run_generate(EXAMPLES / "auth_expansion_api.yaml", tmp_path, "auth_server")
        ast.parse((tmp_path / "_auth.py").read_text())

    def test_operation_auth_map_present(self, tmp_path):
        _run_generate(EXAMPLES / "auth_expansion_api.yaml", tmp_path, "auth_server")
        auth_content = (tmp_path / "_auth.py").read_text()
        assert "OPERATION_AUTH_MAP" in auth_content


# ──────────────────────────────────────────────────────────────
# Full pipeline: JWT auth spec
# ──────────────────────────────────────────────────────────────

class TestFullPipelineJWT:
    def test_auth_file_contains_jwt_auth(self, tmp_path):
        _run_generate(EXAMPLES / "jwt_auth_api.yaml", tmp_path, "jwt_server")
        auth_content = (tmp_path / "_auth.py").read_text()
        assert "JWTAuth" in auth_content

    def test_jwt_auth_file_valid_syntax(self, tmp_path):
        _run_generate(EXAMPLES / "jwt_auth_api.yaml", tmp_path, "jwt_server")
        ast.parse((tmp_path / "_auth.py").read_text())

    def test_jwt_env_vars_in_env_file(self, tmp_path):
        _run_generate(EXAMPLES / "jwt_auth_api.yaml", tmp_path, "jwt_server")
        env_content = (tmp_path / ".env").read_text()
        assert "JWT_SECRET" in env_content


# ──────────────────────────────────────────────────────────────
# Full pipeline: mTLS auth spec
# ──────────────────────────────────────────────────────────────

class TestFullPipelineMTLS:
    def test_auth_file_contains_mtls_auth(self, tmp_path):
        _run_generate(EXAMPLES / "mtls_api.yaml", tmp_path, "mtls_server")
        auth_content = (tmp_path / "_auth.py").read_text()
        assert "MTLSAuth" in auth_content

    def test_mtls_auth_file_valid_syntax(self, tmp_path):
        _run_generate(EXAMPLES / "mtls_api.yaml", tmp_path, "mtls_server")
        ast.parse((tmp_path / "_auth.py").read_text())

    def test_mtls_env_vars_in_env_file(self, tmp_path):
        _run_generate(EXAMPLES / "mtls_api.yaml", tmp_path, "mtls_server")
        env_content = (tmp_path / ".env").read_text()
        assert "MTLS_CERT_PATH" in env_content

    def test_server_py_valid_syntax(self, tmp_path):
        _run_generate(EXAMPLES / "mtls_api.yaml", tmp_path, "mtls_server")
        ast.parse((tmp_path / "server.py").read_text())


# ──────────────────────────────────────────────────────────────
# validate_spec.py CLI integration
# ──────────────────────────────────────────────────────────────

class TestValidateSpecCLI:
    def test_banking_spec_passes(self):
        result = _run_validate(EXAMPLES / "banking_api.yaml")
        assert result.returncode == 0

    def test_minimal_fixture_passes(self):
        result = _run_validate(FIXTURES / "minimal.yaml")
        assert result.returncode == 0

    def test_auth_expansion_passes(self):
        result = _run_validate(EXAMPLES / "auth_expansion_api.yaml")
        assert result.returncode == 0

    def test_error_fixture_fails(self):
        result = _run_validate(FIXTURES / "with_errors.yaml")
        assert result.returncode != 0

    def test_strict_flag_blocks_on_warnings(self, tmp_path):
        """A spec with warnings should exit 1 under --strict."""
        # minimal spec has no servers → warning
        spec_with_warnings = tmp_path / "no_servers.yaml"
        spec_with_warnings.write_text(
            "openapi: '3.0.3'\n"
            "info:\n  title: T\n  version: '1'\n"
            "paths:\n"
            "  /x:\n"
            "    get:\n"
            "      operationId: x\n"
            "      summary: X\n"
            "      responses:\n"
            "        '200':\n"
            "          description: OK\n"
            "          content:\n"
            "            application/json:\n"
            "              schema:\n"
            "                type: object\n"
            "        '400':\n"
            "          description: Bad\n"
        )
        result = _run_validate(spec_with_warnings, ["--strict"])
        assert result.returncode != 0

    def test_quiet_flag_suppresses_output(self):
        result = _run_validate(EXAMPLES / "banking_api.yaml", ["--quiet"])
        assert result.returncode == 0
        # Quiet mode should have minimal/no output
        assert len(result.stdout.strip().splitlines()) <= 2

    def test_output_flag_writes_json_report(self, tmp_path):
        report_path = tmp_path / "report.json"
        result = _run_validate(
            EXAMPLES / "banking_api.yaml",
            ["--output", str(report_path)]
        )
        assert result.returncode == 0
        assert report_path.exists()
        import json
        report = json.loads(report_path.read_text())
        assert isinstance(report, dict)


# ──────────────────────────────────────────────────────────────
# Body kinds: all 5 body kinds generate valid code
# ──────────────────────────────────────────────────────────────

class TestAllBodyKinds:
    def test_all_body_kinds_generate_valid_python(self, tmp_path):
        _run_generate(FIXTURES / "with_all_body_kinds.yaml", tmp_path, "body_server")
        _parse_py_files(tmp_path)

    def test_json_body_in_tool_function(self, tmp_path):
        _run_generate(FIXTURES / "with_all_body_kinds.yaml", tmp_path, "body_server")
        server = (tmp_path / "server.py").read_text()
        assert "jsonBody" in server or "json_body" in server

    def test_multipart_body_in_models(self, tmp_path):
        _run_generate(FIXTURES / "with_all_body_kinds.yaml", tmp_path, "body_server")
        models = (tmp_path / "_models.py").read_text()
        ast.parse(models)  # valid Python with multipart fields

    def test_form_body_in_models(self, tmp_path):
        _run_generate(FIXTURES / "with_all_body_kinds.yaml", tmp_path, "body_server")
        models = (tmp_path / "_models.py").read_text()
        ast.parse(models)


# ──────────────────────────────────────────────────────────────
# Complex schemas
# ──────────────────────────────────────────────────────────────

class TestComplexSchemas:
    def test_complex_spec_generates_without_error(self, tmp_path):
        _run_generate(FIXTURES / "with_complex_schemas.yaml", tmp_path, "complex_server")
        assert (tmp_path / "server.py").exists()

    def test_complex_spec_all_python_files_valid(self, tmp_path):
        _run_generate(FIXTURES / "with_complex_schemas.yaml", tmp_path, "complex_server")
        _parse_py_files(tmp_path)

    def test_complex_spec_models_valid(self, tmp_path):
        _run_generate(FIXTURES / "with_complex_schemas.yaml", tmp_path, "complex_server")
        ast.parse((tmp_path / "_models.py").read_text())


# ──────────────────────────────────────────────────────────────
# Force API key flag
# ──────────────────────────────────────────────────────────────

class TestForceApiKey:
    def test_force_apikey_on_spec_without_auth(self, tmp_path):
        _run_generate(FIXTURES / "minimal.yaml", tmp_path, "forced_auth_server", ["--api-key"])
        assert (tmp_path / "_auth.py").exists()
        auth_content = (tmp_path / "_auth.py").read_text()
        assert "APIKeyAuth" in auth_content

    def test_force_apikey_auth_file_valid_syntax(self, tmp_path):
        _run_generate(FIXTURES / "minimal.yaml", tmp_path, "forced_auth_server", ["--api-key"])
        ast.parse((tmp_path / "_auth.py").read_text())
