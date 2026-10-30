# glucose-forecast

血糖预测的**评测骨架**与基线。目标是先把「怎么算赢」定死，再往里换模型。

## 为什么先搭框架再训模型

如果先训模型再想指标，得到的数字一定好看，也一定没法比较。这个目录的顺序是：

1. **统一数据形状**（`series.py`、`windows.py`）：所有数据集最终都变成同一个 5 分钟网格，切成「过去 6 小时 72 点 → 未来 1 小时 12 点」的窗口，分段数 6（每段 12 点，段长 = 预测长度，与 GluLLM 的组织方式一致）。
2. **统一指标**（`metrics.py`）：RMSE / MAE 之外，必须有 Clarke 误差网格分区和**按事件**的低血糖检出指标。原因很实际：Clarke A+B 区连 persistence 基线都能超过 90%，只报它等于自欺。
3. **三个基线**（`baselines.py`）：persistence、线性外推、岭回归自回归。模型必须在这三个之上才有资格叫模型。
4. 然后才是模型。

## 数据

| 数据集 | 用途 | 状态 |
|---|---|---|
| T1D-UOM（17 人，3 个月，CGM + 营养 + 活动 + 睡眠，CC BY 4.0） | 跑通链路、验证 loader | 已下载 |
| DiaData（1720 人，5 分钟 CGM，CC-BY-NC 4.0） | 规模与评测主力 | 下载中 |
| HUPA-UCM（25 人，CGM + 胰岛素 + 碳水 + Fitbit） | 唯一的开放胰岛素数据 | 待下载 |
| OhioT1DM（12 人，8 周，模态最全） | 领域事实标准，审稿必需 | 申请中 |

**注意**：T1D-UOM 的 `Insulin Data` 目录是空的，它**没有胰岛素数据**。任何需要胰岛素的实验只能用 HUPA-UCM。

数据集**不进仓库**（各有自己的许可，OhioT1DM 还要申请）。所有 loader 都从 `--root` 或环境变量
`GLUCOSE_DATA_ROOT` 找数据，默认 `./data/glucose`：

```bash
export GLUCOSE_DATA_ROOT=/path/to/datasets   # 放 T1D-UOM/、DiaData-5min.zip、OhioT1DM/ …
```

## 快速开始

```bash
py -3 -m pip install -e ".[dev]"
py -3 -m glucose_forecast.evaluate --dataset t1d-uom \
  --root "$GLUCOSE_DATA_ROOT/T1D-UOM/sharpic-ManchesterCSCoordinatedDiabetesStudy-ea52718"
```

输出一张按基线分行的指标表（含 Clarke 分区与低血糖事件检出），并写出 JSON。

## 目录

```
src/glucose_forecast/
  series.py        统一数据结构 + 5 分钟网格对齐
  windows.py       6 小时 → 1 小时 滑窗
  splits.py        按受试者划分（绝不按时间随机切）
  metrics.py       RMSE / MAE / MAPE、Clarke 分区、低血糖事件检出
  baselines.py     persistence / 线性外推 / 岭回归自回归
  data/t1d_uom.py  T1D-UOM loader
  evaluate.py      命令行入口
tests/             窗口切分与指标的单元测试
```

## 两条纪律

- **划分必须按受试者**。同一受试者的相邻窗口几乎相同，按窗口随机切会把未来样本漏进训练集，指标虚高。
- **先记 persistence 的数**。这个领域常故意省略它，而它往往很强；不报它的模型结论不可信。

## Clarke 误差网格的说明

`metrics.clarke_zone` 用的是文献中最常见的简化规则表（六个格子），规则写在函数文档里并有单元测试覆盖。发表前应与一个参考实现（例如 PyPI 上的 `clarke-error-grid`）逐点对拍，并在方法部分写明所用的判据。
