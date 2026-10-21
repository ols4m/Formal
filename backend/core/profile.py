"""Profile configuration for school terms and grading rules."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BACKEND_DIR / "profile.json"
PROFILE_EXAMPLE_PATH = BACKEND_DIR / "profile.example.json"
_EXAMPLE_PROFILE_WARNING_SHOWN = False
CATEGORY_ALIASES = {
    "HW": "Homework",
    "CW": "Classwork",
    "Quiz": "Quizzes",
    "Test": "Tests",
    "IA": "Interim Assessment",
    "Interim Assessments": "Interim Assessment",
}


def normalize_category_name(category: str) -> str:
    """Map common school category aliases to their canonical profile names."""
    return CATEGORY_ALIASES.get(category, category)


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate a user profile, falling back to the committed example."""
    global _EXAMPLE_PROFILE_WARNING_SHOWN
    profile_path = Path(path) if path else PROFILE_PATH
    if not profile_path.exists() and path is None:
        profile_path = PROFILE_EXAMPLE_PATH
        if not _EXAMPLE_PROFILE_WARNING_SHOWN:
            print(
                f"⚠️  {PROFILE_PATH} not found; using {PROFILE_EXAMPLE_PATH}. "
                "Copy the example to profile.json and customize it."
            )
            _EXAMPLE_PROFILE_WARNING_SHOWN = True

    with profile_path.open("r", encoding="utf-8") as profile_file:
        profile = json.load(profile_file)
    validate_profile(profile)
    return profile


def validate_profile(profile: dict[str, Any]) -> None:
    """Reject incomplete or internally inconsistent profile configuration."""
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")

    terms = profile.get("terms")
    if not isinstance(terms, list) or not terms:
        raise ValueError("Profile must define a non-empty 'terms' list.")

    term_names: set[str] = set()
    parsed_terms: list[tuple[date, date, str]] = []
    for term in terms:
        if not isinstance(term, dict) or not all(
            isinstance(term.get(key), str) for key in ("name", "start", "end")
        ):
            raise ValueError("Each term must define string 'name', 'start', and 'end' values.")
        name = term["name"].strip()
        if not name or name in term_names:
            raise ValueError(f"Term names must be non-empty and unique: {name!r}.")
        term_names.add(name)
        try:
            start = date.fromisoformat(term["start"])
            end = date.fromisoformat(term["end"])
        except ValueError as error:
            raise ValueError(f"Term {name!r} dates must use YYYY-MM-DD format.") from error
        if end < start:
            raise ValueError(f"Term {name!r} ends before it starts.")
        parsed_terms.append((start, end, name))

    parsed_terms.sort()
    for previous, current in zip(parsed_terms, parsed_terms[1:]):
        if current[0] <= previous[1]:
            raise ValueError(f"Terms {previous[2]!r} and {current[2]!r} overlap.")

    grading = profile.get("grading")
    if not isinstance(grading, dict):
        raise ValueError("Profile must define a 'grading' object.")
    if not isinstance(grading.get("scale"), dict) or not grading["scale"]:
        raise ValueError("Profile grading.scale must be a non-empty letter-to-threshold map.")
    if not isinstance(grading.get("gpa_points"), dict) or not grading["gpa_points"]:
        raise ValueError("Profile grading.gpa_points must be a non-empty letter-to-points map.")
    if not isinstance(grading.get("target_grade"), (int, float)):
        raise ValueError("Profile grading.target_grade must be numeric.")
    for letter, threshold in grading["scale"].items():
        if not isinstance(threshold, (int, float)):
            raise ValueError(f"Grade threshold for {letter!r} must be numeric.")
    for letter, points in grading["gpa_points"].items():
        if not isinstance(points, (int, float)):
            raise ValueError(f"GPA points for {letter!r} must be numeric.")
    for key in ("weighted_bonus", "gpa_adjustment"):
        if key in grading and not isinstance(grading[key], (int, float)):
            raise ValueError(f"Profile grading.{key} must be numeric.")
    lookahead = grading.get("lookahead_days", 14)
    if not isinstance(lookahead, (int, float)) or lookahead <= 0:
        raise ValueError("Profile grading.lookahead_days must be a positive number.")
    rounding = grading.get("rounding")
    if not isinstance(rounding, dict) or not isinstance(rounding.get("round_up_at"), (int, float)):
        raise ValueError("Profile grading.rounding.round_up_at must be numeric.")
    if not 0 <= rounding["round_up_at"] < 1:
        raise ValueError("Profile grading.rounding.round_up_at must be between 0 and 1.")
    if not isinstance(profile.get("categories"), dict):
        raise ValueError("Profile must define a 'categories' object.")
    for category, settings in profile["categories"].items():
        if not isinstance(settings, dict) or not isinstance(settings.get("weight"), (int, float)):
            raise ValueError(f"Category {category!r} must define a numeric weight.")
        for key in ("floor", "volatility"):
            if key in settings and not isinstance(settings[key], (int, float)):
                raise ValueError(f"Category {category!r} {key} must be numeric.")
    courses = profile.get("courses", {})
    if not isinstance(courses, dict):
        raise ValueError("Profile courses must be an object.")
    for course_name, settings in courses.items():
        if not isinstance(settings, dict):
            raise ValueError(f"Course {course_name!r} settings must be an object.")
        credits = settings.get("credits", 1.0)
        if not isinstance(credits, (int, float)) or credits <= 0:
            raise ValueError(f"Course {course_name!r} credits must be a positive number.")
        category_weights = settings.get("category_weights", {})
        if not isinstance(category_weights, dict):
            raise ValueError(f"Course {course_name!r} category_weights must be an object.")
        if not all(isinstance(weight, (int, float)) for weight in category_weights.values()):
            raise ValueError(f"Course {course_name!r} category weights must be numeric.")

    for term in terms:
        source_periods = term.get("source_periods", [])
        if not isinstance(source_periods, list) or not all(
            isinstance(period, str) and period.strip() for period in source_periods
        ):
            raise ValueError(f"Term {term['name']!r} source_periods must be a list of strings.")


def get_current_term(
    profile: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the configured term containing today, or raise when none is active."""
    profile = profile or load_profile()
    today = today or date.today()
    if isinstance(today, datetime):
        today = today.date()
    for term in profile["terms"]:
        if date.fromisoformat(term["start"]) <= today <= date.fromisoformat(term["end"]):
            return term
    raise ValueError(
        f"No configured term contains {today.isoformat()}; update the dates in profile.json."
    )


def get_term_for_date(
    value: str | date,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the configured term containing a date, or None for dates outside terms."""
    profile = profile or load_profile()
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        day = date.fromisoformat(value[:10])
    for term in profile["terms"]:
        if date.fromisoformat(term["start"]) <= day <= date.fromisoformat(term["end"]):
            return term
    return None


def source_periods_for_term(
    term_name: str,
    profile: dict[str, Any] | None = None,
) -> list[str]:
    """Return mapped source periods, or the term name when no mapping is configured."""
    profile = profile or load_profile()
    term = next((item for item in profile["terms"] if item["name"] == term_name), None)
    if term is None:
        raise ValueError(f"Unknown configured term: {term_name!r}.")
    periods = term.get("source_periods")
    return list(periods) if periods else [term_name]
