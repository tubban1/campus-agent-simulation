# World2 平行宇宙：多语境与跨区域开放世界扩展规格 (World2 Multiverse Expansion Spec)

> **世界观核心定位 (World2 Vision)**：  
> 本系统的底层核心世界观为 **World2** —— 一个由自治 AI Agent 完全驱动的**互联平行宇宙 (Agent-Driven Parallel Multiverse)**。清华大学校区仅为 World2 的**首个联通空间 Sector（Sector-01）**。未来系统将逐步解锁与连通中关村科技园区（Sector-02）、硅谷数字孪生园区（Sector-03）及虚拟赛博协同空间（Sector-04），构建多 Sector 跨域连通的平行维度网络。
>
> **规范定位**：本规格书是 [ENVIRONMENT_REALISM_ROADMAP1.md](ENVIRONMENT_REALISM_ROADMAP1.md)（物理与底层机制）、[ENVIRONMENT_REALISM_ROADMAP2.md](ENVIRONMENT_REALISM_ROADMAP2.md)（主体认知与观察）及 [ENVIRONMENT_REALISM_ROADMAP3.md](ENVIRONMENT_REALISM_ROADMAP3.md)（算法与 LLM 分工）的**多 Sector / 多维度落地扩展子规范**。  
> **目标**：指导系统从单校区无缝扩展至 World2 多区域、跨维度开放世界，支持多 Sector 拓扑、跨维度 Agent 迁徙、多时区、多语言、多货币及本地化制度体系。

---

## 1. 与既有 Roadmap 的对齐与关系

### 1.1 架构契约对齐
- **Roadmap 1 0.1 节（环境配置与版本）**：已预留 `world_key` 隔离、多校区/多区域模板化配置与外部数据快照。
- **Roadmap 1 1F 节（异质性能力）**：已定义 `language_access`（语言通达度）、文化背景与制度可达性。
- **Roadmap 1 2.1 节（统一账本）**：已建立双边复式记账、多部门账户体系与系统对账规则。
- **Roadmap 3 4.4 节（解耦硬编码）**：明确要求“长期避免将校园名词固化为底层真值”，规则层负责物理/货币/时间/拓扑，LLM 负责语言表达与叙事。

### 1.2 引入方式：主干规范 + 专用落地架构
1. **主干 Roadmap 保持统一**：Roadmap 1~3 继续作为事实层、认知层与算法/LLM分工的最高规范。
2. **本规格书作为工程落地指南**：针对语言 i18n、货币体系、空间语义解耦、时区与 RSS 摄入给出具体的数据库 Schema 升级、API 变动与代码改造实施步骤。

---

## 2. 五维扩展技术规格

```text
                    多语境开放世界架构 (Multi-Locale World Architecture)
                                             │
   ┌───────────────────┬─────────────────────┼─────────────────────┬───────────────────┐
   ▼                   ▼                     ▼                     ▼                   ▼
1. 地理与气象        2. 语言与认知         3. 货币与经济         4. 空间语义解耦     5. 制度与文化语境
• WGS84 经纬度       • i18n Prompt 模板    • 货币代码(CNY/USD/CHF)• 抽象 node_type     • 组织/机构映射
• 本地时区 (IANA)    • 语言能力向量        • 本地物价与 WTP      • i18n 动态显示 Label• 门禁与假期规则
• 外部气象/RSS源    • 跨语言沟通摩擦      • 双边账户隔离        • 场景 Affordance   • 社区非正式规范
```

### 2.1 地理、时间与气象 (Geography, Timezone & Weather)

| 字段 / 机制 | 当前状态 | 多语境扩展规范 |
| --- | --- | --- |
| **地理原点 (Origin WGS84)** | 支持任意经纬度 (`geo_importer.py`) | 保持 `origin_lat/origin_lon` 动态推断，为每个 `world_key` 生成米制投影。 |
| **时区 (Timezone)** | `Asia/Shanghai` | 使用 IANA 标准时区标识（如 `America/Los_Angeles`, `Europe/Zurich`）。所有 `world_tick` 与 `real_time` 按当前世界的 `world_timezone` 推进。 |
| **气象同步 (Weather)** | Open-Meteo API | `OpenMeteoAdapter` 自动传入该世界的经纬度与时区参数，平滑计算当地气温、降水与风速。 |
| **资讯摄入 (News/RSS)** | 固定 RSS 源 | `external_world_config` 允许按世界配置独立的 RSS Feed URL（如 Berkeley News, ETH News），自动匹配分类器。 |

### 2.2 语言、Prompt 与认知心智 (Language, i18n & Mindset)

#### 2.2.1 Prompt i18n 国际化模板
LLM 思考与生成的语言由 **系统提示词 (System Prompt) 的语言** 决定。
在 `app/i18n/` 中增加多语言 Prompt 映射矩阵：

```python
# app/i18n/prompts.py
PROMPT_TEMPLATES = {
    "zh-CN": {
        "planner_system": "你是一个校园平行世界的运行时 planner...",
        "dream_system": "你正在经历一场梦境...",
    },
    "en-US": {
        "planner_system": "You are the runtime planner for a parallel campus world...",
        "dream_system": "You are experiencing a dream...",
    },
    "de-CH": {
        "planner_system": "Du bist der Laufzeit-Planer für eine parallele Campus-Welt...",
        "dream_system": "Du erlebst einen Traum...",
    }
}
```

