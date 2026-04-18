"""
악플AI (AkpulAI) — 통합 백엔드 v4.1 (5단계 하이브리드)
YouTube Data API v3 + GPT-4o mini + 리드 수집

실행: uvicorn main:app --host 0.0.0.0 --port $PORT

Railway 환경변수:
  YOUTUBE_API_KEY=AIza...
  OPENAI_API_KEY=sk-proj-...
  ADMIN_TOKEN=akpulai_admin_2026
  SUPABASE_URL=https://xxx.supabase.co  (선택)
  SUPABASE_KEY=your-anon-key            (선택)
"""

import os, re, json, time, hashlib, logging, secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── 로깅 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 환경변수 ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
ADMIN_TOKEN     = os.getenv("ADMIN_TOKEN", "")
SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")

# ── 앱 ───────────────────────────────────────────────────
app = FastAPI(title="악플AI API", version="4.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://akpulai.com",
        "https://www.akpulai.com",
        "https://akpul.kr",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "x-admin-token"],
    allow_credentials=False,
)

# ══════════════════════════════════════════════════════════
# STORAGE — JSON 파일 저장 (Supabase 미설정 시 자동 사용)
# ══════════════════════════════════════════════════════════

DATA_DIR = Path("/tmp/akpulai_data")  # nosec B108 - Railway 의도적 사용
DATA_DIR.mkdir(exist_ok=True)

USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
_supabase = None
if USE_SUPABASE:
    try:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase 연결 완료")
    except Exception as e:
        logger.warning(f"⚠️ Supabase 연결 실패 → JSON 저장: {e}")
        USE_SUPABASE = False


def _make_id(prefix: str) -> str:
    raw = f"{prefix}{time.time()}"
    return f"{prefix}_{int(time.time())}_{hashlib.sha256(raw.encode()).hexdigest()[:6]}"


async def _json_save(collection: str, data: dict) -> str:
    fp = DATA_DIR / f"{collection}.json"
    existing = []
    if fp.exists():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except:
            existing = []
    existing.append(data)
    fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return data.get("id", "")


async def _json_load(collection: str, filter_type: Optional[str] = None, limit: int = 100) -> list:
    fp = DATA_DIR / f"{collection}.json"
    if not fp.exists():
        return []
    try:
        all_data = json.loads(fp.read_text(encoding="utf-8"))
        if filter_type:
            all_data = [d for d in all_data if d.get("type") == filter_type]
        all_data.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return all_data[:limit]
    except:
        return []


async def save_record(data: dict) -> str:
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data["id"] = _make_id(data.get("type", "record"))
    if USE_SUPABASE and _supabase:
        try:
            _supabase.table("leads").insert(data).execute()
            return data["id"]
        except Exception as e:
            logger.error(f"Supabase 저장 실패 → JSON fallback: {e}")
    return await _json_save("leads", data)


async def load_records(filter_type: Optional[str] = None, limit: int = 100) -> list:
    if USE_SUPABASE and _supabase:
        try:
            q = _supabase.table("leads").select("*").order("created_at", desc=True).limit(limit)
            if filter_type:
                q = q.eq("type", filter_type)
            return q.execute().data or []
        except Exception as e:
            logger.error(f"Supabase 조회 실패 → JSON fallback: {e}")
    return await _json_load("leads", filter_type, limit)


# ══════════════════════════════════════════════════════════
# 리드 수집 API
# ══════════════════════════════════════════════════════════

def _check_contact(email: Optional[str], contact: Optional[str]):
    if not email and not contact:
        raise HTTPException(400, "이메일 또는 연락처 중 하나는 필수입니다")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "올바른 이메일 형식이 아닙니다")


class FreeTrialReq(BaseModel):
    name:                 Optional[str] = None
    email:                Optional[str] = None
    contact:              Optional[str] = None
    youtube_channel_name: str
    youtube_channel_url:  Optional[str] = None
    current_comment_pain: Optional[str] = None
    source_page:          str = "main"

    @field_validator("youtube_channel_name")
    @classmethod
    def channel_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("채널명을 입력해주세요")
        if len(v) > 200:
            raise ValueError("채널명이 너무 깁니다")
        return v.strip()


