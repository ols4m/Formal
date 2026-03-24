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

# Fix Windows console encoding for Unicode characters
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')


# ==========================================
# CONFIGURATION - Grade Weights & GPA Scale
# ==========================================

# Category weights (must sum to 100%)
CATEGORY_WEIGHTS = {
    'Classwork': 0.35,
    'Homework': 0.20,
    'Quizzes': 0.15,
    'Tests': 0.15,
    'Interim Assessment': 0.15,
    # Aliases
    'Quiz': 0.15,
    'Test': 0.15,
    'IA': 0.15,
    'HW': 0.20,
}

# Grade floors by category (minimum score when missing)
CATEGORY_FLOORS = {
    'Homework': 0,
    'Classwork': 0,
    'Quizzes': 55,
    'Quiz': 55,
    'Tests': 55,
    'Test': 55,
    'Interim Assessment': 55,
    'IA': 55,
}

# GPA conversion tables
GPA_UNWEIGHTED = {
    'A+': 4.0, 'A': 4.0, 'A-': 3.7,
    'B+': 3.3, 'B': 3.0, 'B-': 2.7,
    'C+': 2.3, 'C': 2.0, 'C-': 1.7,
    'D+': 1.3, 'D': 1.0, 'D-': 0.7,
    'F': 0.0, 'F-': 0.0
}

GPA_WEIGHTED_BONUS = 1.0  # AP/Honors classes get +1.0 to GPA points
GPA_ADJUSTMENT = 0.1  # School-specific adjustment to match official GPA calculation

# Percentage to letter grade mapping
def percent_to_letter(percent: float) -> str:
    """Convert percentage to letter grade."""
    if percent is None:
        return None
    if percent >= 97: return 'A+'
    if percent >= 93: return 'A'
    if percent >= 90: return 'A-'
    if percent >= 87: return 'B+'
    if percent >= 83: return 'B'
    if percent >= 80: return 'B-'
    if percent >= 77: return 'C+'
    if percent >= 73: return 'C'
    if percent >= 70: return 'C-'
    if percent >= 67: return 'D+'
    if percent >= 63: return 'D'
    if percent >= 60: return 'D-'
    return 'F'


def school_round(value: float) -> int:
    """
    Apply school's rounding rules to whole numbers.
    If the decimal part is >= 0.3, round up to the next whole number.
    
    Examples:
        94.32 → 95 (0.32 >= 0.3, rounds up)
        94.29 → 94 (0.29 < 0.3, stays)
        88.5 → 89 (0.5 >= 0.3, rounds up)
        100.0 → 100 (no decimal, stays)
    """
    decimal_part = value - int(value)
    
    if decimal_part >= 0.3:
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

def calculate_gpa(grades_data: dict, quarter: str = 'Q2', weighted: bool = True) -> Tuple[float, List[dict]]:
    """
    Calculate GPA from grades data.
    
    Args:
        grades_data: The full grades.json structure
        quarter: Which quarter to use for grades (e.g., 'Q1', 'Q2', 'O2')
        weighted: If True, add +1.0 for AP/Honors classes
    
    Returns:
        Tuple of (gpa, breakdown_list)
    """
    breakdown = []
    total_points = 0
    class_count = 0
    
    for course in grades_data.get('grades', []):
        course_name = course.get('course', 'Unknown')
        course_type = course.get('type', 'Regular')  # AP, Honors, Regular
        is_weighted_class = course_type in ['AP', 'Honors', 'IB']
        
        # Get the grade for the specified quarter
        quarter_grades = course.get('grades', {})
        
        # Try to find the grade (check multiple quarter keys)
        grade_info = None
        for qkey in [quarter, 'O2', 'Q2', 'Q1', 'Y1']:
            if qkey in quarter_grades:
                grade_info = quarter_grades[qkey]
                break
        
        if not grade_info:
            continue
        
        letter = grade_info.get('letter')
        numeric = grade_info.get('numeric')
        
        if not letter or letter not in GPA_UNWEIGHTED:
            continue
        
        # Calculate points
        base_points = GPA_UNWEIGHTED.get(letter, 0)
        if weighted and is_weighted_class:
            points = base_points + GPA_WEIGHTED_BONUS
        else:
            points = base_points
        
        breakdown.append({
            'course': course_name,
            'type': course_type,
            'letter': letter,
            'numeric': numeric,
            'points': points,
            'is_ap': is_weighted_class
        })
        
        total_points += points
        class_count += 1
    
    gpa = round((total_points / class_count) + GPA_ADJUSTMENT, 2) if class_count > 0 else 0.0
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


