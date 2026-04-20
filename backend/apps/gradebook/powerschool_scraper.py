"""
PowerSchool Grade Scraper - Your Working File
Handles Google SSO + School Portal authentication
"""

import asyncio
from playwright.async_api import async_playwright, Page, Browser
import sys
import json
import re
from datetime import datetime
from pathlib import Path

# Allow importing from backend/core regardless of CWD
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.db import get_db, init_db


async def detect_current_quarter(page: Page) -> str:
    """
    Find the latest active quarter by reading fg= params from bold grade links.
    Returns the highest Q-quarter that has real grades (e.g. 'Q4', 'Q3').
    """
    try:
        quarters = await page.evaluate("""
            () => {
                const seen = new Set();
                for (const a of document.querySelectorAll('tr[id^="ccid_"] a.bold[href*="scores.html"]')) {
                    try {
                        const params = new URLSearchParams(a.getAttribute('href').split('?')[1] || '');
                        const fg = params.get('fg');
                        if (fg && /^[QqOo]\\d$/.test(fg)) seen.add(fg.toUpperCase());
                    } catch(e) {}
                }
                return Array.from(seen).sort();
            }
        """)
        if quarters:
            current = quarters[-1]
            print(f"📅 Current quarter: {current}  (active quarters with grades: {quarters})")
            return current
    except Exception as e:
        print(f"⚠️  Quarter detection failed: {e}")
    print("📅 Falling back to Q3")
    return "Q3"


async def get_available_quarters(page: Page) -> list:
    """Return all Q-labeled quarters that have real grade links on the page."""
    try:
        quarters = await page.evaluate("""
            () => {
                const seen = new Set();
                for (const a of document.querySelectorAll('tr[id^="ccid_"] a.bold[href*="scores.html"]')) {
                    try {
                        const params = new URLSearchParams(a.getAttribute('href').split('?')[1] || '');
                        const fg = params.get('fg');
                        if (fg && /^[Qq]\\d$/.test(fg)) seen.add(fg.toUpperCase());
                    } catch(e) {}
                }
                return Array.from(seen).sort();
            }
        """)
        return quarters or []
    except Exception:
        return []


# PowerSchool URL - Uncommon Schools
POWERSCHOOL_URL = "https://psnj.uncommonschools.org/guardian/home.html?_userTypeHint=student"


def get_credentials():
    """
    Get login credentials from user input.
    
    Returns:
        tuple: (email, username, password)
    """
    print("=" * 60)
    print("POWERSCHOOL GRADE SCRAPER")
    print("=" * 60)
    print("\nPlease enter your login credentials:\n")
    
    email = input("School Email: ").strip()
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    if not email or not username or not password:
        print("\n❌ Error: All credentials are required!")
        sys.exit(1)
    
    print("\n✓ Credentials received. Starting automation...\n")
    
    return email, username, password



