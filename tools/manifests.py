"""Per-agent tool manifests and LiteLLM/OpenAI tool-schema builder."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolManifest:
    agent_name: str
    tool_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-agent manifests (spec §Tool Use → Per-agent manifests)
# ---------------------------------------------------------------------------

PLAN_MANIFEST = ToolManifest(
    agent_name="plan",
    tool_names=["list_dir", "read_file", "write_file", "grep"],
)

EXECUTE_MANIFEST = ToolManifest(
    agent_name="execute",
    tool_names=["read_file", "write_file", "create_file", "grep", "execute_cli"],
)

VALIDATE_MANIFEST = ToolManifest(
    agent_name="validate",
    tool_names=["read_file", "write_file", "execute_cli", "grep"],
)

TEST_MANIFEST = ToolManifest(
    agent_name="test",
    tool_names=["read_file", "append_file", "execute_cli", "grep"],
)

SUMMARY_MANIFEST = ToolManifest(
    agent_name="summary",
    tool_names=["list_dir", "read_file", "grep", "execute_cli"],
)

# ---------------------------------------------------------------------------
# Claude API tool schemas
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict] = {
    "list_dir": {
        "name": "list_dir",
        "description": "List files and directories at the given path (relative to repo root).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to repo root. Use '.' for repo root.",
                }
            },
            "required": ["path"],
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "Read the full contents of a file (relative to repo root or run dir).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root.",
                }
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": (
            "Write (overwrite) content to a file. "
            "Only permitted for files in your agent's allowed output list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to repo root or run dir)."},
                "content": {"type": "string", "description": "Full content to write."},
            },
            "required": ["path", "content"],
        },
    },
    "create_file": {
        "name": "create_file",
        "description": "Create a new file. Fails if the file already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to repo root)."},
                "content": {"type": "string", "description": "Initial file content."},
            },
            "required": ["path", "content"],
        },
    },
    "append_file": {
        "name": "append_file",
        "description": "Append content to an existing file. Only permitted for test files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Test file path (relative to repo root)."},
                "content": {"type": "string", "description": "Content to append."},
            },
            "required": ["path", "content"],
        },
    },
    "grep": {
        "name": "grep",
        "description": "Search for a regex pattern in files. Returns matching lines with file:line context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression pattern to search for."},
                "path": {
                    "type": "string",
                    "description": "File or directory to search (relative to repo root). Use '.' for repo root.",
                },
                "flags": {
                    "type": "string",
                    "description": "Optional flags: 'i' for case-insensitive.",
                    "default": "",
                },
            },
            "required": ["pattern", "path"],
        },
    },
    "execute_cli": {
        "name": "execute_cli",
        "description": (
            "Execute a CLI command from the allowlist. "
            "Shell metacharacters and '..' path components are rejected. "
            "CWD is always the repo root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Full command string, e.g. 'pytest tests/' or 'ruff check .'",
                }
            },
            "required": ["cmd"],
        },
    },
}


def build_tools(manifest: ToolManifest) -> list[dict]:
    """Convert a ToolManifest into LiteLLM/OpenAI function-calling schemas."""
    result = []
    for name in manifest.tool_names:
        if name not in _SCHEMAS:
            raise ValueError(f"Unknown tool '{name}' in manifest for agent '{manifest.agent_name}'")
        s = _SCHEMAS[name]
        result.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        })
    return result
