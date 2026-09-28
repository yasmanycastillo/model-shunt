#!/usr/bin/env python3
"""
Model-Shunt MCP Server
Universal stdio MCP server exposing bulk_read, code_write, and get_available_models tools.
Agent-agnostic (supports Antigravity, Cursor, Claude Desktop, Windsurf, Aider, etc.).
Zero external pip dependencies (pure Python standard library).
"""

import sys
import os
import json

# Allow running as a plain script (python3 src/model_shunt/server.py) in addition
# to being imported as an installed package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model_shunt.worker import (
    run_worker,
    run_bulk_reader,
    clean_markdown_fences,
    number_file_lines,
    is_binary_file,
    resolve_settings,
    fetch_available_models,
    get_best_model
)


def get_allowed_roots():
    """Directories that bulk_read/code_write may touch.

    Defaults to the server's working directory (the agent's workspace).
    Expand or override with SHUNT_ALLOWED_ROOTS, a PATH-style list
    (os.pathsep-separated absolute or relative paths).
    """
    raw = os.environ.get("SHUNT_ALLOWED_ROOTS")
    if raw and raw.strip():
        return [os.path.realpath(p.strip()) for p in raw.split(os.pathsep) if p.strip()]
    return [os.path.realpath(os.getcwd())]


def resolve_allowed_path(path, for_write=False):
    """Resolve `path` and verify it stays inside an allowed root.

    Resolves symlinks in the path and its existing parents, including for new
    write targets. Returns the canonical path or raises PermissionError.
    """
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("path must be a non-empty string without null bytes")
    abs_path = os.path.realpath(path)
    for root in get_allowed_roots():
        try:
            if os.path.commonpath([abs_path, root]) == root:
                return abs_path
        except ValueError:
            # Paths on different drives cannot share an allowed root.
            continue
    action = "write to" if for_write else "read"
    raise PermissionError(
        f"Path rejected: '{path}' is outside the allowed roots. "
        f"{action.capitalize()} operations are restricted to the workspace "
        f"(set SHUNT_ALLOWED_ROOTS to expand)."
    )

TOOLS = [
    {
        "name": "bulk_read",
        "description": "Reads text files within the allowed workspace roots and sends their content and a question to the configured worker provider (remote or local). Requests a concise answer with line citations. Does not modify files; token savings depend on the input and response.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The specific question to answer about the files"
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths to analyze"
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override (or 'auto' to select the best available reader model)"
                },
                "provider": {
                    "type": "string",
                    "description": "Optional provider override (gemini, groq, openai, deepseek, anthropic, ollama, openrouter)"
                }
            },
            "required": ["question", "file_paths"]
        }
    },
    {
        "name": "code_write",
        "description": "Sends a specification and the content of a reference file to the configured worker provider (remote or local) to generate code. Returns the code when target_path is omitted; otherwise creates parent directories and writes the code, overwriting any existing target file. File access is restricted to the allowed workspace roots.",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "Description of what code to generate"
                },
                "reference_path": {
                    "type": "string",
                    "description": "Path to reference file whose conventions, style, and structure should be replicated"
                },
                "target_path": {
                    "type": "string",
                    "description": "Optional output path within the allowed roots. Creates missing parent directories and overwrites an existing file."
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override (or 'auto' to select the best available writer model)"
                },
                "provider": {
                    "type": "string",
                    "description": "Optional provider override (gemini, groq, openai, deepseek, anthropic, ollama, openrouter)"
                }
            },
            "required": ["spec", "reference_path"]
        }
    },
    {
        "name": "get_available_models",
        "description": "Queries the configured worker provider for available models and recommends reader and writer models using built-in preferences. Falls back to a built-in recommendation list when discovery fails or is unsupported; fallback entries are not verified as currently available. Does not modify files.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Optional provider to query (gemini, groq, openai, deepseek, anthropic, ollama, openrouter). Defaults to active provider."
                }
            }
        }
    }
]

