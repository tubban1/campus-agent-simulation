# 万象 WanXiang v2.0

> 确定性世界模拟引擎 — 一个可复现、可对比、可嵌入的"世界模型基座"。

万象用纯 Rust 从种子构建一个完整的世界：物质、生命、角色、社会、文明、灵性、数学八层模拟在守恒律约束下协同运转，每个 NPC 携带自由能最小化(FEP)决策大脑，事件流可溯源、可回放、可对比。同一种子 + 同一规则 = 逐字节可复现的整个世界轨迹。

## 核心特性

| 特性 | 说明 |
|------|------|
| **确定性复现** | 同 `seed` + 同 `rules` → 跨进程逐字节一致的事件流、世界状态、守恒量 |
| **守恒律** | 四池（物质/能量/信息/熵）总量恒定，所有变更经 `ConservationLedger` 路由，漂移 ≈ 0 |
| **FEP 统一大脑** | 每个 NPC 携带 `PredictiveBrain`，以自由能最小化驱动决策 |
| **分层世界模拟** | 8 层核心管线（规则→物质→生命→角色→社会→文明→灵性→数学）+ 50+ 专项模块（修仙/外交/谍报/金融/生态/王朝/律法/昼夜…） |
| **题材塑形 (GenreKit)** | 仙侠 / 科幻 / 都市 / 历史 / 西幻 五种题材真正接入 spawn 动力学（非皮肤） |
| **Save/Load/Diff** | 检查点持久化、断点续跑、两个世界的结构化 diff |
| **规则扫描矩阵** | 量化每个 `WorldRules` 字段对世界的真实影响 |
| **可嵌入 SDK** | `WorldModel` 提供 fork / branch / compare 护城河 API |
| **自包含看板** | 双击 `index.html` 即可在浏览器查看世界编年史 |
| **零 Python 依赖** | 纯 Rust workspace，无外部运行时 |

## 快速开始

### 编译

```bash
# 需要 Rust stable (GNU 工具链)
cargo build --release -p wanxiang_cli
# 产物: target/release/wanxiang.exe
```

