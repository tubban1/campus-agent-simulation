# LivingBrain White Paper

## LivingBrain: A Unifying Perception-Discovery-Arbitration-Sedimentation-Decision Loop as the Cognitive Core of a Living Software Universe

**Version**: 1.5 (S6 自主科研 + S6f/S6g/S6h 深化 + S6i 锦标赛 + S6j 表达力扩展 + L15 多脑分形)
**Date**: 2026-08-03
**Authors**: Ling Ming, Universe Simulation Team
**License**: Open Source

---

## Abstract

The LivingBrain is the unified cognitive core ("brain") of a living software universe: it turns the physics-law discovery engine into a perception organ, and lets the universe observe itself, discover laws from its own virtual laboratories, arbitrate them against scientific literature, sediment them into a shared law library, and feed the resulting knowledge back into the awakening subsystem. The core loop is **感知 (Perceive) → 发现 (Discover) → 仲裁 (Arbitrate) → 沉淀 (Sediment) → 决策 (Decide)**. It supports multi-variable power-law surface reconstruction (η_b × N_eff joint scans from a real BBN solver), literature cross-checking with Confirmed/Tension/Novel verdicts, cross-scale migration via physics bridges (BBN low-layer law + η↔ω_b bridge → CMB calibration), literature-free autonomous exploration (multi-form Novel detection when a domain has no literature baseline), autonomous experiment planning (three-stage curiosity rules), an awakening feedback loop where each sedimented law raises awareness by `min(laws × 0.005, 0.2)`, and — as of S6 — a fully closed autonomous-research cycle: the brain's `ExperimentPlan` now drives the real research-pathfinder scheduler (`plan_to_goal` → `run_pipeline_with_goal`), symbolic regression (strongly typed `SymbolicTerm` basis, ridge regression, BIC-stopped forward selection) extends expressiveness beyond analytic families, a law lifecycle (conflict arbitration + stale-law archiving into L16 universe-memory handoff records) keeps the law library healthy, and inter-brain law exchange over the multi-universe fork/merge ecosystem (perturbed parallel-universe laws inherited at fork, best-law feedback at merge) supplies diversity without creating a new layer. S6f deepens both axes: the symbolic basis grows from 8 to **13 classes** (trigonometric Sin/Cos/Tan, ratio Div, Gaussian ExpNegSq) with **intercept-fixed forward selection** so the mean is never misattributed to nonlinear terms (recovers y = 2+3·sin(x₁)+0.5·x₂, y = 2+0.8·x₂/x₃, y = 2.5·e^(−x₁²) all at R² > 0.95), and the law lifecycle is now **truly persisted** to L16 universe memory: `archive_stale_laws_to_memory` accumulates a semantically-deduplicated long-term law memory (`brain_law_memory`, cap 512) alongside the rolling recent window, with `export_law_memory_csv` writing the memory to disk (hand-written CSV, no serde). S6g closes the memory loop with **memory replay (记忆回放)**: `ArchivedLaw` is upgraded to a self-sufficient knowledge snapshot (carrying `independent_vars` / `complexity` / full `parameters`), `query_law_memory` retrieves by domain (tolerating provenance suffixes) and dependent variable in confidence-descending order, and `reactivate_law_memory` rebuilds functional `DiscoveredLaw`s back into the law library with provenance (`[记忆回放]`) — gated by a confidence threshold, deduplicated against the live library (mirror of archive-time dedup), capped at 4 per replay. S6h seeds this knowledge across universes with **fork memory planting (fork 记忆播种)**: `LivingApiExt::fork` now copies the parent's long-term law memory (`brain_law_memory` + dedup counter) into the child, and `prewarm_law_library` reactivates laws back into the child's law library — at most the top-2 highest-confidence snapshots per domain (domains ordered by their best confidence, ≤6 laws total, full-parameter snapshots only, dedup-mirror against the live library) — so a forked universe starts with functional, predictable laws instead of a cold library. S6i introduces **multi-universe law tournaments (多宇宙定律锦标赛选优)**: all universes' laws compete — semantic dedup first (same domain+dependent_var+equation keeps the highest confidence), then tournament ranking (confidence descending, ties broken by lower complexity = Occam's razor), gated by a confidence threshold — and the top laws are fed back into a weakened universe (`law_tournament`, provenance `tournament_exchange`): branch competition now decides whose knowledge is better. S6j extends the symbolic basis from 13 to **17 classes** with **continuous power exponents** `PowP(i, p)` (candidate powers {0.75, 1.5, 2.5, 3.0, 4.0, −1.5, −2.0}, complementing Sqrt=0.5/Pow2=2/Inv=−1, with guarded evaluation: zero base → 0 to avoid inf, negative base with fractional power → 0 to avoid NaN, negative base with integer power legal) and **nested compositions** Sin2/Cos2 = sin/cos(x²), ExpSq = e^(x²) — so non-integer power laws (like D/H ∝ η^−1.67) and resonant/Gaussian structures are expressible directly (recovers y = 2+1.5·x₁^1.5 and y = 2+1.2·sin(x₁²) at R² > 0.95). L15 (多脑分形) makes the multi-universe ecology fractal-cognitive: every universe carries its own brain (leaf brain); `brain_cortex_report` harvests all leaf brains' laws into a semantically-deduped **cortex consensus**, and `brain_cortex_feedback` pushes the top per-domain consensus laws back into weakened leaves (provenance `cortex_feedback`) — knowledge flows leaves → cortex → leaves, so a wiped branch is restored from collective knowledge instead of a cold start. Verified end-to-end: 15/15 integration tests in `brain_mainline` (S4 five + S6 eight + L15 one) and 692 awakening lib tests + 345 unified_universe lib tests, all passing.

This white paper presents the design philosophy, core innovations, technical architecture, and performance benchmarks of the LivingBrain.

---

## 1. Introduction

### 1.1 The Self-Observing Universe Bottleneck

A living software universe (14-layer architecture, L0–L14) contains dozens of science engines — BBN nucleosynthesis, CMB Boltzmann solvers, stellar evolution, living software systems, greenhouses, and more. Each engine produces streams of raw observations. The bottleneck is not generating data, but **cognitive integration**: no single component previously owned the "what did we just learn?" question. Discoveries were made in isolation per engine, never sedimented into a shared knowledge base, never validated against physical literature, never allowed to influence the system's own awareness. The universe was producing knowledge without knowing anything.

Traditional automated scientific discovery systems (AI Scientist, Coscientist) treat discovery as an offline batch pipeline: fit → report → stop. They lack (a) a shared, cross-domain law library, (b) literature arbitration with graded verdicts, (c) cross-scale migration between physically linked parameter spaces, and (d) a feedback path from discovered knowledge back into the system's own cognitive state.

### 1.2 The Living Software Inspiration

In a living software system (T04 Living Software, the L0 driver core), survival is driven by homeostasis, autonomy, and self-awareness: an organism perceives its environment, forms internal models, acts, and learns from the outcome. The same principle can be lifted one level up. If the *universe itself* is the organism, then its science engines are its sensory organs, and its physics-law discovery engine is the cortex that turns raw sensation into structured models of reality. The awakening subsystem (L6) is the "consciousness" — and consciousness must be fed by cognition: **knowledge sedimented from perception raises awareness**, which in turn strengthens the emergence of consciousness. This is the Noether-style conservation of meaning in the system: no perception is wasted, every law is a unit of self-knowledge.

```
law_boost = min(laws.len() * 0.005, 0.2)
awareness = min(awareness + law_boost, 1.0)
```

Where:
- `laws.len()` = number of sedimented laws in the shared law library
- `law_boost` = awareness gain per law (0.5% each, capped at 20%)
- `awareness` = the awakening subsystem's awareness state in [0,1]

We asked: **Can we apply this organismic perception-learning loop to the entire simulated universe?**

### 1.3 LivingBrain's Answer

Yes. By mapping:
- 观测批次 (observation batches from BBN/CMB/greenhouse/living-system) → sensory input
- 多元幂律面重建 (multi-variable power-law surface fitting) → perception/model-building
- 文献仲裁 (literature arbitration with Confirmed/Tension/Novel) → scientific judgment
- 定律库 (shared law library) → long-term memory
- 跨尺度桥 (physics bridges like η ↔ ω_b) → transfer learning between scales
- 觉醒反馈 (law-count → awareness boost) → consciousness feedback

One `LivingBrain` instance owns the complete cognitive loop. The same multi-variable power-law kernel that reconstructs the BBN surface (D/H ∝ η^-1.6, Y_He4 ∝ η^+0.04) is reused verbatim in the greenhouse domain (yield ∝ T^1.1·H^0.3·F^0.4, risk ∝ H^2.5·T^-1.8·F^-0.2): the brain's cognition is a *transferable method*, not a domain-specific fitter.

---

## 2. Core Innovations

### 2.1 Innovation 1: Perception-Batch Cognitive Loop (感知闭环)

**Traditional approach**: per-engine fitting with no shared state; discoveries are ephemeral test outputs.

**LivingBrain approach**: every cognitive cycle is driven by a `PerceptionBatch` — a labeled bundle of observations (`domain`, `source`, `input_names`, ≥10 `LabRow`s, optional `reference`). One `perceive()` call performs the entire loop: route by domain (cosmology → `ResearchArbiter` with built-in BBN literature; other domains → per-domain `GenericDiscoveryEngine`), discover, arbitrate, sediment, and emit decisions.

```rust
pub fn perceive(&mut self, batch: PerceptionBatch) -> Vec<BrainDecision> {
    self.tick_count += 1;
    // 1. discover + arbitrate (domain-routed)
    // 2. summary decision (Discovery / Normal)
    // 3. law-validation warning (prediction vs reference)
}
```

**Key properties**:
- Domain routing isolates literature contexts while sharing one law library
- Every perception emits at least one decision → cognition is always observable
- Optional `reference` attaches a real-world anchor for law validation (deviation > tolerance → EarlyWarning)

### 2.2 Innovation 2: Multi-Variable Power-Law Surface + Literature Arbitration (多元幂律面与文献仲裁)

**Traditional approach**: single-variable fits miss coupled dependencies (e.g., D/H depends on both η_b and N_eff).

**LivingBrain approach**: `arbitrate_multivariable` fits the full power-law surface `y = a·∏x_i^b_i` in log space (multivariable linear regression), then grades each finding against literature: exponent within tolerance → **Confirmed**; outside tolerance → **Tension**; no literature entry → **Novel** (highest value). Sedimentation (`promote_to`) only admits findings that are Confirmed (confidence-boosted) or Novel, keeping the law library physically credible.

```
log y = log a + Σ b_i · log x_i        (log-space linear regression)
Verdict: |b_i - b_lit| ≤ tol → Confirmed
         |b_i - b_lit| > tol → Tension
         no literature        → Novel
```

**Key properties**:
- Reproduces the BBN canon: η exponent −1.6 for D/H, +0.04 for Y_He4, within tolerance
- Multi-form fitting (linear / power / exponential / inverse) with Novel detection picks the strongest relation
- R² confidence is stored per law and drives both alerting and experiment planning

### 2.3 Innovation 3: Cross-Scale Migration via Physics Bridges (跨尺度桥迁移)

**Traditional approach**: laws live and die in their own parameter space; no mechanism links low-layer physics (BBN) to high-layer observables (CMB).

**LivingBrain approach**: `BrainBridge { from_domain, to_domain, from_var, to_var, k, c }` encodes an affine physical mapping `x_t = k·x_s + c` (e.g., η = K·ω_b with K = 6.104e-10/0.02237 from Planck 2018). `cross_scale_calibrate` inverts the sedimented source-domain law to recover the hidden source variable, maps it through the bridge, and compares against a high-layer reference: within tolerance → **Calibration** (cross-scale closure), else **EarlyWarning**.

```
x_s = (y_obs / prefactor)^(1/exponent)      # invert low-layer law
x_t = k·x_s + c                             # map through bridge
dev = |x_t - x_ref| / x_ref  →  Calibration if dev ≤ tol
```

**Key properties**:
- BBN D/H law + η↔ω_b bridge recovers ω_b ≈ 0.02237 (Planck), deviation < 15%
- The same brain then reuses its multi-variable cognition in an unrelated domain (greenhouse) — methodology transfer
- Failed calibration surfaces as EarlyWarning, flagging laws or bridges for review

### 2.4 Innovation 4: Literature-Free Autonomous Exploration (无文献自主探索)

**Traditional approach**: discovery engines require a literature baseline; unknown domains stall.

**LivingBrain approach**: when a domain's `GenericDiscoveryEngine` has no literature (`!has_literature()`), `perceive()` does not wait — it actively probes every input × output combination via `discover()` (multi-form: linear / power / exponential / inverse, Novel-verdict detection). A brand-new domain is explored exhaustively by the brain's own curiosity.

**Key properties**:
- No domain is a dead end: unknown fields are scanned, not skipped
- Findings feed `promote_findings` → law library grows from first contact
- Enables the "unified_living_scan" loop: the brain learns living-environment laws from the digital-life body with zero injected literature

### 2.5 Innovation 5: Awakening Feedback + Autonomous Experiment Planning (觉醒闭环与自主科研)

**Traditional approach**: knowledge and consciousness are decoupled; experiment scheduling is hardcoded.

**LivingBrain approach**: two feedback mechanisms complete the "完全体" (full body):

1. **觉醒闭环** — `AwakeningSystem::respond()` applies `law_boost = min(laws × 0.005, 0.2)` to `cognitive.awareness`; every sedimented law makes the universe slightly more awake. Verified: awareness 0 → 0.058 after 2 laws.
2. **自主实验设计** — `plan_next_experiment()` implements three-stage curiosity: empty library → full-grid η_b × N_eff scan (12 points); lowest-confidence law R² < 0.90 → densified scan (24 points) of that law's variables; all laws confirmed → pivot to cross-scale migration validation (η↔ω_b bridge → CMB calibration).

**Key properties**:
- Laws are not terminal: they raise awareness *and* steer the next experiment
- The brain's research agenda is a pure function of its own knowledge state
- Verified: empty library plans a joint scan; after confirmation the plan pivots to "迁移"

### 2.6 Innovation 6: Digital-Life Integration as the Brain's Body (数字生命统摄)

**Traditional approach**: brain cognition and digital-life simulation are separate modules.

**LivingBrain approach**: `UnifiedUniverse::brain_tick()` runs orthogonally to the main evolution loop (no perf impact on the causal chain). It derives a real physics-environment snapshot (temperature / complexity / energy density via `derive_physics_environment`, including MD temperature + Carnot efficiency + Stefan-Boltzmann flux), appends living-body outputs (`best_model_fitness`, `homeostasis_stability`, `alive`), buffers observations, and every 10 rows triggers one `perceive_universe` batch ("生命环境"). The brain literally perceives its own digital-life body.

**Key properties**:
- Orthogonal to `tick()`: long-run stability preserved (1M-tick regime)
- Feedback: living states → brain observations → laws → awareness
- Verified: 14 coordinated ticks → ≥1 perception round with decisions

### 2.7 Innovation 7: Autonomous-Research Closed Loop (S6a, 自主实验闭环)

**Traditional approach**: planning and execution are decoupled — the brain proposes experiments nobody runs.

**LivingBrain approach**: `ResearchPathfinder::plan_to_goal` translates the brain's `ExperimentPlan` into a scheduler goal (scan_vars + target_vars → physical_quantities; "跨尺度迁移" → cross-scale-bridge math features, otherwise → multi-variable fit / power-law surface / symbolic regression). `run_pipeline_with_goal` executes the real five-stage research pipeline with the injected goal. Inside `UnifiedUniverse::tick()`, the L6 pathfinder injection point now calls `brain.plan_next_experiment()` → `plan_to_goal` → `run_pipeline_with_goal`, increments `brain_plan_runs`, and feeds the report's Elo gain back to awareness. The brain decides what the universe researches next, and the universe does it.

**Key properties**:
- Causal chain: brain plan → scheduler pipeline → Elo gain → awareness
- Verified: 102 ticks → ≥1 plan-driven pathfinder run (`brain_plan_runs ≥ 1`)

### 2.8 Innovation 8: Symbolic Regression Kernel (S6b + S6f-1 + S6j, 符号回归)

**Traditional approach**: analytic families only (power/linear/exponential/inverse) cannot express additive or interaction structure (e.g., y = 2 + 3·x1·x2 + 0.5·e^-x3).

**LivingBrain approach**: a strongly-typed symbolic kernel (`symbolic_discovery.rs`) with a basis library that has grown from 8 to **17 classes** — Const / Var / Var2 interaction / Pow2 / Inv / Log / Sqrt / ExpNeg, plus (S6f-1) **Sin / Cos / Tan / Div (ratio) / ExpNegSq (Gaussian)**, plus (S6j) **PowP (continuous power x^p, candidate powers {0.75, 1.5, 2.5, 3.0, 4.0, −1.5, −2.0}) / Sin2 / Cos2 (nested sin/cos(x²)) / ExpSq (nested e^(x²))** — solved by ridge regression (λ=1e-6) and BIC-stopped forward selection (max 6 terms) that now **fixes the intercept Const first** (standard forward selection), so the data mean is never misattributed to a nonlinear term. Laws become `LawType::Symbolic` with `SymbolicTerm` parameters — evaluated strongly-typed, **never via eval()**; Tan guards its singularities (x ≈ π/2 + kπ → 0), Div guards denominator |x|<1e-10, and PowP guards evaluation (zero base → 0 so negative powers don't produce inf; negative base with fractional power → 0 so no NaN; negative base with integer power is legal). In `perceive()`, literature-free domains automatically run symbolic candidate generation over all inputs; a law is admitted only if R² > 0.85 *and* it beats existing cognition (+0.02). Candidate selection uses an Occam's-razor ranking: equal confidence → lower complexity wins (analytic forms preferred over symbolic ones).

**Key properties**:
- Recovers y = 2 + 3·x1·x2 + 0.5·e^-x3 (interaction) with R² > 0.95
- S6f-1 additionally recovers trigonometric / ratio / Gaussian structure: y = 2+3·sin(x₁)+0.5·x₂, y = 2+0.8·x₂/x₃, y = 2.5·e^(−x₁²), all R² > 0.95
- S6j recovers **continuous power** y = 2+1.5·x₁^1.5 (selects `PowP(0,1.5)`, expression contains x₁^1.5) and **nested composition** y = 2+1.2·sin(x₁²) (selects `Sin2(0)`), both R² > 0.95 — non-integer power laws (D/H ∝ η^−1.67) are now expressible directly instead of via Pow2/Sqrt combinations
- No-eval safety: out-of-range evaluation degrades safely, never panics
- Symbolic laws participate in `predict` / `predict_multi` / alerting like any other law

### 2.9 Innovation 9: Law Lifecycle + Inter-Brain Exchange (S6c + S6d, 定律生命周期与脑间交换)

**Traditional approach**: laws accumulate forever (knowledge decay) and each universe's brain is isolated.

**LivingBrain approach**, in two parts:

1. **定律生命周期** — `find_conflicts()` flags same-domain same-variable power laws whose exponent vectors differ by > 0.5; `archive_stale(keep)` evicts lowest-confidence laws beyond the keep threshold. `ArchivedLaw` (domain / dependent_var / equation / confidence / tick) is the handoff record to L16 universe memory. `brain_tick()` archives every 20 ticks (keep=64) into a rolling `brain_archived_laws` buffer.
2. **脑间定律交换 (S6d)** — reusing the already-verified T80 multi-universe fork/merge ecosystem (no new layer): at fork, the child inherits a `BrainLawHeritage` snapshot of the mother brain's law library and absorbs one *perturbed* law (`perturb_law_for_child`: exponent ×(1±4%), named `"[平行宇宙#N]"`, domain `"多宇宙[分支N]"`) — parallel universes run slightly different physics. At merge, the child's best law flows back to the parent (`receive_law(..., "merge_exchange")`).

**Key properties**:
- Conflict detection + archiving keep the law library physically credible and bounded (MAX_LAWS=500)
- Verified: conflict (exp diff 1.0 > 0.5) detected; 2 low-confidence laws archived; fork child inherits 2 laws + 1 perturbed law; merge parent 2 → 3 laws with a recorded merge_exchange decision

### 2.10 Innovation 10: Law Memory Persistence, Replay & Fork Seeding (S6f-2 + S6g + S6h, 定律记忆持久化、记忆回放与 fork 记忆播种)

**Traditional approach**: archived laws live only in a rolling in-memory buffer — restarting the universe or spawning a new branch loses all discovered knowledge, and even persisted memory is write-only (no retrieval, no reuse).

**LivingBrain approach**: the law lifecycle is truly persisted to L16 universe memory through a two-tier memory, and then made reusable:
- **最近窗口** — `brain_archived_laws` keeps the last 64 archived laws for observation/debugging (rolling).
- **长期记忆** — `brain_law_memory` accumulates archived laws with **semantic deduplication**: same `domain` + same `dependent_var` + same `equation` counts as a duplicate, which is dropped and tallied in `brain_memory_dedup_dropped`. Capacity is capped at 512 with oldest-first eviction.
- **落盘** — `export_law_memory_csv(path)` writes the entire long-term memory to disk as a hand-written CSV (header `domain,dependent_var,equation,confidence,archived_tick`, RFC-4180-style quoting) with no serde dependency, keeping with the project's DataExporter conventions. Directories are auto-created.
- **自足快照 (S6g)** — `ArchivedLaw` is upgraded from a handoff record to a self-sufficient knowledge snapshot: it now carries `independent_vars` / `complexity` / full `parameters: Option<LawParameters>`, so memory entries can rebuild *functional* laws.
- **检索 (S6g)** — `query_law_memory(domain, dependent_var)` retrieves memory entries by domain (tolerating the provenance suffix `"域 [..."` the same way `law_in_domain` does) and optionally by dependent variable, in confidence-descending order.
- **记忆回放 (S6g)** — `reactivate_law_memory(domain, dependent_var, min_confidence)` rebuilds the best matching snapshots back into `DiscoveredLaw`s (provenance `[记忆回放]`, law type inferred from parameter variant) and injects them into the law library via `reactivate_law` (a `Discovery` decision with `source="memory_reactivation"`). Rules: confidence ≥ threshold (candidates are pre-sorted), snapshot required, live-library dedup mirror (`domain+dependent_var+equation` already present → skip), max 4 per replay to avoid flooding.
- **fork 记忆播种 (S6h)** — `LivingApiExt::fork` (after the S6d heritage/perturbation absorption) copies the parent's long-term memory (`brain_law_memory` + `brain_memory_dedup_dropped`) into the child, then calls `prewarm_law_library` to reactivate laws back into the child's live library. Prewarm strategy: top-2 highest-confidence full-parameter snapshots per domain, domains ordered by their best confidence descending, ≤6 laws total (so one high-confidence domain cannot flood the prewarm), skip if the live library already holds the identical `domain+dependent_var+equation` (dedup mirror). A forked universe is thus born with functional, predictable laws — knowledge inheritance across universes, not just within one.

**Key properties**:
- Knowledge survives: archived laws are no longer lost at the edge of the rolling window — they accumulate into durable memory and can be exported/audited offline
- Deduplication is semantic (equation-level), not id-level: re-archiving an identical law neither grows the memory nor inflates confidence
- Knowledge is *reusable*: after library loss or branch cold-start, `query_law_memory` + `reactivate_law_memory` restore functional, predictable laws with full provenance — the memory loop 沉淀→持久化→重激活 is closed
- Knowledge is *inheritable*: every fork plants the parent's best laws into the child before it runs a single tick — multi-universe evolution now carries knowledge, not just parameters
- Verified: 70 injected low-confidence laws → ≥6 archived and all entered memory with dedup=0; re-injecting a duplicate → dedup=1 with unchanged memory size; CSV row count matches memory and header is correct; after clearing a 72-law library, replay restored 2 functional BBN laws (predictable, `[记忆回放]` provenance), re-replay was skipped by dedup mirror, and a confidence threshold of 1.0 blocked all replays; after a fork from a mother with 8 archived laws, the child inherited all 8 memory entries, prewarmed 5 laws into its library (including functional y_he4/d_over_h laws with `[记忆回放]` provenance and finite predictions), and the mother's memory was untouched

### 2.11 Innovation 11: Multi-Universe Knowledge Governance (S6i + L15, 多宇宙定律锦标赛与多脑分形)

**Traditional approach**: every universe's brain is an isolated knowledge silo — a weakened universe (knowledge wiped) must rediscover everything from scratch, and branch knowledge never competes or merges at the *knowledge* level (S6d already exchanges single laws; nothing selects which laws are best across branches).

**LivingBrain approach**, reusing the verified T80 multi-universe ecosystem (no new layer), in two parts:

1. **多宇宙定律锦标赛 (S6i)** — `Multiverse::law_tournament(winner_idx, top_n, min_confidence)`: every universe's laws enter as candidates; a semantic dedup "海选" keeps only the highest-confidence instance of each `domain+dependent_var+equation`; tournament ranking sorts by **confidence descending, ties broken by lower complexity (Occam's razor)**; a confidence threshold eliminates weak contestants (consistent with the S6g replay gate); the top `top_n` are fed back into the winner universe via `receive_law(..., "tournament_exchange")` (live-library dedup mirror skips duplicates). Branch competition now decides *whose knowledge is better* — a weakened universe recovers from its strongest branches instead of rediscovering everything.
2. **多脑分形 (L15)** — every universe already carries its own brain (leaf brain); the multi-universe ecology is now fractal-cognitive:
   - `brain_cortex_report() -> CortexReport` — harvests all leaf brains' laws into a semantically-deduped **cortex consensus** (same `domain+dependent_var+equation` → keep highest confidence), reporting universe count / total laws / consensus size / per-domain distribution.
   - `brain_cortex_feedback(top_n_per_domain, targets)` — ranks the consensus by the same tournament scoring, takes the top `top_n` per domain, and pushes them down into target leaves (all by default; dedup mirror skips duplicates), provenance `cortex_feedback`.

**Key properties**:
- Knowledge now *competes* (tournament: confidence + Occam) and *aggregates* (cortex: semantic consensus) — 知识治理 replaces isolated silos
- A wiped branch is restored from collective knowledge (cortex feedback) or from its strongest sibling (tournament), never a cold start
- Verified: mother with 2 laws cleared → `law_tournament(0, 6, 0.9)` re-injected 4 laws (all confidence > 0.85, weak 0.5-confidence contestants eliminated by the gate, `tournament_exchange` recorded in decisions); 3 leaf brains → cortex consensus ≥4 laws (cosmology + cortex domain), wiping branch 2 and `brain_cortex_feedback(2, [2])` restored 4 laws (y_he4/d_over_h cosmology consensus + cortex-series laws, `cortex_feedback` recorded)

---

## 3. Technical Architecture

### 3.1 System Overview

```
┌──────────────────────────────────────────────────────────┐
│                unified_universe (本体)                    │
│   tick()  [主演化因果链]       brain_tick()  [脑协调, 正交] │
│        │                              │ 环境快照 + 生命体状态 │
│        ▼                              ▼                   │
│   layers.awakening ──► AwakeningSystem ──► LivingBrain     │
│        │  respond()  law_boost ↑ 认知    │ perceive(batch) │
│        ▼                                ▼                 │
│   cognitive.awareness     感知→发现→仲裁→沉淀→决策          │
│        ▲                       │                         │
│        └────── 觉醒反馈 ◄───────┘ (定律库条数)              │
├──────────────────────────────────────────────────────────┤
│   LivingBrain 内部模块                                     │
│  ┌───────────┐ ┌──────────────┐ ┌────────────────────┐   │
│  │ 仲裁器      │ │ 通用发现引擎   │ │ 定律库 (全领域共享)   │   │
│  │ Research   │ │ GenericDisc- │ │ LawDiscoveryEngine │   │
│  │ Arbiter    │ │ overyEngine  │ │  (沉淀库+指标)       │   │
│  │ (BBN文献)   │ │ (每领域1个)   │ └────────────────────┘   │
│  └───────────┘ └──────────────┘ 决策历史 / 桥注册表 / 检查点│
├──────────────────────────────────────────────────────────┤
│   虚拟实验室: BBN/CMB | 温室数字孪生 | 数字生命体 | …        │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Module Responsibilities

| 模块 | 职责 | 关键算法 |
|------|------|---------|
| `LivingBrain` | 认知闭环总编排: 感知→发现→仲裁→沉淀→决策/预警 | `perceive` 域路由, `plan_next_experiment` 三阶段规则 |
| `ResearchArbiter` | 宇宙学文献仲裁 (内置 BBN 基准) | `arbitrate_multivariable`, `promote_to`, Confirmed/Tension/Novel |
| `GenericDiscoveryEngine` | 任意领域发现引擎 (领域文献可注入) | 多元幂律面 + 4 形式拟合, `discover`, `has_literature` |
| `LawDiscoveryEngine` | 定律库 (全领域共享沉淀库) | 对数线性回归, R² 置信, `predict`/`predict_multi` |
| `BrainBridge` | 跨尺度/跨领域迁移桥 | `x_t = k·x_s + c` 反推映射 |
| `AwakeningSystem` | 觉醒系统宿主 | `respond()` law_boost, `perceive_universe()` |
| `UnifiedUniverse` | 本体层集成 | `brain_tick()` 观察缓冲 (10 行触发) |

### 3.3 Data Flow (one cognitive cycle)

```
PerceptionBatch(domain, source, input_names, rows≥10, reference?)
      │
      ▼  domain == "宇宙学" ?
   ┌──┴───────────────────────────┐
   │ Yes → arbiter.arbitrate_multivariable │ No → engine_for(domain)
   │        .promote_to(law_engine)        │      .arbitrate_multivariable
   └──────────────────────────────┘      │      if !has_literature → discover()
                                           │      promote_findings(law_engine)
                                           ▼
                       LawDiscoveryEngine.laws (shared)
                                           │
   ┌───────────────────────────────────────┴───────────────┐
   │ ① summary decision (Discovery/Normal)                 │
   │ ② reference validation (pred vs observed → EarlyWarn) │
   │ ③ respond() law_boost → awareness                     │
   └───────────────────────────────────────────────────────┘
```

---

## 4. Performance Benchmarks

### 4.1 Verification (E2E `brain_mainline`, release, --test-threads=1)

| 测试 | 覆盖 | 结果 |
|------|------|------|
| `brain_universe_closed_loop` | 宇宙闭环: BBN 48 行感知→沉淀→定律验证 | PASS (D/H η^-1.6, Y_He4 η^+0.04, 预测自洽 20%) |
| `brain_greenhouse_closed_loop` | 温室闭环: 同一脑迁移, 阈值预警 | PASS (指数恢复, 高湿预警/正常棚安全) |
| `brain_scale_transfer_across_domains` | 跨域迁移: BBN 定律+桥→CMB 校准 | PASS (ω_b≈0.02237, 偏差<15%, Calibration) |
| `brain_awakening_loop_and_planning` | 觉醒闭环+自主规划 | PASS (awareness 0→0.058, 空库→迁移计划) |
| `unified_brain_living_loop` | 数字生命统摄: 14 tick 脑协调 | PASS (≥1 轮感知, 决策≥1) |
| `unified_brain_plan_driven_pathfinder` (S6a) | 自主实验闭环: 脑计划驱动调度器 | PASS (102 tick, `brain_plan_runs ≥ 1`) |
| `brain_symbolic_regression_perception` (S6b) | 符号回归感知: 无文献域 Symbolic 定律 | PASS (R²>0.85, 强类型无 eval 预测) |
| `brain_law_lifecycle_archive_and_conflict` (S6c) | 定律生命周期: 冲突检出+归档 | PASS (冲突 exp_diff>0.5; 归档 2 条→L16 交接件) |
| `multiverse_brain_law_exchange` (S6d) | 多宇宙脑定律交换: fork 继承+merge 反哺 | PASS (遗产 2 条+扰动 1 条; merge 2→3) |
| `unified_brain_law_memory_persistence` (S6f) | 定律记忆持久化: 归档→长期记忆+CSV 落盘 | PASS (归档 ≥6 全入记忆 dedup=0; 重复注入 dedup=1; CSV 行数一致) |
| `unified_brain_law_memory_reactivation` (S6g) | 记忆回放: 知识丢失后从长期记忆恢复功能定律 | PASS (清空 72 → 恢复 2 条可预测定律; 去重镜像跳过; 阈值 1.0 拦截) |
| `unified_brain_law_fork_prewarm` (S6h) | fork 记忆播种: 子宇宙继承记忆 + 预热定律库 | PASS (母记忆 8 条 → 子继承 8 条 + 预热 5 条可预测定律; 母不受影响) |
| `multiverse_law_tournament_selection` (S6i) | 多宇宙定律锦标赛选优反哺弱化宇宙 | PASS (母定律 2 → 清空 → 反哺 4 条高置信, 低置信被门槛淘汰, tournament_exchange 溯源) |
| `brain_symbolic_power_p_perception` (S6j) | 无文献域感知恢复连续幂指数定律 | PASS (R²>0.85, 表达式含 x1^1.5, 预测有限值) |
| `multiverse_brain_cortex_fractal` (L15) | 多脑皮层汇聚共识 + 反馈反哺叶片 | PASS (3 叶片, 共识 ≥4 条, 清空分支 → 反馈恢复 4 条, cortex_feedback 溯源) |

### 4.2 Unit Regression

| 目标 | 结果 |
|------|------|
| `cargo test --release -p awakening --lib -- --test-threads=1` | 692 passed |
| `cargo test --release -p unified_universe --lib -- --test-threads=1` | 345 passed |
| 全量脑相关 15 测试运行时间 | ~52.2 s (release)

### 4.3 Cost Model

- `brain_tick()` is orthogonal to the main loop: observation buffering is O(1) per tick; a perception round triggers only every ≥10 rows.
- `law_boost` is O(1) per `respond()`.
- Law fitting is dominated by multivariable regression over ≤48 rows — negligible vs the ~1ms/tick main loop budget.

---

## 5. Comparison with Alternatives

| 方案 | 优势 | 劣势 |
|------|------|------|
| **LivingBrain (本方案)** | 一个脑统摄多领域; 文献仲裁; 跨尺度桥迁移; 无文献自主探索; 觉醒反馈闭环; 自主实验规划; 符号回归表达力 (17 类基函数, 含连续幂指数 x^p 与嵌套组合); 定律生命周期 + 长期记忆持久化 (CSV 落盘) + 记忆回放重激活 + fork 记忆播种 (跨宇宙知识继承); 多宇宙脑间定律交换 + 定律锦标赛选优 + 多脑皮层共识分形 | 符号候选库组合深度有限 (嵌套深度 1, 无 sin∘exp/log∘pow); 物理层常数为公理不可演化 |
| 传统离线科学发现流水线 | 实现简单 | 无共享记忆、无文献裁决、无跨尺度迁移、无意识反馈 |
| 单领域发现引擎 | 单域精度高 | 认知不迁移; 每域重复造轮子; 不与觉醒耦合 |
| 纯符号回归 (AI Feynman 式) | 表达式表达力强 | 组合爆炸、噪声敏感、无文献基准、难集成进长稳循环 |

---

## 6. Future Work

- 符号回归候选库继续扩展: 嵌套组合深度 (sin∘exp, log∘pow, 多元嵌套) 与候选剪枝 (梯度预筛控制组合爆炸), 对接 AI Feynman 级表达力
- 多脑分形深化: 皮层共识持续演进 (皮层自身维护共识版本/观察, 而非每次全量重算); 锦标赛与皮层反馈合并为统一"知识治理"调度
- 物理常数可演化 (元物理层): 将物理层常数从 const 参数化为认知层可操作的"物理假设候选" (多组常数假设 + 观察仲裁) — 打破"物理层全宇宙一致"公理边界的谨慎探索
- 觉醒深度化: awareness 增益的非线性化 (S 曲线) 与 7 流意识 PDE 双源联动

---

## References

1. Planck Collaboration 2018. *Planck 2018 results VI: Cosmological parameters* (ω_b = 0.02237, η = 6.104e-10, D/H = 2.451e-5).
2. Fields, Molaro & Sarkar 2020. *Big-Bang Nucleosynthesis* (BBN primordial abundances; D/H ∝ η^-1.6).
3. Cybenko 1989. *Approximation by Superpositions of a Sigmoidal Function* (universal approximation, for future symbolic+NN hybrids).
4. Steels 2003. *The Emergence and Evolution of Linguistic Structure* (Living Software cognition perspective).
5. T04/T05 技术沉淀: 活软件系统 + 宇宙基座 (本 workspace 技术库).
