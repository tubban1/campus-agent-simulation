你的方向是对的，而且我觉得它比“先定义 1000 多个人格字段，再随机组合一个人”更接近 World2 真正应该做的人格系统。

但我会改掉一个很关键的词：

不是“拿一个人的全部资料去训练一个 Agent”，而是“用真实的人生证据，构建这个人在不同时间点的动态人格模型”。

这两者差别很大。

现在已经有研究证明，“真实个人数据”确实比简单 demographic persona 强。Stanford 的工作给 1,052 个真人做了约两小时深度访谈，再把完整访谈作为 Agent 的依据；Agent 在 GSS 等任务上能较好复现本人的回答。 2026 年的 SPIRIT 也发现，从真实个人文本中提取价值观、世界观、人格等信息，比只给年龄、性别、收入之类的人口统计信息更能保持个体差异。

但另一项 2025 年的大型预注册研究也给了一个很重要的警告：即使给数字孪生大量真人数据，它们和真人在 164 个结果上的平均相关性仍只有大约 0.2，而且数字孪生往往比真人“更稳定、更没变化”。

所以，我不会说：

“数据够多，我们就能复制一个乔布斯。”

我会把目标定义成：

基于可观察的人生证据，建立一个能够在未知情境下，以接近该真人的认知方式、价值取舍和行为倾向作出反应的概率模型。

这就科学很多，也更强。

你说的乔布斯例子，最关键的其实不是“资料多”

假设我们要构建 Steve Jobs Agent。

最简单的做法是把：

传记、采访、演讲、邮件、产品发布会、同事回忆、家庭信息、公司经历……

全部塞进去，然后 prompt：

“你现在是 Steve Jobs。”

这个 Agent 很可能会“很像乔布斯说话”。

但它不一定真的像乔布斯思考。

因为它很容易变成：

Wikipedia + 乔布斯语气模仿器。

真正的人格系统要解决的是另一件事：

为什么这个人在那个时间点，会做那个决定？

我会把 World2 的人格 Engine 设计成 7 层

而且我甚至不太想叫它 Personality Engine。

我更推荐：

Person Model Engine

因为“人格”只是人的一部分。

第一层：Life Evidence

这是最底层，也是最重要的一层。

不要先总结人格。

先保存证据。

比如乔布斯：

1972
Reed College
退学
学习书法
对传统课程兴趣低
与 Wozniak 建立关系

1974
Atari
印度旅行
禅宗影响增强

1976
Apple 创立
角色：联合创始人
关系：Wozniak / Markkula
资源状态发生变化

1983–1985
Macintosh 团队
与 Sculley 冲突
产品控制欲增强
被 Apple 排挤

但每一条都应该不是一句 AI 总结，而是：

Evidence
- event
- timestamp
- source
- source_type
- quote / original content
- participants
- reliability
- direct / second-hand
- contradictions
- confidence

也就是说：

事实和 AI 对事实的解释必须分开。

这和 World2 现在区分 objective world / subjective belief，本质上是同一个思想。

第二层：Life Graph

然后把这些事实连起来。

不是简单 Timeline，而是一张人的人生图谱：

Person
 ├── Events
 ├── People
 ├── Organizations
 ├── Places
 ├── Projects
 ├── Failures
 ├── Successes
 ├── Conflicts
 ├── Ideas
 ├── Skills
 └── Relationships

尤其重要的是关系。

因为一个人的人格不能脱离他跟谁在一起看。

例如：

Jobs ↔ Wozniak
Jobs ↔ Sculley
Jobs ↔ Ive
Jobs ↔ Pixar team
Jobs ↔ Apple board

每个 relationship 都应该随时间变化：

trust(t)
respect(t)
dependency(t)
conflict(t)
power(t)
affection(t)

这样模拟“1984 年的 Jobs 面对 Sculley”和“2004 年的 Jobs 面对 Jony Ive”，不会只是同一个人格 prompt 换名字。

第三层：Dynamic Internal State

这是我认为你原来方案里最需要加强的地方。

人格不是固定参数。

乔布斯 20 岁和 50 岁不是同一个乔布斯。

所以不要：

Steve Jobs
perfectionism = 0.93
risk_tolerance = 0.88
design_obsession = 0.97

然后用一辈子。

应该是：

PersonState(t)

例如：

Values
Beliefs
Goals
Identity
Skills
Preferences
Risk tolerance
Trust model
Status sensitivity
Control preference
Aesthetic standards
Leadership style
Emotional regulation
World model

