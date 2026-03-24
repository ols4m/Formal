"""
PowerSchool Grade Scraper
A modular web scraper for extracting academic data from PowerSchool portals.
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from playwright.async_api import async_playwright, Page, Browser
import re


@dataclass
class Assignment:
    """Represents a single assignment with all relevant grading data."""
    name: str
    type: str  # homework, classwork, test, project, etc.
    score_received: Optional[float]
    total_points: Optional[float]
    percentage: Optional[float]
    due_date: Optional[str]  # ISO format: YYYY-MM-DD
    submission_date: Optional[str]
    category: Optional[str]  # For weighted grade calculations
    status: str  # completed, missing, exempt, pending
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ClassGrades:
    """Represents all grade data for a single class."""
    class_name: str
    teacher: str
    period: Optional[str]
    current_grade: Optional[str]
    current_percentage: Optional[float]
    assignments: List[Assignment]
    grading_categories: Dict[str, float]  # category: weight
    scrape_timestamp: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "class_name": self.class_name,
            "teacher": self.teacher,
            "period": self.period,
            "current_grade": self.current_grade,
            "current_percentage": self.current_percentage,
            "assignments": [a.to_dict() for a in self.assignments],
            "grading_categories": self.grading_categories,
            "scrape_timestamp": self.scrape_timestamp
        }


class PowerSchoolScraper:
    """Handles PowerSchool login and data extraction."""
    
    def __init__(self, base_url: str, headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        
    async def initialize(self):
        """Initialize the browser and page."""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(headless=self.headless)
        self.page = await self.browser.new_page()
        
    async def login(self, username: str, password: str) -> bool:
        """
        Login to PowerSchool portal.
        
        Args:
            username: Student username
            password: Student password
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            await self.page.goto(self.base_url, wait_until="networkidle")
            
            # Wait for login form (adjust selectors based on your PowerSchool instance)
            await self.page.wait_for_selector('input[name="account"]', timeout=10000)
            
            # Fill in credentials
            await self.page.fill('input[name="account"]', username)
            await self.page.fill('input[name="pw"]', password)
            
            # Submit login
            await self.page.click('input[type="submit"]')
            
            # Wait for navigation and verify login success
            await self.page.wait_for_load_state("networkidle")
            
            # Check if login was successful (look for a known element after login)
            try:
                await self.page.wait_for_selector('.student-name, #userName', timeout=5000)
                print("✓ Login successful")
                return True
            except:
                print("✗ Login failed - credentials may be incorrect")
                return False
                
        except Exception as e:
            print(f"✗ Login error: {str(e)}")
            return False
    
    async def get_class_links(self) -> List[Dict[str, str]]:
        """
        Extract links and metadata for all classes.
        
        Returns:
            List of dicts with class_name, teacher, period, and url
        """
        classes = []
        
        try:
            # Navigate to grades page if not already there
            await self.page.wait_for_selector('.class-row, .classRow, a.bold', timeout=10000)
            
            # Extract class information (adjust selectors for your PowerSchool version)
            class_elements = await self.page.query_selector_all('.class-row, .classRow')
            
            for element in class_elements:
                try:
                    # Extract class name
                    name_elem = await element.query_selector('a.bold, .class-name')
                    class_name = await name_elem.inner_text() if name_elem else "Unknown Class"
                    
                    # Extract teacher name
                    teacher_elem = await element.query_selector('.teacher-name, td:nth-child(3)')
                    teacher = await teacher_elem.inner_text() if teacher_elem else "Unknown Teacher"
                    
                    # Extract period
                    period_elem = await element.query_selector('.period, td:nth-child(2)')
                    period = await period_elem.inner_text() if period_elem else None
                    
                    # Get link to detailed grades
                    link_elem = await element.query_selector('a[href*="scores.html"], a.bold')
                    if link_elem:
                        href = await link_elem.get_attribute('href')
                        url = href if href.startswith('http') else f"{self.base_url.rstrip('/')}/{href.lstrip('/')}"
                        
                        classes.append({
                            'class_name': class_name.strip(),
                            'teacher': teacher.strip(),
                            'period': period.strip() if period else None,
                            'url': url
                        })
                except Exception as e:
                    print(f"Warning: Could not extract data for one class: {e}")
                    continue
            
            print(f"✓ Found {len(classes)} classes")
            return classes
            
        except Exception as e:
            print(f"✗ Error getting class links: {str(e)}")
            return []
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[str]:
        """Parse date string to ISO format (YYYY-MM-DD)."""
        if not date_str or date_str.strip() in ['--', '', 'N/A']:
            return None
        
        try:
            # Try common date formats
            for fmt in ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%b %d, %Y']:
                try:
                    dt = datetime.strptime(date_str.strip(), fmt)
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            return None
        except:
            return None
    
    def _parse_score(self, score_str: Optional[str]) -> tuple[Optional[float], Optional[float]]:
        """
        Parse score string like '85/100' or '42.5 / 50'.
        
        Returns:
            Tuple of (score_received, total_points)
        """
        if not score_str or score_str.strip() in ['--', '', 'N/A']:
            return None, None
        
        try:
            # Match pattern like "85/100" or "42.5 / 50"
            match = re.match(r'([\d.]+)\s*/\s*([\d.]+)', score_str.strip())
            if match:
                received = float(match.group(1))
                total = float(match.group(2))
                return received, total
            
            # Try just a single number
            try:
                return float(score_str.strip()), None
            except:
                return None, None
                
        except Exception:
            return None, None
    
    def _categorize_assignment(self, name: str, category: str = None) -> str:
        """
        Infer assignment type from name or category.
        
        Returns:
            One of: homework, classwork, test, quiz, project, lab, other
        """
        if category:
            category_lower = category.lower()
            if 'test' in category_lower or 'exam' in category_lower:
                return 'test'
            elif 'quiz' in category_lower:
                return 'quiz'
            elif 'homework' in category_lower or 'hw' in category_lower:
                return 'homework'
            elif 'project' in category_lower:
                return 'project'
            elif 'lab' in category_lower:
                return 'lab'
            elif 'classwork' in category_lower or 'cw' in category_lower:
                return 'classwork'
        
        name_lower = name.lower()
        if 'test' in name_lower or 'exam' in name_lower:
            return 'test'
        elif 'quiz' in name_lower:
            return 'quiz'
        elif 'homework' in name_lower or 'hw' in name_lower:
            return 'homework'
        elif 'project' in name_lower:
            return 'project'
        elif 'lab' in name_lower:
            return 'lab'
        elif 'classwork' in name_lower or 'cw' in name_lower:
            return 'classwork'
        
        return 'other'
    
    async def scrape_class_assignments(self, class_info: Dict[str, str]) -> ClassGrades:
        """
        Scrape all assignments for a specific class.
        
        Args:
            class_info: Dictionary with class metadata and URL
            
        Returns:
            ClassGrades object with all assignment data
        """
        print(f"Scraping: {class_info['class_name']}")
        
        try:
            # Navigate to class grades page
            await self.page.goto(class_info['url'], wait_until="networkidle")
            await self.page.wait_for_selector('.assignment-row, .assignmentRow, table', timeout=10000)
            
            # Extract current grade
            current_grade = None
            current_percentage = None
            try:
                grade_elem = await self.page.query_selector('.current-grade, .grade')
                if grade_elem:
                    grade_text = await grade_elem.inner_text()
                    # Extract letter grade and percentage
                    match = re.search(r'([A-F][+-]?)\s*(\d+\.?\d*)%?', grade_text)
                    if match:
                        current_grade = match.group(1)
                        current_percentage = float(match.group(2))
            except:
                pass
            
            # Extract grading categories and weights
            grading_categories = {}
            try:
                category_elements = await self.page.query_selector_all('.category-row, .categoryRow')
                for cat_elem in category_elements:
                    cat_name = await cat_elem.query_selector('.category-name')
                    cat_weight = await cat_elem.query_selector('.category-weight')
                    
                    if cat_name and cat_weight:
                        name = await cat_name.inner_text()
                        weight_text = await cat_weight.inner_text()
                        weight_match = re.search(r'(\d+)', weight_text)
                        if weight_match:
                            grading_categories[name.strip()] = float(weight_match.group(1)) / 100
            except:
                pass
            
            # Extract assignments
            assignments = []
            assignment_rows = await self.page.query_selector_all('.assignment-row, .assignmentRow, tr.assignment')
            
            for row in assignment_rows:
                try:
                    # Assignment name
                    name_elem = await row.query_selector('.assignment-name, td:nth-child(1)')
                    name = await name_elem.inner_text() if name_elem else "Unknown Assignment"
                    
                    # Category
                    category_elem = await row.query_selector('.category, td.category')
                    category = await category_elem.inner_text() if category_elem else None
                    
                    # Score
                    score_elem = await row.query_selector('.score, td.score')
                    score_text = await score_elem.inner_text() if score_elem else None
                    score_received, total_points = self._parse_score(score_text)
                    
                    # Calculate percentage
                    percentage = None
                    if score_received is not None and total_points and total_points > 0:
                        percentage = round((score_received / total_points) * 100, 2)
                    
                    # Due date
                    due_elem = await row.query_selector('.due-date, td.due')
                    due_text = await due_elem.inner_text() if due_elem else None
                    due_date = self._parse_date(due_text)
                    
                    # Submission date (if available)
                    sub_elem = await row.query_selector('.submission-date, td.submitted')
                    sub_text = await sub_elem.inner_text() if sub_elem else None
                    submission_date = self._parse_date(sub_text)
                    
                    # Determine status
                    status = 'completed'
                    if score_received is None:
                        status = 'missing' if due_date else 'pending'
                    
                    assignment = Assignment(
                        name=name.strip(),
                        type=self._categorize_assignment(name, category),
                        score_received=score_received,
                        total_points=total_points,
                        percentage=percentage,
                        due_date=due_date,
                        submission_date=submission_date,
                        category=category.strip() if category else None,
                        status=status
                    )
                    
                    assignments.append(assignment)
                    
                except Exception as e:
                    print(f"  Warning: Could not parse assignment row: {e}")
                    continue
            
            print(f"  ✓ Found {len(assignments)} assignments")
            
            return ClassGrades(
                class_name=class_info['class_name'],
                teacher=class_info['teacher'],
                period=class_info['period'],
                current_grade=current_grade,
                current_percentage=current_percentage,
                assignments=assignments,
                grading_categories=grading_categories,
                scrape_timestamp=datetime.now().isoformat()
            )
            
        except Exception as e:
            print(f"  ✗ Error scraping class: {str(e)}")
            # Return empty class data rather than failing completely
            return ClassGrades(
                class_name=class_info['class_name'],
                teacher=class_info['teacher'],
                period=class_info['period'],
                current_grade=None,
                current_percentage=None,
                assignments=[],
                grading_categories={},
                scrape_timestamp=datetime.now().isoformat()
            )
    
    async def scrape_all_classes(self) -> List[ClassGrades]:
        """
        Scrape all classes and return complete grade data.
        
        Returns:
            List of ClassGrades objects for all classes
        """
        class_links = await self.get_class_links()
        all_grades = []
        
        for class_info in class_links:
            grades = await self.scrape_class_assignments(class_info)
            all_grades.append(grades)
            
            # Brief pause to avoid overwhelming the server
            await asyncio.sleep(1)
        
        return all_grades
    
    async def close(self):
        """Close the browser."""
        if self.browser:
            await self.browser.close()


