# Formal — Architecture

## Email Pipeline

```
INTAKE    scrape_gmail_api()  ← apps/gmail/gmail_scraper.py
          Gmail API (OAuth), personal + school accounts
          Each email: id, from, subject, body[:2000], date, gmail_tab
          (first CATEGORY_* label, else 'primary'), is_important,
          has_list_unsubscribe
                           │
                           ▼
GATE      classify_emails() → _classify_one()   (pool vs noise only)
          Signals loaded once per run:
            engagement index  — domains you sent mail to; messages still in
                                the inbox per domain (Primary/Updates, last 500);
                                cached 7 days, never cached after an API error
            sender frequency  — emails_history.json + this batch
            sender_reputation — done/dismiss counts from the Agenda
          Rules (first match wins):
            0. bulk tab, not Important, dismissed ≥3× and never done → noise
            1. Promotions/Social: Important | replied | ≥5 kept    → pool
            2. Updates/Forums:    Important | rare | replied | ≥2 kept → pool
            3. Primary/other tabs                                   → pool
          Every result carries _pass_reason (None for noise).
          Optional --ai: Groq labels pooled mail; otherwise 'uncategorized'.
                           │
                           ▼
ADAPT     emails_to_items()  ← apps/gmail/items_adapter.py
          Group by ticket/order number (order token needs a digit)
          Invites → event. Record rules (OTP, grades, confirmations,
          completed, status, delivery, purchase alert, deposit, digest)
          → archived record. Anything else → open task.
          Derive importance, consequence, effort, due_at (month/day dates
          wrap to next year relative to the email's Date header).
          raw_payload keeps sender_domain and pass_reason.
                           │
                           ▼
STORE     ItemRepository.upsert()  ← core/items.py
          dedup key (source, source_item_id); re-imports never change status
          reopen_deferred_items() / archive_past_items() run each time
                           │
                           ▼
SCORE     score_item()  ← core/priority.py
          additive score with explainable reasons; sender_reputation nudges it
          Tiers: S ≥ 0.80 · A ≥ 0.60 · B ≥ 0.40 · C ≥ 0.20 · D < 0.20
                           │
                           ▼
DISPLAY   apps/agenda/main.py        S/A/B by default, --all for C/D
          --done N / --dismiss N     row numbers match both views;
                                     both record sender_reputation
          apps/agenda/ranked_items.py  same pool, accepts short IDs
          apps/coach/main.py           top items → advice
```
