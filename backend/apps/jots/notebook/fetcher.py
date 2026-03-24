"""
Metadata fetcher for Resources App.
Auto-detects platform and fetches title, description, thumbnail from URLs.
"""

import re
from urllib.parse import urlparse, parse_qs

try:
    import requests
    from bs4 import BeautifulSoup
    FETCH_AVAILABLE = True
except ImportError:
    FETCH_AVAILABLE = False


# Platform detection patterns
PLATFORM_PATTERNS = {
    "youtube": [
        r"youtube\.com",
        r"youtu\.be",
    ],
    "instagram": [
        r"instagram\.com",
    ],
    "twitter": [
        r"twitter\.com",
        r"x\.com",
    ],
    "tiktok": [
        r"tiktok\.com",
    ],
    "github": [
        r"github\.com",
    ],
    "reddit": [
        r"reddit\.com",
    ],
    "medium": [
        r"medium\.com",
    ],
    "linkedin": [
        r"linkedin\.com",
    ],
}


def detect_platform(url):
    """Detect the platform from a URL."""
    url_lower = url.lower()

    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower):
                return platform

    return "website"


def detect_type(url, platform):
    """Detect the resource type based on URL and platform."""
    url_lower = url.lower()

    # Video platforms
    if platform in ["youtube", "tiktok"]:
        return "video"

    # Instagram can be video or image
    if platform == "instagram":
        if "/reel/" in url_lower or "/p/" in url_lower:
            return "video"
        return "link"

    # Check for common file extensions
    if re.search(r"\.(pdf)$", url_lower):
        return "document"
    if re.search(r"\.(jpg|jpeg|png|gif|webp)$", url_lower):
        return "image"
    if re.search(r"\.(mp4|webm|mov)$", url_lower):
        return "video"
    if re.search(r"\.(mp3|wav|ogg)$", url_lower):
        return "audio"

    return "link"


def get_youtube_thumbnail(url):
    """Extract YouTube video ID and return thumbnail URL."""
    parsed = urlparse(url)

    # Handle youtu.be short URLs
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/")
    else:
        # Handle youtube.com URLs
        query = parse_qs(parsed.query)
        video_id = query.get("v", [None])[0]

    if video_id:
        return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

    return None


def fetch_metadata(url):
    """
    Fetch metadata from a URL.
    Returns: dict with title, description, thumbnail, platform, type
    """
    platform = detect_platform(url)
    resource_type = detect_type(url, platform)

    result = {
        "title": None,
        "description": None,
        "thumbnail": None,
        "platform": platform,
        "type": resource_type,
        "url": url,
    }

    # Get YouTube thumbnail directly (doesn't require fetching the page)
    if platform == "youtube":
        result["thumbnail"] = get_youtube_thumbnail(url)

    # If requests/bs4 not available, return basic info
    if not FETCH_AVAILABLE:
        # Use URL as fallback title
        parsed = urlparse(url)
        result["title"] = parsed.path.split("/")[-1] or parsed.netloc
        return result

    # Fetch the page and extract metadata
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Try OpenGraph tags first (most reliable)
        og_title = soup.find("meta", property="og:title")
        og_desc = soup.find("meta", property="og:description")
        og_image = soup.find("meta", property="og:image")

        # Fallback to regular meta tags and title
        title_tag = soup.find("title")
        meta_desc = soup.find("meta", attrs={"name": "description"})

        # Set title
        if og_title and og_title.get("content"):
            result["title"] = og_title["content"]
        elif title_tag and title_tag.string:
            result["title"] = title_tag.string.strip()

        # Set description
        if og_desc and og_desc.get("content"):
            result["description"] = og_desc["content"]
        elif meta_desc and meta_desc.get("content"):
            result["description"] = meta_desc["content"]

        # Set thumbnail (only if not already set, e.g., YouTube)
        if not result["thumbnail"] and og_image and og_image.get("content"):
            result["thumbnail"] = og_image["content"]

    except Exception as e:
        # If fetch fails, use URL as fallback title
        parsed = urlparse(url)
        result["title"] = parsed.path.split("/")[-1] or parsed.netloc

    # Final fallback for title
    if not result["title"]:
        parsed = urlparse(url)
        result["title"] = url

    return result


def is_valid_url(string):
    """Check if a string is a valid URL."""
    try:
        result = urlparse(string)
        return all([result.scheme, result.netloc])
    except:
        return False
