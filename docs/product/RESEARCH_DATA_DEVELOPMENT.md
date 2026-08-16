# 社会仿真研究数据开发文档

本文档面向项目开发者，说明如何把校园 Agent 系统作为社会科学研究数据平台来维护和扩展。重点不是解释页面功能，而是说明研究数据从哪里来、如何组织、如何导出、如何保证可复现。

## 目标

系统应支持三类研究工作：

- 观察性研究：记录世界自然运行时的 Agent 行为、空间状态、关系网络和事件流。
- 干预性研究：记录 admin 或参与者注入事件后，世界状态和 Agent 行为如何变化。
- 方法研究：比较规则执行、LLM 决策、观察者关注和模型预算对仿真结果的影响。

开发时需要保持一个原则：所有影响仿真的输入和所有关键输出都应被结构化记录。不能只把结果显示在前端。

## 当前阶段判断

当前系统已经具备探索性研究基础，但尚未形成完整的可复现实验系统。

现有数据能够支持内部探索、课程项目、实习生分析、社会科学概念验证和方法验证原型。它已经记录了 Agent 身份、行为日志、世界事件、记忆、关系网络、空间状态、观察记录、模型调用和行动计划等基础数据。

但需要明确区分两件事：

- 数据量丰富，不等于具备科研实验能力。
- 有运行日志，不等于形成实验数据系统。

当前系统更接近一个“运行日志系统”：它能够说明世界运行时发生了什么，也能支持调试和探索性分析。下一阶段需要把它升级为“实验数据系统”：能够明确实验批次、分组、配置、干预、快照、导出数据集和质量报告。

论文和开发文档中建议明确区分三层系统：

| 层级 | 职责 | 当前状态 |
| --- | --- | --- |
| 运行系统 | 让 Agent 世界持续运行，处理 tick、计划、事件、观察者和模型调用 | 已有较好基础 |
| 实验系统 | 管理实验批次、分组、干预、配置、快照和复现条件 | 下一阶段重点建设 |
| 研究数据系统 | 清洗、聚合、导出和检查研究数据集 | 下一阶段重点建设 |

只有补齐实验系统和研究数据系统，项目才会从“可以观察 Agent 行为的虚拟世界”，进一步变成“可以被研究机构重复使用的 Agent 实验环境生成器”。

## 核心数据表

### 个体与状态

| 表 | 用途 | 研究价值 |
| --- | --- | --- |
| `residents` | Agent 基础身份、角色、当前位置 | 个体样本框、角色分组、空间位置 |
| `agent_profiles` | 当前任务、感知、长期目标等状态 | 行为解释、心理状态代理变量 |
| `agent_learning` | 行动后的学习记录 | 行为适应、经验积累 |
| `memories` | 记忆，包括经历、关系、语义和工作记忆 | 个体叙事、决策依据、长期影响 |
| `simulation_action_logs` | 感知、检索记忆、LLM 决策、执行结果 | 行为链路复盘、模型可解释性 |

### 关系网络

| 表 | 用途 | 研究价值 |
| --- | --- | --- |
| `relationships` | Agent 间基础关系分 | 社会网络边权 |
| `relationship_dynamics` | 好感、信任、合作、竞争、冲突 | 多维关系分析、冲突/合作机制 |
| `relationship_change_events` | 关系指标变化和触发原因 | 关系演化证据、涌现关系解释 |
| `social_interaction_events` | 共处、协作、冲突、资源交换等细粒度互动 | 关系生成机制、互动序列分析 |
| `social_relation_interpretations` | 某一时刻的关系解释、候选标签和证据 | 关系标签如何形成和变化 |
| `social_beliefs` | Agent 对关系的知道、怀疑、误解和公开/隐藏状态 | 信息不对称、秘密、误解和社会认知 |
| `collaborations` | 协作项目 | 群体任务和合作结构 |
| `competitions` | 竞争事件 | 竞争扩散、胜负影响 |
| `group_goals` | 群体目标 | 集体行动和目标推进 |

### 空间与环境