async def login_with_google_sso(page: Page, email: str, username: str, password: str):
    """
    Handle Google SSO + School Portal login flow using Playwright's auto-wait.
    
    Args:
        page: Playwright page object
        email: School email address
        username: School username
        password: School password
    
    Returns:
        bool: True if login successful, False otherwise
    """
    try:
        print(" Navigating to PowerSchool login page...")
        await page.goto(POWERSCHOOL_URL)
        
        # PowerSchool may redirect to Google sign-in either in the same tab or as a popup
        print(" Waiting for Google sign-in (either inline or popup)...")
        login_page = None

        # First, check if Google sign-in loaded in the current page
        try:
            await page.wait_for_selector('#identifierId', timeout=5000)
            login_page = page
            print(" Google sign-in detected on the current page.")
        except Exception:
            # Try detecting a popup opening
            try:
                async with page.context.expect_page(timeout=5000) as popup_info:
                    # Wait for popup to open automatically
                    pass
                login_page = await popup_info.value
                print(" Google sign-in popup detected and ready")
            except Exception:
                # As a last resort, check URL for Google login
                if "accounts.google" in page.url or "google.com" in page.url:
                    login_page = page
                    print(" Detected Google sign-in by URL on current page.")
                else:
                    raise Exception("Google sign-in page not detected (no popup and no sign-in form)")
        
        # ============================================
        # GOOGLE EMAIL ENTRY
        # ============================================
        print(" Entering email (field is already selected)...")
        # Email field is auto-selected, just type directly
        email_input = login_page.locator('#identifierId')
        await email_input.fill(email)
        
        # Click Next button - auto-waits until clickable
        next_button = login_page.locator('#identifierNext')
        await next_button.click()
        
        # ============================================
        # SCHOOL USERNAME ENTRY
        # ============================================
        print("👤 Entering username...")
        # Auto-waits for username field to appear
        username_input = login_page.locator('input[name="username"]')
        await username_input.fill(username)
        await username_input.press('Enter')
        
        # ============================================
        # SCHOOL PASSWORD ENTRY
        # ============================================
        print("🔑 Entering password...")
        # Auto-waits for password field to appear
        password_input = login_page.locator('input[name="password"]')
        await password_input.fill(password)
        await password_input.press('Enter')
        
        # ============================================
        # IDENTITY VERIFICATION (if present)
        # ============================================
        print(" Checking for identity verification...")
        try:
            buttons = login_page.locator('button')
            if await buttons.count() > 0:
                print("✓ Clicking verification continue button...")
                await buttons.nth(0).click()
        except Exception as e:
            print(f"  No verification button found (this is normal): {e}")
        
        # ============================================
        # WAIT FOR POWERSCHOOL TO LOAD
        # ============================================
        # Close popup if it was opened
        if login_page != page:
            print(" Closing Google popup...")
            await login_page.close()
        
        # Wait for PowerSchool to load - auto-waits for any of these elements
        print("✓ Waiting for PowerSchool to load...")
        try:
            # Playwright auto-waits for one of these elements to appear
            await page.wait_for_selector('.student-name, #userName, [class*="grades"], [id*="grades"], [class*="home"]', timeout=10000)
            print(" Login successful!")
            return True
        except Exception as e:
            print(f"⚠️  Could not verify login success: {e}")
            print("   Checking if we're on PowerSchool anyway...")
            # Check URL as backup
            if "uncommonschools.org" in page.url:
                print(" On PowerSchool page - login likely successful!")
                return True
            return False
    
    except Exception as e:
        print(f"❌ Login failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def normalize_grade(raw: str):
    """Normalize a grade string like "A 93" or "B+ 88" into {'letter': 'A', 'numeric': 93}.

    Returns None if it cannot parse the grade.
    """
    if not raw:
        return None

    raw = raw.strip()
    match = re.search(r'([A-F][+-]?)\s*(\d{1,3})', raw)
    if not match:
        return None

    return {"letter": match.group(1), "numeric": int(match.group(2))}


async def scrape_grades(page: Page) -> list:
    """
    Scrape all quarter grades for every course by reading fg= from each bold grade link href.
    Only counts a.bold links to scores.html — skips [ i ] info links and attendance links.

    Returns:
        list of dicts: [{"course": "...", "type": "AP|Regular", "grades": {"Q1": {...}, "Q4": {...}}}, ...]
    """
    print("🔎 Waiting for grades table...")
    try:
        await page.wait_for_selector('tr[id^="ccid_"]', timeout=15000)
    except Exception as e:
        print(f"❌ Grades table not found: {e}")
        return []

    results = await page.evaluate("""
        () => {
            const rows = Array.from(document.querySelectorAll('tr[id^="ccid_"]'));
            const out = [];
            for (const row of rows) {
                const courseEl = row.querySelector('td.table-element-text-align-start');
                if (!courseEl) continue;
                const course = (courseEl.innerText || courseEl.textContent)
                    .trim().split('\\n')[0].trim();
                if (!course) continue;

                const grades = {};
                // Only bold links to scores.html have real grade data
                for (const a of row.querySelectorAll('a.bold[href*="scores.html"]')) {
                    try {
                        const params = new URLSearchParams(
                            a.getAttribute('href').split('?')[1] || ''
                        );
                        const fg = params.get('fg');
                        if (!fg) continue;
                        const parts = (a.innerText || a.textContent).trim()
                            .split('\\n').map(s => s.trim()).filter(Boolean);
                        if (parts.length >= 2) {
                            const letter = parts[0];
                            const num = parseInt(parts[parts.length - 1], 10);
                            if (letter && !isNaN(num)) {
                                grades[fg.toUpperCase()] = { letter, numeric: num };
                            }
                        }
                    } catch(e) {}
                }

                if (Object.keys(grades).length === 0) continue;
                const courseType = course.startsWith('AP ') ? 'AP' : 'Regular';
                out.push({ course, type: courseType, grades });
            }
            return out;
        }
    """)

    print(f"🔢 Found {len(results)} courses with grades.")
    return results


async def scrape_current_quarter_assignments(page: Page, current_quarter: str) -> list:
    """
    Click into each course's current quarter detail page and scrape assignment data.
    Finds the right link by href containing fg={current_quarter} — no index guessing.

    Returns a list of dicts:
    [
      {
        "course": "AP English",
        "quarter": "Q4",
        "letter_grade": "A",
        "numeric_grade": 95,
        "assignments": [ {name, earned, possible, percent, category}, ... ]
      },
      ...
    ]
    """
    print(f"🔎 Scraping assignment details for {current_quarter}...")
    try:
        await page.wait_for_selector('tr[id^="ccid_"]', timeout=15000)
    except Exception as e:
        print(f"❌ Cannot find course rows: {e}")
        return []

    rows = page.locator('tr[id^="ccid_"]')
    count = await rows.count()
    details = []

    for i in range(count):
        row = rows.nth(i)

        try:
            course_raw = (await row.locator('td.table-element-text-align-start').inner_text()).strip()
            course = course_raw.splitlines()[0].strip()
        except Exception as e:
            print(f"⚠️  Couldn't read course name for detail row {i}: {e}")
            continue

        # Find the quarter link directly by fg= in href — no positional index needed
        quarter_link = row.locator(f'a.bold[href*="scores.html"][href*="fg={current_quarter}"]')
        if await quarter_link.count() == 0:
            print(f"⚠️  No {current_quarter} grade for {course}, skipping")
            continue

        try:
            raw = (await quarter_link.inner_text()).strip()
            parts = [p.strip() for p in raw.splitlines() if p.strip()]
            letter = parts[0] if parts else None
            numeric = int(parts[-1]) if parts and re.search(r'\d', parts[-1]) else None
        except Exception as e:
            print(f"⚠️  Couldn't parse {current_quarter} grade for {course}: {e}")
            letter = None
            numeric = None

        detail_page = None
        try:
            try:
                async with page.expect_navigation(timeout=7000):
                    await quarter_link.click()
                detail_page = page
            except Exception:
                try:
                    async with page.context.expect_page(timeout=7000) as popup_info:
                        await quarter_link.click()
                    detail_page = await popup_info.value
                    await detail_page.wait_for_load_state('networkidle')
                except Exception:
                    await quarter_link.click()
                    await page.wait_for_load_state('networkidle')
                    detail_page = page
        except Exception as e:
            print(f"⚠️  Clicking into {current_quarter} for {course} failed: {e}")
            continue

        assignments = []
        try:
            await detail_page.wait_for_selector('table#scoreTable tbody', timeout=7000)
            raw_rows = await detail_page.evaluate("""
                () => {
                    // Flag names from header columns
                    const flagNames = [];
                    const headerCols = document.querySelectorAll(
                        'table#scoreTable thead th.codeCol, table#scoreTable thead td.codeCol'
                    );
                    headerCols.forEach(th => {
                        flagNames.push(
                            (th.getAttribute('title') || th.textContent || '').trim()
                        );
                    });

                    const out = [];
                    const rows = document.querySelectorAll(
                        'table#scoreTable tbody tr[role="row"]'
                    );
                    for (const row of rows) {
                        if ((row.textContent || '').includes(
                            'Assignment Score Or Flag Last Updated'
                        )) break;

                        const tds = row.querySelectorAll('td');

                        const due = tds[0] ? tds[0].textContent.trim() : null;

                        const catEl = row.querySelector('td.categorycol');
                        const cat = catEl
                            ? catEl.textContent.trim().replace(/\\s+/g, ' ')
                            : null;

                        const nameEl =
                            row.querySelector('td.assignmentcol span.ng-binding') ||
                            row.querySelector('td.assignmentcol');
                        const name = nameEl
                            ? (nameEl.innerText || nameEl.textContent)
                                  .trim().split('\\n')[0].trim()
                            : null;
                        if (!name) continue;

                        // A flag is active only when its cell contains an <img> indicator.
                        // Inactive cells are empty or contain visually-hidden label text.
                        const flags = [];
                        const codeCols = row.querySelectorAll('td.codeCol');
                        codeCols.forEach((td, i) => {
                            const img = td.querySelector('img');
                            if (img) {
                                const label = flagNames[i] ||
                                    img.getAttribute('alt') ||
                                    img.getAttribute('title') || '';
                                if (label) flags.push(label);
                            }
                        });

                        let scoreText = '', percentText = '', gradeText = '';
                        for (let k = 0; k < tds.length; k++) {
                            if ((tds[k].className || '').includes('score')) {
                                scoreText = tds[k].textContent.trim();
                                if (tds[k + 1]) percentText = tds[k + 1].textContent.trim();
                                if (tds[k + 2]) gradeText = tds[k + 2].textContent.trim();
                                break;
                            }
                        }

                        out.push({ due, cat, name, flags, scoreText, percentText, gradeText });
                    }
                    return out;
                }
            """)

            for row in raw_rows:
                score_text = row.get('scoreText', '')
                percent_text = row.get('percentText', '')
                grade_text = row.get('gradeText', '')

                earned = None
                possible = None
                percent = None
                try:
                    s = score_text.replace('(', '').replace(')', '').replace('\u00a0', ' ')
                    if '/' in s:
                        left, right = [p.strip() for p in s.split('/', 1)]
                        left_num = re.search(r"(\d+(?:\.\d+)?)", left)
                        right_num = re.search(r"(\d+(?:\.\d+)?)", right)
                        if left_num:
                            earned = float(left_num.group(1))
                        if right_num:
                            possible = float(right_num.group(1))
                        if earned is not None and possible is not None and possible != 0:
                            percent = round((earned / possible) * 100, 2)
                    if percent is None and percent_text:
                        m = re.search(r"(\d+(?:\.\d+)?)", percent_text)
                        if m:
                            percent = float(m.group(1))
                            possible = possible or 100.0
                            earned = earned or (
                                round((percent / 100.0) * possible, 2) if possible else earned
                            )
                except Exception:
                    pass

                assignments.append({
                    'name': row['name'],
                    'due_date': row.get('due'),
                    'category': row.get('cat'),
                    'flags': row.get('flags', []),
                    'earned': earned,
                    'possible': possible,
                    'percent': percent,
                    'letter': grade_text or None
                })
        except Exception as e:
            print(f"⚠️  Error scraping assignments for {course}: {e}")

        details.append({
            "course": course,
            "quarter": current_quarter,
            "letter_grade": letter,
            "numeric_grade": numeric,
            "assignments": assignments
        })

        try:
            if detail_page is not None and detail_page != page:
                await detail_page.close()
            else:
                await page.go_back()
                await page.wait_for_selector('tr[id^="ccid_"]', timeout=10000)
        except Exception:
            pass

    return details


def save_to_db(grades_data: dict):
    """
    Persist scraped grades and assignments into the shared formal.db.
    Inserts fresh rows on every scrape run (does not upsert — history is preserved).
    """
    init_db()
    scraped_at = grades_data.get("scraped_at", datetime.utcnow().isoformat() + "Z")
    conn = get_db()

    with conn:
        for course in grades_data.get("grades", []):
            course_name = course.get("course", "")
            course_type = course.get("type", "Regular")

            # Insert one row per quarter grade
            for quarter, grade_info in course.get("grades", {}).items():
                if not grade_info:
                    continue
                conn.execute(
                    """
                    INSERT INTO grades (scraped_at, course, course_type, quarter, letter_grade, numeric_grade)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scraped_at,
                        course_name,
                        course_type,
                        quarter,
                        grade_info.get("letter"),
                        grade_info.get("numeric"),
                    ),
                )

            # Insert assignment rows for each quarter
            for quarter, assignments in course.get("assignments", {}).items():
                for a in assignments:
                    flags = json.dumps(a.get("flags", []))
                    conn.execute(
                        """
                        INSERT INTO assignments
                            (scraped_at, course, quarter, name, category, due_date,
                             earned, possible, percent, letter, flags)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scraped_at,
                            course_name,
                            quarter,
                            a.get("name", ""),
                            a.get("category"),
                            a.get("due_date"),
                            a.get("earned"),
                            a.get("possible"),
                            a.get("percent"),
                            a.get("letter"),
                            flags,
                        ),
                    )

    conn.close()
    print(f"✅ Saved to formal.db (scraped_at={scraped_at})")


