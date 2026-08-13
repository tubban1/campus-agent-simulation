# 运维与部署

## 环境变量

`.env.example`:

```dotenv
LLM_API_KEY=你的API_KEY
LLM_API_URL=https://api.tourmaster.ch/v1beta/models/gemini-3.1-flash-lite:generateContent
DATABASE_URL=
DB_PATH=data/city.db
WORLD_RUNNER_ENABLED=true
ADMIN_TOKEN=本地_admin_token
```

变量说明：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | AI 决策必填 | LLM API Key |
| `LLM_API_URL` | AI 决策必填 | LLM generateContent 风格接口 |
| `DATABASE_URL` | 否 | 设置后使用 PostgreSQL；不设置则使用 SQLite |
| `DATABASE_SCHEMA` | 否 | PostgreSQL schema，默认 `public`；初始化、运行时与 Alembic 必须一致 |
| `DB_PATH` | 否 | SQLite 文件路径，默认 `data/city.db` |
| `WORLD_RUNNER_ENABLED` | 否 | 默认 `true`；共享数据库的本地只读实例必须设为 `false`，避免与生产 runner 重复写入 |
| `ADMIN_TOKEN` | 推荐 | World Runtime admin 接口 Bearer token；未设置时本地开发会放行并写 warning |
| `PORT` | 部署时常用 | Uvicorn 监听端口 |

## 初始化策略

当前版本只支持全新世界 bootstrap：

| 脚本 | 行为 | 适用场景 |
| --- | --- | --- |
| `python scripts/deploy_database.py` | 从空 schema 创建当前版本世界 | 本地、Supabase |
| `python scripts/reset_fresh_world.py --confirm-schema public --yes-rebuild-fresh-world` | 清空确认的 schema 后重新创建 | 只用于无保留数据的环境 |

不提供旧城市示例、旧数据库或历史 schema 的升级路径。

数据库结构升级使用以下固定顺序：

```bash
python scripts/deploy_database.py --require-postgres
```

该流程会导入仓库版本化的 `data/geo/tsinghua_main.geojson`，并把初始 Agent 直接绑定到真实
`tsinghua_main` 建筑/POI；不会创建合成校园空间。节点、道路和服务资源规模以导入输出和
`spatial_import_batches` 为准。

- `migrate_db.py` 仅接受 bootstrap 写入的全新 schema 标记，随后执行 `upgrade head`。
- `seed_spatial_foundation.py` 幂等写入校园拓扑，并为尚无空间状态的居民建立兼容初始状态。
- `seed_economy_foundation.py` 幂等建立经济主体、账户和可追溯期初余额，并执行账本对账。
- `seed_organization_runtime.py` 幂等建立组织治理档案、角色权限、初始成员责任和组织关系。
- `seed_supply_foundation.py` 幂等建立商品与服务目录、库存账户、生产配方和服务供给，并将旧库存迁移为可追溯期初流水。
- `seed_labor_runtime.py` 幂等建立组织职位、劳动合同、收入计划和周期必要支出。
- `seed_budget_runtime.py` 幂等建立居民预算档案、储蓄账户和初始预算快照；信用、透支与借款保持关闭。
- `seed_market_runtime.py` 幂等建立商品和服务的固定价、动态价或配给市场机制。
- `seed_credit_runtime.py` 幂等建立储蓄目标、风险档案、信用产品、信用额度和有来源的信用合作社准备金。
- `seed_public_policy_runtime.py` 幂等建立公共服务、政策工具和有来源的公共政策基金。
- `seed_social_institution_runtime.py` 幂等建立传播渠道、制度规则和居民权力画像。
- `seed_macro_runtime.py` 幂等建立指标口径并生成当前日宏观快照和统一核验结果。
- `audit_economy_ledger.py` 校验所有已入账交易的借贷平衡与账户余额；异常时记录审计事件并以非零状态退出。
- `--check` 只读取当前 revision；未到最新版本时以非零状态退出。
- 新增空间表及后续结构变化必须通过 Alembic migration，不再继续扩展启动时建表逻辑。

