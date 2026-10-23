from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from antang_api.health_profile.types import (
    HealthFactSnapshot,
    PersonalProfileUnit,
    PersonalProfileSnapshot,
)
from antang_api.models import (
    FactAssertion,
    FactTemporalStatus,
    HealthFactType,
    PersonalProfileField,
    ProfileChangeMode,
    ProfileChangeStatus,
    ProfileOperation,
    ProfileTargetType,
    WearableRecordType,
)

ExternalRecordId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WearableDevice(StrictApiModel):
    manufacturer: ShortText | None = None
    model: ShortText | None = None
    device_type: Annotated[int, Field(ge=0)] | None = None


class StepsData(StrictApiModel):
    count: NonNegativeInt


class ExerciseData(StrictApiModel):
    exercise_type: NonNegativeInt
    title: ShortText | None = None


class DistanceData(StrictApiModel):
    meters: NonNegativeDecimal


class ElevationGainedData(StrictApiModel):
    meters: NonNegativeDecimal


class WeightData(StrictApiModel):
    kilograms: PositiveDecimal


class RespiratoryRateData(StrictApiModel):
    breaths_per_minute: PositiveDecimal


class RestingHeartRateData(StrictApiModel):
    beats_per_minute: PositiveInt


class HeartRateSample(StrictApiModel):
    time: datetime
    beats_per_minute: PositiveInt

    @model_validator(mode="after")
    def require_timezone(self) -> "HeartRateSample":
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("heart rate sample time requires timezone")
        return self


class HeartRateData(StrictApiModel):
    samples: Annotated[list[HeartRateSample], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def require_sorted_unique_samples(self) -> "HeartRateData":
        times = [sample.time for sample in self.samples]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("heart rate samples must be sorted and unique")
        return self


class SleepStage(StrictApiModel):
    start_time: datetime
    end_time: datetime
    stage: Literal[
        "unknown",
        "awake",
        "sleeping",
        "out_of_bed",
        "awake_in_bed",
        "light",
        "deep",
        "rem",
    ]

    @model_validator(mode="after")
    def validate_interval(self) -> "SleepStage":
        if (
            self.start_time.tzinfo is None
            or self.start_time.utcoffset() is None
            or self.end_time.tzinfo is None
            or self.end_time.utcoffset() is None
        ):
            raise ValueError("sleep stage times require timezone")
        if self.end_time <= self.start_time:
            raise ValueError("sleep stage end_time must be after start_time")
        return self


class SleepData(StrictApiModel):
    stages: Annotated[list[SleepStage], Field(max_length=10000)]

    @model_validator(mode="after")
    def require_ordered_non_overlapping_stages(self) -> "SleepData":
        previous_end: datetime | None = None
        for stage in self.stages:
            if previous_end is not None and stage.start_time < previous_end:
                raise ValueError("sleep stages must be ordered and non-overlapping")
            previous_end = stage.end_time
        return self


class OxygenSaturationData(StrictApiModel):
    percentage: Annotated[Decimal, Field(ge=0, le=100)]


class WearableRecordBase(StrictApiModel):
    external_record_id: ExternalRecordId
    start_time: datetime
    end_time: datetime
    start_zone_offset_seconds: Annotated[int, Field(ge=-64800, le=64800)] | None = None
    end_zone_offset_seconds: Annotated[int, Field(ge=-64800, le=64800)] | None = None
    # Zepp/Health Connect 与 Gadgetbridge 手动导入可能来自不同设备。
    # 限定来源，避免将手机或其他 App 的累计数据再次相加。
    source_package: Literal[
        "com.huami.watch.hmwatchmanager",
        "nodomain.freeyourgadget.gadgetbridge",
    ]
    recording_method: Annotated[int, Field(ge=0)] | None = None
    device: WearableDevice | None = None
    source_last_modified_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> "WearableRecordBase":
        for value in (self.start_time, self.end_time, self.source_last_modified_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("wearable record times require timezone")
        if self.end_time < self.start_time:
            raise ValueError("end_time cannot be before start_time")
        return self


class StepsRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.STEPS]
    data: StepsData


class ExerciseRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.EXERCISE]
    data: ExerciseData


class DistanceRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.DISTANCE]
    data: DistanceData


class ElevationGainedRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.ELEVATION_GAINED]
    data: ElevationGainedData


class WeightRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.WEIGHT]
    data: WeightData


class RespiratoryRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.RESPIRATORY_RATE]
    data: RespiratoryRateData


class RestingHeartRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.RESTING_HEART_RATE]
    data: RestingHeartRateData


class HeartRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.HEART_RATE]
    data: HeartRateData

    @model_validator(mode="after")
    def samples_must_be_inside_record(self) -> "HeartRateRecord":
        if any(
            sample.time < self.start_time or sample.time > self.end_time
            for sample in self.data.samples
        ):
            raise ValueError("heart rate samples must be inside record interval")
        return self


class SleepRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.SLEEP]
    data: SleepData

    @model_validator(mode="after")
    def stages_must_be_inside_record(self) -> "SleepRecord":
        if any(
            stage.start_time < self.start_time or stage.end_time > self.end_time
            for stage in self.data.stages
        ):
            raise ValueError("sleep stages must be inside record interval")
        return self


class OxygenSaturationRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.OXYGEN_SATURATION]
    data: OxygenSaturationData


WearableRecord = Annotated[
    StepsRecord
    | ExerciseRecord
    | DistanceRecord
    | ElevationGainedRecord
    | WeightRecord
    | RespiratoryRateRecord
    | RestingHeartRateRecord
    | HeartRateRecord
    | SleepRecord
    | OxygenSaturationRecord,
    Field(discriminator="record_type"),
]


class WearableImportRequest(StrictApiModel):
    client_sync_id: UUID
    record_type: WearableRecordType
    health_context_complete: bool = False
    records: Annotated[list[WearableRecord], Field(max_length=1000)]
    deleted_record_ids: Annotated[list[ExternalRecordId], Field(max_length=1000)] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def validate_record_ids(self) -> "WearableImportRequest":
        if any(record.record_type != self.record_type for record in self.records):
            raise ValueError("every record must match request record_type")
        sources = {record.source_package for record in self.records}
        if len(sources) > 1:
            raise ValueError("a wearable import cannot mix data sources")
        if (
            self.health_context_complete
            and self.record_type is not WearableRecordType.HEART_RATE
        ):
            raise ValueError(
                "health_context_complete is only valid for heart_rate imports"
            )
        if (
            self.health_context_complete
            and "nodomain.freeyourgadget.gadgetbridge" in sources
        ):
            raise ValueError("Gadgetbridge imports lack verified exercise context")
        record_ids = [record.external_record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("records contain duplicate external_record_id")
        if len(self.deleted_record_ids) != len(set(self.deleted_record_ids)):
            raise ValueError("deleted_record_ids contain duplicates")
        if set(record_ids) & set(self.deleted_record_ids):
            raise ValueError("a record cannot be uploaded and deleted in one import")
        return self


class WearableImportResponse(BaseModel):
    import_id: UUID
    status: Literal["completed"] = "completed"
    records_created: int
    records_updated: int
    records_unchanged: int
    records_deleted: int


class HealthConnectRecordIdsResponse(BaseModel):
    ids: list[str]
    next_after: str | None


class WearableDashboardDay(BaseModel):
    day: date
    steps: int
    distance_meters: float


class WearableDashboardHeartRatePoint(BaseModel):
    observed_at: datetime
    beats_per_minute: int


class WearableDashboardSleepSession(BaseModel):
    start_time: datetime
    end_time: datetime
    stages: list[SleepStage]


class WearableDashboardTotals(BaseModel):
    heart_rate_samples: int
    heart_rate_minimum: int | None
    heart_rate_maximum: int | None
    heart_rate_average: float | None
    steps: int
    distance_meters: float
    oxygen_saturation_samples: int
    oxygen_saturation_minimum: float | None
    oxygen_saturation_average: float | None
    resting_heart_rate_samples: int
    resting_heart_rate_minimum: int | None
    sleep_sessions: int
    sleep_minutes: int


class WearableDashboardSource(BaseModel):
    source_package: str
    device: WearableDevice | None
    latest_observed_at: datetime
    daily: list[WearableDashboardDay]
    heart_rate_trend: list[WearableDashboardHeartRatePoint]
    sleep_sessions: list[WearableDashboardSleepSession]
    totals: WearableDashboardTotals


class WearableDashboardResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    sources: list[WearableDashboardSource]


class WearableLatestResponse(BaseModel):
    record_type: WearableRecordType
    observed_at: datetime
    data: dict[str, object]
    source_package: str


class HeartRateTrendPointResponse(BaseModel):
    observed_at: datetime
    beats_per_minute: PositiveInt


class ProfileCardResponse(BaseModel):
    id: UUID
    kind: Literal[ProfileChangeMode.CONFIRMATION, ProfileChangeMode.CLARIFICATION]
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation
    question: str
    proposed_value: dict[str, object] | None
    options: list["ProfileCardOption"]
    allow_custom_input: bool
    custom_input_placeholder: str | None
    created_at: datetime


class ProfileCardsResponse(BaseModel):
    cards: list[ProfileCardResponse]


class ProfileCardOption(BaseModel):
    id: Literal["accept", "reject", "kg", "jin"]
    label: str
    value: dict[str, object] | None = None


class PersonalProfileCardCustomAnswer(StrictApiModel):
    value: str | int | Decimal
    unit: PersonalProfileUnit | None = None


class HealthFactCardCustomAnswer(StrictApiModel):
    statement: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    assertion: FactAssertion
    temporal_status: FactTemporalStatus


class ProfileCardAnswerRequest(StrictApiModel):
    client_action_id: UUID
    option_id: Literal["accept", "reject", "kg", "jin"] | None = None
    custom_answer: (
        PersonalProfileCardCustomAnswer | HealthFactCardCustomAnswer | None
    ) = None

    @model_validator(mode="after")
    def require_one_answer(self) -> "ProfileCardAnswerRequest":
        if (self.option_id is None) == (self.custom_answer is None):
            raise ValueError("provide exactly one of option_id or custom_answer")
        return self


class ProfileCardDecisionRequest(StrictApiModel):
    client_action_id: UUID
    decision: Literal["accept", "reject"]


class ProfileCardDecisionResponse(BaseModel):
    card_id: UUID
    status: Literal[
        ProfileChangeStatus.APPLIED,
        ProfileChangeStatus.REJECTED,
        ProfileChangeStatus.CONFLICTED,
    ]


class PersonalProfileChangeRequest(StrictApiModel):
    client_action_id: UUID
    expected_revision: NonNegativeInt
    target_type: Literal[ProfileTargetType.PERSONAL_PROFILE]
    field_name: PersonalProfileField
    operation: Literal[ProfileOperation.SET, ProfileOperation.CLEAR]
    value: str | int | Decimal | None = None
    unit: PersonalProfileUnit | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "PersonalProfileChangeRequest":
        if self.operation is ProfileOperation.CLEAR:
            if self.value is not None or self.unit is not None:
                raise ValueError("clear operation cannot contain value or unit")
        elif self.value is None:
            raise ValueError("set operation requires value")
        return self


class HealthFactChangeRequest(StrictApiModel):
    client_action_id: UUID
    expected_revision: NonNegativeInt | None = None
    target_type: Literal[ProfileTargetType.HEALTH_FACT]
    operation: Literal[
        ProfileOperation.ADD,
        ProfileOperation.UPDATE,
        ProfileOperation.RETRACT,
    ]
    fact_type: HealthFactType
    target_id: UUID | None = None
    statement: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
        ]
        | None
    ) = None
    assertion: FactAssertion | None = None
    temporal_status: FactTemporalStatus | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "HealthFactChangeRequest":
        complete = (
            self.statement is not None
            and self.assertion is not None
            and self.temporal_status is not None
        )
        if self.operation is ProfileOperation.ADD:
            if self.target_id is not None or self.expected_revision is not None:
                raise ValueError("add operation cannot target an existing fact")
            if not complete:
                raise ValueError("add operation requires a complete fact")
        elif self.operation is ProfileOperation.UPDATE:
            if self.target_id is None or self.expected_revision is None:
                raise ValueError("update operation requires target and revision")
            if not complete:
                raise ValueError("update operation requires a complete fact")
        else:
            if self.target_id is None or self.expected_revision is None:
                raise ValueError("retract operation requires target and revision")
            if any(
                value is not None
                for value in (self.statement, self.assertion, self.temporal_status)
            ):
                raise ValueError("retract operation cannot contain replacement content")
        return self


