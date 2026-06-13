# Provider Playbook: Local LLM (llama.cpp / vLLM)

> **Endpoint:** `http://localhost:8080/v1` | **Auth:** None (local endpoint)  
> **Concurrent tasks:** 1 (GPU-bound) | **Cost:** Electricity (~$0.50/hr GPU)  
> **Context limit:** 8,192 tokens (critical for 70B models)

## Pipeline Stage → Local LLM Command Map

### Stage 1: Worker Implementation

Local models use the OpenAI-compatible chat API. All commands use `curl` against the local endpoint.

**Pattern: System prompt + code generation**

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-model",
    "messages": [
      {
        "role": "system",
        "content": "You are an expert software engineer. Implement features in <language>.
          Always include: type hints, error handling, docstrings, and tests.
          Self-validate: build, test, fix, retest. Only report success when clean.
          Output: the implementation followed by build and test results."
      },
      {
        "role": "user",
        "content": "Implement <feature>: <detailed specification>"
      }
    ],
    "max_tokens": 4096,
    "temperature": 0.2
  }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

**Key parameters:**
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `max_tokens` | 4096 | Limit output (8K context window — reserve 4K for input) |
| `temperature` | 0.2 | Low temperature for code (precision over creativity) |
| `model` | `local-model` | vLLM default; use `llama-3.1-70b` for specific models |

**Limitations:**
- 8K context window — cannot process large files in one prompt
- No tool use / no autonomous agent loops — output is text only
- Quality degrades at >80% context (6.5K tokens)

---

### Stage 2: Self-Validation Loop

Local models CANNOT run shell commands. Self-validation must be scripted externally:

```bash
#!/bin/bash
# Save LLM output to file
curl -s http://localhost:8080/v1/chat/completions \
  -d '{"model":"local-model","messages":[...],"max_tokens":4096}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])" \
  > /tmp/llm_output.py

# Build check
python3 -m py_compile /tmp/llm_output.py || { echo "BUILD FAILED"; exit 1; }

# Test run (if testable)
timeout 30 python3 /tmp/llm_output.py || { echo "TESTS FAILED"; exit 1; }

# Lint
ruff check /tmp/llm_output.py 2>/dev/null

echo "SELF-VALIDATION: PASS"
```

