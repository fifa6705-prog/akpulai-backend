"""
악플AI (AkpulAI) — 실제 작동하는 채널 분석 백엔드
YouTube Data API v3 + GPT-4o mini 연동

설치:
pip install fastapi uvicorn httpx openai python-dotenv

실행:
uvicorn main_api:app --reload --port 8000

.env 파일:
YOUTUBE_API_KEY=AIza...
OPENAI_API_KEY=sk-proj-...
"""

import os, re, json, asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="악플AI API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")

# ══════════════════════════════════════════════
# Stage 1: 규칙 기반 필터 (한국어 악플 사전)
# ══════════════════════════════════════════════
TOXIC_KW = [
    "ㅅㅂ","씨발","개새끼","ㄴㅁ","미친","병신","좆","쉬발","지랄",
    "개소리","꺼져","닥쳐","죽어","찐따","느금","빻은","역겨워",
    "토나와","구역질","최악이다","쓰레기","폐지","탈주","하차",
    "퀄리티가","수준이","이게뭐","저퀄","망했","끝났","실망이다",
    "신고할게","고소해","캡처했","박제","유포",
]
CLEAN_KW = [
    "감사","고마워","최고","사랑해","응원","힘내","기대","좋아요",
    "대박","멋있","훌륭","존경","팬이에요","구독","알림설정",
    "행복","힘을","위로","덕분에","도움","잘봤","좋은 영상",
]

def rule_score(text: str) -> dict:
    t = text.lower().replace(" ", "")
    toxic = sum(1 for kw in TOXIC_KW if kw in t)
    clean = sum(1 for kw in CLEAN_KW if kw in t)
    chosung = len(re.findall(r'[ㄱ-ㅎ]{2,}', text))
    ts = min(100, toxic * 25 + chosung * 15)
    cs = min(100, clean * 20)
    return {
        "toxic_score": ts,
        "clean_score": cs,
        "is_toxic": ts >= 60,
        "is_clean": cs >= 70 and ts < 30,
    }

