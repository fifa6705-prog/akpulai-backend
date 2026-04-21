"""
악플AI (AkpulAI) — 통합 백엔드 v5.0 (5분류 체계) (5단계 하이브리드)
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
    # 욕설 기본형 (정규화 후 매칭됨)
    "씨발","개새끼","미친","미친놈","병신","좆","좆같","보지","지랄",
    "꺼져","닥쳐","뒤져","존나","개같","느금마","빻은","찐따",
    "쓰레기같","역겨워","토나와","구역질",
    # 파생형 (정규화로도 못 잡는 것)
    "개년","개놈","개년아","썅","썅년","썅놈","후장","보지년",
    "보지같","보지년","음란","섹스","성교","자지","보지",
    # 죽음/폭력 위협
    "죽어라","꺼져라","닥쳐라","뒤져라","뒤지라","꺼지라","하지마라",
    "죽여버","때려버","패버","신고해버",
    # 초성 (정규화 후 안 바뀌는 것들)
    "ㅅㅂ","ㅆㅂ","ㅂㅅ","ㅁㅊ","ㅈㄹ","ㄱㅅㄲ","ㄴㅁ","ㅈㄴ",
]

HATE_TOXIC = [
    "한남","한녀","페미","꼴페미","일베","극우","빨갱이","틀딱",
    "노인네","급식충","맘충","쪽바리","짱깨","동남아","흑형",
]

# ── Stage 2: 소프트 규칙 사전 (5분류 체계) ──────────────
# mockery / sarcasm / passive-aggressive 핵심 표현
SOFT_TOXIC = [
    # 반어형 칭찬 (sarcasm)
    "일정하네","일정하다","역시나","역시네","참특이","특이하시네",
    "대단하시네","대단하다","참대단","오히려좋아","나름나름",
    # 하락/쇠퇴 암시 (decline_insinuation)
    "나았는데","좋았는데","달랐는데","하락세","떨어졌",
    "예전만못","갈수록","점점별로","요즘왜","옛날이","초창기가",
    # passive-aggressive 부정 평가
    "고만좀","그만좀","좀그만","어이없","황당하네","가관이네",
    "실망이다","실망했","노잼","지루해","억지","어색해","거슬려",
    "웃기네","별로네","별로임","이상하네","의미없","쓸데없",
    "저퀄","망했네","끝났네","수준이","이게뭐야","그게뭐야",
    # 집단 선동 (group_instigation)
    "다들그렇","다들아닌가","저만그런가","그렇죠","다같이","동의하시죠",
    # 이탈/공격 의도
    "탈주","하차","언팔","구독취소","캡처","박제",
    # 비꼬기 웃음표현 단독 (문맥 결합)
    "ㅎㅎㅎ","ㅋㅋㅋ",
    # veiled insult
    "가식","위선","관종","어그로","허세","쇼하네","연기하네","위선자",
]

# ── mockery 전용 신호 패턴 (Lightweight Classifier) ──────
MOCKERY_PATTERNS = [
    # 반어형 칭찬 + 웃음
    (r"역시.{0,6}(ㅎ+|ㅋ+|\^\^|네요|이네|하네)", 28),
    (r"일정한.{0,8}(퀄|실력|수준|퀄리티)", 26),
    (r"참.{0,4}(특이|독특|대단|신기).{0,4}(네|네요|이네|시네)", 24),
    (r"(오히려|나름|그나마).{0,8}(좋|낫|괜찮)", 20),
    # 하락/쇠퇴 암시
    (r"(예전|전에|옛날|초기|초창기).{0,12}(낫|나았|좋았|달랐|됐었)", 26),
    (r"(요즘|갈수록|점점|계속|날이갈수록).{0,10}(떨어|하락|별로|이상|달라|변해)", 24),
    (r"(하락세|쇠퇴|전성기|전만못)", 22),
    # 집단 선동
    (r"(구독자|팬|보는\s*사람|시청자).{0,15}(이상|아닌가|어떻게|동의)", 26),
    (r"(저만|다들|여러분|우리끼리).{0,10}(그렇|아닌가|동의|생각)", 22),
    # passive-aggressive 웃음 + 부정
    (r".{4,}(ㅎ+|ㅋ+)\s*$", 18),                     # 문장 끝 웃음
    (r"(ㅎ+|ㅋ+).{0,3}(별로|이상|그렇|아닌)", 20),
    # 외모/말투/일상 비하 완곡형 (appearance/tone/lifestyle mocking)
    (r"(얼굴|목소리|말투|표정|외모|스타일).{0,10}(특이|독특|그렇|왜|좀)", 24),
    (r"(사는\s*방식|라이프|일상|생활).{0,10}(특이|독특|이상|그런|저런)", 20),
    # 비꼬기 의문형
    (r"(본인은|본인이).{0,8}(모르|알까|알겠|알지)", 22),
    (r"(이런\s*게|이게|이거).{0,6}(맞나|맞아|맞죠|맞는건가)", 18),
    # 반복 빈정거림
    (r"(또|또다시|이번에도|역시나).{0,10}(이러|저러|그러|별로|실망)", 22),
]

# ── criticism 보호 패턴 ───────────────────────────────────
CRITICISM_PROTECT = [
    r"(개선|수정|보완|피드백|제안|건의).{0,12}(해주|드리|싶어|바랍|하면)",
    r"아쉬.{0,10}(었|웠|점|부분|네요|워|운)",
    r"(다음|앞으로|이번엔|이다음엔).{0,10}(기대|응원|바랍|잘|더)",
    r"(솔직히|개인적으로|제 생각|사실).{0,15}(것 같|아쉽|바랍|느낌)",
    r"(편집|음질|조명|구성|내용|자막|길이|템포|설명|썸네일).{0,10}(아쉽|별로|부족|짧|길|불편|좋아)",
    r"(퀄리티|퀄).{0,8}(아쉽|별로|부족|낮|좋|괜찮)",
    r"(좀|조금).{0,6}(아쉽|부족|길|짧|불편|어색)",
    r"것 같.{0,4}$",
    r"것 같아",
    r"것 같네",
    r"것 같은데",
]

# ── Stage 3: 문맥 패턴 (정규식) ─────────────────────────
# MOCKERY_PATTERNS와 중복 피하고 보완적 패턴만
CONTEXT_PATTERNS = [
    # 존재 부정 / 퇴장 요구
    (r"(사라|없어|꺼|나가).{0,3}(져|져라|지세요|지길|지길바)", 35),
    # 보기/듣기 싫다
    (r"(보기|듣기|보는게|보는거).{0,5}(싫|지겨|귀찮)", 30),
    # 채널 종말 암시
    (r"(채널|영상|콘텐츠|유튜브).{0,6}(망|끝|죽|폭망|폐지|접어)", 30),
    # 퀄리티 비하
    (r"(퀄리티|수준|실력|내용|편집).{0,8}(없|낮|별로|형편|최악|엉망|바닥)", 25),
    # 왜 이렇게 + 부정
    (r"왜.{0,4}(이렇|저렇|그렇|이런|저런).{0,6}(됐|변|해|됩)", 22),
    # 그만 + 동사
    (r"그만.{0,5}(해|하세요|하지|둬|두세요|뒀으면|할래)", 20),
    # 비교 비하
    (r"(훨씬|더).{0,6}(나았|좋았|재밌었|볼만했|낫던)", 22),
    # 이게 맞나 의구심
    (r"(이게|이거|이런\s*게).{0,6}(맞나|맞아|맞는|맞죠)", 18),
    # 구독 취소 선언
    (r"(구독|팔로우).{0,6}(취소|끊|해지|안해|말아야)", 25),
]

# 비판 보호: 이 패턴 있으면 criticism 가능성 높음
CRITICISM_SAFE = [
    r"(개선|수정|보완|피드백|제안|건의).{0,12}(해주|드리|싶어|바랍|하면)",
    r"아쉬.{0,10}(었|웠|점|부분|네요)",
    r"(다음|앞으로|이번엔).{0,10}(기대|응원|바랍|잘|더)",
    r"(솔직히|개인적으로|제\s*생각|사실).{0,15}(것\s*같|아쉽|바랍|느낌)",
    r"(편집|음질|조명|구성|내용|자막|길이|템포|설명).{0,10}(아쉽|별로|부족|짧|길|불편)",
]

CLEAN_KW = [
    "감사합니다","감사해요","감사드려요","고마워요","최고예요","사랑해요","응원해요",
    "힘내세요","기대돼요","잘봤어요","잘봤습니다","대박이에요","위로됐어요",
    "도움됐어요","덕분에","최고다","감동이에요","재밌어요","유익해요",
    "유익했","팬이에요","파이팅","화이팅","응원할게요","항상잘봐요","최고입니다",
    "응원합니다","항상응원","좋아요","좋은영상","좋은콘텐츠","행복해요",
    "힐링됐","위로가","오늘도","잘보고","잘보았","잘시청","잘봅니다",
]

REPLY_TPL = {
    "toxic":    "",  # 경고 대댓글 없음 — 조용히 숨김 처리
    "warn":     "",   # 경고 대댓글 없음 — 모니터링만,
    "clean":    "따뜻하게 봐주셔서 정말 감사해요 🙏 이런 댓글 덕분에 더 좋은 콘텐츠 만들 힘이 납니다!",
    "neutral":  "댓글 감사합니다 😊",
    "criticism":"",
}


# ── Stage 1 + 2: 규칙 기반 분석 ─────────────────────────

# ══════════════════════════════════════════════════════════
# 텍스트 정규화 (우회 표현 → 표준형 변환)
# Stage1 사전 검사 전 반드시 적용
# ══════════════════════════════════════════════════════════
def normalize_text(text: str) -> str:
    """우회 표현을 표준형으로 변환 — 사전 탐지율 극대화"""
    t = text.lower()

    # 1. 초성 → 표준형
    cho_map = {
        "ㅆㅂ": "씨발", "ㅅㅂ": "씨발",
        "ㅂㅅ": "병신", "ㅁㅊ": "미친",
        "ㅈㄹ": "지랄", "ㄱㅅㄲ": "개새끼",
        "ㄴㅁ": "느금마", "ㅈㄴ": "존나",
        "ㅈ같": "좆같", "ㄷㅊ": "닥쳐",
        "ㄲㅈ": "꺼져", "ㄷㅈ": "뒤져",
    }
    for k, v in cho_map.items():
        t = t.replace(k, v)

    # 2. 모음/자음 변형 우회 → 표준형
    bypass_map = {
        # 씨발 계열
        "씨빨": "씨발", "씨팔": "씨발", "씨펄": "씨발",
        "쉬발": "씨발", "쉬빨": "씨발", "쉬팔": "씨발",
        "시발": "씨발", "시빨": "씨발", "시팔": "씨발",
        "씨8": "씨발", "ㅅ1발": "씨발",
        # 뒤져 계열
        "뒈져": "뒤져", "뒈지": "뒤져", "뒤져라": "뒤져라",
        "디져": "뒤져", "디지": "뒤져", "디졌": "뒤져",
        "뒤저": "뒤져", "뒤졌": "뒤져",
        # 좆 계열
        "줏도": "좆도", "줏같": "좆같", "줬도": "좆도",
        # 병신 계열
        "뵹신": "병신", "병씬": "병신", "벙신": "병신",
        "뵝신": "병신", "뵹씬": "병신",
        # 미친 계열
        "미칀": "미친", "미첬": "미친", "미칩": "미친",
        "미칠": "미친", "미친ㄴ": "미친",
        # 개새끼 계열
        "개쌔끼": "개새끼", "개쌔": "개새끼",
        "개색": "개새끼", "개색히": "개새끼",
        # 존나 계열
        "졲나": "존나", "존ㄴ": "존나",
        # 지랄 계열
        "지ㄹ": "지랄", "지럴": "지랄",
        # 닥쳐 계열
        "닥쳐": "닥쳐", "닥처": "닥쳐",
        # 꺼져 계열
        "꺼저": "꺼져", "꺼지라": "꺼져라",
    }
    for k, v in bypass_map.items():
        t = t.replace(k, v)

    # 3. 특수문자로 글자 가림 (씨*발, ㅂ*ㅅ 등)
    t = re.sub(r"씨[\*\.\-\_\!\s]?발", "씨발", t)
    t = re.sub(r"병[\*\.\-\_\!\s]?신", "병신", t)
    t = re.sub(r"개[\*\.\-\_\!\s]?새[\*\.\-\_\!\s]?끼", "개새끼", t)
    t = re.sub(r"미[\*\.\-\_\!\s]?친", "미친", t)
    t = re.sub(r"좆[\*\.\-\_\!\s]?같", "좆같", t)

    # 4. 자음/모음 늘림 제거 (씨이이발 → 씨발, ㅋㅋㅋㅋ → ㅋㅋ)
    t = re.sub(r"씨+발", "씨발", t)
    t = re.sub(r"뒤+져", "뒤져", t)
    t = re.sub(r"미+친", "미친", t)
    t = re.sub(r"(.){3,}", r"", t)  # 4회 이상 반복 → 2회로

    return t

def stage1_hard(t: str, text: str) -> dict:
    """욕설/혐오 — 명확한 악플"""
    hard = sum(1 for k in HARD_TOXIC if k in t)
    hate = sum(1 for k in HATE_TOXIC if k in t)
    chos = len(re.findall(r"[ㄱ-ㅎ]{2,}", text))
    score = min(98, hard*42 + hate*38 + chos*14)
    return {"hard": hard, "hate": hate, "chos": chos, "score": score}


def stage2_soft(t: str) -> dict:
    """소프트 규칙 + mockery 패턴 — 5분류 체계"""
    soft  = sum(1 for k in SOFT_TOXIC if k in t)
    clean = sum(1 for k in CLEAN_KW   if k in t)

    # mockery 패턴 매칭 (원본 텍스트 기준)
    mockery_score = 0
    mockery_tags = []
    for pattern, weight in MOCKERY_PATTERNS:
        if re.search(pattern, t):
            mockery_score += weight
            # 태그 추출
            if "역시" in pattern or "일정" in pattern: mockery_tags.append("sarcasm")
            elif "예전" in pattern or "하락" in pattern: mockery_tags.append("decline_insinuation")
            elif "구독자" in pattern or "다들" in pattern: mockery_tags.append("group_instigation")
            elif "얼굴" in pattern or "말투" in pattern: mockery_tags.append("appearance_mocking")
            elif "본인" in pattern: mockery_tags.append("veiled_insult")
            elif "또" in pattern or "이번에도" in pattern: mockery_tags.append("repetitive_needling")
            else: mockery_tags.append("passive_aggressive")

    # criticism 보호 패턴 체크
    is_criticism_hint = any(re.search(p, t) for p in CRITICISM_PROTECT)

    # 최종 소프트 점수: 키워드 + mockery 패턴
    score = min(70, soft * 15 + min(mockery_score, 50))
    return {
        "soft": soft,
        "clean": clean,
        "score": score,
        "mockery_score": mockery_score,
        "mockery_tags": list(set(mockery_tags)),
        "is_criticism_hint": is_criticism_hint,
    }


# ── Stage 3: 문맥 패턴 분석 ─────────────────────────────
def stage3_context(text: str) -> dict:
    """정규식 기반 우회 표현 탐지 + criticism 보호"""
    t_lower = text.lower()
    t_nospace = t_lower.replace(" ", "")

    pattern_score = 0
    matched = []
    for pattern, weight in CONTEXT_PATTERNS:
        if re.search(pattern, t_lower):
            pattern_score += weight
            matched.append(pattern[:20])

    # criticism 보호: CRITICISM_SAFE 패턴 있으면 점수 대폭 감소
    is_criticism = any(re.search(p, t_lower) for p in CRITICISM_SAFE)

    # criticism 강화 판단: 인신공격 없고 구체적 피드백
    no_personal = not re.search(
        r"(본인|쟤|이\s*사람|저\s*사람|당신).{0,5}(이상|문제|왜|그래|못)", t_lower
    )
    has_specific = bool(re.search(
        r"(편집|음질|조명|구성|내용|자막|길이|템포|설명|썸네일)", t_lower
    ))
    no_sarcasm_laugh = not re.search(r".{3,}(ㅎ+|ㅋ+)\s*$", t_lower)

    is_strong_criticism = is_criticism and no_personal and no_sarcasm_laugh

    if is_strong_criticism:
        pattern_score = max(0, pattern_score - 40)  # 강한 보호
    elif is_criticism:
        pattern_score = max(0, pattern_score - 20)  # 일반 보호

    score = min(55, pattern_score)
    return {
        "score": score,
        "matched": len(matched),
        "is_criticism_hint": is_criticism,
        "is_strong_criticism": is_strong_criticism,
    }


# ── Stage 4: GPT 분석 ────────────────────────────────────
async def stage4_gpt(comment: str) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None
    prompt = f"""너는 한국어 유튜브 댓글 분류 전문가다.