## 本地运行

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/deploy_database.py
python scripts/seed_market_runtime.py
python scripts/seed_credit_runtime.py
python scripts/seed_public_policy_runtime.py
python scripts/seed_social_institution_runtime.py
python scripts/seed_macro_runtime.py
python scripts/audit_economy_ledger.py
uvicorn app.main:app --reload
```

默认 SQLite 数据库会创建在 `data/city.db`。`data/` 目录不需要手动创建。

## World Runtime v1

World Runtime v1 让校园世界从“点击模拟一天”升级为后台 tick 驱动。普通用户默认只观察；admin 可以启动、暂停、手动推进 tick 和注入事件。

实时 tick 默认运行核心路径：计划、环境同步、事件记录和 Agent 行动。市场、韧性、经济、制度、外部同步与扩展学习必须通过独立任务处理，避免长事务阻塞世界推进。需要逐项恢复时，使用以下开关并在每次变更后完成 tick 验证：

- `WORLD_RUNTIME_EXTENDED_SUBSYSTEMS_ENABLED=true`：恢复扩展后处理。
- `WORLD_RUNTIME_MARKET_TICK_ENABLED=true`：在扩展后处理已启用时恢复市场运行。
- `WORLD_RUNTIME_RESILIENCE_TICK_ENABLED=true`：在扩展后处理已启用时恢复韧性与冲击运行。

常用接口：

- `GET /api/world/runtime`：运行状态、世界时间、最新 tick、模型预算。
- `GET /api/spatial/scene`：米制空间节点与可通行连接。
- `GET /api/spatial/agents`：全部居民当前坐标、路线、进度和移动能力。
- `GET /api/spatial/resources`：空间席位、窗口、服务能力与当前可用量。
- `GET /api/spatial/admission-queue`：当前 FIFO 等待队列、位次、耐心和预计等待时间。
- `GET /api/supply/catalog`：商品、服务和生产资料目录。
- `GET /api/supply/inventory`：按主体与商品查询真实库存账户。
- `GET /api/supply/production-batches`：生产批次、状态和预计完成时间。
- `GET /api/supply/services`、`/api/supply/service-deliveries`：服务容量与交付/排队结果。
- `GET /api/labor/positions`、`/contracts`、`/shifts`：职位、合同和有行动证据的班次结算。
- `GET /api/labor/income-programs`、`/payments`、`/expense-obligations`：持续收入、支付来源和必要支出。
- `GET /api/labor/distribution`：基于账本余额与收入流水的不平等摘要。
- `GET /api/budgets/residents/{id}`：居民当前预算、可支配资金、每日时间预算和信用关闭状态。
- `GET /api/budgets/residents/{id}/snapshots`、`/savings-transfers`、`/choices`：预算历史、储蓄流水和行动机会成本。
- `GET /api/market/mechanisms`、`/prices`：市场规则及带供需、库存、成本解释的小时价格。
- `GET /api/market/demand`、`/frictions`：需求响应、替代选择、缺货、配给和摩擦成本。
- `GET /api/market/quote?item_name=奶茶&location=商业街&resident_id=1`：商品报价或居民支付意愿评估。
- `GET /api/credit/products`、`/profiles`、`/contracts`、`/installments`、`/payments`：信用规则、额度、债务合同和还款事实。
- `GET /api/credit/savings-goals`、`/risk-profiles`、`/shocks`、`/risk-claims`：储蓄缓冲、风险暴露、经济冲击和共济赔付。
- `GET /api/credit/events`：放款、计息、还款、逾期、违约与额度变化历史。
- `GET /api/public-policy/services`、`/operations`、`/usages`：公共服务定义、每日财政运行与居民使用结果。
- `GET /api/public-policy/externalities`、`/exposures`：外部性来源、范围和逐居民暴露证据。
- `GET /api/public-policy/policies`、`/benefits`、`/outcomes`：政策规则、财政受益和按群体聚合的结果。
- `GET /api/social-institutions/claims`、`/transmissions`、`/exposures`、`/beliefs`：信息主张、传播路径、暴露和居民信念。
- `GET /api/social-institutions/rules`、`/cases`、`/decisions`：制度规则、案件、奖惩和申诉决定。
- `GET /api/social-institutions/power`、`/trust-events`：正式权力、非正式影响和制度信任证据。
- `GET /api/macro/definitions`、`/snapshots`、`/snapshots/latest`：宏观指标口径、历史窗口和最新聚合结果。
- `GET /api/macro/snapshots/{id}`：快照内全部总体、角色与收入组指标及统一核验结果。
- `GET /api/macro/values/{id}/components`：从宏观值下钻到底层账户、交易、服务或事件组成。
- `POST /api/macro/snapshots?window_type=manual`：创建可复现的人工宏观检查点。
- `GET /api/body-states`：全部居民当前身体、注意力与恢复状态，以及达到阈值的告警。
- `GET /api/agents/{id}/body-state`：单个居民的完整身体状态。
- `GET /api/agents/{id}/perception-evidence`：单个居民最近的局部观察、信念、空间记忆和实际接收消息。
- `GET /api/perception/observations`：按 Agent 或 tick 查询局部观察研究证据。
- `GET /api/agents/{id}/trajectory`：按实验运行和 tick 查询不可变移动轨迹。
- `POST /api/agents/{id}/movement/plan`：预览当前环境下的最低成本路线。
- `POST /api/agents/{id}/movement/pause`、`resume`：暂停或恢复在途移动。
- `GET /api/world/events?after_id=0&limit=20`：统一实时事件流。
- `POST /api/world/observer-sessions`：记录观察者关注的 Agent 或地点。
- `POST /api/admin/world/start`：启动后台运行。
- `POST /api/admin/world/pause`：暂停后台运行。
- `POST /api/admin/world/tick`：手动推进一个 tick。
- `POST /api/admin/events/trigger`：注入 admin 世界事件。

启动后台运行：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/world/start \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

手动推进一个 tick：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/world/tick \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

前端 admin 控件依赖浏览器本地 token：

```js
localStorage.setItem("ADMIN_TOKEN", "你的_ADMIN_TOKEN")
```

刷新页面后会显示启动、暂停和推进 tick 控制；不再存在独立“模拟一天”写入入口。

v1 的 8 小时行动计划优先使用 `llm-planner-v1`，写入 `agent_action_plans`，每次自动模型调用都会进入 `model_call_logs` 并消耗 `daily_auto_model_budget`。v2 默认每日自动预算为 500 次；预算耗尽或模型失败时会自动降级为规则计划、规则等待或规则观察，世界运行不会被阻塞。`model_call_logs` 只记录真实成功、失败或预算耗尽结果，不把预算预占记作一次模型调用。

未配置 LLM 时，runtime 使用 `rule-unconfigured-v1`，不会扣减自动模型预算。可使用以下只读接口检查多尺度环境更新：

```bash
curl http://127.0.0.1:8000/api/world/update-schedules
curl http://127.0.0.1:8000/api/world/update-runs
```

默认调度包括每小时空间活动、每 8 小时社会动态、每日制度与公共资源汇总。更新记录保存输入事件游标、规则版本、聚合指标和来源事件血缘；宏观指标来自底层状态与事件，不由日报或 LLM 文案生成。

每个计划窗口开始前，运行时还会确保 Agent 的五层目标链存在，并进行到期复盘。`agent_action_plans.plan_json.goal_chain` 保存长期、中期、短期目标和当前承诺；到点步骤执行后，结果进入 `plan_outcomes`，随后更新 `agent_goals`、`goal_revisions` 和 `trajectory_episodes`。这部分使用规则运行，不额外消耗模型预算。

真实天气和外部世界资讯由 world tick 每小时自动同步一次。天气会更新 `campus_state` 并写入 `real_weather_auto_sync` / `real_weather_auto_sync_failed` 事件；资讯会写入 `external_information`、`agent_information` 和 `external_information_auto_sync` / `external_information_auto_sync_failed` 事件。前端不再提供手动同步按钮。

校园新闻由 world tick 在每个已完成的 8 小时窗口后自动尝试发布一次。系统从上一窗口的 `agent_tick` 事件里抽取当天还没有发布过校园新闻的 Agent，最多生成 3 条快讯，写入 `agent_news_posts`，并保存 `source_slot`、来源事件和新闻价值；随后在 `world_event_stream` 写入 `campus_news_published`。如果窗口内没有新素材或预算耗尽，会写入 `campus_news_skipped`，世界运行继续。

出版口径是“每日一期、分时更新”：8 小时窗口生成的是快讯，不是新的报纸期号；前端把同一天的快讯汇编为一份日报。当天显示“今日滚动版”，过往日期显示“归档日报”，上一期和下一期始终按有内容的日期切换。

v3 真实感规则由 `campus_schedule_rules` 和 `world_causal_weights` 驱动。前者定义角色、动作、地点、时间段和随机噪声，例如上课、用餐、排队、夜间休息、社团活动；后者定义天气、考试压力、活动热度、资源压力和人流如何影响地点/动作权重。自主循环支持 `attend_class`、`queue`、`consume`、`rest`、`club_activity`、`conflict`、`collaborate`、`late`、`request_leave` 等动作。

身体状态由 world tick 按实际经过时间推进。饥饿、疲劳、睡眠债、压力、注意力、社交能量、健康和天气暴露会受到位置、移动、等待、天气和行动影响；吃饭、休息和反思分别恢复不同状态。高疲劳、极度饥饿、低健康或低注意力会形成结构化行动拒绝原因，疲劳、饥饿和健康也会降低路线规划与移动 tick 使用的实际步速。`agent_profiles.energy` 仅保留为兼容摘要，身体真值以 `agent_body_states` 为准。

有限可观测性由三层数据组成：`agent_observations` 保存不可变局部证据，`agent_belief_states` 保存 Agent 当前解释，`agent_spatial_memories` 保存带地点的主观经历。视觉、听觉和亲历证据由坐标、感知半径和来源事件计算；未进入感知范围且未被定向传递的消息不会出现在 Agent 决策上下文。观察者聚焦只生成界面侧 `observer_model_detail`，不会提高行动扰动概率、写入 Agent 记忆或向 Agent 暴露“正在被观察”。

研究校准可以先手动录入观测值，再生成偏差报告：

```bash
curl -X POST http://127.0.0.1:8000/api/research/calibration-observations \
  -H "Content-Type: application/json" \
  -d '{"metric_name":"library_crowd","metric_value":72,"source_name":"survey","location":"图书馆","sample_size":30}'

