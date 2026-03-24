"""
Classroom Scraper - Login helper (Google SSO) tailored for Classroom project

This file provides a reusable `login_with_google_sso` function and a small CLI
that demonstrates how to use it. Replace `CLASSROOM_URL` with your target URL.
"""

import asyncio
import sys
import json
import os
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page
from bs4 import BeautifulSoup

# ==========================================
# CONFIG
# ==========================================
CLASSROOM_URL = "https://accounts.google.com/v3/signin/identifier?continue=https%3A%2F%2Fclassroom.google.com%2Fu%2F1%2F&dsh=S-753428510%3A1767229623173503&emr=1&followup=https%3A%2F%2Fclassroom.google.com%2Fu%2F1%2F&ifkv=Ac2yZaWIPYfvdmN1lJnGFG16T9CAfM2INZldwt173-yzQjkxpzwX3iTl39ccXVNbUkd9twTIpG4j&passive=1209600&service=classroom&flowName=GlifWebSignIn&flowEntry=ServiceLogin"

os.makedirs('output', exist_ok=True)
JSON_PATH = 'output/assignments.json'


# ==========================================
# PART 1: LOGIN SYSTEM (Unchanged)
# ==========================================

def get_credentials():
    """Prompt for credentials and return (email, username, password)."""
    print('=' * 60)
    print('CLASSROOM SCRAPER - LOGIN')
    print('=' * 60)
    email = input('School Email: ').strip()
    username = input('Username: ').strip()
    password = input('Password: ').strip()
    if not email or not username or not password:
        print('\n❌ Error: all credentials are required')
        sys.exit(1)
    return email, username, password


async def login_with_google_sso(page: Page, email: str, username: str, password: str, target_url: str = CLASSROOM_URL) -> bool:
    """Login helper for Google SSO flows.

    This mirrors the logic used in `powerschool_scraper.py` but is tailored for
    classroom flows (same-page sign-in or popup).

    Returns True on success, False otherwise.
    """
    try:
        print('🌐 Navigating to target login page...')
        await page.goto(target_url)

        print('⏳ Waiting for Google sign-in (inline or popup)...')
        login_page = None

        # First try: Google sign-in elements appear on the same page
        try:
            await page.wait_for_selector('#identifierId', timeout=5000)
            login_page = page
            print('🟢 Google sign-in on current page')
        except Exception:
            # Second try: popup opens
            try:
                async with page.context.expect_page(timeout=5000) as popup_info:
                    pass
                login_page = await popup_info.value
                print('🪟 Google sign-in popup detected')
            except Exception:
                # Third try: check URL
                if 'accounts.google' in page.url or 'google.com' in page.url:
                    login_page = page
                    print('🟠 Detected Google sign-in by URL')
                else:
                    raise Exception('Google sign-in not detected')

        # Enter email
        print('📧 Entering email...')
        await login_page.locator('#identifierId').fill(email)
        await login_page.locator('#identifierNext').click()

        # Username (some school flows ask for a school username)
        try:
            await login_page.locator('input[name="username"]').fill(username)
            await login_page.locator('input[name="username"]').press('Enter')
            print('👤 Entered username')
        except Exception:
            # If the school does not require the extra username step, continue
            pass

        # Password
        print('🔑 Entering password...')
        await login_page.locator('input[name="password"]').fill(password)
        await login_page.locator('input[name="password"]').press('Enter')

        # Handle potential verify/continue button
        try:
            buttons = login_page.locator('button')
            if await buttons.count() > 0:
                await buttons.nth(0).click()
        except Exception:
            pass

        # Close popup if used
        if login_page != page:
            try:
                await login_page.close()
            except Exception:
                pass

        # Wait for Google Classroom dashboard
        print('⏳ Waiting for Google Classroom dashboard...')
        try:
            # 1. Check if we are redirected to classroom.google.com
            await page.wait_for_url('**classroom.google.com**', timeout=20000)
            
            # 2. Wait for common dashboard elements (Main Menu, specific aria labels, or role="main")
            #    Google Classroom typically has a "Google Classroom" link or specific header elements.
            await page.wait_for_selector(
                '[aria-label="Main menu"], [aria-label="Google Classroom"], [role="main"], [aria-label="Create or join a class"]',
                timeout=15000
            )
            
            print('✅ Login successful! Reached Google Classroom dashboard.')
            return True
            
        except Exception as e:
            # Double check URL just in case the selector failed but we are on the right domain
            if "classroom.google.com" in page.url:
                print('✅ URL verified as Google Classroom - treating as success.')
                return True
                
            print(f'⚠️ Could not verify dashboard load: {e}')
            return False

    except Exception as e:
        print(f'❌ Login failed: {e}')
        return False


