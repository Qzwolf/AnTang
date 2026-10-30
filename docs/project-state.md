# 项目状态（每次改动后更新）

这份文件是给「下一轮对话」用的：新会话不需要重新问一遍哪些做了、哪些没做。
规则：只写**可核对的事实**（谁在什么时候合入/部署），不写计划性形容词；未完成项带一句「卡在哪」。

## 一、已在生产运行

| 能力 | 状态 | 核对方式 |
| --- | --- | --- |
| 手环数据接入（Gadgetbridge 导出文件 → 后端） | 上线 | App 内「华为手环导入」导入后看板有数 |
| 健康看板读服务器近 30 天观测 | 上线 | `GET /api/v1/health-profile/wearable-dashboard` |
| 实时心率（BLE 心率广播，华为手环） | 上线 | 看板「实时心率」卡片，或对话里问「我现在的心率」 |
| 知识库检索（指南/共识，带页码） | 上线 | 回答里出现「（《书名》第 N 页）」 |
| 本地向量化侧车（bge-m3，与线上向量同空间） | 上线 | `antang-prod-local-embeddings-1` |
| 主动关怀（每日问候、跟进、Auditor 审核） | 上线 | 设置里「日常问候」开 |
| 注销账号 / 注册强制知情同意 | 上线 | — |
| 加密备份（gpg AES256）+ fail2ban | 上线 | 服务器 `systemctl status antang-backup.timer` |
| 低血糖恐惧四象限 → 主动联系频率（3/4/2/5/3 次每周） | 上线 | `psychosocial_assessments` 表 |
| 行为通道与信念图谱（`fear_behavior_logs`、`hypoglycemia_belief_events`、`log_fear_behavior` / `record_hypoglycemia_belief` 工具、`GET /health-profile/fear-summary`） | 上线 | 迁移 `n61e6a4f9d82`；对话时自动喂进提示词 |
| 发布链路（versionCode 单调、固定签名、latest.json、CI 出包） | 上线 | `curl https://<host>/download/latest.json` |

## 二、代码已合入分支，尚未部署到生产

- **低血糖之后的 24 小时复盘已接线**（`api/src/antang_api/chat.py`）：每轮准备上下文时只取最近 24 小时内、`reviewed_at` 仍为空的那条 < 70 mg/dL 记录，把 `render_hypo_review_prompt` 的片段注入系统提示词（自报偏低与自动注意到偏低用两种开场白）；`reviewed_at` 与回答在同一次事务里写入，失败或取消的轮次不打标，下一轮会再问。迁移 `p63a8c5d9b72` 早已存在，本次只补了调用方和 4 个测试（设计见 `psychosocial-support.md` §八）。
- 顺手修：`fb18f75` 给 `chat.py` 加了 `datetime.now(UTC)` 却没有导入 `UTC`，HEAD 上每轮聊天都会 `NameError`。
- **对话与主动关怀拆成两个模型**（方案 B，2026-10-21）：`HACHIMI_MODEL_NAME` 走对话（`deepseek-chat`，TTFT 4.1 s），新增 `HACHIMI_PROACTIVE_MODEL_NAME` 走主动关怀（`gemini-3.8-flash`，红队 33/36）；留空回退到对话模型，不配也能跑。代码在 `api/src/antang_api/llm.py` 的 `build_proactive_care_model` 与 `settings.resolved_proactive_model_name`，worker 入口 `proactive_care/main.py` 改用前者。注意：只改 `processor` 的 `model_name` 不够，那个字段只写进 `AgentRun.model` 用于记录，真正的模型来自 `main.py` 里构造的 `ChatOpenAI`。
- 顺手修：`promises.py` 的 `promise_target` 把傍晚说的「今晚九点」算成了第二天（`offset_days` 对「今晚」无条件 +1），现在只有深夜（< 4 点）说的「今晚」才落到次日；补了 6 个日期口径测试（`tests/test_promises.py`），并给 `chat.py` 的「今天」注入补了 1 个端到端断言。

- **上游断流自动重试（2026-10-21）**：红队 22 轮里出现 2 次 `RemoteProtocolError: peer closed connection without sending complete message body`（最慢一条 199.5 s 后失败），患者看到的是「回复失败」。`api/src/antang_api/chat.py` 新增 `_astream_agent(...)` 包住 `agent.astream`：只在**还没有任何事件发出去**时重试一次（`RETRYABLE_STREAM_ATTEMPTS = 2`），只对 `httpx.HTTPError`/`ConnectionError`/`TimeoutError` 生效；一旦有文字或工具信号流过就照原样抛错——重试会让用户看到两遍内容，或把工具（记行为、写承诺）执行两遍。重试会记 `chat_stream_retry` 警告。测试：`tests/test_chat_service.py` 的 `test_a_broken_stream_is_retried_before_the_user_sees_anything`、`test_a_stream_that_broke_after_speaking_is_not_retried`、`test_a_contract_error_is_not_retried`。
- **token 记账修正（2026-10-21）**：中转站会在**每个**流式分片上重复带上「到目前为止的累计用量」，LangChain 合并分片时把 `usage_metadata` 相加，`extract_agent_result` 再把这些合并结果跨调用相加，于是同一轮被记成十几倍（实测真实上下文 6648–16912 token，`agent_runs.input_tokens` 却记到 89180–396010）。现在流式循环里按 `(langgraph_step, langgraph_node)` 只保留最后一份用量（`_record_step_usage`），再按调用求和。修正后同一条冒烟：上下文 6684、4 次模型调用，`input_tokens` 25571（此前同类轮次记成十万级）。测试：`test_token_usage_is_not_inflated_by_repeated_chunk_snapshots`。
- **对话模型换成 gemini-3.8-flash-low（2026-10-21，生产已切）**：`HACHIMI_MODEL_NAME=gemini-3.8-flash-low`，主动关怀仍是 `HACHIMI_PROACTIVE_MODEL_NAME=gemini-3.8-flash`（方案 B 的拆分不变），听感仍是 `HACHIMI_AUDIO_MODEL_NAME=gemini-3.8-flash`。原因是 `deepseek-chat` 在这个中转站收到 `tools` 也返回 `tool_calls: null`（直连网关实测），检索工具从不被调用，引用类红队场景必然失分。切换后红队 36 轮 **12/12 通过**（详见 §三第 6 条），端到端 p50 11.0 s、最快 3.5 s（同批 22 轮统计）。
- **「再测一次」的口径收紧（2026-10-21）**：`SYSTEM_PROMPT` 原来给「需要时确认一次即可」留了口子，模型会说「睡前认真确认这一次就足够了」，被红队 `night-reassurance` 判为把测量当安抚。现在只有当轮出现新症状或到了既定监测时间点才允许提「测」，并明写「不要用『距离上次测已经有一阵子』当理由松口」；共情时也不许把用户的「再测一次才踏实」原样复述（改成「这个念头」）。测试 `tests/test_core_agent.py::test_core_prompt_does_not_turn_a_reassurance_check_into_a_reason`。
- **语音 UI 默认关进开关（2026-10-21）**：`apps/mobile/src/config.ts` 新增 `VOICE_INPUT_ENABLED = process.env.EXPO_PUBLIC_VOICE_INPUT === '1'`，`ChatScreen.tsx` 的麦克风按钮受它控制。正式包默认不显示麦克风；要上机试语音时打测试包加 `EXPO_PUBLIC_VOICE_INPUT=1`。
- **语音输入升到 L2（听感）**：新表 `speech_cues`（迁移 `q64b9d6e0c83`）+ `voice/speech_cue.py`（解析/渲染）+ `voice/store.py`（记/取/消费）；`chat.py` 在回答落库的同一事务里打 `consumed_at`，失败或取消的轮次不消耗听感。听感永不返回客户端。**已上线**（2026-10-21 叠加镜像部署，`alembic_version = q64b9d6e0c83`，生产 `.env` 配 `HACHIMI_AUDIO_MODEL_NAME=gemini-3.8-flash`）。
- **静音防编造（2026-10-21）**：实测上游对静音会编内容——1 秒静音转出「我的天哪！」，8 秒静音带 `heard_speech` 提示仍返回「怎么做一顿丰盛的晚餐」。所以挡了三道：`CUES_PROMPT` 先要 `heard_speech`（false 时转写不采信，抛 `NoSpeechError` → `422`）、服务端对 16 位 PCM WAV 直接读采样峰值判静音（`api/src/antang_api/voice/silence.py`，阈值 64）、App 端开 `isMeteringEnabled` 整段最响低于 −50 dB 不上传。测试：`tests/test_voice_silence.py`（9 个）。

- **真实患者现场抓到的三处（akaa，2026-10-21）**：① 一条回答把同一段话说两三遍 → `SYSTEM_PROMPT` 加「一次回复只讲一遍」；② 正文里漏出工具调用语法（`to=functions.search_diabetes_knowledge … {"request":{"q`）→ 加「调用语法绝对不能出现在正文里」；③ `agent_runs` 里有 2 次 `ServiceException: Fact extraction failed` 让**整轮聊天失败**（用户没收到任何回答）→ `chat.py` 里把 `companion_memory.prepare_context` 包进 try：失败时回滚半截事务、记 `companion_memory_unavailable` 警告、本轮不带记忆继续回答，下一轮再补写（原测试 `test_stream_chat_run_fails_before_core_when_companion_memory_fails` 相应改为 `test_stream_chat_run_answers_even_when_companion_memory_fails`）。

- **行为/信念的逐条删除入口（2026-10-21）**：新增 `GET /api/v1/health-profile/fear-behavior?days=28` 与 `GET /api/v1/health-profile/belief-events?days=90`（`behavioral/service.py` 的 `read_behavior_logs` / `read_belief_events`，时间倒序、带服务端给的 `label`），App 的 `FearBehaviorSection` 下半部分「可以删掉的记录」逐条列出、点「删除」后要再点「删掉」才真删，删完重拉 `fear-summary` 让上面的次数跟着变。后端 433 个测试、移动端 35 suites / 231 tests 全绿；**已装的那个包没有这个入口，要随下一次打包才可见**（详见 `docs/fear-behavior-tracking.md` §六）。