| 表 | 用途 | 研究价值 |
| --- | --- | --- |
| `campus_state` | 每日/当前环境、天气、人流、情绪等 | 外部环境变量 |
| `campus_spaces` | 空间容量、开放状态、拥挤度 | 空间机会结构 |
| `campus_events` | 校园事件 | 环境冲击、事件序列 |
| `external_information` | 外部新闻/资讯 | 外部信息输入 |
| `agent_information` | Agent 接收资讯记录 | 信息扩散路径 |

### 世界运行时

| 表 | 用途 | 研究价值 |
| --- | --- | --- |
| `world_runtime` | 世界运行状态、时间、tick 配置、预算 | 实验条件和运行配置 |
| `world_ticks` | 每次 tick 的开始、完成、处理人数、失败数 | 时间步审计、运行可靠性 |
| `world_event_stream` | 统一事件流 | 时间序列主表 |
| `agent_action_plans` | 8 小时行动计划 | 计划与实际行为偏离 |
| `agent_goals` | 长期、中期、短期目标及父子层级 | 目标形成、分解、竞争与完成 |
| `goal_dependencies` | 目标之间的支持、前置、冲突和替代 | 多目标权衡与资源竞争 |
| `goal_revisions` | 目标创建、调整、暂停、放弃和完成记录 | 目标演化与人生转折 |
| `agent_commitments` | 对他人、组织和制度的承诺 | 社会义务、违约与信任变化 |
| `plan_outcomes` | 计划步骤、实际行为、偏离原因与结果 | 计划遵从、拖延和机会响应 |
| `trajectory_episodes` | 多时间尺度的计划轨迹和实际轨迹 | 生命历程、路径依赖和阶段分析 |
| `observer_sessions` | 观察者进入、关注 Agent/地点 | 观察者效应 |
| `participant_actions` | 参与者互动预留 | v2 互动干预 |
| `model_call_logs` | 模型调用来源、状态、成本 | 成本与复杂度分析 |

### 实验与复现（下一阶段）

| 表 | 用途 | 研究价值 |
| --- | --- | --- |
| `experiment_runs` | 每次实验、运行批次、实验条件和状态 | 对照分析、实验分组、可复现条件 |
| `world_snapshots` | 实验开始、干预前后、关键时点的世界状态快照 | 状态复原、异常定位、实验比较 |
| `research_export_jobs` | 研究数据导出任务、范围、格式和质量报告位置 | 数据产品化、导出审计 |

这三类表是下一阶段从“运行日志”走向“科研实验”的关键，不应只作为附属日志处理。

## 实验运行单元

建议新增统一的 `experiment_runs`，将每次仿真明确识别为一个独立实验或运行批次。

最低字段建议：

```text
id
experiment_id
run_id
experiment_name
hypothesis
control_or_treatment
intervention_type
start_time
end_time
random_seed
environment_version
agent_config_version
model_config_version
world_rules_version
status
metadata_json
created_at
updated_at
```

其中：

- `experiment_id` 表示研究项目或实验设计。
- `run_id` 表示某一次具体运行。
- `control_or_treatment` 用于标记对照组、实验组或自然运行组。
- `metadata_json` 保存额外配置，但关键字段仍应结构化。

后续所有行为、事件、关系、记忆和模型调用数据，都应逐步关联到 `run_id`。没有这一层，多个实验的数据容易混在一起，也无法做严格对照分析。

## 世界快照

建议新增 `world_snapshots`，保存实验开始时、干预前后以及关键时间点的世界状态。

快照不只是备份数据，而是为了回答：

> 在某个时刻，整个实验世界处于什么状态？

快照建议覆盖：

- Agent 状态：位置、任务、能量、目标、profile 模块。
- 关系网络：关系边、信任、合作、竞争、冲突。
- 空间状态：开放状态、容量、拥挤度、在场 Agent。
- 世界事件：最近事件流和活跃 campus events。
- 环境参数：天气、时间、人流、校园情绪、资源压力。
- 活跃计划：8 小时行动计划和当前执行步骤。
- 资源与规则配置：模型预算、tick 间隔、同步开关、规则版本。

最低字段建议：

