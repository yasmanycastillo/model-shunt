#!/usr/bin/env python3
"""
Model-Shunt Test Suite
Verifies engine resolution, model discovery, binary checks, hooks, and MCP stdio protocol.
"""
import sys
import os
import json
import re
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import model_shunt.worker as worker_mod
from model_shunt.worker import (
    resolve_settings,
    clean_markdown_fences,
    is_binary_file,
    get_best_model,
    number_file_lines,
    estimate_tokens,
    split_numbered_payload,
    run_bulk_reader,
    RECOMMENDED_MODELS
)

def test_engine_resolution():
    print("[1/6] Testing engine settings resolution and model selection...")
    os.environ["SHUNT_PROVIDER"] = "gemini"
    os.environ["SHUNT_MODEL"] = "gemini-2.5-flash"
    os.environ["SHUNT_API_KEY"] = "dummy_key"
    cfg = resolve_settings()
    assert cfg["provider"] == "gemini", f"Expected gemini, got {cfg['provider']}"
    assert cfg["model"] == "gemini-2.5-flash", f"Expected gemini-2.5-flash, got {cfg['model']}"
    assert "googleapis" in cfg["base_url"], f"Unexpected base_url: {cfg['base_url']}"
    assert cfg["timeout"] >= 60, f"Expected timeout >= 60, got {cfg['timeout']}"

    # Test overrides
    cfg_override = resolve_settings(override_provider="groq", override_model="llama-3.3-70b-versatile")
    assert cfg_override["provider"] == "groq"
    assert cfg_override["model"] == "llama-3.3-70b-versatile"

    # Test best model recommendations
    best_reader = get_best_model("bulk-reader", provider="gemini")
    assert "flash" in best_reader
    best_writer = get_best_model("code-writer", provider="groq")
    assert best_writer in RECOMMENDED_MODELS["groq"]["writer"]

    code_with_fences = "```python\nprint('hello world')\n```"
    assert clean_markdown_fences(code_with_fences) == "print('hello world')"
    print("  -> Engine resolution & model selection passed.")

def test_binary_detection():
    print("[2/6] Testing binary file detection...")
    bin_file = "/tmp/test_binary.bin"
    txt_file = "/tmp/test_text.txt"
    try:
        with open(bin_file, "wb") as f:
            f.write(b"Hello\x00World\x01\x02\x03")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("Normal text file with lines\nLine 2\n")

        assert is_binary_file(bin_file) is True, "Expected binary file detection to return True"
        assert is_binary_file(txt_file) is False, "Expected text file detection to return False"
        print("  -> Binary detection passed.")
    finally:
        for p in (bin_file, txt_file):
            if os.path.exists(p):
                os.remove(p)

