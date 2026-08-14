# 采购与供应链层（Pluggable Supplier / Procurement Layer）

## 目标

在保持未来接入真实外部经济体兼容性的前提下，让生产投入品（`食材包`、`饮品原料`）
可以在校园世界中持续再供应，从而把“生产 → 消费 → 补货”串成可审计的循环，而不是
一次性播种后永久断供。

设计原则（与 `ENVIRONMENT_REALISM_ROADMAP3` §4.3/§4.4 一致）：
**机制通用、内容可换**。采购层是通用接口，供应商是数据驱动、可替换的 actor；
今天用校内供应商，未来换成真实外部经济体只需改配置，不重写核心。

## 为什么需要这一层

- 原实现中，投入品只在 `seed_supply_foundation` 播种一次（各 12 个），被生产消耗后
  永不补充；耗尽后 `_start_production_batch` 返回 `input_shortage`，生产永久中断。
- 货币端依赖外部注入（`external_inflow`），并向 `system:campus-services` 单向漏出。
- 要形成“内循环”，必须补上“上游生产/采购”这一层，并把边界上的进出口显式记账。

## 现状（本次实现）

### 新增表（迁移 `20260813_0047_procurement_layer`）

- `procurement_suppliers`：把某个目录条目映射到一个供应商 actor 与供应方式。
  - `supplier_key` / `supplier_actor_key`：供应商身份（一个经济 actor）。
  - `upstream_actor_key`：该供应商的上游（外部经济体），用于补货。
  - `supply_kind`：`in_world`（校内供应商经账本交易供货）或 `external`（直接外部进口）。
  - `unit_cost_minor` / `replenish_threshold` / `replenish_qty`：成本与再补货参数。
- `procurement_orders`：可审计的采购订单（状态、原因、账本交易引用）。

### 新模块 `app/supply/procurement.py`

- `seed_default_suppliers(conn)`：幂等注册校内供应商 `supplier:campus-logistics`
  （`校园后勤供应中心`）与外部上游 `external:campus-wholesale`，并为 `食材包`/`饮品原料`
  各建一条供应商配置。表不存在时优雅跳过（保持旧世界/旧测试可用）。
- `external_goods_inflow(...)`：跨边界货物进口原语——买方库存资产借方、外部上游
  `imports-equity` 贷方，账本守恒，并在元数据中标记 `cross_boundary`。
  这是未来接真实经济体的进口钩子（对“钱”的 `external_inflow` 的货物对应物）。
- `_transfer_goods(...)`：校内供应商→生产者的通用货物转移（支持 `input` 类型货物，
  复用账本交易原语，买方付现金、库存资产借方、卖方记收入/COGS）。
- `procure_inputs(conn, now)`：对每个低于再订货点的投入品，按供应商配置补货——
  `in_world` 走 `_transfer_goods`，`external` 走 `external_goods_inflow`；缺货/资金不足
  记为 `blocked` 订单而非抛错。
- `process_procurement_runtime(conn, world_time)`：tick 入口，先补足供应商缓冲，再按需采购。

### 接线

- `process_supply_runtime` 在生产前调用 `process_procurement_runtime`，并把采购结果并入
  返回载荷 `procurement`。
- `seed_supply_foundation` 末尾调用 `seed_default_suppliers`（幂等）。

### 设施工单 ↔ 供应账本（本次已打通）

- `facility_service` 的 restock 工单完成时优先走 `procurement.procure_item`：
  `meal_stock → 套餐饭` 映射到目录商品，从配置的供应商采购进货（场内 `_transfer_goods`
  或外部 `external_goods_inflow`），生成可审计的 `procurement_orders` 与账本交易，再填满
  空间货架（`spatial_facility_states.inventory_units`），而不是“就地无中生有补满”。
- **采购是补货的唯一路径，不保留旧逻辑回退**：供应子系统不可用、无供应商、或买家资金
  不足时，`_try_supply_restock` 返回 `False`，工单保持 `open/assigned` 作为真实缺货，
  绝不虚构货架库存。
- 新增 `procure_item(...)` 公共接口：按目录商品 + 数量 + 买家 + 地点，从该商品的配置
  供应商采购并记订单。

## 兼容性设计

- **供应商是可替换 actor**：把投入品换成外部经济体供应，只需新增一条
  `procurement_suppliers` 行并指向 `external:xxx`，核心机制不变。
- **边界显式记账**：进口走 `external_goods_inflow`（库存资产 ↔ imports-equity），
  与货币的 `external_inflow` 平行，未来可加真实价格/汇率/贸易条款。
- **守恒与可审计**：采购订单、库存流水、账本交易都可下钻（对应 roadmap §3.6.5A 跨尺度桥）。

## 后续（本仓库下一步）

1. **真实跨设施物流路径**：目前 restock 采购是“账面采购 + 就地填货架”；下一步让工人
   去供应商/仓库节点取货、携带库存、沿空间边运回目标设施再结算（而非仅按工单就地结算）。
2. **显式排班 + 供应链成本**：给后勤/商家加排班，让补货成本有真实上游来源，驱动服务能力。
3. **真实外部经济体接入器**：在边界上实现真实价格/汇率/贸易条款与多币种。