HealthProfileChangeRequest = Annotated[
    PersonalProfileChangeRequest | HealthFactChangeRequest,
    Field(discriminator="target_type"),
]


class HealthProfileChangeResponse(BaseModel):
    change_id: UUID
    status: Literal[ProfileChangeStatus.APPLIED]
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation
    target_id: UUID | None
    result_revision: int


class HealthProfileResponse(BaseModel):
    personal_profile: PersonalProfileSnapshot
    health_facts: list[HealthFactSnapshot]
    wearable_latest: list[WearableLatestResponse]
    heart_rate_trend: list[HeartRateTrendPointResponse]


class InstrumentAnswerRequest(BaseModel):
    """患者在一道题上的选择。"""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[int, Field(ge=0, le=100)]


class PsychosocialAssessmentRequest(BaseModel):
    """一次心理社会评估：可以交逐题答案，也可以只交两个百分比。

    存百分比而不是原始分，因为 HFS 有 33 题、13 题、9 题三个版本，分母不同。
    带 `answers` 时百分比由服务端按当前题库重算（换量表不用重打包）；不带时沿用
    客户端算好的百分比，保证还没升级的 App 仍能提交。
    """

    model_config = ConfigDict(extra="forbid")

    answers: list[InstrumentAnswerRequest] | None = None
    fear_behavior_percent: Annotated[int, Field(ge=0, le=100)] | None = None
    fear_worry_percent: Annotated[int, Field(ge=0, le=100)] | None = None
    gold_score: Annotated[int | None, Field(ge=1, le=7)] = None
    instrument: Annotated[str, Field(max_length=64)] = "HFS-II"
    assessed_at: datetime

    @field_validator("assessed_at")
    @classmethod
    def require_offset(cls, value: datetime) -> datetime:
        """不带时区的时间无法判断是哪一天，直接拒掉。"""

        if value.tzinfo is None:
            raise ValueError("assessed_at 必须带时区")
        return value

    @model_validator(mode="after")
    def require_a_score(self) -> "PsychosocialAssessmentRequest":
        """逐题答案与两个百分比至少要有一份，否则这次提交没有意义。"""

        if self.answers is None and (
            self.fear_behavior_percent is None or self.fear_worry_percent is None
        ):
            raise ValueError("要么带逐题答案，要么带两个百分比")
        return self


