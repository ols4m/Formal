"""
Resources Web App - Interactive browser with auto-categorized tabs.
Run with: python app.py
Then open: http://localhost:5000
"""

from flask import Flask, render_template, jsonify, request
from db import get_all_resources, add_resource, delete_resource, update_topics
from fetcher import fetch_metadata, is_valid_url

app = Flask(__name__)


# Topic detection keywords - tabs auto-appear/disappear based on library content
TOPIC_KEYWORDS = {
    "Coding": [
        "code", "coding", "programming", "python", "javascript", "java", "react",
        "developer", "software", "api", "github", "git", "web dev", "devops",
        "frontend", "backend", "algorithm", "data structure", "debug",
        "html", "css", "node", "typescript", "rust", "golang", "ruby", "php",
        "c++", "c#", "swift", "kotlin", "flutter", "django", "flask", "fastapi",
        "docker", "kubernetes", "aws", "azure", "sql", "database", "mongodb"
    ],
    "Finance": [
        "finance", "money", "investing", "stock", "crypto", "bitcoin", "trading",
        "budget", "wealth", "economy", "bank", "investment", "portfolio", "market",
        "dividend", "etf", "401k", "retire", "savings", "debt", "credit", "loan",
        "income", "passive income", "real estate", "forex", "options", "hedge",
        "accounting", "tax", "financial", "wall street", "nasdaq", "s&p"
    ],
    "Math": [
        "math", "mathematics", "calculus", "algebra", "geometry", "statistics",
        "probability", "equation", "theorem", "proof", "linear", "matrix",
        "derivative", "integral", "trigonometry", "stochastic", "discrete",
        "covariance", "volatility", "regression", "optimization", "numerical"
    ],
    "Science": [
        "science", "physics", "chemistry", "biology", "quantum", "atom", "molecule",
        "experiment", "research", "theory", "hypothesis", "lab", "scientific",
        "engineering", "space", "nasa", "astronomy", "climate", "evolution",
        "genetics", "neuroscience", "ecology", "geology"
    ],
    "Sports": [
        "sports", "football", "soccer", "basketball", "baseball", "tennis", "golf",
        "hockey", "cricket", "rugby", "volleyball", "swimming", "running", "marathon",
        "olympics", "nba", "nfl", "fifa", "ufc", "mma", "boxing", "wrestling",
        "athlete", "workout", "gym", "fitness", "exercise", "crossfit",
        "yoga", "espn", "championship", "league"
    ],
    "Gaming": [
        "gaming", "video game", "playstation", "xbox", "nintendo", "steam",
        "esports", "twitch", "streamer", "minecraft", "fortnite", "valorant",
        "league of legends", "dota", "rpg", "fps", "mmorpg", "gamer",
        "speedrun", "walkthrough", "gameplay"
    ],
    "Music": [
        "music", "song", "album", "artist", "band", "concert", "spotify", "playlist",
        "guitar", "piano", "drums", "singing", "vocal", "producer", "beat", "remix",
        "hip hop", "rap", "rock", "pop", "jazz", "classical", "edm", "country",
        "lyrics", "cover", "music video", "musician"
    ],
    "Health": [
        "health", "medical", "doctor", "hospital", "medicine", "nutrition", "diet",
        "mental health", "therapy", "wellness", "sleep", "meditation", "mindfulness",
        "anxiety", "depression", "stress", "self care", "healthy", "disease",
        "symptoms", "treatment", "vaccine", "healthcare"
    ],
    "Food": [
        "food", "cooking", "recipe", "chef", "restaurant", "cuisine", "baking",
        "meal", "dinner", "breakfast", "lunch", "snack", "vegan", "vegetarian",
        "kitchen", "ingredient", "taste", "delicious", "foodie", "mukbang"
    ],
    "Travel": [
        "travel", "vacation", "trip", "destination", "flight", "hotel", "airbnb",
        "tourism", "backpacking", "adventure", "explore", "country", "city",
        "beach", "mountain", "passport", "visa", "abroad", "wanderlust"
    ],
    "Entertainment": [
        "movie", "film", "cinema", "netflix", "tv show", "series", "actor", "actress",
        "hollywood", "trailer", "comedy", "drama", "horror", "action",
        "documentary", "anime", "cartoon", "celebrity", "entertainment", "podcast"
    ],
    "Design": [
        "design", "ui", "ux", "figma", "photoshop", "illustrator", "graphic",
        "logo", "branding", "typography", "color", "layout", "wireframe",
        "prototype", "user interface", "user experience", "creative", "canva",
        "sketch", "adobe", "animation", "motion graphics", "3d", "blender"
    ],
    "Productivity": [
        "productivity", "habit", "routine", "goal", "focus", "time management",
        "organize", "planning", "schedule", "efficiency", "workflow", "notion",
        "obsidian", "note-taking", "second brain", "gtd", "pomodoro", "life hack"
    ],
    "AI & ML": [
        "ai", "artificial intelligence", "machine learning", "deep learning",
        "neural network", "gpt", "llm", "chatgpt", "claude", "training",
        "dataset", "tensorflow", "pytorch", "nlp", "computer vision", "openai",
        "anthropic", "midjourney", "stable diffusion", "generative ai"
    ],
    "Business": [
        "business", "startup", "entrepreneur", "marketing", "sales", "growth",
        "strategy", "management", "leadership", "company", "founder", "ceo",
        "product", "customer", "revenue", "profit", "ecommerce", "saas", "b2b"
    ],
    "Education": [
        "learn", "learning", "course", "education", "study", "lecture",
        "lesson", "teach", "student", "university", "college", "degree",
        "online course", "certification", "skill", "knowledge", "how to"
    ],
    "Robotics": [
        "robot", "robotics", "frc", "first robotics", "arduino", "raspberry pi",
        "automation", "mechatronics", "servo", "sensor", "actuator", "drone",
        "autonomous", "ros", "embedded", "microcontroller", "cad", "solidworks"
    ],
    "Motivation": [
        "motivation", "inspiration", "mindset", "success", "self improvement",
        "personal development", "confidence", "discipline", "growth mindset",
        "motivational", "life advice", "hustle", "grind", "never give up"
    ],
    "News": [
        "news", "breaking", "headline", "journalism", "reporter", "politics",
        "election", "government", "policy", "world news", "current events",
        "update", "announcement", "press", "media"
    ],
    "Fashion": [
        "fashion", "style", "outfit", "clothing", "dress", "shoes", "accessories",
        "trend", "designer", "runway", "streetwear", "haul", "lookbook"
    ],
    "Art": [
        "art", "artist", "painting", "drawing", "sculpture", "gallery", "museum",
        "artwork", "canvas", "sketch", "portrait", "abstract", "digital art",
        "illustration", "creative process"
    ],
    "Tech": [
        "tech", "technology", "gadget", "smartphone", "iphone", "android", "samsung",
        "apple", "google", "microsoft", "laptop", "computer", "hardware", "software",
        "review", "unboxing", "specs", "benchmark", "processor", "gpu", "cpu",
        "tablet", "smartwatch", "wearable", "innovation", "startup", "silicon valley"
    ],
}


