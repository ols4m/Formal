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

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"


async def save_screenshot(page, label: str):
    """Save a screenshot for visual inspection in VSCode."""
    try:
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        ts = datetime.utcnow().strftime("%H%M%S")
        path = SCREENSHOTS_DIR / f"{ts}_{label}.png"
        await page.screenshot(path=str(path), full_page=False)
        print(f"📸 Screenshot saved → apps/gradebook/screenshots/{path.name}")
    except Exception as e:
        print(f"⚠️  Screenshot failed: {e}")


async def get_grade_column_labels(page: Page) -> list:
    """
    Detect grade column labels by matching table headers to columns that have
    a.bold grade links, using JavaScript for reliable colspan-aware DOM traversal.
    Returns labels like ['Q1', 'Q2', 'F2', 'Q3', 'F3', 'Y1'] — one entry per grade link.
    """
    try:
        labels = await page.evaluate("""
            () => {
                const pat = /^[QqOoFfSsYyEe]\\d$/;

                // Find which td-column indices have a.bold in the first data row
                const firstRow = document.querySelector('tbody tr[id^="ccid_"]');
                if (!firstRow) return [];
                const tds = Array.from(firstRow.querySelectorAll('td'));
                const activeCols = tds.reduce((acc, td, idx) => {
                    if (td.querySelector('a.bold')) acc.push(idx);
                    return acc;
                }, []);
                if (activeCols.length === 0) return [];

                // Build a flat header array expanding colspan
                const headerRow = document.querySelector('thead tr:last-child') ||
                                  document.querySelector('thead tr');
                if (!headerRow) return activeCols.map((_, i) => 'G' + (i + 1));

                const flat = [];
                for (const th of headerRow.querySelectorAll('th, td')) {
                    const span = parseInt(th.getAttribute('colspan') || '1', 10);
                    const t = (th.innerText || th.textContent || '').trim().replace(/\\s+/g, ' ');
                    for (let i = 0; i < span; i++) flat.push(t);
                }

                // Map each active column index to its header label
                return activeCols.map((col, i) => {
                    const t = col < flat.length ? flat[col] : '';
                    return pat.test(t) ? t.toUpperCase() : 'G' + (i + 1);
                });
            }
        """)
        if labels:
            print(f"📋 Detected grade columns: {labels}")
        return labels or []
    except Exception as e:
        print(f"⚠️  Column label detection failed: {e}")
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
    Scrape PowerSchool grade rows and extract course name and all quarter grades.
    Dynamically detects column labels (Q1, Q2, Q3, Q4, Y1, etc.) from the table header.

    Returns:
        list of dicts: [{"course": "...", "grades": {"Q1": {...}, "Q3": {...}, "Y1": {...}}}, ...]
    """
    print("🔎 Waiting for grades table...")
    try:
        await page.wait_for_selector('tbody tr[id^="ccid_"]', timeout=15000)
    except Exception as e:
        print(f"❌ Grades table not found: {e}")
        return []

    # Detect grade column labels from the table header
    grade_labels = await get_grade_column_labels(page)

    rows = page.locator('tbody tr[id^="ccid_"]')
    count = await rows.count()
    print(f"🔢 Found {count} course rows.")

    results = []
    for i in range(count):
        row = rows.nth(i)

        # Course name
        try:
            course_raw = (await row.locator('td.table-element-text-align-start').inner_text()).strip()
            course = course_raw.splitlines()[0].strip()
            course_type = "AP" if course[:2].upper() == "AP" else "Regular"
        except Exception as e:
            print(f"⚠️  Couldn't read course name for row {i}: {e}")
            continue

        grades_links = row.locator('td a.bold')
        gl_count = await grades_links.count()
        grades_dict = {}

        # Read every grade link and label it using detected column headers
        for k in range(gl_count):
            label = grade_labels[k] if k < len(grade_labels) else f"G{k + 1}"
            try:
                raw = (await grades_links.nth(k).inner_text()).strip()
                parts = [p.strip() for p in raw.splitlines() if p.strip()]
                if len(parts) >= 2:
                    try:
                        norm = {"letter": parts[0], "numeric": int(parts[-1])}
                    except ValueError:
                        norm = normalize_grade(" ".join(parts))
                else:
                    norm = normalize_grade(" ".join(parts))
                if norm:
                    grades_dict[label] = norm
            except Exception as e:
                print(f"⚠️  Error parsing {label} for {course}: {e}")

        # ARIA fallback: if grade links yielded nothing, scan td[role=cell] aria-labels
        if not grades_dict:
            grade_cells = row.locator('td[role="cell"]')
            gcount = await grade_cells.count()
            grade_texts = []
            for j in range(gcount):
                cell = grade_cells.nth(j)
                try:
                    aria = await cell.get_attribute('aria-label')
                except Exception:
                    aria = None
                try:
                    text = (aria or (await cell.inner_text() or '')).strip()
                except Exception:
                    text = aria or ''
                if text and re.search(r'\d', text):
                    grade_texts.append(text)

            for k, text in enumerate(grade_texts):
                norm = normalize_grade(text)
                if norm:
                    label = grade_labels[k] if k < len(grade_labels) else f"G{k + 1}"
                    grades_dict[label] = norm

        if not grades_dict:
            print(f"⚠️  Skipping {course!r}: no grades found (link_count={gl_count})")
            continue

        results.append({
            "course": course,
            "type": course_type,
            "grades": grades_dict,
        })

    return results


async def scrape_current_quarter_assignments(page: Page, grade_labels: list) -> list:
    """
    Click into each course's CURRENT quarter detail link and scrape assignment-level data.
    Automatically detects the latest active quarter from the detected grade column labels.

    Returns a list of dicts:
    [
      {
        "course": "AP English",
        "quarter": "Q3",
        "letter_grade": "B-",
        "numeric_grade": 80,
        "assignments": [ {name, earned, possible, percent, category}, ... ]
      },
      ...
    ]
    """
    # Find the last Q/O column (current quarter) — skip Y1/E1/S1 year-summary labels
    quarter_cols = [
        (i, lbl) for i, lbl in enumerate(grade_labels)
        if re.match(r'^[QqOo]\d$', lbl)
    ]
    if quarter_cols:
        current_idx, current_label = quarter_cols[-1]
    else:
        # Fallback: assume 2nd link = current quarter (old Q2 behavior)
        current_idx, current_label = 1, "Q2"

    print(f"🔎 Scraping assignment details for {current_label} (link index {current_idx})...")
    try:
        await page.wait_for_selector('tbody tr[id^="ccid_"]', timeout=15000)
    except Exception as e:
        print(f"❌ Cannot find course rows to click into: {e}")
        return []

    rows = page.locator('tbody tr[id^="ccid_"]')
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

        grades_links = row.locator('td a.bold')
        gl_count = await grades_links.count()
        if gl_count <= current_idx:
            print(f"⚠️  No {current_label} link for {course} (only {gl_count} links), skipping")
            continue

        try:
            raw = (await grades_links.nth(current_idx).inner_text()).strip()
            parts = [p.strip() for p in raw.splitlines() if p.strip()]
            letter = parts[0] if parts else None
            numeric = int(parts[-1]) if parts and re.search(r'\d', parts[-1]) else None
        except Exception as e:
            print(f"⚠️  Couldn't parse {current_label} grade for {course}: {e}")
            letter = None
            numeric = None

        detail_page = None
        try:
            try:
                async with page.expect_navigation(timeout=7000):
                    await grades_links.nth(current_idx).click()
                detail_page = page
            except Exception:
                try:
                    async with page.context.expect_page(timeout=7000) as popup_info:
                        await grades_links.nth(current_idx).click()
                    detail_page = await popup_info.value
                    await detail_page.wait_for_load_state('networkidle')
                except Exception:
                    await grades_links.nth(current_idx).click()
                    await page.wait_for_load_state('networkidle')
                    detail_page = page
        except Exception as e:
            print(f"⚠️  Clicking into {current_label} for {course} failed: {e}")
            continue

        assignments = []
        try:
            await detail_page.wait_for_selector('table#scoreTable tbody', timeout=7000)
            assign_rows = detail_page.locator('table#scoreTable tbody tr[role="row"]')
            ar_count = await assign_rows.count()
            for j in range(ar_count):
                ar = assign_rows.nth(j)

                try:
                    row_text = (await ar.inner_text() or '').strip()
                except Exception:
                    row_text = ''
                if 'Assignment Score Or Flag Last Updated' in row_text:
                    break

                try:
                    due = (await ar.locator('td').nth(0).inner_text()).strip()
                except Exception:
                    due = None

                try:
                    cat = (await ar.locator('td.categorycol').inner_text()).strip()
                    cat = ' '.join(cat.split()) if cat else None
                except Exception:
                    cat = None

                try:
                    name = (await ar.locator('td.assignmentcol span.ng-binding').inner_text()).strip()
                except Exception:
                    name = (await ar.locator('td.assignmentcol').inner_text()).strip()

                flags = []
                try:
                    code_cells = ar.locator('td.codeCol')
                    cc = await code_cells.count()
                    for k in range(cc):
                        txt = (await code_cells.nth(k).inner_text()).strip()
                        if txt:
                            flags.append(' '.join(txt.split()))
                except Exception:
                    pass

                tds = ar.locator('td')
                tc = await tds.count()
                score_text = ''
                percent_text = ''
                grade_text = ''
                score_idx = None
                for k in range(tc):
                    cls = (await tds.nth(k).get_attribute('class')) or ''
                    if 'score' in cls:
                        score_idx = k
                        break
                if score_idx is not None:
                    try:
                        score_text = (await tds.nth(score_idx).inner_text()).strip()
                    except Exception:
                        score_text = ''
                    if score_idx + 1 < tc:
                        try:
                            percent_text = (await tds.nth(score_idx + 1).inner_text()).strip()
                        except Exception:
                            percent_text = ''
                    if score_idx + 2 < tc:
                        try:
                            grade_text = (await tds.nth(score_idx + 2).inner_text()).strip()
                        except Exception:
                            grade_text = ''

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
                            earned = earned or (round((percent / 100.0) * possible, 2) if possible else earned)
                except Exception:
                    pass

                assignments.append({
                    'name': name,
                    'due_date': due,
                    'category': cat,
                    'flags': flags,
                    'earned': earned,
                    'possible': possible,
                    'percent': percent,
                    'letter': grade_text or None
                })
        except Exception as e:
            print(f"⚠️  Error scraping assignments for {course}: {e}")

        details.append({
            "course": course,
            "quarter": current_label,
            "letter_grade": letter,
            "numeric_grade": numeric,
            "assignments": assignments
        })

        try:
            if detail_page is not None and detail_page != page:
                await detail_page.close()
            else:
                await page.go_back()
                await page.wait_for_selector('tbody tr[id^="ccid_"]', timeout=10000)
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


async def main(creds=None):
    """
    Main function to run the scraper.
    Pass creds=(email, username, password) to skip the prompt.
    """
    email, username, password = creds if creds else get_credentials()
    
    print(f"\n Starting browser automation...")
    print(f" Target: {POWERSCHOOL_URL}\n")
    
    # Start Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await save_screenshot(page, "01_start")

        # Perform login
        success = await login_with_google_sso(page, email, username, password)

        if success:
            print("\n" + "="*60)
            print(" LOGIN COMPLETE!")
            print("="*60)

            await save_screenshot(page, "02_home_grades_table")

            # Detect grade column labels from the table header
            grade_labels = await get_grade_column_labels(page)

            # Scrape all quarter grades
            grades = await scrape_grades(page)
            if grades:
                await save_screenshot(page, "03_after_grade_scrape")

                # Scrape current-quarter assignment details
                current_details = await scrape_current_quarter_assignments(page, grade_labels)

                await save_screenshot(page, "04_after_assignments")

                # Merge assignments into grades by course name
                for g in grades:
                    for d in current_details:
                        if g.get('course') and d.get('course') and g['course'].lower() == d['course'].lower():
                            q_label = d.get('quarter', 'Q?')
                            g.setdefault('assignments', {})[q_label] = d.get('assignments', [])
                            break

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
            await save_screenshot(page, "02_login_failed")
            print("\n❌ Login failed.")

        await browser.close()
        print(" Browser closed. Automation complete!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Automation stopped by user.")
        sys.exit(0)
