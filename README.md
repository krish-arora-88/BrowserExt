# Anchor AI Backend: End-to-End Explanation, Review, and Rebuild Plan

This walks through the whole `backend/` folder of the Anchor AI repo, based on `anchor-ai-backend-line-by-line.md`. Line numbers like `L42` match the gutter numbers in that document.

**Contents**

1. What this system is, in plain words
2. Core concepts you need first
3. The technology stack, and why each piece is there
4. The architecture: layers, and why it's split this way
5. How it works end to end, and why each design choice was made
6. Problems, fixes, and what I'd do differently
7. If I built this backend from scratch

---

## Part 1: What this system is, in plain words

A bank has IT incidents, like an outage or a data problem. US bank regulators (the **FRB**, Federal Reserve Board, and the **OCC**, Office of the Comptroller of the Currency) require the bank to notify them about serious ones. Deciding "is this serious enough to report?" and then writing the notification is slow, manual work for several teams.

**Anchor AI** automates the first draft of both steps with an **LLM** (large language model: an AI like GPT that reads and writes text). Humans still review everything:

1. **Assess:** is the incident reportable? The LLM answers 4 FRB questions with Yes or No.
2. **Draft:** if it is reportable, the LLM writes the notification text.
3. **Review:** human reviewers (1LOD, then 2LOD) approve it or send feedback. Feedback makes the LLM redraft.
4. **Submit:** the Reg Relations team sends the final version.

The **backend** (the part of the software that runs on a server, not in your browser) holds the rules, talks to the database and calls the LLM. A **frontend** (the web pages people click on, the "operator UI") calls it, but that isn't in this folder.

---

## Part 2: Core concepts you need first

| Term | What it means |
|---|---|
| **API** (Application Programming Interface) | A set of URLs a program can call to ask the backend to do something. |
| **HTTP** | The protocol (agreed message format) browsers and servers use. Each request has a **method**: `GET` reads, `POST` creates or acts. |
| **Endpoint** | One URL + method, e.g. `POST /api/assess`. |
| **JSON** | A text format for data: `{"incident_number": "INC0575428"}`. |
| **Status code** | A number in each response: `200` OK, `202` accepted (working on it), `400` bad input, `404` not found, `429` too many requests, `500` server crashed. |
| **Query parameter** | Data in the URL after `?`, e.g. `?mode=force`. |
| **Database** | A program that stores data permanently in **tables** (like spreadsheets: rows and columns). |
| **SQL** | The language you use to talk to a database (`SELECT`, `INSERT`, `UPDATE`). |
| **Thread** | One line of execution inside a program. Several threads let a program do several things at once. |
| **Process** | One running program. It can contain many threads. Memory isn't shared between processes. |
| **Synchronous vs asynchronous** | Synchronous: wait until the work finishes. Asynchronous: start the work, return straight away, check back later. |
| **Environment variable** | A setting passed to a program from outside (e.g. `POSTGRES_PASSWORD`), so secrets aren't written in the code. |
| **Prompt** | The text instructions you send to the LLM. The **system prompt** sets its role and rules; the **user prompt** carries the specific task. |

---

## Part 3: The technology stack, and why each piece is there

### Python
- **What:** the programming language for the whole backend.
- **Why chosen:** it's the default language for AI/ML work, every LLM library supports it first, and data teams already know it.
- **Tradeoff:** slower than Java or Go, and the **GIL** (Global Interpreter Lock: only one thread runs Python code at a time) limits true parallelism. That doesn't matter much here: the program spends most of its time *waiting* on the LLM and the database, and waiting threads release the GIL.

### FastAPI (`main.py`, `routers/`)
- **What:** a Python **web framework** (a library that turns your functions into HTTP endpoints).
- **Why chosen:**
  - It's modern and fast.
  - It validates inputs automatically.
  - It auto-generates **Swagger docs** (an interactive web page at `/docs` listing every endpoint, where you can try them out).
- **Without it:** you'd hand-write HTTP parsing, validation and docs.
- **Alternatives:** Flask (older, less built in) and Django (heavier, ships with its own database layer). FastAPI is the standard choice for new Python APIs.
- **The server:** FastAPI needs an **ASGI server** (the program that actually listens on a network port and hands requests to FastAPI), almost certainly **Uvicorn**. The README starts it with `python main.py --server` from the repo-root `main.py`, which isn't in the source doc.

### Pydantic (`schemas.py`)
- **What:** a library where you declare data shapes as classes, e.g. "incident_number is a string of 1–50 letters and digits". Pydantic then checks real data against them.
- **Why:** every request is checked before your code runs, so bad input gets a clean `422`/`400` instead of crashing deep inside.
- **A second, very important use:** the LLM is forced to return data in a Pydantic shape (see *structured output* in Part 5).
- **Without it:** garbage input and malformed LLM answers flow straight into the database.

