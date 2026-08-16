# 阶段 1A：空间真值层详细技术设计

## 1. 文档状态

- 统一阶段：`ENVIRONMENT_REALISM_ROADMAP.md` 阶段 1A。
- 空间子路线：`docs/spatial-intelligence-roadmap.md` 阶段 1A。
- 前置状态：统一阶段 0 已完成，现有 environment config、因果执行、事件谱系、多尺度更新、世界快照和分支能力直接复用。
- 目标分支：后续实现分别使用 `refactor/...`、`feat/...` 和 `fix/...`。
- 本文定位：空间真值层的 schema、迁移、服务边界、兼容策略和验收设计。
- 阶段边界：本阶段先建立空间事实，不提前实现路径规划、排队或局部感知。
- 实施状态：阶段 1A-1F 已完成开发与本地验证。数据库迁移、空间真值、连续移动、空间资源与准入、身体状态、有限可观测性、空间记忆、个体能力与机会差异和快照恢复已在同一阶段分支内落地；下一步进入阶段 2。

## 2. 阶段 1A 目标

阶段 1A 建立唯一的空间真值与证据底座，使后续路径规划、连续移动、空间准入、有限可观测性和空间实验可以在稳定边界内开发。

本阶段交付：

- 可同时运行于本地 SQLite 和线上 PostgreSQL 的统一数据访问基础。
- 正式数据库 migration 与回滚流程。
- 空间节点、空间连接、Agent 空间能力、当前空间状态和不可变轨迹模型。
- 复用现有 `experiment_runs`、`world_snapshots`、`world_branches` 和 runtime 随机种子，不创建第二套实验系统。
- 从 `app/main.py` 中拆出的空间服务边界。
- 可查询的校园场景图 API。
- 空间真值的快照、恢复和分支隔离。
- 可重复的空间基础测试。

本阶段不交付：

- LLM 空间决策 Prompt。
- A* 路径规划。
- 连续移动动画。
- 视野、遮挡和局部感知。
- 排队、碰撞与群体动力学。
- Marble、Unity、Unreal Engine 或 Isaac Sim 集成。

## 3. 关键架构决策

### 3.1 数据库策略

环境约定：

```text
本地开发与单元测试：SQLite
线上 Render 与正式实验：PostgreSQL
```

两种数据库必须使用：

- 同一套数据模型。
- 同一组 migration。
- 同一套 repository 接口。
- 同一套业务规则。
- 同一套 API 契约。

不允许针对 SQLite 和 PostgreSQL 维护两套业务实现。

### 3.2 数据访问技术

新增空间模块采用：

```text
SQLAlchemy 2 Core
+ Alembic
+ SQLite
+ PostgreSQL / psycopg 3
```

选择 SQLAlchemy Core 而不是立即全面使用 ORM，原因是：

- 当前项目大量使用原始 SQL 和字典行。
- Core 更容易与现有查询方式渐进衔接。
- 可以统一参数绑定、事务、类型和方言处理。
- 不要求一次重写现有 Agent、记忆和关系逻辑。

现有 `app/db.py` 暂时保留。后续通过独立重构 PR 将连接创建迁移到统一 Engine；在迁移完成前，不在同一业务事务中混用两套连接。

### 3.3 JSON 与时间

阶段 1A 采用可移植类型：

- JSON 数据使用 SQLAlchemy `JSON` 类型，由方言映射到 SQLite JSON 文本和 PostgreSQL JSON。
- 暂不依赖 PostgreSQL `JSONB` 查询能力。
- 时间统一以 UTC 写入数据库。
- API 输出使用 ISO 8601 格式。
- 世界内部顺序使用 `experiment_run_id + tick_number`，不依赖服务器时间排序。

### 3.4 坐标系统

阶段 1A 使用右手坐标系：

- `x`：校园东西方向。
- `y`：高度，阶段 1A 通常为 `0`。
- `z`：校园南北方向。
- 距离单位：米。
- 时间单位：模拟分钟和 tick。

Three.js 直接使用相同坐标，不在前端维护第二套空间位置。

## 4. 目标模块边界

```text
app/
├── db/
│   ├── engine.py
│   ├── metadata.py
│   └── migrations/
├── spatial/
│   ├── models.py
│   ├── repository.py
│   ├── scene_graph.py
│   ├── service.py
│   └── schemas.py
├── experiments/
│   ├── models.py
│   ├── repository.py
│   └── service.py
└── api/
    ├── spatial.py
    └── experiments.py
```

职责划分：

