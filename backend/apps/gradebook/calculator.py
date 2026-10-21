"""
GRADE CALCULATOR
Comprehensive grade analysis, GPA calculation, and assignment impact prediction.

Uses:
- grades.json: PowerSchool grades with assignment details
- output/assignments.json: Upcoming assignments from Google Classroom
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from core.profile import (
    get_current_term,
    load_profile,
    normalize_category_name,
    source_periods_for_term,
)

# Fix Windows console encoding for Unicode characters
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')


def percent_to_letter(percent: float, profile: dict | None = None) -> str:
    """Convert percentage to letter grade."""
    if percent is None:
        return None
    scale = (profile or load_profile())["grading"]["scale"]
    for letter, threshold in sorted(scale.items(), key=lambda item: item[1], reverse=True):
        if percent >= threshold:
            return letter
    return min(scale.items(), key=lambda item: item[1])[0]


def school_round(value: float, profile: dict | None = None) -> int:
    """
    Apply school's rounding rules to whole numbers.
    If the decimal part is >= 0.3, round up to the next whole number.
    
    Examples:
        94.32 → 95 (0.32 >= 0.3, rounds up)
        94.29 → 94 (0.29 < 0.3, stays)
        88.5 → 89 (0.5 >= 0.3, rounds up)
        100.0 → 100 (no decimal, stays)
    """
    rounding_threshold = (profile or load_profile())["grading"]["rounding"]["round_up_at"]
    decimal_part = value - int(value)

    if decimal_part >= rounding_threshold:
        return int(value) + 1
    else:
        return int(value)


# ==========================================
# DATA LOADING
# ==========================================

def load_grades(path: str = 'grades.json') -> dict:
    """Load grades from PowerSchool JSON."""
    if not os.path.exists(path):
        print(f"⚠️ {path} not found")
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def detect_current_term(profile: dict | None = None) -> str:
    """Return the active configured term name based on today's date."""
    return get_current_term(profile)["name"]


def term_grade_info(course: dict, term: str, profile: dict | None = None) -> dict:
    """Get the latest grade available from the source periods mapped to a term."""
    profile = profile or load_profile()
    grades = course.get("grades", {})
    for period in reversed(source_periods_for_term(term, profile)):
        if grades.get(period):
            return grades[period]
    return {}


def term_assignments(course: dict, term: str, profile: dict | None = None) -> list:
    """Combine assignment rows from all source periods mapped to a term."""
    profile = profile or load_profile()
    assignments = course.get("assignments", {})
    return [
        assignment
        for period in source_periods_for_term(term, profile)
        for assignment in assignments.get(period, [])
    ]


def _course_profile(course_name: str | None, profile: dict) -> dict:
    return profile.get("courses", {}).get(course_name, {}) if course_name else {}


def _category_setting(category: str, profile: dict, course_name: str | None = None) -> dict:
    category = normalize_category_name(category)
    course_settings = _course_profile(course_name, profile)
    weights = {
        normalize_category_name(name): weight
        for name, weight in course_settings.get("category_weights", {}).items()
    }
    categories = profile.get("categories", {})
    setting = categories.get(category, {})
    return {
        "weight": weights.get(category, setting.get("weight", 0.2)),
        "floor": setting.get("floor", 0.0),
    }


def load_upcoming_assignments(path: str = 'output/assignments.json') -> list:
    """Load upcoming assignments from Classroom scraper."""
    if not os.path.exists(path):
        print(f"⚠️ {path} not found")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get('assignments', [])


# ==========================================
# GPA CALCULATIONS
# ==========================================