### PostgreSQL ("Postgres") + psycopg3 + psycopg_pool (`utils/postgres_client.py`)
- **Postgres:** a reliable, free **relational database** (data in tables with relationships between them).
- **psycopg3:** the Python **driver** (the library that lets Python talk to Postgres).
- **psycopg_pool:** a **connection pool**. Opening a database connection is slow (network handshake plus login), so the pool keeps about 10 connections open and lends them out. Without it, every query pays that setup cost, and a traffic spike can exhaust the database's connection limit.
- **Why Postgres:** banks trust it. It has **transactions** (a group of changes that either all happen or none do) and **JSONB** (a JSON column type; audit details use it). It also has the next feature, which this design depends on.

### Postgres triggers + LISTEN/NOTIFY (`workflow_listener.py`, `workflow_service.py`)
- **Trigger:** code that lives *inside the database* and runs automatically when a row changes. Here: "when `mimt_input_completed` flips from FALSE to TRUE, send an event."
- **LISTEN/NOTIFY:** Postgres's built-in **pub/sub** (publish/subscribe: one side shouts a message on a named channel, anyone listening hears it). The trigger runs `NOTIFY workflow_events, '{"event_type":"1lod_approved", "incident_number":"INC…"}'`, and the Python listener thread hears it.
- **Why chosen:** the team didn't need to add a separate **message broker** (dedicated queue software like RabbitMQ, Kafka or Redis). They already had Postgres, so they got event-driven automation with zero new infrastructure.
- **Tradeoff:**
  - NOTIFY messages are **fire-and-forget**. If the listener is disconnected when one fires, it's gone forever.
  - Business logic in triggers is invisible from the Python code. The trigger SQL lives in `db_setup.sql`, outside `backend/`, which is exactly why there's an event-name mismatch nobody noticed (see Part 6).

