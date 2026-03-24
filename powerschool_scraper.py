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
            # Look for Continue/Verify buttons with short timeout
            buttons = login_page.locator('button').all()
            if await buttons.count() > 0:
                print("✓ Clicking verification continue button...")
                await buttons[0].click()
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
    Scrape PowerSchool grade rows and extract course name and normalized Q1/O2 grades.

    Returns:
        list of dicts: [{"course": "Course Name", "grades": {"Q1": {...}, "O2": {...}}}, ...]
    """
    print("🔎 Waiting for grades table...")
    try:
        await page.wait_for_selector('tbody tr[id^="ccid_"]', timeout=15000)
    except Exception as e:
        print(f"❌ Grades table not found: {e}")
        return []

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
            # Determine course type (AP vs Regular) — AP classes start with "AP"
            course_type = "AP" if course[:2].upper() == "AP" else "Regular"
        except Exception as e:
            print(f"⚠️  Couldn't read course name for row {i}: {e}")
            continue

        # Prefer grade links: the grades are stored in <a class="bold"> inside <td>
        grades_links = row.locator('td a.bold')
        gl_count = await grades_links.count()
        q1_norm = None
        o2_norm = None
        y1_norm = None

        if gl_count >= 2:
            # Best case: inner_text returns two lines: letter and numeric
            try:
                raw_q1 = (await grades_links.nth(0).inner_text()).strip()
                parts = [p.strip() for p in raw_q1.splitlines() if p.strip()]
                if len(parts) >= 2:
                    q1_norm = {"letter": parts[0], "numeric": int(parts[-1])}
                else:
                    q1_norm = normalize_grade(" ".join(parts))
            except Exception as e:
                print(f"⚠️  Error parsing Q1 from a.bold for {course}: {e}")

            try:
                raw_o2 = (await grades_links.nth(1).inner_text()).strip()
                parts = [p.strip() for p in raw_o2.splitlines() if p.strip()]
                if len(parts) >= 2:
                    o2_norm = {"letter": parts[0], "numeric": int(parts[-1])}
                else:
                    o2_norm = normalize_grade(" ".join(parts))
            except Exception as e:
                print(f"⚠️  Error parsing O2 from a.bold for {course}: {e}")

        # Try direct Y1 from the third grade link if present
        if y1_norm is None and gl_count >= 3:
            try:
                raw_y1 = (await grades_links.nth(2).inner_text()).strip()
                parts = [p.strip() for p in raw_y1.splitlines() if p.strip()]
                if len(parts) >= 2:
                    y1_norm = {"letter": parts[0], "numeric": int(parts[-1])}
                else:
                    y1_norm = normalize_grade(" ".join(parts))
            except Exception as e:
                print(f"⚠️  Error parsing Y1 from a.bold[n=2] for {course}: {e}")

        # Fallback: try to extract Y1 from the last <td> in the row (best-effort)
        if y1_norm is None:
            try:
                tds = row.locator('td')
                td_count = await tds.count()
                if td_count:
                    last_td = tds.nth(td_count - 1)
                    # Prefer a.link if present
                    try:
                        if await last_td.locator('a.bold').count() > 0:
                            raw_y1 = (await last_td.locator('a.bold').nth(0).inner_text()).strip()
                        else:
                            aria = await last_td.get_attribute('aria-label')
                            raw_y1 = aria.strip() if aria and re.search(r'\d', aria) else (await last_td.inner_text()).strip()
                    except Exception:
                        raw_y1 = (await last_td.inner_text()).strip()

                    parts = [p.strip() for p in raw_y1.splitlines() if p.strip()]
                    if len(parts) >= 2:
                        y1_norm = {"letter": parts[0], "numeric": int(parts[-1])}
                    else:
                        y1_norm = normalize_grade(" ".join(parts))
            except Exception as e:
                print(f"⚠️  Error parsing Y1 for {course}: {e}")

        # Fallback: use ARIA or cell text to find numeric grade if any approach fails
        if o2_norm is None or y1_norm is None:
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

            q1_norm = q1_norm or (normalize_grade(grade_texts[0]) if len(grade_texts) >= 1 else None)
            o2_norm = o2_norm or (normalize_grade(grade_texts[1]) if len(grade_texts) >= 2 else None)
            # Prefer the positional 3rd numeric grade for Y1, else fallback to the last numeric value
            if len(grade_texts) >= 3:
                y1_norm = y1_norm or normalize_grade(grade_texts[2])
            else:
                y1_norm = y1_norm or (normalize_grade(grade_texts[-1]) if len(grade_texts) >= 1 else None)

        if o2_norm is None:
            print(f"⚠️  Skipping {course!r}: O2 grade not found or couldn't parse (found link_count={gl_count})")
            continue

        results.append({
            "course": course,
            "type": course_type,
            "grades": {"Q1": q1_norm, "O2": o2_norm, "Y1": y1_norm}
        })

    return results


async def scrape_q2_assignments(page: Page) -> list:
    """
    Click into each course's O2/Q2 detail link and scrape assignment-level data.

    Returns a list of dicts:
    [
      {
        "course": "AP English",
        "quarter": "Q2",
        "letter_grade": "B-",
        "numeric_grade": 80,
        "assignments": [ {name, earned, possible, percent, category}, ... ]
      },
      ...
    ]
    """
    print("🔎 Scraping assignment details for Q2 (O2)...")
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

        # Course name
        try:
            course_raw = (await row.locator('td.table-element-text-align-start').inner_text()).strip()
            course = course_raw.splitlines()[0].strip()
        except Exception as e:
            print(f"⚠️  Couldn't read course name for detail row {i}: {e}")
            continue

        # Find the O2/Q2 grade link (2nd bold link)
        grades_links = row.locator('td a.bold')
        if await grades_links.count() < 2:
            print(f"⚠️  No O2 link for {course}, skipping detail scrape")
            continue

        # Get letter and numeric grade from the link text
        try:
            raw_o2 = (await grades_links.nth(1).inner_text()).strip()
            parts = [p.strip() for p in raw_o2.splitlines() if p.strip()]
            letter = parts[0] if parts else None
            numeric = int(parts[-1]) if len(parts) >= 1 and re.search(r'\d', parts[-1]) else None
        except Exception as e:
            print(f"⚠️  Couldn't parse O2 grade for {course}: {e}")
            letter = None
            numeric = None

        # Click into the detail page (handle same-tab navigation or popup)
        detail_page = None
        try:
            # Prefer navigation in same tab
            try:
                async with page.expect_navigation(timeout=7000):
                    await grades_links.nth(1).click()
                detail_page = page
            except Exception:
                # Maybe opens a popup
                try:
                    async with page.context.expect_page(timeout=7000) as popup_info:
                        await grades_links.nth(1).click()
                    detail_page = await popup_info.value
                    await detail_page.wait_for_load_state('networkidle')
                except Exception:
                    # Fallback: click and wait for networkidle
                    await grades_links.nth(1).click()
                    await page.wait_for_load_state('networkidle')
                    detail_page = page
        except Exception as e:
            print(f"⚠️  Clicking into O2 for {course} failed: {e}")
            continue

        # Scrape assignments on detail_page using the score table
        assignments = []
        try:
            await detail_page.wait_for_selector('table#scoreTable tbody', timeout=7000)
            assign_rows = detail_page.locator('table#scoreTable tbody tr[role="row"]')
            ar_count = await assign_rows.count()
            for j in range(ar_count):
                ar = assign_rows.nth(j)

                # If we hit the footer row that says "Assignment Score Or Flag Last Updated" stop early
                try:
                    row_text = (await ar.inner_text() or '').strip()
                except Exception:
                    row_text = ''
                if 'Assignment Score Or Flag Last Updated' in row_text:
                    print(f"ℹ️ Found footer row for {course}; ending assignment scrape for this class")
                    break

                # Due date, category, name
                try:
                    due = (await ar.locator('td').nth(0).inner_text()).strip()
                except Exception:
                    due = None

                try:
                    cat = (await ar.locator('td.categorycol').inner_text()).strip()
                    # category might include extra whitespace
                    cat = ' '.join(cat.split()) if cat else None
                except Exception:
                    cat = None

                try:
                    name = (await ar.locator('td.assignmentcol span.ng-binding').inner_text()).strip()
                except Exception:
                    name = (await ar.locator('td.assignmentcol').inner_text()).strip()

                # Flags: check codeCol cells for screen_readers_only text
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

                # Find the score/percent/grade cells by scanning tds
                tds = ar.locator('td')
                tc = await tds.count()
                score_text = ''
                percent_text = ''
                grade_text = ''
                # find index of score cell
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
                    # percent is usually next cell
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

                # Normalize score_text / percent_text
                earned = None
                possible = None
                percent = None
                # score like '100/100' or '--/100' or '(27.5/50)'
                try:
                    s = score_text.replace('(', '').replace(')', '').replace('\u00a0', ' ')
                    if '/' in s:
                        left, right = [p.strip() for p in s.split('/', 1)]
                        # remove non-number chars
                        left_num = re.search(r"(\d+(?:\.\d+)?)", left)
                        right_num = re.search(r"(\d+(?:\.\d+)?)", right)
                        if left_num:
                            earned = float(left_num.group(1))
                        if right_num:
                            possible = float(right_num.group(1))
                        if earned is not None and possible is not None and possible != 0:
                            percent = round((earned / possible) * 100, 2)
                    # percent column may contain a number
                    if percent is None and percent_text:
                        m = re.search(r"(\d+(?:\.\d+)?)", percent_text)
                        if m:
                            percent = float(m.group(1))
                            possible = possible or 100.0
                            earned = earned or round((percent / 100.0) * possible, 2) if possible else earned
                    # fallback: grade_text may contain a letter, ignore
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
            "quarter": "Q2",
            "letter_grade": letter,
            "numeric_grade": numeric,
            "assignments": assignments
        })

        # Close popup or go back
        try:
            if detail_page is not None and detail_page != page:
                await detail_page.close()
            else:
                await page.go_back()
                await page.wait_for_selector('tbody tr[id^="ccid_"]', timeout=10000)
        except Exception:
            pass

    return details


async def main():
    """
    Main function to run the scraper.
    """
    # Get credentials from user
    email, username, password = get_credentials()
    
    print(f"\n Starting browser automation...")
    print(f" Target: {POWERSCHOOL_URL}\n")
    
    # Start Playwright
    async with async_playwright() as p:
        # Launch browser (headless=False so you can see it)
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Perform login using Playwright's auto-wait features
        success = await login_with_google_sso(page, email, username, password)
        
        if success:
            print("\n" + "="*60)
            print(" LOGIN COMPLETE!")
            print("="*60)

            # Scrape grades and write to JSON
            grades = await scrape_grades(page)
            if grades:
                # Scrape Q2 assignment details and merge into grades
                q2_details = await scrape_q2_assignments(page)
                # Do not write a separate class_scores.json (we store details inside grades.json only)
                # Merge by course name into grades.json
                for g in grades:
                    for d in q2_details:
                        if g.get('course') and d.get('course') and g['course'].lower() == d['course'].lower():
                            g.setdefault('assignments', {})['Q2'] = d.get('assignments', [])
                            # also include the numeric/letter as a quick sanity
                            g['grades']['O2'] = g['grades'].get('O2') or {'letter': d.get('letter_grade'), 'numeric': d.get('numeric_grade')}
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
                except Exception as e:
                    print(f"❌ Failed to write grades to {out_path}: {e}")
            else:
                print("⚠️ No grades extracted.")

            print("\n You can now explore PowerSchool in the browser window.")
            print(" Press Ctrl+C in the terminal when you're done.\n")
            
            # Keep browser open for exploration
            try:
                await page.wait_for_timeout(300000)  # Wait 5 minutes max
            except KeyboardInterrupt:
                print("\n🛑 Closing browser...")
        else:
            print("\n❌ Login failed. Check the browser window for details.")
            print("   Press Ctrl+C to close.\n")
            await page.wait_for_timeout(60000)  # Wait 1 minute before closing
        
        await browser.close()
        print(" Browser closed. Automation complete!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Automation stopped by user.")
        sys.exit(0)