class BetaApplyReq(BaseModel):
    name:                 str
    email:                Optional[str] = None
    contact:              Optional[str] = None
    youtube_channel_name: str
    youtube_channel_url:  Optional[str] = None
    category:             Optional[str] = None
    why_join_beta:        Optional[str] = None
    can_give_feedback:    bool = False
    willing_interview:    bool = False
    join_community:       bool = False

    @field_validator("youtube_channel_name")
    @classmethod
    def channel_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("채널명을 입력해주세요")
        if len(v) > 200:
            raise ValueError("채널명이 너무 깁니다")
        return v.strip()


@app.post("/api/lead/free-trial")
async def free_trial(req: FreeTrialReq, request: Request):
    _check_contact(req.email, req.contact)
    data = req.model_dump()
    data["type"] = "free_trial"
    data["client_ip"] = request.client.host if request.client else None
    record_id = await save_record(data)
    logger.info(f"[FREE_TRIAL] channel={req.youtube_channel_name} source={req.source_page} id={record_id}")
    return {
        "success": True,
        "id": record_id,
        "message": "7일 무료 체험 신청이 완료되었습니다 🎉 검토 후 빠른 시일 내 안내드릴게요!",
    }


@app.post("/api/lead/beta-apply")
async def beta_apply(req: BetaApplyReq, request: Request):
    _check_contact(req.email, req.contact)
    data = req.model_dump()
    data["type"] = "beta_apply"
    data["client_ip"] = request.client.host if request.client else None
    # 우선순위 점수 (내부 선별용)
    priority = (30 if req.can_give_feedback else 0) + \
               (40 if req.willing_interview  else 0) + \
               (20 if req.join_community     else 0) + \
               (10 if req.why_join_beta      else 0)
    data["_priority_score"] = priority
    record_id = await save_record(data)
    logger.info(f"[BETA_APPLY] name={req.name} channel={req.youtube_channel_name} priority={priority} id={record_id}")
    return {
        "success": True,
        "id": record_id,
        "message": "베타 신청이 완료되었습니다 🙏 검토 후 개별적으로 연락드릴게요!",
    }


# ══════════════════════════════════════════════════════════
# 댓글 분석 API — 5단계 하이브리드
# ══════════════════════════════════════════════════════════
#
# Stage 1: 하드 규칙 (욕설/초성체/혐오)       → 즉시 판정, GPT 스킵
# Stage 2: 소프트 규칙 (조롱/비꼬기 패턴)     → 문맥 패턴 매칭 강화
# Stage 3: 문맥 패턴 분석 (정규식 기반)       → 우회 표현 탐지
# Stage 4: GPT-4o mini (애매한 케이스만)      → 호출 범위 확대
# Stage 5: 앙상블 합산 + 비판 보호 로직       → 최종 판정
#
# ══════════════════════════════════════════════════════════

# ── Stage 1: 하드 규칙 사전 ─────────────────────────────
HARD_TOXIC = [
    "ㅅㅂ","씨발","개새끼","ㄱㅅㄲ","미친놈","병신","좆","보지","쉬발",
    "지랄","꺼져","닥쳐","죽어","찐따","느금","빻은","ㄴㅁ","ㅂㅅ","ㅈㄹ","ㅁㅊ",
    "개소리","쓰레기같","역겨워","토나와","구역질","뒤져","개같","존나",
    "꺼지라","닥쳐라","뒤져라","꺼져라","죽어라","하지마라",
]

HATE_TOXIC = [
    "한남","한녀","페미","꼴페미","일베","극우","빨갱이","틀딱",
    "노인네","급식충","맘충","쪽바리","짱깨","동남아","흑형",
]

