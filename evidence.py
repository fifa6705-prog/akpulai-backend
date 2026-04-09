"""
악플AI — 증거 저장 API
POST /api/evidence/save  → 악플 증거 저장
GET  /api/evidence/{id}  → 증거 조회
"""

import hashlib, time, logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.store import save_evidence, _json_read

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evidence", tags=["evidence"])


class EvidenceReq(BaseModel):
    channel_id:     str
    comment_id:     Optional[str] = None
    comment_text:   str
    author_name:    Optional[str] = None
    author_channel: Optional[str] = None
    comment_url:    Optional[str] = None
    posted_at:      Optional[str] = None   # 댓글 원본 작성 시각
    analysis_type:  str                    # toxic / warn
    analysis_score: int


@router.post("/save")
async def save_evidence_record(req: EvidenceReq):
    if req.analysis_score < 0 or req.analysis_score > 100:
        raise HTTPException(400, "analysis_score는 0~100 사이여야 합니다")

    data = req.dict()
    data["type"] = "evidence"

    # 증거 고유 ID — 채널+댓글+타임스탬프 해시
    raw = f"{req.channel_id}{req.comment_id or req.comment_text[:20]}{time.time()}"
    evidence_id = "ev_" + hashlib.sha256(raw.encode()).hexdigest()[:10]
    data["evidence_id"] = evidence_id

    await save_evidence(data)

    logger.info(
        f"[EVIDENCE] id={evidence_id} channel={req.channel_id} "
        f"type={req.analysis_type} score={req.analysis_score}"
    )
    return {
        "success":     True,
        "evidence_id": evidence_id,
        "message":     "증거가 저장되었습니다",
    }


@router.get("/list/{channel_id}")
async def list_evidence(channel_id: str, limit: int = 50):
    """채널별 저장된 증거 목록 조회"""
    all_ev = await _json_read("evidence", "evidence", limit=500)
    filtered = [e for e in all_ev if e.get("channel_id") == channel_id]
    return {
        "channel_id": channel_id,
        "count":      len(filtered),
        "evidence":   filtered[:limit],
    }
