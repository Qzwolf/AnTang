from collections.abc import AsyncIterator, Sequence
from typing import Annotated, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langchain.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from antang_api.agents.core import CoreAgentGraph
from antang_api.chat import (
    ActiveAgentRunError,
    ChatAttachmentError,
    ChatConversationRequired,
    ChatRetryError,
    DuplicateClientMessageError,
    PreparedChatRun,
    prepare_chat_retry,
    prepare_chat_run,
    stream_chat_run,
)
from antang_api.companion_memory import CompanionMemory
from antang_api.conversations import (
    conversation_message_counts,
    create_chat_conversation,
    list_conversations,
)
from antang_api.database import get_session
from antang_api.models import (
    AgentRun,
    Message,
    MessageAttachment,
    MessageAttachmentKind,
    User,
)
from antang_api.patient_access import get_patient_user
from antang_api.settings import settings
from antang_api.schemas.chat import (
    AttachmentUploadResponse,
    ChatAttachmentResponse,
    ChatMessageResponse,
    ConversationListResponse,
    ConversationResponse,
    LiveHeartRateReading,
    MessageHistoryResponse,
    SendMessageRequest,
    encode_stream_event,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
SUPPORTED_ATTACHMENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}


def require_chat_enabled() -> None:
    if settings.local_basic_mode:
        raise HTTPException(
            503,
            "当前为本地基础联调，智能聊天尚未配置。注册、档案和医护授权可正常使用。",
        )


async def _message_responses(
    session: AsyncSession,
    messages: Sequence[Message],
) -> list[ChatMessageResponse]:
    attachments = (
        list(
            await session.scalars(
                select(MessageAttachment).where(
                    MessageAttachment.message_id.in_(
                        [message.id for message in messages]
                    )
                )
            )
        )
        if messages
        else []
    )
    by_message = {
        attachment.message_id: ChatAttachmentResponse.model_validate(attachment)
        for attachment in attachments
        if attachment.message_id is not None
    }
    return [
        ChatMessageResponse.model_validate(message).model_copy(
            update={
                "attachments": (
                    [by_message[message.id]] if message.id in by_message else []
                )
            }
        )
        for message in messages
    ]


@router.post("/attachments", response_model=AttachmentUploadResponse)
async def upload_attachment(
    kind: Annotated[MessageAttachmentKind, Form()],
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AttachmentUploadResponse:
    """保存一份待发送的图片或 PDF。"""

    if file.content_type not in SUPPORTED_ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="只支持 PDF、JPG 和 PNG",
        )
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="附件不能为空")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="附件不能超过 10MB")

    filename = (file.filename or "附件").strip()[:255] or "附件"
    attachment = MessageAttachment(
        user_id=user.id,
        message_id=None,
        kind=kind,
        filename=filename,
        mime_type=file.content_type,
        size_bytes=len(data),
        data=data,
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)
    return AttachmentUploadResponse.model_validate(attachment)


@router.get("/attachments/{attachment_id}/content", response_class=Response)
async def get_attachment_content(
    attachment_id: UUID,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """返回当前用户上传的图片或报告原文件。"""

    attachment = await session.scalar(
        select(MessageAttachment).where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.user_id == user.id,
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="找不到附件")
    return Response(
        content=attachment.data,
        media_type=attachment.mime_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


def _stream_response(
    http_request: Request,
    session: AsyncSession,
    user: User,
    prepared_run: PreparedChatRun,
    live_heart_rate: LiveHeartRateReading | None = None,
) -> StreamingResponse:
    model = cast(BaseChatModel, http_request.app.state.chat_model)
    agent = cast(CoreAgentGraph, http_request.app.state.core_agent)
    tools = cast(Sequence[BaseTool], http_request.app.state.core_tools)
    companion_memory = cast(
        CompanionMemory,
        http_request.app.state.companion_memory,
    )

    async def ndjson_stream() -> AsyncIterator[str]:
        async for event in stream_chat_run(
            session=session,
            model=model,
            agent=agent,
            tools=tools,
            companion_memory=companion_memory,
            user_id=user.id,
            prepared_run=prepared_run,
            live_heart_rate=live_heart_rate,
        ):
            yield encode_stream_event(event)

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/messages", response_model=None, dependencies=[Depends(require_chat_enabled)]
)
async def send_message(
    request: SendMessageRequest,
    http_request: Request,  # FastAPI 原生的请求对象（包含 app 引用）
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse | JSONResponse:
    """保存用户消息，并以 NDJSON 持续返回 Core Agent 的执行事件。"""

    try:
        prepared_run = await prepare_chat_run(
            session=session,
            user_id=user.id,
            client_message_id=request.client_message_id,
            content=request.content,
            attachment_id=request.attachment_id,
            conversation_id=request.conversation_id,
        )

    except DuplicateClientMessageError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "duplicate_client_message",
                "message": "这条消息已经提交",
            },
        )
    except ActiveAgentRunError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "active_agent_run",
                "message": "上一条消息仍在处理中",
            },
        )
    except ChatAttachmentError as error:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "attachment_unavailable", "message": str(error)},
        )
    except ChatConversationRequired as error:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "conversation_not_found", "message": str(error)},
        )

    return _stream_response(
        http_request,
        session,
        user,
        prepared_run,
        request.live_heart_rate,
    )