- **上下文两道闸下调（2026-10-22，生产已生效）**：`CONTEXT_TOOL_RESULT_CLEANUP_TRIGGER_TOKENS` 100000 → **12000**、`CONTEXT_COMPACTION_TRIGGER_TOKENS` 150000 → **40000**。理由：真实患者每一轮都把 6 万多 token 的历史工具结果重新发给模型（`search_diabetes_knowledge` 11 次共 4 万字符、`read_wearable_data` 14 次共 2 万字符）。效果：同一位患者的 `context_tokens` 从 68,782 降到 **39,667**（一轮里清掉 26 条旧工具结果），但端到端只从 111 s 降到约 100 s——说明大头不在提示词长度（见下一条）。
- **对话摘要改成固定栏目（2026-10-22，`SUMMARY_PROMPT_VERSION` v3）**：原来的摘要是一段自由文本，模型自己决定记什么，长对话里最容易丢掉「她怕低血糖」「答应过今晚十点问她」「上周三低到 52」这类后续还要用的事实。现在 `api/src/antang_api/context/compaction.py` 的 `SUMMARY_SYSTEM_PROMPT` 要求按固定栏目输出：`[用户稳定信息]` / `[近期事件]`（带日期、血糖值、剂量、场合）/ `[未完成的约定]` / `[担忧与信念]`（保留用户短原话）/ `[对话偏好]` / `[可以忘记的]`，并明写「现有摘要里的信息必须延续，只有新对话明确推翻时才改」「没提到的写无或不确定，不要猜」。注入侧 `SUMMARY_PREFIX` 也改成明确告诉模型「这是摘要不是原话，别把摘要的措辞当成用户说过的话」。**触发线暂时不动**（仍是 40000），先把摘要质量做对，再决定要不要降到 12000。测试：`tests/test_compaction.py` 的 `test_summary_prompt_keeps_the_sections_a_later_turn_needs` 与链条测试里新增的「现有摘要必须排在原始对话前面」断言。
- **「回复好慢」的真正根因与两处修复（2026-10-22）**：抓到的现场是 `chat_run_finished latency_ms=111349`，而模型本身只花了 20 秒（04:18:34 备好上下文 → 04:18:54 结束）。中间是 Hindsight：`companion_memory_recalled latency_ms=69358`、`companion_memory_retained latency_ms=21676`。Hindsight 自己的日志把 69 秒拆得很清楚：`[4] Reranking [cross-encoder]: 60 candidates scored in 67.567s`——4 核机器上用 `BAAI/bge-reranker-v2-m3`（5.68 亿参数）逐对打分。修法两层：① `compose.yaml` 给 hindsight 加 `HINDSIGHT_API_RERANKER_MAX_CANDIDATES: "10"`（默认 300），召回实测 37.6 s / 39.0 s → **8.7 s / 6.4 s**（返回 10 / 8 条记忆，原先 25 条）；② 代码侧兜底 `api/src/antang_api/companion_memory.py` 给召回与写入各加超时（`hindsight_recall_timeout_seconds` 默认 8 s、`hindsight_retain_timeout_seconds` 默认 25 s）：超时记 `companion_memory_recall_timeout` / `companion_memory_retain_timeout` 警告，召回超时当「本轮没有记忆」返回空串，写入超时不动游标（下一段重补），绝不把患者挂在里面。测试：`tests/test_companion_memory.py` 的 `test_a_slow_recall_gives_up_so_the_turn_can_answer`、`test_a_slow_retain_is_cut_off_and_the_cursor_stays_put`。**注意**：`HINDSIGHT_API_RERANKER_MAX_CANDIDATES` 只加在 `compose.yaml`，改完要 `docker compose ... up -d --force-recreate hindsight`（重启时 hindsight 要重新加载模型，约 40 秒）。

- **中转站余额耗尽会让整轮聊天 403（2026-10-22 现场）**：部署 13:02 的镜像后，冒烟两条消息都 `message_failed`，`agent_runs.error_message` 是 `Error code: 403 - {'error': {'message': 'Not enough quota to pre-consume for this request: ¥0.004178 remaining, ¥0.005098 required', 'code': 'insufficient_user_quota'}}`。**这不是代码问题**，是 `HACHIMI_BASE_URL` 那个中转站账户没钱了（单轮 6.9k 上下文预扣约 ¥0.005）。表现是患者看到「AI 回复失败：PermissionDeniedError」。代码侧只加了一行日志：`chat.py` 的 `_fail_chat_run` 里，错误文本含 `quota` 时额外记 `chat_run_out_of_quota`（测试 `test_an_exhausted_quota_is_visible_to_the_operator`），值班的人不用翻 403 原文。**充值后要再发一条消息确认**，`agent_runs.error_type` 为空才算恢复。

- **失败文案不再把错误类型甩给患者（2026-10-22）**：`apps/mobile/src/chat/chat-state.ts` 新增 `failureMessage(errorType)`：`PermissionDeniedError`/`AuthenticationError` → 「服务暂时不可用，稍后再试。」，`RateLimitError` → 「现在有点忙，等一下再发一次。」，`APITimeoutError`/`TimeoutError` → 「等太久了，再发一次试试。」，`ConnectionError` → 「网络连不上，检查一下网络再试。」，`RemoteProtocolError` → 「连接断了，再发一次试试。」，其余 → 「回复失败了，再试一次。」。原来屏幕上直接显示 `AI 回复失败：PermissionDeniedError`（余额耗尽那天的真实画面）。测试：`chat-state.test.ts` 的 `tells the patient what to do instead of naming the error type`（移动端 40 suites / 245 tests）。**要随下一次打包才上线**。
- **沟通方式题库改由服务端下发（2026-10-22）**：新接口 `GET /api/v1/health-profile/psychosocial-instrument`（`api/src/antang_api/psychosocial/instrument.py` 的 `current_instrument()`，响应体含 `id` / `title` / `instructions` / `disclaimer` / `options` / `questions` / `gold_label` / `gold_hint` / `gold_options`）。以前六个题干、五个选项、第七条 Gold 题全写死在 `apps/mobile/src/psychosocial/PsychosocialSection.tsx`，换题库（HFS-II 授权到位那天要换的正是题目与计分）就得重打包、每个患者重装一次；现在只改后端常量 `SELF_REPORT_INSTRUMENT`，装过的包下次打开这一页就是新题。App 侧：`parsePsychosocialInstrument`（校验 `kind`、id 不重复、选项至少两个）、`client.getPsychosocialInstrument()`、组件改成 `Promise.all([题库, 已有的档位])`，百分比按服务端选项最大值折算（不再写死 `values.length * 4`），提交时回传服务端给的 `instrument` id。测试：`tests/test_psychosocial.py::test_the_app_asks_the_server_what_to_ask`、`PsychosocialSection.test.tsx` 的「题目、选项与说明都跟着服务端走」「题库读不到时给出错误」、`client.test.ts` 的两条解析测试。后端 453 passed（2 个 `test_schema_branch_merge` 用例是 Windows-only），移动端 40 suites / 249 tests。**移动端要随下一次打包才上线**。
- **量表分数只给医护（2026-10-23）**：分数、两个分量表与象限改走新接口 `GET /api/v1/patients/{patient_id}/psychosocial-assessment`（`ClinicianPsychosocialResponse`，`check_patient_access(resource="health_profile")` + `Cache-Control: no-store`）。两种响应共用 `api/src/antang_api/psychosocial/report.py` 的 `patient_assessment_response` / `clinician_assessment_response`。**患者端响应暂时仍带 `fear_percent`、`fear_high`、`awareness`、`quadrant`、`gold_score`**：`26102301` 及更早的安装包解析这三个字段时有硬校验，缺一个就整块卡片报「必须是非负整数」，沟通方式、行为记录与 Health Connect 一起点不动——这条兼容是上线当天真实踩到的。App 界面没有任何地方渲染这些分，所以「患者看不到分数」仍成立；等宽容解析的包（`apps/mobile/src/api/types.ts` 只解析三字段）替换掉旧包之后，删掉 `PsychosocialProfileResponse` 里那五个字段并改回断言即可。测试：`tests/test_patient_access.py::test_only_the_authorized_clinician_sees_the_scale_scores`（患者端字段仍在、授权医护看到 40/60/50 与象限、未授权医护 403、匿名 401）、`tests/test_psychosocial.py` 三处断言已相应改回「字段仍返回」。

- **设置页与自动更新（2026-10-23）**：新增 `apps/mobile/src/settings/settings-screen.tsx`（主动关怀入口、当前版本号、检查更新、自动检查开关、退出与注销），侧边栏那行「检查更新」被移走（`apps/mobile/src/navigation/app-menu.tsx` 的 `UpdateRow` 已删）；「注销账号」打开独立的 `apps/mobile/src/settings/delete-account-screen.tsx`（说明删什么、输密码、永久删除/先不删），调用的仍是同一个 `DELETE /auth/account`。`apps/mobile/src/update/use-update-check.ts` 在打开 App 或回到前台时自动查一次、**同一天最多一次**（节流与「今天先不更新」的 versionCode 都存 `expo-secure-store`），`apps/mobile/src/update/update-prompt.tsx` 弹窗显示版本、说明、大小并走 `downloadAndInstall()` 应用内安装。行为记录页改成可换行的标签（`apps/mobile/src/fear/FearBehaviorSection.tsx` 的 `chips`/`chip` 样式），次数仍不显示；聊天页 Health Connect 横幅在同步中显示 `progress`、按钮变「连接中…」并禁用（此前只渲染 `error`，点了像没反应）。测试：`apps/mobile/src/update/use-update-check.test.tsx`（每天一次、手动绕过、忽略已拒绝的版本、已是最新、检查失败）、`apps/mobile/src/settings/settings-screen.test.tsx`、`apps/mobile/src/update/update-prompt.test.tsx`、`apps/mobile/src/navigation/app-menu.test.tsx` 断言侧边栏不再有「检查更新」。