# ── Stage 2: 소프트 규칙 사전 (확장) ────────────────────
SOFT_TOXIC = [
    # 조롱/비꼬기
    "고만좀","그만좀","좀그만","특이하네","참특이","어이없","황당하네",
    "가관이네","수준이","이게뭐야","저퀄","망했네","끝났네",
    "실망이다","실망했","나았는데","하락세","떨어졌",
    "웃기네","별로네","별로임","노잼","지루해","억지","어색해","거슬려",
    "일정하네","일정하다","ㅋㅋㅋ","ㅎㅎㅎ","특이하네","참특이","어이없",
    # 이탈/공격
    "탈주","하차","언팔","구독취소","신고","캡처","박제","가식","위선",
    "관종","어그로","허세","쇼하네","연기하네","위선자",
    # 부정 평가
    "퀄리티가","왜이렇게","왜이래","이상하네","왜이런",
    "그게뭐야","이게맞나","뭘하는거","의미없","쓸데없",
    # 이전 대비 비교
    "예전만못","갈수록","점점별로","요즘왜","요즘은","옛날이",
]

# ── Stage 3: 문맥 패턴 (정규식) ─────────────────────────
# 단어 단위로 못 잡는 우회 조롱 표현
CONTEXT_PATTERNS = [
    # "예전이/전에 더 나았는데" 류
    (r"(예전|전에|옛날|초기).{0,12}(낫|나았|좋았|달랐|됐었)", 22),
    # "요즘/갈수록 떨어지는 것 같아" 류
    (r"(요즘|갈수록|점점|계속).{0,10}(떨어|하락|별로|이상|달라)", 20),
    # "ㅎㅎ/ㅋㅋ + 부정" 조롱 패턴
    (r"[ㅎㅋ]{2,}.{0,8}(네요|네|죠|지|냐|나|야)", 18),
    # "이게/이거/이런 게 맞아?" 의구심
    (r"(이게|이거|이런).{0,6}(맞|나|냐|야|어|어요)", 16),
    # "왜 이렇게/저렇게" 부정
    (r"왜.{0,4}(이렇|저렇|그렇|이런|저런)", 18),
    # 존재 부정 "사라져/없어져/꺼져"
    (r"(사라|없어|꺼|나가).{0,3}(져|져라|지세요|지길)", 30),
    # 그만 + 동사
    (r"그만.{0,5}(해|하세요|하지|둬|두세요|뒀으면)", 15),
    # "보기 싫다/지겹다" 류
    (r"(보기|듣기|보는게|보는거).{0,5}(싫|지겨|귀찮)", 25),
    # "퀄리티/수준/실력이 + 부정" 문맥
    (r"(퀄리티|수준|실력|내용).{0,8}(없|낮|별로|형편|최악|엉망)", 22),
    # 비교 비하: "A보다 B가 훨씬"
    (r"(훨씬|더).{0,6}(나았|좋았|재밌었|볼만했)", 18),
    # "채널 망했다/끝났다" 류
    (r"(채널|영상|콘텐츠).{0,6}(망|끝|죽|폭망|폐지)", 25),
]

# 비판 보호: 이 패턴이 있으면 criticism 가능성 높음
CRITICISM_SAFE = [
    r"(개선|수정|보완|피드백|제안).{0,10}(해주|드리|싶어|바랍)",
    r"(아쉬|惜しい|惜).{0,10}(었|웠|점|부분)",
    r"(다음|앞으로).{0,10}(기대|응원|바랍|잘)",
    r"(솔직히|개인적으로|제 생각).{0,15}(것 같|아쉽|바랍)",
]

CLEAN_KW = [
    "감사합니다","감사해요","고마워요","최고예요","사랑해요","응원해요",
    "힘내세요","기대돼요","잘봤어요","잘봤습니다","대박이에요","위로됐어요",
    "도움됐어요","덕분에","최고다","감동이에요","재밌어요","유익해요",
    "유익했","팬이에요","파이팅","응원할게요","항상잘봐요","최고입니다",
]

REPLY_TPL = {
    "toxic":    "⚠️ 이 채널은 악플AI의 보호를 받고 있습니다. 비하나 조롱은 자동으로 감지되어 기록됩니다.",
    "warn":     "⚠️ 해당 댓글은 모니터링 중입니다. 반복 작성 시 자동 숨김 처리됩니다.",
    "clean":    "따뜻하게 봐주셔서 정말 감사해요 🙏 이런 댓글 덕분에 더 좋은 콘텐츠 만들 힘이 납니다!",
    "neutral":  "댓글 감사합니다 😊",
    "criticism":"",
}