def calculate_class_grade_weighted(assignments: list) -> float:
    """
    Calculate weighted class grade using category weights.
    
    Formula: Sum(category_avg * category_weight)
    """
    cat_avgs = calculate_category_averages(assignments)
    
    total_weighted = 0
    total_weight = 0
    
    for category, data in cat_avgs.items():
        weight = CATEGORY_WEIGHTS.get(category, 0)
        if weight > 0 and data['count'] > 0:
            total_weighted += data['average'] * weight
            total_weight += weight
    
    if total_weight > 0:
        # Scale to 100 if not all categories have grades
        return round(total_weighted / total_weight * (1 / total_weight) if total_weight < 1 else total_weighted, 2)
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
    current_class_grade: float = None
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
    weight = CATEGORY_WEIGHTS.get(category, 0.20)
    floor = CATEGORY_FLOORS.get(category, 0)
    
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
    weighted: bool = True
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
    # Convert percentages to letter grades
    current_letter = percent_to_letter(current_class_percent)
    new_letter = percent_to_letter(new_class_percent)
    
    # Get GPA points
    current_points = GPA_UNWEIGHTED.get(current_letter, 0)
    new_points = GPA_UNWEIGHTED.get(new_letter, 0)
    
    if weighted and is_ap:
        current_points += GPA_WEIGHTED_BONUS
        new_points += GPA_WEIGHTED_BONUS
    
    # Calculate GPA change
    # GPA = sum(points) / num_classes
    # If one class changes: new_GPA = (current_GPA * num_classes - old_points + new_points) / num_classes
    old_total = current_gpa * num_classes
    new_total = old_total - current_points + new_points
    new_gpa = new_total / num_classes if num_classes > 0 else 0
    
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

def calculate_all_class_averages(grades_data: dict, quarter: str = 'Q2') -> dict:
    """
    Calculate averages across ALL classes by category.
    
    Returns:
    {
        'Homework': {'average': 92.5, 'count': 45},
        'Classwork': {'average': 88.0, 'count': 32},
        ...
    }
    """
    all_categories = {}
    
    for course in grades_data.get('grades', []):
        assignments = course.get('assignments', {}).get(quarter, [])
        
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
    quarter: str = 'Q2'
) -> dict:
    """
    Analyze an upcoming assignment from Classroom scraper.
    
    Args:
        upcoming: Assignment from output/assignments.json
        grades_data: Full grades.json data
        quarter: Current quarter
    
    Returns detailed impact analysis.
    """
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
    assignments = matching_course.get('assignments', {}).get(quarter, [])
    
    # Calculate category stats
    cat_stats = calculate_category_averages(assignments)
    cat_data = cat_stats.get(normalized_category, {'average': 85, 'total_possible': 0, 'count': 0})
    
    # Get current class grade
    grade_info = matching_course.get('grades', {}).get('O2') or matching_course.get('grades', {}).get(quarter, {})
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
            current_class_grade=current_class_grade
        )
        scenarios[score_name] = impact
    
    # Determine if AP class
    is_ap = matching_course.get('type', 'Regular') in ['AP', 'Honors', 'IB']
    
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
# MAIN REPORT GENERATOR
# ==========================================

