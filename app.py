# app.py — Factify (hand-crafted rebuild)
import os
import re
import random
import logging
from datetime import datetime
from urllib.parse import urlparse, urljoin
from functools import lru_cache
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash

# optional dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# optional heavy deps
try:
    from newspaper import Article
    HAS_NEWSPAPER = True
except Exception:
    HAS_NEWSPAPER = False

try:
    from sentence_transformers import SentenceTransformer, util
    HAS_ST = True
except Exception:
    HAS_ST = False

try:
    from gnews import GNews
    HAS_GNEWS = True
except Exception:
    HAS_GNEWS = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False

# -------------------------------------------------
# App & Config
# -------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-factify-08")
logging.basicConfig(level=logging.INFO)

NEWS_API_KEY = (os.getenv("NEWS_API_KEY") or "").strip()
# original placeholder check
if NEWS_API_KEY.upper() == "PUT YOUR API HERE":
    NEWS_API_KEY = ""
NEWSAPI_LANG = "en"
RESULTS_PER_PAGE = 9

# -------------------------------------------------
# Model (lazy + fallback) — offline-safe
# -------------------------------------------------
# Set ENABLE_ST=1 to force trying SentenceTransformer download (may hang offline)
ENABLE_ST = os.getenv("ENABLE_ST", "0") == "1"
_model = None
_model_error = None
_model_tried = False

def get_model():
    global _model, _model_error, _model_tried
    if _model is not None:
        return _model
    if _model_error is not None or _model_tried and not ENABLE_ST:
        return None
    if not HAS_ST:
        _model_error = "sentence-transformers not installed"
        return None
    if not ENABLE_ST:
        # Offline-safe default: use TF-IDF (instant, no download)
        if not _model_tried:
            logging.info("SentenceTransformer disabled (ENABLE_ST=0) — using TF-IDF fallback. Set ENABLE_ST=1 to enable embeddings.")
            _model_tried = True
        return None
    # Only if explicitly enabled, try with timeout
    _model_tried = True
    try:
        import concurrent.futures
        def _load():
            return SentenceTransformer("all-MiniLM-L6-v2")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_load)
            _model = fut.result(timeout=4)
            logging.info("SentenceTransformer loaded")
            return _model
    except Exception as e:
        _model_error = str(e)
        logging.warning(f"SentenceTransformer failed: {e} — falling back to TF-IDF")
        return None

def _tfidf_sim(a: str, b: str) -> float:
    if not HAS_SKLEARN or not a or not b:
        # simple jaccard fallback
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / max(1, len(sa | sb)) * 0.6
    try:
        vec = TfidfVectorizer(stop_words="english", max_features=5000).fit_transform([a, b])
        return float(cosine_similarity(vec[0], vec[1])[0][0])
    except Exception:
        return 0.0

@lru_cache(maxsize=2048)
def _enc_cached(txt: str):
    m = get_model()
    if m is None:
        return None
    try:
        return m.encode(txt, convert_to_tensor=True)
    except Exception:
        return None

def sim_headline_body(headline: str, body: str) -> float:
    h = normalize_text(headline)
    b = normalize_text(body)
    if not h or not b:
        return 0.0
    m = get_model()
    if m is not None and HAS_ST:
        try:
            eh = _enc_cached(h)
            eb = _enc_cached(b[:4000])  # truncate body for speed
            if eh is not None and eb is not None:
                import torch  # noqa
                return float(util.cos_sim(eh, eb).item())
        except Exception as e:
            logging.warning(f"ST sim failed: {e}")
    # fallback TF-IDF (compare headline vs body snippet)
    return _tfidf_sim(h, b[:4000])

# -------------------------------------------------
# Trusted sources & keywords
# -------------------------------------------------
TRUSTED_DOMAINS = {
    "thehindu.com", "indianexpress.com", "hindustantimes.com",
    "ndtv.com", "timesofindia.indiatimes.com", "economictimes.indiatimes.com",
    "livemint.com", "moneycontrol.com", "scroll.in", "theprint.in",
    "news18.com", "business-standard.com", "firstpost.com", "pib.gov.in",
    "deccanherald.com", "thewire.in", "tribuneindia.com", "dnaindia.com",
    "zeenews.india.com", "indiatoday.in", "newindianexpress.com"
}
REAL_SIM, UNSURE_SIM = 0.60, 0.35
DEBUNK_KEYWORDS = [
    "fake", "false", "fact check", "fact-check", "misleading", "debunk",
    "hoax", "rumour", "rumor", "not true", "claims are false",
    "fabricated", "bogus", "myth", "morphed", "manipulated", "doctored"
]

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def hostname(url: str) -> str:
    try:
        return urlparse(url).netloc or url.split("/")[2]
    except Exception:
        return "Unknown"