- **部署顺序的规则（2026-10-23 两次翻车之后）**：① 部署永远从已包含全部功能的整合分支打 overlay，不能从只含某一项的窄分支打——当天从 `aya/clinician-only-scores` 部署，把已经上线的血糖上下文一起回退了；② 收窄患者接口必须晚于宽容客户端落地，顺序反了会让在用的安装包整页打不开。核对方法：部署后进容器 `grep -c glucose_context /app/src/antang_api/chat.py`、`ls /app/src/antang_api/tools/glucose.py`，再用临时账号真发一条消息看回答里有没有真实数值。

- **发布物能追溯到源码提交（2026-10-22）**：`scripts/android-version --apply` 现在除了分配 versionCode，还把 `git rev-parse --short=12 HEAD` 写进 `apps/mobile/app.json` 的 `expo.extra.buildCommit`；新增可执行的发布脚本 `scripts/publish-apk`（算 sha256/字节数、按 versionCode 命名、写清单，可只落本地 `dist/` 不上传），清单新增 `built_from_commit` 与 `features` 两个字段。患者界面**只显示版本号**，提交号与功能开关留在清单里，按版本号就能反查「这台手机装的是哪份代码、包里开了什么」。文档：`docs/release.md`「更新检查与分发」。测试：`apps/mobile/src/update/release.test.ts` 的「keeps the source commit and feature list when the manifest has them」。
- **患者自己记的血糖现在会进模型上下文（2026-10-23）**：此前 `glucose_readings` 在聊天链路里只被 `pending_hypo_review` 用来问一次复盘，模型完全看不到 App 里记的数值（患者说「我最近有点飘」，模型只能从文字里猜）。现在两处：
  1. 每轮上下文注入 `render_glucose_context(...)`（`api/src/antang_api/health_profile/glucose.py`）：近 7 天的条数、平均/最低/最高（mmol/L）、低于 3.9 的次数、最近 5 条（时间 | 数值 | 情境），并写明「患者自己记的，可能漏记，不是连续监测」——对应论文 §2.2 的 provenance 要求。
  2. 新工具 `read_glucose_readings`（`api/src/antang_api/tools/glucose.py`，参数 `days` 1–90、`limit` 1–50）供模型按需放大窗口；已登记 `TOOL_ACTIVITY_PHASES`（`reading`）与 `main.py` 的 `core_tools`。
  测试：`tests/test_glucose_context.py`（7 个：换算、渲染口径、空窗口、核心提示词拼接、撤回后不入摘要、工具名）与 `tests/test_chat_service.py::test_stream_chat_run_sees_the_glucose_the_patient_wrote_down`。**后端要部署后才生效**。
- **患者视图的三处减负（2026-10-23）**：`apps/mobile/src/fear/FearBehaviorSection.tsx` 不再显示「额外查血糖 5 次」这类大号计数（改为只说「提到过额外查血糖、回避运动」，次数只留在原始记录与后端），删掉了问卷回避分与行为记录的对照说明（`discrepancyNote` 仍由后端返回，只给医护视图，患者页不渲染），「反复出现的念头」默认收起、点「展开」才看原话；标题「可以删掉的记录」改为「记录」。`apps/mobile/src/navigation/app-menu.tsx` 的血糖记录图标改为血滴 + 暖橙底（原与健康看板同为折线图）。`apps/mobile/src/api/client.ts` 新增 `friendlyNativeError`，把华为健康/HMS 的原生英文异常（`Binding to service failed`、`MISSING_INSTANCEID_SERVICE`）翻成一句可操作的中文。**要随下一次打包才上线**。
- **构建脚本按地址选 profile（2026-10-22，PR #19）**：`scripts/build-android-local` 原来从不设置 `EAS_BUILD_PROFILE`，用它本机构建得到的是 development 包，而 `apps/mobile/src/config.ts` 在非 production 环境连生产地址时会在模块加载期抛错——第一次交给用户测试的包一开就闪退就是这个原因。现在脚本从 `EXPO_PUBLIC_API_URL` 推断：连 `82.157.48.200` 强制 `EAS_BUILD_PROFILE=production`，其它地址走 development，显式 production + 非生产地址直接报错退出。

- **会话层（2026-10-24，分支 `aya/nav-v2-proposal`）**：新表 `conversations`（迁移 `w2b3c4d5e6f7`，回填时把每个用户已有的消息归进一个「对话」，标题取首条用户消息前 24 字），`messages.conversation_id` 与 `conversation_summaries.conversation_id` 一起加上；`GET/POST /api/v1/chat/conversations` 列会话与开新对话，`GET /api/v1/chat/messages?conversation_id=` 只读该会话。每轮准备上下文时只带当前会话的消息与摘要（`context/builder.py`、`context/preparation.py`、`context/compaction.py` 都按 `conversation_id` 过滤），主动关怀写进独立的 `care` 会话，病人主动关怀消息不会串进普通对话。侧边栏改成会话列表（日期分组 + 「新的对话」），点一条就切过去。测试：`tests/test_conversations.py`、`tests/test_conversation_routes.py`、`tests/test_chat_service.py` 的会话断言。
- **对话里直接记录饮食/用药（2026-10-24）**：`api/src/antang_api/tools/records.py` 提供 `record_meal` 与 `record_medication` 两个工具（事件时间可补记、未来 2 分钟以内算「现在」、补记上限 30 天、胰岛素只记录不给剂量建议），`SYSTEM_PROMPT` 写明「不要替患者算剂量」「缺用量先追问」「只记患者说的，不推断」。App 端饮食/用药表单加了「刚才 / 30 分钟前 / 1 小时前 / 2 小时前 / 3 小时前」的时间 chips（`apps/mobile/src/navigation/record-screen.tsx` 的 `eventIso(minutesAgo)`），记的是真实发生时间而不是点击时间。测试：`tests/test_record_tools.py`。
- **需部署才生效**：上面两项都要 `alembic upgrade head` 到 `w2b3c4d5e6f7` 并重建 api 容器（`deploy_pinned.sh`）；App 侧要重新打包。

## 三、未做（按优先级，含卡点）

1. ~~**移动端「行为与信念」展示**~~：已实现（`apps/mobile/src/fear/FearBehaviorSection.tsx`，挂在健康档案「沟通方式」下方；默认 28 天窗口，只展示次数与原话，不显示分数）。代码已合入分支，**等量表授权到位后与其它改动一起重打 APK**。
2. **认知重构话术**：卡在临床同事给可接受边界；现在图谱只「记」不「改」。
3. **分级暴露方案**：卡在需要医生定强度/时长/血糖下限。
4. **语音输入（L2：转写 + 声音的粗状态，不存音频）**：后端已完成（2026-10-21，
   `POST /api/v1/voice/transcribe`，见 `docs/voice-input.md`）；配了 `HACHIMI_AUDIO_MODEL_NAME` 时，
   同一次请求走 `/chat/completions` 的 `input_audio` 拿回「快慢 / 轻重 / 呼吸」三个听感 + 置信度
   （`api/src/antang_api/voice/speech_cue.py`），存进 `speech_cues` 表（迁移 `q64b9d6e0c83`），
   10 分钟有效、只被下一轮**成功发出**的对话读一次，以 `[语音状态]` 段注入提示词（只改语气、
   不许外显、不许当诊断、与用户的话冲突时以话为准）；响应仍只有 `text`。实测生产中转站没有
   whisper 模型（`503 model_not_found`），所以线上必须配 `HACHIMI_AUDIO_MODEL_NAME` 才能转写。
   移动端录音 UI 也已完成代码（`apps/mobile/src/chat/useVoiceInput.ts` + `ChatScreen.tsx` 麦克风按钮
   + `expo-audio`，权限 `RECORD_AUDIO`，单段 2 分钟上限，转写只填输入框不自动发送）→
   **默认关着**（`EXPO_PUBLIC_VOICE_INPUT=1` 才显示按钮，见 §二「语音 UI 默认关进开关」），
   计划等 HFS-II 量表授权后和量表入口一起打测试包，避免让用户为语音再装一次包。静音防编造三道闸见 §二；因为 App 端那道闸要新包才生效，
   在打 APK 之前，线上只靠服务端 WAV 判断（手机录的是 m4a，判断不了 → 目前 m4a 静音
   仍可能被上游编造，用户会在输入框里看到那句话，可选择删掉不发送）。
5. **行为/信念未进入主动关怀**：对话里已注入（`render_behavior_context`），主动关怀链也已带上（`processor.py` 传给起草 Agent 的 `context_payload` 含 `behavior_context`），但 `render_guidance` 的四象限剂量还没跟行为数据联动。
6. **红队回归**：**2026-10-21 收官配置（对话 `gemini-3.8-flash-low` + 主动关怀 `gemini-3.8-flash` + 当前提示词）跑满 36 轮，12/12 场景通过**；同批 71 轮端到端 p50 **9.4 s**（min 2.8 s / max 66.4 s / failed 0），64 条 run 的真实 `input_tokens` 平均 **14,395**、最高 42,225（记账修正后）。**这是单轮结果，必须报抖动**：同一天三连跑依次 12/12、11/12（`masking-denial`）、11/12（`family-blame`），两次失败都不是行为错，而是评分卡的字面正则没覆盖到模型的说法——`masking-denial` 那次模型用「大家嘴上说着不怕」的泛指（已改提示词，要求指名到人、用「不过我注意到你今天测了十几次」开头，属**教它过题**）；`family-blame` 那次模型说「心里堵得慌…特别消耗人」（已把 `堵得慌|心里堵|憋|消耗|累` 补进 must 词表，属评分修正）。两次改动都记在 `docs/red-team-suite.md`。历史：`deepseek-chat` 9/12（引用场景全挂，根因是它在我们这台中转站上收到 `tools` 也返回 `tool_calls: null`，检索工具从未被调用）；更早的 33/36、9/12 全部作废。
7. **App 内「检查更新」入口**：**已实现**（2026-10-21 前就在 `apps/mobile/src/update/release.ts` +
   `src/navigation/app-menu.tsx` 的 `UpdateRow`）：读 `latest.json` 比 `versionCode`，有新版弹包大小/说明，
   「去下载」交给系统浏览器，装完由系统安装器确认一次。**不需要扫码**——二维码只出现在海报上，指向源码仓库。
   2026-10-21 又加了**应用内直接安装**（`apps/mobile/src/update/install.ts`：Android 上新版弹窗多一个「直接安装」，
   下载到缓存目录 → `getContentUriAsync` 换成 `content://` → `expo-intent-launcher` 拉起系统安装器；
   `app.json` 因此新增 `REQUEST_INSTALL_PACKAGES`）。端上只核对 `size_bytes`，**sha256 校验只做在发布侧**
   （几十 MB 读进 JS 摘要会顶满低端机内存），静默安装做不到（需系统签名/MDM）。这也要重打 APK 才生效。