curl http://127.0.0.1:8000/api/research/calibration-report
```

观察者聚焦 Agent 时可能触发 `observer_model_detail`，但同一个 Agent 默认 5 分钟内最多触发一次观察者模型细节，避免单个观察者持续停留导致模型调用过密。前端会在 HUD、事件流和 Agent 气泡中标记“观察者触发”。

高频 world tick 会继续写 `simulation_action_logs` 作为完整审计记录，但普通 `world_tick` 观察记忆会按同一 Agent、同一天、同一内容去重写入，避免个人经历被重复观察刷屏。

前端行动时间线会把连续相同的原始行动日志聚合为“持续 N 次 tick”，保留底层审计数据完整性的同时，避免用户在个人资料里看到大量重复行动。

## 重置本地世界

确认需要丢弃当前模拟进度后运行：

```bash
python scripts/seed_fresh_residents.py
```

该脚本会删除并重建 residents、agent_profiles、relationships、memories、inventory、policies、transactions、city_events、campus_state 等核心数据。

## PostgreSQL

设置 `DATABASE_URL` 后，`app/db` 兼容连接层会使用 `psycopg`，Alembic 和新增空间模块使用 SQLAlchemy 的 psycopg 方言。

示例：

```dotenv
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

如果使用 Supabase，可以直接执行 [`supabase_schema.sql`](supabase_schema.sql) 建表；完整流程见 [`SUPABASE.md`](SUPABASE.md)。