# ── Stage 1 + 2: 규칙 기반 분석 ─────────────────────────
def stage1_hard(t: str, text: str) -> dict:
    """욕설/혐오 — 명확한 악플"""
    hard = sum(1 for k in HARD_TOXIC if k in t)
    hate = sum(1 for k in HATE_TOXIC if k in t)
    chos = len(re.findall(r"[ㄱ-ㅎ]{2,}", text))
    score = min(98, hard*42 + hate*38 + chos*14)
    return {"hard": hard, "hate": hate, "chos": chos, "score": score}


def stage2_soft(t: str) -> dict:
    """조롱/비꼬기 키워드"""
    soft  = sum(1 for k in SOFT_TOXIC if k in t)
    clean = sum(1 for k in CLEAN_KW   if k in t)
    score = min(60, soft * 20)
    return {"soft": soft, "clean": clean, "score": score}


# ── Stage 3: 문맥 패턴 분석 ─────────────────────────────
def stage3_context(text: str) -> dict:
    """정규식 기반 우회 표현 탐지"""
    t_lower = text.lower()
    pattern_score = 0
    matched = []
    for pattern, weight in CONTEXT_PATTERNS:
        if re.search(pattern, t_lower):
            pattern_score += weight
            matched.append(pattern[:20])
    # 비판 보호: 건설적 피드백 패턴이 있으면 점수 감소
    is_criticism = any(re.search(p, t_lower) for p in CRITICISM_SAFE)
    if is_criticism:
        pattern_score = max(0, pattern_score - 30)
    score = min(55, pattern_score)
    return {"score": score, "matched": len(matched), "is_criticism_hint": is_criticism}


# ── Stage 4: GPT 분석 ────────────────────────────────────
async def stage4_gpt(comment: str) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None
    prompt = f"""너는 한국어 유튜브 댓글 악성도 판단 전문가다.
아래 댓글을 분석하고 JSON만 반환해라. 설명 없음.

[판단 기준]
- toxic(70~100):    욕설, 인신공격, 비하, 혐오 표현
- sarcasm(40~69):   조롱, 비꼬기, 빈정거림, 우회 비하
- criticism(15~39): 건설적 비판, 정상적 부정 의견 ← 절대 차단 금지
- neutral(5~14):    일반 댓글
- clean(0~4):       응원, 칭찬, 긍정

[핵심 원칙]
1. "비판"과 "악플"은 반드시 구분 — criticism은 절대 숨기지 말 것
2. ㅎㅎ/ㅋㅋ 조롱 말투 + 부정 내용 = sarcasm
3. "예전이 나았다", "요즘 왜 이러지" 패턴 = sarcasm
4. 개선 요청, 솔직한 피드백 = criticism
5. 단순 의견 표현 = neutral or criticism

[출력 형식 — JSON만]
{{"score":0-100,"type":"toxic|sarcasm|criticism|neutral|clean","reason":"판단이유(한문장)","auto_reply":"자동대댓글(20자이내,criticism/neutral/clean은빈문자열)"}}

댓글: "{comment}"
"""
    try:
        async with httpx.AsyncClient(timeout=18) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 250,
                    "temperature": 0.05,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            raw = r.json()["choices"][0]["message"]["content"]
            return json.loads(re.sub(r"```json|```", "", raw).strip())
    except Exception as e:
        logger.warning(f"GPT 오류: {e}")
        return None