# ==========================================
# PART 2: DASHBOARD PARSER
# ==========================================

def parse_due_date(due_text: str):
    """Convert relative due dates to absolute YYYY-MM-DD."""
    if not due_text:
        return None
    due_text = due_text.lower().replace('due', '').strip()
    if not due_text:
        return None
    today = datetime.now()

    # Absolute date: MM/DD or MM/DD/YY
    date_match = re.search(r'(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?', due_text)
    if date_match:
        month, day, year = date_match.groups()
        month, day = int(month), int(day)
        year = int(year) if year else today.year
        if year < 100:
            year += 2000
        # Handle year rollover
        if year == today.year and (month < today.month or (month == today.month and day < today.day)):
            year += 1
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            pass

    # Relative days
    days_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
        'tomorrow': (today.weekday() + 1) % 7,
        'today': today.weekday()
    }
    for day_name, day_num in days_map.items():
        if day_name in due_text:
            current_weekday = today.weekday()
            days_until = (day_num - current_weekday + 7) % 7
            if days_until == 0:
                days_until = 7
            return (today + timedelta(days=days_until)).date().isoformat()

    # "in X days"
    in_days_match = re.search(r'in\s+(\d+)\s+day', due_text)
    if in_days_match:
        return (today + timedelta(days=int(in_days_match.group(1)))).date().isoformat()

    return None


def extract_assignment_from_dashboard_card(html: str):
    """Extract assignment data from a single course card HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    
    # Course metadata
    course_name_elem = soup.find('div', class_='ScpeUc')
    course_name = course_name_elem.get_text(strip=True) if course_name_elem else "Unknown"

    teacher_elem = soup.find('div', class_='jJIbcc')
    teacher = teacher_elem.get_text(strip=True) if teacher_elem else "Unknown"

    course_card = soup.find('li', class_='gHz6xd')
    course_id = course_card.get('data-draggable-item-id') if course_card else None

    # Due header
    due_header = soup.find('h2', class_='COwiKd')
    if not due_header:
        return None
    due_text = due_header.get_text(strip=True)

    # Assignment link (try container first, then fallback)
    assignment_link = None
    container = soup.find('div', class_='Txjvk') or soup.find('div', class_='AX2up')
    if container:
        assignment_link = container.find('a')
    if not assignment_link:
        assignment_link = soup.find('a', class_='ARTZne')
    if not assignment_link:
        return None

    assignment_full_text = assignment_link.get_text(strip=True)
    
    # Remove time prefix if present (e.g., "11:59 PM – Essay")
    title_match = re.split(r'[–—\-]\s*', assignment_full_text, 1)
    raw_title = title_match[1].strip() if len(title_match) > 1 else assignment_full_text.strip()
    
    href = assignment_link.get('href', '')
    assignment_id_match = re.search(r'/a/([^/]+)/details', href)
    assignment_id = assignment_id_match.group(1) if assignment_id_match else None

    due_date_absolute = parse_due_date(due_text)

    return {
        'source': 'google_classroom_dashboard',
        'timestamp': datetime.now().isoformat(),
        'course': {'id': course_id, 'name': course_name, 'teacher': teacher},
        'assignment': {
            'id': assignment_id,
            'raw_title': raw_title,
            'full_text': assignment_full_text,
            'due_raw': due_text,
            'due_absolute': due_date_absolute,
            'points': None,
            'url': f"https://classroom.google.com{href}" if href.startswith('/') else href
        },
        'html_snippet': html[:500]
    }


def extract_all_assignments_from_dashboard(html: str):
    """Extract ALL assignments from the full dashboard HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    course_cards = soup.find_all('li', class_='gHz6xd')
    assignments = []
    for card in course_cards:
        data = extract_assignment_from_dashboard_card(str(card))
        if data:
            assignments.append(data)
    return assignments


