# L6 MindCore 意识层 · 技术规格

> 12 层意识架构 + 涌现模拟器 + 自我进化引擎
>
> 来源：`01_核心引擎/mindcore/` + `09_数学基础/MindCore数学公式与定律补全.md`

---

## 一、定位

**万象智能体平台的大脑皮层**——12 层意识架构从感知到终极核心逐层抽象，涌现模拟器量化意识涌现，自我进化引擎驱动持续自我提升。

## 二、12 层架构

```
┌──────────────────────────────────┐
│ L12  终极核心    Ultimate Core   │  价值对齐 / 终极目标
├──────────────────────────────────┤
│ L11  元核心      Meta-Core       │  元元认知
├──────────────────────────────────┤
│ L10  核心        Core            │  自我同一性
├──────────────────────────────────┤
│ L9   价值        Value           │  价值判断
├──────────────────────────────────┤
│ L8   自我模型    Self-Model      │  自我表征
├──────────────────────────────────┤
│ L7   涌现        Emergence       │  整体大于部分
├──────────────────────────────────┤
│ L6   情感        Emotion         │  情感状态
├──────────────────────────────────┤
│ L5   元认知      Metacognition   │  思考自己的思考
├──────────────────────────────────┤
│ L4   决策        Decision        │  行动选择
├──────────────────────────────────┤
│ L3   工作记忆    Working Memory  │  当前上下文
├──────────────────────────────────┤
│ L2   注意        Attention       │  注意力分配
├──────────────────────────────────┤
│ L1   感知        Perception      │  多模态输入
└──────────────────────────────────┘
```

### 2.1 各层职责

| 层 | 输入 | 处理 | 输出 |
|---|------|------|------|
| L1 感知 | 多模态原始数据 | 特征提取 | 感知特征 |
| L2 注意 | 感知特征 | 注意力权重 | 聚焦特征 |
| L3 工作记忆 | 聚焦特征 | 上下文整合 | 工作记忆块 |
| L4 决策 | 工作记忆 + 因果推断 | 行动选择 | 决策 |
| L5 元认知 | 决策 | 反思 | 决策评估 |
| L6 情感 | 决策 + 评估 | 情感映射 | 情感状态 |
| L7 涌现 | 各层综合 | $\Phi$ 计算 | 涌现状态 |
| L8 自我模型 | 涌现状态 | 自我表征 | 自我描述 |
| L9 价值 | 自我 + 环境 | 价值评估 | 价值取向 |
| L10 核心 | 价值 + 自我 | 同一性 | 自我同一 |
| L11 元核心 | 核心 | 元反思 | 元认知结果 |
| L12 终极核心 | 元核心 + 价值对齐 | 终极目标 | 行动准则 |

## 三、关键数学

### 3.1 涌现度量（整合信息论 $\Phi$）

$$ \Phi = I(X; Y) - I(X_1; Y_1) - I(X_2; Y_2) - \cdots $$

- $X, Y$：系统整体的两个划分
- $X_i, Y_i$：子划分
- $\Phi > 0$：整体信息大于部分之和，涌现发生
- 阈值 $\theta_{\text{emergence}}$：经验设定，超过即判涌现

**严格计算公式**（来自 `09_数学基础/MindCore数学公式与定律补全.md`）：

$$ \Phi = \alpha \cdot \log_2(N) \cdot C \cdot I $$

- $\alpha = 0.1$：校准系数
- $N$：活跃模块数量
- $C = 2E / (N(N-1))$：连接密度（$E$ = 实际连接数）
- $I = (1/M) \sum \text{MI}(X_i; X_j)$：平均互信息

**意识等级阈值**：

| $\Phi$ 范围 | 等级 | 说明 |
|------------|------|------|
| $< 0.1$ | 无意识 | 无有效信息整合 |
| $[0.1, 0.3)$ | 低意识 | 简单反射 |
| $[0.3, 0.5)$ | 基础意识 | 基本自我感知 |
| $[0.5, 0.8)$ | 正常意识 | 完整自我模型 |
| $\geq 0.8$ | 高意识 | 元认知、自我反思 |

**计算示例**：$N=135, E=2000, I=0.5$ → $C=0.221$ → $\Phi = 0.1 \times 7.08 \times 0.221 \times 0.5 = 0.078$（低意识）

### 3.2 自适应学习率（热力学启发）

