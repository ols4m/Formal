"""
Formal — run all scrapers with a single set of credentials.

Usage: python run.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apps.gradebook.powerschool_scraper import main as run_powerschool
from apps.gradebook.classroom_scraper import main as run_classroom


def get_credentials():
    print("=" * 60)
    print("FORMAL — SCRAPER LOGIN")
    print("=" * 60)
    print("\nEnter your credentials once. Both scrapers will use them.\n")
    email    = input("School Email: ").strip()
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    if not email or not username or not password:
        print("\n❌ All fields are required.")
        sys.exit(1)
    print()
    return email, username, password


async def main():
    creds = get_credentials()

    print("─" * 60)
    print("STEP 1 — PowerSchool (grades + assignments)")
    print("─" * 60)
    await run_powerschool(creds=creds)

    print()
    print("─" * 60)
    print("STEP 2 — Google Classroom (upcoming tasks)")
    print("─" * 60)
    await run_classroom(creds=creds)

    print()
    print("=" * 60)
    print("✅ All scrapers done. Run the Agenda:")
    print("   python apps/agenda/main.py")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Stopped.")
        sys.exit(0)