async def scrape_powerschool(
    base_url: str,
    username: str,
    password: str,
    output_file: str = "gradebook_data.json",
    headless: bool = True
) -> bool:
    """
    Main function to scrape PowerSchool and save to JSON.
    
    Args:
        base_url: PowerSchool portal URL
        username: Student username
        password: Student password
        output_file: Path to output JSON file
        headless: Run browser in headless mode
        
    Returns:
        True if scraping successful, False otherwise
    """
    scraper = PowerSchoolScraper(base_url, headless=headless)
    
    try:
        print("Initializing browser...")
        await scraper.initialize()
        
        print("Logging in...")
        if not await scraper.login(username, password):
            return False
        
        print("\nScraping all classes...")
        all_grades = await scraper.scrape_all_classes()
        
        # Convert to JSON-serializable format
        output_data = {
            "scrape_date": datetime.now().isoformat(),
            "student_username": username,
            "classes": [grades.to_dict() for grades in all_grades]
        }
        
        # Save to file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Data saved to {output_file}")
        print(f"✓ Scraped {len(all_grades)} classes with {sum(len(g.assignments) for g in all_grades)} total assignments")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Scraping failed: {str(e)}")
        return False
        
    finally:
        await scraper.close()


# Example usage
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    # Load credentials from environment variables (recommended for security)
    load_dotenv()
    
    POWERSCHOOL_URL = os.getenv("POWERSCHOOL_URL", "https://your-school.powerschool.com")
    USERNAME = os.getenv("STUDENT_USERNAME", "your_username")
    PASSWORD = os.getenv("STUDENT_PASSWORD", "your_password")
    
    # Run the scraper
    asyncio.run(scrape_powerschool(
        base_url=POWERSCHOOL_URL,
        username=USERNAME,
        password=PASSWORD,
        output_file="gradebook_data.json",
        headless=True  # Set to False to see the browser
    ))