# ══════════════════════════════════════════════
# Stage 3: GPT-4o mini 정밀 분석
# ══════════════════════════════════════════════
async def gpt_analyze(comments: List[str]) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None
    sample = comments[:20]
    prompt = f"""한국어 유튜브 댓글 악성도 분석 전문가입니다.
아래 댓글들을 분석하고 JSON만 응답하세요 (다른 텍스트 없이).

댓글: {json.dumps(sample, ensure_ascii=False)}

{{
  "overall_toxicity": 0~100,
  "overall_clean": 0~100,
  "toxic_ratio": 0~100,
  "categories": {{
    "hate_speech": 0~100,
    "subtle_mockery": 0~100,
    "group_attack": 0~100,
    "personal_info": 0~100
  }},
  "verdict": "위험|주의|안전"
}}"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model":"gpt-4o-mini","max_tokens":400,"temperature":0.1,
                  "messages":[{"role":"user","content":prompt}]}
        )
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"```json|```","",txt).strip()
        return json.loads(txt)

# ══════════════════════════════════════════════
# YouTube API 헬퍼
# ══════════════════════════════════════════════
async def yt_get(url, params):
    params["key"] = YOUTUBE_API_KEY
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, params=params)
        return r.json()

async def search_channel(q: str) -> Optional[dict]:
    d = await yt_get("https://www.googleapis.com/youtube/v3/search",
                     {"q":q,"type":"channel","part":"snippet","maxResults":1})
    items = d.get("items", [])
    if not items: return None
    it = items[0]
    return {
        "channel_id": it["id"]["channelId"],
        "title": it["snippet"]["title"],
        "thumbnail": it["snippet"]["thumbnails"]["default"]["url"],
    }

async def get_video_ids(channel_id: str, n=5) -> List[str]:
    d = await yt_get("https://www.googleapis.com/youtube/v3/search",
                     {"channelId":channel_id,"type":"video","part":"id",
                      "order":"date","maxResults":n})
    return [it["id"]["videoId"] for it in d.get("items",[])]

async def get_comments(video_id: str, n=100) -> List[dict]:
    try:
        d = await yt_get("https://www.googleapis.com/youtube/v3/commentThreads",
                         {"videoId":video_id,"part":"snippet","maxResults":n,
                          "order":"relevance","textFormat":"plainText"})
        out = []
        for it in d.get("items",[]):
            s = it["snippet"]["topLevelComment"]["snippet"]
            out.append({"text":s["textDisplay"],"likes":s["likeCount"],
                        "author":s["authorDisplayName"]})
        return out
    except:
        return []

# ══════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════
class AnalyzeReq(BaseModel):
    channel_name: str
    use_gpt: bool = True

@app.get("/")
def root():
    return {"service":"악플AI API","version":"1.0.0","status":"ok"}

@app.get("/api/health")
def health():
    return {
        "youtube_api": "ok" if YOUTUBE_API_KEY else "MISSING",
        "openai_api":  "ok" if OPENAI_API_KEY  else "MISSING (규칙 기반 사용)",
    }

@app.post("/api/analyze")
async def analyze(req: AnalyzeReq):
    if not YOUTUBE_API_KEY:
        raise HTTPException(400, "YOUTUBE_API_KEY 환경변수를 설정하세요")

    # 1) 채널 검색
    ch = await search_channel(req.channel_name)
    if not ch:
        raise HTTPException(404, f"'{req.channel_name}' 채널을 찾을 수 없습니다")

    # 2) 최근 영상 + 댓글 수집
    vids = await get_video_ids(ch["channel_id"], n=5)
    if not vids:
        raise HTTPException(404, "분석할 영상이 없습니다")

    all_comments = []
    for vid in vids:
        all_comments.extend(await get_comments(vid, n=100))

    if not all_comments:
        raise HTTPException(404, "수집된 댓글이 없습니다")

    texts = [c["text"] for c in all_comments]

    # 3) Stage 1 규칙 기반
    scores = [rule_score(t) for t in texts]
    toxic_cnt = sum(1 for s in scores if s["is_toxic"])
    clean_cnt  = sum(1 for s in scores if s["is_clean"])
    avg_toxic  = sum(s["toxic_score"] for s in scores) / len(scores)
    avg_clean  = sum(s["clean_score"] for s in scores) / len(scores)

    # 4) Stage 3 GPT (애매한 케이스만)
    gpt = None
    if req.use_gpt and OPENAI_API_KEY:
        ambiguous = [t for t,s in zip(texts,scores) if 30 <= s["toxic_score"] <= 70]
        if ambiguous:
            try:
                gpt = await gpt_analyze(ambiguous)
                if gpt and "overall_toxicity" in gpt:
                    avg_toxic = (avg_toxic + gpt["overall_toxicity"]) / 2
                    avg_clean  = (avg_clean  + gpt["overall_clean"])  / 2
            except Exception as e:
                gpt = {"error": str(e)}

    ft = round(min(100, avg_toxic))
    fc = round(min(100, avg_clean))
    tr = round(toxic_cnt / len(all_comments) * 100)

    verdict = "위험" if ft >= 70 else "주의" if ft >= 40 else "안전"
    emoji   = "🔴"  if ft >= 70 else "🟡"   if ft >= 40 else "🟢"

    cats = (gpt or {}).get("categories") or {
        "hate_speech":     min(100, int(avg_toxic * 0.9)),
        "subtle_mockery":  min(100, int(avg_toxic * 0.7)),
        "group_attack":    min(100, int(avg_toxic * 0.5)),
        "personal_info":   min(100, int(avg_toxic * 0.3)),
    }

    return {
        "channel": ch,
        "analysis": {
            "total_comments": len(all_comments),
            "toxic_score":    ft,
            "clean_score":    fc,
            "toxic_ratio":    tr,
            "toxic_count":    toxic_cnt,
            "clean_count":    clean_cnt,
            "verdict":        verdict,
            "verdict_emoji":  emoji,
            "categories":     cats,
            "gpt_used":       bool(gpt and "error" not in gpt),
        },
        "samples": {
            "toxic": [c["text"] for c in all_comments
                      if rule_score(c["text"])["is_toxic"]][:3],
            "clean": [c["text"] for c in all_comments
                      if rule_score(c["text"])["is_clean"]][:3],
        }
    }

# ── 대댓글 자동화 ──────────────────────────

class ReplyReq(BaseModel):
    comment: str
    template: Optional[str] = None

class ThanksReq(BaseModel):
    comment: str
    style_samples: List[str] = []

DEFAULT_WARNING = ("⚠️ 이 채널은 악플AI의 보호를 받고 있습니다. "
                   "건설적인 피드백은 환영하지만, 비하나 조롱은 "
                   "자동으로 감지되어 기록됩니다. 건전한 댓글 문화에 함께해주세요.")
DEFAULT_THANKS  = "💙 좋은 댓글 감사합니다! 응원이 큰 힘이 돼요 🙏"

@app.post("/api/auto-reply/warning")
async def warning_reply(req: ReplyReq):
    """악플 → 경고 대댓글 (Pro 이상)"""
    if not OPENAI_API_KEY:
        return {"reply": req.template or DEFAULT_WARNING, "method": "template"}

    prompt = (f"한국어 유튜브 채널 보호 AI입니다.\n"
              f"악성댓글에 경고 대댓글을 정중하지만 단호하게 2~3문장으로 작성하세요.\n"
              f"참고 템플릿: {req.template or DEFAULT_WARNING}\n\n"
              f"악성댓글: {req.comment}\n\n대댓글만 출력:")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model":"gpt-4o-mini","max_tokens":150,
                  "messages":[{"role":"user","content":prompt}]}
        )
        reply = r.json()["choices"][0]["message"]["content"].strip()
        return {"reply": reply, "method": "gpt"}

@app.post("/api/auto-reply/thanks")
async def thanks_reply(req: ThanksReq):
    """클린 댓글 → 감사 대댓글 (Elite, 말투 학습)"""
    if not OPENAI_API_KEY:
        return {"reply": DEFAULT_THANKS, "method": "template"}

    style = ""
    if req.style_samples:
        style = "크리에이터 말투 샘플:\n" + "\n".join(f"- {s}" for s in req.style_samples[:5])

    prompt = (f"한국어 유튜브 크리에이터 대신 감사 대댓글을 2~3문장으로 작성하세요.\n"
              f"{style}\n\n팬 댓글: {req.comment}\n\n대댓글만 출력:")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model":"gpt-4o-mini","max_tokens":150,
                  "messages":[{"role":"user","content":prompt}]}
        )
        reply = r.json()["choices"][0]["message"]["content"].strip()
        return {"reply": reply, "method": "gpt"}

# ══════════════════════════════════════════════
# 댓글 단건 분석 (시뮬레이터용)
# ══════════════════════════════════════════════

class CommentReq(BaseModel):
    comment: str
    use_gpt: bool = True

@app.post("/api/comment/analyze")
async def analyze_comment(req: CommentReq):
    """
    댓글 1개 분석 — 시뮬레이터용
    POST /api/comment/analyze
    {"comment": "댓글 내용", "use_gpt": true}
    """
    text = req.comment.strip()
    if not text:
        raise HTTPException(400, "댓글 내용이 없습니다")

    # Stage 1: 규칙 기반
    stage1 = rule_score(text)

    # Stage 2: GPT-4o mini (use_gpt=True이고 API 키 있을 때)
    if req.use_gpt and OPENAI_API_KEY:
        prompt = f"""당신은 한국어 유튜브 댓글의 악성도를 판단하는 전문가입니다.
