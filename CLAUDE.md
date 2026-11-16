# Formal: Project Briefing for Claude Code

Read this fully before changing anything. Then read COMMANDS.md, run `git log --oneline -15`, and read backend/core/items.py, backend/core/db.py, backend/core/profile.py.

## 0. Current state (update at the end of every session)
As of 2026-11-15. Claude memory does not travel between the Codespace and other machines; this section does.
- Done: Gmail -> items pool -> Agenda loop hardened. Gate decides pool vs noise only, learns from dismissals (`sender_reputation`), and records `_pass_reason` on every email and item. items_adapter fixed: no fake deadlines from "reply to this email", invites are events, FAFSA-style deadlines stay tasks, deliveries/purchase alerts/digests are records, dates wrap to next year. `--done/--dismiss N` hit the right row. Views score the whole pool. Deferred items reopen. ranked_items `--ingest` removed. backend/output/*.json untracked.
- Done (2e9dec4): the fallback 3-day deadline is gated on structure, not wording: only mail sent directly to you or marked Important gets one (bulk = List-Unsubscribe header or Promotions/Social tab). Header names are case-insensitive; List-Id / Auto-Submitted / Precedence are recorded but unused. Real-data check passed: 6 bulk emails lose the fake deadline, 4 direct ones keep it, and Important mail with stated dates keeps its real dates.
- Approved (2026-11-15), not built: the pool schema expansion, so the pool can hold any content type. It adds 9 columns: content_type, content_ref, body, content_hash, intent, category, routed_app, processed and defer_until. raw_payload is renamed `intake` (immutable evidence); `processed` holds the re-runnable interpretation. body is the original text, never shown in lists; description is a short preview. Kept emails store their first 2,000 chars of text in body. Every `--add` lands in the pool unlabeled. Deferral moves to defer_until, so the real due_at is kept. Migration is ALTER TABLE ADD COLUMN plus RENAME COLUMN, with no rebuild. The spec was checked against the Codespace design. The full plan (commits C0-C7, Step 0, verification) is on the Windows PC: `C:\Users\senyo\.claude\plans\ultracode-plan-the-pool-distributed-dusk.md`.
- Next: build C0-C7 on the Windows PC. Do Step 0 first (WAL-safe backup + snapshot). Run no app command mid-series, because init_db would migrate the real DB. After C7: migrate, diff the snapshot, then run `python apps/gmail/gmail_scraper.py --rebuild` over a long window (this is also the pending junk cleanup). Items whose emails are now noise still need a small archive step. Then: Phase 2 (downstream processor + `--reprocess`), then Takeout adapters. Later: read-based engagement signal in shadow mode; skip known message IDs before downloading.
- Database home: the Windows PC (backend/formal.db with real data, plus the Gmail tokens; backup at `C:\Users\senyo\formal.db.bak`). Claude Code CLI is installed there (`claude --continue` from the repo root). The Codespace's copy, if any, is not authoritative.
- Owner decisions pending: expiry for undated auto-imported Gmail tasks; git history rewrite for old personal data; retire legacy `route_to_db` and the dead Playwright school path.
- Takeout: all 5 school zips are downloaded and extracted on the Windows PC (`C:\Users\senyo\takeout`, outside the repo; the links expire 2026-11-21).

## 1. What Formal is
A personal life operating system and AI butler. It ingests data from scattered life sources (school, email, calendar, finances, notes), puts it into one unified model, ranks what matters, and a central AI "Coach" advises (later: acts). Less productivity app, more personal strategist. It should find non-obvious leverage and eventually execute (draft, file, book), not just suggest. The more data it has, the better it knows the user.

Origin: the owner was academically ahead in high school, lost momentum, and is building the system he wished he had.

## 2. Owner and working style
- Owner: Samuel, solo developer, now a college student (previously a high school quarter system, now semesters).
- Environment: iPad, GitHub Codespaces, bash/Linux, plus a Windows PC copy (Git Bash) that has no gitignored data unless copied over. Legacy Windows artifacts exist (CRLF warnings, a reserved filename `nul`). Never create files named nul, con, aux, or prn.
- He mostly copies commands and does not read prose. Give commands in exact run order, in separate blocks per step, with minimal explanation. Recommend ONE option instead of listing many. Never mix explanation text into command blocks.
- Never load huge files into context (a 1M-character file already caused a context error). Use head, sed -n ranges, and grep. Do not read formal.db or grades.json in full.

## 3. Design principles (non-negotiable)
1. Versatile for any user and life stage. No hardcoded school, term system, grading scale, weights, colleges, employer, or personal details in code. Everything user-specific comes from the profile (backend/core/profile.py). profile.json is gitignored. profile.example.json is committed and must stay generic.
2. Say "term", not "quarter". Terms are date-based and configurable (quarter, semester, trimester, custom). Portal labels like Q1 are stored separately as source_period.
3. Source-agnostic core. The item model and Priority Engine must know nothing about Gradebook, Gmail, or any school. Sources are adapters.
4. Prove ONE loop end to end before building more apps: data in -> unified item pool -> priority ranking -> Coach advice -> user acts -> feedback.
5. Advisory first. The Coach recommends. No autonomous actions until trust is earned.
6. Privacy by default. No personal data in the repo. Prefer storing IDs and links to originals (e.g., email message IDs) over copying full content.
7. Explainable ranking. Every score must expose its inputs and reasons so it can be tuned.

## 4. System concept (from the owner's mind map)
Pipeline: Ingestion -> Normalization -> Impact modeling -> Priority engine -> Agenda -> Coach -> Feedback loop.

Apps:
- The Coach: central brain. Inputs: general AI knowledge, personal history, scraped knowledge. Processing: context builder, persona engine, priority awareness, temporal reasoning, opportunity detection, risk assessment, pattern detection. Outputs: actionable advice, strategic guidance, warnings, optimization suggestions, explanations, motivational nudges. Feedback loop learns preferred advice style and tracks whether advice helped. Cross-app effects: updates the Agenda, influences the Receipt, guides the Checkbook.
- The Agenda: one universal, self-updating task list ranked by the Priority Engine. Filters are views of one list, not separate systems.
- The Gradebook: academic tracker (grades, GPA, assignment impact, what-if). School-specific. PAUSED.
- The Checkbook: finances (accounts, spending, projections, "can I afford this").
- The Receipt: wishlist/want tracker plus purchase advisor. Checks affordability against the Checkbook and tiers items Realistic / Stretch / Delusional. The name stays.
- The Utilizer: opportunity engine and automation hub.
- Jotting apps: Notebook (resource library), Journal, Diary (reference vault), Whiteboard (ideas).
- Intake / Jotting & Sorting: share sheet, links, screenshots, voice, text -> normalize -> semantic detection -> route to the right app.
Out of scope for now: social, health, smart home, e-commerce, navigation (other maps, future).

## 5. Repo map (verify with `git ls-files`; this may drift)
- backend/run.py: runs all scrapers sequentially (legacy school-centric runner)
- backend/core/db.py: shared SQLite layer (sqlite3, WAL). DB file: backend/formal.db. Tables: grades, assignments, tasks, plus the new items pool. Legacy `quarter` columns were migrated to `term` plus `source_period`.
- backend/core/profile.py: profile loader/validator. Term detection by date, source_period mapping, category weights, grading scale, per-course credits and weights.
- backend/core/items.py: the new generic `items` pool and adapter contract (fetch() -> list of items). Has tests for persistence and deduplication. Read it before extending it.
- backend/profile.example.json: generic example profile (committed)
- backend/tests/: unittest tests. Run: `cd backend && python -m unittest discover -s tests -v`
- backend/apps/agenda/main.py: daily Agenda CLI over the items pool (S/A/B by default, `--all` for C/D); `--done/--dismiss N` record sender_reputation. ranked_items.py is an alternate view of the same pool (accepts short IDs). backend/apps/coach/main.py: Coach v0 over the top items.
- backend/apps/gmail/gmail_scraper.py: email intake via Gmail API OAuth for personal and school accounts (credentials.json, token.json, token_school.json, all gitignored). The gate only decides pool vs noise (engagement index, sender frequency, sender_reputation) and stamps `_pass_reason`; `--ai` optionally labels with Groq. backend/apps/gmail/items_adapter.py turns kept mail into items (record rules, due dates). Legacy leftovers: `route_to_db` still writes the old `tasks` table; the Playwright school path is dead code.
- backend/tools/takeout_nyt.py: Google Takeout mbox -> NYT articles JSON (backend/data/, gitignored) + optional Obsidian notes.
- backend/apps/gradebook/: PowerSchool and Google Classroom scrapers (Playwright), calculator.py, priority.py. School-specific and PAUSED.
- backend/apps/jots/notebook/: Flask app + CLI + metadata fetcher. Still uses its OWN SQLite file (resources.db) and bare imports (`from db import ...`). Not yet on the shared DB.
- Scripts add backend/ to sys.path and import `from core.db import ...`.
- Secrets live in backend/.env (never committed).
- Frontend: not started. Planned Next.js.
- Root docs: README.md (one line), COMMANDS.md, ARCHITECTURE.md (Mermaid data flow) and PROJECT_STRUCTURE.md may or may not be committed yet.

## 6. Data layer direction
- "Pool" approach for mixed content (text, links, images, PDFs, docs, audio): original files live in file storage (data/raw/ locally, object storage later); the database holds metadata plus extracted text. No blobs in the DB. Hash files on intake for deduplication.
- Flow: item lands in pool -> extraction (OCR, PDF text, transcription) -> semantic detection -> routing. Originals are kept so smarter classification can be re-run later.

## 7. Decisions already made (do not relitigate without asking)
- Intake gate decides keep or drop, nothing else. Everything kept (plus manual adds) goes into the pool unlabeled; categorization (intent, category, routed_app) happens downstream after the pool schema expansion. The record/event/task routing in items_adapter.py is interim and will move there. Prefer structural signals (headers, Gmail tabs/labels, user behavior) over content regex.
- Stay on plain sqlite3 for now. Postgres, Celery, Alembic, SQLAlchemy are deferred until the loop is proven. Likely later: APScheduler for sync jobs, FastAPI for the API, Next.js for the UI.
- Gradebook and school-portal work is paused. The owner does not yet know which portal his college uses. When it returns, it becomes one more adapter.
- Priority formula in gradebook/priority.py is P = clamp(Wc * I * R * U * (1 + V), 0, 1) (category weight, impact, grade risk, urgency, volatility). The generic engine should generalize this to items (due date, importance, effort, consequence) with grade impact as an optional plugin.

## 8. Known issues and debt
- PRIVACY: formal.db (fcd2616) and backend/output/*.json (3dbc4b1) are no longer tracked, and debug_card.html is gone from the tree, but all of them remain in git history. Whether history needs cleaning is a SEPARATE decision. Do NOT rewrite history without explicit approval. No tokens, credentials, .env, or profile.json were ever committed.
- Test fixtures still use a real school domain (drew.edu); move them to example.edu.
- Hardcoded leftovers to move into the profile: PowerSchool and Classroom URLs, classifier regexes tied to one school. (The Groq prompt no longer names colleges or an employer.)
- classroom_scraper.filter_impactful_missing had a bug where `skipped += 1; continue` sat outside the inner `if` (any missing item with a quarter was dropped). Verify whether it was fixed.
- Duplicate logic: Google SSO login exists in two scrapers (Gmail imports it from the classroom scraper); category weights were duplicated across three files before the profile refactor.
- tasks table lacks a UNIQUE constraint, so `INSERT OR IGNORE` from Gmail does not dedupe.
- Gradebook requirements.txt is missing python-dotenv and beautifulsoup4. powerschool_scraper_example.py is dead code.

## 9. Roadmap (next milestone: the generic Priority Engine)
1. Privacy prerequisite: stop tracking formal.db; keep personal data and school-specific settings out of committed defaults.
2. Settle item semantics BEFORE adding integrations. Propose, then confirm with the owner: what importance, consequence, effort, due_at, and status mean, and how re-imports interact with an item the user edited. Core model must stay independent of Gradebook and the term profile.
3. Build the generic Priority Engine as a deterministic function over an item. Test ordinary, overdue, undated, high-consequence, and high-effort cases. It must explain its score inputs.
4. Connect ONE real source, Gmail, in parallel/shadow mode: import classified messages into the item pool, dedupe by stable message ID, keep a link to the original, avoid copying full bodies. Compare against the current task flow before switching anything.
5. Manual item path + CLI: add an item, view the ranked pool, mark done or deferred. A small daily-use workflow beats a new app or frontend.
6. Coach v0: give it the top items and their reasons; answer "what should I focus on today, and why?" Advisory only. Record user feedback before trying to learn from behavior.
Success = Gmail and manual items enter the same pool, rank consistently, and can be reviewed in one daily workflow.
Do NOT start: Calendar integration, Checkbook, Receipt, Utilizer, other jots apps, frontend, more Gradebook work.

## 10. Working agreement
- Back up formal.db OUTSIDE the repo (e.g., `cp backend/formal.db ~/formal.db.bak`) before any schema change or migration.
- Before every commit run `git diff --cached --name-only` and make sure no .db, .env, token.json, credentials.json, profile.json, output/, or grades.json is staged.
- Run the tests before committing. Add tests for new behavior. Small commits with conventional messages (feat:, fix:, chore:, docs:, refactor:).
- Ask first before: rewriting git history, breaking schema changes, adding dependencies or services, or touching the paused gradebook code.
- Never hardcode personal or school details. Put them in the profile.
- Fix bugs at the root: change the function whose logic is wrong. No patches, workarounds, special cases for the symptom, or post-processing band-aids layered on top.
- Prefer small, reviewable diffs. Explain decisions in 2-3 lines, then give the commands.
- Never co-author commits with Claude. No `Co-Authored-By` lines in commit messages.
- Start a new chat when tokens hit 50% capacity. Before ending, remind the user and write an in-depth context paragraph summarizing: what was accomplished in the current chat, what's currently in progress, and what should be tackled next in the following chat. Save this to memory so the next session can pick up seamlessly.

## 11. Suggested first session
1. Read the files listed at the top and summarize the current state back in under 15 lines.
2. Propose the fix for the formal.db tracking issue and wait for approval.
3. Draft the item-semantics definitions (section 9, step 2) for the owner to approve before writing any scoring code.
