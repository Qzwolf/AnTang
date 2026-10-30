# research/

研究代码放这里，先放一份可复现的低血糖预测基线，给「预测 30 分钟后的血糖与低血糖」这件事提供对照：

- `glucose-forecast/`——数据加载、窗口切分、Clarke 分区与事件检出、四个基线与卷积序列模型。
  它**不进产品构建**，不被 `api/`、`apps/` 引用，跑起来用 `py -3 -m`。

## 数据不进仓库

各数据集有自己的许可，OhioT1DM 还要单独申请，所以仓库里只有代码。跑之前把数据解压到本机任意位置，
用环境变量指向它：

```bash
export GLUCOSE_DATA_ROOT=/path/to/datasets     # 里面放 T1D-UOM/、OhioT1DM/、DiaData-5min.zip …
cd research/glucose-forecast
py -3 -m pip install -e ".[dev]"
py -3 -m pytest tests -q
```

没设 `GLUCOSE_DATA_ROOT` 时默认 `./data/glucose`，那个目录被 `.gitignore` 忽略。

## 结果

实验结论写在 `glucose-forecast/docs/实验记录-低血糖预警.md`，跑出来的指标 JSON 也留在本机
`GLUCOSE_DATA_ROOT` 下，不进仓库。