```text
id
run_id
snapshot_type
world_time
day
tick_id
reason
state_json
schema_version
created_at
```

`snapshot_type` 可以包括：

- `run_start`
- `pre_intervention`
- `post_intervention`
- `daily_checkpoint`
- `manual_checkpoint`
- `error_checkpoint`

为了控制体积，第一版可以把快照保存为 JSON；后续如果研究需求稳定，再拆成更规范的快照明细表。

## 实验配置记录

目前模型、规则、tick、预算和外部同步信息分散在不同表中。下一阶段应形成统一的实验 metadata，避免实验结束后无法确认当时到底运行了哪套配置。

建议记录：

```text
simulation_tick_seconds
simulation_speed
agents_per_tick
llm_provider
model_name
temperature
prompt_version
token_budget
agent_count
world_rules_version
planner_version
weather_sync_enabled
news_sync_enabled
external_data_cutoff
observer_effect_enabled
observer_model_cooldown_seconds
admin_intervention_enabled
```

这些字段应写入 `experiment_runs.metadata_json`，其中核心字段也可以冗余为结构化列，方便筛选实验。

## 推荐分析粒度

### Agent-Day

一行表示某个 Agent 在某一天的状态摘要。

建议字段：

- `day`
- `resident_id`
- `role`
- `start_location`
- `end_location`
- `action_count`
- `move_count`
- `chat_count`
- `observe_count`
- `memory_count`
- `avg_relationship_score`
- `conflict_exposure`
- `observer_focus_count`

适合做角色比较、行为聚类、面板数据分析。

### Agent-Tick

一行表示某个 Agent 在某次 tick 中的行为。

来源：

- `world_ticks`
- `world_event_stream`
- `simulation_action_logs`
- `agent_action_plans`

建议字段：

- `tick_id`
- `world_time`
- `slot`
- `resident_id`
- `planned_action`
- `actual_action`
- `planned_location`
- `actual_location`
- `observed`
- `success`
- `decision_reason`

适合分析计划偏离、观察者效应、低成本 tick 的稳定性。

### Relationship Edge

一行表示一个有向关系。

来源：

- `relationships`
- `relationship_dynamics`

建议字段：

- `from_resident_id`
- `to_resident_id`
- `score`
- `affinity`
- `trust`
- `cooperation`
- `competition`
- `conflict`
- `updated_at`

适合做社会网络分析。注意当前关系是有向边，`A -> B` 与 `B -> A` 可能不同。

### Space-Time

一行表示某个空间在某个时间点的状态。

来源：

- `campus_spaces`
- `campus_state`
- `world_event_stream`

建议字段：

- `day`
- `slot`
- `location`
- `status`
- `crowd_percent`
- `actual_agents`
- `active_events`
- `weather`
- `campus_flow`

适合做空间使用、拥挤度、互动机会分析。

### Event Stream

一行表示一个世界事件。

来源：

- `world_event_stream`

建议字段：

- `id`
- `created_at`
- `tick_id`
- `day`
- `slot`
- `event_type`
- `resident_id`
- `location`
- `title`
- `content`
- `payload`

适合做事件序列、冲击响应、外部资讯传播分析。

## 导出接口建议

现有 API 已经能支撑页面观察，但研究导出最好提供独立接口，避免研究脚本依赖前端接口形状。

建议新增：

```text
GET /api/research/export/agent-days?from_day=1&to_day=30
GET /api/research/export/agent-ticks?from_tick=1&to_tick=1000
GET /api/research/export/relationships
GET /api/research/export/space-time?from_day=1&to_day=30
GET /api/research/export/events?after_id=0&limit=1000
GET /api/research/export/model-calls?from_date=2026-07-01&to_date=2026-07-31
```

返回格式建议支持：

```text
format=json
format=csv
```

CSV 用于社会科学常用工具，JSON 用于保留 `payload`、决策日志、记忆内容等嵌套字段。

## 研究数据导出脚本

当前 v1 已提供脚本：

```text
scripts/export_research_dataset.py
```

示例：