악플AI의 핵심 가치는 "정상 댓글은 보호, 악의적 공격만 걸러냄"이다.
아래 댓글을 5분류하고 JSON만 반환해라. 설명 없음.

[5분류 기준]
- hard_toxic(75~100): 욕설, 혐오, 노골적 인신공격, 성적 비하, 폭력 위협
- mockery(45~74):     비꼬기, 조롱, 우회 비하, 빈정거림, 반어법, 집단 선동
                      반드시 포함: "역시 ㅎ", "일정한 퀄리티", "예전이 나았는데",
                      "요즘 왜 이렇게", "본인은 모르실 듯", 끝에 ㅋㅋ/ㅎㅎ+부정
- criticism(15~44):   건설적 비판, 개선 요구, 구체적 불만 ← 절대 차단 금지
                      인신공격 없고, 구체적 대상(편집/내용/구성 등) 있음
- neutral(5~14):      정보성, 일반 반응, 감정 강도 낮음
- clean(0~4):         응원, 감사, 팬 반응, 긍정

[핵심 원칙 — 반드시 지킬 것]
1. criticism은 어떤 상황에도 숨김 금지 — 팬과의 소통 핵심
2. mockery는 단어가 아니라 문맥, 어투, 반복성으로 판단
3. ㅎㅎ/ㅋㅋ + 부정 내용 조합 → mockery
4. 문장 끝 ㅎ/ㅋ 하나라도 + 부정 내용 → mockery 가능성 높음
5. "예전이 나았다", "갈수록 별로", "역시나" → mockery
6. 구독 취소 선언, 집단 선동 → mockery
7. 외모/말투/일상 완곡 비하 → mockery
8. 개선 요청 + 구체적 내용 + 인신공격 없음 → criticism 보호

