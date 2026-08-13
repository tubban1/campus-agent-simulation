# API 速查

默认服务地址：`http://127.0.0.1:8000`

完整交互式文档可打开 `/docs`。本文只记录维护和调试最常用的接口。

## 页面与健康检查

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/` | 返回静态前端页面 |
| GET | `/api/ai/test` | 调用一次 LLM，验证 `LLM_API_KEY` 和 `LLM_API_URL` |

## 世界状态

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 世界总快照：天数、环境、空间、Agent、事件、六模块状态 |
| GET | `/api/world/runtime` | 世界运行器状态、世界时间、最新 tick、模型预算 |
| GET | `/api/world/events?after_id=0&limit=50` | 统一实时事件流，支持按 id 增量读取 |
| GET | `/api/world/environment-config` | 当前生效的完整环境配置、版本和校验和 |
| GET | `/api/world/environment-configs` | 环境配置版本列表 |
| GET | `/api/world/snapshots` | 世界快照元数据列表，不返回完整状态 |
| GET | `/api/world/snapshots/{snapshot_id}?include_state=true` | 快照详情；按需返回完整客观状态 |
| GET | `/api/world/action-rules` | 当前 13 种 autonomous action 的前置条件、成本和效果规则 |
| GET | `/api/world/action-executions?resident_id=1&status=completed` | 查询结构化行动结算记录 |
| GET | `/api/world/delayed-effects?status=pending` | 查询待结算或已结算的延迟效果 |
| POST | `/api/world/observer-sessions` | 创建或更新观察者会话，记录关注 Agent/地点 |
| GET | `/api/agents` | Agent 列表 |
| GET | `/api/residents` | 同 `/api/agents` |
| GET | `/api/agents/modules` | 所有 Agent 六模块状态 |
| GET | `/api/agents/{resident_id}/modules` | 单个 Agent 六模块状态 |
| GET | `/api/inventory` | 全部库存 |

观察者会话示例：

```bash
curl -X POST http://127.0.0.1:8000/api/world/observer-sessions \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"browser-observer","focused_resident_id":1}'
```

增量事件流示例：

```bash
curl 'http://127.0.0.1:8000/api/world/events?after_id=0&limit=20'
```

## World Runtime Admin

Admin 接口使用 `.env` 中的 `ADMIN_TOKEN`。请求头格式：

```text
Authorization: Bearer 你的_ADMIN_TOKEN
```

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/admin/world/start` | 启动后台 world runner |
| POST | `/api/admin/world/pause` | 暂停后台 world runner |
| POST | `/api/admin/world/tick` | 手动推进一个 tick |
| POST | `/api/admin/world/environment-configs` | 创建环境配置版本，可选择立即激活 |
| POST | `/api/admin/world/environment-configs/{config_id}/activate` | 激活配置并应用空间与环境基线 |
| POST | `/api/admin/world/snapshots` | 创建带配置、随机种子和事件游标的世界快照 |
| POST | `/api/admin/world/snapshots/{snapshot_id}/restore` | 校验并恢复快照，默认先创建自动备份 |
| GET | `/api/world/branches` | 查询世界分支及其 base/head 快照 |
| POST | `/api/admin/world/branches` | 从可恢复快照创建隔离分支 |
| POST | `/api/admin/world/branches/{branch_key}/switch` | 封存当前分支并切换到目标分支 |
| POST | `/api/admin/events/trigger` | 注入 admin 世界事件，可选影响校园空间 |

手动 tick 示例：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/world/tick \
  -H 'Authorization: Bearer 你的_ADMIN_TOKEN'
```

创建快照示例：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/world/snapshots \
  -H 'Authorization: Bearer 你的_ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"政策实验前","branch_key":"control","external_data_version":"snapshot-20260729"}'
```

恢复和切换分支前必须先暂停 runtime。分支示例：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/world/branches \
  -H 'Authorization: Bearer 你的_ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"branch_key":"treatment-a","name":"处理组 A","source_snapshot_id":1}'

curl -X POST http://127.0.0.1:8000/api/admin/world/branches/treatment-a/switch \
  -H 'Authorization: Bearer 你的_ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"开始处理组实验"}'
