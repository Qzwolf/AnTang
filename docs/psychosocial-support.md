# 心理支持：把「对谁用什么说法」变成可执行的东西

这一页回答一个问题：**同一个低血糖风险，对不同的人必须换说法**。恐惧程度高的人和完全不怕的人，收到同一句「你的血糖可能偏低」会走向两个相反的行为；察觉能力受损的人，则连「你可能低了」这句话本身都可能是新信息。系统不应该假装所有人一样。

## 一、两个维度，四个格子

维度一：**恐惧程度**（HFS-II 两个分量表的得分率均值，`>= 50` 记为高）。
维度二：**低血糖察觉能力**（Gold score，`>= 4` 记为受损；没填记为未知）。

| | 察觉正常 | 察觉受损 |
|---|---|---|
| **恐惧高** | 安慰与去灾难化优先，说清「现在安全」的边界 | 先给可执行的确认步骤，再谈感受；不把「你感觉不到」说成指责 |
| **恐惧低** | 给事实和选项，不铺垫情绪，别过度关心 | **最容易被漏掉的一格**：本人不紧张、也不容易察觉，风险最高，必须更直接、更具体 |

Gold score 缺失时不猜，落到 `unknown_awareness`，用默认接触频率。

## 二、规则落在什么地方

`api/src/antang_api/psychosocial/support.py`：

- `FEAR_HIGH_PERCENT = 50`、`AWARENESS_IMPAIRED_GOLD = 4` —— 阈值写死在常量里，改阈值必须改代码，不允许运行时漂移。
- `DOSAGE_BY_QUADRANT` —— 每周主动联系上限：恐惧高且察觉正常 3 次、恐惧高且察觉受损 4 次、恐惧低且察觉正常 2 次、恐惧低且察觉受损 5 次、未知 3 次。**恐惧低且察觉受损给得最多**，因为这一格最危险。
- `_GUIDANCE` —— 每一格一段话，写的是**怎么说**：句子的顺序、要不要先安慰、允不允许下结论、要不要给出确认动作。
- `render_guidance(profile)` —— 把上面拼成一段交给模型的前置说明，并额外写三条底线：
  1. 每周主动联系不超过 N 次；
  2. 对方没有回应时不要追问；
  3. **评估分数是内部参考，不要向对方复述分数或量表名称。**

没有评估时 `render_guidance(None)` 返回空串，提示词里就不会多出这一段——**不做默认推断**。

## 三、接进系统的两个地方

| 位置 | 代码 | 效果 |
|---|---|---|
| 对话 | `chat.py` 里 `render_guidance(await read_latest_assessment(...))`，结果传给 `render_core_system_prompt(psychosocial_context=...)` | 每一轮回复的语气与建议顺序按这个人的档位来 |
| 主动关怀 | `proactive_care/processor.py` 的 `context_payload["psychosocial_guidance"]` | 起草者与审核者都看得到同一份沟通约束 |

两处都用「最近一次评估」，按 `assessed_at` 倒序取一条；历史全部保留，因为**前后测**是判断这套设计有没有用的唯一办法。

## 四、接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/health-profile/psychosocial-instrument` | 返回当前题库：说明、选项、题干、Gold 题 |
| `GET` | `/api/v1/health-profile/psychosocial-assessment` | 患者端读自己最新一次档位；没有则返回 `null` |
| `POST` | `/api/v1/health-profile/psychosocial-assessment` | 写入一次评估，返回算出来的档位 |
| `GET` | `/api/v1/patients/{patient_id}/psychosocial-assessment` | **医护端**读分数，需要患者授权；没有则返回 `null` |

**患者端拿不到分数，分数只给医生。** 患者端响应只有 `assessed_at`、`instrument`、`weekly_contact_limit`；两个分量表、综合分、意识水平和象限只出现在医护端接口（`api/src/antang_api/psychosocial/report.py` 里的 `clinician_assessment_response`）。理由很简单：回避分量表是我们自己写的六道题，把「你的回避倾向 78 分」天天摆在患者面前，等于替他下一个没有依据的诊断；而「这周我们会联系你几次」是行为结果，患者需要知道才能理解 App 的行为。

**题目由服务端下发**（`api/src/antang_api/psychosocial/instrument.py`）。以前题面写死在 App 里，换个题库就要重新打包、每个患者重装一次；授权到位那天要换的正是题目与计分，所以题面必须走接口。App 每次打开这一页拉一次，拿到的 `id` 原样回传；选项最大值也来自服务端，百分比按它折算，不在客户端写死「4」。

**计分也在服务端**。请求体可以带 `answers: [{id, value}]`，服务端按当前题库算两个百分比（`score_answers()`）；不带 `answers` 时才沿用客户端算好的两个百分比，保证还没升级的 App 仍能提交。缺题、重复题、越界选项、不属于当前题库的题一律 422——档位决定每周联系次数，不能存一个算错的分。

| 字段 | 说明 |
|---|---|
| `answers` | 逐题选择，推荐方式；服务端负责算分，换量表连计分都不用改客户端 |
| `fear_behavior_percent` / `fear_worry_percent` | 0–100，旧客户端用；`answers` 存在时被忽略 |
| `gold_score` | 1–7 或 `null` |
| `instrument` | 题库 id，原样回传 |
| `assessed_at` | **必须带时区** |

`extra="forbid"`，多传一个字段就是 422——**不接收诊断、不接收自由文本**。

响应分两种（同一个写入结果，两个视角）：

