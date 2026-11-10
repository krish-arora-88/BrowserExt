Clean up the backend of this repo. It has a lot of unnecessary code that I want removed without changing behavior.

SCOPE
Work on ONE folder per session, in this order: routes, then services, then models/db, then utils. Start with the first one not yet cleaned. Do not touch the frontend.

STEP 1: FIND (no edits yet)
1. Run `vulture . --min-confidence 70` and `ruff check --select F401,F841,ERA` on the folder. Install them if missing.
2. For every function, class, and helper in the folder, grep for its callers across the whole repo.
3. For every DB table/model, grep for reads and writes outside its own definition and migrations.
4. Give me a table: item | file | category | evidence | proposed action.
   Categories:
   - unused (zero callers)
   - pointless wrapper (only forwards to another call)
   - single-use helper (called from one place, should be inlined)
   - duplicate (same logic exists elsewhere)
   - speculative (config options, base classes, fallbacks, or error handling for cases that cannot happen)
   - unused table/column
5. Mark anything referenced dynamically (FastAPI dependencies, route decorators, string names, getattr, Pydantic validators, Alembic) as KEEP, since the tools flag these falsely.

Stop and wait for my approval of the table.

STEP 2: DELETE
After I approve:
1. Apply only the approved rows. Delete or inline. Never comment code out and never leave it "for later".
2. Do not refactor, rename, or restyle anything else. Do not add new abstractions, helpers, or files.
3. For unused tables, do NOT drop them. List them separately and I'll decide on a migration.
4. Run the tests and start the app after each category. If something breaks, revert that item and tell me.
5. Commit once per category with a message naming what was removed.

STEP 3: REPORT
Lines before and after, what was removed, what you kept and why, and anything you were unsure about.

STANDING RULES (also add these to AGENTS.md at the repo root so they apply to all future work)
- No new helper/util function unless it's called from 2+ places. Inline it otherwise.
- No wrapper classes or functions that only forward to another call.
- No new tables, columns, or models without asking first.
- Before adding code, search for an existing function that does it.
- Delete code you make unused.
- No speculative config options, abstract base classes, or fallbacks for cases that can't happen.
