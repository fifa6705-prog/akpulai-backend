"""
악플AI — 리드 수집 API
POST /api/lead/free-trial  → 7일 무료 체험 신청
POST /api/lead/beta-apply  → 베타 신청
"""

import re, logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, validator
from typing import Optional
from storage.store import save_lead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lead", tags=["leads"])

# ── 공통 유틸 ────────────────────────────────────────────

def _is_valid_email(v: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v))

def _is_valid_contact(v: str) -> bool:
    """카카오ID / 전화번호 / 기타 연락처 — 2자 이상이면 허용"""
    return len(v.strip()) >= 2

def _check_contact(email: Optional[str], contact: Optional[str]):
    if not email and not contact:
        raise HTTPException(400, "이메일 또는 연락처 중 하나는 필수입니다")
    if email and not _is_valid_email(email):
        raise HTTPException(400, "올바른 이메일 형식이 아닙니다")

def _check_channel(name: str):
    if not name or len(name.strip()) < 1:
        raise HTTPException(400, "채널명을 입력해주세요")


# ══════════════════════════════════════════════════════════
# 1. 7일 무료 체험 신청
# ══════════════════════════════════════════════════════════

class FreeTrialReq(BaseModel):
    name:                  Optional[str] = None
    email:                 Optional[str] = None
    contact:               Optional[str] = None   # 카카오ID / 전화 등
    youtube_channel_name:  str
    youtube_channel_url:   Optional[str] = None
    current_comment_pain:  Optional[str] = None   # 현재 어떤 고민인지 (선택)
    source_page:           str = "main"            # main / analyzer / pricing / beta

    @validator("youtube_channel_name")
    def channel_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("채널명을 입력해주세요")
        return v.strip()

    @validator("source_page")
    def valid_source(cls, v):
        allowed = {"main", "analyzer", "pricing", "beta", "simulator"}
        return v if v in allowed else "main"


@router.post("/free-trial")
async def free_trial(req: FreeTrialReq, request: Request):
    # 이메일 또는 연락처 필수
    _check_contact(req.email, req.contact)
    _check_channel(req.youtube_channel_name)

    data = req.dict()
    data["type"]      = "free_trial"
    data["client_ip"] = request.client.host if request.client else None

    record_id = await save_lead(data)

    logger.info(
        f"[FREE_TRIAL] channel={req.youtube_channel_name} "
        f"source={req.source_page} id={record_id}"
    )
    return {
        "success": True,
        "id":      record_id,
        "message": (
            "7일 무료 체험 신청이 완료되었습니다 🎉\n"
            "검토 후 빠른 시일 내 안내드릴게요!"
        ),
    }


# ══════════════════════════════════════════════════════════
# 2. 베타 신청
# ══════════════════════════════════════════════════════════

class BetaApplyReq(BaseModel):
    name:                  str
    email:                 Optional[str] = None
    contact:               Optional[str] = None
    youtube_channel_name:  str
    youtube_channel_url:   Optional[str] = None
    category:              Optional[str] = None   # 뷰티/일상/게임/푸드 등
    why_join_beta:         Optional[str] = None   # 신청 이유
    can_give_feedback:     bool = False
    willing_interview:     bool = False
    join_community:        bool = False

    @validator("name")
    def name_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("이름을 입력해주세요")
        return v.strip()

    @validator("youtube_channel_name")
    def channel_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("채널명을 입력해주세요")
        return v.strip()


@router.post("/beta-apply")
async def beta_apply(req: BetaApplyReq, request: Request):
    _check_contact(req.email, req.contact)
    _check_channel(req.youtube_channel_name)

    data = req.dict()
    data["type"]      = "beta_apply"
    data["client_ip"] = request.client.host if request.client else None

    # 우선순위 점수 계산 (내부용 — 선별 심사에 활용)
    priority = 0
    if req.can_give_feedback:  priority += 30
    if req.willing_interview:  priority += 40
    if req.join_community:     priority += 20
    if req.why_join_beta:      priority += 10
    data["_priority_score"] = priority   # 관리자만 볼 수 있는 내부 필드

    record_id = await save_lead(data)

    logger.info(
        f"[BETA_APPLY] name={req.name} channel={req.youtube_channel_name} "
        f"priority={priority} id={record_id}"
    )
    return {
        "success":  True,
        "id":       record_id,
        "message": (
            "베타 신청이 완료되었습니다 🙏\n"
            "신청 내용을 검토한 후 개별적으로 연락드릴게요.\n"
            "선별 승인 후 온보딩 안내를 드립니다!"
        ),
    }
