"""侧边栏的会话层：新建一段对话、每段只看自己的消息、关怀消息单独一段。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.chat import prepare_chat_run
from antang_api.context.builder import build_chat_context
from antang_api.conversations import (
    CARE_CONVERSATION_TITLE,
    NEW_CONVERSATION_TITLE,
    care_conversation,
    conversation_message_counts,
    conversation_title,
    create_chat_conversation,
    list_conversations,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Conversation,
    PersonalProfile,
    ProactiveCareSettings,
    User,
)


async def create_user(db_session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:10]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            PersonalProfile(user_id=user.id),
            ProactiveCareSettings(user_id=user.id),
        ]
    )
    await db_session.flush()
    return user


async def finish_run(db_session: AsyncSession, run_id: UUID) -> None:
    """同一个用户同时只能有一个在跑的根 run，测试里要显式收尾才能发下一句。"""

    await db_session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(
            status=AgentRunStatus.COMPLETED,
            finished_at=datetime.now(UTC),
        )
    )
    await db_session.commit()


def test_the_title_comes_from_the_first_message() -> None:
    assert conversation_title("我又低血糖了，刚才测了 3.5。") == "我又低血糖了，刚才测了 3.5。"
    assert len(conversation_title("一" * 40)) == 24
    assert conversation_title("   ") == NEW_CONVERSATION_TITLE


async def test_a_new_conversation_starts_with_a_placeholder_title(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_new")

    conversation = await create_chat_conversation(db_session, user.id)
    await db_session.commit()

    assert conversation.kind == "chat"
    assert conversation.title == NEW_CONVERSATION_TITLE
    assert conversation.last_message_at is None


async def test_the_first_message_names_the_conversation(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_title")

    first = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "我又低血糖了，刚才测了 3.5，吃了半块饼干。",
    )

    listed = await list_conversations(db_session, user.id)
    assert len(listed) == 1
    assert listed[0].title.startswith("我又低血糖了")
    assert len(listed[0].title) <= 24
    assert listed[0].last_message_at is not None
    assert first.conversation_id == listed[0].id


async def test_each_conversation_only_carries_its_own_messages(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_scope")

    first = await prepare_chat_run(
        db_session, user.id, uuid4(), "我又低血糖了，测了 3.5。"
    )
    await finish_run(db_session, first.run_id)

    # 侧边栏点「新的对话」之后再发消息：不传 conversation_id 会落回最近一段，
    # 所以新会话必须由客户端显式带上 id。
    fresh = await create_chat_conversation(db_session, user.id)
    other = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "换个话题聊聊。",
        conversation_id=fresh.id,
    )
    assert other.conversation_id != first.conversation_id

    context = await build_chat_context(
        db_session,
        user.id,
        through_message_id=other.user_message_id,
        conversation_id=other.conversation_id,
    )
    rendered = " ".join(str(message.content) for message in context.messages)
    assert "换个话题" in rendered
    assert "低血糖" not in rendered


async def test_care_messages_do_not_mix_with_the_patients_own_chats(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_care")

    chat = await create_chat_conversation(db_session, user.id)
    care = await care_conversation(db_session, user.id)
    await db_session.commit()

    assert care.kind == "care"
    assert care.title == CARE_CONVERSATION_TITLE
    assert care.id != chat.id

    listed = await list_conversations(db_session, user.id)
    assert [conversation.id for conversation in listed] == [chat.id]


async def test_a_new_session_starts_once_the_last_one_is_hours_old(
    db_session: AsyncSession,
) -> None:
    """隔了 6 小时以上再开口算另一次对话：不然历史全堆一段，侧边栏只有一条。"""

    user = await create_user(db_session, "conv_gap")
    first = await prepare_chat_run(db_session, user.id, uuid4(), "今天血糖还行。")
    await finish_run(db_session, first.run_id)

    await db_session.execute(
        update(Conversation)
        .where(Conversation.id == first.conversation_id)
        .values(last_message_at=datetime.now(UTC) - timedelta(hours=7))
    )
    await db_session.commit()

    second = await prepare_chat_run(db_session, user.id, uuid4(), "又低血糖了。")

    assert second.conversation_id != first.conversation_id
    listed = await list_conversations(db_session, user.id)
    assert len(listed) == 2


async def test_a_quick_follow_up_stays_in_the_same_session(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_same")
    first = await prepare_chat_run(db_session, user.id, uuid4(), "今天血糖还行。")
    await finish_run(db_session, first.run_id)

    second = await prepare_chat_run(db_session, user.id, uuid4(), "晚饭吃了两碗饭。")

    assert second.conversation_id == first.conversation_id


async def test_the_list_counts_the_messages_in_each_session(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "conv_count")
    first = await prepare_chat_run(db_session, user.id, uuid4(), "今天血糖还行。")
    await finish_run(db_session, first.run_id)
    empty = await create_chat_conversation(db_session, user.id)
    await db_session.commit()

    counts = await conversation_message_counts(
        db_session, [first.conversation_id, empty.id]
    )

    assert counts[first.conversation_id] == 2  # 一条用户消息 + 一条待生成的回复
    assert counts.get(empty.id, 0) == 0