Supabase 的 Direct Connection、Session Pooler 和 Transaction Pooler 是不同的完整连接串。
不能只把 Direct Connection 的端口手动改为 `6543`；使用 pooler 时必须从 Dashboard 的
**Connect** 面板复制完整 URI，因为主机名和用户名也不同。应用运行时优先使用 Transaction
Pooler，`pg_dump`、恢复和需要 session 语义的迁移使用 Session Pooler 或 Direct Connection。

已有 Supabase 项目升级到 World Runtime v1 时，重新执行最新的 [`supabase_schema.sql`](supabase_schema.sql) 即可。所有新增表都使用 `create table if not exists` 和 `create index if not exists`，不会清空已有数据。

项目内的 PostgreSQL 兼容层会处理：

- `?` 参数替换为 `%s`
- `INSERT OR IGNORE` 转为 `ON CONFLICT DO NOTHING`
- `simulation_state` 的 `INSERT OR REPLACE` 转为 upsert
- `PRAGMA table_info(...)` 转为查询 `information_schema.columns`
- `INTEGER PRIMARY KEY AUTOINCREMENT` 转为 `SERIAL PRIMARY KEY`

注意：兼容层覆盖的是当前项目已用 SQL 写法，不等同于完整 SQLite 方言转换器。新增复杂 SQL 时请同时在 SQLite 和 PostgreSQL 下验证。

## Docker

构建：

```bash
docker build -t campus-agent-simulation .
```

运行：

```bash
docker run --rm -p 8000:8000 \
  -e LLM_API_KEY=你的API_KEY \
  -e LLM_API_URL=你的模型接口 \
  campus-agent-simulation
```

当前 Dockerfile 在 build 阶段执行：

```bash
python scripts/deploy_database.py
python scripts/seed_supply_foundation.py
python scripts/seed_labor_runtime.py
python scripts/seed_budget_runtime.py
python scripts/seed_market_runtime.py
python scripts/seed_credit_runtime.py
python scripts/seed_public_policy_runtime.py
python scripts/seed_social_institution_runtime.py
python scripts/seed_macro_runtime.py
python scripts/audit_economy_ledger.py
```

这会生成一个全新的校园世界并升级到最新 migration。持久化 PostgreSQL 部署应使用 Render 的安全初始化流程，避免重置线上数据。

