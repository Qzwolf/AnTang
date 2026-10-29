"""血糖记录的请求与响应模型。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GlucoseContext = Literal[
    "fasting", "pre_meal", "post_meal", "bedtime", "night", "other"
]
GlucoseSource = Literal["manual", "report", "public_dataset", "ohio_cgm"]


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
    context: Annotated[
        GlucoseContext,
        Field(default="other", description="测量场景：空腹、餐前、餐后、睡前、夜间或其他。"),
    ] = "other"
    note: Annotated[
        str | None,
        Field(default=None, max_length=200, description="可选备注，例如当天的特殊情况。"),
    ] = None


class GlucoseReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    value_mg_dl: int
    measured_at: datetime
    context: GlucoseContext
    source: GlucoseSource
    note: str | None
    created_at: datetime


class GlucoseSummaryResponse(BaseModel):
    days: int
    count: int
    average_mg_dl: float | None
    minimum_mg_dl: int | None
    maximum_mg_dl: int | None
    below_70_count: int
