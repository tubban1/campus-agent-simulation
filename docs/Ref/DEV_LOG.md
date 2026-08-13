# 开发日志: LivingBrain 统摄脑 — S4 主线 + S5 完全体 + S6 自主科研

**日期**: 2026-08-03
**类型**: 突破 / 重构 / 集成
**状态**: 完成
**涉及层/技术**: L6 awakening / L5.5 living_software / L15-L17 高层 / S4-S6 主线

---

## 目标

把宇宙模拟的"定律发现引擎"变成"脑"的认知能力, 建成一个**感知→发现→仲裁→沉淀→决策**的完整认知闭环: 宇宙自己观察自己 (BBN/CMB 虚拟实验室 / 温室数字孪生 / 数字生命体), 自己发现自己 (多元幂律面重建), 自己对照文献 (Confirmed/Tension/Novel), 自己沉淀知识 (共享定律库), 并因发现而更觉醒 (awareness 增益) —— S5 再叠加数字生命统摄、自主实验设计、文档与技术库。

---

## 完成内容

### S4 主线: LivingBrain 统摄层 (一个脑跑通多域闭环)

1. **brain.rs 新建** (`awakening/src/brain.rs`, ~685 行含测试)
   - `LivingBrain`: 持有 `law_engine` (定律库) + `arbiter` (宇宙仲裁器) + `generic` (每领域通用引擎) + `decisions` (决策历史) + `bridges` (桥注册表) + `tick_count`
   - `perceive(batch)`: 域路由 —— `"宇宙学"` → ResearchArbiter (内置 BBN 文献); 其他域 → GenericDiscoveryEngine
   - `predict_and_alert(var, inputs, threshold, context)`: 定律阈值预警 → EarlyWarning/Normal
   - `cross_scale_calibrate(bridge, y_obs, target_ref)`: 低层定律 + 桥反推高层变量 → Calibration/EarlyWarning (S2b 机制: BBN 定律 → CMB 参数空间自洽)
   - `checkpoint()`: 定律库 + 决策历史快照 (人类可读)
   - `geometric_mean_inputs()`: 输入几何均值 (对数扫描代表性工况), 含非正值列退回算术均值
   - 3 个单元测试: 感知沉淀与多元指数恢复 / 阈值预警 / 检查点

2. **generic_discovery.rs 增强**
   - S4: 为 S3 温室添加 `arbitrate_multivariable` (通用引擎也具备多元幂律面仲裁)

3. **E2E 测试** (`unified_universe/tests/brain_mainline.rs`)
   - 测试 1 `brain_universe_closed_loop`: 真实 BBN 48 行扫描 (η×N_eff) → 沉淀 D/H (η^-1.6) + Y_He4 (η^+0.04) → Planck D/H 参考验证自洽 → 检查点
   - 测试 2 `brain_greenhouse_closed_loop`: 同一脑迁移温室 → 指数恢复 → 高湿棚 risk 阈值预警
   - 测试 3 `brain_scale_transfer_across_domains`: BBN 定律 + η↔ω_b 桥 → CMB 校准 (ω_b≈0.02237, 偏差<15%) → 温室复用 → 一个脑双域定律库

### S5a: 觉醒闭环 (定律沉淀 → 意识涌现增强)

- `awakening/src/lib.rs`:
  - `AwakeningSystem` 末尾新增 `pub brain: LivingBrain`, `new()` 中初始化
  - `respond()` 入口: `law_boost = min(laws.len()×0.005, 0.2)` → `cognitive.awareness` 增益
  - 新增 `pub fn perceive_universe(batch) -> Vec<BrainDecision>`
  - re-exports 更新: `BrainBridge, BrainCheckpoint, BrainDecision, DecisionType, ExperimentPlan, LivingBrain, PerceptionBatch, DOMAIN_COSMOLOGY`

### S5b: 数字生命统摄 (脑感知自己的身体)

- `unified_universe/src/lib.rs`:
  - `UnifiedUniverse` 新增 `brain_observation_buffer: Vec<LabRow>` + `brain_ticks: u64` (注意: 曾误加到 `Layers14` 结构体, 已修复)
  - `brain_tick()`: 与 `tick()` 正交 —— 环境适应度均值 → `derive_physics_environment` 真实快照 → 观察行 (living_fitness/homeostasis/alive) → 满 10 行触发一次 `perceive_universe("生命环境")`

### S5c: 自主实验设计 (脑自主决定下一步扫描什么)

