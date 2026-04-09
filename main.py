"""
악플AI (AkpulAI) — 통합 백엔드 v3.0
YouTube Data API v3 + GPT-4o mini + 리드 수집

설치: pip install -r requirements.txt
실행: uvicorn main:app --host 0.0.0.0 --port $PORT

.env:
  YOUTUBE_API_KEY=AIza...
  OPENAI_API_KEY=sk-proj-...
  ADMIN_TOKEN=your-secret-token
  SUPABASE_URL=https://xxx.supabase.co        (선택 — 없으면 JSON 파일 저장)
  SUPABASE_KEY=your-supabase-anon-key         (선택)
"""

import os, re, json, logging
from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── 로깅 설정 ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 앱 초기화 ────────────────────────────────────────────
app = FastAPI(
    title="악플AI API",
    version="3.0.0",
    description="악플 탐지 · 리드 수집 · 증거 저장 통합 백엔드",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 배포 후 akpulai.com 으로 교체 권장
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 라우터 등록 ──────────────────────────────────────────
from routes.leads    import router as leads_router
from routes.comments import router as comments_router
from routes.evidence import router as evidence_router

app.include_router(leads_router)
app.include_router(comments_router)
app.include_router(evidence_router)

# ── 환경변수 ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
ADMIN_TOKEN     = os.getenv("ADMIN_TOKEN", "")


# ══════════════════════════════════════════════════════════
# 채널 분석 API (기존 유지)
# ══════════════════════════════════════════════════════════

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
    toxic   = sum(1 for kw in TOXIC_KW if kw in t)
    clean   = sum(1 for kw in CLEAN_KW if kw in t)
    chosung = len(re.findall(r'[ㄱ-ㅎ]{2,}', text))
    ts = min(100, toxic * 25 + chosung * 15)
    cs = min(100, clean * 20)
    return {
        "toxic_score": ts,
        "clean_score": cs,
        "is_toxic":    ts >= 60,
        "is_clean":    cs >= 70 and ts < 30,
    }

async def yt_get(url: str, params: dict) -> dict:
    params["key"] = YOUTUBE_API_KEY
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, params=params)
        return r.json()

async def search_channel(q: str) -> Optional[dict]:
    d = await yt_get(
        "https://www.googleapis.com/youtube/v3/search",
        {"q": q, "type": "channel", "part": "snippet", "maxResults": 1}
    )
    items = d.get("items", [])
    if not items:
        return None
    it = items[0]
    return {
        "channel_id": it["id"]["channelId"],
        "title":      it["snippet"]["title"],
        "thumbnail":  it["snippet"]["thumbnails"]["default"]["url"],
    }

async def get_video_ids(channel_id: str, n: int = 5) -> List[str]:
    d = await yt_get(
        "https://www.googleapis.com/youtube/v3/search",
        {"channelId": channel_id, "type": "video", "part": "id",
         "order": "date", "maxResults": n}
    )
    return [it["id"]["videoId"] for it in d.get("items", [])]

async def get_comments(video_id: str, n: int = 100) -> List[dict]:
    try:
        d = await yt_get(
            "https://www.googleapis.com/youtube/v3/commentThreads",
            {"videoId": video_id, "part": "snippet",
             "maxResults": n, "order": "relevance", "textFormat": "plainText"}
        )
        out = []
        for it in d.get("items", []):
            s = it["snippet"]["topLevelComment"]["snippet"]
            out.append({"text": s["textDisplay"], "likes": s["likeCount"]})
        return out
    except:
        return []

