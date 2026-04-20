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


VALID_QUARTERS = {'Q1', 'Q2', 'Q3', 'Q4'}


def get_quarter_selection():
    print()
    print("─" * 60)
    print("QUARTER SELECTION")
    print("─" * 60)
    print("Options: Q1  Q2  Q3  Q4  ALL")
    print("Press Enter to auto-detect current quarter.\n")

    raw_scrape = input("Scrape which quarter? [auto] ").strip().upper()
    if raw_scrape == 'ALL':
        scrape_quarter = 'ALL'
    elif raw_scrape in VALID_QUARTERS:
        scrape_quarter = raw_scrape
    else:
        scrape_quarter = None

    if scrape_quarter == 'ALL':
        # When scraping all, calc defaults to trend report
        raw_calc = input("Calculate: trend report [Enter] or specific quarter (Q1-Q4)? ").strip().upper()
        calc_quarter = raw_calc if raw_calc in VALID_QUARTERS else 'TREND'
    else:
        raw_calc = input("Calculate which quarter? [same as scrape] ").strip().upper()
        if raw_calc in VALID_QUARTERS:
            calc_quarter = raw_calc
        elif scrape_quarter:
            calc_quarter = scrape_quarter
        else:
            calc_quarter = None

    print()
    sq_label = scrape_quarter or "auto-detect"
    cq_label = calc_quarter or "auto-detect"
    print(f"  Scraping: {sq_label}  |  Calculating: {cq_label}")
    return scrape_quarter, calc_quarter


async def main():
    creds = get_credentials()
    scrape_quarter, calc_quarter = get_quarter_selection()

    print("─" * 60)
    print("STEP 1 — PowerSchool (grades + assignments)")
    print("─" * 60)
    await run_powerschool(creds=creds, quarter=scrape_quarter)

    print()
    print("─" * 60)
    print("STEP 2 — Grade Calculator")
    print("─" * 60)
    if calc_quarter == 'TREND':
        generate_trend_report(grades_path='grades.json')
    else:
        generate_full_report(grades_path='grades.json', quarter=calc_quarter)

    print()
    print("─" * 60)
    print("STEP 3 — Google Classroom (upcoming + missing)")
    print("─" * 60)
    await run_classroom(creds=creds)

    print()
    print("─" * 60)
    print("STEP 4 — Missing Assignment Report")
    print("─" * 60)
    generate_missing_report(missing_path='output/missing.json', grades_path='grades.json')

    print()
    print("=" * 60)
    print("✅ All done.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Stopped.")
        sys.exit(0)
