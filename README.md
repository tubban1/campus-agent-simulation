# Campus Agent Simulation

校园封闭世界 AI-Agent 沙盘系统。项目用 FastAPI 提供后端 API，用 SQLite 或 PostgreSQL 保存世界状态，并用一个静态 Three.js 前端展示校园地图、Agent 状态、社交网络、日报和模拟进程。

当前版本的核心体验是：20 个校园 Agent 在宿舍区、教学楼、图书馆、食堂、操场、商业街、校务处之间生活。每个 Agent 有身份、目标、记忆、关系、精力、时间预算和日程；每轮模拟会经历“感知 -> 决策 -> 行动 -> 环境反馈 -> 记忆沉淀”。

## 功能概览

- 校园封闭世界：固定空间、校园环境、空间容量、活动事件、拥挤度和资源压力。
- 多 Agent 生命周期：Agent 根据环境、日程、记忆、关系和长期目标自主选择行动。
- 可解释状态：保留感知、检索记忆、决策、执行结果和环境反馈日志。
- 社交系统：关系分数、信任、合作、竞争、冲突、协作小组和群体目标。
- 经济与治理：库存、交易、政策提案、投票和结算。
- 校园日报：Agent 可根据当天行动发布第一人称校园投稿。
- 外部资讯：从固定 RSS 源同步资讯，再按 Agent 相关性和关系网络传播。
- 真实时间/天气：可同步系统时间和成都天气，驱动校园环境参数。

## 技术栈

- Python 3.11
- FastAPI / Uvicorn
- SQLite 默认本地数据库，PostgreSQL 可选
- Pydantic v2
- Requests
- python-dotenv
- 前端为单文件静态页面，使用 vendored Three.js

## 快速启动

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_campus_safe.py
uvicorn app.main:app --reload
```

打开：

- 前端页面：http://127.0.0.1:8000/
- OpenAPI 文档：http://127.0.0.1:8000/docs
- 当前世界状态：http://127.0.0.1:8000/api/state

如果需要 AI 自主决策和 AI 日报，请在 `.env` 中填写：

```dotenv
LLM_API_KEY=你的 API Key
LLM_API_URL=你的模型 generateContent 接口
```

没有配置 LLM 时，world runtime 会进入不消耗模型预算的规则模式；行动结算、环境更新、校园日报事实稿和普通状态查询仍可持续运行。`/api/ai/test` 会返回明确的 `503` 配置提示。

## 常用命令

安全初始化，不覆盖已有世界：

```bash
python scripts/init_campus_safe.py
```

重置并重新生成校园世界：

```bash
python scripts/init_campus.py
```

启动服务：

```bash
uvicorn app.main:app --reload
```

模拟一天：

```bash
curl -X POST http://127.0.0.1:8000/api/simulate/ai-day
```

手动让单个 Agent 完成一轮生命周期：

```bash
curl -X POST http://127.0.0.1:8000/api/simulate/lifecycle-step/1
```

同步真实天气：

```bash
curl -X POST http://127.0.0.1:8000/api/campus/environment/sync-real-weather
```

## 项目结构

```text
app/
  main.py          FastAPI 应用、业务流程、API 路由
  db.py            SQLite/PostgreSQL 连接与 SQL 兼容层
  models.py        基础数据表
  schema.py        校园环境、社交、资讯、日志等扩展表
agents/
  prompts.py       Agent 提示词素材
frontend/
  index.html       静态前端主页面
  assets/avatars/ 20 个校园 Agent 头像
  vendor/three/    前端使用的 Three.js
scripts/
  init_campus.py       重置式校园种子数据
  init_campus_safe.py  幂等校园初始化
  init_db.py           旧城市示例初始化脚本
tools/
  city_tools.py    基础行动工具：移动、聊天、交易、记忆、关系
services/
  llm_service.py   LLM 调用封装
docs/
  ARCHITECTURE.md  架构说明
  API.md           API 速查
  OPERATIONS.md    初始化、数据、部署和排障
```

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [API 速查](docs/API.md)
- [运维与部署](docs/OPERATIONS.md)
- [Supabase 复原数据库](docs/SUPABASE.md)
- [团队 Git 管理流程](docs/GIT_WORKFLOW.md)
- [模拟一天流程](docs/SIMULATE_DAY_FLOW.md)
- [校园平行世界运行时设计](docs/WORLD_RUNTIME_DESIGN.md)
- [校园真实环境模拟路线图](docs/ENVIRONMENT_REALISM_ROADMAP.md)
- [真实校园地理信息导入](docs/REAL_CAMPUS_GEO_IMPORT.md)
- [外部世界数据接入与因果传播设计](docs/EXTERNAL_WORLD_DATA_DESIGN.md)

## 注意事项

- `scripts/init_campus.py` 会清空并重建核心校园数据，适合开发重置。
- `scripts/init_campus_safe.py` 会检测已有数据并跳过种子写入，适合本地和持久化环境。
- 代码里仍保留部分历史命名，如 `city_events`、`city_tools.py`、`init_db.py`。当前主线语义是校园沙盘。
- 默认 SQLite 数据库路径是 `data/city.db`；设置 `DATABASE_URL` 后切换到 PostgreSQL。