# ── Stage 5: 앙상블 최종 판정 ───────────────────────────
def stage5_ensemble(
    s1: dict, s2: dict, s3: dict, gpt: Optional[dict]
) -> dict:
    """
    가중 합산:
      Stage1(하드규칙) 45% — 명확한 악플은 최우선
      Stage2(소프트)   20%
      Stage3(문맥)     15%
      Stage4(GPT)      20% — 단, GPT 없으면 나머지 비율로 재분배
    """
    r_score = min(98,
        s1["score"] * 0.45 +
        s2["score"] * 0.20 +
        s3["score"] * 0.15
    )
    # 하드 규칙에서 욕설/혐오 감지 시 → 즉시 toxic
    if s1["hard"] >= 1 or s1["hate"] >= 1:
        rule_type = "toxic"
    elif s1["score"] >= 65 or (s2["soft"] >= 2) or (s3["matched"] >= 2):
        rule_type = "sarcasm"
    elif s3["is_criticism_hint"]:
        rule_type = "criticism"
    elif s2["soft"] >= 1 or s3["matched"] >= 1:
        rule_type = "sarcasm"
    elif s2.get("clean", 0) >= 1:
        rule_type = "clean"
    else:
        rule_type = "neutral"

    if gpt is None:
        # GPT 없음 → 규칙만으로 판정, 가중치 재분배
        final_score = min(98, r_score / 0.80)  # 나머지 20% 보정
        final_type  = rule_type
        reason      = "규칙 기반 (5단계)"
        auto_reply  = ""
        method      = "rule_5stage"
    else:
        g_score = gpt.get("score", r_score)
        g_type  = gpt.get("type", rule_type)
        reason  = gpt.get("reason", "")
        auto_reply = gpt.get("auto_reply", "")
        # 가중 합산 (GPT 포함)
        final_score = round(r_score * 0.80 + g_score * 0.20)
        # 타입: GPT 우선, 단 하드규칙이 toxic이면 강제
        if s1["hard"] >= 1 or s1["hate"] >= 1:
            final_type = "toxic"
        elif g_type == "criticism" and g_score < 45:
            # 비판 보호: GPT가 criticism으로 판단하면 최대 45점으로 캡
            final_score = min(final_score, 45)
            final_type  = "criticism"
        else:
            final_type = g_type
        method = "hybrid_5stage"

    # sarcasm → warn (프론트 호환)
    display_type = "warn" if final_type == "sarcasm" else final_type

    # 액션 결정
    if final_type == "criticism":
        action = "통과 (건설적 피드백 보호)"
    elif final_score >= 70:
        action = "자동 숨김"
    elif final_score >= 40:
        action = "모니터링"
    elif final_score >= 10:
        action = "통과"
    else:
        action = "감사 대댓글 발송"

    if not auto_reply:
        auto_reply = REPLY_TPL.get(display_type, "")

    return {
        "score":       round(final_score),
        "type":        display_type,
        "gpt_type":    final_type,
        "action":      action,
        "reason":      reason,
        "auto_reply":  auto_reply,
        "method":      method,
        "rule_score":  round(r_score),
        "stage_scores": {
            "s1_hard":    s1["score"],
            "s2_soft":    s2["score"],
            "s3_context": s3["score"],
            "s4_gpt":     gpt["score"] if gpt else None,
        }
    }


class CommentReq(BaseModel):
    comment: str
    use_gpt: bool = True


@app.post("/api/comment/analyze")
async def analyze_comment(req: CommentReq):
    text = req.comment.strip()
    if not text:
        raise HTTPException(400, "댓글 내용이 없습니다")
    if len(text) > 1000:
        raise HTTPException(400, "댓글이 너무 깁니다 (최대 1000자)")

    t = text.lower().replace(" ", "")

    # Stage 1~3: 규칙 기반 (항상 실행)
    s1 = stage1_hard(t, text)
    s2 = stage2_soft(t)
    s3 = stage3_context(text)

    rule_total = s1["score"] * 0.45 + s2["score"] * 0.20 + s3["score"] * 0.15

    # Stage 4: GPT 호출 여부 결정
    # 기존: score > 88 일 때만 스킵 → 개선: 명확한 케이스만 스킵
    gpt = None
    if req.use_gpt and OPENAI_API_KEY:
        # 명확한 욕설 → GPT 스킵 (비용 절약)
        skip_gpt = (s1["hard"] >= 2) or (s1["hate"] >= 1 and s1["score"] > 70)
        # 명확한 클린 → GPT 스킵
        t_orig = text.lower().replace(" ", "")
        clean_hits = sum(1 for k in CLEAN_KW if k in t_orig)
        skip_gpt = skip_gpt or (clean_hits >= 2 and rule_total < 5)

        if not skip_gpt:
            gpt = await stage4_gpt(text)

    # Stage 5: 앙상블
    result = stage5_ensemble(s1, s2, s3, gpt)
    logger.info(
        f"[COMMENT] score={result['score']} type={result['type']} "
        f"method={result['method']} "
        f"s1={s1['score']} s2={s2['score']} s3={s3['score']} "
        f"gpt={gpt['score'] if gpt else 'skip'}"
    )
    return result