| 模块 | 职责 |
| --- | --- |
| `db/engine.py` | 根据环境创建 SQLite 或 PostgreSQL Engine |
| `db/metadata.py` | 统一 SQLAlchemy Metadata 与命名约定 |
| `spatial/models.py` | 空间相关 Table 定义 |
| `spatial/repository.py` | 数据查询与持久化，不包含业务决策 |
| `spatial/scene_graph.py` | 将节点和连接组装为内存场景图 |
| `spatial/service.py` | 空间状态、占用和场景快照业务接口 |
| `spatial/schemas.py` | Pydantic API 输入输出模型 |
| `experiments/*` | 实验批次、随机种子和运行元数据 |
| `api/*` | FastAPI 路由，不直接编写 SQL |

`app/main.py` 在过渡期只负责注册 router 和现有生命周期编排。

## 5. 配置契约

### 5.1 本地环境

```env
DATABASE_URL=
DB_PATH=data/campus_local.db
```

当 `DATABASE_URL` 为空或未设置时：

```text
sqlite:///绝对路径/data/campus_local.db
```

### 5.2 线上环境

Render 使用：

```env
DATABASE_URL=postgresql://...
```

生产环境不使用 `DB_PATH`。

### 5.3 Engine 配置

SQLite：

- 开启外键约束。
- 测试环境允许内存数据库。
- 文件数据库使用绝对路径。
- 不依赖 SQLite 隐式类型转换。

PostgreSQL：

- 使用 `pool_pre_ping=True`。
- 设置有限连接池。
- 所有写操作使用显式事务。
- 不在应用启动时删除或重建业务表。

## 6. 数据模型

### 6.1 `experiment_runs`

复用阶段 0 已建立的 `experiment_runs`。本节描述空间模块需要依赖的字段语义；实现时优先扩展现有表和解码逻辑，不创建同名表或平行运行实体。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | Integer | PK | 实验运行 ID |
| `run_key` | String(64) | UNIQUE, NOT NULL | 对外稳定标识 |
| `name` | String(200) | NOT NULL | 实验名称 |
| `status` | String(32) | NOT NULL | created/running/paused/completed/failed |
| `random_seed` | Integer | NOT NULL | 随机种子 |
| `code_version` | String(64) | NOT NULL | Git commit |
| `model_config` | JSON | NOT NULL | 模型与 Prompt 配置摘要 |
| `world_config` | JSON | NOT NULL | 世界配置快照 |
| `current_tick` | Integer | NOT NULL | 当前 tick |
| `started_at` | DateTime | NULL | 开始时间 |
| `completed_at` | DateTime | NULL | 完成时间 |
| `created_at` | DateTime | NOT NULL | 创建时间 |

### 6.2 `spatial_nodes`

表示建筑、区域、入口、道路节点和室内子空间。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | Integer | PK | 节点 ID |
| `code` | String(64) | UNIQUE, NOT NULL | 稳定代码 |
| `name` | String(120) | NOT NULL | 显示名称 |
| `node_type` | String(32) | NOT NULL | building/zone/entrance/path_point |
| `parent_id` | Integer | FK, NULL | 父空间 |
| `x` | Float | NOT NULL | X 坐标 |
| `y` | Float | NOT NULL | 高度 |
| `z` | Float | NOT NULL | Z 坐标 |
| `radius` | Float | NOT NULL | 占用或到达半径 |
| `capacity` | Integer | NOT NULL | 空间容量 |
| `status` | String(32) | NOT NULL | open/closed/restricted/maintenance |
| `properties` | JSON | NOT NULL | 功能、开放时间和环境属性 |
| `created_at` | DateTime | NOT NULL | 创建时间 |
| `updated_at` | DateTime | NOT NULL | 更新时间 |

约束：

- `capacity >= 0`。
- `radius > 0`。
- `parent_id` 不能指向自身。
- `code` 合并后不能随意修改。

### 6.3 `spatial_edges`

表示节点之间的可通行连接。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | Integer | PK | 连接 ID |
| `from_node_id` | Integer | FK, NOT NULL | 起点 |
| `to_node_id` | Integer | FK, NOT NULL | 终点 |
| `distance_meters` | Float | NOT NULL | 距离 |
| `base_minutes` | Float | NOT NULL | 基础耗时 |
| `bidirectional` | Boolean | NOT NULL | 是否双向 |
| `status` | String(32) | NOT NULL | open/closed/restricted |
| `congestion_factor` | Float | NOT NULL | 拥堵系数 |
| `weather_factor` | Float | NOT NULL | 天气系数 |
| `properties` | JSON | NOT NULL | 无障碍、道路类型等 |

唯一约束：

```text
(from_node_id, to_node_id)
```

### 6.4 `agent_spatial_states`

