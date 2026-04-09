"""
악플AI — 댓글 분석 API
POST /api/comment/analyze  → 시뮬레이터 / 분석기 공용
"""

import os, re, json, logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/comment", tags=["comment"])

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# ── 키워드 사전 ──────────────────────────────────────────

HARD_TOXIC = [
    "ㅅㅂ","씨발","개새끼","ㄱㅅㄲ","미친놈","병신","좆","보지","쉬발",
    "지랄","꺼져","닥쳐","죽어","찐따","느금","빻은","ㄴㅁ","ㅂㅅ","ㅈㄹ",
    "ㅁㅊ","개소리","쓰레기같","역겨워","토나와","구역질","뒤져","개같","존나",
]
SOFT_TOXIC = [
    "고만좀","그만좀","퀄리티가","수준이","이게뭐야","망했네","끝났네",
    "실망이다","실망했","나았는데","하락세","떨어졌","왜이렇게","왜이래",
    "웃기네","별로네","별로임","노잼","지루해","억지","어색해","거슬려",
    "탈주","하차","언팔","구독취소","신고","캡처","박제","가식","위선",
    "일정하네","일정하다","ㅋㅋㅋ","ㅎㅎㅎ","특이하네","참특이","어이없",
]
HATE_TOXIC = [
    "한남","한녀","페미","꼴페미","일베","극우","빨갱이","틀딱",
    "노인네","급식충","맘충","쪽바리","짱깨","동남아",
]
CLEAN_KW = [
    "감사합니다","감사해요","고마워요","최고예요","사랑해요","응원해요",
    "힘내세요","기대돼요","잘봤어요","잘봤습니다","대박이에요","위로됐어요",
    "도움됐어요","덕분에","최고다","감동이에요","재밌어요","유익해요",
    "유익했","팬이에요","파이팅",
]

# 자동 대댓글 템플릿
REPLY_TEMPLATES = {
    "toxic":   "⚠️ 이 채널은 악플AI의 보호를 받고 있습니다. 비하나 조롱은 자동으로 감지되어 기록됩니다.",
    "warn":    "⚠️ 해당 댓글은 모니터링 중입니다. 반복 작성 시 자동 숨김 처리됩니다.",
    "clean":   "따뜻하게 봐주셔서 정말 감사해요 🙏 이런 댓글 덕분에 더 좋은 콘텐츠 만들 힘이 납니다!",
    "neutral": "댓글 감사합니다 😊",
    "criticism": "",   # 건설적 피드백 — 대댓글 없음 (크리에이터가 직접 대응)
}


# ══════════════════════════════════════════════════════════
# 규칙 기반 분석
# ══════════════════════════════════════════════════════════

def rule_analyze(text: str) -> dict:
    t = text.lower().replace(" ", "")

    hard_hits  = sum(1 for k in HARD_TOXIC if k in t)
    hate_hits  = sum(1 for k in HATE_TOXIC if k in t)
    soft_hits  = sum(1 for k in SOFT_TOXIC if k in t)
    clean_hits = sum(1 for k in CLEAN_KW   if k in t)
    chosung    = len(re.findall(r"[ㄱ-ㅎ]{2,}", text))
    neg_pat    = bool(re.search(r"왜이렇|왜이래|그만보|보기싫|안보고|지겨워", t))

    score = min(98,
        hard_hits * 40 +
        hate_hits * 35 +
        soft_hits * 18 +
        chosung   * 12 +
        (25 if neg_pat else 0)
    )

    if hard_hits >= 1 or hate_hits >= 1 or score >= 75:
        rtype = "toxic"
    elif soft_hits >= 1 or neg_pat or score >= 35:
        rtype = "sarcasm"
    elif clean_hits >= 1:
        rtype = "clean"
    else:
        rtype = "neutral"

    return {
        "score":       score,
        "type":        rtype,
        "hard_hits":   hard_hits,
        "hate_hits":   hate_hits,
        "soft_hits":   soft_hits,
        "clean_hits":  clean_hits,
    }


