# 硬编码审计

对代码库中地理位置、行为方式、世界规则三类硬编码的逐项盘点，区分「可配置的默认值」与「动架构的深层假设」，并给出分阶段的外置化路线。审计范围：`app/` 与 `frontend/`（排除 `tests/`、`venv/`、`*.sql`）。

## 核心结论

硬编码分两层：

1. **可配置但写死了默认值**：作息规则、地点目录、制度参数、动作规则实际上都有数据库表可覆盖（`world_action_rules`、`campus_schedule_rules` 等），代码里的常量只是种子值，迁移成本低。
2. **真正的架构假设**：单世界运行时（`WORLD_RUNTIME_ID = 1`）、角色四分类、「校园」语义渗透、清华地名关键词做地点归类。这四项贯穿提示词、分类逻辑和数据模型，是多世界 / 新场景迁移的真正成本。

## 空间与地理（GEO）

| ID | 位置 | 硬编码内容 | 影响 | 等级 |
|----|------|-----------|------|------|
| GEO-4 | `app/spatial/location_catalog.py:21-27` | 地点分类靠关键词匹配，词表写死清华专名：清晏、清芬、观畴、桃李、紫荆园、逸夫、主楼 | 导入任何非清华世界时分类**静默失效**，全部落为 general | 高 |
| GEO-1 | `app/main.py:873-874` | `BEIJING_LATITUDE = 40.0062` / `BEIJING_LONGITUDE = 116.3269`，天气锚点钉在清华 | 天气系统默认北京；换世界需显式传参 | 中 |
| GEO-2 | `frontend/js/app.js:209-210`、`frontend/js/spatial/maplibre-map.js:145,175,273` | 前端兜底坐标 `116.3221954, 40.0023657`（清华），共 6 处重复 | 无 origin 数据的世界会被画到清华；坐标散落难维护 | 中 |
| GEO-5 | `app/schema.py:69-72` | 默认地点集：宿舍区 / 教学楼 / 图书馆 / 食堂，含容量与开放时段（食堂 6-21 点等） | 世界的兜底地点拓扑，新场景下是一套校园残影 | 中 |
| GEO-3 | `frontend/js/app.js:28,227,259` | `world_key` 默认 `"tsinghua_main"`，回退链也优先找 tsinghua | 新用户 / 清缓存后强制回到清华世界（有回退链，伤害有限） | 低 |
| GEO-6 | `frontend/js/spatial/maplibre-map.js:185,192` | OSM 瓦片源 `tile.openstreetmap.org` + maplibre 字体 CDN | 底图供应商写死，可用 `window.MAP_TILE_URL` 覆盖 | 低 |

## 行为方式与角色（BHR）

| ID | 位置 | 硬编码内容 | 影响 | 等级 |
|----|------|-----------|------|------|
| BHR-5 | `app/world_runtime/planning_decision.py:324` | LLM planner 提示词第一句：「你是一个校园平行世界的运行时 planner」 | 所有 LLM 决策被锚定在校园设定，换世界需要改代码 | 高 |
| BHR-1 | `app/main.py:361-375` | `DEFAULT_SCHEDULE_RULES` 12 条作息：学生 0-6 点宿舍睡觉、6-9 早餐、8-12 上课、11-13 午餐排队；教师授课、商户营业、后勤巡查 | 整个世界的作息节律；角色维度写死为 student / teacher / business / service 四类 | 高 |
| BHR-2 | `app/body_runtime.py:175-179` | 动作生理增量字典：`consume: hunger -45, hydration -14…`、`hydrate: hydration -42`、`reflect: stress -12` | 生理模型全部参数写死，无法按世界调参 | 高 |
| BHR-6 | `app/world_runtime/causal_actions.py` | 行动前置条件在代码而非数据：rest 只能在居住类地点、consume / hydrate 需资源、非 move 动作必须在开放地点 | 行动合法性规则不可配置；新世界若语义不同会被误判 | 中 |
| BHR-4 | `app/adaptation/learning.py:151,167-168` | 奖励系数 `+1.0 / -0.65 / -0.45 / -0.55`、EMA `0.7 / 0.3`、置信度 `0.25 + 0.08n` | 策略学习全部超参数，无法按实验分组调整 | 中 |
| BHR-3 | `app/population/service.py:341-345` | 新居民默认值：`personality="正在适应新环境"`、`goal="建立校园生活"`、`money=100`、`mood='平稳'` | 人口事件兜底人格，带校园语义 | 低 |
| BHR-7 | `app/agent_service.py:35` | 初始 `time_budget = 100`、"开始新的一天" | agent 初始状态常量 | 低 |

## 世界规则与运行时（RUL）

