# TASK: Build backend_v2 for Anchor AI from scratch

You are building a brand-new backend in a new top-level folder `backend_v2/`.
The existing `backend/` is over-engineered and is NOT a reference for structure.
Do not copy its architecture, file layout, helpers, or tables.

## Hard boundaries
- Create and edit files ONLY inside `backend_v2/`. Do not modify, move or delete anything else (old backend, frontends, DB setup scripts).
- Do not touch the old Postgres schema. backend_v2 uses its own schema `anchor_v2` in the same database (reuse the existing POSTGRES_* env vars).
- backend_v2 runs on port 8001 so the old backend keeps working beside it.
- Do not wire any frontend to it in this task.
- If a requirement here is ambiguous or conflicts with what you find, STOP and ask. Never resolve ambiguity by adding code, options or fallbacks.

## The one rule that overrides everything
The code must contain only what the workflow below needs. A reviewer must not be able to delete or inline anything. Target: 500–700 lines of Python total, excluding prompt text and tests. If you pass 800, stop and cut before continuing.

Banned:
- Files or folders named utils, helpers, common, services, tools, core, config, or logging.
- Any class other than Pydantic models. No service, manager, client or repository classes.
- A function whose body is a single call to another function, and any wrapper around psycopg, FastAPI, logging or the OpenAI SDK.
- An ORM, Alembic, LangChain, tenacity, a job queue, LISTEN/NOTIFY, DB triggers, or async DB code.
- try/except that only logs and re-raises, checks for states that cannot happen, and unused parameters.
- Docstrings or comments that restate the code. Comment only a non-obvious "why".
- Endpoints, columns, fields, env vars or settings not listed in this prompt. No health endpoint, pagination, batch endpoints, evaluation code, email, users or auth.
- Logging setup beyond one `logging.basicConfig(...)` line in main.py. Other modules use `logging.getLogger(__name__)`. Never log prompt or incident text.

## What the app does
POC incident-notification system for RBC and City National Bank (CNB). Three roles: MIMT, 1LOD, 2LOD. No auth: every request carries the header `X-Role: MIMT | 1LOD | 2LOD`.

1. MIMT submits an incident.
2. Deterministic rules run. If a rule decides, that is the assessment.
3. Otherwise the assessment agent (one structured LLM call) judges 4 criteria and returns Reportable or Not Reportable. MIMT's work ends here.
4. Not Reportable: 1LOD confirms it (lifecycle ends) or overrides to Reportable (triggers drafting).
5. Reportable: the drafting agent (one structured LLM call) fills the fixed notification template.
6. 1LOD reviews the draft: comments, edits it directly, sends it back to the drafting agent for a re-draft, or approves and sends to 2LOD.
7. 2LOD reviews: comments, edits it directly, sends it back to 1LOD, or approves (lifecycle complete). 2LOD can never trigger the drafting agent.
8. 2LOD can view and comment on a draft while it is still with 1LOD.
9. While a draft is in review, it can be rejected as not reportable.

## Stack
Python, FastAPI with plain `def` endpoints, psycopg 3 with psycopg_pool, Pydantic v2, the openai SDK. Slow agent work runs through FastAPI `BackgroundTasks`. Nothing else unless the LLM connection requires it (see "LLM").

## Files (exactly these)
backend_v2/
  main.py        app, CORS for the localhost frontend origins, all routes
  models.py      Pydantic models: intake, assessment, draft, comment
  db.py          connection pool, runs schema.sql at startup
  schema.sql     the one table
  workflow.py    rules, transition table, run_assessment, run_drafting
  llm.py         OAuth token + one function that makes a structured call
  prompts.py     the two system prompts, copied verbatim
  reference.py   ONLY if the active assessment prompt needs reference data (see "Agents")
  tests/test_workflow.py
  requirements.txt, .env.example, README.md (how to run and test, under 30 lines)