class InstrumentOptionResponse(BaseModel):
    value: int
    label: str


class InstrumentQuestionResponse(BaseModel):
    id: str
    kind: Literal["behavior", "worry"]
    text: str


class PsychosocialInstrumentResponse(BaseModel):
    """当前题库。

    题目放服务端是为了换量表不用重打包，所以这里必须给全：说明、选项、题干、
    以及那一道「能不能感觉到低血糖」的 Gold 题。
    """

    id: str
    title: str
    instructions: str
    disclaimer: str
    options: list[InstrumentOptionResponse]
    questions: list[InstrumentQuestionResponse]
    gold_label: str
    gold_hint: str
    gold_options: list[InstrumentOptionResponse]


class PsychosocialProfileResponse(BaseModel):
    """服务器给 App 看的档位；不返回量表原始题目、逐题答案，也不返回分数。

    刻意不返回任何分数：回避分量表是我们自己写的六道题，把「你的回避倾向
    78 分」摆在患者面前，等于每天替医生下一个诊断，而这个分数撑不起那个
    分量。分数、意识水平与象限只走医护端（ClinicianPsychosocialResponse）。
    """

    assessed_at: datetime
    instrument: str
    weekly_contact_limit: int


class ClinicianPsychosocialResponse(BaseModel):
    """医护端的心理健康档位：两个分量表、综合分、意识水平与象限都在这里。"""

    assessed_at: datetime
    instrument: str
    fear_behavior_percent: int
    fear_worry_percent: int
    fear_percent: int
    fear_high: bool
    gold_score: int | None
    awareness: Literal["normal", "impaired", "unknown"]
    quadrant: Literal[
        "high_fear_normal_awareness",
        "high_fear_impaired_awareness",
        "low_fear_normal_awareness",
        "low_fear_impaired_awareness",
        "unknown_awareness",
    ]
    weekly_contact_limit: int


class BeliefNodeResponse(BaseModel):
    """信念图里的一个节点在窗口内的观测统计。"""

    belief_code: str
    label: str
    mentions: int
    reframes: int
    denials: int
    latest_quote: str
    latest_detected_at: datetime
    resolved: bool


class BehaviorLogResponse(BaseModel):
    """一条行为记录。带 id 是为了让患者自己删掉它。

    label 由服务端给出，和汇总卡片上的说法保持一致；不在客户端各写一套词表，
    否则同一条记录在两个界面里叫法会不一样。
    """

    id: UUID
    kind: str
    label: str
    count: int
    note: str | None
    occurred_at: datetime


class BeliefEventResponse(BaseModel):
    """一条信念观测。quote 是患者原话，删掉这条观测等于连那句原话一起删。"""

    id: UUID
    belief_code: str
    label: str
    quote: str
    occurrence: Literal["mentioned", "reframed", "denied"]
    detected_at: datetime


class FearSummaryResponse(BaseModel):
    """显性分数与行为代理的对照结果。

    behavior_counts 的键是行为类型，值是窗口内的总次数。
    discrepancy 只表达「两路证据一致不一致」，不是诊断。
    """

    window_start: datetime
    window_end: datetime
    behavior_counts: dict[str, int]
    behavior_total: int
    monitoring_weekly: float
    avoidance_weekly: float
    beliefs: list[BeliefNodeResponse]
    assessment: PsychosocialProfileResponse | None
    discrepancy: Literal[
        "aligned",
        "implicit_higher",
        "explicit_higher",
        "unknown",
    ]
    discrepancy_note: str
