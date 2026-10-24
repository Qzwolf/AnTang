# 血糖数据格式与来源规范

> 目的：让不同来源（指尖血、CGM 导出、化验单、报告解析）的数据进到同一套结构里，并且任何一条数值都能回答三个问题：**谁测的、什么时候测的、这份数据从哪来**。
> 适用范围：Patient App、后端导入接口、将来的 CGM 实时接入。
> 版本：2026-10-24 第一版（对应迁移 `u08fa1b2c3d4`）。

---

## 一、为什么必须分两类数据

| | 点测（GlucoseReading） | CGM 序列（CgmSample） |
|---|---|---|
| 频率 | 一天几次到十几次 | 每 5 分钟一条，一天约 288 条 |
| 语义 | 一次测量事实 | 一段连续曲线 |
| 典型来源 | 指尖血、扫描、化验单、报告人工核对 | 传感器导出文件、将来的实时接入 |
| 撤回 | 软撤回（打 `revoked_at`），保留「记过又撤」这件事 | 不支持单条撤回；导错了整批删 |
| 用于 | 对话引用、低血糖复盘、每日概况 | 趋势、夜间低血糖、变异性 |

把 CGM 塞进点测表会让「最近 7 天平均」这类统计失真，所以两张表分开。

---

## 二、点测表 `glucose_readings`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | uuid | 是 | UUIDv7 |
| `user_id` | uuid | 是 | 患者 |
| `value_mg_dl` | int | 是 | 统一换算成 mg/dL，取值范围 20–600 |
| `measured_at` | timestamptz | 是 | **测量时间**（可能早于录入） |
| `context` | text | 是 | `fasting` / `pre_meal` / `post_meal` / `bedtime` / `night` / `other` |
| `source` | text | 是 | `manual`（旧数据）/ `fingerstick` / `cgm` / `lab` / `report` |
| `note` | text(200) | 否 | 备注 |
| `revoked_at` | timestamptz | 否 | 撤回时间；有值即不参与读取与统计 |
| `reviewed_at` | timestamptz | 否 | 24 小时复盘已问过的时间；问过就不再问 |
| `created_at` | timestamptz | 是 | **录入时间**，用于识别「补录」 |

**补录判定**：`created_at - measured_at > 10 分钟` 视为补录，界面标「补录」；超过 24 小时提示「这条是回忆的时间，对吗？」。不拒绝，只标注。

---

## 三、CGM 序列表 `cgm_samples`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | uuid | 是 | UUIDv7 |
| `user_id` | uuid | 是 | 患者 |
| `import_id` | uuid | 否 | 指向 `glucose_imports`，用于整批删除与溯源 |
| `source_system` | text(16) | 是 | `sibionics` / `nightscout` / `libre` / `dexcom` / `generic` |
| `device_id` | text(64) | 是 | 设备或传感器会话标识，未知时为空串 |
| `recorded_at` | timestamptz | 是 | 设备记录时间（带时区） |
| `value_mg_dl` | int | 是 | 20–600 |
| `trend` | text(16) | 否 | 原始趋势箭头/方向文本，不解释 |
| `quality` | text(16) | 是 | `ok` / `gap` / `artifact` |
| `created_at` | timestamptz | 是 | 入库时间 |

**唯一键**：`(user_id, source_system, device_id, recorded_at)`。同一份导出重复导入不会重复入库（`ON CONFLICT DO NOTHING`），因此导入是幂等的。

**导入账本 `glucose_imports`**：`user_id`、`source_system`、`file_name`、`parsed_count`、`inserted_count`、`skipped_count`、`created_at`。删掉一条账本行会级联删除该批样本。

---

## 四、导入契约

### 4.1 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/health-profile/glucose-imports/preview` | 只解析、不落库；返回可用行数、前 10 行、可疑行说明 |
| POST | `/api/v1/health-profile/glucose-imports` | 确认导入；幂等；返回解析数/入库数/跳过数 |
| GET | `/api/v1/health-profile/cgm-samples?days=7` | 按时间正序取序列，供画曲线 |