# ══════════════════════════════════════════════════════════
# GPT 분석
# ══════════════════════════════════════════════════════════

async def gpt_analyze(comment: str) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None

    prompt = f"""너는 한국어 유튜브 댓글 악성도 판단 AI다.
아래 기준으로 JSON만 반환해라. 설명 없음.

[판단 기준]
- toxic    : 욕설/비하/인신공격 (80~100점)
- sarcasm  : 조롱/비꼼/빈정거림 (50~79점)
- criticism: 건설적 피드백/정상 부정 의견 (20~49점) ← 절대 차단 금지
- neutral  : 일반 댓글 (10~29점)
- clean    : 응원/긍정 댓글 (0~9점)

[핵심 원칙]
- "비판"과 "악플"을 반드시 구분
- ㅎㅎ, ㅋㅋ, ~네? 같은 간접 조롱 감지
- 단순 부정 의견은 criticism으로 분류
- 한국어 특유 우회 표현 주의

[출력 형식 - JSON만]
{{
  "score": 0~100,
  "type": "toxic|sarcasm|criticism|neutral|clean",
  "reason": "판단 이유 (한 문장)",
  "auto_reply": "자동 대댓글 (20자 내외, criticism/neutral/clean은 빈 문자열)"
}}

댓글: "{comment}"
"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model":       "gpt-4o-mini",
                    "max_tokens":  250,
                    "temperature": 0.1,
                    "messages":    [{"role": "user", "content": prompt}],
                }
            )
            raw = r.json()["choices"][0]["message"]["content"]
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"GPT 분석 실패: {e}")
        return None


# ══════════════════════════════════════════════════════════
# 최종 결과 합산
# ══════════════════════════════════════════════════════════

def merge_results(rule: dict, gpt: Optional[dict]) -> dict:
    rs = rule["score"]
    rt = rule["type"]

    if gpt is None:
        final_score = rs
        final_type  = rt
        reason      = "규칙 기반 분석"
        auto_reply  = ""
        method      = "rule"
    else:
        gs = gpt.get("score", rs)
        gt = gpt.get("type",  rt)
        # Rule 40% + GPT 60% 가중 합산
        final_score = round(rs * 0.4 + gs * 0.6)
        final_type  = gt
        reason      = gpt.get("reason", "")
        auto_reply  = gpt.get("auto_reply", "")
        method      = "gpt"

        # 건설적 피드백 보호 — 점수 강제 캡
        if gt == "criticism" and gs < 50:
            final_score = min(final_score, 45)
            final_type  = "criticism"

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

    # 자동 대댓글 — GPT가 없거나 비어있으면 템플릿 사용
    if not auto_reply:
        auto_reply = REPLY_TEMPLATES.get(display_type, "")

    return {
        "score":       final_score,
        "type":        display_type,      # toxic/warn/clean/neutral/criticism
        "gpt_type":    final_type,
        "action":      action,
        "reason":      reason,
        "auto_reply":  auto_reply,
        "method":      method,
        "rule_score":  rs,
    }


# ══════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════

class CommentReq(BaseModel):
    comment:  str
    use_gpt:  bool = True


@router.post("/analyze")
async def analyze_comment(req: CommentReq):
    text = req.comment.strip()
    if not text:
        raise HTTPException(400, "댓글 내용이 없습니다")
    if len(text) > 1000:
        raise HTTPException(400, "댓글이 너무 깁니다 (최대 1000자)")

    rule = rule_analyze(text)

    gpt = None
    if req.use_gpt and OPENAI_API_KEY:
        # 명백한 케이스는 GPT 스킵 (비용 절약)
        clearly_toxic = rule["score"] > 88 and rule["hard_hits"] >= 1
        clearly_clean = rule["score"] < 10 and rule["clean_hits"] >= 1
        if not clearly_toxic and not clearly_clean:
            gpt = await gpt_analyze(text)

    result = merge_results(rule, gpt)
    logger.info(
        f"[COMMENT] score={result['score']} type={result['type']} "
        f"method={result['method']}"
    )
    return result
