"""按受试者划分。

按窗口随机切分会让同一受试者的相邻窗口同时出现在训练集和测试集里——相邻 1 分钟
的窗口几乎相同，于是指标被严重高估。这个领域里因此失效的论文不少，所以这里
只有一种划分方式：整位受试者进训练或进测试。
"""

from __future__ import annotations

import numpy as np


def subject_holdout(
    subjects: list[str], *, test_ratio: float = 0.3, seed: int = 0
) -> tuple[list[str], list[str]]:
    """按受试者留出。返回 (train_subjects, test_subjects)。"""

    unique = sorted(set(subjects))
    if len(unique) < 2:
        raise ValueError("按受试者划分至少需要 2 位受试者")
    if not 0.0 < test_ratio < 1.0:
        raise ValueError("test_ratio 必须在 (0, 1) 之间")

    generator = np.random.default_rng(seed)
    shuffled = list(generator.permutation(unique))
    test_size = max(1, int(round(len(unique) * test_ratio)))
    test_size = min(test_size, len(unique) - 1)
    test = sorted(shuffled[:test_size])
    train = sorted(shuffled[test_size:])
    return train, test


def mask_subjects(subjects: list[str], keep: list[str]) -> np.ndarray:
    keep_set = set(keep)
    return np.array([subject in keep_set for subject in subjects], dtype=bool)
