# Prismatic Engine — Local AI Integration Roadmap
**Linear Issue:** [GRO-822](https://linear.app/growthwebdev/issue/GRO-822)  
**Author:** Antigravity Senior Systems Architect  
**Date:** June 8, 2026  
**Status:** Under Review

---

## 1. Overview & Timeline

The integration of local AI agents into the Prismatic Engine follows a structured, progressive rollout designed to balance immediate developer utility with long-term autonomous scalability:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           INTEGRATION ROADMAP                           │
│                                                                         │
│  Phase 1: MVP               Phase 2: Production       Phase 3: Advanced │
│  (Month 1)                  (Months 2-3)              (Months 4-6)      │
│  ┌──────────────────┐       ┌──────────────────┐      ┌───────────────┐ │
│  │ * Ollama API     │──────▶│ * Multi-Agent    │─────▶│ * Dual Server │ │
│  │ * Proofreader    │       │ * Hybrid Routing │      │   Sharding    │ │
│  │ * Init Config    │       │ * Cost Dashboard │      │ * Auto-scaling│ │
│  │ * Integr. Tests  │       │ * All 5 Workflows│      │ * Fallbacks   │ │
│  └──────────────────┘       └──────────────────┘      └───────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: MVP (Month 1 — This Month)

**Objective:** Establish the core infrastructure to run local agents and deploy a single end-to-end local coding/review workflow.

### 2.1 Technical Goals
- [ ] **Ollama Provider Integration:** Implement the `OllamaProvider` class to manage model context loading and prompt wrapping.
- [ ] **Content Proofreader Workflow:** Deploy the `local-proofreader` agent, triggered by file nudges, editing and verifying documentation offline.
- [ ] **Local CLI Wizard Addition:** Update the `prismatic-engine init --standalone` command to prompt the user for their local Ollama/vLLM endpoint address.
- [ ] **Integration Tests:** Write a basic suite to verify that:
  1. A file nudge is successfully created.
  2. The local agent is invoked.
  3. The local model completes the edit.
  4. The result is returned correctly.

### 2.2 Setup Guide Preview: "How to set up a local agent in 10 minutes"
```bash
# 1. Start Ollama and download the model
ollama run qwen2.5:7b-instruct-q5_K_M

# 2. Run Prismatic Engine initialization in standalone mode
prismatic-engine init --standalone

# 3. Enable local agent configurations in config/local_agents.yaml:
# (Set endpoint to http://localhost:11434)

# 4. Trigger proofreader workflow on a markdown file:
echo "This is a simple test file with spelling misteaks." > test_doc.md
prismatic-engine run --agent "local-proofreader" --file "test_doc.md"

# 5. Check test_doc.md for updated corrections
cat test_doc.md
```

---

## 3. Phase 2: Production (Months 2 – 3)

**Objective:** Expand to multi-agent local concurrency, introduce cost routing, and implement all 5 practical workflows.

### 3.1 Technical Goals
- [ ] **Multi-Instance Concurrency:** Allow the scheduler to launch multiple subprocesses/Docker containers of local agents concurrently, utilizing lock management (SQLite WAL mode) to prevent file conflicts.
- [ ] **Hybrid Task Router:** Develop a classifier module using a small local model (e.g. Qwen 3B) that routes incoming issues to local agents if the complexity is low, or routes to cloud APIs if it requires high-level system reasoning.
- [ ] **Monitoring & Cost Dashboard:** Integrate a simple local dashboard displaying the cumulative VRAM usage, token count, and estimated cloud cost savings (e.g., "$142 saved today by running Qwen Coder locally").
- [ ] **Full Workflow Suite Rollout:** Fully implement:
  - **Structured Log Extractor**
  - **Local RAG Document Assistant**
  - **Code Auditor & Reviewer**
  - **Bulk Issue Classifier**

---

## 4. Phase 3: Advanced (Months 4 – 6)

**Objective:** Scale the local architecture across multi-GPU and multi-server nodes to run frontier-class local models.

### 4.1 Technical Goals
- [ ] **Multi-Server Model Sharding (Michael's Setup):** Implement a tensor/pipeline-parallel setup configuration guide utilizing vLLM across multiple servers (2× 96GB GPUs) to run Llama 405B or DeepSeek-R1.
- [ ] **Auto-Scaling Workers:** Build queue-depth tracking in the scheduler. If the task queue exceeds 5 tasks, spin up additional local agent instances; when idle, spin them down to conserve GPU power and VRAM.
- [ ] **Fallback Logic:** If a local agent execution fails (e.g., tool-calling syntax error or model loop timeout), automatically forward the payload to a cloud model (e.g. Claude 3.5 Sonnet) as a fallback.
- [ ] **Local Model Fine-Tuning Pipeline:** Formulate a script to collect past successful task runs from the local database, clean them, and feed them into a LoRA fine-tuning training loop (using Unsloth or Axolotl) to specialize the local model for the team's specific coding style.

---

## 5. Phase 4: Vision (Months 7 – 12)

**Objective:** Realize fully autonomous, zero-marginal-cost developer teams operating on local hardware clusters.

### 5.1 Technical Goals
- [ ] **Autonomous Swarm Loops:** Establish continuous background execution loops. Agents run tests, identify bottlenecks, generate patches, run tests in sandboxes, and push clean commits to local branches overnight without developer intervention.
- [ ] **Privacy-First Document Processing Pipeline:** Build an air-gapped processing workflow that indexes security-sensitive documents, audits contracts, and generates technical specs without any internet connectivity.
- [ ] **Community Agent Marketplace:** Enable exporting local agent setups (YAML + prompts + quantized GGUF links) as shareable configuration packages.
- [ ] **Benchmark Suite:** Establish an automated evaluation matrix running daily tests comparing local models (Qwen, Llama, DeepSeek) against cloud APIs (OpenAI, Anthropic, Gemini) across the codebase's test suite to track local model regression and progression.