保存 Agent 当前空间真值，每个 Agent 只有一条当前记录。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `resident_id` | Integer | PK, FK | Agent ID |
| `current_node_id` | Integer | FK, NOT NULL | 当前节点 |
| `target_node_id` | Integer | FK, NULL | 目标节点 |
| `x`/`y`/`z` | Float | NOT NULL | 当前坐标 |
| `facing_x`/`facing_z` | Float | NOT NULL | 朝向 |
| `movement_status` | String(32) | NOT NULL | idle/planning/moving/blocked/arrived |
| `path` | JSON | NOT NULL | 规划路径节点列表 |
| `path_index` | Integer | NOT NULL | 当前路径位置 |
| `progress` | Float | NOT NULL | 当前边进度 0-1 |
| `updated_tick` | Integer | NOT NULL | 最后更新时间 |
| `version` | Integer | NOT NULL | 乐观锁版本 |

### 6.5 `agent_spatial_capabilities`

保存路径和感知机制需要的基础异质性。阶段 1A 定义字段和默认值，阶段 1B、1D、1E 分别启用移动、身体和感知差异。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `resident_id` | Integer | PK, FK | Agent ID |
| `base_speed_m_per_min` | Float | NOT NULL | 基础移动速度 |
| `mobility_class` | String(32) | NOT NULL | standard/limited/assisted |
| `accessibility_needs` | JSON | NOT NULL | 无障碍与路径需求 |
| `perception_radius_m` | Float | NOT NULL | 基础视觉感知半径 |
| `hearing_radius_m` | Float | NOT NULL | 基础听觉感知半径 |
| `source` | String(64) | NOT NULL | seeded/imported/research_config |
| `version` | Integer | NOT NULL | 配置版本 |
| `updated_at` | DateTime | NOT NULL | 更新时间 |

要求：

- 缺失资料使用明确默认值，不根据人物文案或 LLM 推断敏感能力。
- 每个字段具有允许范围、来源和版本。
- 能力字段不能绕过道路通行、门禁和空间准入规则。

### 6.6 `agent_trajectories`

保存研究用途的不可变轨迹事实。

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | Integer | PK | 轨迹记录 ID |
| `experiment_run_id` | Integer | FK, NOT NULL | 实验批次 |
| `tick_number` | Integer | NOT NULL | tick |
| `resident_id` | Integer | FK, NOT NULL | Agent |
| `node_id` | Integer | FK, NULL | 所在节点 |
| `x`/`y`/`z` | Float | NOT NULL | 位置 |
| `movement_status` | String(32) | NOT NULL | 移动状态 |
| `metadata` | JSON | NOT NULL | 额外证据 |
| `created_at` | DateTime | NOT NULL | 写入时间 |

唯一约束：

```text
(experiment_run_id, tick_number, resident_id)
```

索引：

```text
(experiment_run_id, tick_number)
(resident_id, tick_number)
(node_id, tick_number)
```

## 7. 旧位置字段兼容

现有 `residents.location` 在过渡期保留，但不再作为空间真值来源。

迁移规则：

1. 根据现有地点名称映射到 `spatial_nodes.code`。
2. 为每个 Agent 创建带明确默认值和 `seeded` 来源的 `agent_spatial_capabilities`。
3. 为每个 Agent 创建 `agent_spatial_states`。
4. 同步写入期间，空间状态更新后派生更新 `residents.location`。
5. 新代码只通过空间服务读取 `agent_spatial_states` 和能力档案。
6. 所有消费者迁移完成后，再通过独立 PR 移除旧字段依赖。

不得在阶段 1A 直接删除 `residents.location`。

## 8. Migration 设计

### 8.1 Alembic 基线

阶段 1A 的迁移准备 PR：

- 引入 SQLAlchemy 和 Alembic。
- 建立当前数据库结构的基线 revision。
- 不修改现有表和运行行为。
- SQLite 与 PostgreSQL 均执行 `alembic upgrade head`。
- 保留现有 `app/db.py` 兼容入口，禁止在同一业务事务中混用两套连接。

### 8.2 空间表 migration

空间 schema PR：

- 复用并按需扩展现有 `experiment_runs`。
- 创建 `spatial_nodes`。
- 创建 `spatial_edges`。
- 创建 `agent_spatial_capabilities`。
- 创建 `agent_spatial_states`。
- 创建 `agent_trajectories`。
- 写入7个校园主空间和第一版道路拓扑。
- 根据 `residents.location` 回填 Agent 空间状态。
- 将空间能力、当前状态与拓扑纳入 `world-snapshot-v3` 的后续兼容版本；轨迹作为不可变研究证据，不在恢复时删除。