def test_hooks():
    print("[3/6] Testing PreToolUse hooks (agent-agnostic)...")
    hook_size = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin/hooks/check-file-size")
    hook_bash = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin/hooks/check-bash-read")

    # 1. Targeted read with limit/offset should allow
    p = subprocess.Popen([hook_size], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, _ = p.communicate(json.dumps({"tool_input": {"file_path": "/etc/hosts", "limit": 10}}))
    res = json.loads(out.strip())
    assert res.get("decision") == "allow", f"Expected allow, got {res}"

    # 2. Large file whole read should block
    test_large_file = "/tmp/test_large_file.txt"
    with open(test_large_file, "w") as f:
        f.write("\n" * 400)

    try:
        p = subprocess.Popen([hook_size], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, _ = p.communicate(json.dumps({"tool_input": {"file_path": test_large_file}}))
        res = json.loads(out.strip())
        assert res.get("decision") == "block", f"Expected block, got {res}"

        # 3. Test check-bash-read with flags (e.g. cat -n /tmp/test_large_file.txt)
        p = subprocess.Popen([hook_bash], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, _ = p.communicate(json.dumps({"tool_input": {"command": f"cat -n {test_large_file}"}}))
        res = json.loads(out.strip())
        assert res.get("decision") == "block", f"Expected block on cat -n, got {res}"

        # 4. Pipe should be allowed
        p = subprocess.Popen([hook_bash], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, _ = p.communicate(json.dumps({"tool_input": {"command": f"cat {test_large_file} | grep foo"}}))
        res = json.loads(out.strip())
        assert res.get("decision") == "allow", f"Expected allow on piped cat, got {res}"
        print("  -> Hooks tests passed.")
    finally:
        if os.path.exists(test_large_file):
            os.remove(test_large_file)

def test_mcp_server():
    print("[4/6] Testing MCP stdio server protocol & tools...")
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src/model_shunt/server.py")
    proc = subprocess.Popen(
        [sys.executable, server_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # 1. Initialize
    init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    proc.stdin.write(init_req)
    proc.stdin.flush()
    init_resp = json.loads(proc.stdout.readline())
    assert init_resp["result"]["serverInfo"]["name"] == "model-shunt-mcp"

    # 2. List tools
    tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    proc.stdin.write(tools_req)
    proc.stdin.flush()
    tools_resp = json.loads(proc.stdout.readline())
    tools = {t["name"] for t in tools_resp["result"]["tools"]}
    assert "bulk_read" in tools and "code_write" in tools and "get_available_models" in tools, f"Missing tools: {tools}"
    tool_annotations = {t["name"]: t["annotations"] for t in tools_resp["result"]["tools"]}
    expected_hints = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    assert all(set(annotations) == expected_hints for annotations in tool_annotations.values())
    assert all(isinstance(value, bool) for annotations in tool_annotations.values() for value in annotations.values())

    # 3. Call get_available_models tool
    models_req = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "get_available_models",
            "arguments": {"provider": "gemini"}
        }
    }) + "\n"
    proc.stdin.write(models_req)
    proc.stdin.flush()
    models_resp = json.loads(proc.stdout.readline())
    assert not models_resp.get("result", {}).get("isError")
    info = json.loads(models_resp["result"]["content"][0]["text"])
    assert info["provider"] == "gemini"
    assert "best_model_for_reader" in info

    # 4. Call bulk_read on binary file (inside workspace) -> should return error
    bin_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_binary_mcp.bin")
    with open(bin_file, "wb") as f:
        f.write(b"data\x00nullbyte")
    try:
        call_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "bulk_read",
                "arguments": {"question": "What is this?", "file_paths": [bin_file]}
            }
        }) + "\n"
        proc.stdin.write(call_req)
        proc.stdin.flush()
        call_resp = json.loads(proc.stdout.readline())
        assert call_resp["result"].get("isError") is True, f"Expected error for binary file, got {call_resp}"
        assert "binary" in call_resp["result"]["content"][0]["text"]
    finally:
        if os.path.exists(bin_file):
            os.remove(bin_file)

    proc.terminate()
    print("  -> MCP server protocol & tool execution passed.")

def test_path_sandbox():
    print("[6/6] Testing path sandbox restriction...")
    root = os.path.dirname(os.path.abspath(__file__))
    server_path = os.path.join(root, "src/model_shunt/server.py")

    def start_server(extra_env=None):
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        return subprocess.Popen(
            [sys.executable, server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=root, env=env
        )

    def call(proc, msg_id, name, arguments):
        req = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}}) + "\n"
        proc.stdin.write(req)
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    # 1. Path outside the workspace is rejected
    proc = start_server()
    resp = call(proc, 2, "bulk_read", {"question": "x", "file_paths": ["/etc/passwd"]})
    assert resp["result"].get("isError") is True, f"Expected sandbox error, got {resp}"
    assert "outside the allowed roots" in resp["result"]["content"][0]["text"]
    proc.terminate()

    # 2. code_write target outside the workspace is rejected
    proc = start_server()
    resp = call(proc, 3, "code_write", {
        "spec": "x",
        "reference_path": os.path.join(root, "config.example.json"),
        "target_path": "/tmp/evil_output.py"
    })
    assert resp["result"].get("isError") is True, f"Expected sandbox error, got {resp}"
    assert "outside the allowed roots" in resp["result"]["content"][0]["text"]
    proc.terminate()

    # 3. SHUNT_ALLOWED_ROOTS expands the sandbox
    proc = start_server({"SHUNT_ALLOWED_ROOTS": "/etc"})
    resp = call(proc, 4, "bulk_read", {"question": "x", "file_paths": ["/etc/hostname"]})
    # The call must NOT be rejected by the sandbox (it proceeds to the worker,
    # which is not exercised here; a sandbox rejection would be isError with
    # 'outside the allowed roots').
    text = resp["result"]["content"][0]["text"]
    assert not ("outside the allowed roots" in text and resp["result"].get("isError")), \
        f"Path inside SHUNT_ALLOWED_ROOTS was rejected: {resp}"
    proc.terminate()
    print("  -> Path sandbox tests passed.")