def generate_full_report(grades_path: str = 'grades.json', upcoming_path: str = 'output/assignments.json'):
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
    
    if not grades_data:
        print("❌ No grades data found")
        return
    
    # =========================================
    # 1. GPA CALCULATIONS
    # =========================================
    print("─" * 70)
    print("📈 GPA CALCULATIONS")
    print("─" * 70)
    
    # Weighted GPA
    weighted_gpa, weighted_breakdown = calculate_gpa(grades_data, quarter='Q2', weighted=True)
    print(f"\n🎓 Weighted GPA: {weighted_gpa}")
    print("   Breakdown:")
    for course in weighted_breakdown:
        ap_marker = " (AP +1.0)" if course['is_ap'] else ""
        print(f"      • {course['course']}: {course['letter']} ({course['numeric']}%) → {course['points']} pts{ap_marker}")
    
    # Unweighted GPA
    unweighted_gpa, unweighted_breakdown = calculate_gpa(grades_data, quarter='Q2', weighted=False)
    print(f"\n🎓 Unweighted GPA: {unweighted_gpa}")
    
    # =========================================
    # 2. CATEGORY AVERAGES ACROSS ALL CLASSES
    # =========================================
    print("\n" + "─" * 70)
    print("📚 CATEGORY AVERAGES (All Classes)")
    print("─" * 70)
    
    all_avgs = calculate_all_class_averages(grades_data, 'Q2')
    for cat, data in sorted(all_avgs.items()):
        raw_avg = data['average']
        rounded_avg = school_round(raw_avg)
        letter = percent_to_letter(rounded_avg)
        print(f"   {cat}: {raw_avg}% → {rounded_avg}% ({letter}) - {data['count']} assignments")
    
    # =========================================
    # 3. PER-CLASS BREAKDOWN
    # =========================================
    print("\n" + "─" * 70)
    print("📖 PER-CLASS BREAKDOWN (with School Rounding)")
    print("─" * 70)
    
    for course in grades_data.get('grades', []):
        course_name = course.get('course', 'Unknown')
        course_type = course.get('type', 'Regular')
        assignments = course.get('assignments', {}).get('Q2', [])
        
        grade_info = course.get('grades', {}).get('O2') or course.get('grades', {}).get('Q2', {})
        current_grade = grade_info.get('numeric', 0)
        current_letter = grade_info.get('letter', 'N/A')
        rounded_grade = school_round(current_grade)
        rounded_letter = percent_to_letter(rounded_grade)
        
        print(f"\n   📘 {course_name} ({course_type})")
        print(f"      Current Grade: {current_letter} ({current_grade}%) → Rounded: {rounded_grade}% ({rounded_letter})")
        
        cat_avgs = calculate_category_averages(assignments)
        for cat, data in sorted(cat_avgs.items()):
            weight = CATEGORY_WEIGHTS.get(cat, 0) * 100
            raw = data['average']
            rounded = school_round(raw)
            print(f"         {cat} ({weight:.0f}%): {raw}% → {rounded}% ({data['count']} assignments)")
    
    # =========================================
    # 4. UPCOMING ASSIGNMENTS IMPACT
    # =========================================
    if upcoming:
        print("\n" + "─" * 70)
        print("🔮 UPCOMING ASSIGNMENTS IMPACT ANALYSIS")
        print("─" * 70)
        
        for assignment in upcoming:
            analysis = analyze_upcoming_assignment(assignment, grades_data)
            
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
    print("Your Courses:")
    for i, course in enumerate(courses):
        grade = course.get('grades', {}).get('O2', {}).get('numeric', '?')
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
    assignments = selected_course.get('assignments', {}).get('Q2', [])
    cat_stats = calculate_category_averages(assignments)
    cat_data = cat_stats.get(category, {'average': 85, 'total_possible': 0})
    
    grade_info = selected_course.get('grades', {}).get('O2', {})
    current_grade = grade_info.get('numeric', 85)
    
    impact = calculate_assignment_impact(
        category=category,
        points_possible=possible_points,
        score=score,
        category_avg=cat_data.get('average', 85),
        category_points_so_far=cat_data.get('total_possible', 0),
        current_class_grade=current_grade
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