> **Windows 编译注意事项**：
> 1. **非 ASCII 路径**：若项目路径含中文（如 `d:\万象`），MinGW 链接器 `ld` 不支持。需将编译产物输出到纯 ASCII 路径：
>    ```bash
>    set CARGO_TARGET_DIR=d:\wx_target
>    cargo build --release -p wanxiang_cli
>    ```
> 2. **MinGW 工具链**：Rust GNU 工具链自带的 self-contained `as.exe`/`dlltool.exe` 可能是损坏的 stub（仅 6KB）。若遇到 `dlltool could not create import library` 错误，需将完整的 MinGW64 `bin` 目录加入 PATH：
>    ```bash
>    set PATH=C:\Users\<用户>\mingw64\bin;%PATH%
>    set CARGO_TARGET_DIR=d:\wx_target
>    cargo build --release -p wanxiang_cli
>    ```
>    MinGW64 可从 [winlibs.com](https://winlibs.com/) 或 MSYS2 安装。

### 运行

```bash
# 最简：跑 30 tick，打印系统报告
wanxiang --seed 42 --ticks 30

# 仙侠世界，1000 tick，生成 World Lab 看板 + 散文纪事
wanxiang --seed 42 --type xianxia --ticks 1000 --lab ./my_world --prose

# 科幻世界，导出 8 份 JSON 供下游消费
wanxiang --seed 7 --type scifi --ticks 200 --export ./export_scifi

# 启动 HTTP 游戏后端
wanxiang --serve 8080 --seed 42 --ticks 50
```

### CLI 完整参数

```
wanxiang [选项]

  --type <genre>     世界类型: xianxia(仙侠,默认) | scifi(科幻) | urban(都市) | historical(历史) | western(西幻)
  --name <str>       世界名称 (默认 太虚界)
  --seed <u64>       随机种子 (默认 42；同 seed → 逐帧可复现)
  --ticks <u64>      模拟 tick 数 (默认 30)
  -v | --verbose     打印详细事件
  --replay <path>    导出事件溯源回放日志到 JSON
  --narrative        输出世界编年史（结构化叙事报告）
  --prose            输出世界散文纪事（章节体、可读叙事）
  --export <dir>     导出世界状态为 8 份 JSON
  --report <dir>     生成世界终局报告 report.json（阵营排名 + 命名议题热点 + 重大事件计数）
  --dashboard <dir>  生成自包含 Web 编年史看板
  --save <path>      保存确定性检查点（seed+tick+rules）到 JSON
  --load <path>      从检查点加载并续跑（与同 seed 直接跑到同 tick 逐字节一致）
  --diff <a> <b>     比对两个检查点（守恒Δ/实体Δ/首分歧tick/规则差异）
  --scan             规则参数扫描矩阵：量化哪个字段真正撼动世界
  --lab <dir>        生成 World Lab 三合一看板（观赏+经济标定+实验）
  --serve <port>     启动游戏后端 HTTP 服务
  -h | --help        显示帮助
```

## 架构

```
万象 WanXiang
│
├── wanxiang_types     共享类型层（WorldRules, Entity, WorldEvent, Genome...）
├── wanxiang_core      活核心基础设施（EventBus, StateManager, Persistence, ParallelProcessor）
├── wanxiang_brain     统一大脑（FEP 决策 + 意识循环 + 量子神经元 + 梦境 + 顿悟 + ToM + 反事实 + 模因）
├── wanxiang_evolution 统一进化引擎（多层级选择 + 基因组）
├── wanxiang_world     世界模拟层（8 层 tick + 守恒律 + 生成 + 选择 + 社会 + 叙事 + 导出 + diff + scan）
├── wanxiang_engine    LivingCore 统一入口（组合 world + evolution + event_bus + state）
├── wanxiang_sdk       可嵌入 SDK（WorldModel: fork/branch/compare 护城河 API）
└── wanxiang_cli       命令行入口 + HTTP 后端
```

### 每个 tick 发生什么

```
1. 世界模拟器执行 8 层 tick
   规则 → 物质 → 生命 → 角色(FEP决策) → 社会(贸易/外交/战争) → 文明 → 灵性 → 数学
2. 事件总线分发产生的事件
3. 进化引擎根据事件学习并优化参数
4. 守恒对账（四池漂移检查）
5. 状态管理器更新快照
```

### 确定性契约

> **完整契约见 [ARCHITECTURE.md](ARCHITECTURE.md)** —— 7 条架构铁律、RNG 子流盐值表、跨平台确定性规则、开关体系、配置外置地图，改代码前必读。

1. **种子流隔离** — 所有 RNG 来自 `StdRng::seed_from_u64(seed)` 及 `seed ^ SALT` 派生子流，无 `thread_rng()` / `from_entropy()` 泄漏
2. **有序迭代** — 所有 `HashMap` 迭代经排序 Vec 或 `BTreeSet` 消除跨进程随机性；argmax 必加键序 tiebreak
3. **确定性 ID** — 实体/事件 ID 由种子派生的计数器生成，非 UUID
4. **浮点求和顺序** — 所有 `f64` 聚合先按键排序再求和，消除 IEEE-754 非结合性导致的 ~1e-12 发散

## SDK 使用

```rust
use wanxiang_sdk::WorldModel;
use wanxiang_types::{WorldRules, WorldType};

let rules = WorldRules::for_genre(WorldType::Xianxia);
let mut world = WorldModel::new(rules, 42);

world.step(100);                        // 推进 100 tick
println!("{}", world.info_json());      // 概要 JSON
println!("{}", world.economy_json());   // 经济标定 JSON
let events = world.recent_events(10);   // 最近 10 条事件

// 护城河 API
let forked = world.fork();              // 逐字节一致的独立副本
let branched = world.branch(|r| r.chaos_level = 0.9);  // 规则扰动分支
let diff = world.compare(&branched);    // 结构化 diff
```

### HTTP 游戏后端

```bash
wanxiang --serve 8080 --seed 42 --ticks 50
```

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/` | 世界概要（seed/tick/计数/守恒） |
| GET | `/state` | 完整世界快照 JSON |
| GET | `/economy` | 经济标定 JSON |
| GET | `/events?n=10` | 最近 n 条事件 |
| POST | `/tick?n=1` | 推进 n tick 后返回新概要 |

## 产出层

万象的产物是**数据**，不是文本。以下产出均由世界状态确定性派生：

| 产出 | CLI 参数 | 内容 |
|------|----------|------|
| **8-JSON 导出** | `--export <dir>` | entities / factions / events / eras / skills / wars / resources / rules |
| **World Lab** | `--lab <dir>` | 自包含 HTML（观赏 + 经济标定 + 规则扫描三合一） |
| **编年史看板** | `--dashboard <dir>` | 自包含 HTML（势力兴衰 / 时代时间线 / 事件流） |
| **终局报告** | `--report <dir>` | report.json（阵营排名 + 命名议题热点 + 重大事件计数） |
| **散文纪事** | `--prose` | 章节体世界史书（stdout） |
| **编年史** | `--narrative` | 结构化叙事报告（stdout） |
| **回放日志** | `--replay <path>` | 事件溯源 JSON（每 tick 事件流 + 快照） |
| **检查点** | `--save <path>` | (seed, tick, rules) 三元组 JSON |

## 测试

```bash
cargo test --workspace --no-fail-fast
```

全量测试通过（最近一次全绿约 **450** 个，零失败、零 warning；CI 双平台确定性裁判）：
- 9 个 crate 全量覆盖（types / core / brain / evolution / world / engine / sdk / cli / web）；各 crate 精确计数以 `cargo test --workspace` 输出为准（最近一次全绿约 450 个）。

CI（`.github/workflows/ci.yml`）在 ubuntu + windows 双平台跑全量测试，并做同 seed 双跑 diff + 跨平台导出哈希比对（确定性裁判）。

## 项目定位

万象是一个**世界模型基座**，而非写小说的工具。它为下游提供：

- **游戏后端** — `--serve` HTTP API 驱动游戏世界
- **互动叙事** — 事件流 + 散文纪事作为叙事素材
- **研究分析** — 确定性可对比的虚拟社会实验台
- **可视化看板** — 自包含 HTML 双击即用

## 技术栈

- **语言**: Rust 2021 edition
- **并行**: rayon / crossbeam / parking_lot
- **序列化**: serde / serde_json
- **随机**: rand / rand_distr (StdRng / ChaCha)
- **零外部服务依赖**: 无数据库、无消息队列、无 Python

## 许可

私有项目，版权归维护者所有。

---

**维护者**: 灵明  
**版本**: 0.1.0  
**最后更新**: 2026-08-02
