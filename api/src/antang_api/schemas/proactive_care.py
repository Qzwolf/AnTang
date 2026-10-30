import re
from datetime import datetime, time
from typing import Annotated, Any
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from antang_api.models import (
    PushPermissionState,
    PushPlatform,
    RoutineCareCadence,
)


ExpoPushToken = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^(ExponentPushToken|ExpoPushToken)\[[^\]\s]+\]$",
    ),
]
AppVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
_QUIET_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_IANA_TIMEZONES = available_timezones()


class ProactiveCareSettingsPayload(BaseModel):
    routine_cadence: RoutineCareCadence
    plan_follow_up_enabled: bool
    health_events_enabled: bool
    timezone: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ]
    quiet_hours_start: time
    quiet_hours_end: time
    health_notification_preview_enabled: bool
    # 打扰预算的只读部分：本周已经主动联系了几次、上限几次、是不是我们替你停了。
    weekly_contact_count: int = 0
    weekly_contact_limit: int | None = None
    auto_paused: bool = False
    auto_downgraded_at: datetime | None = None

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if value not in _IANA_TIMEZONES:
            raise ValueError("timezone must be an IANA time zone")
        return value

    @field_validator("quiet_hours_start", "quiet_hours_end", mode="before")
    @classmethod
    def validate_quiet_time(cls, value: Any) -> Any:
        if isinstance(value, time):
            return value
        if not isinstance(value, str) or _QUIET_TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("quiet hours must use HH:MM")
        return time.fromisoformat(value)

    @field_serializer("quiet_hours_start", "quiet_hours_end", when_used="json")
    def serialize_quiet_time(self, value: time) -> str:
        return value.strftime("%H:%M")


class PushInstallationRequest(BaseModel):
    expo_push_token: ExpoPushToken | None
    permission: PushPermissionState
    platform: PushPlatform
    app_version: AppVersion

    @model_validator(mode="after")
    def granted_installation_has_a_token(self) -> "PushInstallationRequest":
        if (
            self.permission == PushPermissionState.GRANTED
            and self.expo_push_token is None
        ):
            raise ValueError("通知权限已授予时必须提供 Expo Push Token")
        return self