async def main(creds=None, quarter: str = None):
    """
    Main function to run the scraper.
    Pass creds=(email, username, password) to skip the prompt.
    Pass quarter='Q3' to force a specific quarter instead of auto-detecting.
    """
    email, username, password = creds if creds else get_credentials()

    print(f"\n Starting browser automation...")
    print(f" Target: {POWERSCHOOL_URL}\n")

    # Start Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Perform login
        success = await login_with_google_sso(page, email, username, password)

        if success:
            print("\n" + "="*60)
            print(" LOGIN COMPLETE!")
            print("="*60)

            # Scrape all quarter grades (reads fg= from each link href)
            grades = await scrape_grades(page)
            if grades:
                scrape_all = quarter and quarter.upper() == 'ALL'

                if scrape_all:
                    quarters_to_scrape = await get_available_quarters(page)
                    print(f"📅 Scraping ALL quarters: {quarters_to_scrape}")
                elif quarter:
                    quarters_to_scrape = [quarter.upper()]
                    print(f"📅 Using specified quarter: {quarter.upper()}")
                else:
                    quarters_to_scrape = [await detect_current_quarter(page)]

                # Scrape assignment details for each selected quarter
                all_details = []
                for q in quarters_to_scrape:
                    print(f"\n── Scraping {q} assignments ──")
                    details = await scrape_current_quarter_assignments(page, q)
                    all_details.extend(details)

                # Merge assignments into grades (no break — collect every quarter per course)
                for g in grades:
                    for d in all_details:
                        if g.get('course') and d.get('course') and g['course'].lower() == d['course'].lower():
                            q_label = d.get('quarter', 'Q?')
                            g.setdefault('assignments', {})[q_label] = d.get('assignments', [])

                out_path = 'grades.json'
                try:
                    data = {
                        "scraped_at": datetime.utcnow().isoformat() + "Z",
                        "grades": grades,
                    }
                    with open(out_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"✅ Saved {len(grades)} grades → {out_path} (scraped_at={data['scraped_at']})")
                    save_to_db(data)
                except Exception as e:
                    print(f"❌ Failed to write grades to {out_path}: {e}")
            else:
                print("⚠️ No grades extracted.")
        else:
            print("\n❌ Login failed.")

        await browser.close()
        print(" Browser closed. Automation complete!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Automation stopped by user.")
        sys.exit(0)
