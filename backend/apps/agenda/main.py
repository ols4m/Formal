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
from core.profile import (
    get_current_term,
    load_profile,
    normalize_category_name,
    source_periods_for_term,
)

# ──────────────────────────────────────────────
# PRIORITY ENGINE
# ──────────────────────────────────────────────

def compute_priority(
    category,
    points_possible,
    current_grade,
    cat_points_so_far,
    due_date_str,
    course_name=None,
    profile=None,
):
    profile = profile or load_profile()
    grading = profile["grading"]
    category = normalize_category_name(category)
    category_config = profile.get("categories", {}).get(category, {})
    course_config = profile.get("courses", {}).get(course_name, {}) if course_name else {}
    course_weights = {
        normalize_category_name(name): weight
        for name, weight in course_config.get("category_weights", {}).items()
    }
    today = date.today()
    wc    = course_weights.get(category, category_config.get("weight", 0.20))
    total = cat_points_so_far + points_possible
    i_val = points_possible / total if total > 0 else 1.0
    r_val = max(0.0, (grading["target_grade"] - current_grade) / 100)

    if due_date_str:
        try:
            due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            days_left = (due - today).days
        except ValueError:
            days_left = grading.get("lookahead_days", 14)
    else:
        days_left = grading.get("lookahead_days", 14)

    lookahead_days = grading.get("lookahead_days", 14)
    u_val = max(0.0, min(1.0, 1 - (days_left / lookahead_days)))
    v_val = category_config.get("volatility", 0.0)
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

def latest_grade(conn, course, term, source_periods):
    for source_period in reversed(source_periods):
        row = conn.execute(
            """SELECT numeric_grade FROM grades
               WHERE course = ? AND term = ? AND source_period = ?
               ORDER BY scraped_at DESC LIMIT 1""",
            (course, term, source_period),
        ).fetchone()
        if row and row["numeric_grade"] is not None:
            return row["numeric_grade"]
    return 85.0


def cat_points(conn, course, category, term):
    rows = conn.execute(
        """SELECT category, SUM(possible) AS total FROM assignments
           WHERE course = ? AND term = ? AND possible IS NOT NULL
             AND scraped_at = (
                 SELECT MAX(scraped_at) FROM assignments WHERE course = ? AND term = ?
             )
           GROUP BY category""",
        (course, term, course, term),
    ).fetchall()
    canonical_category = normalize_category_name(category)
    return sum(
        row["total"] or 0.0
        for row in rows
        if normalize_category_name(row["category"] or "") == canonical_category
    )


# ──────────────────────────────────────────────
# VIEWS
# ──────────────────────────────────────────────

def print_tasks(show_all=False):
    profile = load_profile()
    term = get_current_term(profile)["name"]
    source_periods = source_periods_for_term(term, profile)
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
        grade    = latest_grade(conn, course, term, source_periods) if course else 85.0
        cp       = cat_points(conn, course, category, term) if course else 0.0

        p, days_left, impact = compute_priority(
            category, points, grade, cp, task.get("due_date"), course, profile
        )
        task["priority"] = p
        task["tier"]     = get_tier(p)
        task["days_left"] = days_left
        task["impact"]   = impact
        scored.append(task)

    conn.close()
    scored.sort(key=lambda t: t["priority"], reverse=True)

    print()
    print("=" * 85)
    print(f"  THE AGENDA — {date.today()}   |   Target: {profile['grading']['target_grade']}%   |   Lookahead: {profile['grading'].get('lookahead_days', 14)}d")
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
        """SELECT g.course, g.course_type, g.letter_grade, g.numeric_grade,
                  g.term, g.source_period, g.scraped_at
           FROM grades g
           INNER JOIN (
               SELECT course, source_period, MAX(scraped_at) AS latest
               FROM grades
               GROUP BY course, source_period
           ) l ON g.course = l.course AND g.source_period = l.source_period
              AND g.scraped_at = l.latest
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
    print(f"  {'COURSE':<30}  {'TYPE':<8}  {'TERM':<16}  {'PERIOD':<8}  {'GRADE'}")
    print("-" * 78)
    for r in rows:
        print(f"  {r['course'][:30]:<30}  {(r['course_type'] or ''):<8}  {(r['term'] or ''):<16}  {(r['source_period'] or ''):<8}  {r['letter_grade']} ({r['numeric_grade']}%)")
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
