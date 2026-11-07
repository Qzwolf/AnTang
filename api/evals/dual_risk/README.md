# 双风险标注的小规模评测

跑：

```bash
cd api
.venv/bin/python -m evals.dual_risk.runner
```

或直接跑测试（CI 会跑）：

```bash
.venv/bin/python -m pytest tests/test_dual_risk_eval.py -q
```

## 它测什么

`cases.py` 里 26 条人工标的对照：一条读数（mg/dL）、他自己设的目标范围、他当时
说的话，标出「这个数当时该不该处理」和四档标签（`both` / `silent` / `felt_only` /
`neither`）。`runner.py` 用 `antang_api.psychosocial.dual_risk` 里的规则算出档位，
与人工答案比对，报三个数：

- `dangerous_accuracy`：该不该处理判对的比例
- `label_accuracy`：四档标签判对的比例
- `silent_recall`：`silent`（低了却没感觉）这一档的召回

`silent_recall` 低于 1.0 时 runner 返回非零——漏掉这一档比多问一句的代价大得多。

## 它能说明什么，不能说明什么

能说明：固定阈值（54 / 250 mg/dL）、患者目标范围、四档分类这三段规则没有走样，
改动时不会悄悄把 `silent` 挪到别的档里。

不能说明：这些标签是同一个读规则的人标的，所以准确率 1.000 更像是回归保护，不是
效度验证。真正要回答的是「这样分档对不对」，那得由患者和医生各自独立标一遍，
再看评分者一致性（至少两个人、盲标、算 Kappa）。这一步还没做，写在这里免得把
自测当成证据。

## 什么时候该改这个集子

- 产品里出现一次分错档的真实病例 → 补一条，并写清为什么人工这样标。
- 阈值或目标范围的语义变了 → 边界四条（54 / 53 / 250 / 251）必须一起改。
- 有人开始做真正的标注研究 → 这份留作回归集，另开一份双人盲标的。