def detect_topics(title, description):
    """Detect topics from title and description using keywords."""
    text = f"{title or ''} {description or ''}".lower()
    detected = []

    for topic, keywords in TOPIC_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                detected.append(topic)
                break  # One match per topic is enough

    return detected if detected else ["General"]


def get_platform_label(platform):
    """Get display label for platform."""
    platform_labels = {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "twitter": "Twitter",
        "tiktok": "TikTok",
        "github": "GitHub",
        "reddit": "Reddit",
        "medium": "Medium",
        "linkedin": "LinkedIn",
        "note": "Notes",
        "website": "Website",
    }
    return platform_labels.get(platform, platform.title() if platform else "Other")


def get_type_label(res_type):
    """Get display label for resource type."""
    type_labels = {
        "video": "Video",
        "link": "Link",
        "note": "Note",
        "document": "Document",
        "image": "Image",
        "audio": "Audio",
    }
    return type_labels.get(res_type, res_type.title() if res_type else "Link")


def get_resources_with_categories():
    """Get all resources with platform, type, and topic labels."""
    resources = get_all_resources()

    for r in resources:
        r["platform_label"] = get_platform_label(r.get("platform"))
        r["type_label"] = get_type_label(r.get("type"))

        # Use stored topics if available, otherwise auto-detect
        stored_topics = r.get("topics")
        if stored_topics:
            r["topics"] = stored_topics.split(",")
            r["topics_manual"] = True  # Flag to show it was manually set
        else:
            r["topics"] = detect_topics(r.get("title"), r.get("description"))
            r["topics_manual"] = False

    return resources


def get_all_filters(resources):
    """Extract all unique platforms, types, and topics for filter tabs."""
    platforms = set()
    types = set()
    topics = set()

    for r in resources:
        platforms.add(r["platform_label"])
        types.add(r["type_label"])
        for topic in r["topics"]:
            topics.add(topic)

    return {
        "platforms": sorted(list(platforms)),
        "types": sorted(list(types)),
        "topics": sorted(list(topics)),
    }


@app.route("/")
def index():
    """Main page with interactive resource browser."""
    resources = get_resources_with_categories()
    filters = get_all_filters(resources)
    return render_template("index.html", resources=resources, filters=filters)


@app.route("/api/resources")
def api_resources():
    """API endpoint to get all resources."""
    resources = get_resources_with_categories()
    filters = get_all_filters(resources)
    return jsonify({"resources": resources, "filters": filters})


@app.route("/api/add", methods=["POST"])
def api_add():
    """API endpoint to add a resource."""
    data = request.json
    url = data.get("url")
    note = data.get("note")

    if note:
        resource_id = add_resource(
            title=note[:50] + "..." if len(note) > 50 else note,
            resource_type="note",
            description=note,
            platform="note"
        )
        return jsonify({"success": True, "id": resource_id})

    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400

    if not is_valid_url(url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not is_valid_url(url):
            return jsonify({"success": False, "error": "Invalid URL"}), 400

    metadata = fetch_metadata(url)
    resource_id = add_resource(
        title=metadata["title"],
        url=metadata["url"],
        resource_type=metadata["type"],
        platform=metadata["platform"],
        thumbnail=metadata["thumbnail"],
        description=metadata["description"]
    )

    return jsonify({"success": True, "id": resource_id, "metadata": metadata})


@app.route("/api/delete/<int:resource_id>", methods=["DELETE"])
def api_delete(resource_id):
    """API endpoint to delete a resource."""
    if delete_resource(resource_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Resource not found"}), 404


@app.route("/api/topics/<int:resource_id>", methods=["PUT"])
def api_update_topics(resource_id):
    """API endpoint to update topics for a resource."""
    data = request.json
    topics = data.get("topics", [])

    if update_topics(resource_id, topics):
        return jsonify({"success": True, "topics": topics})
    return jsonify({"success": False, "error": "Resource not found"}), 404


@app.route("/api/all-topics")
def api_all_topics():
    """Get all available topic categories."""
    return jsonify({"topics": list(TOPIC_KEYWORDS.keys())})


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  The NoteBook")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
