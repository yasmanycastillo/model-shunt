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
        return [os.path.abspath(p) for p in raw.split(os.pathsep) if p.strip()]
    return [os.getcwd()]


def resolve_allowed_path(path, for_write=False):
    """Resolve `path` and verify it stays inside an allowed root.

    Returns the absolute path, or raises PermissionError with a clear message.
    """
    abs_path = os.path.abspath(path)
    for root in get_allowed_roots():
        if abs_path == root or abs_path.startswith(root + os.sep):
            return abs_path
    action = "write to" if for_write else "read"
    raise PermissionError(
        f"Path rejected: '{path}' is outside the allowed roots. "
        f"{action.capitalize()} operations are restricted to the workspace "
        f"(set SHUNT_ALLOWED_ROOTS to expand)."
    )

TOOLS = [
    {
        "name": "bulk_read",
        "description": "Reads multiple or large files and answers a targeted question using a cheap, fast worker model (e.g. Gemini Flash, Groq, Ollama). Saves ~90% tokens by returning only structured bullet points.",
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
        "description": "Generates boilerplate code (tests, mocks, stubs, configs) matching the patterns of a reference file. Can write directly to disk without consuming frontier output tokens.",
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
                    "description": "Optional path where generated code should be written directly on disk"
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
        "description": "Discovers active models from the worker provider and recommends the best model for reading (high context / low cost) and writing (code intelligence). Enables calling agents to delegate dynamically to the best model.",
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

def handle_get_available_models(arguments):
    provider = arguments.get("provider")
    settings = resolve_settings(override_provider=provider)
    prov = provider or settings["provider"]

    try:
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
        except PermissionError as e:
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

    try:
        ref_path = resolve_allowed_path(ref_path)
        if target_path:
            target_path = resolve_allowed_path(target_path, for_write=True)
    except PermissionError as e:
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
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
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
        except Exception:
            continue

        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "model-shunt-mcp",
                        "version": "1.2.1"
                    }
                }
            })
        elif method == "notifications/initialized":
            pass
        elif method == "ping":
            send_response({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS}
            })
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})

            if name == "bulk_read":
                res = handle_bulk_read(args)
            elif name == "code_write":
                res = handle_code_write(args)
            elif name == "get_available_models":
                res = handle_get_available_models(args)
            else:
                res = {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res
            })
        else:
            if msg_id is not None:
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method {method} not found"}
                })

if __name__ == "__main__":
    main()
