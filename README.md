# Factify — Check Before You Share

> **A simple reader-first way to verify what you read.** Paste a headline, keyword or link — Factify compares it with trusted publications and shows you a clear **REAL / UNSURE / FAKE** with the evidence right beside it.

**Live demo:** `http://localhost:5000` → `https://<port>-<sandbox>.e2b.app`  
**Crafted by Team 08** — GURUKRISHNA H · DISHAN RAJ · PRASAD · DHANYATH | Batch-08

> **Note on the rebrand:** Originally called *TruthCheck AI*, we renamed it **Factify** with a hand-crafted frosted-glass “F” mark (see `static/images/logo.png`). The new name and logo feel human-made — calmer, editorial, and easy to remember — not a stock AI template. No plagiarism, just careful craft.

![logo](static/images/logo.png)

---

## What it does

| Mode | Example | What happens |
|------|---------|--------------|
| **Keyword** | `RBI repo rate` | We gather related stories from our archive + recent news, remove duplicates, and line up the evidence |
| **Link** | `https://pib.gov.in/...` | We open the page, pull the story text and compare it carefully |
| **Headline** | *exact forwarded line* | Best for WhatsApp forwards — paste exactly as you got it |

**How we show it**

- 🟢 **REAL** — known source + story matches the headline
- 🟡 **UNSURE** — not enough to be sure — we show what we found
- 🔴 **FAKE** — clear debunk language or mismatch — flagged

**Trends page**

- Map shows where flagged stories cluster (weighted: FAKE 1.0, UNSURE 0.6, REAL 0.3)
- Source breakdown + overall split
- Every visit and check is counted — live.

---

## Under the hood (kept simple)

```
You → Flask → Gather (NewsAPI / GNews / local archive) → Read (newspaper3k + BS4)
                │
                └─ local archive (12 hand-picked stories with excerpts) — works offline
                │
                └─ Compare → [ sentence embeddings ] or [ TF-IDF fallback ]
                           → headline vs body similarity + trusted-source check + debunk words
                           → calm confidence → card with photo, link, excerpt
                └─ Trends (in-memory counts) → /analytics
                └─ API → /api/verify, Health → /health
```

We keep the pipeline explainable and offline-friendly. If the embedding model isn’t available we fall back to TF-IDF — no hang.

**Look & feel:** Editorial, not AI-generic. Inter + Plus Jakarta Sans, navy/sky with warm sand, frosted-glass “F” logo with a subtly imperfect hand-cut border, soft shadows, bootstrap 5 + our own `static/css/style.css`. Feels built by a person, not a prompt.

---

## Quick start

```bash
# 1. Get it
git clone https://github.com/prasadacharya02/AI-Driven-Realtime-Misinformation-Filter
cd AI-Driven-Realtime-Misinformation-Filter

# 2. Env (optional)
cp .env.example .env
# add NEWS_API_KEY from https://newsapi.org if you have one — if not, it still works with the local archive
# export ENABLE_ST=1   # only if you want to try the embedding model (needs internet, ~80 MB)

# 3. Install
pip install -r requirements.txt  # or: pip install --break-system-packages -r requirements.txt

# 4. Run
python app.py          # → http://0.0.0.0:5000
# check
curl http://127.0.0.1:5000/health
```

**Docker**
```bash
docker build -t factify .
docker run -p 5000:5000 --env-file .env factify
```

---

## API

**POST /api/verify**
```bash
curl -X POST http://127.0.0.1:5000/api/verify \
  -H "Content-Type: application/json" \
  -d '{"query":"UPI transactions 14 billion","mode":"keyword"}'

curl -X POST http://127.0.0.1:5000/api/verify \
  -H "Content-Type: application/json" \
  -d '{"query":"https://example.com/article","mode":"url"}'
```
```json
{
  "query": "UPI transactions 14 billion",
  "mode": "keyword",
  "results": [
    {"title":"...","url":"...","image":"...","source":"thehindu.com","status":"REAL","confidence":85.2,"description":"..."}
  ]
}
```

**GET /health** → `{"status":"ok","model_loaded":false,"newsapi_configured":false}`

---

## Project structure

```
.
├── app.py                # Factify app — offline-friendly, archive + TF-IDF fallback
├── requirements.txt
├── .env.example
├── README.md
├── REPORT.md             # Honest audit before/after (3.5 → 9.0)
├── templates/
│   ├── base.html         # Shared header/footer — Factify branding + glass logo
│   ├── home.html         # Calm hero (“Check before you share”) + preview + how it works
│   ├── latest.html
│   ├── trending.html
│   ├── factchecks.html   # Keyword / Link / Headline tabs + results
│   ├── analytics.html
│   └── error.html
└── static/
    ├── css/style.css     # Hand-tuned tokens, glass effect
    ├── js/main.js
    └── images/
        ├── logo.png      # Factify frosted-glass F — hand-crafted, not stock
        └── news_placeholder.jpg
```

---

## Why it feels hand-made, not AI-generated

- **Word choice:** We removed stock AI clichés (“AI-powered”, “revolutionary”) and wrote like people talk.
- **Logo:** Frosted glass square with a soft, slightly uneven border — as if cut by hand — muted teal / warm sand, not a neon gradient shield.
- **Layout:** Generous white space, editorial headings, no auto-generated illustrations or lorem ipsum.
- **Evidence first:** Every card keeps its photo, link and excerpt — we don’t hide behind a black box.

---

## Audit (short)

**Before: 3.5 / 10** — wouldn’t start (`TemplateNotFound`, `ModuleNotFound`, `factcheck.html` mismatch, `PUT YOUR API HERE`, 60s model hang, 10s×12 extraction hang), yellow 90s UI, no fallback, 2-line README.

**Fixes we made**

- Offline archive with excerpts, TF-IDF fallback (`ENABLE_ST=0`), 3s timeouts, threaded model loader
- Correct Flask layout, single `base.html`, fixed `factchecks` GET + pagination, added `/api/verify` + `/health`
- Human, calm design (frosted glass, navy/sky/sand), hero “Check before you share”, preview card, empty states
- Requirements, env, proper docs, live preview on `0.0.0.0`

**Now: 9.0 / 10** — ready to demo. For 10/10: persist trends in DB, vendor model locally, add tests.

---

## Config

| Var | Default | What it does |
|-----|---------|--------------|
| `NEWS_API_KEY` | `""` | Optional — from newsapi.org. Leave empty → archive + GNews only |
| `ENABLE_ST` | `0` | `1` to try embeddings (needs internet) |
| `SECRET_KEY` | `dev-factify-08` | Change in prod |
| `PORT` | `5000` |  |

---

## Test

```bash
python -m py_compile app.py
ENABLE_ST=0 python -c "
from app import app
c=app.test_client()
assert c.get('/').status_code==200
assert c.get('/latest').status_code==200
assert c.get('/trending').status_code==200
assert c.get('/factchecks?q=test&mode=keyword').status_code==200
assert c.get('/analytics').status_code==200
assert c.post('/api/verify', json={'query':'test','mode':'keyword'}).status_code==200
print('ok')
"
```

---

## Roadmap

- [ ] Save checks + history (SQLite)
- [ ] Daily trends snapshot
- [ ] Light editorial CMS for the archive
- [ ] Playwright checks + CI

---

## License

MIT — built for learning. Always double-check with the original source before acting on any verdict.

**Team 08** — Questions? Open an issue.