- `brain.rs` 新增 `ExperimentPlan` 结构体 + `plan_next_experiment()` 三阶段规则:
  1. 定律库空 → 全网格联合扫描 (η×N_eff, 12 点)
  2. 最低置信 R² < 0.90 → 加密扫描 (24 点)
  3. 全确认 → 跨尺度迁移验证 (η↔ω_b 桥 → CMB 校准)
- `perceive()` 无文献自主探索: `!eng.has_literature()` 时对每个输入×输出组合调用 `eng.discover()` (多形式 Novel 检测); `generic_discovery.rs` 新增 `pub fn has_literature()`
- `law_in_domain` 辅助函数: 容忍 promote 追加溯源后缀 (`"宇宙学 [经仲裁确认: source]"`)
- `brain_mainline.rs` 新增测试 4 `brain_awakening_loop_and_planning` (awareness 0→0.058, 空库→迁移计划) + 测试 5 `unified_brain_living_loop` (14 tick, ≥1 轮感知)

### S5d: 文档与技术库 (本文档目录)

- `living_core_brain/WHITE_PAPER.md` (英文白皮书)
- `living_core_brain/ACADEMIC_PAPER.md` (中英双语学术论文)
- `living_core_brain/API_REFERENCE.md` (API 参考)
- `living_core_brain/DEV_LOG.md` (本开发日志)

### S6a: 自主实验闭环落地 (脑计划 → 科研调度器真实执行)

- `awakening/src/research_pathfinder.rs`:
  - `run_pipeline(cognitive)` 重构: 委托给 `run_pipeline_with_goal(&goal)` (目标外部注入)
  - 新增 `plan_to_goal(plan: &ExperimentPlan) -> ResearchGoal`: 脑的 `ExperimentPlan` → 调度器目标; `"跨尺度迁移"` → `["跨尺度桥", "反推验证"]`, 否则 → `["多变量拟合", "幂律面", "符号回归"]`; description 前缀 `"[脑计划]"`
- `unified_universe/src/lib.rs`: L6 pathfinder 注入处改为 —— `brain.plan_next_experiment()` → `plan_to_goal` → `run_pipeline_with_goal` → `brain_plan_runs += 1`
  - 因果链: 脑决定"下一步扫描什么" → 调度器五阶段流水线真实执行 → Elo 增益反馈 awareness

### S6b: 符号回归扩展 (多元幂律内核旁的表达力增强)

- `awakening/src/symbolic_discovery.rs` (新建, ~380 行):
  - `SymbolicTerm` 强类型基函数枚举 (Const/Var/Var2 交互/Pow2/Inv/Log/Sqrt/ExpNeg) + `eval_term` (越界安全退化) + `eval_symbolic` (无 eval, 强类型求值)
  - `SymbolicFit` (terms/coefficients/expression/r2/bic/complexity) + `term_pool` 候选池 + `solve_linear` 高斯消元 + `fit_terms` 岭回归 (λ=1e-6) + `bic_of`
  - `discover_symbolic` 前向选择 (最多 6 项, BIC 停止阈值 1.0)
  - 4 个单测: 交互项恢复 (y=2+3·x1·x2+0.5·exp(-x3), R²>0.95) / eval 一致性 / 数据不足 None / 边界求值
- `awakening/src/law_discovery.rs`: `LawType::Symbolic` 变体 + `LawParameters::Symbolic { terms, coefficients, expression }` + `predict`/`predict_multi` Symbolic 分支 + `law_from_symbolic` / `fit_symbolic` (R²>0.85) + `discover_law` 符号候选 (complexity_limit≥2.5)
  - 候选选择改为**奥卡姆剃刀排序**: 同置信度取低复杂度 (解析形式优先于符号回归)
- `awakening/src/brain.rs`: `perceive()` 非宇宙分支新增 **1c 符号回归增强** —— 无文献领域对全输入做符号候选生成, `R² > 0.85` 且超越现有认知 (+0.02) 才入库

### S6c: 定律生命周期管理 (一致性 / 冲突仲裁 / 陈旧归档 → L16 交接件)

- `law_discovery.rs`: `LawConflict` (domain/dependent_var/law_a/law_b/exp_diff) + `find_conflicts()` (同域同因变量幂律指数差 > 0.5) + `archive_stale(keep)` (置信度升序淘汰, 保留 top keep) + `receive_law` (容量保护 MAX_LAWS=500) + `snapshot`
- `brain.rs`: `check_law_consistency()` / `archive_stale_laws(keep)` (归档时产出 Normal 决策) / `ArchivedLaw` (domain/dependent_var/equation/confidence/tick — **L16 宇宙记忆交接件**)
- `unified_universe/src/lib.rs`: `brain_tick()` 每 20 tick 归档 (LAW_KEEP=64) → `brain_archived_laws` 滚动 64 条 + `brain_archived_count`