def calculate_gpa(
    grades_data: dict,
    term: str | None = None,
    weighted: bool = True,
    profile: dict | None = None,
) -> Tuple[float, List[dict]]:
    """
    Calculate GPA from grades data.
    
    Args:
        grades_data: The full grades.json structure
        term: Configured term name; defaults to the date-based current term.
        weighted: If True, add +1.0 for AP/Honors classes
    
    Returns:
        Tuple of (gpa, breakdown_list)
    """
    profile = profile or load_profile()
    term = term or detect_current_term(profile)
    grading = profile["grading"]
    gpa_points = grading["gpa_points"]
    weighted_bonus = grading.get("weighted_bonus", 0.0)
    total_points = 0.0
    total_credits = 0.0
    breakdown = []
    
    for course in grades_data.get('grades', []):
        course_name = course.get('course', 'Unknown')
        course_type = course.get('type', 'Regular')  # AP, Honors, Regular
        course_settings = _course_profile(course_name, profile)
        is_weighted_class = course_settings.get(
            "weighted", course_type in ["AP", "Honors", "IB"]
        )
        grade_info = term_grade_info(course, term, profile)
        if not grade_info:
            continue
        
        letter = grade_info.get('letter')
        numeric = grade_info.get('numeric')
        
        if not letter or letter not in gpa_points:
            continue
        
        base_points = gpa_points[letter]
        if weighted and is_weighted_class:
            points = base_points + weighted_bonus
        else:
            points = base_points
        credits = float(course_settings.get("credits", 1.0))
        if credits <= 0:
            raise ValueError(f"Course {course_name!r} must have a positive credit value.")
        weighted_points = points * credits
        
        breakdown.append({
            'course': course_name,
            'type': course_type,
            'letter': letter,
            'numeric': numeric,
            'points': points,
            'credits': credits,
            'weighted_points': weighted_points,
            'is_weighted': is_weighted_class
        })
        
        total_points += weighted_points
        total_credits += credits
    
    adjustment = float(grading.get("gpa_adjustment", 0.0))
    gpa = round((total_points / total_credits) + adjustment, 2) if total_credits else 0.0
    return gpa, breakdown


# ==========================================
# CATEGORY AVERAGES
# ==========================================

def calculate_category_averages(assignments: list) -> Dict[str, dict]:
    """
    Calculate averages by category (Homework, Classwork, Quizzes, Tests).
    
    Returns dict with structure:
    {
        'Homework': {'average': 95.0, 'count': 10, 'total_earned': 950, 'total_possible': 1000},
        ...
    }
    """
    categories = {}
    
    for assignment in assignments:
        category = assignment.get('category', 'Unknown')
        earned = assignment.get('earned')
        possible = assignment.get('possible')
        percent = assignment.get('percent')
        
        # Skip if no earned points (missing/incomplete)
        if earned is None or possible is None or possible == 0:
            continue
        
        if category not in categories:
            categories[category] = {
                'total_earned': 0,
                'total_possible': 0,
                'count': 0,
                'percentages': []
            }
        
        categories[category]['total_earned'] += earned
        categories[category]['total_possible'] += possible
        categories[category]['count'] += 1
        if percent is not None:
            categories[category]['percentages'].append(percent)
    
    # Calculate averages
    for cat, data in categories.items():
        if data['count'] > 0:
            data['average'] = round(sum(data['percentages']) / len(data['percentages']), 2)
        else:
            data['average'] = 0
    
    return categories


def calculate_class_grade_weighted(
    assignments: list,
    course_name: str | None = None,
    profile: dict | None = None,
) -> float:
    """
    Calculate weighted class grade using category weights.
    
    Formula: Sum(category_avg * category_weight)
    """
    profile = profile or load_profile()
    cat_avgs = calculate_category_averages(assignments)
    
    total_weighted = 0
    total_weight = 0
    
    for category, data in cat_avgs.items():
        configured = _category_setting(category, profile, course_name)
        course_weights = _course_profile(course_name, profile).get("category_weights", {})
        normalized_category = normalize_category_name(category)
        if (
            normalized_category not in profile.get("categories", {})
            and normalized_category not in {
                normalize_category_name(name) for name in course_weights
            }
        ):
            weight = 0.0
        else:
            weight = configured["weight"]
        if weight > 0 and data['count'] > 0:
            total_weighted += data['average'] * weight
            total_weight += weight
    
    if total_weight > 0:
        # Scale to 100 if not all categories have grades
        return round(
            total_weighted / total_weight if total_weight < 1 else total_weighted,
            2,
        )
    return 0


# ==========================================
# ASSIGNMENT IMPACT CALCULATIONS
# ==========================================