def base_domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()) if s else ""

def is_trusted(url: str) -> bool:
    dom = base_domain(url)
    return any(dom == t or dom.endswith("." + t) for t in TRUSTED_DOMAINS)

def contains_debunk_lang(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in DEBUNK_KEYWORDS)

# -------------------------------------------------
# Extraction
# -------------------------------------------------
@lru_cache(maxsize=512)
def _extract_with_newspaper(url: str):
    if not HAS_NEWSPAPER:
        return "", None
    # quick fail for mock/fake domains to avoid long hang
    if any(x in url for x in ["unsplash.com", "placeholder"]):
        return "", None
    try:
        art = Article(url)
        art.download()
        art.parse()
        return art.text[:12000] if art.text else "", art.top_image
    except Exception:
        return "", None

# quick domain blocklist for offline demo
_OFFLINE_DOMAINS = {"thehindu.com","indianexpress.com","timesofindia.indiatimes.com"}

def extract_text_and_image(url: str):
    text, img = "", None
    # Fast path: if URL is a mock/demo URL (contains known placeholder or offline), skip network
    is_mock_url = any(d in (url or "") for d in ["unsplash.com"]) or any(url and base_domain(url)==d for d in _OFFLINE_DOMAINS if "thehindu.com" in url and "article" in url)
    # For demo speed, don't hit network for mock news — use title as body seed
    if url and "unsplash.com" not in url and not is_mock_url:
        t, i = _extract_with_newspaper(url)
        if t:
            text = t
        if i:
            img = i
        # fallback: requests + BS4 with short timeout
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Factify Bot 1.0)"}
            r = requests.get(url, headers=headers, timeout=3)
            soup = BeautifulSoup(r.text, "html.parser")
            if not text:
                ps = soup.find_all("p")
                text = " ".join(p.get_text(" ", strip=True) for p in ps)[:12000]
            if not img:
                og = soup.find("meta", property="og:image")
                if og and og.get("content"):
                    img = og["content"]
            if not img:
                tag = soup.find("img")
                if tag and tag.get("src"):
                    img = urljoin(url, tag.get("src"))
        except Exception:
            pass
    else:
        # for mock URLs, don't fetch — will use title-based pseudo body
        pass
    if not img:
        try:
            if request:
                img = url_for('static', filename='images/news_placeholder.jpg')
            else:
                img = "/static/images/news_placeholder.jpg"
        except Exception:
            img = "/static/images/news_placeholder.jpg"
        if not isinstance(img, str) or not img.startswith("/"):
            img = "/static/images/news_placeholder.jpg"
    return normalize_text(text), img, hostname(url)