### 8.3 回滚

回滚顺序：

1. 停止空间服务写入。
2. 确保 `residents.location` 已同步最新节点名称。
3. 删除轨迹、Agent 空间状态和空间能力表。
4. 删除空间连接与节点表。
5. 保留阶段 0 已存在的实验运行、快照和世界分支数据。

生产环境执行 downgrade 前必须备份数据库。应用启动流程不自动执行 downgrade。

## 9. 场景图服务

### 9.1 内存结构

```python
@dataclass(frozen=True)
class SpatialNode:
    id: int
    code: str
    name: str
    node_type: str
    parent_id: int | None
    position: tuple[float, float, float]
    radius: float
    capacity: int
    status: str
    properties: dict


@dataclass(frozen=True)
class SpatialEdge:
    id: int
    from_node_id: int
    to_node_id: int
    distance_meters: float
    base_minutes: float
    bidirectional: bool
    status: str
    congestion_factor: float
    weather_factor: float
    properties: dict
```

### 9.2 服务接口

```python
class SpatialService:
    def get_scene_graph(self) -> SceneGraph: ...
    def get_node(self, node_id: int) -> SpatialNode: ...
    def get_agent_state(self, resident_id: int) -> AgentSpatialState: ...
    def get_occupancy(self, node_id: int) -> int: ...
    def create_scene_snapshot(self, run_id: int, tick: int) -> SceneSnapshot: ...
```

阶段 1A 场景图可以按请求缓存；空间节点或连接状态改变时显式失效。不得使用无限期全局缓存。

## 10. API 契约

### 10.1 获取场景图

```http
GET /api/spatial/scene
```

响应：

```json
{
  "coordinate_system": "right-handed-meters",
  "version": 1,
  "nodes": [],
  "edges": []
}
```

### 10.2 获取空间占用

```http
GET /api/spatial/occupancy
```

响应：

```json
{
  "tick": 0,
  "spaces": [
    {
      "node_id": 1,
      "capacity": 120,
      "occupancy": 18,
      "occupancy_ratio": 0.15
    }
  ]
}
```

### 10.3 获取 Agent 空间状态

```http
GET /api/agents/{resident_id}/spatial-state
```

响应同时提供当前空间真值和已启用的空间能力摘要。不存在的 Agent 返回 `404`；空间状态或能力档案尚未初始化返回明确的 `409`，不返回伪造默认位置或能力。

### 10.4 获取 Agent 轨迹

```http
GET /api/agents/{resident_id}/trajectory?run_id=...&from_tick=0&to_tick=100
```

限制：

- `to_tick - from_tick` 必须受上限控制。
- 默认只返回当前实验最近窗口。
- 大规模数据导出使用后续专用导出接口。

## 11. Tick 一致性

阶段 1A 暂不实现移动，但必须确定阶段 1B 的后续写入顺序：

```text
1. 锁定 experiment_run 和当前 tick
2. 读取世界快照
3. 读取所有 Agent 空间状态和当前阶段已启用的能力字段
4. 计算本 tick 空间变化
5. 批量更新 agent_spatial_states
6. 批量写入 agent_trajectories
7. 更新 experiment_runs.current_tick
8. 提交事务
```

要求：

- 当前状态更新、轨迹写入和 tick 推进必须在同一事务中完成。
- 任一步失败时整体回滚。
- 使用唯一约束避免同一 Agent 在同一 tick 重复写入。
- PostgreSQL 可使用行锁；SQLite 通过单写者调度保证一致性。

## 12. 本地与线上运行流程

### 12.1 本地 SQLite