def calculate_assignment_impact(
    category: str,
    points_possible: float,
    score: float,
    category_avg: float,
    category_points_so_far: float,
    current_class_grade: float = None,
    course_name: str | None = None,
    profile: dict | None = None,
) -> dict:
    """
    Calculate the impact of a single assignment on grades.
    
    Args:
        category: Assignment category (Homework, Classwork, etc.)
        points_possible: Max points for this assignment
        score: The score you'd get (0-100 scale or actual points)
        category_avg: Current average in this category
        category_points_so_far: Total points counted in category so far
        current_class_grade: Current overall class grade (optional)
    
    Returns:
        Dict with impact metrics
    """
    profile = profile or load_profile()
    category_setting = _category_setting(category, profile, course_name)
    weight = category_setting["weight"]
    floor = category_setting["floor"]
    
    # Normalize score to percentage
    score_percent = (score / points_possible * 100) if points_possible > 0 else 0
    
    # Category impact: How much this assignment matters inside its category
    if category_points_so_far + points_possible > 0:
        importance = points_possible / (category_points_so_far + points_possible)
    else:
        importance = 1.0
    
    # Category grade change
    delta_category = importance * (score_percent - category_avg)
    
    # Class grade change
    delta_class = weight * delta_category
    
    # Best case (100%)
    delta_best = weight * importance * (100 - category_avg)
    
    # Worst case (floor or 0)
    delta_worst = weight * importance * (floor - category_avg)
    
    # New category average
    new_category_avg = category_avg + delta_category
    
    # New class grade (if provided)
    new_class_grade = None
    if current_class_grade is not None:
        new_class_grade = current_class_grade + delta_class
    
    return {
        'category': category,
        'weight': weight,
        'importance_in_category': round(importance * 100, 2),  # As percentage
        'score_percent': round(score_percent, 2),
        'delta_category': round(delta_category, 2),
        'delta_class': round(delta_class, 2),
        'delta_best': round(delta_best, 2),
        'delta_worst': round(delta_worst, 2),
        'new_category_avg': round(new_category_avg, 2),
        'new_class_grade': round(new_class_grade, 2) if new_class_grade else None,
        'risk_span': round(abs(delta_best - delta_worst), 2)
    }


def calculate_gpa_impact(
    current_gpa: float,
    num_classes: int,
    current_class_percent: float,
    new_class_percent: float,
    is_ap: bool = False,
    weighted: bool = True,
    profile: dict | None = None,
    course_credits: float = 1.0,
    total_credits: float | None = None,
) -> dict:
    """
    Calculate how a class grade change affects overall GPA.
    
    Args:
        current_gpa: Current GPA
        num_classes: Number of classes
        current_class_percent: Current percentage in the class
        new_class_percent: New percentage after assignment
        is_ap: Whether this is an AP/Honors class
        weighted: Whether to use weighted GPA
    """
    profile = profile or load_profile()
    current_letter = percent_to_letter(current_class_percent, profile)
    new_letter = percent_to_letter(new_class_percent, profile)
    
    gpa_points = profile["grading"]["gpa_points"]
    current_points = gpa_points.get(current_letter, 0)
    new_points = gpa_points.get(new_letter, 0)
    
    if weighted and is_ap:
        bonus = profile["grading"].get("weighted_bonus", 0.0)
        current_points += bonus
        new_points += bonus
    
    denominator = total_credits if total_credits is not None else float(num_classes)
    if course_credits <= 0 or denominator < course_credits:
        raise ValueError("Course credits must be positive and no greater than total credits.")
    old_total = current_gpa * denominator
    new_total = old_total - current_points * course_credits + new_points * course_credits
    new_gpa = new_total / denominator if denominator > 0 else 0
    
    return {
        'current_letter': current_letter,
        'new_letter': new_letter,
        'letter_changed': current_letter != new_letter,
        'current_gpa': round(current_gpa, 2),
        'new_gpa': round(new_gpa, 2),
        'gpa_change': round(new_gpa - current_gpa, 3)
    }


# ==========================================
# CROSS-CLASS AVERAGES
# ==========================================