```bash
python scripts/export_research_dataset.py --format both --run-id pilot-001
python scripts/export_research_dataset.py --from-day 20 --to-day 30 --format csv
```

默认输出目录：

```text
exports/research/<run_id>/
```

v1 会输出：

```text
agents
agent_profiles
memories
simulation_actions
relationships
relationship_dynamics
campus_state
campus_spaces
world_ticks
events
observer_records
model_calls
action_plans
participant_actions
experiment_runs
world_snapshots
agent_day
space_time
experiment_metadata.json
data_quality_report.json
```

其中 `agent_day` 和 `space_time` 是派生研究数据集；其余文件主要来自运行时原始表。`--format both` 会同时输出 `.csv` 和 `.json`，CSV 用于常见社会科学统计工具，JSON 用于保留嵌套 payload。

`experiment_metadata.json` 应持续补充：

- git commit
- 导出时间
- 数据库类型：SQLite 或 PostgreSQL/Supabase
- world runtime 配置
- 模型预算配置
- 当前 schema 版本或迁移说明
- 实验批次和 run_id
- 数据质量检查结果摘要

第一版可以先从现有表派生，不强制数据库已经有 `run_id`。但导出结果会在 metadata 和质量报告里明确说明数据范围、当前世界状态和是否缺少实验批次标识。

## 派生研究数据集

现有日志粒度较细，适合系统调试，但不适合研究人员直接使用。研究导出层应生成面向统计分析的派生数据集。

建议优先实现：

```text
agent_tick
agent_day
agent_event_response
relationship_daily
emergent_relationships
social_interactions
social_relation_interpretations
social_beliefs
space_time
observer_attention
intervention_response
model_decision
```

其中 `emergent_relationships` 不应把“朋友”“亲密”“合作伙伴”等当作预设事实，而应从连续互动证据中生成候选解释。推荐导出字段包括：

```text
run_id
from_agent_id
to_agent_id
current_label
label_confidence
candidate_labels
evidence_count
recent_evidence
affinity
trust
cooperation
competition
conflict
tension
interaction_count
interpretation_perspective
interpretation_boundary
```

研究报告中需要明确：关系解释是“从互动证据和关系指标推断出的当前解释”，不是预设身份，也不是确定事实。这样才能研究关系如何从连续事件中生成、被感知、被误解和被改变。

例如 `agent_day` 可以整理为：

```text
run_id
agent_id
date
action_count
social_action_count
movement_count
observe_count
unique_spaces_visited
relationship_changes
events_observed
memory_count
llm_calls
token_cost
observer_focus_count
admin_intervention_exposure
```

例如 `observer_attention` 可以整理为：

```text
run_id
observer_id
session_type
focused_resident_id
focused_location
observation_start
observation_end
duration_seconds
attention_switches
observer_model_detail_count
```

例如 `intervention_response` 可以整理为：

```text
run_id
intervention_id
intervention_type
intervention_target
intervention_time
pre_snapshot_id
post_snapshot_id
affected_agents
behavior_change_score
relationship_change_score
space_flow_change_score
```

## 可复现性要求

研究数据必须能回答两个问题：

1. 某个行为为什么发生？
2. 同样条件下是否可以复查当时的输入和输出？

因此需要保留：

- `simulation_action_logs.perception`
- `simulation_action_logs.decision`
- `simulation_action_logs.execution`
- `simulation_action_logs.retrieved_memories`
- `world_event_stream.payload`
- `agent_action_plans.plan`
- `model_call_logs`

如果后续增加真实 LLM 调用详情，建议记录：

- `model`
- `trigger_type`
- `prompt_template_version`
- `input_token_estimate`
- `output_token_estimate`
- `status`
- `error_message`

不要默认保存完整敏感 prompt；如果保存，需要有研究权限和脱敏策略。

## 观察者效应记录

当前观察者会写入 `observer_sessions`，并可影响后台 tick 的 Agent 处理优先级。研究导出时应明确记录：

- 观察者 session 开始时间
- 最后活跃时间
- 关注的 Agent
- 关注的地点
- session 类型：observer、participant、admin
- 对应的 `world_event_stream` 事件

分析时需要区分：