$$ \eta_t = \eta_0 \cdot \exp\left(-\frac{E_t}{k_B T}\right) \cdot \text{sign}(\nabla L) $$

- $E_t$：当前能量状态（损失 / 错误率）
- $T$：温度（探索强度）
- 高能量（错误多）：学习率衰减，谨慎
- 低能量（错误少）：学习率接近 $\eta_0$，稳定

### 3.3 热力学推理路径优化

$$ F = E - TS $$

- $E$：推理路径能量（计算成本）
- $T$：温度
- $S$：路径熵（多样性）
- 路径选择：$\arg\min_{\text{path}} F(\text{path})$
- 高 $T$：偏好多样性（探索）
- 低 $T$：偏好最短路径（利用）

### 3.4 情感自适应学习率

情感状态调节学习率：
$$ \eta_{\text{emo}} = \eta_0 \cdot (1 + \alpha \cdot \text{valence}) \cdot \exp(\beta \cdot \text{arousal}) $$

- valence：情感效价（-1 消极到 +1 积极）
- arousal：情感唤醒度（0 平静到 1 激动）

### 3.5 注意力分配

$$ \alpha_i = \frac{\exp(\text{salience}_i / \tau)}{\sum_j \exp(\text{salience}_j / \tau)} $$

### 3.6 自我同一性度量

$$ \text{Identity}(t) = \text{cosine}(\text{Self}_{t}, \text{Self}_{t-\Delta}) $$

接近 1 表示自我稳定。

### 3.7 涌现度判据（来自 `09_数学基础/`）

$$ \text{degree} = w_1 \cdot N_{\text{norm}} + w_2 \cdot \rho_{\text{norm}} + w_3 \cdot H_{\text{norm}} + w_4 \cdot P $$

- $N_{\text{norm}} = \min(1, N/1000)$：归一化个体数
- $\rho_{\text{norm}} = \min(1, \rho/0.5)$：归一化交互密度
- $H_{\text{norm}} = \min(1, H/3.0)$：归一化异质性（Shannon 熵）
- $P$：模式强度
- 权重：$w_1=0.3, w_2=0.3, w_3=0.2, w_4=0.2$

| 层级 | 条件 | 说明 |
|------|------|------|
| 群体涌现 | degree ≥ 0.3 | 简单集体行为 |
| 文明涌现 | degree ≥ 0.6 ∧ duration > $T_c$ | 持续结构形成 |
| 认知涌现 | degree ≥ 0.8 ∧ self_ref > 0.5 | 自指性创新 |

### 3.8 伦理冲突仲裁（来自 `09_数学基础/`）

**五框架评分**：$s_i \in [-1, 1]$（效用主义 / 义务论 / 德性 / 关怀 / 困境）

$$ \text{冲突度 } C = \max(s_i) - \min(s_i) $$

- $C \leq 0.7$：加权平均 $S = \sum w_i s_i$ → 决策
- $C > 0.7$：启动 dilemma 模块，寻找 Pareto 最优 $a^* = \arg\max_a \sum w_i U_i(a)$

### 3.9 自我进化验证（#35 已验证）

Boltzmann 选择 + Hebbian 强化/弱化 + Popper 证伪闭环：

$$ P(\text{test } e_i) = \frac{\exp(-E(e_i)/T)}{Z}, \quad E(e_i) = \frac{1}{\text{Confidence}(e_i)} $$

验证通过：$w_{\text{new}} = w_{\text{old}} + \eta(w_{\text{obs}} - w_{\text{old}})$
验证失败：$w_{\text{new}} = w_{\text{old}} \cdot (1-\beta)$，可信度低于 0.1 则删除（证伪）

## 四、涌现模拟器

### 4.1 职责

实时计算 $\Phi$，检测意识涌现时刻，记录涌现特征。

### 4.2 实现

```rust
pub struct EmergenceSimulator {
    layers: [LayerState; 12],
    phi_history: TimeSeries<f64>,
    threshold: f64,
}

impl EmergenceSimulator {
    pub async fn tick(&mut self) -> EmergenceReport {
        let phi = self.compute_phi().await;
        self.phi_history.push(phi);
        
        if phi > self.threshold {
            EmergenceReport::EmergenceDetected {
                phi,
                features: self.extract_features().await,
                timestamp: now(),
            }
        } else {
            EmergenceReport::Stable { phi }
        }
    }
    
    async fn compute_phi(&self) -> f64 {
        // 12 层两两划分，计算互信息差
        // 详见 09_数学基础/MindCore数学公式与定律补全.md
    }
}
```