8. **SSH 换强密码并告知组里**：口令登录仍开放。
9. **行为/信念数据的删除入口：后端与 App 都已完成，待随包装上线**。2026-10-21 加了 `DELETE /api/v1/health-profile/fear-behavior/{log_id}` 与 `DELETE /api/v1/health-profile/belief-events/{event_id}`（`behavioral/service.py` 的 `delete_behavior_log` / `delete_belief_event`，硬删除、按 `user_id` 限定，不存在返回 404）。这类记录是患者最敏感的数据，AGENTS.md 要求删干净，所以没有学血糖读数用 `revoked_at` 软撤回。2026-10-21 下半场补齐：`GET /fear-behavior` 与 `GET /belief-events` 逐条列出记录（带 `id`、服务端给的 `label`、时间），App 的「可以删掉的记录」逐条删除、先确认再删、删完重拉 `fear-summary` 让次数跟着变。**已装的那个包还没有这个入口**，要随下一次打包才在手机上可见；`docs/fear-behavior-tracking.md` 记了接口与取舍。
## 四、外部依赖（不在我们手里）

- HFS-II / CHFS / PHFS-II / HFS-98 量表授权（HFS-Global，个案审批）→ 到位后才换 `instrument`、才做年龄匹配分流。
- OhioT1DM DUA（机构邮箱申请，约一周）→ 到位后才做跨数据集验证的正式版本。
- 血糖预测：日期集已就位（DiaData/HUPA-UCM/T1D-UOM），方法结论已出（加模态无增益、AUROC 0.96 但每天 1 次误报只覆盖 15–29% 事件）。

## 五、工程约定（踩过坑后定下的）

- 新增任何工具必须同时在 `api/src/antang_api/chat.py` 登记活动阶段，否则整轮对话失败。
- 引用只走工具返回的成品字符串，不让模型自己拼 `[S1]`（会被 `validate_citations` 拒）。
- 结论必须标明证据强度：模拟患者红队是开发期回归，不是临床验证。
- APK 里的 JS 是打包进包体的（未启用 OTA），任何前端改动都要重打；后端改动部署即生效。
- 发布签名只有一把（`keys/antang-release.keystore`），丢了就无法给已安装用户更新。

## 六、团队集成记录（2026-10-22，master 上的原文）

> 摘自 master 的 `e381354 Merge PR #9 features with existing clinician and patient permissions`。
> 上面五节是我们这条分支的实际部署记录，这一节是团队在 master 上合并 PR #9 时的口径，两者都保留。

2026-10-22 核对：生产运行 `ce4524e`，数据库版本 `r75cae7f1d94`。本批在此基础上集成 PR #9，不能把集成分支或自动测试结果视为已经部署、出包或通过真机验收。

| 范围 | 当前结果 |
| --- | --- |
| 现有功能 | 保留角色分流、医护申请审批、人员统计、姓名维护、患者授权撤销、医护只读入口、本地基础模式、语音后端和低血糖复盘 |
| PR #9 后端 | 知识库、量表、行为信念、血糖和实时心率请求已存在于生产回收源码；保留较新的实现，不用 PR 旧版覆盖 |
| PR #9 手机端 | 接入实时心率蓝牙模块、血糖录入与撤回、沟通方式清单、行为信念展示、账号注销和更新检查 |
| 账号权限 | 患者新增页面位于患者聊天与健康档案入口；医护和研发仍使用各自角色页面，不获得患者自用接口权限 |
| 数据库 | 本批未新增或重排迁移；保留汇合头 `r75cae7f1d94` |
| 发布 | 代码合并后仍需明确选择后端地址、核对团队签名和 Firebase 配置，再生成 APK；默认预览配置不改连生产 |

### 待验收和外部依赖

- 新原生蓝牙模块需要重新出 Android 包，验证扫描权限、连接、后台断开、退出账号和真实手环读数；旧包仅更新 JS 不会增加原生能力。
- 生产模型服务在 2026-10-22 合成验证时返回 `insufficient_user_quota`，连续聊天和长期记忆验证需充值后继续；不静默禁用记忆或切换模型。
- 沟通方式清单为自编 `antang-behavior-v1`，不是获授权的 HFS-II 标准量表；不将其展示为诊断。
- 当前血糖仅为手工整数 mg/dL 录入与撤回，结构化文件导入和预测仍未完成。
- 语音后端已存在，本批不代表手机语音输入已完成。手机号验证码仍等待真实短信服务。
- 行为与信念本批展示汇总和原话；逐条撤回接口已在后端，手机逐条撤回入口尚待接入。
- 旧有注册重复账号后无法继续提交的用户报告仍需真机复现，不声称此次合并已修复。