def validate_tool_arguments(name, arguments):
    """Validate the declared string/array inputs without external dependencies."""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
    for field in schema.get("required", []):
        if field not in arguments:
            return f"{field} is required"
    for field, rules in schema["properties"].items():
        if field not in arguments:
            continue
        value = arguments[field]
        if rules["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                return f"{field} must be a non-empty string"
        elif rules["type"] == "array":
            if not isinstance(value, list) or not value:
                return f"{field} must be a non-empty array"
            if any(not isinstance(item, str) or not item.strip() for item in value):
                return f"{field} must contain non-empty strings"
    return None

def handle_get_available_models(arguments):
    provider = arguments.get("provider")

    try:
        settings = resolve_settings(override_provider=provider)
        prov = provider or settings["provider"]
        models = fetch_available_models(provider=prov)
        best_reader = get_best_model("bulk-reader", provider=prov, available_models=models)
        best_writer = get_best_model("code-writer", provider=prov, available_models=models)

        info = {
            "provider": prov,
            "base_url": settings["base_url"],
            "configured_default_model": settings["model"],
            "best_model_for_reader": best_reader,
            "best_model_for_writer": best_writer,
            "available_models_count": len(models),
            "available_models": models
        }
        return {"content": [{"type": "text", "text": json.dumps(info, indent=2)}]}
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Error fetching models: {e}"}]}

def handle_bulk_read(arguments):
    question = arguments.get("question")
    file_paths = arguments.get("file_paths", [])
    model = arguments.get("model")
    provider = arguments.get("provider")

    if not question:
        return {"isError": True, "content": [{"type": "text", "text": "Error: question is required"}]}
    if not file_paths:
        return {"isError": True, "content": [{"type": "text", "text": "Error: file_paths must not be empty"}]}

    payload_parts = []
    for fp in file_paths:
        try:
            fp = resolve_allowed_path(fp)
        except (ValueError, TypeError, OSError) as e:
            return {"isError": True, "content": [{"type": "text", "text": f"Error: {e}"}]}
        if not os.path.isfile(fp):
            return {"isError": True, "content": [{"type": "text", "text": f"Error: file not found: {fp}"}]}
        if is_binary_file(fp):
            return {"isError": True, "content": [{"type": "text", "text": f"Error: '{fp}' appears to be a binary file and cannot be read by bulk_read."}]}
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            payload_parts.append(f'<file path="{fp}">\n{content}\n</file>')
        except Exception as e:
            return {"isError": True, "content": [{"type": "text", "text": f"Error reading {fp}: {e}"}]}

    payload_parts.append(f"Question: {question}")
    full_payload = "\n\n".join(payload_parts)
    full_payload = number_file_lines(full_payload)

    try:
        ans = run_bulk_reader(
            content=full_payload,
            override_provider=provider,
            override_model=model
        )
        return {"content": [{"type": "text", "text": ans}]}
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Worker execution failed: {e}"}]}

def handle_code_write(arguments):
    spec = arguments.get("spec")
    ref_path = arguments.get("reference_path")
    target_path = arguments.get("target_path")
    model = arguments.get("model")
    provider = arguments.get("provider")

    if not spec:
        return {"isError": True, "content": [{"type": "text", "text": "Error: spec is required"}]}
    if not ref_path:
        return {"isError": True, "content": [{"type": "text", "text": "Error: reference_path is required"}]}

    try:
        ref_path = resolve_allowed_path(ref_path)
        if target_path:
            target_path = resolve_allowed_path(target_path, for_write=True)
    except (ValueError, TypeError, OSError) as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Error: {e}"}]}

    if not ref_path or not os.path.isfile(ref_path):
        return {"isError": True, "content": [{"type": "text", "text": f"Error: reference file not found: {ref_path}"}]}
    if is_binary_file(ref_path):
        return {"isError": True, "content": [{"type": "text", "text": f"Error: reference file '{ref_path}' is a binary file."}]}

    try:
        with open(ref_path, "r", encoding="utf-8", errors="replace") as f:
            ref_content = f.read()
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Error reading reference file {ref_path}: {e}"}]}

    full_payload = f"Spec: {spec}\n\nReference File ({ref_path}):\n{ref_content}"

    try:
        code = run_worker(
            mode="code-writer",
            content=full_payload,
            override_provider=provider,
            override_model=model
        )
        clean = clean_markdown_fences(code)
        if target_path:
            # Generation may take time: recheck parents before touching disk.
            target_path = resolve_allowed_path(target_path, for_write=True)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            target_path = resolve_allowed_path(target_path, for_write=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(clean + "\n")
            line_count = len(clean.splitlines())
            return {"content": [{"type": "text", "text": f"Successfully generated {line_count} lines of code written to {target_path}"}]}
        else:
            return {"content": [{"type": "text", "text": clean}]}
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Worker execution failed: {e}"}]}

def send_response(response_dict):
    body = json.dumps(response_dict)
    sys.stdout.write(body + "\n")
    sys.stdout.flush()

def error_response(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_request(req):
    """Dispatch one request, keeping tool failures inside that request."""
    if not isinstance(req, dict):
        return error_response(None, -32600, "Invalid Request: expected an object")
    msg_id = req.get("id")
    if isinstance(msg_id, bool) or not isinstance(msg_id, (str, int, float, type(None))):
        return error_response(None, -32600, "Invalid Request: invalid id")
    method = req.get("method")
    if req.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
        return error_response(msg_id, -32600, "Invalid Request: expected JSON-RPC 2.0 and a method")
    if "id" not in req:
        # Notifications do not produce responses or invoke request-only tools.
        return None
    params = req.get("params", {})
    if not isinstance(params, dict):
        return error_response(msg_id, -32602, "params must be an object")

    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "model-shunt-mcp", "version": "1.2.1"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not any(tool["name"] == name for tool in TOOLS):
            return error_response(msg_id, -32602, "Unknown or missing tool name")
        args = params.get("arguments", {})
        validation_error = validate_tool_arguments(name, args)
        if validation_error:
            return error_response(msg_id, -32602, validation_error)
        try:
            if name == "bulk_read":
                result = handle_bulk_read(args)
            elif name == "code_write":
                result = handle_code_write(args)
            else:
                result = handle_get_available_models(args)
        except Exception as e:
            result = {"isError": True, "content": [{"type": "text", "text": f"Tool execution failed: {e}"}]}
    else:
        return error_response(msg_id, -32601, f"Method {method} not found")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            send_response(error_response(None, -32700, "Parse error: invalid JSON"))
            continue
        response = handle_request(req)
        if response is not None:
            send_response(response)

if __name__ == "__main__":
    main()