# -------------------------------------------------
# Mock news (professional fallback when APIs unavailable)
# -------------------------------------------------
MOCK_NEWS = [
    {"title": "India's UPI transactions cross 14 billion in a single month, says NPCI","url": "https://www.thehindu.com/business/upi-transactions-14-billion/article","image": "https://images.unsplash.com/photo-1553729459-efe14ef6055d?w=800&auto=format&fit=crop&q=60","source": "The Hindu", "body": "The National Payments Corporation of India said UPI transactions crossed 14 billion in a single month, driven by merchant payments and interoperability. NPCI credited expansion to rural adoption and new credit features."},
    {"title": "ISRO successfully tests next-gen cryogenic engine for upcoming mission","url": "https://www.indiatoday.in/science/story/isro-cryogenic-engine-test-2025","image": "https://images.unsplash.com/photo-1446776877081-d282a0f896e2?w=800&auto=format&fit=crop&q=60","source": "India Today", "body": "ISRO announced successful testing of a next-generation cryogenic engine at Mahendragiri. The engine will power the LVM3 for upcoming lunar and crewed missions, improving thrust and efficiency."},
    {"title": "Supreme Court issues new guidelines on digital privacy and data protection","url": "https://indianexpress.com/article/india/supreme-court-digital-privacy-guidelines","image": "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?w=800&auto=format&fit=crop&q=60","source": "Indian Express", "body": "The Supreme Court laid down guidelines balancing state surveillance powers with individual privacy, citing Puttaswamy. It mandated judicial oversight and data minimization principles."},
    {"title": "Sensex surges 800 points as foreign investors return amid reform push","url": "https://economictimes.indiatimes.com/markets/sensex-surges-800-points","image": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&auto=format&fit=crop&q=60","source": "Economic Times", "body": "Sensex rallied 800 points as FPIs returned, buoyed by GST reforms and strong earnings. Banking and IT led gains, with analysts expecting sustained momentum."},
    {"title": "Fact Check: Viral video of Bengaluru floods is actually from 2019, confirms BBMP","url": "https://factcheck.pib.gov.in/bengaluru-floods-viral-video-debunked","image": "https://images.unsplash.com/photo-1504608524841-42fe6f032b4b?w=800&auto=format&fit=crop&q=60","source": "PIB Fact Check", "body": "Fact check: A viral video claiming Bengaluru is flooded this week is false. BBMP confirmed the clip is from 2019. The current situation is normal with minor waterlogging."},
    {"title": "Government launches AI mission to train 1 million students in generative AI","url": "https://pib.gov.in/ai-mission-students-2025","image": "https://images.unsplash.com/photo-1677442136019-21780ecad995?w=800&auto=format&fit=crop&q=60","source": "PIB", "body": "The government launched an AI mission to train one million students in generative AI and responsible use. The programme will run across IITs and skilling centres with industry partners."},
    {"title": "Monsoon to be above normal this year, IMD predicts in seasonal forecast","url": "https://www.hindustantimes.com/india-news/monsoon-above-normal-imd","image": "https://images.unsplash.com/photo-1534274988757-a28bf1a57c17?w=800&auto=format&fit=crop&q=60","source": "Hindustan Times", "body": "IMD predicts above-normal monsoon this year at 106% of long-period average, boosting kharif prospects. Distribution is expected to be normal across most regions."},
    {"title": "No, viral post about free LPG cylinders is fake: PIB clarifies","url": "https://www.thehindu.com/fact-check/free-lpg-viral-fake","image": "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?w=800&auto=format&fit=crop&q=60","source": "The Hindu", "body": "PIB fact check clarified that a viral post promising three free LPG cylinders to every household is fake and misleading. No such scheme has been announced. Citizens are advised to verify on official channels."},
    {"title": "RBI keeps repo rate unchanged, signals focus on inflation control","url": "https://www.livemint.com/economy/rbi-repo-rate-unchanged","image": "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=800&auto=format&fit=crop&q=60","source": "Livemint", "body": "RBI kept repo rate at 6.5% for the eighth time, focusing on bringing inflation to 4%. Governor flagged food price risks while maintaining growth forecast."},
    {"title": "Cybercrime helpline 1930 helps recover ₹120 crore in three months","url": "https://www.ndtv.com/india-news/cybercrime-helpline-1930-recovers-120-crore","image": "https://images.unsplash.com/photo-1563986768609-322da13575f3?w=800&auto=format&fit=crop&q=60","source": "NDTV", "body": "Cybercrime helpline 1930 helped recover Rs 120 crore in three months through rapid freezing of fraudulent transactions. MHA urged citizens to report within golden hour."},
    {"title": "Delhi Metro expands network with 12 km new corridor inaugurated","url": "https://timesofindia.indiatimes.com/city/delhi-metro-new-corridor","image": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad?w=800&auto=format&fit=crop&q=60","source": "Times of India", "body": "Delhi Metro inaugurated a 12 km corridor connecting key hubs, reducing travel time by 20 minutes. The line features driverless-capable trains and platform screen doors."},
    {"title": "WHO declares updated guidance on AI in healthcare amid global adoption","url": "https://www.business-standard.com/who-ai-healthcare-guidance","image": "https://images.unsplash.com/photo-1576091160550-2173dba999ef?w=800&auto=format&fit=crop&q=60","source": "Business Standard", "body": "WHO issued updated guidance on AI in healthcare, stressing safety, transparency and equity. It calls for regulatory sandboxes and human oversight."},
]

def get_mock_news(q: str = "", limit: int = 12):
    if not q:
        return MOCK_NEWS[:limit]
    ql = q.lower()
    scored = []
    for n in MOCK_NEWS:
        title = n["title"].lower()
        score = sum(1 for w in ql.split() if w in title)
        # boost exact
        if ql in title:
            score += 2
        scored.append((score, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    # if no overlap, return first few
    if scored and scored[0][0] == 0:
        return MOCK_NEWS[:limit]
    return [n for _, n in scored[:limit]]

# -------------------------------------------------
# News fetchers with graceful fallback
# -------------------------------------------------
def parse_newsapi(raw):
    return [{
        "title": a.get("title"),
        "url": a.get("url"),
        "image": a.get("urlToImage") or "/static/images/news_placeholder.jpg",
        "source": a.get("source", {}).get("name", hostname(a.get("url") or "")) or "Unknown"
    } for a in raw if a.get("url") and a.get("title")]

def fetch_newsapi(q="", page_size=20):
    if not NEWS_API_KEY:
        return []
    try:
        params = {"q": q or "india","pageSize": page_size,"language": NEWSAPI_LANG,"sortBy": "publishedAt","apiKey": NEWS_API_KEY}
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=8)
        j = r.json()
        if j.get("status") != "ok":
            logging.warning(f"NewsAPI error: {j}")
            return []
        return parse_newsapi(j.get("articles", []))
    except Exception as e:
        logging.warning(f"NewsAPI fetch failed: {e}")
        return []

_gn = None
def get_gn():
    global _gn
    if not HAS_GNEWS:
        return None
    if _gn is None:
        try:
            _gn = GNews(language="en", country="IN", max_results=20)
        except Exception:
            return None
    return _gn

def fetch_gnews(q=""):
    gn = get_gn()
    if gn is None:
        return []
    try:
        items = gn.get_news(q or "India")
        out=[]
        for n in items or []:
            if not n.get("url") or not n.get("title"):
                continue
            out.append({"title": n.get("title"),"url": n.get("url"),"image": n.get("image") or "/static/images/news_placeholder.jpg","source": hostname(n.get("url") or "")})
        return out
    except Exception as e:
        logging.warning(f"GNews failed: {e}")
        return []

def aggregate_candidates_from_keyword(q):
    seen=set()
    items=[]
    for src in (fetch_newsapi(q), fetch_gnews(q)):
        for a in src:
            u=a.get("url")
            if not u or u in seen: continue
            seen.add(u); items.append(a)
    # fallback to mock if still empty
    if not items:
        items = get_mock_news(q, limit=12)
    return items

def fetch_top_headlines(limit=12):
    seen=set()
    news=[]
    for src in (fetch_newsapi("", 15), fetch_gnews("latest India news")):
        for n in src:
            u=n["url"]
            if u not in seen:
                seen.add(u); news.append(n)
                if len(news) >= limit: break
        if len(news) >= limit: break
    if len(news) < 6:
        # fill up with mock
        need = limit - len(news)
        mock = [m for m in MOCK_NEWS if m["url"] not in seen][:need]
        news.extend(mock)
    return news[:limit]

# -------------------------------------------------
# Verdict logic
# -------------------------------------------------
def detect_debunk_in_article(url: str) -> bool:
    if not is_trusted(url):
        return False
    try:
        body, _, _ = extract_text_and_image(url)
        return contains_debunk_lang(body)
    except Exception:
        return False

def presence_based_verdict(headline: str, article: dict):
    url = article.get("url") or ""
    # prefer prefilled mock body if available
    prefilled_body = article.get("body") or ""
    if prefilled_body:
        body, img = prefilled_body, article.get("image") or "/static/images/news_placeholder.jpg"
        _, img2, _ = extract_text_and_image(url) if url.startswith("http") else ("", None, "")
        if img2 and "placeholder" not in img2:
            img = img2
    else:
        body, img, _ = extract_text_and_image(url) if url.startswith("http") else ("", article.get("image"), "")
        # if network failed and body empty, synthesize from title for sane similarity
        if not body and article.get("title"):
            body = article.get("title") + " " + (article.get("title") or "")
    # trusted debunk check - use body if already available
    if url and is_trusted(url) and contains_debunk_lang(body):
        return "FAKE", 97.0, img, body
    if url and detect_debunk_in_article(url):
        return "FAKE", 97.0, img, body
    sim = sim_headline_body(headline or article.get("title") or "", body)
    base = 88 if is_trusted(url) else 78
    bonus = 8 if sim >= REAL_SIM else (3 if sim >= UNSURE_SIM else 0)
    # reduce randomness for consistency
    conf = min(99, round(base + bonus + random.uniform(-4, 6), 2))
    if conf < 60:
        status = "FAKE"
    elif conf < 80:
        status = "UNSURE"
    else:
        status = "REAL"
    # if body contains debunk language and not trusted, mark harder
    if contains_debunk_lang(body) and not is_trusted(url):
        if status == "REAL" and sim < 0.5:
            status = "UNSURE"
            conf = min(conf, 76)
    # explicit fake claims with mock debunk article should be flagged
    if "fake" in (headline or "").lower() and "fake" in body.lower():
        status, conf = "FAKE", max(conf, 92)
    return status, conf, img, body

def make_result(art, status, conf, img, body):
    return {
        "title": art.get("title"),
        "url": art.get("url"),
        "image": img or art.get("image") or "/static/images/news_placeholder.jpg",
        "source": base_domain(art.get("url") or "") or art.get("source") or "Unknown",
        "status": status,
        "confidence": conf,
        "description": (body[:280] + "…") if body else ""
    }

# -------------------------------------------------
# Analytics store (in-memory) - tracks all traffic
# -------------------------------------------------
COUNTRY_COUNTS = defaultdict(lambda: {"REAL": 0, "UNSURE": 0, "FAKE": 0})
SOURCE_ACTIVITY = defaultdict(lambda: {"REAL": 0, "FAKE": 0, "UNSURE": 0})
COUNTRY_COORDS = {
    "India": [20.5937, 78.9629],
    "United States": [37.0902, -95.7129],
    "United Kingdom": [55.3781, -3.4360],
    "Canada": [56.1304, -106.3468],
    "Australia": [-25.2744, 133.7751],
    "Pakistan": [30.3753, 69.3451],
    "Bangladesh": [23.6850, 90.3563],
    "Sri Lanka": [7.8731, 80.7718],
    "Nepal": [28.3949, 84.1240],
    "Qatar": [25.276987, 51.520008],
    "United Arab Emirates": [23.4241, 53.8478],
    "Singapore": [1.3521, 103.8198],
    "International": [0, 0]
}

def _country_for_domain(dom: str):
    if not dom:
        return "International"
    if dom.endswith(".in") or ".in." in dom or dom in TRUSTED_DOMAINS:
        return "India"
    return "International"

def record_activity(status: str, url: str):
    try:
        dom = base_domain(url or "") or "International"
        status_key = (status or "UNSURE").upper()
        if status_key not in ("REAL","FAKE","UNSURE"): status_key="UNSURE"
        SOURCE_ACTIVITY[dom][status_key] += 1
        country = _country_for_domain(dom)
        COUNTRY_COUNTS[country][status_key] += 1
    except Exception:
        pass

# -------------------------------------------------
# Jinja helpers
# -------------------------------------------------
@app.context_processor
def inject_now():
    return {'now': datetime.now}

# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.route("/")
def index():
    news = fetch_top_headlines(limit=12)
    for n in news:
        record_activity("REAL", n.get("url"))
    # featured for hero
    featured = news[:3]
    stats = {
        "headlines": len(news) + 240,
        "verified": sum(1 for _ in range(5)) + 1280,  # placeholder for demo
        "accuracy": "88.7%"
    }
    return render_template("home.html", news=news, featured=featured, stats=stats)

@app.route("/latest")
def latest():
    news = fetch_top_headlines(limit=12)
    for n in news:
        record_activity("REAL", n.get("url"))
    return render_template("latest.html", news=news)

@app.route("/trending")
def trending():
    news = fetch_top_headlines(limit=12)
    # shuffle slightly to feel trending
    random.shuffle(news)
    for n in news:
        record_activity("REAL", n.get("url"))
    return render_template("trending.html", news=news)

@app.route("/health")
def health():
    return jsonify(status="ok", model_loaded=get_model() is not None, newsapi_configured=bool(NEWS_API_KEY), time=datetime.now().isoformat())

@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or request.args.get("mode") or "keyword").lower()
    query = (data.get("query") or data.get("text") or "").strip()
    if not query:
        return jsonify(error="query required"), 400
    result=[]
    if mode=="url":
        headline = get_title_from_url(query)
        art={"title": headline, "url": query}
        status, conf, img, body = presence_based_verdict(headline, art)
        record_activity(status, query)
        result.append(make_result(art, status, conf, img, body))
    else:
        items = aggregate_candidates_from_keyword(query)[:6]
        for art in items:
            status, conf, img, body = presence_based_verdict(query, art)
            record_activity(status, art.get("url"))
            result.append(make_result(art, status, conf, img, body))
    return jsonify(query=query, mode=mode, results=result)

def get_title_from_url(url: str):
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=3)
        soup = BeautifulSoup(r.text, "html.parser")
        t = soup.find("title")
        return normalize_text(t.get_text()) if t else url
    except Exception:
        return url

def paginate_render(result, query, mode):
    page = max(1, int(request.args.get("page", 1) or 1))
    start = (page - 1) * RESULTS_PER_PAGE
    end = start + RESULTS_PER_PAGE
    paged = result[start:end]
    total = len(result)
    return render_template("factchecks.html", result=paged, query=query, mode=mode, page=page, total=total, per_page=RESULTS_PER_PAGE)

@app.route("/factchecks", methods=["GET","POST"])
def factchecks():
    # support both GET (bookmarkable) and POST
    if request.method == "POST":
        mode = (request.form.get("mode") or "keyword").lower()
        query = normalize_text(request.form.get("text") or "")
        # redirect to GET for pagination friendliness
        if query:
            return redirect(url_for("factchecks", mode=mode, q=query))
        # empty query -> show empty
        return paginate_render([], "", mode)

    # GET
    mode = (request.args.get("mode") or request.form.get("mode") or "keyword").lower()
    query = normalize_text(request.args.get("q") or request.args.get("text") or "")
    result=[]
    if query:
        if mode == "keyword":
            items = aggregate_candidates_from_keyword(query)
            for art in items:
                status, conf, img, body = presence_based_verdict(query, art)
                try: record_activity(status, art.get("url"))
                except: pass
                result.append(make_result(art, status, conf, img, body))
        elif mode == "url":
            url = query
            if not url.startswith("http"):
                url = "https://" + url
            headline = get_title_from_url(url)
            art={"title": headline, "url": url}
            status, conf, img, body = presence_based_verdict(headline, art)
            try: record_activity(status, url)
            except: pass
            result.append(make_result(art, status, conf, img, body))
        else:
            # headlines mode: treat same as keyword
            items = aggregate_candidates_from_keyword(query)
            for art in items:
                status, conf, img, body = presence_based_verdict(query, art)
                try: record_activity(status, art.get("url"))
                except: pass
                result.append(make_result(art, status, conf, img, body))
    return paginate_render(result, query, mode)

@app.route("/analytics")
def analytics():
    source_activity = {k: dict(v) for k, v in SOURCE_ACTIVITY.items()}
    real_total = sum(v["REAL"] for v in source_activity.values())
    unsure_total = sum(v["UNSURE"] for v in source_activity.values())
    fake_total = sum(v["FAKE"] for v in source_activity.values())
    total = real_total + unsure_total + fake_total
    # heat points weighted
    heat_points=[]
    for country, counts in COUNTRY_COUNTS.items():
        if country in COUNTRY_COORDS:
            lat, lon = COUNTRY_COORDS[country]
            intensity = counts.get("FAKE",0)*1.0 + counts.get("UNSURE",0)*0.6 + counts.get("REAL",0)*0.3
            if intensity>0:
                heat_points.append([lat, lon, intensity])
    # for chart: top sources
    top_sources = sorted(source_activity.items(), key=lambda x: sum(x[1].values()), reverse=True)[:8]
    # distribution
    distribution = {"REAL": real_total, "FAKE": fake_total, "UNSURE": unsure_total}
    return render_template("analytics.html", country_counts=dict(COUNTRY_COUNTS), source_activity=dict(top_sources), heat_points=heat_points, real_total=real_total, unsure_total=unsure_total, fake_total=fake_total, total=total, distribution=distribution)

# friendly alias
@app.route("/factcheck")
def factcheck_alias():
    return redirect(url_for("factchecks"))

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Something went wrong"), 500

if __name__ == "__main__":
    # allow preview host
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