- 自然运行事件
- 观察者触发事件
- admin 干预事件
- 模型触发事件

否则会把观察行为误判成世界自身行为。

## 干预记录

观察者行为和管理员操作不能只保存为普通文本日志。干预性研究至少需要结构化记录：

```text
observer_id
observation_start
observation_end
observation_target
attention_switches
intervention_time
intervention_target
intervention_type
intervention_content
expected_effect
```

需要区分：

- 被动观察
- 参数调整
- 信息注入
- 角色干预
- 环境干预
- 行为限制

否则后续难以判断 Agent 的变化来自自然演化，还是观察者或管理员介入。

## 决策过程可解释性

`model_call_logs` 可以记录模型调用，但科研分析还需要知道：

- 使用了哪个 prompt 版本。
- 输入了哪些上下文类型。
- 模型返回了哪些候选结果。
- 最终选择了什么行为。
- 是否经过规则系统修改。
- 是否发生降级、重试或人工干预。

不一定要永久保存完整原始 prompt，但至少应保存：

```text
prompt_version
context_hash
output_hash
decision_summary
selected_action
rule_override
model_parameters
```

完整文本可以采用分级存储、脱敏和定期清理机制。默认情况下，不应把敏感 prompt 和用户输入长期裸存。

## 数据质量检查

建议在导出前运行以下检查：

- `world_ticks.status` 是否存在长时间 running 的 tick。
- `world_tick_complete` 事件数是否与完成 tick 数一致。
- `world_event_stream.tick_id` 是否能关联到 `world_ticks.id`。
- `resident_id` 是否都能关联到 `residents.id`。
- `relationship_dynamics` 是否存在孤立边。
- `observer_sessions.last_seen_at` 是否晚于 `started_at`。
- `model_call_logs.status` 是否记录失败原因。
- 是否存在大量完全重复的 `memories`。
- 是否存在大量连续重复的 `simulation_action_logs`。
- 是否缺失 `run_id` 或实验批次标识。
- 是否有事件时间超出实验运行区间。
- 是否有模型调用没有对应决策或事件。
- 是否同一 Agent 在同一 tick 出现互斥行为。
- 是否快照状态与日志最终状态不一致。

质量检查结果应形成独立报告，而不是只打印程序日志。建议导出：

```text
data_quality_report.json
```

报告中至少包含：

- 检查项名称。
- 严重级别。
- 影响表。
- 异常记录数量。
- 示例记录 id。
- 是否阻塞导出。

## 隐私和伦理边界

当前 Agent 是虚拟角色，不是真人数据。但如果后续引入真实用户行为、真实校园数据或研究参与者输入，需要立刻增加：

- 用户同意记录。
- 数据脱敏。
- 研究导出权限。
- 数据保留期限。
- 管理员操作审计。
- 机构伦理审查说明。

不要把真实学生信息直接写入 `residents`、`memories` 或 `world_event_stream`。

## 开发路线

### v1

- 已更新研究数据文档，明确运行系统、实验系统、研究数据系统三层边界。
- 已增加 `experiment_runs`、`world_snapshots`、`research_export_jobs` schema。
- 已补研究导出脚本 `scripts/export_research_dataset.py`。
- 已输出 Agent-Day、Space-Time、Event Stream、Relationship、Model Call 等 v1 数据集。
- 已输出 `experiment_metadata.json` 和 `data_quality_report.json`。
- 已在 Supabase schema 文档中补充研究数据表。

### v2

- 增加 `/api/research/export/*`。
- 增加导出权限和 admin token 校验。
- 增加研究场景配置表，例如实验组、干预组、运行窗口。
- 将关键运行表逐步关联 `run_id`。
- 支持实验开始、干预前后、每日 checkpoint 的世界快照。
- 完善 Agent-Tick、Relationship Daily、Observer Attention、Intervention Response、Model Decision 的派生口径。

### v3

- 支持多实验并行。
- 支持随机种子和世界快照。
- 支持前后测、A/B 干预、长期追踪。
- 支持直接导出 Gephi、NetworkX、R、Stata 友好格式。
