"""
PRIORITY CALCULATOR
Ranks upcoming assignments based on grade impact, risk, and urgency.

Formula: P = clamp(Wc * I * R * U * (1 + V), 0, 1)
"""

import json
import os
import sys
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# ==========================================
# CONFIGURATION
# ==========================================

TARGET_GRADE = 93.0
LOOKAHEAD_DAYS = 14  # Window for urgency calculation

CATEGORY_WEIGHTS = {
    'Classwork': 0.35,
    'Homework': 0.20,
    'Quizzes': 0.15,
    'Tests': 0.15,
    'Interim Assessment': 0.15,
    # Short names/Aliases
    'Quiz': 0.15,
    'Test': 0.15,
    'IA': 0.15,
    'HW': 0.20,
    'CW': 0.35
}

VOLATILITY_BOOST = {
    'Tests': 0.4,
    'Test': 0.4,
    'Interim Assessment': 0.4,
    'IA': 0.4,
    'Quizzes': 0.2,
    'Quiz': 0.2,
    'Homework': 0.0,
    'HW': 0.0,
    'Classwork': 0.0,
    'CW': 0.0
}

# ==========================================
# UTILS
# ==========================================

def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def parse_date(date_str: str) -> Optional[date]:
    """Parse YYYY-MM-DD string to date object."""
    try:
        if not date_str: return None
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except:
        return None

def get_category_stats(course_assignments: List[dict], target_category: str) -> Tuple[float, float]:
    """Calculate (average, total_points_so_far) for a category."""
    total_earned = 0
    total_possible = 0
    percents = []
    
    # Normalize category names for matching
    cat_map = {
        'HW': 'Homework',
        'CW': 'Classwork',
        'Quiz': 'Quizzes',
        'Test': 'Tests',
        'IA': 'Interim Assessment'
    }
    target = cat_map.get(target_category, target_category)
    
    for a in course_assignments:
        cat = cat_map.get(a.get('category'), a.get('category'))
        if cat == target:
            earned = a.get('earned')
            possible = a.get('possible')
            percent = a.get('percent')
            
            if earned is not None and possible is not None and possible > 0:
                total_earned += earned
                total_possible += possible
                if percent is not None:
                    percents.append(percent)
                    
    avg = sum(percents) / len(percents) if percents else 85.0 # Default if no grades
    return avg, total_possible

# ==========================================
# CORE MATH
# ==========================================

def calculate_priority(
    category: str,
    points_possible: float,
    current_class_grade: float,
    category_points_so_far: float,
    due_date: date
) -> Dict:
    """
    Implements: P = clamp(Wc * I * R * U * (1 + V), 0, 1)
    """
    today = date.today()
    
    # 1. Category Weight (Wc)
    wc = CATEGORY_WEIGHTS.get(category, 0.20)
    
    # 2. Assignment Impact (I)
    # I = points_possible / (category_points_so_far + points_possible)
    i_val = points_possible / (category_points_so_far + points_possible) if (category_points_so_far + points_possible) > 0 else 1.0
    
    # 3. Grade Risk (R)
    # R = max(0, (target - current) / 100)
    r_val = max(0, (TARGET_GRADE - current_class_grade) / 100)
    
    # 4. Urgency (U)
    # U = 1 - days_until_due / lookahead
    days_left = (due_date - today).days if due_date else LOOKAHEAD_DAYS
    u_val = max(0, min(1, 1 - (days_left / LOOKAHEAD_DAYS)))
    
    # 5. Volatility (V)
    v_val = VOLATILITY_BOOST.get(category, 0.0)
    
    # Final Priority
    p_raw = wc * i_val * r_val * u_val * (1 + v_val)
    p_clamped = max(0, min(1, p_raw))
    
    return {
        'priority': p_clamped,
        'metrics': {
            'wc': wc,
            'impact': i_val,
            'risk': r_val,
            'urgency': u_val,
            'volatility': v_val,
            'days_left': days_left
        }
    }