全部可以随经历改变。

也就是：

经历 → 反思 → 状态更新。

这才是真的“一个人活过来了”。

第四层非常关键：Knowledge Boundary

这一层我认为甚至可能成为 World2 的核心技术壁垒。

假设你模拟：

1984 年 1 月的 Steve Jobs

那么 Agent 绝对不能知道：

1985 年自己会被 Apple 赶出去
NeXT 会发生什么
Pixar 后来会成功
1997 年会回 Apple
iPhone 会出现

哪怕数据库里已经有这些资料。

否则你模拟的不是 1984 Jobs。

而是：

2026 年互联网对乔布斯一生的总结，穿越回 1984 年扮演乔布斯。

这是目前很多历史人物 Agent 最大的问题。

所以应该有严格的：

KnowledgeCutoff = 1984-01-01

然后构造：

JobsState@1984

只允许访问：

events before 1984
memories formed before 1984
relationships before 1984
skills acquired before 1984
beliefs supported before 1984
information actually available to Jobs before 1984

未来不能泄漏给过去。

这会让 World2 的历史人格模拟比普通 Character AI 高出一个层级。

第五层：Memory，不等于数据库

还有一个很容易踩的坑：

“乔布斯经历过这件事”

不等于

“乔布斯现在会想起这件事”。

所以需要：

Life Evidence
↓
experienced event
↓
encoded memory
↓
salience
↓
decay / reinforcement
↓
retrieval

例如有人 30 年前讲过一句话，不意味着每次做决定都会调用它。

真正的人在一个新情境里，只会想到少数相关记忆。

这和早期 Generative Agents 的 observation → memory → reflection → planning 思路是一致的；其中 memory、reflection、planning 对 believable behavior 都有贡献。

但 World2 可以比它更进一步：

记忆必须来自真人的真实生命轨迹。

第六层才是 Decision Engine

这一步才让“人格”真正有价值。

不要问：

“乔布斯会说什么？”

而应该问：

“在这个情境下，1984 年的乔布斯看到了什么，他想要什么，他会想起什么，他认为有哪些选择，然后他更可能选择什么？”

我建议决策 pipeline 是：

Situation
↓
Perception
↓
What this person knows
↓
Relevant memories
↓
Current goals
↓
Values / beliefs
↓
Relationship context
↓
Capabilities
↓
Candidate actions
↓
Trade-offs
↓
Action probability distribution
↓
Chosen action

注意最后不是：

Jobs will choose A

而应该是：

A  54%
B  28%
C  13%
D   5%

因为真人本来就不是确定性函数。

同一个人睡眠不足、刚吵完架、刚拿到一个新的信息，可能做不同选择。

这个随机性不是 bug。

这是 human fidelity 的一部分。

第七层：Language / Style 最后才做

这一点可能有点反直觉。

很多“数字人”项目一上来就 Fine-tune：

乔布斯怎么讲话。

我会反过来。

先做：

怎么判断。

然后：

怎么行动。

最后：

怎么表达。

所以：

Person Model
    ↓
Decision Policy
    ↓
Language Style

而不是：

乔布斯说话风格
=
乔布斯人格

语言 style 可以最后用 LoRA / prompt / examples / voice model 做。

不要拿语言风格承担人格建模。

所以我不会为每个人真的“训练一个大模型”

这一点很重要。

如果 World2 以后有：

10 万真人
100 万真人
1 亿真人

你不可能：

每个人 Fine-tune 一个 LLM。

而且也没必要。

我更推荐：

                 Foundation Model
                       │
              Person Model Engine
                       │
      ┌────────────────┼─────────────────┐
      ↓                ↓                 ↓
 Life Evidence     Dynamic State      Memory
      ↓                ↓                 ↓
  Life Graph       Values/Goals      Retrieval
      └────────────────┼─────────────────┘
                       ↓
                Decision Engine
                       ↓
                 World2 Agent

也就是说：

共享 Intelligence

一个强 Foundation Model。

独立 Person Model

每个人都有：

person/
  evidence/
  timeline/
  relationships/
  memories/
  beliefs/
  values/
  capabilities/
  preferences/
  state_trajectory/
  language_style/

这样才可能 scale。

那“训练”应该发生在哪里？

我觉得可以有三种训练。

第一种：Inference。

从真实资料自动抽取：

事件
关系
能力
价值观
信念
偏好
变化点

这是最重要的。

第二种：Calibration。

