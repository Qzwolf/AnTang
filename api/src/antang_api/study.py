"""Read research metadata only after the clinical route has checked patient access."""
from sqlalchemy import func, or_, select
from antang_api.models.study import StudyEvent, StudySubject
from antang_api.schemas.clinical import StudyResponse


async def read_study(session, user_id, begin, finish):
    subject = await session.get(StudySubject, user_id)
    if subject is None:
        return None
    condition = (StudyEvent.user_id == user_id, StudyEvent.measured_at < finish,
                 or_(StudyEvent.measured_at >= begin, StudyEvent.ended_at > begin))
    count = await session.scalar(select(func.count()).select_from(StudyEvent).where(*condition))
    rows = list(await session.scalars(select(StudyEvent).where(*condition)
        .order_by(StudyEvent.measured_at.desc(), StudyEvent.id.desc()).limit(1000)))
    rows.reverse()
    return StudyResponse.model_validate({'dataset': subject.dataset, 'insulin_type': subject.insulin_type,
            'period_start': subject.period_start, 'period_end': subject.period_end,
            'provenance': subject.provenance, 'event_count': count,
            'events_truncated': count > len(rows), 'events': rows})