def calculate_all_class_averages(
    grades_data: dict,
    term: str | None = None,
    profile: dict | None = None,
) -> dict:
    """
    Calculate averages across ALL classes by category.
    
    Returns:
    {
        'Homework': {'average': 92.5, 'count': 45},
        'Classwork': {'average': 88.0, 'count': 32},
        ...
    }
    """
    profile = profile or load_profile()
    term = term or detect_current_term(profile)
    all_categories = {}
    
    for course in grades_data.get('grades', []):
        assignments = term_assignments(course, term, profile)
        
        for assignment in assignments:
            category = assignment.get('category', 'Unknown')
            percent = assignment.get('percent')
            
            if percent is None:
                continue
            
            if category not in all_categories:
                all_categories[category] = {'percentages': [], 'count': 0}
            
            all_categories[category]['percentages'].append(percent)
            all_categories[category]['count'] += 1
    
    # Calculate averages
    result = {}
    for cat, data in all_categories.items():
        if data['count'] > 0:
            result[cat] = {
                'average': round(sum(data['percentages']) / len(data['percentages']), 2),
                'count': data['count']
            }
    
    return result


# ==========================================
# UPCOMING ASSIGNMENT ANALYSIS
# ==========================================

def analyze_upcoming_assignment(
    upcoming: dict,
    grades_data: dict,
    term: str | None = None,
    profile: dict | None = None,
) -> dict:
    """
    Analyze an upcoming assignment from Classroom scraper.
    
    Args:
        upcoming: Assignment from output/assignments.json
        grades_data: Full grades.json data
        term: Current configured term
    
    Returns detailed impact analysis.
    """
    profile = profile or load_profile()
    term = term or detect_current_term(profile)
    course_name = upcoming.get('course_name', '')
    category = upcoming.get('category', 'Homework')
    possible_points = upcoming.get('possible_points') or 100
    
    # Normalize category
    category_map = {
        'HW': 'Homework',
        'Homework': 'Homework',
        'Classwork': 'Classwork',
        'CW': 'Classwork',
        'Quiz': 'Quizzes',
        'Quizzes': 'Quizzes',
        'Test': 'Tests',
        'Tests': 'Tests',
        'IA': 'Interim Assessment',
    }
    normalized_category = category_map.get(category, 'Homework')
    
    # Find matching course in grades
    matching_course = None
    for course in grades_data.get('grades', []):
        if course_name.startswith(course.get('course', '')):
            matching_course = course
            break
        # Fuzzy match
        if course.get('course', '') in course_name or course_name in course.get('course', ''):
            matching_course = course
            break
    
    if not matching_course:
        return {
            'error': f'Could not find matching course for: {course_name}',
            'course_name': course_name
        }
    
    # Get assignments for this course
    assignments = term_assignments(matching_course, term, profile)
    
    # Calculate category stats
    cat_stats = calculate_category_averages(assignments)
    cat_data = cat_stats.get(normalized_category, {'average': 85, 'total_possible': 0, 'count': 0})
    
    # Get current class grade
    grade_info = term_grade_info(matching_course, term, profile)
    current_class_grade = grade_info.get('numeric', 85)
    
    # Calculate impacts for different scenarios
    scenarios = {}
    for score_name, score in [('100%', 100), ('90%', 90), ('80%', 80), ('70%', 70), ('0', 0)]:
        impact = calculate_assignment_impact(
            category=normalized_category,
            points_possible=possible_points,
            score=score * possible_points / 100,  # Convert to points
            category_avg=cat_data.get('average', 85),
            category_points_so_far=cat_data.get('total_possible', 0),
            current_class_grade=current_class_grade,
            course_name=matching_course.get("course"),
            profile=profile,
        )
        scenarios[score_name] = impact
    
    # Determine if AP class
    is_ap = _course_profile(matching_course.get("course"), profile).get(
        "weighted", matching_course.get('type', 'Regular') in ['AP', 'Honors', 'IB']
    )
    
    return {
        'assignment_title': upcoming.get('assignment_title'),
        'course_name': matching_course.get('course'),
        'course_type': matching_course.get('type'),
        'is_ap': is_ap,
        'category': normalized_category,
        'possible_points': possible_points,
        'due_date': upcoming.get('due_date'),
        'current_category_avg': cat_data.get('average'),
        'current_class_grade': current_class_grade,
        'scenarios': scenarios
    }