async def gpt_channel_analyze(comments: List[str]) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None
    sample = comments[:20]
    prompt = f"""한국어 유튜브 댓글 악성도 분석. JSON만 응답.

댓글: {json.dumps(sample, ensure_ascii=False)}

{{
  "overall_toxicity": 0~100,
  "overall_clean":    0~100,
  "toxic_ratio":      0~100,
  "categories": {{
    "hate_speech":    0~100,
    "subtle_mockery": 0~100,
    "group_attack":   0~100,
    "personal_info":  0~100
  }},
  "verdict": "위험|주의|안전"
}}"""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": "gpt-4o-mini", "max_tokens": 400,
                      "temperature": 0.1,
                      "messages": [{"role": "user", "content": prompt}]}
            )
            txt = r.json()["choices"][0]["message"]["content"]
            txt = re.sub(r"```json|```", "", txt).strip()
            return json.loads(txt)
    except:
        return None


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
    vids = await get_video_ids(ch["channel_id"], n=5)
    if not vids:
        raise HTTPException(404, "분석할 영상이 없습니다")

    all_comments = []
    for vid in vids:
        all_comments.extend(await get_comments(vid, n=100))
    if not all_comments:
        raise HTTPException(404, "수집된 댓글이 없습니다")

    texts  = [c["text"] for c in all_comments]
    scores = [rule_score(t) for t in texts]

    toxic_cnt  = sum(1 for s in scores if s["is_toxic"])
    clean_cnt  = sum(1 for s in scores if s["is_clean"])
    avg_toxic  = sum(s["toxic_score"] for s in scores) / len(scores)
    avg_clean  = sum(s["clean_score"] for s in scores) / len(scores)

    gpt = None
    if req.use_gpt and OPENAI_API_KEY:
        ambiguous = [t for t, s in zip(texts, scores) if 30 <= s["toxic_score"] <= 70]
        if ambiguous:
            try:
                gpt = await gpt_channel_analyze(ambiguous)
                if gpt and "overall_toxicity" in gpt:
                    avg_toxic = (avg_toxic + gpt["overall_toxicity"]) / 2
                    avg_clean = (avg_clean  + gpt["overall_clean"])   / 2
            except:
                pass

    ft = round(min(100, avg_toxic))
    fc = round(min(100, avg_clean))
    tr = round(toxic_cnt / len(all_comments) * 100)
    verdict = "위험" if ft >= 70 else "주의" if ft >= 40 else "안전"
    emoji   = "🔴"  if ft >= 70 else "🟡"   if ft >= 40 else "🟢"
    cats = (gpt or {}).get("categories") or {
        "hate_speech":    min(100, int(avg_toxic * 0.9)),
        "subtle_mockery": min(100, int(avg_toxic * 0.7)),
        "group_attack":   min(100, int(avg_toxic * 0.5)),
        "personal_info":  min(100, int(avg_toxic * 0.3)),
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
            "gpt_used":       bool(gpt),
        },
        "samples": {
            "toxic": [c["text"] for c in all_comments
                      if rule_score(c["text"])["is_toxic"]][:3],
            "clean": [c["text"] for c in all_comments
                      if rule_score(c["text"])["is_clean"]][:3],
        },
    }


# ══════════════════════════════════════════════════════════
# 관리자 API
# ══════════════════════════════════════════════════════════

def verify_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "관리자 인증 실패")

@app.get("/api/admin/leads", dependencies=[Depends(verify_admin)])
async def admin_get_leads(lead_type: str = "free_trial", limit: int = 100):
    from storage.store import get_leads
    leads = await get_leads(lead_type, limit)
    return {"type": lead_type, "count": len(leads), "leads": leads}

@app.get("/api/admin/stats", dependencies=[Depends(verify_admin)])
async def admin_stats():
    from storage.store import get_stats
    return await get_stats()


# ══════════════════════════════════════════════════════════
# 헬스체크
# ══════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {
        "service": "악플AI API",
        "version": "3.0.0",
        "status":  "ok",
        "docs":    "/docs",
    }

@app.get("/api/health")
def health():
    from storage.store import USE_SUPABASE
    return {
        "youtube_api": "ok" if YOUTUBE_API_KEY else "MISSING",
        "openai_api":  "ok" if OPENAI_API_KEY  else "MISSING",
        "admin_token": "ok" if ADMIN_TOKEN      else "MISSING ⚠️",
        "storage":     "supabase" if USE_SUPABASE else "json (임시)",
    }