def get_tier(priority: float, all_priorities: List[float]) -> str:
    if not all_priorities: return 'D'
    
    # Simple percentile-like logic if we have enough samples, 
    # but for a dynamic list we use fixed-ish thresholds derived from your mock
    if priority >= 0.025: return '🔥 S'
    if priority >= 0.015: return '⚠️ A'
    if priority >= 0.008: return '📌 B'
    if priority >= 0.003: return '🧱 C'
    return '💤 D'

MAKEUP_POLICY_PATH = 'output/makeup_policy.json'


# ==========================================
# MAKEUP POLICY (dismiss system)
# ==========================================

def load_policy() -> dict:
    if os.path.exists(MAKEUP_POLICY_PATH):
        with open(MAKEUP_POLICY_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'dismissed_exact': [], 'dismissed_keywords': []}


def save_policy(policy: dict):
    with open(MAKEUP_POLICY_PATH, 'w', encoding='utf-8') as f:
        json.dump(policy, f, indent=2, ensure_ascii=False)


def is_dismissed(policy: dict, course: str, title: str) -> bool:
    for entry in policy.get('dismissed_exact', []):
        if entry['course'] == course and entry['title'] == title:
            return True
    for entry in policy.get('dismissed_keywords', []):
        if entry['course'] == course and entry['keyword'].lower() in title.lower():
            return True
    return False


def run_dismiss_prompt(policy: dict, makeup_items: list) -> dict:
    """After report, let user dismiss MAKEUP items by exact title or keyword."""
    if not makeup_items:
        return policy

    while True:
        # Filter out already-dismissed items so the list shrinks as you go
        remaining = [r for r in makeup_items if not is_dismissed(policy, r['course'], r['title'])]
        if not remaining:
            print("\n  ✅ All MAKEUP items have been dismissed.")
            break

        print("\n" + "─" * 60)
        print("MAKEUP ITEMS — mark any that can't be made up:")
        for i, r in enumerate(remaining, 1):
            print(f"  [{i}] {r['course'][:30]} — {r['title']}")

        raw = input("\nEnter numbers to dismiss (or press Enter to finish): ").strip()
        if not raw:
            break

        chosen = []
        for tok in raw.split():
            try:
                idx = int(tok) - 1
                if 0 <= idx < len(remaining):
                    chosen.append(remaining[idx])
            except ValueError:
                pass

        for item in chosen:
            print(f"\n  \"{item['title']}\"")
            print("  (1) Just this assignment")
            print("  (2) Add a keyword — block all future assignments matching it in this course")
            choice = input("  > ").strip()

            if choice == '2':
                kw = input("  Keyword (the repeating part of the title): ").strip()
                if kw:
                    policy['dismissed_keywords'].append({'course': item['course'], 'keyword': kw})
                    print(f"  ✅ Saved — all future assignments containing \"{kw}\" in {item['course']} will be filtered")
            else:
                policy['dismissed_exact'].append({'course': item['course'], 'title': item['title']})
                print(f"  ✅ Dismissed just this one")

        save_policy(policy)

    return policy


# ==========================================
# QUARTER DETECTION
# ==========================================

def detect_current_quarter(courses: list) -> str:
    """Find the highest-numbered quarter that has assignment data."""
    for q in ('Q4', 'Q3', 'Q2', 'Q1'):
        for c in courses:
            if c.get('assignments', {}).get(q):
                return q
    return 'Q4'


def _is_late(flags: list) -> bool:
    late_labels = {'late', 'l', 'ln'}
    return any(f.lower().strip() in late_labels for f in (flags or []))


# ==========================================
# MAIN
# ==========================================