| ID | 位置 | 硬编码内容 | 影响 | 等级 |
|----|------|-----------|------|------|
| RUL-1 | `app/main.py:349` | `WORLD_RUNTIME_ID = 1`，全局单例世界运行时主键 | **最深的硬编码**：整个 runtime 层假设只有一个世界在跑 | 高 |
| RUL-5 | `app/external_world/service.py:87-99`、`app/external_world/adapters.py:54,106` | 新闻源写死：36kr、ithome、Bing News（关键词 AI/大学/教育）、Google News；天气源 open-meteo + met.no | 外部信息流只覆盖中文科技 / 教育新闻，关键词也是校园视角 | 中 |
| RUL-4 | `app/credit/service.py:234` | 信用额度公式 `1500 + economic×80 + money×5`，clamp 2000-12000 | 金融系统参数不可配置 | 中 |
| RUL-2 | `app/main.py:351-355` | stale tick 90s、外部同步 900s、天气同步 1800s、观察者冷却 300s、校园新闻窗口 8h | 运行时节律常量（.env 可覆盖部分） | 低 |
| RUL-3 | `app/social_institutions/service.py:110-127` | 制度参数：违规罚款 `sanction_minor: 300`、贡献奖励 `reward_minor: 500`、审批阈值 55 / 65 | 制度奖惩数值，属种子数据可改 | 低 |
| RUL-6 | `app/spatial/runtime.py:56-67` | 移动速度按身体状态打折的调节公式（基础速度在能力表，公式在代码） | 半数据化；调节曲线不可配置 | 低 |

## 架构级假设（ARC）

四项贯穿数据模型、分类逻辑与提示词的深层假设，不是「改配置」能解决的：

- **ARC-1 单世界运行时**（对应 RUL-1）：`WORLD_RUNTIME_ID = 1` 是全局主键，tick 循环、预算、分支都挂在它上面。空间层的 `world_key` 虽支持多世界数据，但同一时刻只有一个 runtime 在推进。并行多世界需要 runtime 实例化改造。
- **ARC-2 角色四分类**（对应 BHR-1）：student / teacher / business / service 四类角色贯穿作息规则、制度流程、提示词与默认人格。新增角色类型会同时触碰多层代码与种子数据。
- **ARC-3 「校园」语义渗透**（对应 BHR-5、RUL-5）：LLM planner 提示词自称「校园平行世界」、新闻关键词限定「大学 / 教育」、新居民默认目标是「建立校园生活」。语义假设没有集中配置点，散落在提示词与字符串里。
- **ARC-4 关键词即分类**（对应 GEO-4）：地点归类靠清华专名关键词匹配，而非节点元数据。这是导入新城市世界时最可能**悄悄失效**的一处——不报错，只是所有地点都变成 general。

## 分阶段优化路线

按「先止血、再外置、后重构」排序。P0 全部是低风险高收益的配置化改造；P2 动架构，建议和多世界产品决策绑定后再做。

### P0：消除静默失效点（约 1-2 周，不动架构）

- 地点分类改为 `spatial_nodes` 的 `node_type` / 标签驱动，清华关键词表降级为导入期的一次性映射（GEO-4 / ARC-4）。
- planner 提示词模板化：世界名、角色集、行为准则由 `environment_config` 注入，代码里只留占位符（BHR-5 / ARC-3）。
- 新闻源与检索关键词移入 `external_sources` 配置，按世界设定订阅主题（RUL-5）。
- 前端兜底坐标与默认 `world_key` 收敛为单一常量，并从 `/api/spatial/worlds` 取默认值（GEO-2、GEO-3）。

### P1：规则与数值外置（约 1 个月，数据层改造）

- `DEFAULT_SCHEDULE_RULES`、默认地点集、制度参数全部移入 `environment_config` 版本化体系，代码只留空表种子（BHR-1、GEO-5、RUL-3）。
- 身体状态增量字典表化，与 `world_action_rules` 合并为「动作 = 前置条件 + 效果」的统一规则记录（BHR-2、BHR-6）。
- 学习超参数与信用额度公式挂到实验配置（`experiment_runs` 已有分支机制可承接 A/B）（BHR-4、RUL-4）。
- 天气锚点坐标改为 per-world 配置，去掉 Beijing 常量（GEO-1）。

### P2：架构假设重构（按产品节奏，需先做多世界决策）

- runtime 多实例化：`WORLD_RUNTIME_ID` 从常量变为 per-branch 资源，tick 调度按分支隔离（ARC-1）。
- 角色分类学泛化为可定义的角色模板（作息集 + 制度角色 + 提示词人设三件套），四分类变成默认模板而非枚举（ARC-2）。
- 建立「世界描述清单」：一个世界的语义（名称、角色、新闻主题、地名词表）集中在一处声明，供提示词、分类器、外部源共同消费（ARC-3、ARC-4）。
