from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from antang_api.models import MessageAttachmentKind, MessageRole, MessageStatus

MessageContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=2000),
]
ActivityPhase: TypeAlias = Literal[
    "thinking",
    "searching",
    "reading",
    "organizing",
]


class LiveHeartRateReading(BaseModel):
    """手机正在进行的一次实时心率广播中最近的一条读数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    beats_per_minute: int = Field(ge=20, le=300)
    measured_at: datetime
    device_name: str | None = Field(default=None, max_length=100)


class SendMessageRequest(BaseModel):
    """手机发送一条用户消息时提交的数据。"""

    # 手机生成。同一次发送即使因网络问题重试，也继续使用同一个 ID。
    client_message_id: UUID
    content: MessageContent
    attachment_id: UUID | None = None
    # 发到哪一段对话；老客户端不带，就接到最近一段。
    conversation_id: UUID | None = None
    # 手机此刻正在接收的实时心率广播读数；没有实时监测时为 None。
    live_heart_rate: LiveHeartRateReading | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "SendMessageRequest":
        if not self.content and self.attachment_id is None:
            raise ValueError("消息文字和附件不能同时为空")
        return self


class ChatAttachmentResponse(BaseModel):
    """聊天历史中展示附件所需的元数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: MessageAttachmentKind
    filename: str
    mime_type: str
    size_bytes: int


class AttachmentUploadResponse(ChatAttachmentResponse):
    """附件上传成功后的响应。"""


## 聊天流包含消息生命周期、Agent 活动阶段和文字增量。


class MessageStartedEvent(BaseModel):
    """事件：后端已经保存消息，并开始执行 Core Agent。"""

    type: Literal["message_started"] = "message_started"
    user_message_id: UUID
    assistant_message_id: UUID
    run_id: UUID


class TextDeltaEvent(BaseModel):
    """事件：模型刚刚生成的一小段文字。"""

    type: Literal["text_delta"] = "text_delta"
    delta: str


class AgentActivityEvent(BaseModel):
    """事件：Core Agent 当前正在执行的用户可理解阶段。"""

    type: Literal["agent_activity"] = "agent_activity"
    phase: ActivityPhase


class ChatSource(BaseModel):
    """一条助手消息实际引用的网页。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: Annotated[str, StringConstraints(pattern=r"^S[1-9]\d*$")]
    title: str
    url: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("来源标题必须是非空且已规范化的文字")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            value != value.strip()
            or parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
        ):
            raise ValueError("来源 URL 必须是已规范化的 http(s) URL")
        return value


class MessageCompletedEvent(BaseModel):
    """事件：回答已经完整生成并保存。"""

    type: Literal["message_completed"] = "message_completed"
    input_tokens: int
    output_tokens: int
    sources: list[ChatSource]


class MessageFailedEvent(BaseModel):
    """事件：本次 Agent 执行失败。"""

    type: Literal["message_failed"] = "message_failed"
    code: Literal["agent_run_failed"] = "agent_run_failed"
    error_type: str


# {"type":"message_started", ...}
# {"type":"agent_activity", "phase":"searching"}
# {"type": "text_delta", "delta": "我"}
# {"type": "text_delta", "delta": "在这里"}
# {"type": "message_completed", "input_tokens": 100, "output_tokens": 20}

# 一个 ChatStreamEvent 是上面五种事件之一。
ChatStreamEvent = (
    MessageStartedEvent
    | AgentActivityEvent
    | TextDeltaEvent
    | MessageCompletedEvent
    | MessageFailedEvent
)


def encode_stream_event(event: ChatStreamEvent) -> str:
    """把一个事件编码成 NDJSON 中的一行。"""

    return event.model_dump_json() + "\n"


class ChatMessageResponse(BaseModel):
    """历史消息接口返回的一条原始聊天消息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_message_id: UUID | None
    role: MessageRole
    status: MessageStatus
    content: str
    sources: list[ChatSource]
    attachments: list[ChatAttachmentResponse] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None


class MessageHistoryResponse(BaseModel):
    """按时间升序返回的一页聊天历史。"""

    messages: list[ChatMessageResponse]
    next_before: UUID | None


class ConversationResponse(BaseModel):
    """侧边栏里的一段对话。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    title: str
    created_at: datetime
    last_message_at: datetime | None
    # 侧边栏靠它藏掉「点了新的对话却一句没说」的空段。
    message_count: int = 0


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
