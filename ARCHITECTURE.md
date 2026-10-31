# Formal — Architecture

## Email Pipeline

```
INTAKE
  scrape_personal_gmail()          scrape_school_gmail()
  (Gmail API + OAuth)              (Playwright + Google SSO)
            └──────────────┬──────────────┘
                           │  raw email dicts
                           ▼
CLASSIFY  classify_emails()  ← apps/gmail/gmail_scraper.py
    │
    ├─ FAST PATH: gmail_tab in {promotions, social, forums}
    │   →  _make_noise()  (no Groq call)
    │
    └─ GROQ PATH: _classify_one()  →  qwen/qwen3.8-27b
        gmail_tab: updates, personal (and anything else)
        Returns: category, intent, confidence,
                 consequence (1–5), urgency_context,
                 summary, deadline
                           │
                           │  classified email dicts
                           ▼
ADAPT     emails_to_items()  ← apps/gmail/items_adapter.py
    ├─ Filter out noise
    ├─ Thread-group by ticket/order number
    ├─ Classify as record (archived) or action (open)
    ├─ Derive: importance, consequence, effort_minutes, due_at
    └─ Deduplicate by normalized title
                           │
                           │  Item objects
                           ▼
STORE     ItemRepository.upsert()  ← core/items.py
    └─ INSERT OR IGNORE + ON CONFLICT UPDATE
       dedup key: (source, source_item_id)
                           │
                           │  items table  (formal.db)
                           ▼
SCORE     score_item()  ← core/priority.py
    score = importance_norm × urgency_factor × (consequence_norm / effort_norm)
    Tiers:  S ≥ 0.80 · A ≥ 0.60 · B ≥ 0.40 · C ≥ 0.20 · D < 0.20
                           │
                           │  ScoreResult (score + tier + reasons)
                           ▼
DISPLAY   ranked_items.py  ← apps/agenda/
    show_ranked   — ranked list by tier
    done <id>     — mark complete
    defer <id> N  — push out N days
    dismiss <id>  — archive from view
```
