# Classroom Scraper

This is a minimal scaffold for scraping the Google Classroom dashboard at `https://classroom.google.com/u/1/`.

Important notes:
- **Do not** try to bypass Google sign-in or organization controls. Only scrape accounts you own or have explicit permission to access.
- If your institution blocks OAuth, ask the admin to whitelist the app or use an allowed alternative (domain-wide delegation, API, or a personal account for tests).

How it works:
- Uses Playwright with a persistent `session/` directory. Run once, sign in interactively in the opened browser, then the session (cookies) is kept for subsequent runs.

Quick start:
1. python -m pip install -r requirements.txt
2. python -m playwright install
3. python scraper.py

Next steps:
- Inspect the Classroom dashboard in your browser and update selectors in `scraper.py` to extract the fields you need.
- If you want the scraper to reuse a login procedure from your PowerSchool scraper, paste the login helper here or tell me where to find it and I can integrate it.

Security & compliance:
- Add the appropriate accounts as test users or have admins whitelist the OAuth client if you plan to use official APIs.
- Keep credentials and session files out of VCS (see `.gitignore`).