# ══════════════════════════════════════════════════════════
# 증거 저장 API
# ══════════════════════════════════════════════════════════

class EvidenceReq(BaseModel):
    channel_id:     str
    comment_id:     Optional[str] = None
    comment_text:   str
    author_name:    Optional[str] = None
    comment_url:    Optional[str] = None
    analysis_type:  str
    analysis_score: int


@app.post("/api/evidence/save")
async def save_evidence(req: EvidenceReq):
    data = req.model_dump()
    data["type"] = "evidence"
    raw = f"{req.channel_id}{req.comment_id or req.comment_text[:20]}{time.time()}"
    data["evidence_id"] = "ev_" + hashlib.sha256(raw.encode()).hexdigest()[:10]
    data["created_at"]  = datetime.now(timezone.utc).isoformat()
    await _json_save("evidence", data)
    logger.info(f"[EVIDENCE] id={data['evidence_id']} score={req.analysis_score}")
    return {"success": True, "evidence_id": data["evidence_id"]}


# ══════════════════════════════════════════════════════════
# 채널 분석 API (기존 유지)
# ══════════════════════════════════════════════════════════

TOXIC_KW2 = ["ㅅㅂ","씨발","개새끼","ㄴㅁ","미친","병신","좆","쉬발","지랄",
    "개소리","꺼져","닥쳐","죽어","찐따","느금","빻은","역겨워","토나와",
    "구역질","최악이다","쓰레기","탈주","하차","퀄리티가","수준이","이게뭐",
    "저퀄","망했","끝났","실망이다","신고할게","캡처했","박제"]
CLEAN_KW2  = ["감사","고마워","최고","사랑해","응원","힘내","기대","좋아요",
    "대박","멋있","훌륭","팬이에요","구독","행복","위로","덕분에","도움","잘봤"]


def rule_score(text: str) -> dict:
    t = text.lower().replace(" ", "")
    toxic   = sum(1 for kw in TOXIC_KW2 if kw in t)
    clean   = sum(1 for kw in CLEAN_KW2 if kw in t)
    chosung = len(re.findall(r'[ㄱ-ㅎ]{2,}', text))
    ts = min(100, toxic*25 + chosung*15)
    cs = min(100, clean*20)
    return {"toxic_score":ts,"clean_score":cs,"is_toxic":ts>=60,"is_clean":cs>=70 and ts<30}


async def yt_get(url: str, params: dict) -> dict:
    params["key"] = YOUTUBE_API_KEY
    async with httpx.AsyncClient(timeout=15) as c:
        return (await c.get(url, params=params)).json()


async def search_channel(q: str) -> Optional[dict]:
    d = await yt_get("https://www.googleapis.com/youtube/v3/search",
                     {"q":q,"type":"channel","part":"snippet","maxResults":1})
    items = d.get("items", [])
    if not items: return None
    it = items[0]
    return {"channel_id":it["id"]["channelId"],"title":it["snippet"]["title"],
            "thumbnail":it["snippet"]["thumbnails"]["default"]["url"]}


async def get_video_ids(channel_id: str, n: int = 5) -> List[str]:
    d = await yt_get("https://www.googleapis.com/youtube/v3/search",
                     {"channelId":channel_id,"type":"video","part":"id","order":"date","maxResults":n})
    return [it["id"]["videoId"] for it in d.get("items",[])]


async def get_comments(video_id: str, n: int = 100) -> List[dict]:
    try:
        d = await yt_get("https://www.googleapis.com/youtube/v3/commentThreads",
                         {"videoId":video_id,"part":"snippet","maxResults":n,
                          "order":"relevance","textFormat":"plainText"})
        return [{"text":it["snippet"]["topLevelComment"]["snippet"]["textDisplay"],
                 "likes":it["snippet"]["topLevelComment"]["snippet"]["likeCount"]}
                for it in d.get("items",[])]
    except: return []