def test_cli_list_models():
    print("[5/6] Testing CLI --list-models flag...")
    worker_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src/model_shunt/worker.py")
    res = subprocess.run([sys.executable, worker_path, "--list-models", "--provider", "gemini"], capture_output=True, text=True)
    assert res.returncode == 0, f"Expected returncode 0, got {res.returncode}: {res.stderr}"
    assert "Recommended Reader Model" in res.stdout
    assert "Recommended Writer Model" in res.stdout
    print("  -> CLI --list-models passed.")

def test_mapreduce_chunking():
    print("[7/8] Testing map-reduce payload chunking (deterministic)...")
    # Two small files + question: grouped whole-file chunks
    payload = (
        '<file path="a.py">\n1|x\n2|y\n</file>\n\n'
        '<file path="b.py">\n1|z\n2|w\n</file>\n\n'
        'Question: what?'
    )
    chunks = split_numbered_payload(payload, chunk_chars=1000)
    assert len(chunks) == 1, f"Small corpus should stay in one chunk, got {len(chunks)}"
    assert "Question: what?" in chunks[0], "Question must ride along with the chunk"

    # A single file bigger than the budget must split WITHOUT losing/duplicating N| lines
    big_lines = "\n".join(f"{i}|line {i} padding padding padding" for i in range(1, 501))
    big_payload = f'<file path="big.py">\n{big_lines}\n</file>\n\nQuestion: q?'
    chunks = split_numbered_payload(big_payload, chunk_chars=4000)
    assert len(chunks) > 3, f"Expected multiple chunks, got {len(chunks)}"
    all_numbered = []
    for c in chunks:
        assert c.startswith('<file path="big.py">'), "chunks must re-wrap in the same file block"
        for ln in c.split("\n"):
            m = re.match(r"^(\d+)\|", ln)
            if m:
                all_numbered.append(int(m.group(1)))
    assert all_numbered == list(range(1, 501)), (
        f"Line numbers must be complete, ordered, unique: got {len(all_numbered)} lines")

    # A single gigantic LINE must split by characters with explicit markers
    giant = "z" * 20000
    one_line = f'<file path="min.json">\n1|{giant}\n</file>\n\nQuestion: q?'
    chunks = split_numbered_payload(one_line, chunk_chars=5000)
    assert len(chunks) >= 4, f"Giant single line must split by chars, got {len(chunks)}"
    for c in chunks:
        assert re.search(r"chars \d+-\d+ of line 1", c), "Char slices must carry a position marker"
    print("  -> Map-reduce chunking passed.")

def test_drop_unverified_citations():
    print("[9/9] Testing unverified citation filter...")
    from model_shunt.worker import drop_unverified_citations
    source = "noise = 1\ndef shunt_marker_alpha():\n    return 1\n"
    payload = number_file_lines(f'<file path="probe.py">\n{source}</file>\n\nQuestion: where?')
    kept = drop_unverified_citations("shunt_marker_alpha (N|2)", payload)
    dropped = drop_unverified_citations("shunt_marker_alpha (N|1)", payload)
    mixed = drop_unverified_citations(
        "shunt_marker_alpha (N|2) and shunt_marker_alpha (N|1)", payload
    )
    bare = drop_unverified_citations("see (N|2) for context", payload)
    missing = drop_unverified_citations("shunt_marker_alpha (L99)", payload)
    assert kept == "shunt_marker_alpha (N|2)", kept
    assert dropped == "shunt_marker_alpha", dropped
    assert mixed == "shunt_marker_alpha (N|2) and shunt_marker_alpha", mixed
    assert bare == "see (N|2) for context", bare
    assert missing == "shunt_marker_alpha", missing
    print("  -> Unverified citation filter passed.")