## Database: exactly one table
Schema `anchor_v2`, table `incidents`, created with CREATE ... IF NOT EXISTS at startup:

  incident_number  text primary key
  status           text not null
  intake           jsonb not null            -- what MIMT submitted
  assessment       jsonb                     -- null until assessed
  draft            jsonb                     -- null until drafted
  comments         jsonb not null default '[]'
  activity         jsonb not null default '[]'
  updated_at       timestamptz not null default now()

No other tables, columns, indexes or triggers. Every write that reads then modifies a JSONB column does so in one transaction with SELECT ... FOR UPDATE.

JSONB shapes:
- assessment: { reportable: bool, source: "rule" | "agent" | "1lod_override", criteria: { data_system_impact, operational_impact, financial_reputational_impact, systemic_risk } } where each criterion is { met: bool, reasoning: str }. For source "rule", criteria is replaced by a single `reasoning` string.
- draft: title, current_status, date_of_occurrence, type, comment, legal_entity, priority_rating, description, impact, management_actions, root_cause, pir, executive_lead_name, executive_lead_contact. priority_rating is one of: "P1 - Critical", "P1 - Significant", "P2 - High", "P3 - Elevated", "P3 - Moderate".
- comment: { id, role, body, anchor, resolved, created_at, edited_at }. anchor is null for a general comment, or { field, quote } for a comment on highlighted text.
- activity entry: { at, actor, event }. actor is a role, "Rule", "Assessment Agent" or "Drafting Agent".

## Intake fields (models.py)
Required: incident_number, incident_title, incident_type, priority, impact_start_time, legal_entity, impacted_location, impacted_applications, incident_description, current_status, business_impact, incident_manager.
Optional: historical_priority_details, created_time, impact_duration, closed_time, change_related, resolving_group, root_cause.
One validator: if current_status is "Resolved", every field is required.

## Status and transitions
Statuses: assessing, awaiting_1lod_decision, drafting, lod1_review, lod2_review, completed, not_reportable.

Express the actions as ONE data table in workflow.py (action -> allowed roles, allowed statuses, next status) and enforce it in ONE place. Wrong role returns 403. Wrong status returns 409.

  confirm_not_reportable  1LOD        awaiting_1lod_decision     -> not_reportable
  override_reportable     1LOD        awaiting_1lod_decision     -> drafting (start drafting)
  redraft                 1LOD        lod1_review                -> drafting (start drafting)
  approve                 1LOD        lod1_review                -> lod2_review
  approve                 2LOD        lod2_review                -> completed
  send_back               2LOD        lod2_review                -> lod1_review
  reject                  1LOD, 2LOD  lod1_review, lod2_review   -> not_reportable

Automatic transitions:
- create -> assessing -> (reportable) drafting -> lod1_review
- create -> assessing -> (not reportable) awaiting_1lod_decision
- drafting always ends in lod1_review

If an agent call fails, leave the status unchanged and append an activity entry with the error. `retry` re-runs the agent that the current status implies. This also recovers incidents stranded by a restart.

Every transition, agent result, draft edit and failure appends one activity entry and sets updated_at.

