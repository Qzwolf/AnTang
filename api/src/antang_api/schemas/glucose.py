"""血糖记录的请求与响应模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

GlucoseContext = Literal[
    "fasting", "pre_meal", "post_meal", "bedtime", "night", "other"
]
GlucoseSource = Literal[
    "manual", "fingerstick", "cgm", "lab", "report", "public_dataset", "ohio_cgm"
]
# 「report」只由报告解读写入，患者手填/导入能选的来源是下面四个。
GlucoseInputSource = Literal["fingerstick", "cgm", "lab", "manual"]
# 指尖血的机器品牌。它不是来源（来源是"指尖血"），只是"用哪台测的"，
# 所以只在 source=fingerstick 时有意义，其余情况存空字符串。
GlucoseDevice = Literal["roche", "abbott", "sannuo", "other"]
# 测量时间的精度：exact = 有时刻；daypart = 只说了「早上 / 晚上」，界面上要写「（大概）」。
GlucosePrecision = Literal["exact", "daypart"]


class GlucoseReadingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_mg_dl: Annotated[
        int,
        Field(
            ge=20,
            le=600,
            description="血糖值，单位 mg/dL。20–600 之外视为录入错误。",
        ),
    ]
    measured_at: Annotated[datetime, Field(description="测量时间，需带时区。")]
    source: Annotated[
        GlucoseInputSource,
        Field(
            default="fingerstick",
            description="这条数值从哪里来：指尖血、传感器、化验单，或不确定时选手动。",
        ),
    ] = "fingerstick"
    context: Annotated[
        GlucoseContext,
        Field(default="other", description="测量场景：空腹、餐前、餐后、睡前、夜间或其他。"),
    ] = "other"
    source_device: Annotated[
        GlucoseDevice | None,
        Field(
            default=None,
            description="指尖血用的哪台血糖仪；化验单和传感器不需要填，填了也会被忽略。",
        ),
    ] = None
    note: Annotated[
        str | None,
        Field(default=None, max_length=200, description="可选备注，例如当天的特殊情况。"),
    ] = None


class GlucoseReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    value_mg_dl: int
    measured_at: datetime
    measured_precision: GlucosePrecision
    context: GlucoseContext
    source: GlucoseSource
    source_device: str
    note: str | None
    created_at: datetime


class GlucoseSummaryResponse(BaseModel):
    days: int
    count: int
    average_mg_dl: float | None
    minimum_mg_dl: int | None
    maximum_mg_dl: int | None
    # 目标范围是患者自己设的（见 /glucose-target），所以一起回给界面：
    # 界面不用再自己写死 3.9 / 10.0。
    target_low_mg_dl: int
    target_high_mg_dl: int
    below_target_count: int
    in_target_count: int
    above_target_count: int
    # 只为旧安装包保留（26102504 及更早按固定 70 mg/dL 显示「低于 3.9 的次数」，
    # 字段不在就报错、整个血糖页空掉）。新界面读上面的目标范围字段。
    below_70_count: int


class GlucoseTargetResponse(BaseModel):
    """患者自己的血糖目标范围（库里存 mg/dL，界面自己换成 mmol/L）。"""

    low_mg_dl: int
    high_mg_dl: int


class GlucoseTargetUpdate(BaseModel):
    """改目标范围：医生给的范围因人而异，所以是参数，不是常量。"""

    model_config = ConfigDict(extra="forbid")

    low_mg_dl: int = Field(ge=36, le=180)
    high_mg_dl: int = Field(ge=100, le=360)

    @model_validator(mode="after")
    def _low_below_high(self) -> GlucoseTargetUpdate:
        if self.low_mg_dl >= self.high_mg_dl:
            raise ValueError("目标范围的下限要小于上限。")
        return self


class GlucoseReportResponse(BaseModel):
    """一份报告速览。指标可空：报告上没写的，我们也不补。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period_start: date
    period_end: date
    device: str
    device_serial: str
    report_generated_at: date | None
    mean_mmol: float | None
    sd_mmol: float | None
    cv_percent: float | None
    ehba1c_percent: float | None
    tir_percent: float | None
    tar_percent: float | None
    tbr_percent: float | None
    normal_percent: float | None
    low_percent: float | None
    very_low_percent: float | None
    high_percent: float | None
    very_high_percent: float | None
    # high / moderate / low / lowest，留空表示报告上没写。
    lbgi_level: str
    # 原文件（患者发进对话的那份 PDF）的附件 id；没有就为 null。
    source_attachment_id: UUID | None = None
    note: str | None
    created_at: datetime


class GlucoseReportsResponse(BaseModel):
    items: list[GlucoseReportResponse]


class GlucoseReportPreview(BaseModel):
    """选了一份 PDF 之后，先把「我们读到了什么」摆出来给患者看。"""

    file_name: str
    period_start: date
    period_end: date
    report_generated_at: date | None
    device: str
    device_serial: str
    hospital: str
    mean_mmol: float | None
    sd_mmol: float | None
    cv_percent: float | None
    ehba1c_percent: float | None
    normal_percent: float | None
    low_percent: float | None
    very_low_percent: float | None
    high_percent: float | None
    very_high_percent: float | None
    # 报告上没印、或者版式里读不出来的字段，界面照实说「没读到」。
    missing: list[str]
