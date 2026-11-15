# Formal — Commands

All commands run from `backend/` (Codespace: `/workspaces/Formal/backend`; elsewhere: `cd backend` from the repo root).

```bash
cd /workspaces/Formal/backend
```

---

## Daily loop: Gmail → Agenda

1. Pull new mail into the pool

```bash
python apps/gmail/gmail_scraper.py
```

Prompts: `Fetch window in days (1-365)? [7]` and `Max emails? [all]` — press Enter for both.
The run ends with `Pipeline:` (what was kept or dropped) and `Kept because:` counts.

2. See the ranked Agenda (S/A/B tiers)

```bash
python apps/agenda/main.py
```

3. See everything open, including C/D tiers

```bash
python apps/agenda/main.py --all
```

4. Act on a row number from either view

```bash
python apps/agenda/main.py --done 3
```

```bash
python apps/agenda/main.py --dismiss 3
```

Dismissing a sender's items 3 times (and never marking one done) makes the Gmail
filter drop that sender's Promotions/Social/Updates/Forums mail.

Scraper flags: `--rebuild` re-checks emails already in the pool and rebuilds the
engagement cache; `--ai` labels mail with Groq (needs `GROQ_API_KEY` in `.env`).

---

## Run everything (legacy)

`run.py` also re-runs the paused school scrapers (PowerSchool, Classroom), which
ask for a school password and rewrite `output/*.json`. Use the daily loop above instead.

```bash
python run.py
```

---

## Gradebook only

```bash
python apps/gradebook/powerschool_scraper.py
```

Before the first run, copy `profile.example.json` to `profile.json` and
customize the terms, grade scale, category weights, and courses. The ignored
`profile.json` is optional; without it, Formal uses the example profile and
prints a warning. Terms are date-based, and `source_periods` map portal labels
such as `Q1` to configured terms such as `Fall 2026`. Course profiles support
individual credits, weighted GPA eligibility, and category-weight overrides.

The shared database also has a source-neutral `items` pool and adapter contract
in `core/items.py`. Existing Gmail/Agenda `tasks` remain intact while intake
sources are moved to the common model incrementally.

---

## Grade calculator only

```bash
python apps/gradebook/calculator.py
```

---

## Google Classroom (missing/upcoming assignments)

```bash
python apps/gradebook/classroom_scraper.py
```

---

## Missing assignment report

```bash
python -c "
from apps.gradebook.calculator import generate_missing_report
generate_missing_report(missing_path='output/missing_impactful.json', grades_path='grades.json')
"
```

---

## Install dependencies

```bash
# Gmail
pip install -r apps/gmail/requirements.txt

# Gradebook
pip install -r apps/gradebook/requirements.txt
```

---

## Output files

| File | What's in it |
|------|--------------|
| `output/emails.json` | Current run — resets every scrape |
| `output/emails_history.json` | All emails ever scanned, no duplicates (feeds sender frequency) |
| `output/engagement_cache_*.json` | Per-account sender engagement, reused for 7 days |
| `output/assignments.json` | Current assignments from PowerSchool |
| `output/missing.json` | All missing assignments |
| `output/missing_impactful.json` | Missing assignments sorted by priority score |
| `grades.json` | Raw grades from PowerSchool |
| `formal.db` | SQLite — items pool, sender_reputation, legacy tasks/grades/assignments |

---

## Inspect the database

```bash
sqlite3 formal.db
```

```sql
-- See all tables
.tables

-- Pending tasks from email
SELECT title, due_date, notes FROM tasks WHERE status = 'pending' ORDER BY due_date;

-- Recent assignments
SELECT course, name, earned, possible FROM assignments ORDER BY scraped_at DESC LIMIT 20;

-- Grades
SELECT course, term, source_period, letter_grade, numeric_grade
FROM grades ORDER BY scraped_at DESC;
```

---

## Reset the database (destructive)

```bash
rm formal.db && python core/db.py
```