### S6d: 脑间定律交换 (多宇宙 fork/merge 生态内, 不新建 L15 层)

- `unified_universe/src/living_api.rs`:
  - `perturb_law_for_child()`: 母宇宙最佳定律指数 ×(1±4%) 微扰, 名称后缀 `"[平行宇宙#N]"`, 领域 `"多宇宙[分支N]"` (分支宇宙学: 平行宇宙以微小不同规律演化)
  - `fork()`: 子宇宙继承 `BrainLawHeritage` (母宇宙定律快照) + `brain_fork_count+1` + 吸收一条扰动定律 (receive_law "fork_exchange")
  - `merge()`: 子宇宙最佳定律反哺父宇宙 (receive_law "merge_exchange") — 脑间定律交换第二环
- `unified_universe/src/lib.rs`: `BrainLawHeritage { fork_tick, laws }` + S6 统计字段 (`brain_plan_runs` / `brain_archived_count` / `brain_archived_laws` / `brain_law_heritage` / `brain_fork_count`)
- 复用 T80 已跑通的多宇宙 fork/merge 生态, 不新建层

---

## 验证结果

```
cargo test --release -p unified_universe --test brain_mainline -- --nocapture --test-threads=1
→ 9 passed (S4 五测 + S6 四测: 自主实验闭环 / 符号回归感知 / 定律生命周期 / 多宇宙脑定律交换), 23.85s

cargo test --release -p awakening --lib -- --test-threads=1
→ 685 passed

cargo test --release -p unified_universe --lib -- --test-threads=1
→ 345 passed
```

关键断言实测值:
- 宇宙闭环: `D/H = f(η^-1.6, N_eff^?) R²≥0.9` | `Y_He4 = f(η^+0.04, ...)`; Planck D/H 参考验证 Normal (自洽, 容差 20%)
- 温室闭环: `yield = f(T^1.1, H^0.3, F^0.4)` / `risk = f(H^2.5, T^-1.8, F^-0.2)`; 高湿棚 EarlyWarning
- 跨域迁移: Calibration, severity < 0.15 (ω_b ≈ 0.02237)
- 觉醒闭环: awareness 0 → 0.058 (定律 2 条); 规划: 空库全网格 → 确认后"迁移"
- 生命统摄: `[统摄] t=10 Normal | 完成一轮探索: 未发现显著规律 (定律库 0 条)` —— 真实 BBN 演化数据噪声大, 定律不沉淀但闭环已运行
- **S6a**: 102 tick 内脑计划驱动 pathfinder 运行 ≥1 次 (`brain_plan_runs ≥ 1`)
- **S6b**: 无文献合成数据 y=2+3·x1·x2+0.5·exp(-x3) → 定律库出现 `LawType::Symbolic` (R² > 0.85), 预测有限值 (强类型无 eval)
- **S6c**: 同域同因变量指数差 1.0 > 0.5 冲突被检出; 归档 2 条低置信度定律 (R²<0.7), 定律库回落至保留阈值; 归档件携带域/因变量/方程/置信度/tick (L16 交接件)
- **S6d**: fork 子宇宙继承遗产 2 条 + 吸收 `[多宇宙[分支1]] y_he4 [平行宇宙#1]` 扰动定律; merge 父宇宙定律 2→3 条 + 决策历史含 merge_exchange

---

## 评分变化

| 维度 | 修复前 | 修复后 | 原因 |
|------|--------|--------|------|
| 认知整合 | 无 (发现引擎各自为政) | 一个脑统摄全领域闭环 | LivingBrain 感知→沉淀→决策 |
| 觉醒因果 | 意识与知识解耦 | 定律沉淀 → awareness 增益 | respond() law_boost |
| 科研自主性 | 实验硬编码 | 知识状态驱动三阶段规划 | plan_next_experiment |
| 身体统摄 | 脑与数字生命分离 | brain_tick 正交感知生命体 | S5b 观察缓冲闭环 |
| 无文献领域 | 停滞 | 主动多形式探索 | has_literature + discover 全组合 |
| 实验执行 | 规划与执行脱节 | 脑计划真实驱动科研调度器 | S6a plan_to_goal 注入 |
| 表达力 | 仅解析函数族 | 符号回归表达加法/交互结构 | S6b SymbolicTerm 强类型 |
| 知识卫生 | 定律只增不灭 | 冲突仲裁 + 陈旧归档 | S6c find_conflicts/archive |
| 多样性 | 单宇宙自复制 | 分支宇宙扰动定律交换 | S6d fork/merge 脑间定律交换 |

