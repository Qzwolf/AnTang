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

- **发布物能追溯到源码提交（2026-10-22）**：`scripts/android-version --apply` 现在除了分配 versionCode，还把 `git rev-parse --short=12 HEAD` 写进 `apps/mobile/app.json` 的 `expo.extra.buildCommit`；新增可执行的发布脚本 `scripts/publish-apk`（算 sha256/字节数、按 versionCode 命名、写清单，可只落本地 `dist/` 不上传），清单新增 `built_from_commit` 与 `features` 两个字段。患者界面**只显示版本号**，提交号与功能开关留在清单里，按版本号就能反查「这台手机装的是哪份代码、包里开了什么」。文档：`docs/release.md`「更新检查与分发」。测试：`apps/mobile/src/update/release.test.ts` 的「keeps the source commit and feature list when the manifest has them」。

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