def main():
    grades_data = load_json('grades.json')
    upcoming_data = load_json('output/assignments.json')

    if not grades_data:
        print("❌ Missing grades.json — run the PowerSchool scraper first.")
        return

    policy = load_policy()
    courses = grades_data.get('grades', [])
    current_q = detect_current_quarter(courses)
    upcoming_list = (upcoming_data or {}).get('assignments', [])

    results = []

    # ── SOURCE 1: Upcoming assignments (Google Classroom) ──────────────────
    for item in upcoming_list:
        course_name = item.get('course_name', '')
        title       = item.get('assignment_title', '')
        category    = item.get('category') or 'Homework'
        points      = item.get('possible_points') or 100
        due_date    = parse_date(item.get('due_date'))

        matching_course = next(
            (c for c in courses if c.get('course', '') in course_name or course_name in c.get('course', '')),
            None
        )
        if not matching_course:
            continue

        q_assignments = matching_course.get('assignments', {}).get(current_q, [])
        _, cat_points = get_category_stats(q_assignments, category)

        grade_info    = matching_course.get('grades', {}).get(current_q, {})
        current_grade = grade_info.get('numeric') or 85.0

        calc = calculate_priority(category, points, current_grade, cat_points, due_date)
        results.append({
            'source':    'UPCOMING',
            'title':     title,
            'course':    matching_course['course'],
            'priority':  calc['priority'],
            'tier':      '',
            'days_left': calc['metrics']['days_left'],
            'category':  category,
        })

    # ── SOURCE 2: Recoverable assignments (grades.json zeros + low scores) ─
    for course in courses:
        grade_info    = course.get('grades', {}).get(current_q, {})
        current_grade = grade_info.get('numeric') or 85.0
        q_assignments = course.get('assignments', {}).get(current_q, [])

        for a in q_assignments:
            earned   = a.get('earned')
            possible = a.get('possible') or 0
            flags    = a.get('flags', [])

            if earned is None or possible <= 0:
                continue

            is_missing      = earned == 0.0
            is_improvable   = earned < possible and not _is_late(flags)

            if not (is_missing or is_improvable):
                continue

            category      = a.get('category') or 'Homework'
            recoverable_pts = possible - earned  # points we can still gain
            _, cat_points = get_category_stats(q_assignments, category)

            # Urgency is max for past-due recoverable items — they're hurting the grade now
            calc = calculate_priority(category, recoverable_pts, current_grade, cat_points, due_date=date.today())

            source     = 'MISSING' if is_missing else 'MAKEUP'
            title      = a.get('name', 'Unknown')
            course_name = course['course']

            if source == 'MAKEUP' and is_dismissed(policy, course_name, title):
                continue

            results.append({
                'source':    source,
                'title':     title,
                'course':    course_name,
                'priority':  calc['priority'],
                'tier':      '',
                'days_left': 0,
                'category':  category,
            })

    if not results:
        print("⚠️  No assignments to rank.")
        return

    results.sort(key=lambda x: x['priority'], reverse=True)

    all_p = [r['priority'] for r in results]
    for r in results:
        r['tier'] = get_tier(r['priority'], all_p)

    print("=" * 90)
    print(f"🚀 ASSIGNMENT PRIORITY LIST  |  Quarter: {current_q}  |  Target: {TARGET_GRADE}%  |  {date.today()}")
    print("=" * 90)
    print(f"{'TIER':<8} | {'SRC':<8} | {'DAYS':<5} | {'COURSE':<28} | {'ASSIGNMENT'}")
    print("-" * 90)

    for r in results:
        days_str = 'NOW' if r['source'] in ('MISSING', 'MAKEUP') else str(r['days_left'])
        print(f"{r['tier']:<8} | {r['source']:<8} | {days_str:<5} | {r['course'][:28]:<28} | {r['title']}")

    print("-" * 90)
    print("SRC: UPCOMING=not yet due  MISSING=0 in gradebook  MAKEUP=low score, no late flag")
    print("TIER: S=Critical  A=High  B=Medium  C=Low  D=Minimal")
    print("=" * 90)

    makeup_items = [r for r in results if r['source'] == 'MAKEUP']
    run_dismiss_prompt(policy, makeup_items)


if __name__ == "__main__":
    main()