아래 댓글을 분석하고 JSON만 응답하세요 (다른 텍스트 없이).

댓글: "{text}"

{{
  "toxic_score": 0~100,
  "type": "toxic|warn|clean|normal",
  "reason": "판단 이유 한 문장",
  "auto_reply": "AI가 달 자동 대댓글 내용"
}}

판단 기준:
- toxic(70+): 명백한 욕설, 혐오, 집단공격
- warn(30~69): 비꼬기, 조롱, 우회 비하, 존재 부정
- clean(0~10): 응원, 감사, 구독 등 긍정 댓글
- normal(11~29): 일반 의견, 질문 등

warn 예시: "요즘 퀄리티가 많이 떨어졌네요", "그만 보고 싶다", "예전이 나았는데"
→ 이런 표현은 반드시 warn 이상으로 분류하세요."""

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "max_tokens": 300,
                        "temperature": 0.1,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                raw = r.json()["choices"][0]["message"]["content"]
                raw = re.sub(r"```json|```", "", raw).strip()
                gpt = json.loads(raw)

                score = gpt.get("toxic_score", stage1["toxic_score"])
                type_ = gpt.get("type", "normal")
                reason = gpt.get("reason", "")
                auto_reply = gpt.get("auto_reply", "")

                return {
                    "score": score,
                    "type": type_,
                    "reason": reason,
                    "auto_reply": auto_reply,
                    "method": "gpt"
                }
        except Exception as e:
            # GPT 실패 시 규칙 기반으로 폴백
            pass

    # 규칙 기반 결과 반환
    ts = stage1["toxic_score"]
    if ts >= 65:
        type_ = "toxic"
        reply = "⚠️ 이 채널은 악플AI의 보호를 받고 있습니다. 건설적인 피드백은 환영하지만, 비하나 조롱은 자동으로 감지되어 기록됩니다."
    elif ts >= 25:
        type_ = "warn"
        reply = "⚠️ 해당 댓글은 조롱·비꼬기 패턴으로 분류되어 모니터링 중입니다."
    elif stage1["clean_score"] >= 60:
        type_ = "clean"
        reply = "따뜻하게 봐주셔서 정말 감사해요 🙏 이런 댓글 덕분에 더 좋은 콘텐츠 만들 힘이 납니다."
    else:
        type_ = "normal"
        reply = "댓글 감사합니다! 앞으로도 좋은 콘텐츠로 찾아올게요 😊"

    return {
        "score": ts,
        "type": type_,
        "reason": "규칙 기반 분석",
        "auto_reply": reply,
        "method": "rule"
    }
