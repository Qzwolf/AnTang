import re
from datetime import datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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
    # 上限：非空就是患者自己定的（PUT 时传 null 表示「跟着评估走」）。
    weekly_contact_limit: int | None = None
    weekly_contact_limit_source: Literal["patient", "assessment"] = "assessment"
    # 几点收到日常问候（本地整点）。非空 = 患者自己定的；空 = 沿用滚动时间。
    check_in_hour: int | None = None
    auto_paused: bool = False
    auto_downgraded_at: datetime | None = None
    # 行为痕迹的校准期：这段时间内只记录「本来会发」，不真发。空 = 没在校准。
    behavior_calibration_until: datetime | None = None

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("weekly_contact_limit")
    @classmethod
    def validate_weekly_limit(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 7:
            raise ValueError("每周最多主动联系几次，只能填 1 到 7")
        return value

    @field_validator("check_in_hour")
    @classmethod
    def validate_check_in_hour(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 23:
            raise ValueError("想要几点收到日常问候，只能填 0 到 23 的整点")
        return value

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


class BehaviorCalibrationRequest(BaseModel):
    """开启/结束行为痕迹的校准期。

    `days=0` 就是立刻结束校准、恢复真发。上限 30 天：校准是过渡办法，不是永久
    的「只记账不说话」——一直不发，就等于这套东西没上线。
    """

    days: Annotated[int, Field(ge=0, le=30)]


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