[출력 형식 — JSON만]
{{"score":0-100,"type":"hard_toxic|mockery|criticism|neutral|clean","confidence":0.0-1.0,"tags":[],"reason":"한문장","auto_reply":"감사대댓글(clean만,나머지빈문자열)"}}

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


# ── Stage 5: 앙상블 최종 판정 (5분류 체계) ─────────────
def stage5_ensemble(
    s1: dict, s2: dict, s3: dict, gpt: Optional[dict]
) -> dict:
    """
    5분류: hard_toxic / mockery / criticism / neutral / clean
    가중 합산:
      Stage1(하드규칙)  45% — 욕설/혐오 최우선
      Stage2(소프트+mockery) 25% — mockery 탐지 강화
      Stage3(문맥패턴)  15%
      Stage4(GPT)       15% — 판사 역할 (조건부)
    """
    # ── 규칙 기반 점수 합산 ──────────────────────────────
    r_score = min(98,
        s1["score"]             * 0.45 +
        s2["score"]             * 0.25 +
        s3["score"]             * 0.15
    )

    # ── 욕설 감지 시 최소 점수 보장 (GPT 희석 방지) ──────
    if s1["hard"] >= 1:
        r_score = max(r_score, 78)  # 욕설 → 최소 78점
    if s1["hate"] >= 1:
        r_score = max(r_score, 72)  # 혐오 → 최소 72점

    # ── 규칙 기반 타입 결정 ──────────────────────────────
    if s1["hard"] >= 1 or s1["hate"] >= 1:
        rule_type = "hard_toxic"
    elif (
        s3.get("is_strong_criticism") or
        (s3["is_criticism_hint"] and s2["mockery_score"] < 20) or
        (s2.get("is_criticism_hint") and s2["mockery_score"] < 15)  # CRITICISM_PROTECT 매칭
    ):
        rule_type = "criticism"
    elif (s2["soft"] >= 2 or s2["mockery_score"] >= 30 or s3["matched"] >= 2):
        rule_type = "mockery"
    elif s2["soft"] >= 1 or s2["mockery_score"] >= 15 or s3["matched"] >= 1:
        rule_type = "mockery"
    elif s2.get("clean", 0) >= 1:
        rule_type = "clean"
    else:
        rule_type = "neutral"

    # ── GPT 결과 통합 ────────────────────────────────────
    if gpt is None:
        final_score = min(98, r_score * 1.1)  # GPT 없을 때 소폭 보정
        final_type  = rule_type
        reason      = "규칙 기반 (5단계)"
        auto_reply  = ""
        gpt_confidence = 0.0
        tags        = s2.get("mockery_tags", [])
        method      = "rule_5stage"
    else:
        g_score      = gpt.get("score", r_score)
        g_type       = gpt.get("type", rule_type)
        reason       = gpt.get("reason", "")
        auto_reply   = gpt.get("auto_reply", "")
        gpt_confidence = float(gpt.get("confidence", 0.7))
        tags         = gpt.get("tags", []) or s2.get("mockery_tags", [])

        # GPT hard_toxic → sarcasm/toxic 호환 처리
        if g_type in ["sarcasm", "warn"]:
            g_type = "mockery"
        if g_type == "toxic":
            g_type = "hard_toxic"

        # 가중 합산
        final_score = round(r_score * 0.85 + g_score * 0.15)

        # 타입 최종 결정
        if s1["hard"] >= 1 or s1["hate"] >= 1:
            final_type = "hard_toxic"           # 욕설은 규칙 절대 우선
        elif rule_type == "hard_toxic":
            final_type = "hard_toxic"
        elif g_type == "criticism" and gpt_confidence >= 0.7:
            # GPT가 criticism 확신 → 점수 캡 + 보호
            final_score = min(final_score, 42)
            final_type  = "criticism"
        elif rule_type == "criticism" and g_type in ["neutral", "clean", "criticism"]:
            final_score = min(final_score, 42)
            final_type  = "criticism"
        elif g_type == "mockery" and gpt_confidence >= 0.65:
            final_type = "mockery"
            final_score = max(final_score, 48)  # mockery 최소 48점
        elif rule_type == "mockery":
            final_type = "mockery"
        else:
            final_type = g_type
        method = "hybrid_5stage"

    # ── 프론트 호환 타입 변환 ────────────────────────────
    # 기존 프론트가 toxic/warn/clean/neutral/criticism 사용
    type_map = {
        "hard_toxic": "toxic",
        "mockery":    "warn",
        "criticism":  "criticism",
        "neutral":    "neutral",
        "clean":      "clean",
    }
    display_type = type_map.get(final_type, final_type)

    # ── action_score 계산 (toxicity_score와 분리) ─────────
    # 반복성/패턴 강도 반영 가능 구조
    action_score = final_score
    if final_type == "mockery" and s2["mockery_score"] >= 40:
        action_score = min(98, action_score + 8)  # 강한 mockery → action 가중

    # ── 액션 결정 ─────────────────────────────────────────
    if final_type == "criticism":
        action = "통과 (건설적 피드백 보호)"
    elif action_score >= 72:
        action = "자동 숨김"
    elif action_score >= 45:
        action = "모니터링"
    elif action_score >= 10:
        action = "통과"
    else:
        action = "감사 대댓글 발송"

    if not auto_reply:
        auto_reply = REPLY_TPL.get(display_type, "")

    return {
        "score":        round(final_score),
        "type":         display_type,
        "internal_type": final_type,
        "action":       action,
        "action_score": round(action_score),
        "confidence":   gpt_confidence,
        "tags":         tags,
        "reason":       reason,
        "auto_reply":   auto_reply,
        "method":       method,
        "rule_score":   round(r_score),
        "stage_scores": {
            "s1_hard":    s1["score"],
            "s2_soft":    s2["score"],
            "s2_mockery": s2.get("mockery_score", 0),
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

    # 정규화: 우회 표현 → 표준형 변환 (Stage1 전 필수)
    norm_text = normalize_text(text)
    t = norm_text.lower().replace(" ", "")
    t_orig = text.lower().replace(" ", "")  # 원본도 유지

    # Stage 1~3: 규칙 기반 (항상 실행)
    s1 = stage1_hard(t, norm_text)
    s2 = stage2_soft(text.lower())
    s3 = stage3_context(text)

    rule_total = s1["score"] * 0.45 + s2["score"] * 0.20 + s3["score"] * 0.15

    # ── Stage 4: GPT 라우팅 (비용 최적화) ──────────────────
    # 원칙: 욕설 명확 → 스킵 / mockery·애매 → 반드시 호출
    gpt = None
    if req.use_gpt and OPENAI_API_KEY:
        # 1. 명확한 욕설 → GPT 스킵 (규칙으로 충분)
        skip_gpt = (s1["hard"] >= 1)

        # 2. 명확한 클린 → GPT 스킵
        clean_hits = sum(1 for k in CLEAN_KW if k in t)
        if clean_hits >= 2 and rule_total < 8 and s2["mockery_score"] < 15:
            skip_gpt = True

        # 3. mockery 신호 있으면 반드시 GPT 호출 (서비스 핵심 가치)
        if s2["mockery_score"] >= 15 and s1["hard"] == 0:
            skip_gpt = False

        # 4. soft 키워드 있고 욕설 없으면 GPT 호출
        if s2["soft"] >= 1 and s1["hard"] == 0:
            skip_gpt = False

        # 5. criticism 힌트 있으면 GPT로 정확히 분류
        if s3["is_criticism_hint"] and not skip_gpt:
            skip_gpt = False

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

@app.get("/api/evidence/list")
async def list_evidence(channel_id: str):
    """채널별 증거 목록 조회"""
    if not channel_id:
        raise HTTPException(400, "channel_id가 필요합니다")
    try:
        if USE_SUPABASE and _supabase:
            data = _supabase.table("evidence")                .select("*")                .eq("channel_id", channel_id)                .order("created_at", desc=True)                .limit(100)                .execute()
            evidences = data.data or []
        else:
            fp = DATA_DIR / "evidence.json"
            if fp.exists():
                all_ev = json.loads(fp.read_text(encoding="utf-8"))
                evidences = [e for e in all_ev if e.get("channel_id") == channel_id]
                evidences.sort(key=lambda x: x.get("created_at",""), reverse=True)
            else:
                evidences = []
        logger.info(f"[EVIDENCE_LIST] channel={channel_id} count={len(evidences)}")
        return {"channel_id": channel_id, "count": len(evidences), "evidences": evidences}
    except Exception as e:
        logger.error(f"[EVIDENCE_LIST] 오류: {e}")
        raise HTTPException(500, "증거 목록 조회 실패")




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
# Google OAuth 인증 API
# ══════════════════════════════════════════════════════════
#
# 흐름:
#   프론트 → Google OAuth → /auth/callback?code=xxx
#   → POST /api/auth/google/callback { code, redirect_uri }
#   → 백엔드: code → access_token + refresh_token 교환
#   → channel 정보 조회 → 응답
#
# Railway 환경변수 추가 필요:
#   GOOGLE_CLIENT_ID     = 693160558771-...apps.googleusercontent.com
#   GOOGLE_CLIENT_SECRET = (Google Cloud Console → OAuth 클라이언트 → 보안 비밀)
# ══════════════════════════════════════════════════════════

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNEL_URL  = "https://www.googleapis.com/youtube/v3/channels"


class GoogleCallbackReq(BaseModel):
    code:         str
    redirect_uri: str


@app.post("/api/auth/google/callback")
async def google_callback(req: GoogleCallbackReq):
    """
    Google OAuth code → access_token 교환 후 채널 정보 반환
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 환경변수를 설정하세요")

    # ── 1. code → token 교환 ──────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            token_resp = await c.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code":          req.code,
                    "client_id":     GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri":  req.redirect_uri,
                    "grant_type":    "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as e:
        logger.error(f"[OAUTH] token 교환 실패: {e}")
        raise HTTPException(502, "Google 인증 서버 연결 실패")

    token_data = token_resp.json()
    if "error" in token_data:
        logger.error(f"[OAUTH] token 오류: {token_data}")
        raise HTTPException(400, f"인증 오류: {token_data.get('error_description', token_data['error'])}")

    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")

    if not access_token:
        raise HTTPException(400, "access_token을 받지 못했습니다")

    # ── 2. YouTube 채널 정보 조회 ─────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            ch_resp = await c.get(
                YOUTUBE_CHANNEL_URL,
                params={
                    "part": "snippet,statistics",
                    "mine": "true",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except Exception as e:
        logger.error(f"[OAUTH] 채널 조회 실패: {e}")
        raise HTTPException(502, "YouTube 채널 정보 조회 실패")

    ch_data = ch_resp.json()
    items   = ch_data.get("items", [])

    if not items:
        raise HTTPException(404, "연결된 YouTube 채널을 찾을 수 없습니다. YouTube 채널을 먼저 개설해주세요.")

    channel        = items[0]
    channel_id     = channel["id"]
    snippet        = channel.get("snippet", {})
    statistics     = channel.get("statistics", {})
    channel_title  = snippet.get("title", "")
    thumbnail      = snippet.get("thumbnails", {}).get("default", {}).get("url", "")
    subscriber_cnt = statistics.get("subscriberCount", "0")

    logger.info(f"[OAUTH] 채널 연동 완료: {channel_title} ({channel_id})")

    # ── 3. 채널 정보 Supabase 저장 (선택) ────────────────
    # TODO: 추후 users 테이블 생성 후 저장
    # await save_channel_info(channel_id, access_token, refresh_token)

    return {
        "success":          True,
        "channel_id":       channel_id,
        "channel_title":    channel_title,
        "channel_thumbnail":thumbnail,
        "subscriber_count": subscriber_cnt,
        "access_token":     access_token,
        # refresh_token은 보안상 프론트에 직접 내려주지 않음
        # 실제 서비스에서는 서버 세션 또는 암호화된 쿠키로 관리
        "message":          f"'{channel_title}' 채널이 연동되었습니다 🎉",
    }


@app.get("/api/auth/google/revoke")
async def google_revoke(access_token: str):
    """
    Google OAuth 토큰 취소 (연동 해제)
    """
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": access_token},
            )
    except Exception as e:
        logger.warning(f"[OAUTH] 토큰 취소 실패: {e}")

    logger.info("[OAUTH] 채널 연동 해제 완료")
    return {"success": True, "message": "채널 연동이 해제되었습니다."}


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
    return {"service": "악플AI API", "version": "4.3.0", "status": "ok", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {
        "youtube_api": "ok" if YOUTUBE_API_KEY else "MISSING",
        "openai_api":  "ok" if OPENAI_API_KEY  else "MISSING",
        "admin_token": "ok" if ADMIN_TOKEN      else "MISSING ⚠️",
        "storage":     "supabase" if USE_SUPABASE else "json (임시)",
    }
