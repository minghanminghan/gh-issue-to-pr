"""Tests for tool manifests and Claude SDK schema generation."""

import pytest

from tools.manifests import (
    EXECUTE_MANIFEST,
    PLAN_MANIFEST,
    SUMMARY_MANIFEST,
    TEST_MANIFEST,
    VALIDATE_MANIFEST,
    ToolManifest,
    build_tools,
)


class TestBuildTools:
    def test_plan_manifest_schemas(self):
        schemas = build_tools(PLAN_MANIFEST)
        names = {s["name"] for s in schemas}
        assert names == {"list_dir", "read_file", "write_file", "grep"}

    def test_execute_manifest_schemas(self):
        schemas = build_tools(EXECUTE_MANIFEST)
        names = {s["name"] for s in schemas}
        assert names == {"read_file", "write_file", "create_file", "grep", "execute_cli"}

    def test_validate_manifest_schemas(self):
        schemas = build_tools(VALIDATE_MANIFEST)
        names = {s["name"] for s in schemas}
        assert names == {"read_file", "write_file", "execute_cli", "grep"}

    def test_test_manifest_schemas(self):
        schemas = build_tools(TEST_MANIFEST)
        names = {s["name"] for s in schemas}
        assert names == {"read_file", "append_file", "execute_cli", "grep"}

    def test_summary_manifest_schemas(self):
        schemas = build_tools(SUMMARY_MANIFEST)
        names = {s["name"] for s in schemas}
        assert names == {"list_dir", "read_file", "grep", "execute_cli"}

    def test_schema_structure(self):
        schemas = build_tools(PLAN_MANIFEST)
        for schema in schemas:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"
            assert "properties" in schema["input_schema"]

    def test_unknown_tool_raises(self):
        bad_manifest = ToolManifest(agent_name="test", tool_names=["nonexistent_tool"])
        with pytest.raises(ValueError, match="Unknown tool"):
            build_tools(bad_manifest)

    def test_plan_agent_has_no_execute_cli(self):
        schemas = build_tools(PLAN_MANIFEST)
        names = {s["name"] for s in schemas}
        assert "execute_cli" not in names

    def test_plan_agent_has_no_create_or_append(self):
        schemas = build_tools(PLAN_MANIFEST)
        names = {s["name"] for s in schemas}
        assert "create_file" not in names
        assert "append_file" not in names

    def test_test_agent_has_no_write_file(self):
        schemas = build_tools(TEST_MANIFEST)
        names = {s["name"] for s in schemas}
        assert "write_file" not in names

    def test_execute_cli_schema_has_cmd_property(self):
        schemas = build_tools(EXECUTE_MANIFEST)
        execute_cli_schema = next(s for s in schemas if s["name"] == "execute_cli")
        assert "cmd" in execute_cli_schema["input_schema"]["properties"]
        assert "cmd" in execute_cli_schema["input_schema"]["required"]
