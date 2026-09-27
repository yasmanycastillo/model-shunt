# Model-Shunt 🔀

[![CI Test Suite](https://github.com/yasmanycastillo/model-shunt/actions/workflows/test.yml/badge.svg)](https://github.com/yasmanycastillo/model-shunt/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#-key-highlights)
[![Website Live](https://img.shields.io/badge/website-live-cyan.svg)](https://yasmanycastillo.github.io/model-shunt/)
[![M8ven Verified](https://m8ven.ai/badge/mcp/yasmanycastillo-model-shunt-b7r4qh?variant=verified)](https://m8ven.ai/mcp/yasmanycastillo/model-shunt)

A decoupled, zero-dependency, universal implementation of the **Shunt** model-routing pattern (originally conceived by Spotify Engineering).

**Model-Shunt** allows AI coding agents (**Antigravity, Cursor, Windsurf, Claude Code, Aider, OpenHands**, etc.) to delegate token-heavy I/O (bulk file reading/code analysis) and repetitive boilerplate generation (tests, mocks, stubs, configs) to **fast, economical, or local worker models** (Gemini 2.5 Flash, Groq/Llama, Ollama, DeepSeek, GPT-4o-mini). This cuts primary agent token consumption by up to **90%** while keeping the main context window clean.

---

## ⚡ Key Highlights

* **Zero External Dependencies:** Built with pure Python 3 standard library (`urllib`, `json`, `re`, `argparse`). No `pip install`, no virtual environment, and no `npm` required.
* **Agent-Agnostic:** Works transparently across any AI coding agent via standard **MCP (Model Context Protocol)**, standalone **CLI scripts**, or **PreToolUse lifecycle hooks**.
* **Dynamic Model Discovery & Auto-Routing:** Queries the worker endpoint in real time to discover available models and automatically routes to the best model for the task:
  * **Reader Mode (Bulk I/O):** Prioritizes massive context windows and ultra-low cost (e.g., `gemini-2.5-flash`, `llama-3.3-70b-versatile`, `gpt-4o-mini`).
  * **Writer Mode (Code Generation):** Prioritizes specialized coding models (e.g., `qwen2.5-coder:latest`, `gemini-2.5-flash`, `deepseek-chat`).
* **Bypasses Linux `ARG_MAX` Limits:** Unlike naive implementations that pass file contents as CLI arguments (capped at ~128 KB on Linux), Model-Shunt streams corpus data over `stdin`, allowing analysis of hundreds of thousands of lines without buffer overflows.
* **Deterministic Line Numbering (`N|`):** Automatically prefixes every line in file blocks with its 1-based index, forcing worker models to cite verifiable, exact line numbers instead of hallucinating locations.
* **Binary File Protection:** Inspects byte headers to reject binary files (PDFs, images, compiled objects) before sending them to the LLM.
* **Network Resilience:** Automatic exponential backoff retries for rate limits (HTTP 429) and transient server errors (HTTP 503/502), with configurable timeouts and token limits.
* **Map-Reduce for Oversized Corpora:** When a `bulk_read` payload exceeds the direct limit (`SHUNT_MAX_DIRECT_TOKENS`, default ~200k tokens), Model-Shunt automatically splits the corpus into chunks, maps the question over each chunk (preserving absolute `N|` line numbers), and reduces the extracts into one cited answer. Giant single-line files (minified JSON/JS) are sliced by characters with explicit position markers. Rate-limit pacing waits out provider quota windows instead of failing.

---

## 📁 Repository Structure

```
model-shunt/
├── src/model_shunt/
│   ├── worker.py              # Universal LLM worker engine with model discovery (zero-deps)
│   └── server.py              # Stdio MCP server exposing routing tools
├── bin/model-shunt.js         # npm/npx launcher shim (requires local Python 3)
├── plugin/
│   ├── .claude-plugin/        # Plugin manifest for hook-compatible agents
│   ├── hooks/                 # PreToolUse interceptor hooks (check-file-size, check-bash-read)
│   ├── scripts/               # Executable streaming CLIs (bulk-read, code-write)
│   └── skills/                # Agent skill manifests (/bulk-reader, /code-writer)
├── pyproject.toml             # PyPI packaging (uvx / pip install)
├── package.json               # npm packaging (npx)
├── config.example.json        # Configuration template
├── test_shunt.py              # Automated test suite
└── .gitignore                 # Credential and cache protection
```

---

## ⚙️ Configuration

Configure your worker model via environment variables or a `config.json` file (placed in `~/.config/model-shunt/config.json` or in the project root):

### Using `config.json`

```json
{
  "provider": "gemini",
  "model": "auto",
  "timeout": 90,
  "max_tokens": 8192
}
```

> **Tip:** Setting `"model": "auto"` (or passing `--auto-model` in the CLI) will automatically inspect the provider's active models and pick the optimal one for reading vs writing.

> **Security:** Do **not** put your API key in `config.json` — use environment variables instead (e.g. `GEMINI_API_KEY`, `GROQ_API_KEY`, or `SHUNT_API_KEY`). An `api_key` field exists as a last-resort fallback, but keeping secrets out of files is strongly recommended.

### Using Environment Variables

```bash
# Google Gemini (Recommended: 1M token context, high speed, ultra-low cost)
export SHUNT_PROVIDER="gemini"
export GEMINI_API_KEY="your-api-key"

# Groq (Ultra-low latency inference)
export SHUNT_PROVIDER="groq"
export GROQ_API_KEY="your-api-key"

# Ollama (100% private, local, and free)
export SHUNT_PROVIDER="ollama"
export SHUNT_BASE_URL="http://localhost:11434/v1"

# OpenAI / DeepSeek / OpenRouter / Anthropic
export SHUNT_PROVIDER="deepseek"
export DEEPSEEK_API_KEY="your-api-key"
```

---

## 🛠️ Usage Modes

### Mode 1: Universal MCP Server (Recommended)

Model-Shunt provides a standard stdio MCP server exposing three tools:

1. **`get_available_models(provider?)`**: Discovers live models from the provider endpoint and returns recommended models for reading and code writing.
2. **`bulk_read(question, file_paths, model?, provider?)`**: Reads large or multiple files and outputs concise, structured bullets with exact line citations. A citation `name (N|k)` is dropped when source line `k` does not contain that name.
3. **`code_write(spec, reference_path, target_path?, model?, provider?)`**: Replicates patterns, styling, and conventions from a reference file and writes generated code directly to disk without consuming frontier agent output tokens.

#### Installation

MCP Registry name: `mcp-name: io.github.yasmanycastillo/model-shunt`

**Universal one-liner** (detects uv / pip / pipx / npm, installs the `model-shunt` command, and registers it with Claude Code if present):
```bash
curl -fsSL https://yasmanycastillo.github.io/model-shunt/install.sh | bash
```

**Manual alternatives:**
```bash
claude mcp add model-shunt -- uvx model-shunt      # if you have uv
claude mcp add model-shunt -- npx -y model-shunt   # if you have Node + Python
```

**Any MCP client** (Cursor, Windsurf, Antigravity, Claude Desktop, etc.) — add to its MCP settings. No clone, no absolute paths:

```json
{
  "mcpServers": {
    "model-shunt": {
      "command": "uvx",
      "args": ["model-shunt"],
      "env": {
        "SHUNT_PROVIDER": "gemini",
        "SHUNT_MODEL": "auto",
        "GEMINI_API_KEY": "your-api-key"
      }
    }
  }
}
```

> **Fallback (offline / no uv / no npx):** run straight from a clone with Python 3.9+ — replace `"command"`/`"args"` with `"command": "python3", "args": ["/absolute/path/to/model-shunt/src/model_shunt/server.py"]`.

> **Security:** by default `bulk_read`/`code_write` only operate on files inside the server's working directory (the agent workspace). Set `SHUNT_ALLOWED_ROOTS` (PATH-style list) to expand the sandbox.

### Map-Reduce Tuning (optional)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SHUNT_MAX_DIRECT_TOKENS` | `200000` | Payloads above this estimated size switch to map-reduce |
| `SHUNT_CHUNK_CHARS` | `600000` | Chunk size in characters (~150k tokens) |
| `SHUNT_CHUNK_RETRIES` | `3` | Retries per chunk on rate limits |
| `SHUNT_CHUNK_RETRY_DELAY` | `60` | Seconds to wait out a provider quota window (free-tier TPM) |

---

### Mode 2: PreToolUse Interceptor Hooks

For agents supporting pre-execution hooks (e.g., Claude Code, custom agent loops):

1. **File Read Interceptor (`check-file-size`):**
   * If the agent attempts a whole-file read on a file exceeding the threshold (default: 350 lines, configurable via `SHUNT_MIN_LINES`), the hook **blocks** the call and instructs the agent to delegate to `bulk-read`.
   * Targeted reads with `offset` and `limit` are **allowed**, preserving surgical context for code editing.
2. **Terminal Guard (`check-bash-read`):**
   * Prevents agents from bypassing the read hook by executing commands like `cat`, `less`, or `more` on large files directly in the terminal context.

---

### Mode 3: Standalone CLI & Scripts

You can also use Model-Shunt directly from the command line or from agent bash sessions:

#### Discover Available Models & Recommendations
```bash
python3 src/model_shunt/worker.py --list-models --provider gemini
```

#### Run Bulk Reading Analysis
```bash
./plugin/scripts/bulk-read \
  --question "How does the token refresh cycle work?" \
  --paths src/auth.py src/tokens.py \
  --auto-model
```

#### Generate Boilerplate Directly to Disk
```bash
./plugin/scripts/code-write \
  --spec "Create unit tests for the BillingService covering charge and refund" \
  --reference tests/test_user.py \
  --target tests/test_billing.py \
  --auto-model
```

---

## 🧪 Verification

Run the built-in test suite to verify your environment:

```bash
python3 test_shunt.py
```

The test suite validates:
- Configuration resolution, fallback cascades, and model selection.
- Binary file detection and rejection.
- Hook decisions (surgical reads allowed, large file reads blocked, bash flag parsing).
- MCP stdio protocol compliance and tool execution.
- CLI discovery flags.

---

## 📄 License

MIT. Inspired by Spotify Engineering's Shunt architecture.

## 🔒 Privacy

See [PRIVACY.md](PRIVACY.md) for filesystem scope, provider data handling, and credential practices.