```

快照恢复不会删除 `world_event_stream` 审计历史。事件带有 `branch_key`，`GET /api/world/events` 默认只返回当前活动分支，也可显式传入 `branch_key` 查询其他分支。

环境配置至少包含 `campus`、`spaces`、`population`、`institutions`、`economy` 和 `external_context`。当前版本的 `spaces` 必须覆盖现有七个 runtime 地点；激活时会更新容量、开放时间与状态，并应用 `environment_baseline` 中允许修改的字段。自定义地点、人口和组织生成仍属于后续阶段。

行动结算分为两种模式：

- `active`：计划步骤到点，检查空间与资源前置条件，结算精力、时间、金钱、成功概率和直接/延迟效果。
- `passive`：等待计划或窗口已完成时的 runtime 轮询，只保留观察记录，不消耗每日资源或产生宏观效果。

规则拒绝与概率失败属于有效世界结果，不会被计为 tick 系统错误。其 `failure_code`、前置条件、结算前后资源和来源事件可通过 `action-executions` 查询。

注入事件示例：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/events/trigger \
  -H 'Authorization: Bearer 你的_ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"title":"临时社团展示","event_type":"大型活动","target_spaces":["操场"],"intensity":65}'
```

## 校园环境与空间

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/campus/environment/today` | 今日校园环境 |
| POST | `/api/campus/environment/set` | 手动更新今日环境字段 |
| POST | `/api/campus/environment/sync-real-time` | 根据系统时间更新学期、时段和人流 |
| POST | `/api/campus/environment/sync-real-weather` | 兼容/调试入口：立即调用天气 API 并更新校园环境。主流程由 world tick 每小时自动同步 |
| GET | `/api/campus/spaces` | 校园空间快照 |
| POST | `/api/campus/spaces/{location}/status` | 手动调整空间状态 |
| POST | `/api/campus/events/trigger` | 触发校园事件 |
| POST | `/api/campus/events/{event_id}/resolve` | 结束校园事件 |

示例：设置环境。

```bash
curl -X POST http://127.0.0.1:8000/api/campus/environment/set \
  -H 'Content-Type: application/json' \
  -d '{"weather":"小雨","rainfall":45,"exam_pressure":70,"campus_mood":"紧张"}'
```

示例：触发空间事件。

```bash
curl -X POST http://127.0.0.1:8000/api/campus/events/trigger \
  -H 'Content-Type: application/json' \
  -d '{"title":"图书馆设备检修","event_type":"设施故障","intensity":60,"target_spaces":["图书馆"]}'
```

## 手动行动工具

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/tools/move` | 移动 Agent |
| POST | `/api/tools/chat` | 让两个 Agent 聊天 |
| POST | `/api/tools/buy-sell` | 完成交易 |
| POST | `/api/tools/submit-policy` | 提交校园政策 |
| POST | `/api/tools/vote-policy` | 对政策投票 |
| POST | `/api/tools/close-policy/{policy_id}` | 结算政策 |
| POST | `/api/tools/daily-reflect` | 让所有 Agent 写一句当天总结 |

示例：移动。

```bash
curl -X POST http://127.0.0.1:8000/api/tools/move \
  -H 'Content-Type: application/json' \
  -d '{"resident_id":1,"destination":"图书馆"}'
```

示例：聊天。

```bash
curl -X POST http://127.0.0.1:8000/api/tools/chat \
  -H 'Content-Type: application/json' \
  -d '{"speaker_id":1,"listener_id":11,"message":"今天一起去社团招新看看吗？"}'
```

示例：交易。

```bash
curl -X POST http://127.0.0.1:8000/api/tools/buy-sell \
  -H 'Content-Type: application/json' \
  -d '{"buyer_id":1,"seller_id":5,"item_name":"套餐饭","quantity":1,"unit_price":12}'
```

