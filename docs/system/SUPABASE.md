# Supabase 数据库接入指南

项目提供了一份完整且包含空间基础设施的 Supabase/PostgreSQL 结构脚本：

- [`docs/supabase_schema.sql`](supabase_schema.sql)

该 SQL 覆盖当前系统所需的全部表结构（核心模拟表 + 3D 空间网格表 `spatial_nodes` / `spatial_edges` / `spatial_affordances` 等），并自动写入初始空间状态与系统配置。

## 部署流程

### 步骤一：Supabase SQL Editor 建表
1. 打开 Supabase Console，进入你的项目。
2. 打开左侧 `SQL Editor`。
3. 复制并粘贴 [`docs/supabase_schema.sql`](supabase_schema.sql) 全文并运行 (`Run`)。

### 步骤二：配置本地 `.env`
在项目根目录 `.env` 中配置 Supabase 连接串（若使用 Transaction Pooler 6543 端口）：

```dotenv
DATABASE_URL=postgresql://postgres.[REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
```

### 步骤三：运行一键初始化与种子数据注入
运行数据库部署集成脚本，自动完成 Schema 校验、初始化与种子数据（含 Agent、经济账本、空间实体）填充：

```bash
python scripts/deploy_database.py
```

如需载入清华大学真实校园空间网格，运行：

```bash
python scripts/fetch_tsinghua_geojson.py
```

---

## 常见问题诊断 (Troubleshooting)

### 1. 前端显示“正在读取世界状态”或“读取中”卡住
* **原因**：Supabase 数据库缺少 `spatial_nodes` / `spatial_edges` 等空间表，或数据尚未初始化。
* **解决**：在 Supabase SQL Editor 中重新执行 [`docs/supabase_schema.sql`](supabase_schema.sql)，并运行 `python scripts/deploy_database.py`。

### 2. PgBouncer 预编译语句错误 (`prepared statement does not exist`)
* **原因**：Supabase Pooler Transaction 模式 (6543) 不支持服务端预编译语句。
* **解决**：系统内部 `app/db/engine.py` 已自动配置 `prepare_threshold=None` 以兼容 Supabase 连池。

---

## 包含的基础与空间表清单

* 核心模拟表：`residents`, `agent_profiles`, `inventory`, `transactions`, `relationships`, `policies`, `city_events`, `memories`, `simulation_state`, `agent_learning`, `collaborations`, `competitions`, `campus_state`, `campus_spaces`, `campus_events`, `agent_news_posts`, `external_information`, `agent_information`, `relationship_dynamics`, `long_term_goals`, `group_goals`, `campus_organizations`, `organization_members`, `simulation_action_logs`, `world_runtime`, `world_ticks`, `world_event_stream`, `agent_action_plans`
* 3D 空间与真实世界表：`spatial_nodes`, `spatial_edges`, `agent_spatial_capabilities`, `agent_spatial_states`, `agent_trajectories`, `spatial_resources`, `spatial_admission_queue`, `spatial_import_batches`, `spatial_affordances`