# ==========================================
# MISSING ASSIGNMENT REPORT
# ==========================================

def generate_missing_report(
    missing_path: str = 'output/missing.json',
    grades_path: str = 'grades.json'
):
    """
    Load missing assignments and show their grade impact per course.
    Cross-references with grades.json to estimate the hit.
    """
    if not os.path.exists(missing_path):
        print("⚠️  No missing.json found — run the classroom scraper first.")
        return

    with open(missing_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    missing = data.get('missing', [])
    grades_data = load_grades(grades_path)
    profile = load_profile()
    term = detect_current_term(profile)

    print("=" * 70)
    print("🚨 MISSING ASSIGNMENTS REPORT")
    print(f"   Scraped: {data.get('scraped_at', 'unknown')}  |  Current term: {term}")
    print("=" * 70)

    if not missing:
        print("\n✅ No missing assignments found.")
        print("=" * 70)
        return

    # Build a lookup of every graded assignment name (earned != null) from grades.json
    # so we can detect "missing on Classroom but already graded in PowerSchool"
    graded_names: set = set()
    if grades_data:
        for c in grades_data.get('grades', []):
            for q_assignments in c.get('assignments', {}).values():
                for a in q_assignments:
                    if a.get('earned') is not None:
                        graded_names.add(a.get('name', '').lower().strip())

    def _is_already_graded(title: str) -> bool:
        """Fuzzy match: Classroom title vs PowerSchool assignment names."""
        t = title.lower().strip()
        if t in graded_names:
            return True
        # Partial match: if the classroom title is a substring of a PS name or vice versa
        for g in graded_names:
            if t and (t in g or g in t):
                return True
        return False

    # Group by course, tagging each item as truly missing or already graded
    by_course: dict = {}
    for item in missing:
        course_key = item.get('course') or 'Unknown'
        item['_already_graded'] = _is_already_graded(item.get('title', ''))
        by_course.setdefault(course_key, []).append(item)

    truly_missing_total = sum(
        1 for items in by_course.values()
        for i in items if not i['_already_graded']
    )

    for course_label, items in sorted(by_course.items()):
        truly_missing = [i for i in items if not i['_already_graded']]
        already_graded = [i for i in items if i['_already_graded']]

        if not truly_missing and not already_graded:
            continue

        print(f"\n📘 {course_label}")

        # Already graded — just flagged as not turned in on Classroom
        for item in already_graded:
            print(f"   ✅ {item.get('title')}  (graded in PowerSchool — just not marked turned in)")

        if not truly_missing:
            continue

        print(f"   ⚠️  {len(truly_missing)} genuinely missing:")

        # Match to grades.json course for impact calculation
        matched = None
        if grades_data:
            for c in grades_data.get('grades', []):
                c_name = c.get('course', '').lower()
                if c_name in course_label.lower() or course_label.lower() in c_name:
                    matched = c
                    break

        for item in truly_missing:
            title = item.get('title', 'Unknown')
            due   = item.get('due') or 'No due date'
            print(f"   • {title}")
            print(f"     Due: {due}")

        if matched:
            grade_info = term_grade_info(matched, term, profile)
            current_num = grade_info.get('numeric')
            current_let = grade_info.get('letter', 'N/A')

            assignments = term_assignments(matched, term, profile)
            cat_avgs    = calculate_category_averages(assignments)
            hw_data     = cat_avgs.get('Homework', {'average': 85, 'total_possible': 0})

            count = len(truly_missing)
            impact_per = calculate_assignment_impact(
                category='Homework',
                points_possible=100,
                score=0,
                category_avg=hw_data.get('average', 85),
                category_points_so_far=hw_data.get('total_possible', 0),
                current_class_grade=current_num,
                course_name=matched.get("course"),
                profile=profile,
            )
            total_hit   = round(impact_per['delta_class'] * count, 2)
            projected   = round((current_num or 0) + total_hit, 1) if current_num else None

            print(f"\n   Current {term} grade: {current_let} ({current_num}%)")
            print(f"   Estimated hit if all stay 0: {total_hit:+.2f}%", end='')
            if projected is not None:
                proj_let = percent_to_letter(school_round(projected))
                print(f"  →  projected {projected}% ({proj_let})", end='')
            print()
        else:
            print("   (no matching course in grades.json for impact calculation)")

    print("\n" + "=" * 70)
    flagged = len(missing) - truly_missing_total
    print(f"⚠️  Genuinely missing: {truly_missing_total}  |  Already graded (not turned in): {flagged}")
    print("=" * 70)


def generate_trend_report(grades_path: str = 'grades.json'):
    """Show grade progression across configured terms with available data."""
    grades_data = load_grades(grades_path)
    if not grades_data:
        print("❌ No grades data found")
        return
    profile = load_profile()

    available = [
        term["name"]
        for term in profile["terms"]
        if any(term_grade_info(course, term["name"], profile)
               for course in grades_data.get("grades", []))
    ]

    print("=" * 70)
    print("📈 GRADE PROGRESSION REPORT")
    print(f"   Terms with data: {' → '.join(available)}")
    print("=" * 70)

    # ── GPA trend ──────────────────────────────────────────────────────
    print("\n─" * 35)
    print("🎓 WEIGHTED GPA BY TERM")
    print("─" * 70)
    gpas = {}
    for term in available:
        gpa, _ = calculate_gpa(grades_data, term=term, weighted=True, profile=profile)
        gpas[term] = gpa
        arrow = ''
        if len(gpas) > 1:
            prev = list(gpas.values())[-2]
            diff = gpa - prev
            arrow = f"  ({'↑' if diff > 0 else '↓' if diff < 0 else '→'}{abs(diff):+.2f})"
        print(f"   {term}: {gpa}{arrow}")

    # ── Per-class progression ───────────────────────────────────────────
    print("\n─" * 35)
    print("📖 PER-CLASS GRADE PROGRESSION")
    print("─" * 70)

    header = "   {:<38}".format("Course") + "  ".join(f"{term:>14}" for term in available)
    print(header)
    print("   " + "─" * (38 + 16 * len(available)))

    for course in grades_data.get('grades', []):
        name = course.get('course', 'Unknown')
        ctype = course.get('type', 'Regular')
        tag = ' (AP)' if ctype == 'AP' else ''
        label = (name + tag)[:38].ljust(38)
        row = f"   {label}"
        prev_num = None
        for term in available:
            info = term_grade_info(course, term, profile)
            if info:
                num = info.get('numeric', 0)
                arrow = ''
                if prev_num is not None:
                    diff = num - prev_num
                    arrow = '↑' if diff > 0 else ('↓' if diff < 0 else '→')
                row += f"  {num:>10.1f}{arrow:<1}"
                prev_num = num
            else:
                row += "      --      "
        print(row)

    # ── Category trend across all configured terms ─────────────────────
    print("\n─" * 35)
    print("📚 CATEGORY AVERAGES BY TERM (assignments scraped)")
    print("─" * 70)

    cat_by_term = {}
    for term in available:
        avgs = calculate_all_class_averages(grades_data, term, profile)
        if avgs:
            cat_by_term[term] = avgs

    all_cats = sorted({c for avgs in cat_by_term.values() for c in avgs})
    if all_cats:
        header2 = "   {:<22}".format("Category") + "  ".join(
            f"{term:>14}" for term in cat_by_term
        )
        print(header2)
        print("   " + "─" * (22 + 16 * len(cat_by_term)))
        for cat in all_cats:
            row = f"   {cat:<22}"
            for term, avgs in cat_by_term.items():
                if cat in avgs:
                    row += f"  {avgs[cat]['average']:>12.1f}%"
                else:
                    row += "        --      "
            print(row)
    else:
        print("   (No assignment data scraped for configured terms.)")

    print("\n" + "=" * 70)
    print("✅ Trend Report Complete")
    print("=" * 70)


# ==========================================
# MAIN REPORT GENERATOR
# ==========================================

def generate_full_report(
    grades_path: str = 'grades.json',
    upcoming_path: str = 'output/assignments.json',
    term: str | None = None,
):
    """
    Generate a comprehensive grade report.
    """
    print("=" * 70)
    print("📊 GRADE CALCULATOR - FULL REPORT")
    print("=" * 70)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Load data
    grades_data = load_grades(grades_path)
    upcoming = load_upcoming_assignments(upcoming_path)
    profile = load_profile()

    if not grades_data:
        print("❌ No grades data found")
        return

    term_was_specified = term is not None
    term = term or detect_current_term(profile)
    if term not in {configured["name"] for configured in profile["terms"]}:
        raise ValueError(f"Unknown configured term: {term!r}.")
    print(f"Term: {term}" + (" (specified)" if term_was_specified else " (date-based current term)"))

    # =========================================
    # 1. GPA CALCULATIONS
    # =========================================
    print("─" * 70)
    print("📈 GPA CALCULATIONS")
    print("─" * 70)

    # Weighted GPA
    weighted_gpa, weighted_breakdown = calculate_gpa(
        grades_data, term=term, weighted=True, profile=profile
    )
    print(f"\n🎓 Weighted GPA: {weighted_gpa}")
    print("   Breakdown:")
    for course in weighted_breakdown:
        weighted_marker = (
            f" (weighted; {course['credits']} credits)"
            if course['is_weighted'] else f" ({course['credits']} credits)"
        )
        print(f"      • {course['course']}: {course['letter']} ({course['numeric']}%) → {course['points']} pts{weighted_marker}")

    # Unweighted GPA
    unweighted_gpa, _ = calculate_gpa(
        grades_data, term=term, weighted=False, profile=profile
    )
    print(f"\n🎓 Unweighted GPA: {unweighted_gpa}")

    # =========================================
    # 2. CATEGORY AVERAGES ACROSS ALL CLASSES
    # =========================================
    print("\n" + "─" * 70)
    print(f"📚 CATEGORY AVERAGES ({term} — All Classes)")
    print("─" * 70)

    all_avgs = calculate_all_class_averages(grades_data, term, profile)
    for cat, data in sorted(all_avgs.items()):
        raw_avg = data['average']
        rounded_avg = school_round(raw_avg, profile)
        letter = percent_to_letter(rounded_avg, profile)
        print(f"   {cat}: {raw_avg}% → {rounded_avg}% ({letter}) - {data['count']} assignments")

    # =========================================
    # 3. PER-CLASS BREAKDOWN
    # =========================================
    print("\n" + "─" * 70)
    print(f"📖 PER-CLASS BREAKDOWN — {term} (with School Rounding)")
    print("─" * 70)

    for course in grades_data.get('grades', []):
        course_name = course.get('course', 'Unknown')
        course_type = course.get('type', 'Regular')
        assignments = term_assignments(course, term, profile)

        grade_info = term_grade_info(course, term, profile)
        current_grade = grade_info.get('numeric', 0)
        current_letter = grade_info.get('letter', 'N/A')
        rounded_grade = school_round(current_grade, profile)
        rounded_letter = percent_to_letter(rounded_grade, profile)

        print(f"\n   📘 {course_name} ({course_type})")
        print(f"      Current Grade: {current_letter} ({current_grade}%) → Rounded: {rounded_grade}% ({rounded_letter})")

        cat_avgs = calculate_category_averages(assignments)
        if cat_avgs:
            for cat, data in sorted(cat_avgs.items()):
                weight = _category_setting(cat, profile, course_name)["weight"] * 100
                raw = data['average']
                rounded = school_round(raw, profile)
                print(f"         {cat} ({weight:.0f}%): {raw}% → {rounded}% ({data['count']} assignments)")
        else:
            print(f"         (no {term} assignment data)")
    
    # =========================================
    # 4. UPCOMING ASSIGNMENTS IMPACT
    # =========================================
    if upcoming:
        print("\n" + "─" * 70)
        print("🔮 UPCOMING ASSIGNMENTS IMPACT ANALYSIS")
        print("─" * 70)
        
        for assignment in upcoming:
            analysis = analyze_upcoming_assignment(assignment, grades_data, term, profile)
            
            if 'error' in analysis:
                print(f"\n   ⚠️ {analysis.get('course_name')}: {analysis.get('error')}")
                continue
            
            print(f"\n   📝 {analysis['assignment_title']}")
            print(f"      Course: {analysis['course_name']} ({analysis['course_type']})")
            print(f"      Category: {analysis['category']} | Points: {analysis['possible_points']}")
            print(f"      Due: {analysis['due_date']}")
            print(f"      Current Category Avg: {analysis['current_category_avg']}%")
            print(f"      Current Class Grade: {analysis['current_class_grade']}%")
            print()
            print("      📊 Scenario Analysis:")
            
            for score_name, impact in analysis['scenarios'].items():
                new_grade = impact.get('new_class_grade', analysis['current_class_grade'])
                change = impact['delta_class']
                arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
                print(f"         If you get {score_name}: Class grade {arrow} {change:+.2f}% → {new_grade}%")
            
            print(f"\n      💡 Risk Span: {analysis['scenarios']['100%']['risk_span']}% (best - worst impact)")
    
    print("\n" + "=" * 70)
    print("✅ Report Complete")
    print("=" * 70)


# ==========================================
# INTERACTIVE CALCULATOR
# ==========================================

def interactive_calculator():
    """
    Interactive mode for calculating assignment impacts.
    """
    grades_data = load_grades()
    
    if not grades_data:
        print("❌ No grades data. Run the scraper first.")
        return
    
    print("\n" + "=" * 50)
    print("🧮 INTERACTIVE GRADE CALCULATOR")
    print("=" * 50)
    print("\nThis tool lets you predict how assignments will affect your grade.\n")
    
    # List courses
    courses = grades_data.get('grades', [])
    profile = load_profile()
    current_term = detect_current_term(profile)
    print("Your Courses:")
    for i, course in enumerate(courses):
        grade = term_grade_info(course, current_term, profile).get('numeric', '?')
        print(f"  {i+1}. {course['course']} - Currently: {grade}%")
    
    try:
        choice = int(input("\nSelect course number: ")) - 1
        selected_course = courses[choice]
    except (ValueError, IndexError):
        print("Invalid selection")
        return
    
    print(f"\n📘 {selected_course['course']}")
    
    # Get assignment details
    print("\nCategories: Homework, Classwork, Quizzes, Tests, Interim Assessment")
    category = input("Category: ").strip()
    
    try:
        possible_points = float(input("Possible points: "))
        score = float(input("Score you expect to get: "))
    except ValueError:
        print("Invalid number")
        return
    
    # Calculate
    assignments = term_assignments(selected_course, current_term, profile)
    cat_stats = calculate_category_averages(assignments)
    cat_data = cat_stats.get(category, {'average': 85, 'total_possible': 0})

    grade_info = term_grade_info(selected_course, current_term, profile)
    current_grade = grade_info.get('numeric', 85)
    
    impact = calculate_assignment_impact(
        category=category,
        points_possible=possible_points,
        score=score,
        category_avg=cat_data.get('average', 85),
        category_points_so_far=cat_data.get('total_possible', 0),
        current_class_grade=current_grade,
        course_name=selected_course["course"],
        profile=profile,
    )
    
    print("\n" + "─" * 50)
    print("📊 IMPACT ANALYSIS")
    print("─" * 50)
    print(f"   Score: {impact['score_percent']}%")
    print(f"   Category weight: {impact['weight']*100}%")
    print(f"   Importance in category: {impact['importance_in_category']}%")
    print(f"   Category change: {impact['delta_category']:+.2f}%")
    print(f"   Class grade change: {impact['delta_class']:+.2f}%")
    print(f"   New class grade: {impact['new_class_grade']}%")
    print(f"   Best possible: {impact['delta_best']:+.2f}%")
    print(f"   Worst possible: {impact['delta_worst']:+.2f}%")


# ==========================================
# MAIN
# ==========================================

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--interactive':
        interactive_calculator()
    else:
        generate_full_report()