def extract_details_from_assignment_page(html: str):
    """
    Extract detailed assignment info from the assignment detail page HTML.
    
    Returns dict with:
    - category: HW, Classwork, Test, Quiz, etc.
    - posted_date: When the assignment was posted
    - due_date_full: Full due date with year (e.g., "Due Jan 6, 2026")
    - points: Max points possible
    - instructions: The assignment instructions/description
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    details = {
        'category': None,
        'posted_date': None,
        'due_date_full': None,
        'points': None,
        'instructions': None
    }
    
    # 1. TITLE (for verification)
    title_elem = soup.find('h1', class_='fOvfyc')
    if title_elem:
        details['title'] = title_elem.get_text(strip=True)
    
    # 2. METADATA ROW: Teacher • Date Posted • Quarter
    # Structure: <div class="rec5Nb cSyPgb QRiHXd"> contains multiple <div class="YVvGBb">
    metadata_row = soup.find('div', class_='rec5Nb')
    if metadata_row:
        meta_items = metadata_row.find_all('div', class_='YVvGBb')
        # Typically: [Teacher Name, Posted Date, Quarter]
        if len(meta_items) >= 2:
            details['posted_date'] = meta_items[1].get_text(strip=True)  # e.g., "Dec 18"
        if len(meta_items) >= 3:
            details['quarter'] = meta_items[2].get_text(strip=True)  # e.g., "Quarter 2"
    
    # 3. CATEGORY + POINTS ROW
    # Structure: <div class="W4hhKd"> contains:
    #   - <div class="CzuI5c"> with category and points
    #   - <div class="asQXV BjHIWe"> with full due date
    category_row = soup.find('div', class_='W4hhKd')
    if category_row:
        # Category: <div class="YVvGBb HM4nYe">HW</div>
        category_elem = category_row.find('div', class_='HM4nYe')
        if category_elem:
            details['category'] = category_elem.get_text(strip=True)
        
        # Points: Look for <div jscontroller="teDhve">100 points</div>
        # The structure is: <div class="CzuI5c"> -> <div class="YVvGBb"> -> <div jscontroller="teDhve">100 points</div>
        # Direct approach: find the div with jscontroller="teDhve" or ANY div containing "points"
        points_elem = category_row.find('div', attrs={'jscontroller': 'teDhve'})
        if points_elem:
            text = points_elem.get_text(strip=True)
            points_match = re.search(r'(\d+)', text)
            if points_match:
                details['points'] = int(points_match.group(1))
        else:
            # Fallback: search entire category_row for any text with "points"
            all_text = category_row.get_text()
            if 'points' in all_text.lower():
                points_match = re.search(r'(\d+)\s*points', all_text.lower())
                if points_match:
                    details['points'] = int(points_match.group(1))
        
        # Full Due Date: <div class="asQXV BjHIWe">Due Jan 6, 2026</div>
        due_elem = category_row.find('div', class_='BjHIWe')
        if due_elem:
            details['due_date_full'] = due_elem.get_text(strip=True)
    
    # 4. INSTRUCTIONS
    # Structure: <div class="nGi02b tLDEHd j70YMc">...<span>instructions</span></div>
    instructions_elem = soup.find('div', class_='nGi02b')
    if instructions_elem:
        # Get all text, replacing <br> with newlines
        for br in instructions_elem.find_all('br'):
            br.replace_with('\n')
        details['instructions'] = instructions_elem.get_text(strip=True)[:500]  # Limit length
    
    return details


async def scrape_assignment_details(page: Page, assignment_url: str):
    """
    Navigate to an assignment detail page and extract full details.
    Returns the details dict or None if failed.
    """
    try:
        print(f'   📄 Opening assignment details...')
        
        # Navigate to the assignment detail page
        await page.goto(assignment_url)
        
        # Wait for the detail page to load (look for the title or category elements)
        try:
            await page.wait_for_selector('h1.fOvfyc, div.W4hhKd', timeout=10000)
        except Exception:
            print(f'   ⚠️ Detail page did not load properly')
            return None
        
        # Get the page HTML
        html = await page.content()
        
        # Parse it
        details = extract_details_from_assignment_page(html)
        
        return details
        
    except Exception as e:
        print(f'   ❌ Error scraping details: {e}')
        return None

# ==========================================
# PART 3: NORMALIZER
# ==========================================

def normalize(raw: dict, details: dict = None):
    """Normalize raw extracted data for clean JSON output.
    
    Args:
        raw: The basic data from dashboard card
        details: Optional detailed data from assignment detail page
    """
    result = {
        'source': raw['source'],
        'timestamp': raw['timestamp'],
        'course_name': raw['course']['name'],
        'teacher': raw['course']['teacher'],
        'assignment_title': raw['assignment']['raw_title'],
        'due_raw': raw['assignment']['due_raw'],
        'due_date': raw['assignment']['due_absolute'],
        'possible_points': raw['assignment']['points'],
        'url': raw['assignment']['url'],
        # New detail fields (will be populated if details are provided)
        'category': None,
        'posted_date': None,
        'due_date_full': None,
        'quarter': None,
        'instructions': None
    }
    
    # Merge in details if provided
    if details:
        result['category'] = details.get('category')
        result['posted_date'] = details.get('posted_date')
        result['due_date_full'] = details.get('due_date_full')
        result['quarter'] = details.get('quarter')
        result['instructions'] = details.get('instructions')
        # Override possible_points if we got it from detail page (more reliable)
        if details.get('points'):
            result['possible_points'] = details.get('points')
    
    return result



# ==========================================
# PART 4: WRITER
# ==========================================

def write_assignment(data: dict):
    """Append assignment to JSON file (avoids duplicates)."""
    # Load existing data
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            db = {"assignments": []}
    else:
        db = {"assignments": []}
    
    # Dedup: check if assignment already exists (by title + course)
    exists = False
    for entry in db['assignments']:
        if (entry.get('assignment_title') == data.get('assignment_title') and
            entry.get('course_name') == data.get('course_name')):
            exists = True
            break
    
    if not exists:
        db['assignments'].append(data)
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved: {data.get('assignment_title', 'Unknown')}")
    else:
        print(f"ℹ️ (Already exists): {data.get('assignment_title', 'Unknown')}")


# ==========================================
# PART 5: WATCHER (MutationObserver)
# ==========================================

async def inject_dashboard_watcher(page: Page):
    """Inject JS MutationObserver to watch for changes in the dashboard cards."""
    await page.evaluate("""
    () => {
        window.__DASHBOARD_ASSIGNMENTS__ = [];
        const courseList = document.querySelector('ol[jsname="bN97Pc"]') || document.querySelector('ol') || document.body;
        if (!courseList) {
            console.warn('Could not find course list');
            return;
        }
        
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1 && node.classList && node.classList.contains('gHz6xd')) {
                        const dueHeader = node.querySelector('h2.COwiKd');
                        if (dueHeader && dueHeader.textContent.includes('Due')) {
                            window.__DASHBOARD_ASSIGNMENTS__.push(node.outerHTML);
                        }
                    }
                });
                
                // Also check for updates within existing cards
                if (mutation.type === 'childList') {
                    document.querySelectorAll('.gHz6xd').forEach((card) => {
                        const dueHeader = card.querySelector('h2.COwiKd');
                        const state = dueHeader?.textContent || 'none';
                        const lastState = card.__lastDueState || 'unknown';
                        if (state !== lastState && state.includes('Due')) {
                            window.__DASHBOARD_ASSIGNMENTS__.push(card.outerHTML);
                        }
                        card.__lastDueState = state;
                    });
                }
            });
        });
        
        observer.observe(courseList, { childList: true, subtree: true });
        
        // Initialize state for existing cards
        document.querySelectorAll('.gHz6xd').forEach((card) => {
            const dueHeader = card.querySelector('h2.COwiKd');
            card.__lastDueState = dueHeader?.textContent || 'none';
        });
        
        console.log('✅ Dashboard watcher injected');
    }
    """)


# ==========================================
# PART 6: MAIN ORCHESTRATION
# ==========================================

async def main():
    email, username, password = get_credentials()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 1. Login
        ok = await login_with_google_sso(page, email, username, password)
        if not ok:
            print('\n❌ Login did not complete successfully. Check the browser.')
            await browser.close()
            return

        # Store the dashboard URL for navigation
        dashboard_url = page.url

        # 2. Wait for course cards to appear
        print('\n🔎 Scanning dashboard for assignments...')
        try:
            await page.wait_for_selector('li.gHz6xd', timeout=15000)
        except Exception:
            print('⚠️ No course cards appeared immediately.')

        # Load existing assignments from DB to check for duplicates
        existing_assignments = set()
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                    for entry in db.get('assignments', []):
                        # Create a unique key: (course_name, title)
                        key = (entry.get('course_name', ''), entry.get('assignment_title', ''))
                        existing_assignments.add(key)
                print(f'   📚 {len(existing_assignments)} assignments already in database.')
            except:
                pass

        # 3. Initial Scrape - Collect all assignments first
        initial_cards = await page.query_selector_all('li.gHz6xd')
        print(f'   Found {len(initial_cards)} course cards.')
        
        # First pass: collect basic info from dashboard
        assignments_to_process = []
        for i, card in enumerate(initial_cards):
            html = await card.evaluate('node => node.outerHTML')
            data = extract_assignment_from_dashboard_card(html)
            if data:
                assignments_to_process.append(data)
                print(f'   📌 Found: {data["assignment"]["raw_title"]}')
            elif i == 0:
                # Save debug dump if first card has nothing
                with open('debug_card.html', 'w', encoding='utf-8') as f:
                    f.write(html)
        
        if len(assignments_to_process) == 0 and len(initial_cards) > 0:
            print('   (No "Due Soon" assignments found on existing cards)')
            print('   Saved first card HTML to debug_card.html for inspection.')
        
        # Second pass: click into each assignment for detailed info (ONLY if not already scraped)
        new_count = 0
        skip_count = 0
        print(f'\n📄 Processing {len(assignments_to_process)} assignments...')
        for i, data in enumerate(assignments_to_process):
            assignment_url = data['assignment']['url']
            title = data['assignment']['raw_title']
            course_name = data['course']['name']
            
            # Check if already in database
            key = (course_name, title)
            if key in existing_assignments:
                print(f'   [{i+1}/{len(assignments_to_process)}] ⏭️ Skipping (already scraped): {title}')
                skip_count += 1
                continue
            
            print(f'\n   [{i+1}/{len(assignments_to_process)}] {title}')
            
            if assignment_url and assignment_url.startswith('http'):
                # Navigate to detail page
                details = await scrape_assignment_details(page, assignment_url)
                
                if details:
                    print(f'      Category: {details.get("category", "N/A")}')
                    print(f'      Points: {details.get("points", "N/A")}')
                    print(f'      Posted: {details.get("posted_date", "N/A")}')
                    print(f'      Due: {details.get("due_date_full", "N/A")}')
                
                # Normalize with details
                normalized = normalize(data, details)
                
                # Navigate back to dashboard
                print('   ↩️ Returning to dashboard...')
                await page.goto(dashboard_url)
                try:
                    await page.wait_for_selector('li.gHz6xd', timeout=10000)
                except Exception:
                    print('   ⚠️ Dashboard reload took longer than expected')
            else:
                # No valid URL, normalize without details
                normalized = normalize(data, None)
            
            # Write to JSON
            write_assignment(normalized)
            new_count += 1
            
            # Add to existing set so we don't process again
            existing_assignments.add(key)

        print(f'\n✅ Done! New: {new_count}, Skipped: {skip_count}')


        # 4. Inject Watcher
        print('\n👀 Injecting real-time watcher...')
        await inject_dashboard_watcher(page)
        
        print('\n' + '~' * 50)
        print('   MONITORING ACTIVE. Press Ctrl+C to stop.')
        print('~' * 50 + '\n')

        # 5. Monitor Loop - KEEPS RUNNING until Ctrl+C
        check_count = 0
        try:
            while True:
                new_htmls = await page.evaluate("window.__DASHBOARD_ASSIGNMENTS__.splice(0)")
                for html in new_htmls:
                    data = extract_assignment_from_dashboard_card(html)
                    if data:
                        # For new assignments detected via watcher, also get details
                        assignment_url = data['assignment']['url']
                        if assignment_url and assignment_url.startswith('http'):
                            details = await scrape_assignment_details(page, assignment_url)
                            await page.goto(dashboard_url)
                            try:
                                await page.wait_for_selector('li.gHz6xd', timeout=10000)
                            except:
                                pass
                        else:
                            details = None
                        
                        normalized = normalize(data, details)
                        write_assignment(normalized)
                        print(f'🔔 NEW: {data["assignment"]["raw_title"]} (Category: {details.get("category") if details else "N/A"})')
                
                check_count += 1
                # Show status every 60 seconds (60 checks at 1 second each)
                if check_count % 60 == 0:
                    print(f'   ⏳ Still watching... ({check_count} checks)')
                
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print('\n🛑 Watcher stopped by user.')
        finally:
            await browser.close()
            print('✅ Browser closed. Assignments saved to:', JSON_PATH)



if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n🛑 Stopped by user')