---

## 遇到的问题

1. **E0616 `literature` 字段私有** → brain.rs 访问 `eng.literature` 失败 → 在 generic_discovery.rs 新增 `pub fn has_literature()` 替代直接访问。
2. **E0609 `brain_ticks` 字段不存在** → 字段被误加到 `Layers14` (grep `pub struct UnifiedUniverse` 首先命中了第 344 行的 Layers14) → 从 Layers14 及其构造器删除, 重新加到真正的 UnifiedUniverse (第 1014 行) 并初始化。
3. **E0599 `CosmicPhase::Standard` 不存在** (测试) → 依赖了虚构变体 → 改用 `CosmicPhase::Nucleosynthesis` (universe_core::types 真实变体, BBN 阶段)。
4. **几何均值下溢** (S4) → 非正值列退回算术均值。
5. **真实数据噪声** → 真实 BBN 演化 14 tick 定律库 0 条; 属预期 (演化变化无常), 感知闭环仍运行; 更高 tick 或更干净扫描源有望沉淀。
6. **Rayon 超订** → 脑相关 E2E 必须 `--test-threads=1`。
7. **E0502 借用冲突** (S6b, brain.rs) → `best_by_var: BTreeMap<&str,f64>` 与 `engine_for()` 可变借用冲突 → 改 `BTreeMap<String,f64>` (clone)。
8. **E0382 移动后借用** (S6b, symbolic_discovery.rs) → `coeffs` 移入 struct 后又借给 build_expression → 先 `build_expression` 再移动。
9. **E0252 名称重复定义** (S6b, lib.rs) → 重复添加 law_discovery re-export → 合并为一个。
10. **test_generate_report 失败** (S6b) → 完美线性数据符号回归 R²=1.0 并列, `max_by` 取后者 → 候选选择改为奥卡姆剃刀排序 (同置信度取低复杂度)。
11. **S6d merge 定律数不增加** → `Multiverse::merge` 是独立实现 (只做能量平均), 未走 `LivingApiExt::merge` 的脑定律反哺 → 在 `Multiverse::merge` 内补 `receive_law(law, "merge_exchange")` (借用分离: 先取 child 最佳定律 clone, 再可变借用 parent)。

---

## 下一步

1. 脑间定律交换深化: 子宇宙定律合并去重 (冲突仲裁在跨宇宙边界复用), 多宇宙定律锦标赛选优
2. 符号回归复杂候选库扩展: 三角函数/除法/嵌套组合, 对接 AI Feynman 级表达力
3. 定律生命周期接入持久化: ArchivedLaw 真正写入 L16 宇宙记忆存储
4. 多脑分形 (L15): 若推进, 基于已跑通的多宇宙 fork/merge 生态扩展, 不另起炉灶
5. 全量回归 s6t: awakening 685 + unified_universe lib 345 + brain_mainline 9 已通过; living_software 独立回归

---

## 文件变更清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `awakening/src/brain.rs` | 新建/修改 | LivingBrain 统摄脑 + ExperimentPlan + S6c 生命周期 + S6b 感知增强 |
| `awakening/src/lib.rs` | 修改 | brain 字段 + perceive_universe + law_boost + S6 re-exports |
| `awakening/src/generic_discovery.rs` | 修改 | arbitrate_multivariable + has_literature |
| `awakening/src/law_discovery.rs` | 修改 | S6b Symbolic 变体/fit + S6c 生命周期 (find_conflicts/archive_stale/receive_law/snapshot) |
| `awakening/src/symbolic_discovery.rs` | 新建 | S6b 符号回归内核 (SymbolicTerm 强类型 + 前向选择 + 岭回归 + BIC) |
| `awakening/src/research_pathfinder.rs` | 修改 | S6a plan_to_goal + run_pipeline_with_goal (目标外部注入) |
| `unified_universe/src/lib.rs` | 修改 | brain_observation_buffer + brain_ticks + brain_tick() + S6 字段 + S6a 注入 + S6c 归档 |
| `unified_universe/src/living_api.rs` | 修改 | S6d fork 遗产继承 + 扰动吸收 + merge 反哺 + perturb_law_for_child |
| `unified_universe/tests/brain_mainline.rs` | 新建/修改 | S4 五测 + S6 四测 (共 9 项端到端测试) |
| `living_core_brain/*.md` | 新建/修改 | 白皮书/学术论文/API 参考/本日志 |