```bash
python -m venv venv
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### 12.2 线上 PostgreSQL

Render 发布流程：

```text
安装依赖
→ alembic upgrade head
→ 启动 Uvicorn
```

生产 migration 失败时不得继续启动新版本应用。

`scripts/seed_fresh_residents.py` 只在全新世界 bootstrap 中运行；种子数据必须通过显式独立命令写入。

## 13. 测试策略

### 13.1 单元测试

- 场景图节点和连接组装。
- 坐标与属性序列化。
- 空间容量和占用计算。
- 旧地点名称到节点代码的映射。
- 空间能力默认值、范围、来源和版本校验。

### 13.2 Migration 测试

SQLite 和 PostgreSQL 分别验证：

- 空数据库执行到 `head`。
- 现有结构升级到 `head`。
- 回填空间状态。
- 回填空间能力且重复执行不覆盖研究配置。
- 重复执行幂等种子逻辑。
- downgrade 后旧应用仍可读取 `residents.location`。

### 13.3 API 测试

- 场景图响应结构。
- 不存在资源的错误码。
- 轨迹窗口和参数边界。
- SQLite/PostgreSQL 响应一致性。

### 13.4 CI 矩阵

```text
Python + SQLite
Python + PostgreSQL service container
```

两个数据库任务都通过后才允许合并数据库相关 PR。

## 14. 性能边界

阶段 1A 目标规模：

- 20-100 个 Agent。
- 7个主空间及不超过100个子空间节点。
- 不超过500条空间连接。
- 单次查询轨迹不超过10,000条。

性能目标：

- 场景图 API P95 小于300毫秒。
- 100个 Agent 空间状态批量读取 P95 小于200毫秒。
- 单 tick 空间状态和轨迹事务 P95 小于500毫秒，不包含 LLM 调用。

## 15. 安全与研究边界

- API 不接受客户端直接写入 Agent 坐标。
- 空间状态修改必须经过服务层和规则校验。
- 实验配置记录代码版本和模型配置，但不保存 API Key。
- 不在轨迹中写入真实学生身份信息。
- 线上数据库凭据只通过环境变量提供。
- 日志不得输出完整 `DATABASE_URL`。

## 16. 实现 PR 顺序

### PR 1：`refactor/spatial-database-foundation`

- 引入 SQLAlchemy Engine 和 Alembic。
- 建立现有数据库基线。
- 保持行为不变。
- SQLite/PostgreSQL migration 测试。
- 不迁移无关业务查询，不重建阶段 0 的 experiment、snapshot 或 branch 模型。

### PR 2：`feat/spatial-schema`

- 新增节点、连接、Agent 空间能力、当前空间状态和不可变轨迹模型。
- 新增 migration、回填和回滚。
- 新增 repository 测试。
- 将空间当前状态和拓扑接入快照与分支隔离。

### PR 3：`feat/spatial-scene-api`

- 新增场景图服务和 API。
- 前端仍可保持现有展示。
- 增加 API 契约测试。
- API 返回后端坐标与拓扑，为后续 Three.js 移除硬编码地图做准备。

### PR 4：`refactor/spatial-service-boundary`

- 将现有空间状态逻辑从 `app/main.py` 迁入空间服务。
- 保持现有接口兼容。
- 增加回归测试。
- 新空间逻辑不再直接读取 `residents.location`。

完成上述四个 PR 即完成统一主路线阶段 1A。之后进入阶段 1B 的路径规划和连续移动，不在阶段 1A 顺带实现排队、感知或社会接触。

实现结果：

- Alembic revision `20260729_0002` 建立 5 张空间基础表。
- 幂等种子建立 19 个空间节点、20 条连接边及居民空间能力和当前状态。
- 场景图、占用、Agent 当前空间状态和轨迹查询 API 已发布。
- 当前空间真值进入 `world-snapshot-v4-spatial`；不可变轨迹保留为实验审计记录。
- SQLite 在线迁移、PostgreSQL 离线 migration SQL、服务读取及恢复测试均已通过。

## 17. 阶段 1A 验收标准

- 本地未设置 `DATABASE_URL` 时使用 SQLite。
- 线上设置 `DATABASE_URL` 时使用 PostgreSQL。
- 两种数据库执行同一套 Alembic migration。
- 应用启动过程不再删除或重建生产数据。
- 空间相关路由不直接执行 SQL。
- 7个校园主空间拥有稳定节点代码和坐标。
- 每位 Agent 拥有唯一空间状态。
- 每位 Agent 拥有带来源、范围和默认值策略的唯一空间能力档案。
- 实验批次能够记录随机种子、代码版本和配置快照。
- 轨迹记录通过实验批次和 tick 唯一定位。
- SQLite/PostgreSQL CI 均通过。
- 原有校园页面和 Agent 生命周期没有行为回归。
- 当前空间真值与世界快照、恢复和分支切换保持一致。
- 轨迹作为不可变研究证据，不因快照恢复被覆盖或删除。

## 18. 已确认的实施决策

结合环境主路线和空间子路线，阶段 1A 采用以下决策：

1. 新增 SQLAlchemy 2 Core 与 Alembic，但采用渐进迁移，不一次性重写现有原始 SQL。
2. 继续保留 `residents.location` 兼容字段，空间服务成为新代码的真值入口。
3. 阶段 1A 使用二维空间真值，`y` 默认值为 `0`，Three.js 负责三维展示。
4. 先完成空间数据底座，再进入路径规划；先完成真实移动，再进入局部感知。
5. 阶段 0 的实验运行、随机种子、快照和分支能力直接复用。
6. 容量准入属于阶段 1C，社会性排队、个人空间和插队属于阶段 2。
