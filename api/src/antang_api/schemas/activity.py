"""运动与睡眠手工补录的请求/响应模型。

字段少是刻意的：只回答「做了什么、多久、什么时候」和「几点睡、几点醒」。
没有卡路里、没有配速、没有睡眠分期——这些要么靠设备，要么靠患者猜，
写进来只会变成一份没人看也看不准的体测报告。
"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ActivityKind = Literal["walk", "run", "cycle", "strength", "swim", "yoga", "other"]
ActivityIntensity = Literal["light", "moderate", "vigorous"]

# 一段睡眠的上限，和 `sleep_logs` 的 CHECK 保持一致。
_MAX_SLEEP = timedelta(hours=24)


class ActivityLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: datetime
    kind: ActivityKind = "walk"
    minutes: int = Field(ge=1, le=600)
    intensity: ActivityIntensity = "moderate"
    note: str | None = Field(default=None, max_length=200)

    @field_validator("performed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("运动时间要带时区")
        return value


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    performed_at: datetime
    kind: ActivityKind
    minutes: int
    intensity: ActivityIntensity
    note: str | None
    created_at: datetime


class SleepLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fell_asleep_at: datetime
    woke_at: datetime
    note: str | None = Field(default=None, max_length=200)

    @field_validator("fell_asleep_at", "woke_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("睡觉和醒来的时间都要带时区")
        return value

    @model_validator(mode="after")
    def _check_order(self) -> "SleepLogRequest":
        if self.woke_at <= self.fell_asleep_at:
            raise ValueError("醒来时间要晚于入睡时间")
        if self.woke_at - self.fell_asleep_at > _MAX_SLEEP:
            raise ValueError("一段睡眠不能超过 24 小时，是不是把两晚记成一次了")
        return self


class SleepLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fell_asleep_at: datetime
    woke_at: datetime
    note: str | None
    created_at: datetime
