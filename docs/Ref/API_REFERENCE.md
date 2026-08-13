# LivingBrain API Reference

**版本**: v1.1.0 (S6 自主科研)
**日期**: 2026-08-03
**所在 crate**: `awakening` (brain / research_arbiter / generic_discovery / law_discovery / symbolic_discovery / research_pathfinder) + `unified_universe` (brain_tick 集成 + living_api S6d)
**阅读对象**: 希望将宇宙/温室/生命体/任意业务领域观测接入统摄脑的开发人员

---

## 目录

1. [快速上手](#1-快速上手)
2. [核心类型](#2-核心类型)
3. [LivingBrain 脑 API](#3-livingbrain-脑-api)
4. [感知数据: PerceptionBatch / LabRow](#4-感知数据-perceptionbatch--labrow)
5. [文献基准: LiteratureTarget / Verdict / ResearchFinding](#5-文献基准-literaturetarget--verdict--researchfinding)
6. [仲裁器: ResearchArbiter](#6-仲裁器-researcharbiter)
7. [通用发现引擎: GenericDiscoveryEngine / DataSource](#7-通用发现引擎-genericdiscoveryengine--datasource)
8. [定律库: LawDiscoveryEngine / DiscoveredLaw](#8-定律库-lawdiscoveryengine--discoveredlaw)
9. [觉醒系统集成: AwakeningSystem](#9-觉醒系统集成- awakeningsystem)
10. [本体层集成: UnifiedUniverse::brain_tick / S6 多宇宙脑定律交换](#10-本体层集成-unifieduniversebrain_tick--s6-多宇宙脑定律交换)
11. [S6 符号回归: SymbolicFit / SymbolicTerm / fit_symbolic](#11-s6-符号回归-symbolicfit--symbolicterm--fit_symbolic)
12. [S6 自主科研: plan_to_goal / run_pipeline_with_goal](#12-s6-自主科研-plan_to_goal--run_pipeline_with_goal)
13. [错误处理与边界条件](#13-错误处理与边界条件)

---

## 1. 快速上手

### 1.1 最小闭环 (宇宙学领域, 一行感知)

```rust
use awakening::{LivingBrain, PerceptionBatch, DOMAIN_COSMOLOGY, LabRow};

let mut brain = LivingBrain::new();
let batch = PerceptionBatch::new(
    DOMAIN_COSMOLOGY,                    // 领域: 宇宙学 (路由到内置 BBN 文献仲裁器)
    "bbn_eta_neff_scan",                 // 数据来源 (溯源)
    vec!["eta_b".to_string(), "n_eff".to_string()],  // 自变量列名
    rows,                                // Vec<LabRow>, ≥ 10 行
)
.with_reference("d_over_h", 2.451e-5, 0.20); // 附加参考观测 (定律验证)

let decisions = brain.perceive(batch);   // 一次完整认知循环
// decisions: Discovery 沉淀决策 + 定律验证决策
```

### 1.2 任意业务领域 (温室示例)

```rust
use awakening::{LiteratureTarget, PerceptionBatch, LabRow};

let mut brain = LivingBrain::new();
brain.add_literature("温室", LiteratureTarget::new("T", "yield", 1.1, 0.2, "农艺文献: 产量 ∝ 温度^1.1"));

let batch = PerceptionBatch::new("温室", "greenhouse_grid_48",
    vec!["T".to_string(), "H".to_string(), "F".to_string()], rows);
brain.perceive(batch);

// 阈值预警 (用定律预测给定工况)
let alert = brain.predict_and_alert("risk", &[25.0, 0.95, 3.0], 1.5e-5, "棚A(高湿)");
```

### 1.3 跨尺度迁移 (BBN → CMB)

```rust
use awakening::{BrainBridge, DOMAIN_COSMOLOGY};

let bridge = BrainBridge {
    from_domain: DOMAIN_COSMOLOGY.to_string(),
    to_domain: "CMB".to_string(),
    from_var: "d_over_h".to_string(),
    to_var: "omega_b".to_string(),
    k: 0.02237 / 6.104e-10,   // η = K·ω_b ⇒ 反方向 k = ω_b/η = 1/K
    c: 0.0,
};
let cal = brain.cross_scale_calibrate(&bridge, 2.451e-5, (0.02237, 0.15));
// cal.kind == DecisionType::Calibration (自洽) 或 EarlyWarning (超容差)
```

### 1.4 觉醒系统宿主 + 自主规划

```rust
use awakening::{AwakeningSystem, PerceptionBatch, DOMAIN_COSMOLOGY, LabRow};
use universe_core::types::CosmicPhase;

let mut sys = AwakeningSystem::new(42);
let plan0 = sys.brain.plan_next_experiment();     // 空库 → 全网格联合扫描
sys.perceive_universe(batch);                     // 脑感知宇宙批次
sys.respond(CosmicPhase::Nucleosynthesis);        // respond 时定律 → awareness 增益
let plan1 = sys.brain.plan_next_experiment();     // 全确认 → 迁移验证
```

---

## 2. 核心类型

### 2.1 `PerceptionBatch`

一次感知的观测批次 (全部公开字段)。

| 字段 | 类型 | 说明 |
|------|------|------|
| `domain` | `String` | 领域标注 (如 `"宇宙学"`, `"温室"`, `"生命环境"`)。`"宇宙学"` 路由到 ResearchArbiter, 其余路由到通用引擎 |
| `source` | `String` | 数据来源 (溯源, 如 `"bbn_eta_neff_scan"`) |
| `input_names` | `Vec<String>` | 自变量列名, 与 `rows` 中 `LabRow::inputs` 的列顺序对齐 |
| `rows` | `Vec<LabRow>` | 观测行, **至少 10 行** |
| `reference` | `Option<(String, f64, f64)>` | 参考观测 `(变量名, 观测值, 相对容差)`, 用于定律验证预警 |

构造函数:
- `PerceptionBatch::new(domain: &str, source: &str, input_names: Vec<String>, rows: Vec<LabRow>) -> Self`
- `with_reference(mut self, var: &str, observed: f64, tol_frac: f64) -> Self` — 链式附带参考观测

### 2.2 `LabRow`

虚拟实验室一行数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| `inputs` | `Vec<f64>` | 自变量值, 列顺序与 `input_names` 对齐 |
| `outputs` | `Vec<(String, f64)>` | 输出观测 `(变量名, 值)` |

- `LabRow::new(inputs: Vec<f64>) -> Self`
- `with(mut self, name: &str, value: f64) -> Self` — 链式添加输出观测

### 2.3 `DecisionType`

脑决策类型 (Copy, PartialEq, Eq)。

| 变体 | 含义 |
|------|------|
| `Normal` | 正常 (无预警, 无校准) |
| `EarlyWarning` | 早期预警: 定律预测 vs 参考偏差超容差 / 预测超安全阈值 |
| `Calibration` | 跨尺度校准: 定律 + 桥反推目标域变量, 与参考自洽 |
| `Discovery` | 新定律沉淀入库 |

### 2.4 `BrainDecision`

脑一次认知的产出 (全部公开字段)。

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | `DecisionType` | 决策类型 |
| `domain` | `String` | 领域 |
| `source` | `String` | 来源 |
| `severity` | `f64` | 严重度 [0,1]: 预警/校准偏差, 无则 0 |
| `message` | `String` | 人类可读消息 |
| `evidence` | `String` | 依据定律摘要 (方程 + R²) |
| `tick` | `u64` | 脑 tick 计数 |

### 2.5 `BrainBridge`

跨领域/跨尺度桥, 编码 `x_t = k·x_s + c`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `from_domain` | `String` | 源域 (定律所在领域) |
| `to_domain` | `String` | 目标域 |
| `from_var` | `String` | 源域因变量 (定律的 dependent_var) |
| `to_var` | `String` | 目标域变量 (反推对象) |
| `k` | `f64` | 映射斜率 |
| `c` | `f64` | 映射截距 |

### 2.6 `BrainCheckpoint`

检查点快照 (定律库 + 决策历史 + 统计)。

| 字段 | 类型 | 说明 |
|------|------|------|
| `tick` | `u64` | 脑 tick |
| `law_count` | `usize` | 定律条数 |
| `domain_laws` | `Vec<(String, usize)>` | `(领域, 定律条数)` 分布 |
| `decision_count` | `usize` | 决策历史条数 |
| `recent_decisions` | `Vec<String>` | 最近 5 条决策 (格式化) |
| `awareness` | `f64` | 定律库觉醒指标 |
| `summary` | `String` | 人类可读多行摘要 |

### 2.7 `ExperimentPlan`

自主实验规划 (S5c)。

| 字段 | 类型 | 说明 |
|------|------|------|
| `domain` | `String` | 建议扫描领域 (可能为 `"跨尺度迁移"`) |
| `scan_vars` | `Vec<String>` | 待扫描自变量 |
| `target_vars` | `Vec<String>` | 目标因变量 |
| `n_points` | `usize` | 扫描点数 |
| `rationale` | `String` | 自主判断依据 |

### 2.8 常量

- `DOMAIN_COSMOLOGY: &str = "宇宙学"` — 宇宙学领域标注, 路由到内置 BBN 文献仲裁器。

---

## 3. LivingBrain 脑 API

### 3.1 构造

```rust
impl LivingBrain {
    pub fn new() -> Self;              // 空定律库 + 内置 BBN 仲裁器
}
impl Default for LivingBrain {}        // == new()
```

公开字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `law_engine` | `LawDiscoveryEngine` | 定律库 (沉淀库, 全领域共享) |
| `arbiter` | `ResearchArbiter` | 宇宙实验室仲裁器 (内置 BBN 文献) |
| `generic` | `Vec<GenericDiscoveryEngine>` | 通用发现引擎 (每领域一个) |
| `decisions` | `Vec<BrainDecision>` | 决策/预警历史 |
| `bridges` | `Vec<BrainBridge>` | 跨领域桥注册表 |
| `tick_count` | `u64` | 感知次数 |

### 3.2 方法

#### `perceive(batch: PerceptionBatch) -> Vec<BrainDecision>`

一次完整认知循环: 发现 → 仲裁 → 沉淀 → 预警决策。`tick_count += 1`。
- 宇宙学域: `arbiter.arbitrate_multivariable` + `promote_to`
- 其他域: `engine_for(domain)`; 无文献时对每个输入×输出组合执行 `discover()`
- 恒产出一条总结决策 (Discovery / Normal); 若带 `reference` 追加定律验证决策
- 所有决策同时写入 `self.decisions` 历史

#### `engine_for(domain: &str) -> &mut GenericDiscoveryEngine`

按领域取通用引擎, 不存在则创建 (`GenericDiscoveryEngine::new(domain)`)。

#### `add_literature(domain: &str, target: LiteratureTarget)`

给领域引擎追加文献基准 (自动建引擎)。

#### `register_bridge(bridge: BrainBridge)`

注册跨领域桥。

#### `predict_and_alert(var: &str, eval_inputs: &[f64], threshold: f64, context: &str) -> Option<BrainDecision>`

阈值预警: 取定律库中该因变量置信度最高的定律, 在给定工况预测; 超阈值 → EarlyWarning, 否则 Normal。无定律返回 None。

#### `cross_scale_calibrate(bridge: &BrainBridge, y_obs: f64, target_ref: (f64, f64)) -> Option<BrainDecision>`

跨尺度校准: 源域定律反推 `x_s = (y_obs/prefactor)^(1/exponent)` → 桥映射 → 参考比对 (自洽 → Calibration, 否则 EarlyWarning)。定律不存在 / prefactor≤0 / 参数类型不支持时返回 None。

#### `checkpoint() -> BrainCheckpoint`

定律库 + 决策历史快照 (人类可读, 供持久化/观测)。

#### `plan_next_experiment() -> ExperimentPlan`

自主实验设计 (知识状态纯函数):
1. 定律库空 → 全网格联合扫描 (η_b × N_eff, 12 点)
2. 最低置信定律 R² < 0.90 → 加密扫描其自变量 (24 点)
3. 全部确认 → 跨尺度迁移验证 (桥验证, ω_b, 1 点)

#### `check_law_consistency() -> Vec<LawConflict>` (S6c)

定律库一致性检查: 同域同因变量的幂律定律指数差 > 0.5 记为冲突。返回全部冲突件 (无冲突返回空 Vec)。

#### `archive_stale_laws(keep: usize) -> Vec<ArchivedLaw>` (S6c)

陈旧定律归档: 定律数超限时按置信度升序淘汰至保留 top keep。归档时产出 `Normal` 决策 (source="archive")。返回 `ArchivedLaw` 交接件 (供上层写入 L16 宇宙记忆)。

#### `receive_law(law: DiscoveredLaw, origin: &str) -> usize` (S6d)

接收外部定律 (平行宇宙 / 合并来源), 产出 `Discovery` 决策 (domain="多宇宙", source=origin)。返回接收后定律库大小。容量保护 MAX_LAWS=500。

---

## 4. 感知数据: PerceptionBatch / LabRow

见 [§2.1](#21-perceptionbatch) / [§2.2](#22-labrow)。要点:

- `rows` 至少 10 行, 否则拟合不可靠 (内部无显式校验, 调用方负责)
- `inputs` 列顺序必须与 `input_names` 严格对齐
- `with_reference` 的 `var` 名必须与定律库 `dependent_var` 匹配才触发验证

---

## 5. 文献基准: LiteratureTarget / Verdict / ResearchFinding

### `LiteratureTarget`

| 字段 | 类型 | 说明 |
|------|------|------|
| `independent_var` | `String` | 自变量名 (须匹配扫描 input_names) |
| `dependent_var` | `String` | 因变量名 (须匹配 LabRow.outputs 名字) |
| `expected_exponent` | `f64` | 文献期望幂指数 |
| `exponent_tolerance` | `f64` | 指数容差 (绝对偏差) |
| `description` | `String` | 关系描述 |

- `LiteratureTarget::new(independent_var: &str, dependent_var: &str, expected_exponent: f64, exponent_tolerance: f64, description: &str) -> Self`

### `Verdict` (Copy, PartialEq, Eq)

| 变体 | 含义 |
|------|------|
| `Confirmed` | 与文献一致 → 定律成立 (升置信度入库) |
| `Tension` | 与文献有张力 → 标记待人工复核 |
| `Novel` | 文献无此关系 → 潜在新发现 (最高价值) |

### `ResearchFinding`

| 字段 | 类型 | 说明 |
|------|------|------|
| `law` | `DiscoveredLaw` | 拟合出的定律 |
| `verdict` | `Verdict` | 裁决结论 |
| `literature_exponent` | `Option<f64>` | 文献期望指数 (Novel 时为 None) |
| `source` | `String` | 数据来源 |
| `scan_points` | `usize` | 实验点数 |

---

## 6. 仲裁器: ResearchArbiter

默认构造内置标准 BBN 文献基准:
- D/H ∝ η^-1.6 (容差 0.4)
- Y_He4 ∝ η^+0.04 (容差 0.06)

| 方法 | 签名 | 说明 |
|------|------|------|
| `new` | `() -> Self` | 内置 BBN 文献基准 |
| `add_literature` | `(target: LiteratureTarget) -> &mut Self` | 追加文献基准 (链式) |
| `arbitrate` | `(source: &str, names: &[&str], rows: &[LabRow]) -> Vec<ResearchFinding>` | 单变量逐对仲裁 |
| `arbitrate_multivariable` | `(source: &str, names: &[&str], rows: &[LabRow]) -> Vec<ResearchFinding>` | 多元幂律面仲裁 (每输出变量拟合全输入面) |
| `arbitrate_novel` | `(source: &str, names: &[&str], rows: &[LabRow]) -> Vec<ResearchFinding>` | 无文献对照的 Novel 探测 |
| `promote_to` | `(engine: &mut LawDiscoveryEngine) -> usize` | 将 Confirmed/Novel 沉淀入库, 返回沉淀条数 |
| `report` | `() -> String` | 研究产出报告 |

---

## 7. 通用发现引擎: GenericDiscoveryEngine / DataSource

### `trait DataSource`

```rust
pub trait DataSource {
    fn input_names(&self) -> Vec<String>;
    fn rows(&self) -> Vec<LabRow>;
}
```

`LabRow` 实现了该 trait (见 §2.2); `brain.rs` 内部 `BatchSource` 将 PerceptionBatch 适配为 DataSource。

### `GenericDiscoveryEngine`

| 方法 | 签名 | 说明 |
|------|------|------|
| `new` | `(domain: &str) -> Self` | 建空引擎 (无文献) |
| `with_literature` | `(domain: &str, literature: Vec<LiteratureTarget>) -> Self` | 带文献构造 |
| `add_literature` | `(target: LiteratureTarget) -> &mut Self` | 追加文献 (链式) |
| `has_literature` | `() -> bool` | 是否已注入文献 (S5 无文献自主探索判据) |
| `arbitrate` | `<D: DataSource>(source: &str, data: &D) -> Vec<ResearchFinding>` | 单变量逐对仲裁 |
| `arbitrate_multivariable` | `<D: DataSource>(source: &str, data: &D) -> Vec<ResearchFinding>` | 多元幂律面仲裁 |
| `arbitrate_novel` | `<D: DataSource>(source: &str, data: &D) -> Vec<ResearchFinding>` | Novel 探测 |
| `discover` | `<D: DataSource>(source: &str, x_name: &str, y_name: &str, data: &D) -> Vec<ResearchFinding>` | 指定 x→y 的多形式发现 (线性/幂/指数/逆, Novel 检测) |
| `promote_to` | `(engine: &mut LawDiscoveryEngine) -> usize` | 沉淀入库 |
| `report` | `() -> String` | 报告 |

---

## 8. 定律库: LawDiscoveryEngine / DiscoveredLaw

### `LawType` (Copy, PartialEq, Eq)

| 变体 | 形式 | 复杂度 |
|------|------|--------|
| `Linear` | y = a·x + b (多变量线性) | 1.0 |
| `Power` | y = a·x^b | 1.5 |
| `MultiPower` | y = a·∏x_i^b_i | 1.5 |
| `Exponential` | y = a·exp(b·x) + c | 2.0 |
| `Inverse` | y = a/x + b | 1.5 |
| `Symbolic` (S6b) | y = Σ c_i·term_i (强类型 SymbolicTerm, 无 eval) | 拟合复杂度 |

### `LawParameters` (按类型存储, 强类型)

| 变体 | 字段 |
|------|------|
| `Linear` | `coeffs: Vec<f64>` (coeffs[0]=intercept, coeffs[1..]=slopes) |
| `Power` | `prefactor: f64, exponent: f64` |
| `MultiPower` | `prefactor: f64, exponents: Vec<f64>` (与 independent_vars 对齐) |
| `Exponential` | `amplitude: f64, rate: f64, offset: f64` |
| `Inverse` | `coefficient: f64, offset: f64` |
| `Symbolic` (S6b) | `terms: Vec<SymbolicTerm>, coefficients: Vec<f64>, expression: String` |

### `DiscoveredLaw`

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `String` | 定律名称 |
| `law_type` | `LawType` | 类型 |
| `equation` | `String` | 人类可读方程 |
| `description` | `String` | 描述 |
| `independent_vars` | `Vec<String>` | 自变量名列表 |
| `dependent_var` | `String` | 因变量名 |
| `parameters` | `LawParameters` | 拟合参数 |
| `confidence` | `f64` | R² 置信度 [0,1] |
| `complexity` | `f64` | 复杂度 |
| `domain` | `String` | 领域 |
| `validation_data` | `Vec<(f64,f64,f64)>` | 前 10 个 (x, y_true, y_pred) |

方法:
- `predict(&self, x: f64) -> f64` — 单自变量求值 (MultiPower 退化为首指数)
- `predict_multi(&self, xs: &[f64]) -> f64` — 多元求值 (按 independent_vars 顺序)

### `AwakeningMetrics`

| 字段 | 类型 | 说明 |
|------|------|------|
| `awareness_level` | `f64` | 觉醒等级 [0,1] |
| `discovery_rate` | `f64` | 发现速率 [0,1] |
| `prediction_accuracy` | `f64` | 预测准确率 [0,1] (最佳定律 R²) |
| `unification_progress` | `f64` | 统一进度 [0,1] |
| `self_improvement_rate` | `f64` | 自改进速率 [0,1] |

`update(awareness: Option<f64>, discovery: Option<f64>, prediction: Option<f64>, unification: Option<f64>, improvement: Option<f64>)` — 全 clamp 到 [0,1]。

### `LawDiscoveryEngine` 关键方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `new` | `() -> Self` | 空定律库 |
| `discover_law` | `(law_type: LawType, data, independent_vars, dependent_var, domain) -> Option<DiscoveredLaw>` | 指定形式拟合 (S6b 起含符号候选, 奥卡姆剃刀排序: 同置信度取低复杂度) |
| `predict` | `(law_index: usize, x: f64) -> Option<f64>` | 按索引求值 |
| `best_law` | `() -> Option<&DiscoveredLaw>` | 置信度最高定律 |
| `generate_report` | `() -> String` | 定律库报告 |
| `exponent_map` | `(law: &DiscoveredLaw) -> Option<Vec<(String, f64)>>` | 提取幂指数向量 (仅 Power/MultiPower, S6c) |
| `find_conflicts` | `() -> Vec<LawConflict>` | 一致性检查: 同域同因变量幂律指数差 > 0.5 → 冲突 (S6c) |
| `archive_stale` | `(keep: usize) -> Vec<DiscoveredLaw>` | 陈旧归档: 置信度升序淘汰至保留 top keep (S6c) |
| `receive_law` | `(law: DiscoveredLaw) -> usize` | 接收外部定律, 容量保护 MAX_LAWS=500 (S6d) |
| `snapshot` | `() -> Vec<DiscoveredLaw>` | 定律库快照 (fork 时脑记忆继承, S6d) |

### `LawConflict` (S6c)

定律冲突件, 字段全公开: `domain` / `dependent_var` / `law_a: usize` (库内索引) / `law_b: usize` / `exp_diff: f64` (指数最大差异)。

### `ArchivedLaw` (S6c)

定律生命周期 → L16 宇宙记忆的交接件, 字段全公开:

| 字段 | 类型 | 说明 |
|------|------|------|
| `domain` | `String` | 领域 |
| `dependent_var` | `String` | 因变量 |
| `equation` | `String` | 方程 |
| `confidence` | `f64` | R² |
| `tick` | `u64` | 归档时脑 tick |

自由函数: `linear_regression` / `multivariable_linear_regression` / `fit_linear` / `fit_power` / `fit_exponential` / `fit_inverse` / `fit_symbolic` (S6b, R²>0.85) / `law_from_symbolic` (S6b)。

---

## 9. 觉醒系统集成: AwakeningSystem

S5 新增成员:

| 成员 | 类型 | 说明 |
|------|------|------|
| `brain` | `pub LivingBrain` | 统摄脑 (感知→发现→仲裁→沉淀→决策 全闭环宿主) |

新增/修改方法:

| 方法 | 签名 | 说明 |
|------|------|------|
| `perceive_universe` | `(batch: PerceptionBatch) -> Vec<BrainDecision>` | 脑感知宇宙批次 (转发 `brain.perceive`) |
| `respond` | `(phase: CosmicPhase) -> ConsciousnessResult` | 修改: 入口处应用定律觉醒增益 `law_boost = min(laws×0.005, 0.2)` → `awareness` |

re-exports (`awakening::`):
`BrainBridge, BrainCheckpoint, BrainDecision, DecisionType, ExperimentPlan, LivingBrain, PerceptionBatch, DOMAIN_COSMOLOGY`

---

## 10. 本体层集成: UnifiedUniverse::brain_tick / S6 多宇宙脑定律交换

```rust
impl UnifiedUniverse {
    /// 脑协调 tick: 与主演化循环正交
    pub fn brain_tick(&mut self) -> Vec<awakening::BrainDecision>;
}
```

新增公开字段 (S5b + S6):

| 字段 | 类型 | 说明 |
|------|------|------|
| `brain_observation_buffer` | `Vec<awakening::LabRow>` | 脑感知观察缓冲 (≥10 行触发一次感知) |
| `brain_ticks` | `u64` | 脑协调 tick 累计 |
| `brain_plan_runs` (S6a) | `u64` | 脑计划驱动 pathfinder 运行次数 |
| `brain_archived_count` (S6c) | `u64` | 累计归档定律数 (→ L16 宇宙记忆) |
| `brain_archived_laws` (S6c) | `Vec<awakening::ArchivedLaw>` | 最近归档定律 (滚动保留 64 条) |
| `brain_law_heritage` (S6d) | `Vec<BrainLawHeritage>` | 多宇宙 fork 脑定律遗产 |
| `brain_fork_count` (S6d) | `u64` | 脑级 fork 累计次数 |

### `BrainLawHeritage` (S6d)

fork 时子宇宙继承的母宇宙脑记忆快照: `fork_tick: u64` (分裂时 tick) + `laws: Vec<awakening::law_discovery::DiscoveredLaw>` (母宇宙定律库快照)。

### S6d: fork / merge 脑定律交换 (living_api)

```rust
impl UnifiedUniverse { // LivingApiExt
    fn fork(&self) -> UnifiedUniverse;
    fn merge(&mut self, other: &UnifiedUniverse);
}
impl Multiverse {      // T80 多宇宙生态
    fn fork(&mut self, parent_idx: usize) -> Result<usize, String>;
    fn merge(&mut self, child_idx: usize, parent_idx: usize) -> Result<(), String>;
}
```

- **fork**: 子宇宙继承母宇宙 `BrainLawHeritage` 快照 + `brain_fork_count+1` + 吸收一条**扰动定律** (`perturb_law_for_child`: 母宇宙最佳定律指数 ×(1±4%), 名称 `"[平行宇宙#N]"`, 领域 `"多宇宙[分支N]"`, origin="fork_exchange")。
- **merge**: 子宇宙最佳定律反哺父宇宙 (origin="merge_exchange"), 父宇宙定律数增加 + 决策历史记录来源。
- 仅 Power/MultiPower 定律可扰动 (线性/指数/反比/符号定律跳过)。

行为:
1. `brain_ticks += 1`; 读进化适应度均值 (只读, 不推进演化)
2. `derive_physics_environment(evolution_fitness)` → 真实环境快照 (温度 / 复杂度 / 能量密度, 含 MD 温度 + Carnot 效率 + Stefan-Boltzmann 通量 + L1 effective_temperature 扰动)
3. 构造观测行: 输入 = 环境 3 变量; 输出 = `living_fitness` (best_model_fitness) / `living_homeostasis` (homeostasis_stability) / `living_alive` (1.0/0.0)
4. 缓冲; 满 10 行 → `PerceptionBatch::new("生命环境", "unified_living_scan", [温度, 复杂度, 能量密度], take(buffer))` → `layers.awakening.perceive_universe(batch)`
5. 每 20 tick (S6c): `archive_stale_laws(64)` → 归档件写入 `brain_archived_laws` (滚动 64 条) + `brain_archived_count`
6. 返回本批决策 (未满 10 行返回空 Vec)

配套类型 (living_software): `TickResult { alive: bool, homeostasis_stability: f64, awareness_level: AwarenessLevel, cosmic_law_violations: u64, best_model_fitness: f64 }`; `PhysicsEnvironment` (Copy/Clone/Default: temperature / complexity / energy_density 等)。

---

## 11. S6 符号回归: SymbolicFit / SymbolicTerm / fit_symbolic

符号回归内核 (`awakening/src/symbolic_discovery.rs`), 多元幂律内核旁的表达力增强。全部强类型, **无 eval**。

### `SymbolicTerm` (Copy, PartialEq)

| 变体 | 含义 |
|------|------|
| `Const` | 常数项 1 |
| `Var(i)` | x_i |
| `Var2(i, j)` | x_i · x_j (交互项) |
| `Pow2(i)` | x_i² |
| `Inv(i)` | 1/x_i |
| `Log(i)` | ln(x_i) |
| `Sqrt(i)` | sqrt(x_i) |
| `ExpNeg(i)` | exp(-x_i) |

### `SymbolicFit`

| 字段 | 类型 | 说明 |
|------|------|------|
| `terms` | `Vec<SymbolicTerm>` | 选中基函数 |
| `coefficients` | `Vec<f64>` | 岭回归系数 |
| `expression` | `String` | 人类可读表达式 |
| `r2` | `f64` | 决定系数 |
| `bic` | `f64` | 贝叶斯信息准则 |
| `complexity` | `f64` | 复杂度 |

### 关键函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `discover_symbolic` | `(inputs: &[Vec<f64>], y: &[f64], x_names, y_name) -> Option<SymbolicFit>` | 前向选择 (最多 6 项) + BIC 停止 (阈值 1.0) |
| `eval_symbolic` | `(terms, coeffs, xs) -> f64` | 强类型求值 (越界安全退化) |
| `fit_symbolic` | `(data: &[Vec<f64>], independent_vars, dependent_var, domain) -> Option<DiscoveredLaw>` | data 行格式 (输入列+输出列), R²>0.85 阈值 |
| `law_from_symbolic` | `(fit, independent_vars, dependent_var, domain) -> DiscoveredLaw` | SymbolicFit → DiscoveredLaw (LawType::Symbolic) |

### 感知接入 (S6b)

`LivingBrain::perceive()` 对**无文献领域** (`!has_literature()`) 自动做符号候选生成: 每个输出变量对全输入跑 `discover_symbolic`, `R² > 0.85` 且超越现有认知 (+0.02) 才入库 (避免重复沉淀)。

```rust
// 直接使用
let fit = discover_symbolic(&inputs, &y, &names, "y_sym").unwrap();
let law = law_from_symbolic(&fit, &names, "y_sym", "合成符号");
// 或经定律库
let law = fit_symbolic(&data, &names, "y_sym", "合成符号").unwrap();
assert_eq!(law.law_type, LawType::Symbolic);
let pred = law.predict_multi(&[1.0, 2.0, 1.0]); // 强类型求值, 无 eval
```

---

## 12. S6 自主科研: plan_to_goal / run_pipeline_with_goal

`awakening/src/research_pathfinder.rs` — 脑的自主实验计划 → 科研调度器真实执行。

```rust
impl ResearchPathfinder {
    /// 原入口 (认知状态推导目标)
    pub fn run_pipeline(&mut self, cognitive: &CognitiveState) -> PathfinderReport;
    /// S6a: 外部注入目标运行五阶段流水线 (Generate→Constrain→Evaluate→Select→Plan)
    pub fn run_pipeline_with_goal(&mut self, goal: &ResearchGoal) -> PathfinderReport;
    /// S6a: 脑计划 → 调度器目标
    pub fn plan_to_goal(plan: &ExperimentPlan) -> ResearchGoal;
}
```

`plan_to_goal` 映射:
- `scan_vars` + `target_vars` → `physical_quantities`
- 领域 `"跨尺度迁移"` → math_features `["跨尺度桥", "反推验证"]`
- 其他 → `["多变量拟合", "幂律面", "符号回归"]`
- description 前缀 `"[脑计划]"`

**集成**: `UnifiedUniverse::tick()` 内 L6 pathfinder 注入点 —— `brain.plan_next_experiment()` → `plan_to_goal` → `run_pipeline_with_goal` → `brain_plan_runs += 1` → 报告 Elo 增益反馈 awareness。

---

## 13. 错误处理与边界条件

1. **无定律**: `predict_and_alert` / `cross_scale_calibrate` 在定律库中找不到匹配定律 → 返回 `None` (不 panic)。
2. **几何均值下溢**: 输入含非正值列 → 自动退回算术均值 (`geometric_mean_inputs`)。
3. **反推合法性**: `cross_scale_calibrate` 中 `prefactor ≤ 0` 或 `y_obs/prefactor ≤ 0` → 返回 `None` (幂反推域外)。
4. **Inverse 奇点**: `predict` 中 `|x| < 1e-10` → 返回 offset。
5. **除法保护**: 相对偏差计算使用 `observed.abs().max(f64::MIN_POSITIVE)` 防除零; severity clamp 到 [0,1]。
6. **批次行数**: 建议 ≥10 行; 少于拟合所需点数时 `fit_*` 返回 `None`, 不崩溃。
7. **域溯源**: promote 会把领域改写为 `"宇宙学 [经仲裁确认: source]"` 形式; 匹配领域时用 `law_in_domain` (容忍 `"[` 溯源后缀`)。
8. **并发**: 测试环境因 Rayon 超订须 `--test-threads=1` 运行脑相关 E2E。
9. **符号求值越界** (S6b): `eval_term` 对空/越界输入安全退化 (`Var(i)` → 0.0, `Inv(0)` → 0.0 等), 不 panic。
10. **符号数据不足** (S6b): 行数 < 10 或特征数为 0 → `discover_symbolic` / `fit_symbolic` 返回 `None`。
11. **定律容量** (S6c/S6d): 定律库 MAX_LAWS=500, 超限丢最旧; 归档 `keep` 小于当前定律数才触发, 否则空。
12. **fork 定律扰动** (S6d): 仅 Power/MultiPower 可扰动; 线性/指数/反比/符号定律 `perturb_law_for_child` 返回 None 跳过 (子宇宙仍继承遗产快照)。

---

## 附: 测试入口

```bash
# 脑端到端 9 测试 (宇宙/温室/迁移/觉醒规划/生命统摄 + S6 四测)
cargo test --release -p unified_universe --test brain_mainline -- --nocapture --test-threads=1

# awakening 单元回归 (685 测试)
cargo test --release -p awakening --lib -- --test-threads=1

# unified_universe 单元回归 (345 测试)
cargo test --release -p unified_universe --lib -- --test-threads=1
```