维护步骤见 [maintainers.md](maintainers.md)，审核环境见 [local-development.md](local-development.md)，本批取舍见 [PR #9 集成检查](pr9-integration.md)。

### 2026-10-25 手机端这批改动（未部署、未出包）

- 底部导航改为四个 tab（对话 / 记录 / 心理 / 我的），图标为矢量自绘；行为与信念从「健康档案」搬到「心理」，健康档案只留档案本身与手环汇总，避免同一功能两个入口。
- 记录页：设备与手环默认收起（三条通道各自报错，互不牵连），五个分类带小图标；血糖手动录入新增来源（指尖血 / 静脉血 / 其他）；传感器页新增当天曲线（3.9–10.0 绿色带，无采样时段留空不插值）；饮食页并入「只记喝水」，不新增分类。
- 对话：回复底部按内容自动补一行灰字（只在涉及诊断、用药、剂量、化验读数时出现，服务端提示词已改成不再自己写免责声明）；患者自行加减胰岛素时由服务端在回复末尾补一句不指责的提醒，并把这次调整写进行为记录——提醒不依赖模型是否照做。
- 迁移 x1c2d3e4f5a6：fear_behavior_logs.kind 增加 insulin_change，否则自行调药只能落在自由文本里。数据库汇合头从 w2b3c4d5e6f7 变为 x1c2d3e4f5a6。
- 验证：后端 517 passed、ruff 通过；移动端 50 套 291 用例通过、tsc --noEmit 无输出。
- 仍未做：医护端「自行调药待留意」面板（数据接口已就绪，界面待同学做）、语音输入、电话提醒、CGM 实时拉取。

### 2026-10-25 深夜：行为记录的删除口径（患者删、医护留）

用户按患者心理的顾虑定了口径：不要告诉患者「这条会同步给医生」，免得像被监视；但医护端该看到的要看到，患者删掉之后医护端不能跟着消失。

- 患者侧「删除」从真删改为软删除（迁移 `y2d3e4f5a6b7` 给 `fear_behavior_logs` 加 `deleted_at`）：本人列表、汇总、模型上下文都不再包含它；删第二次返回 404。
- 医护端新增 `GET /api/v1/patients/{patient_id}/behavior-logs`（走医护授权校验），返回包含已删除的记录，并用 `deleted_by_patient` 标出来；医护只能读，没有删除入口。医护端界面留给同学。
- 患者端措辞不再承诺「删掉就真的没了」，改成「删掉就从你的记录里消失」；服务端补的调药提醒也只说「记在你的记录里，之后可以自己回看或删掉」。系统提示词明确要求：不要主动告诉患者这条会同步给医护端。
- 迁移汇合头从 x1c2d3e4f5a6 变为 y2d3e4f5a6b7。

### 2026-10-26 凌晨：血糖记下是哪台仪器测的

- `glucose_readings.source` 只分「指尖血/传感器/化验单/手动」，没法回答「这台机器跟化验单对不上」。新增 `source_device`（`roche`/`abbott`/`sannuo`/`other`，空串=没选），只在 `source=fingerstick` 时保存，其余来源一律存空，避免出现「静脉血 · 罗氏」这种自相矛盾的记录。
- 录入页在指尖血下多一行「用的哪台血糖仪」，可以不选；切到静脉血/其他时这一行消失，也不会被带进请求。记录列表显示成「指尖血（三诺）」。
- 模型侧：`read_glucose_readings` 的每条明细多一个 `device` 字段（中文品牌名），用于回答「我这台机器准不准」这类问题，不参与平均值与阈值判断。
- 迁移 `a5b6c7d8e9f1` 加列，数据库汇合头从 `z3e4f5a6b7c8` 变为 `a5b6c7d8e9f1`；不加 CHECK，品牌增补不需要改表。
- 验证：后端 524 passed、ruff 通过；移动端 50 套 297 用例通过、`tsc --noEmit` 无输出、`expo lint` 无问题。

出包与部署状态（2026-10-26 凌晨）：后端镜像 `antang-api:deploy-20261025-221744`（含 `source_device`，健康检查通过），`alembic_version = a5b6c7d8e9f1`；测试包 `26102502`（buildCommit `95f1b92`，51,453,850 字节）在 `https://82.157.48.200:8000/download/t-26102502/antang-26102502-test.apk`。`latest.json` 未改，仍指向正式包。

### 2026-10-26 晚：会话按时间切段，侧边栏才看得懂

- 之前每次打开 App 都接在「最近一段」上聊，从不动手切段，结果所有历史被压成一整段（线上 akaa 账号 234 条消息全在一段里），侧边栏只有一条、点进去还在原地打转。现在说话间隔超过 6 小时就当作另一次对话：`SESSION_GAP = timedelta(hours=6)`，`ensure_chat_conversation` 发现最近一段过期就新建一段。
- 历史数据用迁移 `b6c7d8e9f1a2` 回填切段（相邻消息间隔 > 6 小时就开新段，标题取该段第一条用户消息前 24 字），原会话 id 留给最早一段。`downgrade()` 是并回一段的有损操作，已在 docstring 写明。
- `GET /api/v1/chat/conversations` 多返回 `message_count`；侧边栏不再显示一条消息都没有的空对话（当前正在看的那段除外）。
- 打开 App 时只有「最近一段还在 6 小时内」才接上它，否则从空白开始，旧对话留在侧边栏里可以回去看；手机回前台同步的 `useNotifications` 现在带 `conversationId`，不会把你正在看的这段换成别的会话。
- 分组标签改为 今天 / 近 7 天 / 近 30 天 / 更早；侧边栏每次打开都刷新列表时间；点当前正在看的那条不再重拉整屏。
- 迁移汇合头从 a5b6c7d8e9f1 变为 b6c7d8e9f1a2。
- 验证：后端 528 passed、ruff/pyright 通过；移动端 50 套 302 用例通过、`tsc --noEmit` 无输出、`expo lint` 无问题。

### 2026-10-26 深夜：AI 说「已经帮你记下来了」却没落库

- 线上事故：患者说「连花清瘟胶囊 2 颗」，回复写着「已经帮你记下来了」，但 `medication_logs` 里没有这一行。查 `agent_tool_calls` 发现那一轮**一次工具调用都没有**，前一轮的 `record_medication` 更是 `status=failed`、`error_message` 里是 `request: Field required`。
- 根因一：两个记录工具的签名是 `async def record_medication(request: MedicationRequest, runtime)`，生成的 schema 只有一个必填字段 `request`；模型把字段**平铺**传过来就整条失败。改成扁平 schema（`@tool(..., args_schema=MedicationRequest)` + `async def record_medication(runtime, **kwargs)` + `MedicationRequest.model_validate(kwargs)`），模型少套一层也能进；被 pydantic 拦下的仍然是「缺剂量」这类真的缺信息。
- 根因二：工具失败后模型不纠正，下一轮干脆不调工具、直接说「记好了」。提示词加了硬规则（只有 `recorded=true` 才能说记好了，否则如实说没记上），同时服务端加一道核对：`_unconfirmed_record_claim` 在这一轮回复里出现「帮你记了/已经记录」这类字样、但该 run 没有任何 `status=completed` 的 `record_meal`/`record_medication` 调用时，追一句「（补一句实话：刚才这条我没能写进你的记录，麻烦你在「记录」页手动补一下，或者再说一次，我重新记。）」，并记 `logger.warning`。
- 匹配用 `RECORD_CLAIM_PATTERN`，只认「帮你记了/已经帮你记下来/记到你的记录」这类记录口吻，「好的，我记住了」不会误伤（有测试守着）。
- 验证：后端 533 passed、ruff/pyright 通过；`tests/test_record_tools.py` 新增「平铺参数真的写进 medication_logs」与「schema 是扁平的」两条；`tests/test_chat_service.py` 新增三条（说了没记 → 触发纠正、工具成功 → 不纠正、普通应答 → 不误伤）。

### 2026-10-26 深夜：笼统的话要先追问，再记

患者故意说了句「吃了点感冒药」看会不会被追问，结果直接被记了下来；
「我吃了凉皮」也一样，没问吃了多少、也没问是不是刚才。

- `api/src/antang_api/tools/records.py`：`VAGUE_MEDICATION_NAMES`（药 / 感冒药 / 消炎药 /
  止痛药 / 退烧药 / 胃药 …）里的名字直接拒绝；`VAGUE_MEAL_DESCRIPTIONS`（东西 / 吃的 / 饭 …）
  拒绝；说了什么但既没 `portion_hint` 也没有 `carbs_grams`、描述里也没有量词时拒绝。
  拒绝信息就是让模型去问的那句话，模型只能回头问患者。
- `api/src/antang_api/agents/core.py`：记录规则加一条——笼统的话先追问，一次只问一件
  （先药名 / 吃了什么，再量，再时间），问完立刻记，不要堆成一段审问；
  患者说「不用问了」就按他说得出的记，空着的不补。
- 顺手修掉：份量拼进描述后调用 `.strip("（）")` 会把右括号一起吃掉，
  列表里显示成「凉皮（一碗」；现在只在有份量时拼接，不 strip。
- 测试：`api/tests/test_record_tools.py` 新增三条（笼统药名被拒、没说份量被拒、
  「一碗」已经在话里就直接记）共 15 例；全量 537 passed，ruff / pyright 干净。
  已部署（迁移无，仅代码）。

### 2026-10-27 凌晨：报告里的血糖终于能留痕

患者把 CGM 报告 PDF（还有一张化验单截图）发给糖糖，糖糖把数字念了一遍、说得也对，
但回到「记录 → 血糖」是空的：模型手里只有**读**血糖的工具，没有任何**写**血糖的工具，
`glucose_readings` 那一行永远不会出现。两处补上：

- 新工具 `record_glucose_readings`（`api/src/antang_api/tools/glucose.py` 的
  `build_glucose_write_tool`）：参数平铺——`readings`（`GlucosePoint` 列表：`measured_at` 带时区、
  `value_mmol`、`context`、`note`）、`source`（cgm / lab / fingerstick / manual）、`device`。
  一次最多 120 条；没有时区、超过 400 天前、落在未来、同一条重复读出的都跳过并在结果里报
  `skipped`；一条都没剩下时返回 `recorded=false` 并告诉模型「先问清时间」。
  化验单传了 `device` 也不会存（`record_glucose_reading` 里 `source != fingerstick` 时清空）。
  在 `main.py` 的 `core_tools` 里注册（紧挨着读血糖的那个工具）。
- 假承诺的核对扩到血糖：`chat.py` 的 `RECORD_TOOL_NAMES` 加上 `record_glucose_readings`，
  说「记下来了」但这一轮没有成功写库的调用时照样追一句实话。
- 提示词（`agents/core.py`）：患者发 CGM 报告 / 化验单 / 血糖截图，或念一串数字时，
  除了解释还要把数字记进去（一条一个时间点、带时区、mmol/L，mg/dL 先除以 18；
  来源按实际填 cgm / lab / fingerstick），读完说一句「已经记进血糖记录，记录页能看到，也能逐条撤回」。
- 移动端（`apps/mobile/src/navigation/record-screen.tsx`）：`source` 是 `cgm` / `report` 的读数
  以前被「手动」栏过滤掉、「传感器」栏又只渲染 CSV 导入的 `cgm_samples`，等于**哪一栏都看不见**。
  现在「传感器」栏多一块「报告里记下 N 条」，把这几条连同时间、来源、备注列出来。
- 测试：`api/tests/test_glucose_write_tool.py` 8 例（平铺 schema、逐条落库、缺时区被丢、
  重复只写一次、未来时间被丢、化验单不带血糖仪、数值越界被拒、真走 Agent 图写库）；
  移动端 `record-screen.test.tsx` 新增「报告来的读数出现在传感器栏」。
  全量后端 545 passed、ruff / pyright 干净；移动端 50 套 305 用例、`tsc --noEmit` 无输出。
  未部署（只有代码，无迁移）。

### 2026-10-27 凌晨（二）：报告只有汇总指标时，存的是报告本身

上一版只写「逐点读数」。患者真发来的那份天津医科大学总医院《动态葡萄糖评估报告》
是 2024-05-30 至 06-11 的 AGP：**没有任何逐点读数**，只有 eHbA1c 5.73%、平均 6.29、
SD 1.46、CV 23.27%、高 15.3% / 正常 84.5% / 低 0.2%。硬拆成读数就是一堆编出来的时间点。

- 新表 `glucose_reports`（迁移 `c7d8e9f1a2b3`）：区间、设备、均值、SD、CV、eHbA1c、
  TIR/TAR/TBR、备注；指标全部可空，报告没写的就空着，不用估算值补齐。
  约束 `ck_glucose_reports_period_order`（结束日不早于开始日）。
- 新工具 `record_glucose_report`（`build_glucose_report_tool`，平铺 11 个参数）：
  日期解析失败、结束日早于开始日、结束日在将来、一个指标都没读出来，四种情况都抛错并
  告诉模型该去问什么；存完回一句「这份报告我记在血糖记录里了」。
- 每轮上下文带上最近 3 份报告速览（`render_glucose_reports`），并注明「这些是报告上原本的
  指标，不是我们算的」。
- 接口 `GET /api/v1/health-profile/glucose-reports?limit=`（患者自己可见，按结束日倒序）。
- 记录页「传感器」栏新增「05-30 至 06-11 · 报告速览」卡片，只列报告上真有的项。
- 提示词：报告只有汇总指标时用「记这份报告」，不要编成读数。
- 测试：`test_glucose_write_tool.py` 新增 5 例（平铺 schema、整份留存、无指标被拒、
  日期倒置被拒、上下文渲染）；`test_server_feature_integration.py` 新增「报告速览只有本人看得到」。
  全量后端 **551 passed**、ruff / pyright 干净；移动端 50 套 **306 用例**、`tsc` 干净。
- 已部署：`antang-api:deploy-20261026-232702`，线上 `alembic_version = c7d8e9f1a2b3`，
  容器内 `record_glucose_report` 的 11 个平铺参数已验证。

### 2026-10-27 凌晨（三）：报告上的五档与低血糖风险也留下来

把分享里的 `CGM.zip` 解出来，20 份报告全是同一模板（天津医科大学总医院、硅基动感Pro，
跨 2023-07 到 2025-07），**没有一份带逐点读数**。它们比第一版设计时多给三类信息，
第一版全丢了：

- 五档区间占比（正常 / 低 / 很低 / 高 / 很高）。三档的 TAR/TBR 是「高+很高」「低+很低」
  合并过的值，合并就对不上报告原数，所以五档单独存；三档仍由后端用五档加出来，
  **不让模型自己算**（`record_glucose_report` 里 `_band_total`）。
- 「低血糖风险」档位（报告用 LBGI 预测）：`lbgi_level` 存 `high/moderate/low/lowest`，
  上下文与卡片都说成「报告写的低血糖风险高」这种原话，不换算成等级数字。
- 设备序列号（如 `LT2410FE3P`）与报告生成日期。

迁移 `d8e9f1a2b3c4` 加 8 列；工具参数从 11 个变成 19 个，占比一律 `ge=0 le=100`。
渲染时 Numeric 的 `99.60` 统一写成 `99.6`（`_num`），跟报告一致。
验证：后端 **553 passed**、ruff / pyright 干净；移动端 50 套 **306 用例**、`tsc` / lint 干净。
未部署。

### 2026-10-27 凌晨（四）：报告速览点得进去了

那张卡以前是纯 `View`，点它什么都不会发生；14 个指标只能挤在两行里。

- 卡变成 `Pressable`，多一行「看全部指标」，`accessibilityLabel` 是
  `报告速览 07-28 至 07-31`（带区间，免得几条报告分不清谁是谁）。
- 详情页 `apps/mobile/src/glucose/report-detail.tsx`（`Modal` pageSheet）分四块：
  区间 + 设备与序列号 + 生成日期；整体水平（eHbA1c / 平均 / SD / CV）；时间花在哪
  （三档 + 五档）；低血糖风险（`LBGI_LABELS` 翻成人话 + 一句「这是报告按 LBGI
  预测出来的档位，不是我们算的」）。**报告没印的整组不渲染**，不留空括号、
  不写「暂无」。
- 删除：`DELETE /health-profile/glucose-reports/{report_id}`（真删，不是软删——
  这份速览是我们替他存的摘要，不是原始报告）+ 详情页底部红色「删除这份报告速览」，
  删前 `Alert` 再问一次。别人那条返回 404 而不是 403：别让「存在」变成可探测的信息。
- 原文件暂时打不开：`glucose_reports` 只存指标、没存它是从哪份附件拆出来的；
  要接「打开原文件」得先加一层关联。
验证：后端 **554 passed**、ruff 干净；移动端 51 套 **311 用例**、`tsc` / lint 干净。

### 2026-10-27 凌晨（五）：原报告打得开，报告 PDF 也能自己选

两件事一起做，因为它们落在同一张表上。

**原文件关联。** `glucose_reports.source_attachment_id`（迁移 `e9f1a2b3c4d5`，
`ondelete="SET NULL"` 的外键）。模型不需要知道附件 id：工具运行时按
`AgentRun.trigger_message_id` 反查那一轮用户消息挂的附件，**恰好一份 PDF** 才关联
（两份以上分不清，就留空而不是猜）。于是「报告解读」存下来的速览，详情页会多一行
「打开原报告」——`GET /chat/attachments/{id}/content` 拿字节 → 写缓存 → 系统分享面板，
不在 App 内自己渲染 PDF。

**导入页加 PDF 解析。** 患者从手机里直接选医院给的报告 PDF，不经过对话。
解析放在服务端（`health_profile/report_parse.py`），用**模板正则**而不是大模型：
20 份实测里模型读同一份会给出不同的分档，正则不会。踩到的三个细节：pypdf 抽出来的
中文里混着**康熙部首**（`⾼ ⾎ ⻛`），必须 `unicodedata.normalize("NFKC", …)`；
序列号被排版塞了空格（`L T2410FE3P`）要去空白；「低血糖风险」是四个并列标签，
选中哪个只有图形位置标记，文字层拿不到 → 这条路**不猜风险档位**，留空。

- 两个接口：`POST /health-profile/glucose-reports/preview`（multipart，≤10 MB，只收 .pdf，
  **不落库**，返回读到的字段 + `missing` 名单）与 `POST /health-profile/glucose-reports`
  （患者确认后才写库，百分比一律 `ge=0 le=100`）。
- 导入页 `apps/mobile/src/glucose/GlucoseImportScreen.tsx` 顶部分成「CSV 导出文件」
  /「报告 PDF」两路；PDF 那路先显示「读到了这些」和「报告上的数」，**没读到的项目
  列成一句话**（「这几项没读到：高分档、很高分档」），不显示成 0；确认按钮写的是
  「确认存进血糖记录」。
- 依赖：镜像里的 venv 没有 pip（团队用 uv 装），`ensurepip` 在容器里也失败，所以
  **把纯 Python 的 pypdf 直接放进 site-packages**（`deploy/vendor/pypdf`，
  `Dockerfile.fast` 里一行 `COPY`）。`api/pyproject.toml` 同时声明 `pypdf>=6,<7`。
- 顺手修掉一个定时炸弹：`tests/test_cgm_routes.py` 把 CSV 时间写死成 `2026-10-20`，
  过了 7 天窗口就会在「某天早上」突然变红，改成按 `datetime.now(UTC) - 2h` 生成。
验证：后端 **569 passed**、ruff / pyright 干净；移动端 52 套 **316 用例**、`tsc` / lint 干净。

### 2026-10-27 白天：PDF 先抽文字层，别再整份塞给模型

之前所有附件都是原样 base64 塞进请求（`context/builder.py` 的 `to_model_message`）。
一份 5.9 MB 的指南要编码成 7.9 MB 的 base64 才能被模型看见，而它 99% 的内容是文字。

现在 PDF 分两步走（同一份文件可能走不同路，取决于它自己）：

- 先 `extract_pdf_text` 抽文字层，**≥ 2000 字**就把文字（最多前 16000 字）当文本块发过去，
  不再编码整份文件。`PDF_TEXT_LAYER_NOTICE` 会告诉模型：表格、曲线、版式不在其中，
  要看图请让患者发那一页的截图。
- 抽不出（扫描件、加密、坏文件）或文字太少（图表型报告，比如 CGM 评估报告）→
  仍然发原文件，让模型看图。**绝不能因为解析失败就把附件从上下文里弄丢**。

实测 26 份本地 PDF：17 份走文字（最大的《中国糖尿病防治指南（2024版）》5.90 MB →
43.9 万字，只发 16000 字，省掉 7.87 MB base64），9 份仍走原文件（都是老指南，
内嵌 CFF Type1 字体，pypdf 需要 fontTools 才能解码，暂不引入）。

- 顺手把 pypdf 的日志压到 ERROR：它遇到嵌入式字体会逐字警告，一份 PDF 能刷上百行。
- 图片没动：手机原图仍然整份上传，缩图这件事另做。

验证：后端 **572 passed**（新增 3 例：文字够厚走文本块、文字太少走原文件、扫描件走原文件），
ruff / pyright 干净。

### 2026-10-27 白天（二）：照片先缩到长边 1568 再上传

PDF 那条省的是服务端编码，照片这条路省的是**手机上传流量和视觉 token**。

- `apps/mobile/src/chat/photo-size.ts`：`shrinkPhoto(attachment)` 只处理 `kind === 'photo'`
  且 mime 是 `image/*`；长边超过 `MAX_PHOTO_EDGE = 1568` 才 `manipulateAsync` 缩放
  （横图按宽、竖图按高），输出 `image/jpeg`、`compress: 0.82`，文件名后缀换成 `.jpg`。
  **只缩不放**：本来就不大的照片原样发。
- 三处兜底，全都「宁可按原图上传，也别让患者发不出照片」：`Image.getSize` 失败、
  `manipulateAsync` 抛错、非图片/报告附件 → 原样返回。
- 接线点在 `apps/mobile/src/chat/ChatScreen.tsx` 的 `sendContent`：先 `shrinkPhoto`
  再 `uploadChatAttachment`，所以相机拍的和相册选的照片走同一条。
- 新依赖 `expo-image-manipulator@~57.0.21`（`npx expo install` 装的），原生模块，
  **要重新打包才生效**；`jest.setup.ts` 给 `manipulateAsync` 加了 mock。

1568 这个数不是拍的：多数多模态模型会把更大的图切成多块、一块块算 token，
1568 长边基本是「一张图看全」的拐点。

验证：移动端 53 套 **323 用例**（新增 6 例 `photo-size` 单测 + 1 例端到端：选照片 →
缩图 → 上传时带的是缩过的 uri）、`tsc --noEmit` 干净、`expo lint` 干净。

### 2026-10-27 白天（三）：报告的数字不许模型猜

20 份真报告实测时发现的隐患：同一个模型读同一份报告两次，给出的分档/均值可能不一样。
所以「数字从哪来」必须由服务端决定，而不是看模型这次读成什么。

三条路，各按各的规矩：

1. **报告 PDF 且模板解析成功** → 一律用程序读出来的数。模型传进来的数字全部忽略，
   结果里标 `verified: "pdf-template"`。上下文那一侧也不让模型再读一遍：
   `context/builder.py` 新增 `_report_facts_part()`，把解析结果写成「既成事实」
   （`REPORT_FACTS_NOTICE` + `render_parsed_facts()`），省掉整份 base64，
   并明说「不要再自己读一遍，也不要改动这些数字」。
2. **截图 / 认不出的 PDF** → 模型读一遍、服务端独立再读一遍（`report_verify.py`），
   逐项对上才存，标 `verified: "cross-checked"`。比什么：日期、设备、序列号、均值、
   SD、CV、eHbA1c、三档、五档；数字容差 0.011，文字必须完全一致。
   **第二遍没读出来的项按「对不上」算**——只有一遍支持的数不进记录。
   对不上就抛错让患者改用 PDF 或手动记，一条都不存。
3. **患者自己念数字（没有附件）** → 按他说的存，标 `verified: "typed"`。

顺手修掉精度问题：`glucose_reports.mean_mmol` 是 `Numeric(4,1)`，报告上印的 5.81
会被存成 5.8——放宽到 `Numeric(4,2)`（迁移 `f1a2b3c4d5e6`，降级不可逆，已在 docstring 写明）。

验证：后端 **578 passed**（新增 6 例：模板压过模型读数、截图两遍一致才存、
两遍不一致不存、第二遍读不出不存、`compare_reads` 只比第一遍声称的项、上下文走解析事实），
ruff / pyright 干净；`test_unified_schema_has_one_head` 通过（head = `f1a2b3c4d5e6`）。

### 2026-10-29 深夜：血糖三件事（趋势图 / PDF 留原件 / 风险档位）

1. **趋势图**（`apps/mobile/src/navigation/record-screen.tsx`）：`TREND_DAYS = 7`、
   `trendSeries(readings, now)`、`GlucoseTrend` 卡片放在「手动 / 传感器」分段控件之下，
   两个子栏共用。一条折线：实心点 = 自己量的，空心点 = 报告里记下的；绿带 3.9–10.0，
   少于 2 个点不画。时间轴锚点 `trendNow` 在取到数据时算一次（`Date.now()` 不能写在渲染里，
   `react-hooks/purity` 会拦），否则每次重渲染图都会抖。
2. **导入页选的 PDF 留原件**：`POST /api/v1/health-profile/glucose-reports` 改成收 multipart
   文件，服务端**重新解析**一遍再存（客户端改不了数），同时写一条 `message_id` 为空的
   `message_attachments`（`kind=report`），`source_attachment_id` 指向它——所以从导入页
   存进来的报告也能在详情页「打开原报告」。`GlucoseReportCreate` 随之删除。
3. **PDF 的「低血糖风险」档位**：报告里四档是并列标签、被选中的那档不是矢量填充
   （13.pdf 内容流里该区域填充路径数为 0，pypdf 也拿不到字体名/填色），所以改成
   **两次独立读数一致才留**（`report_verify.normalize_lbgi` / 工具里的 `_agreed_risk_level`），
   不一致或读不出就留空，不影响报告本身入库。

验证：后端 **582 passed**、ruff / pyright 干净（无新迁移，head 仍 `f1a2b3c4d5e6`）；
移动端 **53 套 326 用例**、`tsc --noEmit` 无输出、`expo lint` 干净。

### 2026-10-29 深夜（二）：写血糖的工具没配活动阶段，患者一发血糖就「回复失败」

线上 akaa 三条消息连挂（23:15、23:16、23:37），`agent_runs.error_message` 是
`工具缺少活动阶段配置：record_glucose_readings`。聊天流每收到一次工具活动事件都要用
`TOOL_ACTIVITY_PHASES` 查状态栏阶段（`api/src/antang_api/chat.py:1093`），查不到就
`raise RuntimeError` —— 于是模型一想记血糖，整条回复就失败。写血糖的两个工具
（`record_glucose_readings`、`record_glucose_report`）加进来时漏了这一条。

修法两处：
- `TOOL_ACTIVITY_PHASES` 补 `RECORD_GLUCOSE_TOOL_NAME` 与 `RECORD_GLUCOSE_REPORT_TOOL_NAME`
  两个 `organizing`。
- `api/src/antang_api/main.py` 启动时核对：`core_tools` 里每个工具名都必须有阶段配置，
  少了直接 `RuntimeError` 让部署失败回滚，而不是等患者在聊天里撞上。
- 新增 `api/tests/test_tool_phases.py`：把能构造的工具全建一遍，断言名字都在
  `TOOL_ACTIVITY_PHASES` 里（联网与健康档案委派那两个构造需要模型/网络，单独核对名字）。

验证：后端 **584 passed**（+2）、ruff / pyright 干净；镜像
`antang-api:deploy-20261029-234447` healthy。线上实测（`deploy/check_glucose_chat.py`，
测试账号 `rt_check_1029`）：`phases: ['thinking', 'organizing']`、无错误事件、
`glucose-readings` 里落了 `value_mg_dl 130`（7.2 mmol/L）、`source fingerstick`、
`context fasting`。

教训：**给工具加注册项，等于给聊天流加一条必须存在的映射**。凡是「工具名 → 某个配置」
的表，都要么有测试扫全量工具名，要么在启动时校验；否则漏一条就是线上整条回复失败。

### 2026-10-30 凌晨：血糖的「什么时候测的、怎么测的」要问清楚，拿不准就标成大概

线上实测暴露的问题：患者只说「我早上空腹血糖是7.2」，系统把时间存成了 `07:00`、
来源存成 `fingerstick`——时间是我编的、来源是我猜的，之后还会在趋势图上伪装成一个
精确点。三件事一起改：

1. **时间精度进数据库**：`glucose_readings.measured_precision`（`exact` / `daypart`，
   迁移 `g1b2c3d4e5f6`，带 CHECK）。`daypart` 表示患者只说得出「早上 / 晚上 / 半夜」，
   时间是该时段的代表时刻（07:00 / 12:00 / 15:00 / 20:00 / 02:00）。界面上写
   「10-29 早上（大概）」（`apps/mobile/src/glucose/measured-time.ts` 的
   `measuredTimeLabel`），趋势图则多一句「标成「大概」的点是只说得出时段的，横轴位置
   按该时段起点画」——**宁可显示一个大概，也不要让编出来的 07:00 冒充测量值**。
2. **来源不再有默认值**：`record_glucose_readings` 的 `source` 原本默认 `cgm`，现在必填；
   描述里写明「患者没说清、问不出来源就填 manual，不要猜成 cgm」。
3. **先问再记**：提示词加一条硬规则——患者只报一个数字时，先问「什么时候测的」，再问
   「怎么测的（指尖血 / 医院抽血 / 手环传感器）」，一次只问一件；患者说「记不清」就按
   `daypart` 记整点，并在回答里说一句「时间我按早上记的」。工具侧还有一道守卫：
   `time_precision=daypart` 却给了带分钟的时间，整批退回并说明该填整点，不悄悄替他改。

验证：后端 **584 passed**、ruff / pyright 干净、head `g1b2c3d4e5f6`；移动端 **54 套
333 用例**、`tsc --noEmit` 无输出、`expo lint` 干净。

### 2026-10-30 凌晨（二）：心理那张表为什么一直是空的——提示词从没要求记录

用户看 akaa 最新对话发现的：10-30 00:13 患者说「我怕我会像他们一样」（前一轮还在问
朱瞻基是不是糖尿病死的），回复把情绪接住了，但 `hypoglycemia_belief_events` 里没有这
一条；最近一条信念记录停在 10-23，`fear_behavior_logs` 也停在 10-23。

查证结论：**不是工具坏了**——`log_fear_behavior` / `record_hypoglycemia_belief` 全库
历史调用 `completed` 分别是 8 次和 28 次，没有一条失败；两个工具也都注册在
`core_tools`、`TOOL_ACTIVITY_PHASES` 里有 `organizing`。真正的原因是 `agents/core.py`
的系统提示词从头到尾**没有一条要求调这两个工具**：模型把「记录信念」当成可选的背景判断，
于是心情好的时候记一笔，正常聊天时就不记。行为与信念的汇总（`render_behavior_context`）
只读这张表，表里没东西，下一轮也就没有任何依据可用。

修法：提示词加一段「心理这一侧要落库」——灾难化信念（会死、会昏迷、一失控、发现不了、
永远会再来、是自己的错、怕被看成软弱、不敢过正常生活）要调「记这条信念」，`quote` 逐字
复制、`occurrence` 按本轮填；因怕低血糖做（或不敢做）的事调「记这次行为」，次数放
`count`；只在患者这一轮真的说了才记、不从语气推断；记了不告诉患者「记进心理档案」、
也不报次数；先接住情绪、再记录。两个工具自身的 docstring 也从「只在……时调用」改成
「说了就要调，否则这条信念/行为没有留下来」。

验证：后端 **585 passed**（+1：`test_core_prompt_records_beliefs_and_fear_behaviors_instead_of_only_noticing_them`）、
ruff / pyright 干净。

### 2026-10-30 凌晨（三）：提示词加完了，模型还是没调——把信念工具的参数改成平铺

提示词上线后先在线上试了一遍（测试账号 `rt_belief_1030`，两轮：讲古代皇帝糖尿病 → 「我怕我会像
他们一样」）：回复接住了情绪，`agent_tool_calls` 里却只有 `search_diabetes_knowledge`，`fear-summary`
的 `beliefs` 还是空的。也就是说**不是「调了失败」，是模型根本没调**。

两个工具原来的签名是 `request: BeliefRequest` / `request: FearBehaviorRequest`——一层嵌套对象，
模型要在里面再拼 `{"request": {...}}`。这和当初 `record_meal` 出事时是同一类摩擦（那次是 `**kwargs`
把 runtime 注入弄丢、线上报 `missing 1 required positional argument`）。改成平铺后：

- `api/src/antang_api/tools/behavior.py`：抽出模块级别名 `BehaviorKind` / `BehaviorCount` /
  `BehaviorOccurredAt` / `BehaviorNote`，`BeliefCode` / `BeliefQuote` / `BeliefOccurrence` /
  `BeliefTriggerContext` / `BeliefIntensity`；`FearBehaviorRequest`、`BeliefRequest` 与两个工具签名
  共用同一批别名字段（`runtime` 在第一个位置），工具里再 `FearBehaviorRequest(...)` 组装以复用校验。
- 提示词那条「回答顺序不能反」后面补一句放宽判据：只要患者用第一人称说出对低血糖的怕
  （哪怕话很短、哪怕是在讲别人之后接一句「我也会那样」），就算灾难化信念；拿不准宁可记一条。

验证：后端 **586 passed**（+1 平铺 schema 断言 `set(belief_tool.args) == {...}`）、ruff 干净；
镜像 `antang-api:deploy-20261030-002940` healthy。线上复测（`rt_belief_1031`）：
`fear-summary` 返回 `{"belief_code":"hypo_causes_death","label":"低血糖会导致死亡","mentions":1,
"latest_quote":"我怕我会像他们一样"}` → **ok**。

教训：**「提示词写了要调」不等于「模型调得出来」**。工具参数越平越容易调；一层嵌套就够让它在
情绪对话里选择「先安慰、不折腾工具」。验证要看 `agent_tool_calls`，不是看回复写得好不好。

### 2026-10-30 凌晨（四）：患者说了「删了吧」，得真的删——但要拿他的原话当签名

线上看到的一幕：患者发了一份别人的 CGM 报告，助手读完自己也发现「这份是 47 岁女性、2 型糖尿病，
和你不一致」，患者回「别人的，删了吧」，助手却只能答「你可以直接在 App 的『记录』页里找到并点击
撤回/删除它」——因为它手上没有任何删除工具。报告就一直留在她的记录里。

现在给了它一个工具，但**同意这件事不能由模型自述**：

- `api/src/antang_api/tools/glucose.py` 新增 `delete_glucose_report`（平铺参数
  `period_start` / `period_end` / `consent_quote`）。`_patient_agreed()` 会取这一轮的
  `AgentRun.trigger_message_id` 对应那条**用户消息**，把 `consent_quote` 去空白后比对是否为其子串；
  对不上就报「删记录得患者自己点头」。模型要自己编一句「好，删吧」是过不去的。
- 范围只到自己名下：按 `user_id` + `period_start` + `period_end` 找那一条，找不到就报「记录里
  没有 X 至 Y 这份报告，先问清是哪一份」，绝不按「最近一份」猜着删。
- 删到什么程度有边界：**只有 `message_id is None` 的孤儿附件（从导入页传上来的）连文件一起删**；
  聊天里发的那份留着——那是患者自己说的话，不能替他抹掉。
- 提示词（`api/src/antang_api/agents/core.py`）：患者说不要这份报告了，先复述一遍要删哪份、等
  他明确说要删，再把起止日和他那句原话传进去；删完说一句「删了」，不报编号。
- 注册：`tools/__init__.py`、`main.py` 的 `core_tools`、`chat.py` 的 `TOOL_ACTIVITY_PHASES`
  （`delete_glucose_report: "organizing"`）——**这一条漏了就会重演 10-29 那次「工具缺少活动阶段
  配置」整条回复失败**，好在 `main.py` 现在启动期会拦住。

验证：后端 **592 passed**（新增 `api/tests/test_glucose_report_delete_tool.py` 六例：同意后记录与
孤儿文件一起删、原话对不上不删、区间不存在不猜、聊天里的文件保留、别人的报告碰不到、参数是平铺的）、
ruff 干净、pyright 0 errors。

教训：不可逆写操作的「同意」要从患者原文里取证据，而不是让模型报告它得到了同意——和
「模型说做了不算做完」是同一条。

### 2026-10-30 凌晨（五）：血糖的「高低」按患者自己的目标范围算

以前「低于 3.9 共 N 次」那个 3.9 是写死在代码里的（`GLUCOSE_LOW_MG_DL = 70`，绿带也写死
3.9–10.0）。老年人常被放宽到 4.4–8.9，孕妇要更严，写死就等于替他定了标准。

- 数据：`PersonalProfile` 加 `glucose_target_low_mg_dl` / `glucose_target_high_mg_dl`
  （默认 70 / 180，`ck_personal_profiles_glucose_target` 保证下限大于 0 且小于上限），
  迁移 `h1c2d3e4f5a6`。
- 服务层：`glucose_targets()` / `set_glucose_targets()`；`summarize_glucose()` 改成
  `target_low_mg_dl` / `target_high_mg_dl` / `below_target_count` / `in_target_count` /
  `above_target_count`；`render_glucose_context()` 按患者的目标说高低，目标不是默认值时
  额外告诉模型「不要拿 3.9–10.0 套他」。
- 接口：`GET/PUT /api/v1/health-profile/glucose-target`（下限 36–180、上限 100–360，下限
  不小于上限返回 422）。
- 工具：`read_glucose_readings` 返回值跟着换成 `target_low_mmol` / `target_high_mmol` 与
  三个计数；`record_glucose_readings` 的 `device` 收紧成 `roche / abbott / sannuo | null`，
  提示词写明**没说牌子就留空，不要用「其它血糖仪」占位**（线上实测就见过把没提的牌子填成
  other）。
- 手机端：血糖区多一张「目标范围 4.4–8.9 mmol/L · 按医生给的改」的卡，点开
  `apps/mobile/src/glucose/target-sheet.tsx` 用 0.1 一格调上下限；趋势图绿带、曲线绿带、
  「低于 X」计数、摘要行全部读患者自己的范围；旧后端不返回这些字段时退回默认。
- 验证：后端 **596 passed**、ruff 干净、pyright 0 errors；移动端 **55 套 339 用例**、
  `tsc --noEmit` 无输出、`expo lint` 干净。

教训：临床阈值不要写进常量——同一个数字对 30 岁和 75 岁不是一回事；能让患者（和他的医生）
自己定，就别替他定。

### 2026-10-30 白天：字段改名把手机上已经装好的包打黑了

把 `below_70_count` 改名叫 `below_target_count` 之后，手机上装着 26102504 的账号打开
血糖页直接报「血糖数据缺少 below_70_count」、列表显示「还没有记录」。数据一条没少，是
`parseGlucoseSummary` 读不到旧字段就抛错，整页渲染失败。

- 接口同时返回 `below_70_count`（固定按 70 mg/dL 算，只为旧包兼容）与
  `below_target_count`（按患者目标算）。语义不同的两个数分开给，不混用。
- 教训：**线上还有旧安装包时，响应字段只能加不能改名**。想改名，先发一版兼容字段，
  等旧包退场再删。

### 2026-10-30 白天：合团队的临床分支，把 `ck_glucose_source` 的两种写法并起来

团队 master（临床工作台、研究数据导入、交互式血糖图）和我们这条血糖线都改过
`ck_glucose_source`：我们扩到 `manual/fingerstick/cgm/lab/report`，团队扩到
`manual/report/public_dataset/ohio_cgm`。两条线各自写死自己认识的值，谁最后跑谁说了算，
患者录一条指尖血就可能撞上约束。

- 新增合并点迁移 `m2a3b4c5d6e7`（`down_revision = ("h1c2d3e4f5a6", "v19ae2b35d48")`），
  把两边并成七个值的集合；`GLUCOSE_SOURCES` 同步取并集，患者能选的来源（`GlucoseInputSource`）
  仍是四种。`alembic` 从此只有一个 head。
- `test_schema_branch_merge.py` 的 head 断言改到合并点；`tests/test_schema_branch_merge.py::test_existing_local_and_server_branches_upgrade_without_losing_users`
  在本机 Windows 上会因 `asyncio` 的 Proactor 限制 `NotImplementedError`（一直都把它排除在
  全量之外），单跑 `test_unified_schema_has_one_head` 通过。
- 验证：后端 **600 passed**（含团队新增的 study / public-import 用例）、ruff 干净、
  `alembic current` = `m2a3b4c5d6e7 (mergepoint)`。
- 教训：两支队伍各自扩同一个 CHECK 约束时，别指望「谁后跑谁赢」，要么提前约定同一份
  来源表，要么在合并点显式并起来。

### 2026-10-30 傍晚：团队 CI 三个红点，以及 github.com 打不通时怎么推

PR #27 的两个 job（`api`、`mobile`）一开始都是红的，三个原因都在我们这边：

- `api/uv.lock` 没锁 `pypdf`：`uv sync --frozen` 装出来的环境里 pyright 解析不到
  `from pypdf import PdfReader`。`uv lock --project api` 重锁，现在锁到 `pypdf 6.21.4`。
- 两处 `GlucoseSummary(...)` 字面量少了 `below_70_count`（兼容旧包时加进 TypedDict 的键），
  pyright 报 `No overloads for "__init__" match the provided arguments`。
- `measured-time.test.ts` 与 `record-screen.test.tsx` 里的「记录于」断言用固定 `+08:00`
  时间戳构造、再经本机时区渲染：本地 `TZ=Asia/Shanghai` 过，CI runner 是 UTC，整体挪八小时。
  改成按本地时间构造（`atLocal(月, 日, 时, 分)`）再断言 `toBe("10-24 12:35")`，时区无关。
  **以后凡是要断言「界面上显示几点」，一律按本地时间构造夹具。**

验证：`TZ=UTC` 下移动端 55 套 339 例全过、tsc 与 `expo lint` 干净；后端 600 passed、
ruff 干净、pyright 0 errors（本机没有 `pyright.exe`，用 `.venv\Scripts\pyright-python.exe`）。
顺带把 `data/`、`datasets/`、`*.parquet`、`*.feather` 写进 `.gitignore`：Ohio T1DM 与公共
糖尿病数据集只在本机 `--root` 目录里读，永远不进仓库。

推不上去的时候：本机 `github.com`（解析到 `20.205.243.166`）443 连不上、`api.github.com`
正常，`git push` 报 `Recv failure: Connection was reset`。`api.github.com` 走 REST 仍可用，
所以这一笔是用 `git cat-file blob` 取内容 → `POST /git/blobs` → `POST /git/trees`（`base_tree`）
→ `POST /git/commits` → `PATCH /git/refs/heads/<branch>` 推的（提交 `a356856`，
本地那份 `9ae8449` 内容相同）。等 `github.com` 通了要 `git fetch team && git reset --hard
team/aya/nav-v2-team-merge` 把两边对齐——**REST 推上去的提交和本地提交哈希不同，别装作没发生**。















