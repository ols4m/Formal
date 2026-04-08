# Formal — Project Context for Claude Code

## What This Is

Formal is a personal life operating system and AI-powered butler. It ingests data from multiple life sectors (school, finance, tasks, personal notes), finds asymmetric advantages, and executes on them. Not a productivity app — a personal strategist.

## Repo Structure

```
formal/
├── backend/
│   └── apps/
│       ├── gradebook/        ← mostly built, Python backend
│       │   ├── docs/
│       │   ├── calculator.py         # GPA calc, category averages, assignment impact
│       │   ├── classroom_scraper.py  # Google Classroom (blocked, needs fallback)
│       │   ├── powerschool_scraper.py
│       │   ├── priority.py           # Priority scoring engine (P = Wc * I * R * U * (1 + V))
│       │   ├── requirements.txt
│       │   └── .env.example
│       └── jots/
│           └── notebook/     ← built, Flask web app
│               ├── templates/index.html
│               ├── app.py            # Flask app, resource management, auto-categorization
│               ├── cli.py
│               ├── db.py
│               ├── fetcher.py
│               └── requirements.txt
```

## Core Apps (build in this order)

| App | Status | Purpose |
|-----|--------|---------|
| **Gradebook** | ✅ Mostly built | Grade scraping, GPA calc, assignment prioritization |
| **Notebook** | ✅ Built | Resource library with smart categorization |
| **The Agenda** | 🔲 Next | Universal task list, self-updating, pulls from Gradebook + email + scrapers |
| **The Coach** | 🔲 | Central AI brain, ingests all apps, outputs actionable advice |
| **The Checkbook** | 🔲 | Financial mgmt, bank sync, projections |
| **The Receipt** | 🔲 | Purchase advisor + want tracker, connects to Checkbook |
| **The Utilizer** | 🔲 | Opportunity engine, automation hub |
| **Remaining Jots** | 🔲 | Journal, Diary, Whiteboard (Notebook already built) |

## Architecture & Data Flow

```
Inputs (PowerSchool, Email, Finance, Manual, Share Sheet)
  → Intake Layer (semantic detect → normalize → route)
    → Core Apps (Gradebook, Agenda, Checkbook, Receipt, Utilizer, Jots)
      → Priority Engine (impact + urgency scoring)
        → Coach (context + strategy + advice)
          → Outputs (ranked tasks, actionable advice, alerts)
            → Feedback Loop (refines Coach over time)
```

## Key Systems

### Priority Engine (`gradebook/priority.py`)
Cross-app scoring formula: `P = clamp(Wc * I * R * U * (1 + V), 0, 1)`
- **Wc**: Category weight (Classwork 0.35, Homework 0.20, Quizzes/Tests/IA 0.15 each)
- **I**: Assignment impact (points_possible / total_category_points)
- **R**: Grade risk (distance from target grade of 93%)
- **U**: Urgency (days until due, 14-day lookahead window)
- **V**: Volatility boost (higher for high-stakes items like tests)
- Output: tier ranking S/A/B/C/D with priority scores

### Grade Calculator (`gradebook/calculator.py`)
- Weighted/unweighted GPA from letter grades (AP/Honors +1.0 bonus)
- Per-category grade breakdown by weight
- Assignment impact prediction (best/worst/floor scenarios)
- School rounding rule: round up if decimal >= 0.3

### Notebook App (`jots/notebook/app.py`)
- Flask web app, runs on localhost:5000
- 22 auto-detected topic categories via keyword matching
- Platform detection (YouTube, GitHub, Reddit, etc.)
- Endpoints: `/api/resources`, `/api/add`, `/api/delete/<id>`, `/api/topics/<id>`, `/api/all-topics`

## Tech Stack

- **Backend**: Python, Flask currently → moving toward FastAPI
- **Frontend**: React / Next.js (not started yet)
- **Database**: SQLite currently → moving to unified DB layer
- **Scraping**: BeautifulSoup, PowerSchool scraper built

## Current Limitations

- Google Classroom API blocked — need fallback scraping approach
- No unified database layer yet (each app has its own)
- No frontend yet
- No cross-app data sharing yet

## Immediate Next Step

**Build The Agenda** — unified task list that:
1. Pulls from Gradebook's priority output (`priority.py`)
2. Becomes the first cross-app integration in the system
3. Self-updates based on due dates, workload, consequences

## Conventions

- Keep apps self-contained under `backend/apps/<appname>/`
- Each app gets its own `requirements.txt`
- Prefer FastAPI for new apps, Flask only for existing Notebook
- SQLite for now; design schemas with future migration in mind
- No frontend work until backend APIs are stable
