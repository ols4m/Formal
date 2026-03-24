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

# ==========================================
# MAIN
# ==========================================

def main():
    grades_data = load_json('grades.json')
    upcoming_data = load_json('output/assignments.json')
    
    if not grades_data or not upcoming_data:
        print("❌ Missing data files. Ensure grades.json and output/assignments.json exist.")
        return
        
    upcoming_list = upcoming_data.get('assignments', [])
    courses = grades_data.get('grades', [])
    
    results = []
    
    for item in upcoming_list:
        course_name = item.get('course_name', '')
        title = item.get('assignment_title', '')
        category = item.get('category') or 'Homework'
        points = item.get('possible_points') or 100
        due_date = parse_date(item.get('due_date'))
        
        # Match course
        matching_course = None
        for c in courses:
            c_name = c.get('course', '')
            if c_name in course_name or course_name in c_name:
                matching_course = c
                break
        
        if not matching_course:
            continue
            
        # Get stats
        q2_assignments = matching_course.get('assignments', {}).get('Q2', [])
        cat_avg, cat_points = get_category_stats(q2_assignments, category)
        
        # Current Grade
        grade_info = matching_course.get('grades', {}).get('O2') or matching_course.get('grades', {}).get('Q2', {})
        current_grade = grade_info.get('numeric', 85)
        
        # Calculate
        calc = calculate_priority(category, points, current_grade, cat_points, due_date)
        
        results.append({
            'title': title,
            'course': matching_course['course'],
            'priority': calc['priority'],
            'tier': '', # filled later
            'days_left': calc['metrics']['days_left'],
            'category': category,
            'impact': round(calc['metrics']['impact'] * 100, 1)
        })

    # Sort by priority descending
    results.sort(key=lambda x: x['priority'], reverse=True)
    
    # Assign Tiers
    all_p = [r['priority'] for r in results]
    for r in results:
        r['tier'] = get_tier(r['priority'], all_p)
        
    # Output Report
    print("=" * 80)
    print(f"🚀 ASSIGNMENT PRIORITY LIST (Lookahead: {LOOKAHEAD_DAYS} days)")
    print(f"Target: {TARGET_GRADE}% | Date: {date.today()}")
    print("=" * 80)
    print(f"{'TIER':<6} | {'PRIORITY':<8} | {'DAYS':<4} | {'COURSE':<25} | {'ASSIGNMENT'}")
    print("-" * 80)
    
    for r in results:
        days_str = str(r['days_left']) if r['days_left'] >= 0 else "LATE"
        print(f"{r['tier']:<6} | {r['priority']:>8.4f} | {days_str:<4} | {r['course'][:25]:<25} | {r['title']}")
    
    print("-" * 80)
    print("Legend: S (Critical), A (High), B (Medium), C (Low), D (Minimal)")
    print("=" * 80)

if __name__ == "__main__":
    main()
