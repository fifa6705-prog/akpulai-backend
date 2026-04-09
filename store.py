"""
악플AI — 저장 추상화 레이어
현재: JSON 파일 저장 (Railway Volume)
향후: SUPABASE_URL 환경변수 설정 시 자동으로 Supabase 전환
이 파일만 수정하면 전체 저장소 교체 가능
"""

import json, os, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 환경 감지 ────────────────────────────────────────────
USE_SUPABASE = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Supabase 클라이언트 (설정된 경우만 초기화) ──────────
_supabase = None
if USE_SUPABASE:
    try:
        from supabase import create_client
        _supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
        logger.info("✅ Supabase 연결 완료")
    except Exception as e:
        logger.warning(f"⚠️ Supabase 연결 실패 → JSON 저장으로 fallback: {e}")
        USE_SUPABASE = False


# ══════════════════════════════════════════════════════════
# PUBLIC API — 이 함수들만 외부에서 호출
# ══════════════════════════════════════════════════════════

async def save_lead(data: dict) -> str:
    """
    리드/베타신청 저장
    반환: 생성된 ID
    """
    data = _enrich(data)
    if USE_SUPABASE:
        return await _supabase_insert("leads", data)
    return await _json_append("leads", data)


async def save_evidence(data: dict) -> str:
    """
    증거 저장
    반환: 생성된 evidence_id
    """
    data = _enrich(data)
    if USE_SUPABASE:
        return await _supabase_insert("evidence", data)
    return await _json_append("evidence", data)


async def get_leads(
    lead_type: Optional[str] = None,
    limit: int = 100
) -> list:
    """
    리드 조회 (관리자용)
    lead_type: free_trial / beta_apply / None(전체)
    """
    if USE_SUPABASE:
        return await _supabase_select("leads", lead_type, limit)
    return await _json_read("leads", lead_type, limit)


async def get_stats() -> dict:
    """
    간단한 통계 (관리자 대시보드용)
    """
    all_leads = await get_leads(limit=9999)
    free_trial = [l for l in all_leads if l.get("type") == "free_trial"]
    beta_apply = [l for l in all_leads if l.get("type") == "beta_apply"]
    return {
        "total":       len(all_leads),
        "free_trial":  len(free_trial),
        "beta_apply":  len(beta_apply),
        "storage":     "supabase" if USE_SUPABASE else "json",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════
# INTERNAL — JSON 저장 구현
# ══════════════════════════════════════════════════════════

async def _json_append(collection: str, data: dict) -> str:
    file_path = DATA_DIR / f"{collection}.json"
    existing = []
    if file_path.exists():
        try:
            existing = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"JSON 읽기 실패: {e}")
            existing = []
    existing.append(data)
    try:
        file_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"JSON 저장 실패: {e}")
        raise
    logger.info(f"✅ JSON 저장: {collection}/{data.get('id')}")
    return data.get("id", "")


async def _json_read(
    collection: str,
    filter_type: Optional[str],
    limit: int
) -> list:
    file_path = DATA_DIR / f"{collection}.json"
    if not file_path.exists():
        return []
    try:
        all_data = json.loads(file_path.read_text(encoding="utf-8"))
        if filter_type:
            all_data = [d for d in all_data if d.get("type") == filter_type]
        # 최신순 정렬
        all_data.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return all_data[:limit]
    except Exception as e:
        logger.error(f"JSON 읽기 실패: {e}")
        return []


# ══════════════════════════════════════════════════════════
# INTERNAL — Supabase 구현 (향후 활성화)
# ══════════════════════════════════════════════════════════

async def _supabase_insert(table: str, data: dict) -> str:
    try:
        res = _supabase.table(table).insert(data).execute()
        return data.get("id", "")
    except Exception as e:
        logger.error(f"Supabase 저장 실패 → JSON fallback: {e}")
        return await _json_append(table, data)


async def _supabase_select(
    table: str,
    filter_type: Optional[str],
    limit: int
) -> list:
    try:
        q = _supabase.table(table).select("*").order(
            "created_at", desc=True
        ).limit(limit)
        if filter_type:
            q = q.eq("type", filter_type)
        res = q.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Supabase 조회 실패 → JSON fallback: {e}")
        return await _json_read(table, filter_type, limit)


# ══════════════════════════════════════════════════════════
# UTILS
# ══════════════════════════════════════════════════════════

def _enrich(data: dict) -> dict:
    """공통 필드 자동 추가"""
    import time, hashlib
    now = datetime.now(timezone.utc).isoformat()
    data["created_at"] = data.get("created_at") or now
    # ID 생성: type_타임스탬프_해시4자리
    raw = f"{data.get('type','x')}{time.time()}"
    short = hashlib.sha256(raw.encode()).hexdigest()[:6]
    data["id"] = f"{data.get('type','lead')}_{int(time.time())}_{short}"
    return data
