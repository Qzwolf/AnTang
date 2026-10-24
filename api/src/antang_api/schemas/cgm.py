"""CGM 导入的请求与响应模型。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CgmSourceSystem = Literal["sibionics", "nightscout", "libre", "dexcom", "generic"]


class CgmSamplePreview(BaseModel):
    recorded_at: datetime
    value_mg_dl: int
    trend: str | None = None


class CgmImportPreviewResponse(BaseModel):
    """先看再导：解析结果与可疑行都摆出来，由用户确认。"""

    source_system: CgmSourceSystem
    file_name: str
    unit: str
    total_rows: int
    usable_rows: int
    problems: list[str]
    first_at: datetime | None = None
    last_at: datetime | None = None
    samples: list[CgmSamplePreview]


class CgmImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_system: str
    file_name: str
    parsed_count: int
    inserted_count: int
    skipped_count: int
    created_at: datetime


class CgmSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_at: datetime
    value_mg_dl: int
    trend: str | None
    source_system: str


class CgmSeriesResponse(BaseModel):
    days: Annotated[int, Field(ge=1, le=90)]
    count: int
    samples: list[CgmSampleResponse]