@router.post(
    "/messages/{failed_assistant_message_id}/retry",
    response_model=None,
    dependencies=[Depends(require_chat_enabled)],
)
async def retry_message(
    failed_assistant_message_id: UUID,
    http_request: Request,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse | JSONResponse:
    """重试失败或取消的回答，不创建第二条用户消息。"""

    try:
        prepared_run = await prepare_chat_retry(
            session,
            user.id,
            failed_assistant_message_id,
        )
    except ChatRetryError as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "retry_message_not_found"
            else status.HTTP_409_CONFLICT
        )
        return JSONResponse(
            status_code=status_code,
            content={"code": error.code, "message": str(error)},
        )
    except ActiveAgentRunError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "active_agent_run",
                "message": "上一条消息仍在处理中",
            },
        )

    return _stream_response(http_request, session, user, prepared_run)


@router.get("/conversations", response_model=ConversationListResponse)
async def get_conversations(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConversationListResponse:
    """侧边栏的对话列表，按最近一条消息倒序。"""

    conversations = await list_conversations(session, user.id, limit=limit)
    counts = await conversation_message_counts(
        session, [conversation.id for conversation in conversations]
    )
    return ConversationListResponse(
        conversations=[
            ConversationResponse.model_validate(conversation).model_copy(
                update={"message_count": counts.get(conversation.id, 0)}
            )
            for conversation in conversations
        ]
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    """「新的对话」：建一段空会话，之后的消息带 conversation_id 发进来。"""

    conversation = await create_chat_conversation(session, user.id)
    await session.commit()
    return ConversationResponse.model_validate(conversation)


@router.get("/messages", response_model=MessageHistoryResponse)
async def get_message_history(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    before: Annotated[UUID | None, Query()] = None,
    conversation_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessageHistoryResponse:
    """使用消息 UUIDv7 游标读取一页历史，并按时间升序返回。"""

    previous_run = aliased(AgentRun)
    later_run = aliased(AgentRun)
    superseded_core_answer = exists(
        select(later_run.id)
        .select_from(previous_run)
        .join(
            later_run,
            and_(
                later_run.user_id == previous_run.user_id,
                later_run.trigger_message_id == previous_run.trigger_message_id,
                later_run.parent_run_id.is_(None),
                later_run.agent_name == "core_agent",
                later_run.id > previous_run.id,
            ),
        )
        .where(
            previous_run.result_message_id == Message.id,
            previous_run.parent_run_id.is_(None),
            previous_run.agent_name == "core_agent",
        )
    )
    query = select(Message).where(
        Message.user_id == user.id,
        ~superseded_core_answer,
    )

    if conversation_id is not None:
        query = query.where(Message.conversation_id == conversation_id)

    if before is not None:
        query = query.where(Message.id < before)

    newest_first = list(
        await session.scalars(
            query.order_by(Message.id.desc()).limit(limit + 1),
        )
    )
    has_more = len(newest_first) > limit
    page = newest_first[:limit]
    next_before = page[-1].id if has_more else None

    return MessageHistoryResponse(
        messages=await _message_responses(session, list(reversed(page))),
        next_before=next_before,
    )


@router.get(
    "/messages/{message_id}/window",
    response_model=MessageHistoryResponse,
)
async def get_message_window(
    message_id: UUID,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageHistoryResponse:
    """返回目标消息前后各最多 25 条消息，供旧通知准确定位。"""

    target = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.user_id == user.id,
        )
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到聊天消息",
        )

    previous = list(
        reversed(
            list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.user_id == user.id,
                        Message.id < target.id,
                    )
                    .order_by(Message.id.desc())
                    .limit(25)
                )
            )
        )
    )
    following = list(
        await session.scalars(
            select(Message)
            .where(
                Message.user_id == user.id,
                Message.id > target.id,
            )
            .order_by(Message.id)
            .limit(25)
        )
    )
    return MessageHistoryResponse(
        messages=await _message_responses(
            session,
            [*previous, target, *following],
        ),
        next_before=None,
    )