**Max iterations:** 2 (local models don't benefit from more — limited context for fixing)  
**Artifact:** LLM output file + build/test results

---

### Stage 3: Peer Review (Read-Only)

Local models CAN review code — send the diff/file and ask for structured findings:

```bash
# Pipe the diff to the model
git diff main...feature-branch | curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-model",
    "messages": [
      {
        "role": "system",
        "content": "You are a senior code reviewer. Review this diff for:
          bugs, security vulnerabilities, race conditions, missing error handling,
          and style violations. For each finding: cite the specific file and line.
          Output structured as: SEVERITY | FILE:LINE | ISSUE | SUGGESTED FIX.
          Verdict at end: APPROVED | NEEDS_FIXES | REJECTED."
      },
      {
        "role": "user",
        "content": "Review this diff:"
      }
    ],
    "max_tokens": 4096,
    "temperature": 0.1
  }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

**⚠️ Context limit warning:** Large diffs (>3K tokens) will overflow. Split by file:
```bash
# Review file by file
for f in $(git diff --name-only main...feature-branch); do
  echo "=== REVIEWING: $f ==="
  git diff main...feature-branch -- "$f" | curl -s http://localhost:8080/v1/chat/completions \
    -d '{"model":"local-model","messages":[...same review prompt...],"max_tokens":2048}'
done
```

**Limitations:**
- No multi-file context — each file reviewed in isolation
- 4K output limit — may truncate long review reports
- Research capability limited — no web search

---

### Stage 4: Fix Application

Same as Stage 1 — send the fix instructions with the review findings:

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-model",
    "messages": [
      {
        "role": "system",
        "content": "You are applying fixes from a code review. 
          Address ALL findings. One fix at a time. Do NOT change architecture.
          Output the corrected code."
      },
      {
        "role": "user",
        "content": "Fix these issues in <file>:\n<review findings>\n\nCurrent code:\n<file contents>"
      }
    ],
    "max_tokens": 4096,
    "temperature": 0.2
  }'
```

---

### Stage 5: Orchestrator Review (Fred)

Not a local LLM stage.

---

### Stage 6: Post-Publish Validation

Local models are read-only validators in the post-publish loop — they review but don't fix:

```bash
# Read-only review of published code
git show HEAD | curl -s http://localhost:8080/v1/chat/completitions \
  -d '{"model":"local-model","messages":[{"role":"system","content":"Review this commit for issues."},...],"max_tokens":4096}'
```

A stronger provider (AGY or Claude) applies the fixes.

---

## Server Setup

### llama.cpp (server mode)
```bash
# Install
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp && make -j

# Start server with a GGUF model
./llama-server -m models/llama-3.1-70b-q4_k_m.gguf \
  --host 0.0.0.0 --port 8080 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --threads 16
```

**Config notes:**
- `--ctx-size 8192` matches the context limit in the policy engine
- `--n-gpu-layers 99` offloads all layers to GPU (adjust for your VRAM)
- Server exposes OpenAI-compatible `/v1/chat/completions` endpoint

### vLLM (production serving)
```bash
# Install
pip install vllm

# Start server
python3 -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-70B-Instruct \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --port 8080
```

**Config notes:**
- `--max-model-len 8192` matches the context limit
- `--gpu-memory-utilization 0.90` leaves headroom for KV cache
- Same OpenAI-compatible endpoint as llama-server

### Health Check
```bash
# Verify server is responding
curl -s http://localhost:8080/health
# Expected: {"status":"ok"}

# List available models
curl -s http://localhost:8080/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

---

## Context Window Management (Critical)

### Warning thresholds:
| Usage | Status | Action |
|-------|--------|--------|
| < 65% (< 5.3K tokens) | Normal | Full precision |
| 65-80% (5.3-6.5K) | Warning | Quality degrades — reduce input |
| 80-95% (6.5-7.8K) | Critical | Policy engine warns via `context_80pct_warning` |
| > 95% (> 7.8K) | Hard stop | Policy engine blocks — model silently corrupts output |

### Strategies to manage context:
1. **Split large files:** Review/implement one function at a time
2. **Strip comments before sending:** `cat file.py | grep -v '^#' | grep -v '^$'`
3. **Send diffs, not full files:** `git diff` is more token-efficient
4. **Progressive disclosure:** Send file structure first, then specific sections
5. **External context:** Save reference docs to files, let model request them

---

## Cost Comparison

| Provider | Per Task (Code) | Per Task (Review) | Monthly | Notes |
|----------|----------------|-------------------|---------|-------|
| Local 70B | $0.00 | $0.00 | ~$360 (GPU electricity) | Electricity $0.50/hr × 720h |
| Claude Sonnet | ~$0.015 | ~$0.008 | Variable | Token-based |
| AGY/Gemini | 5 credits | 3 credits | 10,000 credits/mo | Flow credits |

**When local is worth it:** High-volume review work (>50 reviews/day) or constant background validation. For occasional tasks, Claude or AGY is cheaper than the GPU electricity.

---

## Pitfalls

- **Context overflow is silent:** Local models don't error on overflow — they silently truncate and produce corrupted output. The policy engine's 80% warning and 95% hard stop are essential.
- **No autonomous tool use:** Local models cannot run commands, read files, or execute code. All tool use must be scripted externally.
- **Temperature matters:** Local models need lower temperature (0.1-0.2) than cloud models for code. Higher temperatures produce hallucinated APIs and syntax errors.
- **Single-task only:** GPU-bound — one request at a time. Parallel requests queue and timeout.
- **Model quality varies wildly:** LLaMA 70B ≈ GPT-4 level; Mistral 7B ≈ GPT-3.5. Don't use <30B models for implementation work.
- **No vision / no multi-agent:** Local models are text-only, single-model. Asset generation and sub-agent orchestration require cloud providers.
- **GPU memory:** 70B model at Q4 quantization needs ~40GB VRAM. 8K context adds ~2GB. Plan GPU accordingly.
