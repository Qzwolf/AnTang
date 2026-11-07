# 「用你的话问你的血糖」的评测集

一句话折成一个时间窗口 + 一类场景。规则在
`api/src/antang_api/health_profile/query_parse.py`，集子在 `cases.py`。

## 怎么跑

```powershell
cd api
python -m evals.glucose_query.runner
```

报告写到 `api/evals/reports/glucose-query-<UTC>.json`；有一条对不上就返回非零。

## 这个数说明什么

- `understood_rate`：这些话里有多少句看出了是哪一天。
- `window_accuracy`：看出的那一天，起止时间对不对。
- `context_accuracy`：场景（空腹 / 餐前 / 餐后 / 睡前 / 夜间）对不对。

集子的期望值是写死的（基准时刻 2026-11-07 周六 10:00，+08:00），所以它是**回归
保护**，不是对真实语料的覆盖率。真实患者会用我没写进来的说法——那些话现在的表现
是「认不出」，工具会返回 `understood=false`，模型应该回问一句，而不是换成
「最近七天」。这条退路比准确率更重要。

## 还没做的

- 真实语料：从线上对话里抽样匿名句子，人工标窗口，再算一次准确率。
- 反例集：同一句话有两种解释时（「上周」是指周一到现在还是整个上周），现在的取法
  是上一整周；这类歧义应该显式问一句，而不是默默选一个。
