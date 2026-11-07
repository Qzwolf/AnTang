import asyncio

import httpx
from hindsight_client import Hindsight
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from antang_api.agents.proactive_care import (
    build_proactive_care_agent,
    build_proactive_care_auditor,
)
from antang_api.companion_memory import CompanionMemory
from antang_api.database import engine
from antang_api.llm import build_proactive_care_model
from antang_api.log_config import configure_logging
from antang_api.proactive_care.processor import ProactiveCareProcessor
from antang_api.proactive_care.refine import build_wording_refiner
from antang_api.proactive_care.push import ExpoPushClient, PushDeliveryWorker
from antang_api.proactive_care.worker import ProactiveCareWorker
from antang_api.settings import settings


async def run_worker() -> None:
    """创建 Worker 独享的外部客户端与 Agent，并持续处理持久任务。"""

    if settings.local_basic_mode:
        raise RuntimeError(
            "本地基础联调未启用主动关怀，请配置完整 AI 服务后启动 Worker。"
        )

    configure_logging()
    hindsight_client = Hindsight(
        base_url=settings.hindsight_base_url,
        api_key=(
            settings.hindsight_api_key.get_secret_value()
            if settings.hindsight_api_key is not None
            else None
        ),
        timeout=settings.hindsight_timeout_seconds,
        user_agent="antang-proactive-care-worker",
    )
    try:
        async with httpx.AsyncClient(
            base_url="https://exp.host",
            timeout=settings.expo_push_timeout_seconds,
        ) as push_http_client:
            async with AsyncPostgresSaver.from_conn_string(
                settings.langgraph_database_url
            ) as checkpointer:
                await checkpointer.setup()
                # 主动关怀改用自己那把模型：worker 在后台慢慢跑，不占对话流；
                # 但真正发消息的是它，所以这里宁可用更稳的那个。
                model = build_proactive_care_model()
                processor = ProactiveCareProcessor(
                    agent=build_proactive_care_agent(model, checkpointer),
                    auditor=build_proactive_care_auditor(model, checkpointer),
                    companion_memory=CompanionMemory(
                        hindsight_client,
                        mode=settings.hindsight_mode,
                        retain_user_turns=settings.hindsight_retain_user_turns,
                        recall_budget=settings.hindsight_recall_budget,
                        recall_max_tokens=settings.hindsight_recall_max_tokens,
                        recall_timeout_seconds=settings.hindsight_recall_timeout_seconds,
                        retain_timeout_seconds=settings.hindsight_retain_timeout_seconds,
                    ),
                    token_model=model,
                    model_name=settings.resolved_proactive_model_name,
                    refiner=build_wording_refiner(model),
                )
                access_token = (
                    settings.expo_push_access_token.get_secret_value()
                    if settings.expo_push_access_token is not None
                    else None
                )
                care_worker = ProactiveCareWorker(process_task=processor.process)
                push_worker = PushDeliveryWorker(
                    client=ExpoPushClient(
                        push_http_client,
                        access_token=access_token or None,
                    )
                )
                async with asyncio.TaskGroup() as workers:
                    workers.create_task(care_worker.run_forever())
                    workers.create_task(push_worker.run_forever())
    finally:
        await hindsight_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_worker())
