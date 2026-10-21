"""
Formal — run all scrapers with a single set of credentials.

Usage: python run.py
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from apps.gradebook.powerschool_scraper import main as run_powerschool
from apps.gradebook.classroom_scraper import main as run_classroom
from apps.gradebook.calculator import generate_full_report, generate_trend_report, generate_missing_report
from apps.gmail.gmail_scraper import main as run_gmail

from core.profile import get_current_term, load_profile

# Load .env from the backend directory (gitignored, never committed)
load_dotenv(Path(__file__).parent / '.env')


def get_credentials():
    email    = os.getenv('SCHOOL_EMAIL', '').strip()
    username = os.getenv('SCHOOL_USERNAME', '').strip()
    password = os.getenv('SCHOOL_PASSWORD', '').strip()

    if email and username and password:
        print("🔑 Credentials loaded from .env")
        return email, username, password

    # Fall back to prompting if .env isn't filled in
    print("=" * 60)
    print("FORMAL — SCRAPER LOGIN")
    print("=" * 60)
    print("\nTip: fill in backend/.env to skip this prompt.\n")
    email    = input("School Email: ").strip()
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    if not email or not username or not password:
        print("\n❌ All fields are required.")
        sys.exit(1)
    print()
    return email, username, password


def get_term_selection():
    profile = load_profile()
    terms = [item["name"] for item in profile["terms"]]
    current_term = get_current_term(profile)["name"]
    source_periods = list(dict.fromkeys(
        period
        for term in profile["terms"]
        for period in term.get("source_periods", [])
    ))
    print()
    print("─" * 60)
    print("TERM SELECTION")
    print("─" * 60)
    print(f"Configured terms: {', '.join(terms)}")
    print(f"PowerSchool source periods: {', '.join(source_periods) or '(auto-detect)'}")
    print("Press Enter to use the active term and auto-detect source period.\n")

    raw_scrape = input("Scrape which source period? [auto] ").strip().upper()
    if raw_scrape == 'ALL':
        scrape_source_period = 'ALL'
    elif raw_scrape in source_periods:
        scrape_source_period = raw_scrape
    elif raw_scrape:
        raise ValueError(
            f"Unknown source period {raw_scrape!r}; choose one of {source_periods} or ALL."
        )
    else:
        scrape_source_period = None

    if scrape_source_period == 'ALL':
        raw_calc = input(
            f"Calculate: trend report [Enter] or configured term ({' / '.join(terms)})? "
        ).strip()
        if raw_calc:
            calc_term = next(
                (term for term in terms if term.casefold() == raw_calc.casefold()),
                None,
            )
            if calc_term is None:
                raise ValueError(f"Unknown term {raw_calc!r}; choose one of {terms}.")
        else:
            calc_term = 'TREND'
    else:
        raw_calc = input(f"Calculate which term? [{current_term}] ").strip()
        calc_term = next(
            (term for term in terms if term.casefold() == raw_calc.casefold()),
            None,
        ) if raw_calc else current_term
        if calc_term is None:
            raise ValueError(f"Unknown term {raw_calc!r}; choose one of {terms}.")

    print()
    source_label = scrape_source_period or "auto-detect"
    print(f"  Scraping source period: {source_label}  |  Calculating term: {calc_term}")
    return scrape_source_period, calc_term


async def main():
    creds = get_credentials()
    scrape_source_period, calc_term = get_term_selection()

    print("─" * 60)
    print("STEP 1 — PowerSchool (grades + assignments)")
    print("─" * 60)
    await run_powerschool(creds=creds, source_period=scrape_source_period)

    print()
    print("─" * 60)
    print("STEP 2 — Grade Calculator")
    print("─" * 60)
    if calc_term == 'TREND':
        generate_trend_report(grades_path='grades.json')
    else:
        generate_full_report(grades_path='grades.json', term=calc_term)

    print()
    print("─" * 60)
    print("STEP 3 — Google Classroom (upcoming + missing)")
    print("─" * 60)
    await run_classroom(creds=creds)

    print()
    print("─" * 60)
    print("STEP 4 — Missing Assignment Report")
    print("─" * 60)
    generate_missing_report(missing_path='output/missing_impactful.json', grades_path='grades.json')

    print()
    print("─" * 60)
    print("STEP 5 — Gmail Intake (school + personal)")
    print("─" * 60)
    await run_gmail(creds=creds)

    print()
    print("=" * 60)
    print("✅ All done.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n🛑 Stopped.")
        sys.exit(0)