#### 2.2.2 语言能力向量 (Language Vector)
将单一的 `language_access`（0-100）升级为多语言通达度向量：

```json
{
  "language_proficiencies": {
    "en": 95,
    "de": 70,
    "zh": 20
  }
}
```

- **感知与理解**：理解当地新闻/通告时，按对应语言的分数进行置信度与误解率修正。
- **社交沟通摩擦 (Chat Friction)**：两名 Agent 对话时，共享语言能力 $\text{SharedLang} = \max_l (\min(A_l, B_l))$。若 $\text{SharedLang} < 50$，对话成功率降低，失真度升高，关系增益受阻。

### 2.3 货币与经济体系 (Currency & Double-Entry Ledger)

#### 2.3.1 货币代码与账户隔离 (`currency_code`)
统一账本 (`world_resource_accounts`) 与分录表 (`world_resource_transfers`) 引入 `currency_code`：

- 清华 (`tsinghua_main`): `"CNY"` (￥)
- 伯克利 (`uc_berkeley`): `"USD"` ($)
- 苏黎世高工 (`eth_zentrum`): `"CHF"` (CHF)

账本幂等对账校验严格在同一 `world_key` 与 `currency_code` 内部执行，禁止直接跨币种混算。

#### 2.3.2 本地物价基线与 WTP 支付意愿
- **物价锚定 (`price_baseline`)**：每个世界的商业设施与商品库保存当地基础货币定价（例如清华早餐 8 元 CNY，伯克利早餐 6 美元 USD）。
- **WTP 计算**：最高支付意愿根据该世界的物价指数与 Agent 个人收入/资产弹性校准：
  $$\text{MaxWTP} = \text{PriceBaseline} \times \max(1.0, \text{Elasticity}) \times \text{IncomeScale}$$

### 2.4 空间语义解耦 (Spatial Semantic Decoupling)

底层空间真值采用抽象 `node_type` 与 `amenity` 属性，展示名称根据世界的 `locale` 动态解析：

| 抽象功能 `node_type` | `zh-CN` 显示 Label | `en-US` 显示 Label | `de-CH` 显示 Label |
| --- | --- | --- | --- |
| `canteen` / `dining` | 食堂 / 餐饮点 | Dining Hall / Cafeteria | Mensa |
| `classroom` / `lecture` | 教学楼 / 报告厅 | Academic Building / Hall | Hörsaal / Hauptgebäude |
| `dormitory` / `housing` | 宿舍区 / 居住区 | Student Housing / Dorm | Wohnheim |
| `library` / `study` | 图书馆 / 自习室 | Main Library / Study Center | Bibliothek |
| `commercial` / `retail` | 商业街 / 便民店 | Student Center / Stores | Campus Shop |
| `administration` | 校务处 / 服务大厅 | Student Services / Admin | Rektorat / Dekanat |

代码中的 `VALID_LOCATIONS` 升级为基于 `node_type` 的动态查询，不再依赖硬编码中文字符串。

### 2.5 制度与社会文化语境 (Institutional & Cultural Context)

- **机构分类映射**：`campus_organizations` 采用标准组织类型编码（`academic_dept`, `administrative_office`, `student_club`, `local_merchant`）。
- **时间规约 (Schedule Rules)**：门禁、学期阶段（期中周、考试周、暑假）、作业截止日期的文化习惯按世界的 `semester_system` 配置实例化。

---

## 3. 跨文化/跨语言平行世界对比实验设计

一旦完成多语境扩展，系统将具备进行**高内部效度社科对比实验**的能力：

```text
[实验组 A] 清华校区 (tsinghua_main)   : 20 Agents + CNY + 华北气象 + 中文 RSS/i18n
[实验组 B] 伯克利校区 (uc_berkeley)  : 20 Agents (相同能力曲线) + USD + 加州气象 + 英文 RSS/i18n
[实验组 C] 苏黎世校区 (eth_zentrum)   : 20 Agents (相同能力曲线) + CHF + 瑞典/瑞士气象 + 德英文 RSS/i18n

→ 核心观察点：语言通达阻抗、物价弹性与外部信息冲击对 Agent 群体涌现与生命历程的蝴蝶效应。
```

---

## 4. 分阶段工程实施路线 (Implementation Roadmap)

### 阶段 A：地理与气象全量多世界配置（低成本，现有框架已 80% 支持）
1. 在 `world_runtime` Schema 中增加 `world_locale`、`world_currency`、`world_timezone`。
2. 配置 `OpenMeteoAdapter` 与 `FixedRSSAdapter` 的世界专属参数。

### 阶段 B：空间语义与提示词 i18n 解耦（中等成本）
1. 重构 `planning_decision.py` 等模块中的提示词，抽出 `app/i18n/` 提示词模板包。
2. 将 `VALID_LOCATIONS` 硬编码解耦为基于 `node_type` 的动态 Label 解析器。

### 阶段 C：多语言能力向量与沟通摩擦（中等成本）
1. 扩展 `agent_capability_profiles` 中的 `language_proficiencies` 字段。
2. 在 `social_interaction` 中加入基于共享语言能力的沟通阻抗与信息失真逻辑。

### 阶段 D：多货币账本与本地物价对账（高成本）
1. 在 `world_resource_accounts` 与交易分录中全面引入 `currency_code` 维度。
2. 验证多世界隔离对账与复式记账守恒。