### OpenAI Python SDK, via a corporate gateway (`utils/llm_service.py`)
- **SDK** (Software Development Kit): the official library for calling OpenAI-style models.
- The model is `gpt-4.1…-eastus-dz`, reached through the bank's internal **gateway** (a company-controlled proxy in front of the AI provider). It handles security, logging and billing, and keeps data inside approved boundaries.
- **OAuth "client credentials":** the gateway needs a temporary **access token** (a string proving you're allowed in, valid for about an hour). The app trades its `client_id` + `client_secret` for one, then refreshes it before it expires (`_ensure_valid_token`, L82).
- **`rbc_security.enable_certs()`:** installs the company's **TLS certificates**. TLS is the encryption behind `https`; certificates prove the server is who it claims to be. Corporate networks often inspect traffic with their own certificates, so normal certificate checks fail without this.

### tenacity (`@retry` decorators)
- **What:** a retry library. "If this fails with a network error, wait 2s, 4s… and try up to 3 times."
- **Why:** networks and LLM gateways fail transiently (briefly and randomly). One retry usually fixes it.
- **Uses:** **exponential backoff** (waiting longer after each failure) so you don't hammer a struggling server.

### requests and httpx
- Two HTTP client libraries.
- `requests` fetches the OAuth token; `httpx` is what the OpenAI SDK uses under the hood.
- They are configured with **timeouts** (`LLM_REQUEST_TIMEOUT = 120s`). The comment at `config.py` L37–42 tells a real war story: a batch job hung for **16+ hours** because one connection stalled and nothing had a timeout. Without a timeout, a retry never triggers, because nothing ever "fails".

### LangChain (`@tool` in `tools/`)
- **What:** a popular framework for building LLM **agents** (an LLM in a loop that decides which functions — "tools" — to call next).
- **Why it was chosen originally:** the first design was a **ReAct agent** (Reason + Act: the LLM thinks, picks a tool like `db_query`, sees the result, thinks again, and repeats). LangChain makes that loop easy.
- **What happened since:** the team removed the agent loop (see Part 5.3). Today LangChain is used only for the `@tool` decorator and `.invoke({...})`. That's a fancy way to call a normal function. **It's now a heavy dependency doing nothing.**

### pandas + Excel + difflib (`tools/reference_data_tools.py`)
- **pandas:** a data-table library. It reads `Data Sample File.xlsx` into memory.
- **difflib:** Python's built-in fuzzy string matching ("PaymentHub" ≈ "Payment Hub").
- **Why:**
  - The CUSO Applications sheet has about 2,600 rows with long descriptions.
  - Pasting all of it into the prompt would blow the LLM's **context window** (the maximum text an LLM can read in one go) and cost a lot.
  - So the code loads it once into memory and fuzzy-matches only the few rows relevant to the incident's apps.
  - The Regulatory Requirements sheet is about 15 rows, so it's sent in full.
- **Tradeoff:** to update the reference data, someone replaces a file and restarts the app. No history, no validation.

### Python stdlib concurrency: `threading`, `ThreadPoolExecutor`, `contextvars`
- **`threading.Lock`:** a "one at a time" gate. Whoever holds the lock runs; others wait.
- **`ThreadPoolExecutor`:** a fixed team of worker threads you hand tasks to. It's used for batch runs (2 workers) and the workflow listener (2 workers).
- **`contextvars`:** a variable that holds a different value per task/thread, like a sticky note attached to the current unit of work. Explained in Part 5.5.

### python-dotenv
- Loads a `.env` file (a local file of `KEY=value` settings) into environment variables. That way developers don't have to export 15 variables by hand.

### CORS middleware (`main.py` L76)
- **CORS** (Cross-Origin Resource Sharing): browsers block a web page on one domain (e.g. the UI at `localhost:3000`) from calling an API on another (`localhost:8000`) unless the API says it's allowed.
- **Middleware** is code that runs around every request.
- `allow_origins=["*"]` means "any website may call me". It's fine on a laptop and too open for production; the comment admits this.

---

## Part 4: The architecture: layers, and why it's split this way

```
HTTP request
   ▼
main.py        → creates the app, startup/shutdown, plugs in routers
   ▼
routers/       → HTTP only: read params, validate, call a service, return
   ▼
services/      → business logic: "what happens when we assess an incident"
   ▼
tools/         → individual actions: query DB, call the LLM for assessment, etc.
   ▼
utils/         → raw infrastructure: DB pool, LLM client
```

**Why layers?** This is called **separation of concerns** (each part has one job):
- Routers don't know SQL.
- Services don't know about HTTP.
- `utils` doesn't know what an "incident" is.

**What that buys you:**
- You can call `run_single_assessment()` from an HTTP request, from the workflow engine and from a batch script. All three do that today.
- You can swap the LLM provider by touching one file.

`services/__init__.py` says it was split out of "a single 1,100+ line `main.py`". The branch name `feature/simplify-codebase-20261028` suggests this cleanup is ongoing.

### What each file does

| File | Job | What breaks without it |
|---|---|---|
| `main.py` | Builds the FastAPI app. **Lifespan** (code that runs once at startup and once at shutdown) checks the DB and starts the listener thread. | Nothing runs. |
| `config.py` | Reads every setting from environment variables, with defaults. | Settings scattered and hard-coded everywhere. |
| `logger.py` | Logging setup, plus `log_tool_call` / `log_llm_call` helpers. | Blind when things fail. |
| `schemas.py` | All Pydantic shapes for requests and responses (650 lines). | No validation; inconsistent responses. |
| `routers/intake.py` | MIMT creates incidents and marks input complete. | No way to start the workflow. |
| `routers/assessment.py`, `drafting.py` | Manually trigger assess/draft (single or batch) and evaluations. | Only automatic runs possible. |
| `routers/review.py` | Reviewers edit drafts, approve, send feedback. | No human loop. |
| `routers/operations.py` | `/health`, `/agents`, `GET /api/jobs/{id}`. | Can't poll job results or monitor. |
| `services/assessment_service.py` | Orchestrates (coordinates the steps of) one assessment: rules → context → LLM → save → verify. | Core feature gone. |
| `services/drafting_service.py` | Same for drafting. | Core feature gone. |
| `services/workflow_service.py` | The **state machine** (explained in Part 5.2). | No automation; humans trigger each step by hand. |
| `services/workflow_listener.py` | Thread that hears DB events and calls `workflow_service`. | Triggers fire into the void. |
| `services/job_queue.py` | In-memory store of background job status. | Long requests would time out. |
| `services/operation_runtime.py` | Locks so only one assessment (and one draft) runs at a time. | Concurrent LLM calls trip the corporate proxy (the "407" errors in the `config.py` comments). |
| `tools/data_tools.py` | `db_query` / `db_write` wrappers that **auto-stamp** prompt version and revision. | Can't tell which prompt produced which output. |
| `tools/assessment_tools.py` | Builds the assessment prompt, calls the LLM, computes the Yes/No flag. | No assessments. |
| `tools/drafting_tools.py` + `template_schema.json` | Builds the drafting prompt; copies factual fields without the LLM. | No drafts. |
| `tools/reference_data_tools.py` | Excel lookups. | The LLM loses app criticality and regulatory context. |
| `tools/audit_tools.py` | Writes to `audit_logs`. | You fail a regulatory audit. |
| `utils/llm_service.py` | Token management, retries, timeouts. | No LLM calls. |
| `utils/postgres_client.py` | Pool + generic query/insert/upsert. | No database access. |
| `utils/sql_utils.py` | Safe SQL helpers. **Unused.** | Nothing (dead code). |
| `agents/*.py` | Name, description, tool list, system prompt per "agent". | Only `/agents` and `/health` would break; see Part 5.3. |
| `evaluation/` | Scores prompts against human answers. | You can't prove a new prompt is better. |

---

## Part 5: How it works end to end, and why each design choice was made

### 5.1 An incident's journey through the code

**1. MIMT** (Major Incident Management Team) calls `POST /api/incidents` with the incident form.
- `intake_service.upsert_incident` → `PostgresClient.db_write("incidents", …)`.
- **Upsert** means "update if it exists, insert if not".

**2. MIMT calls `POST /api/incidents/INC123/mimt-complete`.** This sets `mimt_input_completed = TRUE`. That's *all* the endpoint does. It returns immediately, with no AI involved.

**3. The Postgres trigger fires** → `NOTIFY workflow_events '{"event_type":"mimt_input_completed",…}'`.

**4. `workflow_listener._run`** (L57) is a background thread with its own dedicated connection, looping on `conn.notifies(timeout=1.0)`.
- It receives the event and hands it to a 2-thread pool: `executor.submit(workflow_service.handle_event, payload)`.
- **Why a separate connection from the pool?** A LISTEN connection must stay open forever. If it were borrowed from the pool, it would never be returned.
- **Why `timeout=1.0`?** So the loop wakes up every second to check "should I stop?". That allows clean shutdown.
- **Why reconnect after 3s** (L75)? Networks drop. Without it, one blip kills the automation until someone restarts the server.

**5. `handle_event` → `_handle_mimt_completed`:**
- Checks the status is `Intake` or `Awaiting_MIMT_Input`.
- `set_status(ASSESSMENT)`.
- `_run_assessment` (under the `"assessment"` lock) → `assessment_service.run_single_assessment`.

**6. Inside `run_single_assessment`** (`assessment_service.py` L230):
- **a. Location rule** (L68–94): if the location is *known* and is neither US nor Global (e.g. "CANADA"), write a rule-based "No" with **no LLM call**.
  - Why: per the Sep 24 meeting, only US/Global incidents are reportable to US regulators. It's free, instant and deterministic.
  - Notice the careful distinction: *unknown* location ("no data found") is **not** auto-rejected. "We don't know" ≠ "it's not US".
- **b. `compute_assessment_revision`:** decides the revision number. Has a 1LOD/2LOD reviewer left new feedback on the assessment since the last row? If so, revision +1 and pass that feedback in.
- **c. `_gather_assessment_context`:** plain lookups, no AI.
  - The human MIMT draft.
  - Fuzzy-matched CUSO app rows.
  - All regulatory requirements.
  - Reviewer feedback.
  - Similar past incidents (same legal entity + priority) and how the agent flagged them.
- **d. `evaluate_reportability`:** **one** LLM call with **structured output**. You pass a Pydantic class (`ReportabilityAssessment`), and the model is constrained to return JSON matching it: 4 text answers plus 4 confidence levels.
  - Without structured output you'd be parsing free text with regex and praying.
- **e. The code itself decides the final flag** (L290–297): if *any* criterion starts with "Yes", the incident is reportable.
  - Why not let the LLM decide? LLMs are inconsistent at simple logic. "Any of 4" is a rule, so it's written as a rule.
- **f.** Save with `db_write`, then re-read the row and verify that all required fields are present. If incomplete, retry once. Write an audit log.

**7. Back in the workflow:**
- If the flag is Yes: `DRAFTING` → `run_single_draft(stage="Drafting Agent Draft")` → `1LOD_Review`.
- Otherwise: `Not_Reportable`.

**8. Drafting** (`drafting_service.run_single_draft`, L104):
- **a. `populate_template_fields`:** copies the factual fields (date, legal entity, priority formatted as "P1 – Significant", and so on) straight from the incident, with no LLM.
  - Why: an LLM can **hallucinate** (confidently make things up), and you never want it to "rewrite" a date.
- **b. Picks a baseline:** the human MIMT draft for the first agent draft, or the agent's own earlier draft for revisions.
- **c. `generate_template_content`:** one structured LLM call that writes the 6 prose fields (title, current_status, description, impact, management_actions, root_cause).
- **d.** Save, verify, retry once, audit.

**9. A 1LOD reviewer** uses `GET /api/review/template`, edits, then calls `POST /api/review/INC123/1lod/approve` or `/feedback`.
- `review_service` writes `lod1_approval_status = 'Approved'` (or `'Feedback'` plus the text).
- The trigger fires `1lod_approved` / `1lod_feedback`.
- Feedback → the engine redrafts at stage `1LOD R&C` with the feedback in the prompt, then goes back to `1LOD_Review`.
- Approve → `2LOD_Review`. 2LOD works the same way.

**10. Reg Relations** calls `/reg-relations/complete` → `Submitted` → `Completed`.

### 5.2 The state machine (`workflow_service.py`)

A **state machine** is a model where something is always in exactly one **state** (`1LOD_Review`), and only certain **transitions** (moves to other states) are allowed. `ALLOWED_TRANSITIONS` (L64) is the map of legal moves. `set_status` refuses illegal jumps.

Why a state machine? With regulatory filings, you must never jump from `Intake` to `Submitted` by accident. An explicit list of legal moves makes that impossible and easy to audit.

Two clever tricks are worth understanding:

- **Idempotency gating.** **Idempotent** means doing something twice has the same effect as doing it once.
  - Every handler first checks "am I in the state I expect?" (e.g. `if status != LOD1_REVIEW: return`).
  - So if the same event arrives twice, the second one does nothing.
  - Without this, a duplicate event could draft the notification twice or skip a review stage.
- **`_clear_flag`** (L112). The trigger only fires when `lod1_approval_status` *changes to* "Feedback".
  - Picture a reviewer who gives feedback twice in a row: "Feedback" → "Feedback" isn't a change, so the second event would never fire.
  - So after processing, the engine resets the column to NULL. NULL doesn't match any trigger condition, so the reset itself emits nothing.
  - Without it, the second round of feedback is silently ignored.

### 5.3 The single most important design story: ReAct agent → deterministic pipeline

The folder is called `agents/` and the docs talk about "the Assessment Agent", but **there is no agent loop anymore**. The docstrings at `assessment_service.py` L9–14 and `drafting_service.py` L70–77 explain why it was removed:

- **The old design:** the LLM was given tools (`db_query`, `db_write`, `lookup_cuso_applications`, …) and decided which to call, in what order.
- **Problem 1, cost and speed:** about 5 extra LLM round-trips per incident just to decide "should I look up the MIMT draft?". The answer is always yes. That's not a judgment call.
- **Problem 2, reliability:** the agent sometimes gathered context and then simply **stopped**, without ever writing the assessment. For a compliance system, "sometimes forgets to do the job" is unacceptable.
- **The fix:** the code fetches everything deterministically, makes **one** LLM call for the one part that needs judgment, then saves the result in code.

This is the correct call, and a lesson many teams learn the hard way: **use an agent loop only when the steps truly can't be known in advance.** Here the steps are always the same.

The leftovers from that era:
- `agents/*.py` now only supplies a name, description and tool list for `/agents` and `/health`.
- LangChain `@tool` wrappers.
- `COMMON_TOOLS` and the other unused lists in `tools/__init__.py`.
- The `contextvars` trick (5.5).

### 5.4 Async jobs: `202 + job_id` and polling (`job_queue.py`)

- An LLM call takes 10–120s, and a batch of 200 incidents takes hours.
- HTTP requests, browsers and corporate proxies time out after roughly 30–60s.
- So `POST /api/assess` doesn't do the work. It:
  1. Creates a job record in a Python dictionary (`_jobs`) with status `queued`.
  2. Schedules the work with FastAPI's `BackgroundTasks` (functions that run *after* the response is sent).
  3. Immediately returns `202 Accepted` + `job_id`.
- The UI then **polls** (asks repeatedly) `GET /api/jobs/{id}` until the status is `completed` or `failed`.

**Tradeoff (the file's own note, L3–9):** the job store lives in process memory.
- Restart the server and every job's status is gone.
- Run 2 server processes and a poll may hit the one that doesn't know the job.
- It's acceptable for a single-instance pilot; it's the first thing to fix for production.

### 5.5 Versioned rows, the NULL trick, and `contextvars`

Every agent output row is stamped with `prompt_version` (e.g. `v10`). The upsert key (`REVISIONED_TABLES`, `postgres_client.py` L58) is:
- `reportability_assessments`: incident + prompt_version + revision
- `template_details`: incident + stage + drafted_by + prompt_version + assessment_prompt_version

That means:
- Rerunning v10 on an incident **overwrites** the v10 row (no duplicates).
- Running v11 creates a **new** row, so you can compare v10 vs v11 side by side.
- Human rows have `prompt_version = NULL`. In SQL, **NULL never equals NULL** (NULL means "unknown", and two unknowns aren't known to be equal). So a human row never matches an existing one and is **always inserted**. Every human edit is kept as history for free. Clever, but subtle.

**Why `contextvars`?** Back when the LLM itself called `db_write`, you didn't want the LLM choosing the revision number or the assessment version. So the orchestration code "pinned" the value on the current task (`set_current_assessment_revision`), and `db_write` read it invisibly. The LLM never saw it.
- Now that the code calls `db_write` directly, these could simply be **normal function arguments**. The `contextvars` are a fossil of the agent era.

### 5.6 Prompt versioning and evaluation (`evaluation/`)

**Prompts as Python modules:**
- Each prompt version is its own file: `assessment_agent_prompt_v0.py` … `v10.py`.
- `prompt_config.py` names the active one, and `loader.py` imports it by name with `importlib`.
- **Why:** prompts are effectively code. A change can make the system better or worse, so they're versioned, reviewable and reproducible.

**Ground truth** (the "correct answers" you grade against): human assessments that 2LOD signed off (`final_approval_by_lod == 'Yes'`).

**Assessment scoring** (`evaluate_assessment_prompts.py`) builds a **confusion matrix** per criterion: a 2×2 table of (agent said Yes/No) × (human said Yes/No):
- **Accuracy:** % of answers that matched.
- **Precision:** of the times the agent said "Yes, reportable", what % were truly reportable?
- **Recall:** of the truly reportable incidents, what % did the agent catch?
- **F1:** one number combining precision and recall (their harmonic mean).

For this domain, **recall matters most**. A **false negative** (calling a reportable incident "not reportable") means the bank fails to notify a regulator, which is a compliance breach. A **false positive** just costs a human a review.

**Drafting scoring** (`evaluate_drafting_prompts.py`) uses **LLM-as-judge**: a second LLM call grades each drafted field High/Medium/Low against what Reg Relations actually sent.
- You can't compare prose with `==`; two good paragraphs never match word for word.
- **Tradeoff:** the judge is itself an LLM, so it can be biased or inconsistent.

### 5.7 Audit logs

Every meaningful action writes a row to `audit_logs`: who, what, when, plus JSON details. Regulators and internal audit will ask "who approved this notification and when?", and this table is the answer. Without it, the system is unusable in a bank, no matter how good the AI is.

---

## Part 6: Problems, fixes, and what I'd do differently

The source doc's appendix already lists many leads. Below they're prioritized, with several the source doc **doesn't mention** marked 🆕. Everything comes from reading transcribed screenshots, not from running the code, so **verify each one against the real repo before acting on it.**

### 🔴 Critical

**1. 🆕 There is no authentication or authorization at all.**
- **Authentication** = proving who you are (login). **Authorization** = what you're allowed to do.
- There is no login check or role check on any endpoint; a search for FastAPI's `Depends`, user objects and API keys found none.
- Anyone who can reach the server can call `POST /api/review/INC123/2lod/approve` and approve a regulatory filing.
- `audit_log` has an `actor_id` parameter, but `review_service` never fills it in. The audit trail records the role ("1LOD"), never *which person*.
- **Fix:** put the bank's **SSO** (Single Sign-On: the company login) in front via **OIDC** (OpenID Connect, the standard SSO protocol). Map users to roles (MIMT/1LOD/2LOD/RegRel), check the role on every review endpoint, and record the user ID in every audit row.

**2. 🆕 A brand-new LLM client, and a new OAuth token, is created for every single LLM call.**
- `evaluate_reportability` (L178) and `generate_template_content` (L318) both do `llm = LLMService()`.
- The token cache and `_token_lock` live *on that instance*, so every call starts with no token and fetches a fresh one.
- The `config.py` comment (L59–63) credits that lock with reducing the proxy's 407 errors and raising concurrency to 2. But the lock protects nothing: each call has its own instance, so two concurrent calls still make two simultaneous OAuth requests.
- **Fix:** one module-level shared instance, e.g. `_llm = LLMService()` once, or a `get_llm()` that caches it. It's a ~3-line change, and could fix the 407s for real.

**3. Event name mismatch (source doc appendix).**
- The intake docstring says the trigger emits `mimt_completed`; the handler table expects `mimt_input_completed`.
- If the trigger really sends the former, **the entire automatic workflow never starts.**
- Check `db_setup.sql` first. It takes five minutes.

### 🟠 Correctness and reliability

**4. 🆕 Race conditions in the upsert.**
- **Race condition:** two things running at once interfere because the outcome depends on timing.
- `_upsert_keyed` does "SELECT to see if it exists → then INSERT", as two separate steps. Two workers handling the same incident can both see "doesn't exist" and both insert, creating duplicate rows.
- The same pattern exists in `db_write` for `incidents`.
- **Fix:** the database does this atomically (as one indivisible step): add a **unique index** (a database rule that forbids duplicate values in those columns) and use `INSERT … ON CONFLICT (…) DO UPDATE`.
  - For "humans always insert", use a **partial** unique index (`WHERE prompt_version IS NOT NULL`).
  - This is the "database constraint over app code" principle: the database is the only place that can truly guarantee uniqueness.

**5. 🆕 Workflow events for the same incident can be processed concurrently, and long jobs starve the listener.**
- The listener pool has 2 threads.
- Picture a reviewer double-clicking "feedback". Two events → both handlers read status `1LOD_Review` → both proceed. That's a classic **check-then-act race**.
- Also, each handler can take minutes (LLM calls). Two slow drafts occupy both threads, and every other incident's events wait.
- **Fix:** lock the incident row in the database (`SELECT … FOR UPDATE`), or route all events through a durable queue processed in order per incident (see Part 7).

**6. Lost events and the `Error` dead end (source doc appendix, expanded).**
- NOTIFY events fired while the listener is reconnecting are gone forever.
- A failed handler sets `Error`. `ALLOWED_TRANSITIONS` *allows* leaving `Error`, but no handler starts from `Error`, so in practice the incident is stuck.
- The approval flag is also never cleared on failure, so redoing the same human decision won't re-fire the trigger.
- **Fix:**
  - On startup, and every few minutes, run a **reconciliation sweep**: find incidents whose data says "should have moved" but didn't, and process them.
  - Add a "retry" admin endpoint for `Error` incidents.

**7. 🆕 Reviewer hand-edits are ignored when the agent redrafts.**
- On feedback, `run_single_draft` looks up the existing template using `drafted_by = "Drafting Agent"` (L116–122, L157).
- But a 1LOD reviewer's own edits are saved as `drafted_by = "1LOD (human)"` (`review_service._drafted_by`).
- So the redraft sees the agent's old text plus the feedback comment, and **not the reviewer's edits**. Combined with the "first revision sends a blank template" bug from the source doc, revisions may be working from much less than the reviewer expects.
- Verify with a real run.

**8. 🆕 Yes/No is buried in free text.**
- The schema field is `data_system_impact: str` ("Yes/No + explanation"), and the code checks `.startswith('yes')`.
- An answer like `**Yes**` or "Based on the evidence, yes" would be read as No. That would be a silent false negative, the worst kind of error here.
- **Fix:** give each criterion separate fields: `answer: Literal["Yes","No"]` plus `explanation: str`. Structured output then guarantees a clean value.

**9. 🆕 Silent fallback prompts.**
- `_get_assessment_system_prompt` (L67) and `_get_drafting_system_prompt` catch any error and fall back to a one-sentence generic prompt, with only a warning logged.
- The row still gets stamped `v10`, so a broken prompt file means production quietly assesses incidents with almost no criteria while claiming v10.
- The agents module says "fail loud"; these tools do the opposite.
- **Fix:** delete the fallbacks and let it crash.

**10. Retrying errors that can't succeed (source doc appendix).**
- `chat()` retries on `APIError`, which covers 400/401 errors. Retrying a bad request just wastes time.
- **Fix:** retry only rate limits (429), server errors (5xx), timeouts and connection errors.

**11. Blocking calls in `async def` endpoints (source doc appendix).**
- FastAPI runs `async def` functions on a single **event loop** (one thread that juggles many requests, as long as none of them blocks).
- `intake` and `review` endpoints call blocking database code from `async def`, which freezes *every* request while that query runs.
- **Fix:** change them to plain `def`. FastAPI then runs them in a thread pool automatically. It's a one-word fix per endpoint.

### 🟡 Security hygiene

**12.**
- **TLS verification off by default** (`LLM_VERIFY_SSL=false`). That opens the door to a **man-in-the-middle attack** (someone on the network reading or altering traffic). Default it to on.
- **Full prompts, including incident details, in plain-text logs.** Redact them, or log only IDs, token counts and timings.
- **Table and column names in f-strings.** The LLM no longer picks tables (good), but still allow only known names.
- **The password is put into the connection string unquoted.** Pass connection fields as keyword arguments instead of a single string.
- **CORS `*`.** List the real UI domain.

### 🟢 Cleanup and maintainability

**13.**
- **Remove LangChain:** call the functions directly. That's a big dependency gone, and stack traces get simpler.
- **Replace `contextvars` with normal arguments.**
- **Delete dead code:** `sql_utils.py`, the `COMMON_TOOLS` lists, `get_embeddings`, `PostgresClient.listen()`, SMTP settings, `HISTORY_LIMIT`.
- **Collapse `agents/`:** fold its metadata into a small dict, or rename it so newcomers aren't misled into thinking there's an agent loop.
- **Use `logger` instead of `print()`** in `agents/*.py`.
- **Call `load_dotenv()` in `config.py`**, the first module that reads settings.

**14. Fix the eval-cache label bug (source doc appendix):** `v10` vs `assessment_agent_prompt_v10`. It's a one-line fix: use the short label in `build_summary`.

**15. 🆕 No tests visible in `backend/`.**
- **Unit tests** (small automated checks that one function behaves correctly) are cheapest where bugs are most dangerous:
  - the location rule,
  - "any Yes → reportable",
  - `compute_assessment_revision`,
  - the state-machine transitions.
- Also run `evaluate_assessment_prompts` as a **regression gate** (an automatic check that blocks a change if it makes things worse): a new prompt can't ship if recall drops.

**16. 🆕 Two domain risks to raise with the business.**
- **Auto-"No" by location:** if the location data is wrong (tagged "CANADA" but US clients were hit), the incident is never even looked at. Consider writing these with a "needs human glance" flag instead of a silent No.
- **Feeding the agent its own past decisions:** "similar incidents" context includes *the agent's own earlier flags*. If v10 was wrong about one incident, it nudges later ones toward the same mistake, a self-reinforcing loop. Prefer 2LOD-approved human decisions as the examples.

**17. Docs drift (source doc appendix):** `README_RUN.md` and the prompt README are out of date. Updating them is a good, safe first PR (pull request: a proposed change submitted for review).

---

## Part 7: If I built this backend from scratch

The guiding idea: **boring, durable, few moving parts.** The current team's best instinct (deterministic code, one LLM call per judgment) stays. Most of the changes are about making the plumbing survive restarts, multiple servers and auditors.

### Shape

```
            ┌──────────────┐     SSO login (OIDC)
 UI ───────▶│  API process │◀──── roles: MIMT / 1LOD / 2LOD / RegRel
            │  (FastAPI)   │
            └──────┬───────┘
                   │ ONE transaction: change incident + audit row + job row
                   ▼
            ┌──────────────┐
            │  PostgreSQL  │  incidents, assessments, drafts, reviews,
            │              │  audit_log, jobs, prompt_versions
            └──────┬───────┘
                   │ workers claim jobs: SELECT … FOR UPDATE SKIP LOCKED
                   ▼
            ┌──────────────┐      ┌──────────────┐
            │ Worker proc. │─────▶│ LLM gateway  │
            │ (same code)  │      └──────────────┘
            └──────────────┘
```

### Key choices

1. **Same core stack: Python, FastAPI, Postgres.** They're right for this problem, and the team knows them. No Kafka, no Redis, no Kubernetes on day one.

2. **Postgres as the job queue, instead of LISTEN/NOTIFY + an in-memory dict.**
   - Add a `jobs` table: `id, kind, incident_number, status, attempts, run_after, error`.
   - Workers grab work with `SELECT … FOR UPDATE SKIP LOCKED`: lock a row so no other worker takes it, and skip rows others have already locked. That's a safe, multi-worker queue built into Postgres. The `procrastinate` library packages this pattern.
   - Jobs survive restarts.
   - Retries and backoff are just columns.
   - `GET /jobs/{id}` works from any server.
   - NOTIFY can stay, but only as a "wake up, there's work" nudge, never as the record of truth.

3. **No triggers carrying business logic. Use the "transactional outbox" instead.**
   - When a reviewer hits Approve, the API does, *in one transaction*:
     1. Validate the state transition.
     2. Update the incident.
     3. Insert an audit row with the real user ID.
     4. Insert a `jobs` row ("advance workflow").
   - Either all of it happens or none of it does.
   - **Outbox** = writing "work to do next" into your own database in the same transaction, so it can never be lost.
   - All logic lives in Python, where you can read it, test it and grep for it. No more `_clear_flag` trick, and no event-name mismatches between two codebases.

4. **The data model is append-only where it matters.**
   - `assessments` and `drafts` are only ever inserted, never updated. Each row carries `prompt_version`, `prompt_hash` (a fingerprint of the exact prompt text), `model`, `created_by`.
   - The "current" draft is simply the latest row.
   - Unique constraints are enforced by the database.
   - Schema changes are managed with **Alembic** (a migration tool: versioned scripts that change the database structure step by step, kept in git). No more hand-run `db_setup.sql`.
   - Reviews get their own `reviews` table (`incident, level, decision, feedback, reviewer_id, at`) instead of `lod1_*` / `lod2_*` columns on `incidents`. That gives a full history of every round, not just the latest.

5. **A thin, single LLM module.**
   - One shared client.
   - Token cached and refreshed under a lock.
   - TLS on.
   - Retries only on 429/5xx/timeouts.
   - A hard timeout.
   - Every call logs model, tokens, latency and cost, but not raw incident text.
   - Structured output with `Literal["Yes","No"]` fields, plus an "evidence" field quoting the incident text that justifies each answer, so reviewers can check the reasoning quickly.
   - No LangChain: the OpenAI SDK plus Pydantic is enough.

6. **Prompts and evaluation as a first-class pipeline.**
   - Prompts live in versioned files (as now).
   - A fixed **golden dataset** (a frozen set of 2LOD-approved incidents used as the test).
   - A CLI command `eval assessment --prompt v11` that prints the confusion matrix.
   - **CI** (Continuous Integration: automated checks that run on every code change) blocks a prompt change if **recall** on the golden set drops.

7. **Reference data in Postgres, not Excel.**
   - Load the CUSO sheet into a table with a version/upload date.
   - Use Postgres's `pg_trgm` extension for fuzzy matching. **Trigram** matching compares strings by overlapping 3-letter chunks; it's fast and indexed.
   - Now "which reference data did this assessment use?" is answerable.

8. **Security from day one:**
   - SSO + roles.
   - Every audit row has a real person.
   - Secrets come from the bank's secret manager, not `.env`.
   - Strict CORS.
   - Logs redacted.

9. **Testing:**
   - Unit tests for the rules and the state machine. They're pure functions, so they're trivial to test.
   - A few end-to-end tests with the LLM **mocked** (replaced by a fake that returns canned answers), so tests are fast, free and deterministic.

10. **Deployment:**
    - One Docker image (a packaged, portable copy of the app and its dependencies), run as two process types: `api` and `worker`.
    - Scale either independently.
    - Because state is all in Postgres, running 1 or 5 of each just works.

### What not to build yet
- A message broker.
- Microservices (splitting into many small separately deployed services).
- An agent framework.
- Vector search / embeddings (`get_embeddings` exists but is unused, and nothing here needs semantic search yet).

Add those only when a measured problem demands them. Most of the current pain (hung jobs, 407s, lost events, single-process limits) comes from plumbing, not from missing fancy tech.

---
