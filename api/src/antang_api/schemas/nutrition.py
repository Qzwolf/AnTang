"""饮食与用药记录的请求/响应模型。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MealType = Literal["breakfast", "lunch", "dinner", "snack"]
MedicationKind = Literal["insulin", "oral", "other"]


class MealLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eaten_at: datetime
    meal_type: MealType
    description: str = Field(default="", max_length=200)
    carbs_grams: int | None = Field(default=None, ge=0, le=400)

    @field_validator("eaten_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("吃饭时间要带时区")
        return value


class MealLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    eaten_at: datetime
    meal_type: MealType
    description: str
    carbs_grams: int | None
    created_at: datetime


class MedicationLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taken_at: datetime
    name: str = Field(min_length=1, max_length=64)
    dose_text: str = Field(default="", max_length=64)
    kind: MedicationKind = "other"
    note: str | None = Field(default=None, max_length=200)

    @field_validator("taken_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("用药时间要带时区")
        return value


class MedicationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    taken_at: datetime
    name: str
    dose_text: str
    kind: MedicationKind
    note: str | None
    created_at: datetime
