"""把一次量表结果整理成医护端能读的档位。

患者端只拿到「每周联系几次」（见 `PsychosocialProfileResponse`）：回避分量表
是我们自己写的六道题，把百分比摊给患者看等于每天替他下一个诊断。分数、两个
分量表、意识水平和象限都留在这里，由路由那一层的角色与授权校验决定谁能读。
"""

from __future__ import annotations

from antang_api.models import PsychosocialAssessment
from antang_api.psychosocial.support import PsychosocialProfile
from antang_api.schemas.health_profile import (
    ClinicianPsychosocialResponse,
    PsychosocialProfileResponse,
)


def clinician_assessment_response(
    row: PsychosocialAssessment,
) -> ClinicianPsychosocialResponse:
    profile = PsychosocialProfile(
        fear_behavior_percent=row.fear_behavior_percent,
        fear_worry_percent=row.fear_worry_percent,
        gold_score=row.gold_score,
    )
    return ClinicianPsychosocialResponse(
        assessed_at=row.assessed_at,
        instrument=row.instrument,
        fear_behavior_percent=row.fear_behavior_percent,
        fear_worry_percent=row.fear_worry_percent,
        fear_percent=profile.fear_percent,
        fear_high=profile.fear_high,
        gold_score=profile.gold_score,
        awareness=profile.awareness,
        quadrant=profile.quadrant,
        weekly_contact_limit=profile.weekly_contact_limit,
    )


def patient_assessment_response(
    row: PsychosocialAssessment,
) -> PsychosocialProfileResponse:
    """患者端只回行为结果，不回分数。"""

    profile = PsychosocialProfile(
        fear_behavior_percent=row.fear_behavior_percent,
        fear_worry_percent=row.fear_worry_percent,
        gold_score=row.gold_score,
    )
    return PsychosocialProfileResponse(
        assessed_at=row.assessed_at,
        instrument=row.instrument,
        weekly_contact_limit=profile.weekly_contact_limit,
    )