比如我们已经知道 Jobs 过去做过 500 个重要决策。

拿前 400 个构建人格。

把后 100 个藏起来。

然后问：

在当时的信息条件下，Agent 会怎么选？

看它是否能预测真人。

这个设计非常关键。

第三种：Behavior Adapter。

如果积累了很多这个真人的：

situation → decision

数据，再训练一个小的 person-specific adapter / embedding / policy layer。

这时候 LoRA 之类才可能有意义。

但它应该是增强 Person Model，而不是代替 Person Model。

最重要的实验，我建议你这么做

这其实可能直接变成 World2 的一篇论文。

假设乔布斯有：

1970–2011 全部资料。

先构造：

Jobs@25

只给 25 岁以前的信息。

然后让他经历真实历史里 25–30 岁发生的情境。

预测他的行为。

再：

Jobs@35

只给 35 岁以前的信息。

预测 35–40。

再：

Jobs@45

预测 45–50。

于是你得到：

Historical reconstruction
       ↓
Temporal holdout
       ↓
Behavior prediction
       ↓
Real Jobs outcome
       ↓
Compare

这个比问：

“你觉得你是多么像乔布斯？”

科学性高太多了。

甚至可以做非常漂亮的 Ablation

例如：

A
只有 demographic。

B
Biography summary。

C
全部 RAG 文档。

D
Timeline。

E
Timeline + relationship graph。

F
Timeline + relationship + dynamic beliefs/values。

G
完整 Person Model Engine。

然后比较：

谁最能预测历史上真实的人下一步怎么做。

这会真正回答你的问题：

“这些真实数据训练出来的 Agent，能不能代表真人？”

而不是凭感觉说“挺像”。

但这里还有一个更深的问题

我会非常谨慎地区分三个目标：

Level 1 — Mimicry

他说话像乔布斯。

容易。

Level 2 — Behavioral Twin

面对已知类型的情境，他的选择接近乔布斯。

已经很难。

Level 3 — Generative Person Model

**面对乔布斯从来没遇到过的新世界，他仍然表现




Life Event Ledger——人生事件账本。 不先写“他是完美主义者”，而是尽可能保存原始证据：1985 年发生了什么、他和谁发生冲突、他说了什么、最后做了什么、结果是什么。每条数据都带时间、来源、可信度，以及这是本人陈述、第三方陈述还是系统推断。
Temporal Self Model——时间化的人格状态。 在任意年龄生成一个 snapshot，包括当时的价值观、信念、目标、恐惧、能力、知识、社会身份、财富、健康、声望和当前关系。非常重要的一条规则是：未来信息绝不能泄漏给过去的 Agent。 Jobs@22 不应该知道未来 Apple 会成功，也不应该因为系统知道未来而表现得“命中注定”。
Relationship Graph——关系不是人物简介的一句话。 例如某个人与 Jobs 的关系应该是随时间变化的：角色、亲密度、信任、依赖、冲突、共同经历、未解决事件。很多人类决策其实无法只靠 personality 解释，必须放进“他正在面对谁”这个上下文。
Decision Episodes——真正训练“这个人怎么做决定”。 这可能是最值钱的数据。不要只收集传记，要把历史事件转成类似：当时世界状态 → 他知道什么 → 他有哪些选择 → 他选择什么 → 为什么 → 结果是什么 → 事后怎么看。这样你训练的不是“乔布斯语气模型”，而是一个乔布斯决策模型。
Cognitive Policy——LLM 不负责保存这个人。 我反而不建议第一步把所有资料直接 fine-tune 进一个模型权重里。更好的初始架构是：基础模型 + 时间化状态 + 人生记忆检索 + 关系图谱 + 决策模式 + 动态身体/情绪状态。LLM 负责在这些信息上做当前判断。这样事实可以修正、证据可以追溯、不同年龄可以切片，也不会把 1978 年和 2005 年的 Jobs 混成一个“平均 Jobs”。这部分是我的工程建议，而不是现有论文已经证明的唯一正确方案。
Validation Engine——没有这个，就不能说“代表真人”。 你应该故意藏掉真人的一部分历史，然后让 Agent 预测。比如给 Jobs Agent 所有截至 1984 年的信息，但不给它 1985 年真实发生的关键决策。然后把它放进当时的情境里运行 100 次，看它的决策分布和真人实际选择有多接近。下一批事件继续 holdout。这样才能真正知道是在“复述传记”，还是学到了某种可泛化的个人决策结构。