## Endpoints (exactly these)
  POST   /incidents                          MIMT only. Validate, insert, start assessment in background. 201.
  GET    /incidents                          List: incident_number, title, priority, legal_entity, status, updated_at. No filters; the frontend filters.
  GET    /incidents/{n}                      The full row.
  POST   /incidents/{n}/actions/{action}     The transition table above.
  PUT    /incidents/{n}/draft                Replace the draft. Allowed only for the role the incident is waiting on (1LOD in lod1_review, 2LOD in lod2_review).
  POST   /incidents/{n}/comments             1LOD or 2LOD, in lod1_review or lod2_review.
  PATCH  /incidents/{n}/comments/{id}        Change `body` (author's role only; sets edited_at) and/or `resolved` (1LOD or 2LOD).
  DELETE /incidents/{n}/comments/{id}        Author's role only. Hard delete.
  POST   /incidents/{n}/retry                Only in assessing or drafting.

## Agents
Read these old files for CONTENT ONLY (rules, prompt text, LLM connection details). Ignore their structure. Do not read the rest of `backend/`.
- backend/services/assessment_service.py                    the deterministic rules
- backend/evaluation/prompt_iterations/prompt_config.py     which prompt versions are active
- the two active prompt modules it names
- backend/tools/assessment_tools.py, backend/tools/drafting_tools.py, backend/tools/template_schema.json    what goes into each LLM call and what comes out
- backend/tools/reference_data_tools.py                     only if the active assessment prompt consumes reference data
- backend/utils/llm_service.py                              OAuth and TLS/proxy details

Rules: port every deterministic rule that exists in the old code into one function `apply_rules(intake)` that returns an assessment or None. Do not invent rules. In your report, list each rule and the file and line it came from.

Prompts: copy the two ACTIVE system prompts into prompts.py character for character. Do not edit, shorten or "improve" them.

Context: build exactly the inputs the active prompts consume, nothing more. "Similar past incidents", if the prompt uses them, come from the anchor_v2 incidents table. If and only if the assessment prompt uses CUSO application or regulatory-requirement data, load it in reference.py from the workbook at REFERENCE_DATA_FILE_PATH, in memory, the way the old code does.

Assessment: one structured call returning the four criteria. reportable = any criterion met.

Drafting: copy factual fields from intake in Python, with no LLM. One structured call writes the prose fields. For a re-draft, also send the current draft and every UNRESOLVED comment (with its anchor quote). After a successful re-draft, mark those comments resolved.

## LLM (llm.py)
- OpenAI-compatible gateway with OAuth client credentials. Env: LLM_OAUTH_URL, LLM_CLIENT_ID, LLM_CLIENT_SECRET, LLM_BASE_URL, LLM_MODEL.
- Cache the token in module globals with its expiry, guarded by one threading.Lock.
- One function: (system prompt, user content, Pydantic model) -> parsed model, using `client.beta.chat.completions.parse`.
- 120-second request timeout. Retries: the SDK's own `max_retries=2`, nothing else.
- Reproduce the old file's TLS, certificate and proxy behaviour exactly (rbc_security certs, LLM_VERIFY_SSL, LLM_CA_BUNDLE, LLM_USE_ENV_PROXY). That part is known to work on the RBC network. Leave out its logging, retry decorators, embeddings and class structure.

## Tests (tests/test_workflow.py)
pytest with FastAPI TestClient against real Postgres in a throwaway schema, with the LLM function monkeypatched. Cover:
1. Reportable: create -> lod1_review -> redraft -> approve -> send_back -> approve -> approve -> completed.
2. Not reportable: confirm_not_reportable.
3. Not reportable: override_reportable -> draft exists.
4. A rule short-circuit makes no LLM call.
5. reject from lod1_review and from lod2_review.
6. 2LOD redraft returns 403. approve in the wrong status returns 409.
7. Comments: add general and anchored, edit, resolve, delete. 2LOD can comment in lod1_review. Only unresolved comments reach the re-draft call.
8. Agent failure leaves the status unchanged, and retry recovers.
9. Intake validation, including the Resolved rule.

## Order of work
1. Read the old files listed above. Write down the rules, the active prompt versions and the prompt inputs before writing any code.
2. schema.sql, db.py, models.py.
3. workflow.py and llm.py.
4. main.py.
5. Tests. Run them until they all pass.
6. If LLM credentials exist in your environment, run one real incident end to end. If they do not, say so. Do not claim it ran.
7. Deletion pass: for every function, import, field and line, ask whether the tests still pass and the workflow still works without it. If yes, delete it. Re-run the tests.

## Final report
- `wc -l` for every file in backend_v2/.
- Full pytest output.
- The rules you ported, with their sources.
- Which prompt versions you copied.
- Whether the real-LLM run happened.
- Anything in this prompt you could not satisfy, stated plainly.
