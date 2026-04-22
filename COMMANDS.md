# Formal — Commands

All commands run from `backend/`.

```bash
cd /workspaces/Formal/backend
```

---

## Run everything (grades + gmail)

```bash
python run.py
```

---

## Gmail only

```bash
python apps/gmail/gmail_scraper.py
```

Prompts:
- `Fetch window — 1, 7, or 30 days?` — press Enter for 7
- `Max emails? [all]` — press Enter for all, or type a number like `30`

---

## Gradebook only

```bash
python apps/gradebook/powerschool_scraper.py
```

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
| `output/emails_history.json` | All emails ever scanned, no duplicates |
| `output/assignments.json` | Current assignments from PowerSchool |
| `output/missing.json` | All missing assignments |
| `output/missing_impactful.json` | Missing assignments sorted by priority score |
| `grades.json` | Raw grades from PowerSchool |
| `formal.db` | SQLite — tasks, grades, assignments |

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
SELECT course, quarter, letter_grade, numeric_grade FROM grades ORDER BY scraped_at DESC;
```

---

## Reset the database (destructive)

```bash
rm formal.db && python core/db.py
```
