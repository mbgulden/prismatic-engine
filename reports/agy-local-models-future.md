# Local AI Model Capability Survey — Future (Dec 2026 / June 2027)
**Linear Issue:** [GRO-822](https://linear.app/growthwebdev/issue/GRO-822)  
**Author:** Antigravity Senior Systems Architect  
**Date:** June 8, 2026  
**Status:** Under Review

---

## 1. Technological Drivers of Local AI Evolution

Over the next 6 to 12 months (Dec 2026 – June 2027), the performance of local models will be driven by four key technical paradigms rather than simply scaling raw parameter counts:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LOCAL AI ENGINE EVOLUTION                       │
│                                                                        │
│  ┌──────────────────────┐                     ┌─────────────────────┐  │
│  │ Low-Bit Quantization │                     │ Speculative Draft   │  │
│  │ (1.5 - 2.5 Bit IQ)   │                     │ (Medusa / Lookahead)│  │
│  └──────────┬───────────┘                     └──────────┬──────────┘  │
│             │                                            │             │
│             ▼                                            ▼             │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                  HIGH-THROUGHPUT LOCAL INFERENCE                 │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
│                                     │                                  │
│                                     ▼                                  │
│  ┌──────────────────────┐                     ┌─────────────────────┐  │
│  │ Sparse MoE Tensors   │                     │ Linear Attention SS │  │
│  │ (Dynamic Offloading)  │                     │ (Mamba / Constant KV)│  │
│  └──────────────────────┘                     └─────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Quantization Breakthroughs (IQ & Low-Bit Formats)
The traditional loss of coherence at low bitrates is being resolved by **Activation-Aware Quantization (AWQ)** and **Importance Matrix (I-Matrix) GGUF** techniques. By June 2027, 2-bit quantization (`IQ2_XXS` and similar) will achieve the perplexity score of today’s 4-bit quantizations, effectively doubling the parameter capacity of existing VRAM configurations.

### 1.2 Mixture of Experts (MoE) Optimization
Next-generation local MoE engines will use **Dynamic Expert Offloading** and **Quantized MoE Tensors**. Instead of keeping all experts in GPU VRAM, engines will cache active experts and load inactive ones on-demand via PCIe 4.0/5.0, or run deep-quantized experts. This allows a 100B-class MoE (with 14B active parameters) to fit inside a single 16GB-24GB VRAM envelope while achieving token rates of 20+ tokens/sec.

### 1.3 Speculative Decoding & Draft Models
Inference engines will natively integrate multi-candidate speculative decoding (e.g., **Medusa heads** or **Lookahead decoding**). By utilizing a lightweight draft model (like a Qwen 1.5B) to predict sequences that are verified in a single forward pass by a 70B model, consumer GPUs will achieve 2× to 3× speedups, making local execution feel instantaneous.

### 1.4 State-Space Models (Mamba / RWKV) & Linear Attention
The transition away from standard self-attention to hybrid architectures (e.g., Mamba-Transformer or Griffin-like models) will eliminate the **KV Cache VRAM bottleneck**. These architectures feature constant-size state representations, enabling million-token contexts to run on 8GB-12GB VRAM cards with zero memory expansion over time.

---

## 2. 6-Month Projection (December 2026)

### Tier 1 (8 GB VRAM)
* **Expected Models:** Llama 3.5 8B (Q5), Qwen 3.0 Coder 7B (FP16-parity quantized), Gemma 3 9B (Q4).
* **Capabilities Improved:** 32k context window will become the default standard with minimal speed penalty. Basic function calling (single-tool use) will reach 95%+ accuracy.
* **Remaining Frontier-Only:** Multi-step autonomous planning and complex source-code analysis.
* **Gap Analysis:** Narrowing. Small models will handle standard formatting and basic coding chores easily.

### Tier 2 (12 GB VRAM)
* **Expected Models:** Llama 3.5 14B (Q5), Qwen 3.0 14B (Q6), Mistral 3 12B (Q8).
* **Capabilities Improved:** Native tool-calling schemas will be fully supported. Multi-turn reasoning for code generation will stabilize.
* **Remaining Frontier-Only:** Massive document comparisons (100k+ tokens) and complex mathematical logic.
* **Gap Analysis:** Narrowing. Tier 2 becomes the standard baseline for local developer agents.

### Tier 3 (16 GB VRAM)
* **Expected Models:** Qwen 3.0 32B (IQ3), Mistral Small 3 24B (Q5), Gemma 3 27B (IQ4).
* **Capabilities Improved:** 64k context support with Q4 KV Cache quantization. Reliable execution of simple agent loops.
* **Remaining Frontier-Only:** Deep multi-turn reasoning and agent planning.
* **Gap Analysis:** Closing. Tier 3 local agents will comfortably replace GPT-4 (early versions) for 80% of daily programming workflows.

### Tier 4 (24 GB VRAM)
* **Expected Models:** Qwen 3.0 Coder 32B (Q8), Llama 3.5 70B (IQ3_S), DeepSeek-V3-Mini (MoE with 14B active parameters, Q4).
* **Capabilities Improved:** 128k context support. Complex code refactoring and local codebase editing.
* **Remaining Frontier-Only:** Zero-shot system design of large-scale distributed applications.
* **Gap Analysis:** Closing fast. Developers will run models that rival standard GPT-4o deployments at zero cost.

### Tier 5 (32 GB VRAM)
* **Expected Models:** Llama 3.5 70B (Q4), Mistral Large 3 (IQ3_S), Command R++ v2 (IQ3).
* **Capabilities Improved:** High-speed agentic reasoning loops (30+ tokens/sec via speculative decoding). High-reliability multi-turn tool calling.
* **Remaining Frontier-Only:** Advanced mathematical proofs and multi-modal video/audio processing.
* **Gap Analysis:** Narrowing. Excellent workstation performance.

### Tier 6 (48 GB VRAM)
* **Expected Models:** Llama 3.5 70B (Q8 / FP16), Qwen 3.0 72B (Q8 / FP16), Command R++ v2 (Q4).
* **Capabilities Improved:** Multi-agent task coordination on a single machine. Deep semantic search and code refactoring.
* **Remaining Frontier-Only:** Elite-tier planning and massive parallel multi-agent loops.
* **Gap Analysis:** Very narrow. Local systems match the baseline utility of cloud APIs for all text/code workflows.

### Tier 7 (192 GB VRAM)
* **Expected Models:** DeepSeek-R2 (FP8), Llama 3.5 405B (Q4_K_M).
* **Capabilities Improved:** Native chain-of-thought planning models running at high speeds (20-30 tokens/sec). Local deployment of specialized agent clusters.
* **Remaining Frontier-Only:** Super-long-context multi-modal search (1M+ video/audio processing).
* **Gap Analysis:** Eliminated. At this scale, the local system matches or exceeds cloud capabilities for core developer/reasoning loops.

---

## 3. 12-Month Projection (June 2027)

```
VRAM Capability Progress (2026 vs 2027):
 8 GB: [Basic Text Generation]   ---> [32K Context + 95% Simple Tool Use]
24 GB: [Code Completion / RAG]    ---> [128K Context + Elite Coder (Parity 3.5 Sonnet)]
96GB+: [General 70B Inference]    ---> [DeepSeek-R1 Class Reasoning & Autonomous Agent Clusters]
```

### Tiers 1 & 2 (8 GB - 12 GB)
* **Expected Models:** Qwen 3.5 Coder 7B/14B, Llama 4.0 8B, Gemma 4 9B.
* **Technical State:** Hybrid Transformer-Mamba architectures will dominate these tiers. KV Cache memory overhead is reduced to zero. 100k context windows are standard.
* **Capability:** 8B models will perform multi-step programming tasks and execute tool-calling pipelines with 98% accuracy.

### Tiers 3 & 4 (16 GB - 24 GB)
* **Expected Models:** Qwen 3.5 Coder 32B (FP16), Llama 4.0 70B (IQ2_XXS/IQ3_S), Mistral Medium 4 40B.
* **Technical State:** Speculative decoding is native to the silicon/drivers (RTX 50-series and Apple M4/M5). Sparse MoEs running at 30+ tokens/sec.
* **Capability:** A 24GB system becomes an autonomous "junior engineer" worker. It can run a continuous background loop to find bugs, write unit tests, and refactor code.

### Tiers 5 & 6 (32 GB - 48 GB)
* **Expected Models:** Llama 4.0 70B (Q4/Q8), Qwen 3.5 72B (Q8).
* **Technical State:** Zero-loss low-bit quantization. Multi-GPU Tensor Parallelism is fully standardized in consumer software (Ollama/vLLM).
* **Capability:** Local workflows support complete, self-contained multi-agent squads (e.g., Product Manager agent, Developer agent, QA agent, and DevOps agent) running on a single workstation.

### Tier 7 (192 GB VRAM)
* **Expected Models:** Llama 4.0 405B (Q4/Q8), DeepSeek-R2 (FP8 native / sharded).
* **Technical State:** Terabyte-bandwidth memory interconnects on consumer server nodes.
* **Capability:** True autonomous R&D laboratories. Systems can run deep reasoning models over entire codebases for days, exploring optimal system architectures, generating documentation, and writing complete features.

---

## 4. Closing the Gap: Local vs. Frontier Cloud

The gap between local and frontier cloud models is **closing**, driven by a shifting bottleneck:

1. **The Cost of Inference:** Cloud providers must charge markups for API requests to sustain margins. Local inference is capital-expenditure only; once hardware is purchased, marginal cost is zero. This changes agent economics—allowing agents to run long, iterative thought loops (e.g., generating 100 candidates, testing them in a sandbox, and choosing the best one) which would cost thousands of dollars on cloud APIs.
2. **Data Privacy:** Codebases and internal company knowledge bases cannot be sent to external APIs in highly regulated sectors. Local agent integration is the *only* viable path for these enterprises.
3. **Latency:** Local models bypass internet routing. Small model generation rates (50-100 tokens/sec) exceed cloud API speeds, providing faster autocomplete and interactive code edits.
4. **Cloud's Remaining Advantage:** Cloud APIs will maintain advantages in **scale-dependent intelligence** (e.g., frontier reasoning models that require thousands of GPUs for a single query), real-time multi-modal video/audio processing, and global state sync.