## Render

`render.yaml` 当前配置：

```yaml
buildCommand: pip install -r requirements.txt && python scripts/deploy_database.py --require-postgres
startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

`deploy_database.py` 会拒绝缺少 `DATABASE_URL` 的 Render 构建，避免迁移意外落入临时 SQLite。PostgreSQL 部署使用 advisory lock 串行化初始化和迁移；完成全部幂等种子后再次检查 Alembic revision。Web 启动命令只启动 Uvicorn，不执行 DDL。

使用 Docker 部署时，镜像构建只会在 `/tmp/campus-build/city.db` 上验证完整初始化链路，不连接 Render 持久盘或生产数据库。容器启动命令也不会修改表结构；持久化 PostgreSQL 必须在发布镜像前单独运行 `python scripts/deploy_database.py --require-postgres`。

安全初始化会保留已有校园数据，随后补齐基线结构、执行 migration，并幂等补齐空间拓扑、经济账本、组织治理档案、供给目录和劳动收入制度。账本审计通过后服务才会启动。首次部署会写入种子数据，后续构建只执行幂等结构升级、缺失空间状态回填、期初账户补录、组织角色补齐、旧库存来源化和缺失劳动合同补齐。

需要配置的环境变量：

- `LLM_API_KEY`
- `LLM_API_URL`
- `DATABASE_URL`，如果使用 Render PostgreSQL

## 外部网络依赖

以下功能依赖外网：

- `/api/campus/environment/sync-real-weather`：Open-Meteo，失败后尝试 Met.no fallback。
- `/api/external-information/sync`：Google News RSS / Bing News RSS。
- 所有 LLM 决策和 AI 日报接口：`LLM_API_URL`。

如果外部天气失败，`auto_update_environment()` 会 fallback 到模拟天气。外部资讯同步失败时接口返回 502。

## 数据表分组

基础世界：

- `residents`
- `agent_profiles`
- `inventory`
- `transactions`
- `relationships`
- `policies`
- `city_events`
- `memories`
- `simulation_state`

校园环境：

- `campus_state`
- `campus_spaces`
- `campus_events`

学习、社交与目标：

- `agent_learning`
- `relationship_dynamics`
- `long_term_goals`
- `group_goals`
- `collaborations`
- `competitions`
- `campus_organizations`
- `organization_members`
- `simulation_action_logs`

日报与资讯：

- `agent_news_posts`
- `external_information`
- `agent_information`

World Runtime：

- `world_runtime`
- `world_ticks`
- `world_event_stream`
- `agent_action_plans`
- `observer_sessions`
- `participant_actions`
- `model_call_logs`
- `world_update_schedules`
- `world_update_runs`

Research Data：

- `experiment_runs`
- `world_snapshots`
- `world_branches`
- `research_export_jobs`

## 导出研究数据

第一版研究导出使用脚本生成 CSV/JSON 数据包：

```bash
python scripts/export_research_dataset.py --format both --run-id pilot-001
```

可按仿真日过滤：

```bash
python scripts/export_research_dataset.py --from-day 20 --to-day 30 --format csv
```

默认输出到 `exports/research/<run_id>/`，包含原始运行表、派生的 `agent_day` / `space_time`，以及 `experiment_metadata.json` 和 `data_quality_report.json`。如果 `.env` 配置了 `DATABASE_URL`，脚本会导出 Supabase/Postgres 数据；否则导出本地 SQLite 数据。

## 常见问题

### `RuntimeError: 缺少 LLM_API_KEY`

`.env` 没有配置 `LLM_API_KEY`。状态查询和手动动作仍可用，但 AI 决策、AI 日报和日记生成需要 LLM。

### 前端显示连接失败

确认后端服务在运行：

```bash
curl http://127.0.0.1:8000/api/state
```

如果 `/api/state` 报数据库表不存在，按空库重建：

```bash
python scripts/reset_fresh_world.py --confirm-schema public --yes-rebuild-fresh-world
```

### Agent 行动失败

常见原因：

- 精力不足。
- 今日时间预算不足。
- 目标空间关闭、维护中、暂停开放或满员。
- 交易时买方余额不足或卖方库存不足。
- LLM 返回了不符合格式的 JSON。

失败会写入 `city_events`、`memories` 和 `simulation_action_logs`，并消耗失败动作成本。

### PostgreSQL 下某个接口事务异常

PostgreSQL 在单条语句失败后会让当前事务进入 aborted 状态。项目中关键执行路径已经在失败时 `rollback()`，但新增代码如果捕获异常后继续写数据库，也需要先回滚。