## 自主模拟

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/agent/decide/{resident_id}` | 只生成单个 Agent 决策 |
| POST | `/api/agent/act/{resident_id}` | 决策并执行单个 Agent 行动 |
| POST | `/api/agent/act-all` | 所有 Agent 轮流决策并执行 |

世界时间只通过 `/api/admin/world/tick` 或后台 runner 推进；不再提供会绕过真实地理和物理结算的批量模拟入口。

## 社交、目标与组织

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/social/hierarchy` | 按层级查看 Agent |
| POST | `/api/social/communicate` | 社交沟通，底层等价于聊天并记录学习 |
| POST | `/api/social/negotiate` | 协商 |
| POST | `/api/social/collaborate` | 发起协作 |
| POST | `/api/social/compete` | 发起竞争 |
| GET | `/api/social/relationships/{resident_id}` | 关系列表与关系动态 |
| GET | `/api/agents/{resident_id}/social-graph` | 前端人物页使用的关系图 |
| GET | `/api/agents/{resident_id}/learning` | 学习记录 |
| GET | `/api/agents/{resident_id}/long-term-goals` | 长期目标 |
| GET | `/api/agents/{resident_id}/goal-system` | 五层目标树、承诺、计划结果与行为轨迹 |
| GET | `/api/agents/{resident_id}/profile-activity` | 人物详情首屏聚合：关系网络、行动时间线和最新决策 |
| POST | `/api/goals` | 创建长期目标 |
| GET | `/api/organizations` | 校园组织 |
| GET | `/api/groups` | 群体目标 |
| POST | `/api/groups` | 创建群体目标 |

## 记忆、时间线与日志

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/agents/{resident_id}/memories/relevant?query=图书馆,考试` | 按相关性检索个人记忆 |
| GET | `/api/agents/{resident_id}/timeline?limit=30` | 简化行动时间线 |
| GET | `/api/agents/{resident_id}/simulation-logs?limit=12` | 完整感知、记忆、决策、执行和反馈日志 |

## Agent 生命历程观察台

这些接口只读，不调用 LLM，也不会改变世界状态。返回的事件均带有 `evidence.source` 与 `evidence.id`；`research_boundaries.causal_links_available` 始终为 `false`，表示时间先后不等于因果关系。

| GET | `/api/agents/{resident_id}/life-course/overview?from_day=1&to_day=30&limit=240` | 当前状态、经历时间线、转折点、关系与群体的综合视图 |
| GET | `/api/agents/{resident_id}/life-course/events` | 带感知、检索记忆、决策、执行、环境反馈及前后状态快照的事件 |
| GET | `/api/agents/{resident_id}/life-course/turning-points?limit=12` | 按透明规则评分的高重要性事件 |
| GET | `/api/agents/{resident_id}/life-course/relationships` | 当前关系与已记录的关系变化历史 |
| GET | `/api/agents/{resident_id}/life-course/groups` | 当前群体归属与关联证据（成员历史尚待运行数据积累） |

## 日报与外部资讯

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/newspaper/today` | 当日事件和 Agent 状态数据 |
| GET | `/api/newspaper/agent-posts?day=2` | Agent 自主投稿 / runtime 分时快讯；默认当前天，返回每日一期的 `edition`、每条快讯的 `source_slot`，以及 `available_days`、`previous_day`、`next_day` 便于回看历史日报 |
| GET | `/api/newspaper/ai-today` | 调用 LLM 生成校园日报 |
| POST | `/api/agents/daily-diaries/backfill` | 为指定日期补写 Agent 日记 |
| POST | `/api/external-information/sync` | 兼容/调试入口：立即从固定 RSS 源同步外部资讯。主流程由 world tick 每小时自动同步 |
| GET | `/api/external-information` | 查看已同步资讯 |

## 常见请求体

`CampusEnvironmentRequest` 支持的字段来自 `DEFAULT_ENV`，包括 `weather`、`semester_stage`、`time_slot`、`weekday`、`temperature`、`rainfall`、`exam_pressure`、`assignment_pressure`、`study_atmosphere`、`activity_heat`、各空间 crowd、`traffic_status`、`network_status`、`safety_level`、`resource_pressure`、`campus_mood`、`consumption_index` 等。

`CampusEventRequest`：

```json
{
  "title": "校园主题活动",
  "event_type": "大型活动",
  "intensity": 60,
  "target_spaces": ["操场", "教学楼"],
  "effects": {}
}
```

`GroupGoalRequest`：

```json
{
  "name": "图书馆复习互助组",
  "group_type": "学习小组",
  "leader_id": 2,
  "member_ids": [13, 16],
  "shared_goal": "一起完成期末复习计划",
  "current_plan": "每天晚间同步复习进度"
}
```