参数：`file`（multipart，`.csv`/`.txt`，≤ 5 MB）、`source_system`（默认 `generic`）。
错误：空文件 422；非 CSV 415（提示去「报告解读」发 PDF/截图）；行全不可用 422；未登录 401。

### 4.2 CSV 约定

- 编码：UTF-8（允许 BOM）、GB18030、UTF-16。
- 分隔符：逗号、分号、制表符均可。
- 表头必须能认出**时间列**与**数值列**：
  - 时间列名：`time` / `timestamp` / `datetime` / `date_time` / `date` / `时间` / `测量时间` / `记录时间`
  - 数值列名：`value` / `glucose` / `glucose_mgdl` / `sgv` / `血糖` / `血糖值` / `数值`
  - 可选：`unit`（单位）、`trend`（趋势）、`device`（设备）
- 时间格式：ISO 8601（含 `+08:00` / `Z`）、`YYYY-MM-DD HH:MM[:SS]`、`YYYY/MM/DD`、10 位秒级或 13 位毫秒级时间戳。
- **没有时区的行按 +08:00 处理，并在返回的 `problems` 里明说**，不静默假设。

### 4.3 校验规则

| 规则 | 处理 |
|---|---|
| 值域 20–600 mg/dL | 超出即跳过，进 `problems` |
| 单位 | 以文件内单位为准；同一文件内单位不一致的行跳过并说明 |
| 单位缺失 | 按数值量级推断（最大值 ≤ 35 视为 mmol/L），推断不算错误 |
| 时间重复（同一文件内） | 保留第一条，其余跳过 |
| 文件为空 / 表头认不出 / 行数 > 50000 | 整份拒绝（422） |
| 未来时间 | 允许入库但 `problems` 提示；由界面二次确认 |

### 4.4 单位换算

`mg/dL = mmol/L × 18`（取整）。展示时 `mmol/L = mg/dL ÷ 18`，保留一位小数。数据库只存 mg/dL，避免同一数值两种口径。

---

## 五、各来源适配

| 来源 | 获取方式 | 落地状态 |
|---|---|---|
| 硅基（GS3/GS5） | 厂家 App 导出 CSV | 通用解析器可读；导出模板差异靠列名兜底 |
| Nightscout | `GET /api/v1/entries.json`（`sgv`=mg/dL，`date`=毫秒） | 计划：填地址 + token 的只读连接器 |
| 雅培 Libre / 德康 Clarity | 网页导出 CSV | 通用解析器可读 |
| 指尖血 | 患者手输（App 记录页） | 已上线 |
| 化验单 | 手输或报告解读 | 手输已上线；PDF/图片走报告解读，不落结构化数值 |
| 实时传感器 | BLE 直连 / 厂商私有协议 | **不做**（授权 + 稳定性 + 后台限制，见产品蓝图） |

---

## 六、模型边界

1. **数值解析由代码完成**：CSV 一律走确定性解析器；模型不参与数字提取。
2. **图片/PDF 报告**走「报告解读」：模型读图给文字，**不产生入库数值**；如果将来要抽取，必须标 `source=report`、带 `provenance`（文件名/页号/解析器版本）与置信度，并且**需要患者确认一次**才允许写入。
3. 对话里引用数值时，必须带来源与时间（「你昨天睡前记的 4.4」），不得把不同来源混成一句话。

---

## 七、待办与后续

- 前端导入页（选文件 → 预览 → 确认），对应 P0。
- 用药/饮食记录复用同一套「导入账本 + 唯一键 + 预览确认」的结构。
- CGM 曲线展示：桌面与最低点标注，避免把单点抖动当结论。
- 保留期：CGM 原始样本默认保留 90 天，更早的按小时聚合（尚未实现）。