def test_marker_line_contract():
    print("[9/9] Testing planted-marker citation contract (no API)...")
    import benchmark
    source_lines = ["noise = 1", "def shunt_marker_alpha():", "    return 1"]
    source = "\n".join(source_lines) + "\n"
    payload = f'<file path="probe.py">\n{source}</file>\n\nQuestion: where?'
    numbered = number_file_lines(payload)
    body = numbered.split("<file path=\"probe.py\">\n", 1)[1].split("\n</file>", 1)[0]
    for idx, raw in enumerate(source_lines, 1):
        assert body.split("\n")[idx - 1] == f"{idx}|{raw}", body.split("\n")[idx - 1]

    good = "shunt_marker_alpha is defined at (N|2)."
    bad = "shunt_marker_alpha is defined at (N|1)."
    mixed = "- shunt_marker_alpha (N|2)\n- noise (N|1)"
    good_score = benchmark.score_named_citations(good, source, ["shunt_marker_alpha"])
    bad_score = benchmark.score_named_citations(bad, source, ["shunt_marker_alpha"])
    mixed_score = benchmark.score_named_citations(mixed, source, ["shunt_marker_alpha"])
    assert good_score[0]["hits"][0]["symbol_on_line"] is True
    assert good_score[0]["hits"][0]["line"] == 2
    assert bad_score[0]["hits"][0]["symbol_on_line"] is False
    assert bad_score[0]["hits"][0]["line"] == 1
    assert [hit["line"] for hit in mixed_score[0]["hits"]] == [2]
    assert benchmark.extract_cited_lines("shunt_marker_alpha (N|2)", "probe.py") == [2]
    print("  -> Marker line contract passed.")

def test_mapreduce_orchestration():
    print("[8/8] Testing map-reduce orchestration (mocked worker)...")
    lines = "\n".join(f"{i}|code line {i}" for i in range(1, 4001))
    payload = f'<file path="huge.py">\n{lines}\n</file>\n\nQuestion: Where is the parser?'
    os.environ["SHUNT_MAX_DIRECT_TOKENS"] = "10"     # force map-reduce
    os.environ["SHUNT_CHUNK_CHARS"] = str(20 * 1024)  # chunk by chars
    calls = []
    original = worker_mod.run_worker
    def fake_run_worker(mode, content, **kwargs):
        calls.append(content)
        if "Extract every fact" in content or content.startswith("<file"):
            return f"* fact from chunk {len(calls)} (N|123)"
        return "* FINAL SYNTHESIS (N|123)"
    worker_mod.run_worker = fake_run_worker
    try:
        result = run_bulk_reader(payload, override_provider="gemini", override_model="m")
    finally:
        worker_mod.run_worker = original
        os.environ.pop("SHUNT_MAX_DIRECT_TOKENS", None)
        os.environ.pop("SHUNT_CHUNK_CHARS", None)
    map_calls = [c for c in calls if "Extract every fact" in c]
    reduce_calls = [c for c in calls if "FINAL SYNTHESIS" not in c and "Extract every fact" not in c]
    assert len(map_calls) >= 2, f"Expected >=2 map calls, got {len(map_calls)}"
    assert len(reduce_calls) == 1, f"Expected exactly 1 reduce call, got {len(reduce_calls)}"
    assert "chunk 1/2" in map_calls[0] or "chunk 1/" in map_calls[0], "Map prompt must carry chunk numbering"
    assert all("Question: Where is the parser?" in c for c in map_calls), "Every map call must carry the question"
    assert "FINAL SYNTHESIS" in result, f"Reduce output must be returned, got: {result[:80]}"
    assert any("N|123" in c for c in calls), "Citations must survive into reduce"
    print("  -> Map-reduce orchestration passed.")

if __name__ == "__main__":
    test_engine_resolution()
    test_binary_detection()
    test_hooks()
    test_mcp_server()
    test_cli_list_models()
    test_path_sandbox()
    test_mapreduce_chunking()
    test_drop_unverified_citations()
    test_marker_line_contract()
    test_mapreduce_orchestration()
    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!")