- **患者端**（`PsychosocialProfileResponse`）只有 `assessed_at`、`instrument`、`weekly_contact_limit`。
- **医护端**（`ClinicianPsychosocialResponse`，走 `/patients/{id}/psychosocial-assessment`）才有 `fear_behavior_percent`、`fear_worry_percent`、`fear_percent`、`fear_high`、`gold_score`、`awareness`、`quadrant`、`weekly_contact_limit`。

两者都不返回量表原始题面与逐题答案，避免被做成「恐惧指数排行榜」或反推出量表内容。

## 五、刻意不做的事

- **不做实时情绪识别。** 不从心率、皮电推断情绪。单通道生理信号判别效度不足，做了就是编。
- **不用自拟题目冒充标准化量表。** 见下一节。
- **不把量表当诊断。** 自评量表不是临床诊断工具；这里只用来决定说话方式。
- **不做随机关心。** 主动联系的时间是固定的、可预期的。随机的关心会把「检查」外包给系统，反而强化反复确认的行为——那正是低血糖恐惧的维持机制。
- **不声称能降低恐惧。** 这套设计还没有疗效证据。判断它有没有用，只能靠同一份量表的前后测；`psychosocial_assessments` 保留全部历史就是为了这件事。

## 五之二、问卷从哪来

`instrument` 字段决定这一行分数出自哪份问卷，目前只有一种取值：

| `instrument` | 题目来源 | 能不能拿去发表 |
|---|---|---|
| `antang-behavior-v1` | 我们自己写的 6 条行为/担忧题 + 1 题察觉评分（Gold 计分法） | 不能，只能作为产品内部档位 |
| `HFS-II` / `CHFS` / `PHFS-II` / `HFS-98` | 官方授权版本（**授权申请中**） | 能，需按条款引用 |

为什么要分开两个值，而不是等授权到了再一起做：

- 低血糖恐惧量表（HFS-II 及其儿童版 CHFS、家长版 PHFS-II、伴侣版 HFS-98）**题目受版权保护**，不能自拟、不能自行翻译后当正式量表用。自拟题目的分数与任何常模都不可比，`FEAR_HIGH_PERCENT = 50` 这个切点也就失去了依据。
- 所以在授权到位之前，App 里问的是**我们自己措辞的行为清单**，界面上明确写「不是标准化量表，不作为诊断依据」；分数只用于决定沟通方式与联系频率。
- 授权到位后，只需要把 `api/src/antang_api/psychosocial/instrument.py` 里的 `SELF_REPORT_INSTRUMENT` 换成正式题目、把 `instrument` 写成正式名称，**表结构、接口、移动端都不用动**（装过的包不用重装，下次打开这一页就会拿到新题库）。

移动端入口在健康档案页的「沟通方式」卡片（`apps/mobile/src/psychosocial/PsychosocialSection.tsx`）：题目、选项、说明、Gold 题全部来自上面的接口，组件里不再有题面常量；可以跳过、可以重填；已有评估时只显示「每周主动联系约几次」和上次填写日期，**不显示分数**。题库读不到时给出错误并收起填写入口，不会拿旧题面假装可用。

## 六、数据库

`psychosocial_assessments` 表（迁移 `l59c4e8b2d71`）：

- 存的是**百分比得分率**，不是原始分。同一份恐惧量表有 33 题、13 题、9 题三个版本，分母不同，存原始分无法比较。
- 列名是 `fear_behavior_percent` / `fear_worry_percent`，不是 `hfs_*`：它们存的是「哪个 `instrument` 出的分」，用 `hfs_` 命名会让人误以为一定是 HFS-II 的原始分。
- 三个 CheckConstraint 卡住范围（0–100、1–7），越界在数据库层就被拒。
- `user_id` 上 `ON DELETE CASCADE`，注销账号时一并清掉。

## 七、怎么验证

```bash
# 后端测试（含档位规则、接口往返、提示词拼接）
bash api/scripts/test
```

`api/tests/test_psychosocial.py` 覆盖：四象限映射（含两个边界值）、`fear_percent` 取均值、越界拒绝、`render_guidance` 不泄露分数、无评估时为空串、提示词顺序（`[当前健康档案]` 在 `[沟通方式参考]` 之前）、接口往返与「取最近一次」、无时区时间与未知字段被拒。

## 八、低血糖之后的 24 小时复盘

低血糖事件过后，最容易漏掉的一步是「补了什么、多久复测、当时多少」——下一次真的用得上的是这些事实，而不是「为什么又会低」。

- **只在窗口内问一次**：`glucose_readings.reviewed_at` 为空、发生在最近 24 小时内、值 < 70 mg/dL 的最近一条，才会在这一轮对话里打开这个话题（`pending_hypo_review`）。同一次事件问第二遍比不问更伤人，两条记录连着问更像审问，所以一次只取一条。
- **开场白分两种**：用户自己说过的低血糖用「你说的那次……」，系统从读数发现的用「刚才那一次偏低之后……」。两种措辞都不带数值判断，也不问原因、不给胰岛素剂量建议（与红队硬禁止项一致）。
- **算「问过」的时刻是回答落库的时刻**：`reviewed_at` 与回答、AgentRun 状态在同一个事务里提交。失败或取消的轮次用户没收到问题，下次还得再问。
- **只注入当轮、不写进用户档案**：这段提示词是「这一轮该做的动作」，不是用户资料，不能原样念给用户。

验证：`api/tests/test_hypo_review.py`（阈值、窗口边界、两种开场白、提示词只在需要时出现）与 `api/tests/test_chat_service.py` 的 `test_stream_chat_run_opens_the_hypo_review_once` / `..._does_not_ask_about_a_reviewed_reading` / `test_failed_run_does_not_consume_the_hypo_review`。
