from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from antang_api.schemas.auth import UserResponse
from antang_api.schemas.glucose import GlucoseReadingResponse
from antang_api.schemas.health_profile import HealthProfileResponse, WearableDashboardResponse

ClinicalScope = Literal["granted", "all"]


class ClinicalUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)


class ClinicalPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ClinicalAccessStatus(BaseModel):
    configured: bool
    unlocked: bool = False
    expires_at: datetime | None = None


class ClinicalUnlockResponse(BaseModel):
    token: str
    expires_at: datetime | None = None


class ClinicalPatientPage(BaseModel):
    patients: list[UserResponse]
    next_after: str | None
    total: int


class ClinicalGlucoseSummary(BaseModel):
    count: int
    average_mg_dl: float | None
    minimum_mg_dl: int | None
    maximum_mg_dl: int | None


class ClinicalGlucose(BaseModel):
    summary: ClinicalGlucoseSummary
    points: list[GlucoseReadingResponse]
    truncated: bool
    limit: int


class StudyEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    measured_at: datetime
    ended_at: datetime | None
    value: float
    unit: str
    detail: str
    source_ref: str


class StudyResponse(BaseModel):
    dataset: str
    insulin_type: str | None
    period_start: datetime
    period_end: datetime
    provenance: dict
    event_count: int
    events_truncated: bool
    events: list[StudyEventResponse]


class ClinicalOverview(BaseModel):
    patient: UserResponse
    period_start: datetime
    period_end: datetime
    glucose: ClinicalGlucose
    study: StudyResponse | None = None
    profile: HealthProfileResponse
    wearable: WearableDashboardResponse