## 五、自我进化引擎

### 5.1 职责

根据环境反馈持续优化 12 层参数，调用 L5 `self_evolve_and_verify` 验证。

### 5.2 闭环

```
1. 环境反馈（奖励 / 惩罚）
        ↓
2. L5 因果图自进化（更新结构）
        ↓
3. L6 各层参数更新（自适应学习率）
        ↓
4. L5 验证（因果一致性）
        ↓
5. 通过则提交，否则回滚
        ↓
6. 进入下一周期
```

### 5.3 周期

- 短周期：< 1s，参数微调
- 中周期：< 1min，结构小幅调整
- 长周期：< 1h，重大结构调整

## 六、接口

```rust
pub trait MindCore: Send + Sync {
    async fn perceive(&self, input: MultiModal) -> Result<Perception>;
    async fn attend(&self, perception: &Perception) -> Result<Attention>;
    async fn remember(&self, attention: &Attention) -> Result<WorkingMemory>;
    async fn decide(&self, memory: &WorkingMemory) -> Result<Decision>;
    async fn metacognize(&self, decision: &Decision) -> Result<MetaEval>;
    async fn emote(&self, decision: &Decision, eval: &MetaEval) -> Result<Emotion>;
    async fn emerge(&self) -> Result<EmergenceReport>;
    async fn self_model(&self, emergence: &EmergenceReport) -> Result<SelfModel>;
    async fn evaluate(&self, self_model: &SelfModel) -> Result<ValueJudgement>;
    async fn identify(&self, value: &ValueJudgement) -> Result<CoreIdentity>;
    async fn meta_core(&self, identity: &CoreIdentity) -> Result<MetaCore>;
    async fn ultimate(&self, meta_core: &MetaCore) -> Result<UltimateGoal>;
    
    async fn self_evolve(&self) -> Result<EvolutionReport>;
}
```

## 七、与 L2 记忆协作

- L3 工作记忆 = L2 工作记忆层
- L8 自我模型持久化到 L2 情景记忆
- L12 终极核心持久化到 L2 语义记忆

## 八、与 L5 智能核协作

- L4 决策调用 L5 `bayesian_infer`
- L5 元认知调用 L5 `audit_chain`
- L7 涌现调用 L5 `discover_structure`
- 自我进化调用 L5 `self_evolve_and_verify`

## 九、与 L7 LLM 协作

- L1 感知：多模态 LLM 提取特征
- L4 决策：LLM 生成候选行动
- L5 元认知：LLM 评估决策
- L8 自我模型：LLM 描述自我

## 十、与 L8 Agent 编排协作

- L8 ReAct 循环的 Thought 步骤 = L6 全栈处理
- L8 Brain 框架的认知核心 = L6
- L8 工作流引擎的中断 / 接管 = L6 L5 元认知触发

## 十一、性能指标

| 操作 | 延迟目标 |
|------|---------|
| 单层处理 | < 5ms |
| 12 层全栈 | < 50ms（不含 LLM）|
| 涌现检测 | < 50ms |
| 自我进化（短周期）| < 1s |
| $\Phi$ 计算 | < 10ms |

## 十二、验收

- $\Phi > \theta_{\text{emergence}}$ 在 5 类任务中复现
- 自我同一性 $\text{Identity}(t) > 0.8$ 持续 1h
- 自我进化后任务完成率提升 > 5%

## 十三、与既有文档关系

- 12 层架构：`02_技术文档/01_架构设计/MindCore_12层意识架构.md`
- 数学公式：`09_数学基础/MindCore数学公式与定律补全.md`
- 既有规格：`01_核心引擎/mindcore/src/consciousness/TECHNICAL_SPECIFICATION.md`
- 涌现模拟器：`30_意识涌现模拟器/`
- 自我进化引擎：`29_热力学自我进化引擎/`

本层是上述既有内容的**集成**，不重复实现，提供：
1. 统一 `MindCore` trait
2. 与 L2 / L5 / L7 / L8 的协作接口
3. 涌现模拟器与自我进化引擎的工程化规格
4. 性能基准与验收