class AnalyzeReq(BaseModel):
    channel_name: str
    use_gpt:      bool = True


@app.post("/api/analyze")
async def analyze_channel(req: AnalyzeReq):
    if not YOUTUBE_API_KEY:
        raise HTTPException(400, "YOUTUBE_API_KEY 환경변수를 설정하세요")
    ch = await search_channel(req.channel_name)
    if not ch:
        raise HTTPException(404, f"'{req.channel_name}' 채널을 찾을 수 없습니다")
    vids = await get_video_ids(ch["channel_id"])
    if not vids:
        raise HTTPException(404, "분석할 영상이 없습니다")
    all_comments = []
    for vid in vids:
        all_comments.extend(await get_comments(vid))
    if not all_comments:
        raise HTTPException(404, "수집된 댓글이 없습니다")
    texts  = [c["text"] for c in all_comments]
    scores = [rule_score(t) for t in texts]
    toxic_cnt = sum(1 for s in scores if s["is_toxic"])
    clean_cnt = sum(1 for s in scores if s["is_clean"])
    avg_toxic = sum(s["toxic_score"] for s in scores) / len(scores)
    avg_clean = sum(s["clean_score"] for s in scores) / len(scores)
    ft = round(min(100, avg_toxic))
    fc = round(min(100, avg_clean))
    tr = round(toxic_cnt / len(all_comments) * 100)
    verdict = "위험" if ft>=70 else "주의" if ft>=40 else "안전"
    return {
        "channel": ch,
        "analysis": {
            "total_comments": len(all_comments),
            "toxic_score": ft, "clean_score": fc, "toxic_ratio": tr,
            "toxic_count": toxic_cnt, "clean_count": clean_cnt,
            "verdict": verdict,
            "verdict_emoji": "🔴" if ft>=70 else "🟡" if ft>=40 else "🟢",
            "categories": {
                "hate_speech":    min(100,int(avg_toxic*0.9)),
                "subtle_mockery": min(100,int(avg_toxic*0.7)),
                "group_attack":   min(100,int(avg_toxic*0.5)),
                "personal_info":  min(100,int(avg_toxic*0.3)),
            },
            "gpt_used": False,
        },
        "samples": {
            "toxic": [c["text"] for c in all_comments if rule_score(c["text"])["is_toxic"]][:3],
            "clean": [c["text"] for c in all_comments if rule_score(c["text"])["is_clean"]][:3],
        },
    }


# ══════════════════════════════════════════════════════════
# 관리자 API
# ══════════════════════════════════════════════════════════

def verify_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN:
        raise HTTPException(401, "관리자 인증 실패")
    if not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(401, "관리자 인증 실패")


@app.get("/api/admin/leads", dependencies=[Depends(verify_admin)])
async def admin_leads(lead_type: str = "free_trial", limit: int = 100):
    leads = await load_records(lead_type, limit)
    return {"type": lead_type, "count": len(leads), "leads": leads}


@app.get("/api/admin/stats", dependencies=[Depends(verify_admin)])
async def admin_stats():
    all_leads    = await load_records(limit=9999)
    free_trial   = [l for l in all_leads if l.get("type") == "free_trial"]
    beta_apply   = [l for l in all_leads if l.get("type") == "beta_apply"]
    return {
        "total":      len(all_leads),
        "free_trial": len(free_trial),
        "beta_apply": len(beta_apply),
        "storage":    "supabase" if USE_SUPABASE else "json",
    }


# ══════════════════════════════════════════════════════════
# 헬스체크
# ══════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"service": "악플AI API", "version": "3.0.0", "status": "ok", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {
        "youtube_api": "ok" if YOUTUBE_API_KEY else "MISSING",
        "openai_api":  "ok" if OPENAI_API_KEY  else "MISSING",
        "admin_token": "ok" if ADMIN_TOKEN      else "MISSING ⚠️",
        "storage":     "supabase" if USE_SUPABASE else "json (임시)",
    }
