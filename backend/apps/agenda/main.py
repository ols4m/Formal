"""
The Agenda — CLI
Pulls tasks from formal.db, scores them with the priority engine, prints a ranked list.

Run: python main.py
     python main.py --all        (include done/dismissed)
     python main.py --grades     (show current grade snapshot)
"""

import sys
import argparse
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.db import get_db, init_db

# ──────────────────────────────────────────────
# PRIORITY ENGINE
# ──────────────────────────────────────────────

TARGET_GRADE  = 93.0
LOOKAHEAD_DAYS = 14

CATEGORY_WEIGHTS = {
    "Classwork": 0.35, "CW": 0.35,
    "Homework":  0.20, "HW": 0.20,
    "Quizzes":   0.15, "Quiz": 0.15,
    "Tests":     0.15, "Test": 0.15,
    "Interim Assessment": 0.15, "IA": 0.15,
}

VOLATILITY_BOOST = {
    "Tests": 0.4, "Test": 0.4,
    "Interim Assessment": 0.4, "IA": 0.4,
    "Quizzes": 0.2, "Quiz": 0.2,
    "Homework": 0.0, "HW": 0.0,
    "Classwork": 0.0, "CW": 0.0,
}


def compute_priority(category, points_possible, current_grade, cat_points_so_far, due_date_str):
    today = date.today()
    wc    = CATEGORY_WEIGHTS.get(category, 0.20)
    total = cat_points_so_far + points_possible
    i_val = points_possible / total if total > 0 else 1.0
    r_val = max(0.0, (TARGET_GRADE - current_grade) / 100)

    if due_date_str:
        try:
            due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            days_left = (due - today).days
        except ValueError:
            days_left = LOOKAHEAD_DAYS
    else:
        days_left = LOOKAHEAD_DAYS

    u_val = max(0.0, min(1.0, 1 - (days_left / LOOKAHEAD_DAYS)))
    v_val = VOLATILITY_BOOST.get(category, 0.0)
    p     = max(0.0, min(1.0, wc * i_val * r_val * u_val * (1 + v_val)))
    return p, days_left, round(i_val * 100, 1)


def get_tier(p):
    if p >= 0.025: return "S"
    if p >= 0.015: return "A"
    if p >= 0.008: return "B"
    if p >= 0.003: return "C"
    return "D"


TIER_LABEL = {"S": "🔥 S", "A": "⚠️  A", "B": "📌 B", "C": "🧱 C", "D": "💤 D"}

# ──────────────────────────────────────────────
# DB HELPERS
# ──────────────────────────────────────────────

def latest_grade(conn, course):
    row = conn.execute(
        """SELECT numeric_grade FROM grades
           WHERE course = ?
           ORDER BY scraped_at DESC LIMIT 1""",
        (course,),
    ).fetchone()
    return row["numeric_grade"] if row and row["numeric_grade"] is not None else 85.0


def cat_points(conn, course, category):
    row = conn.execute(
        """SELECT SUM(possible) AS total FROM assignments
           WHERE course = ? AND category = ? AND possible IS NOT NULL
             AND scraped_at = (SELECT MAX(scraped_at) FROM assignments WHERE course = ?)""",
        (course, category, course),
    ).fetchone()
    return row["total"] or 0.0


# ──────────────────────────────────────────────
# VIEWS
# ──────────────────────────────────────────────

def print_tasks(show_all=False):
    init_db()
    conn = get_db()

    query = "SELECT * FROM tasks"
    if not show_all:
        query += " WHERE status = 'pending'"

    rows = conn.execute(query).fetchall()

    if not rows:
        print("No tasks found. Run the scrapers first, or add tasks manually via the DB.")
        conn.close()
        return

    scored = []
    for row in rows:
        task     = dict(row)
        course   = task.get("course") or ""
        category = task.get("category") or "Homework"
        points   = task.get("possible_points") or 100.0
        grade    = latest_grade(conn, course) if course else 85.0
        cp       = cat_points(conn, course, category) if course else 0.0

        p, days_left, impact = compute_priority(category, points, grade, cp, task.get("due_date"))
        task["priority"] = p
        task["tier"]     = get_tier(p)
        task["days_left"] = days_left
        task["impact"]   = impact
        scored.append(task)

    conn.close()
    scored.sort(key=lambda t: t["priority"], reverse=True)

    print()
    print("=" * 85)
    print(f"  THE AGENDA — {date.today()}   |   Target: {TARGET_GRADE}%   |   Lookahead: {LOOKAHEAD_DAYS}d")
    print("=" * 85)
    print(f"  {'TIER':<6}  {'SCORE':>7}  {'DAYS':>4}  {'SOURCE':<10}  {'COURSE':<22}  TASK")
    print("-" * 85)

    for t in scored:
        days_str = str(t["days_left"]) if t["days_left"] >= 0 else "LATE"
        tier_str = TIER_LABEL.get(t["tier"], t["tier"])
        status   = f" [{t['status']}]" if t["status"] != "pending" else ""
        print(
            f"  {tier_str:<7}  {t['priority']:>7.4f}  {days_str:>4}  "
            f"{(t.get('source') or ''):<10}  {(t.get('course') or '')[:22]:<22}  "
            f"{t.get('title','')}{status}"
        )

    print("-" * 85)
    print(f"  {len(scored)} task(s)   Legend: S=Critical  A=High  B=Medium  C=Low  D=Minimal")
    print("=" * 85)
    print()


def print_grades():
    init_db()
    conn = get_db()

    rows = conn.execute(
        """SELECT g.course, g.course_type, g.letter_grade, g.numeric_grade, g.quarter, g.scraped_at
           FROM grades g
           INNER JOIN (
               SELECT course, MAX(scraped_at) AS latest
               FROM grades
               GROUP BY course
           ) l ON g.course = l.course AND g.scraped_at = l.latest
           ORDER BY g.numeric_grade DESC"""
    ).fetchall()
    conn.close()

    if not rows:
        print("No grade data found. Run the PowerSchool scraper first.")
        return

    print()
    print("=" * 55)
    print(f"  GRADE SNAPSHOT — {date.today()}")
    print("=" * 55)
    print(f"  {'COURSE':<30}  {'TYPE':<8}  {'QTR':<4}  {'GRADE'}")
    print("-" * 60)
    for r in rows:
        print(f"  {r['course'][:30]:<30}  {(r['course_type'] or ''):<8}  {(r['quarter'] or ''):<4}  {r['letter_grade']} ({r['numeric_grade']}%)")
    print("=" * 55)
    print()


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Agenda — priority task list")
    parser.add_argument("--all",    action="store_true", help="Include done/dismissed tasks")
    parser.add_argument("--grades", action="store_true", help="Show current grade snapshot")
    args = parser.parse_args()

    if args.grades:
        print_grades()
    else:
        print_tasks(show_all=args.all)
