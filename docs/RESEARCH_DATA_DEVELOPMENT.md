# 社会仿真研究数据开发文档

本文档面向项目开发者，说明如何把校园 Agent 系统作为社会科学研究数据平台来维护和扩展。重点不是解释页面功能，而是说明研究数据从哪里来、如何组织、如何导出、如何保证可复现。

## 目标

系统应支持三类研究工作：

- 观察性研究：记录世界自然运行时的 Agent 行为、空间状态、关系网络和事件流。
- 干预性研究：记录 admin 或参与者注入事件后，世界状态和 Agent 行为如何变化。
- 方法研究：比较规则执行、LLM 决策、观察者关注和模型预算对仿真结果的影响。

开发时需要保持一个原则：所有影响仿真的输入和所有关键输出都应被结构化记录。不能只把结果显示在前端。

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
| `observer_sessions` | 观察者进入、关注 Agent/地点 | 观察者效应 |
| `participant_actions` | 参与者互动预留 | v2 互动干预 |
| `model_call_logs` | 模型调用来源、状态、成本 | 成本与复杂度分析 |

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

## 导出脚本建议

如果暂时不做 API，可以先放脚本：

```text
scripts/export_research_dataset.py
```

建议输出目录：

```text
exports/research/YYYY-MM-DD-HHMM/
```

建议文件：

```text
agents.csv
agent_days.csv
agent_ticks.csv
relationships.csv
relationship_dynamics.csv
spaces.csv
space_time.csv
events.csv
observer_sessions.csv
model_call_logs.csv
metadata.json
```

`metadata.json` 必须包含：

- git commit
- 导出时间
- 数据库类型：SQLite 或 PostgreSQL/Supabase
- world runtime 配置
- 模型预算配置
- 当前 schema 版本或迁移说明

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

## 数据质量检查

建议在导出前运行以下检查：

- `world_ticks.status` 是否存在长时间 running 的 tick。
- `world_tick_complete` 事件数是否与完成 tick 数一致。
- `world_event_stream.tick_id` 是否能关联到 `world_ticks.id`。
- `resident_id` 是否都能关联到 `residents.id`。
- `relationship_dynamics` 是否存在孤立边。
- `observer_sessions.last_seen_at` 是否晚于 `started_at`。
- `model_call_logs.status` 是否记录失败原因。

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

- 补研究导出脚本。
- 固化 Agent-Day、Agent-Tick、Relationship Edge、Space-Time、Event Stream 五类数据集。
- 在 README 或运营文档中说明如何导出。

### v2

- 增加 `/api/research/export/*`。
- 增加导出权限和 admin token 校验。
- 增加研究场景配置表，例如实验组、干预组、运行窗口。

### v3

- 支持多实验并行。
- 支持随机种子和世界快照。
- 支持前后测、A/B 干预、长期追踪。
- 支持直接导出 Gephi、NetworkX、R、Stata 友好格式。
