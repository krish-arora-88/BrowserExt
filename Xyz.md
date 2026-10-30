# Anchor AI: the `backend/` folder, line by line

**Repo:** `anx0-anchor-incident-notification-ai` · **Branch in the screenshots:** `feature/simplify-codebase-20261028`
**Source:** 205 photos of the VS Code editor, transcribed and merged by line number into 47 files (about 8,000 lines).

This document contains every line of code that was visible in the screenshots. Each file is split into small chunks: you see the code with its real line numbers, then a bullet-by-bullet explanation of what each line does and why it's there. Lines are referred to as **L42**, matching the gutter numbers.

---

## How to read this document

### Markers in the code

| Marker | Meaning |
|---|---|
| `⟦lines X–Y not visible in the screenshots⟧` | No photo showed these lines. The explanation says what was probably there, labelled as an inference. |
| `⟦CUT⟧` | The line ran past the right edge of the screen; everything up to the cut is exact. |
| `⟦?⟧` | The characters just before it were hard to read (glare, cursor, scrollbar); best reading shown. |
| `⟦inferred⟧` | Not visible, but the content is near-certain from the surrounding code (closing brackets). |

### Transcription caveats

- **Dashes:** in comments, docstrings and prompt text, `-` vs `–` vs `—` often can't be told apart in a photo. Treat dash characters in prose as approximate.
- **Banner comments** like `# ── Section ─────` have the right text, but the exact number of `─` characters is estimated.
- **IDE overlays** were removed: git-blame ghost text, "author" CodeLens lines, error squiggles and lint messages.
- **Before relying on an exact string** (a prompt, a SQL filter), check it against the real file in the repo.

### What's missing

- **Every line of every photographed file is now transcribed.** Gaps in `config.py`, `template_schema.json`, `drafting_agent_prompt_v0.py`, `drafting_service.py` and `llm_service.py` were filled in from follow-up photos.
- **Files in `backend/` with no screenshots:**
  - `backend/README.md` and `backend/requirements.txt`.
  - The active prompt modules `assessment_agent_prompt_v1…v10` and `drafting_agent_prompt_v1…v4`. Only the `v0` prompts were photographed, and the app currently runs assessment **v10** and drafting **v4** (set in `prompt_config.py`).
- **Possibly incomplete:** `main.py` ends at line 88 in the photos. That looks like the natural end (the last router is mounted), but it can't be confirmed.

---

## The big picture

Anchor AI automates part of a bank's **regulatory incident notification** process. When a major operational incident happens (an `INC…` ticket), the bank may be required to notify the **FRB/OCC**. The process has two questions, each with an LLM agent:

1. **Is this incident reportable?** The **Assessment Agent** answers 4 FRB criteria (Yes/No each). Any "Yes" means the incident is reportable.
2. **If it is, what do we tell the regulator?** The **Drafting Agent** fills in the notification template (title, description, impact, root cause, management actions, …).

Humans stay in the loop at every step. MIMT logs the incident, 1LOD and 2LOD review and give feedback, and Reg Relations sends the final notification. Every agent output is saved as a versioned row, so the evaluation scripts can score it against what the humans wrote.

### Layers (top to bottom)

```
 HTTP request (operator UI / curl)
        │
        ▼
 main.py ── FastAPI app: CORS, lifespan (DB check + start workflow listener), mounts routers
        │
        ▼
 routers/*.py ── thin HTTP layer: validate params → build schemas.py object → call a service
        │          slow LLM work is wrapped in a handler and queued: job_queue.submit_job → 202 + job_id
        ▼
 services/*.py ── business logic / orchestration
        │   assessment_service  → decide reportability for 1 or many incidents
        │   drafting_service    → fill the notification template, LLM-as-judge evaluation
        │   intake_service      → create/read incidents, "MIMT input complete"
        │   review_service      → reviewer edits, approve / feedback
        │   workflow_service    → state machine: moves an incident through its lifecycle
        │   workflow_listener   → Postgres LISTEN/NOTIFY thread that feeds workflow_service
        │   job_queue, operation_runtime, common, eval_cache → plumbing
        ▼
 tools/*.py ── LangChain @tool functions that do the actual reads, writes and LLM calls
        │   data_tools (db_query/db_write + prompt-version stamping), assessment_tools,
        │   drafting_tools (+ template_schema.json), reference_data_tools (Excel lookups), audit_tools
        ▼
 utils/*.py ── infrastructure
            llm_service (OAuth-authenticated OpenAI-compatible client), postgres_client (pooled DB),
            sql_utils (safe SQL building)

 agents/*.py ── identity/metadata for each agent (name, tools, system prompt) loaded from
                evaluation/prompt_iterations/prompts/<active version> via loader.py + prompt_config.py
 evaluation/*.py ── offline/endpoint scoring of agent output vs 2LOD-approved human ground truth
```

### An incident's journey

1. **Intake.** MIMT creates the incident (`POST /api/incidents`), then marks their input complete (`POST /api/incidents/{n}/mimt-complete`).
2. **Trigger.** That write fires a **Postgres trigger**, which sends `NOTIFY workflow_events, '{"event_type": …, "incident_number": …}'`.
3. **Listen.** `workflow_listener.py` receives the event on its own thread and calls `workflow_service.handle_event`.
4. **Assess.** The workflow engine runs `assessment_service.run_single_assessment`:
   - A rule-based shortcut handles non-US/Global locations.
   - It then gathers context (MIMT draft, CUSO app lookup, regulatory requirements, similar past incidents).
   - It makes **one structured LLM call** in `assessment_tools.evaluate_reportability`.
   - The result is saved to `reportability_assessments`, stamped with the prompt version and revision.
5. **Draft.** If the incident is reportable, `drafting_service.run_single_draft` runs:
   - It copies the factual fields from `incidents` (`populate_template_fields`, no LLM).
   - **One LLM call** writes the prose fields (`generate_template_content`).
   - The draft is saved to `template_details`.
6. **Review.** 1LOD and 2LOD review the draft (`review_service`). "Approve" moves the incident forward. "Feedback" re-runs the Drafting Agent at the `1LOD R&C` / `2LOD R&C` stage, with the feedback in the prompt.
7. **Submit.** Reg Relations produces the final version, and the incident is marked completed.

Every step is written to `audit_logs`.

### Prompt versioning and evaluation (the ML side)

- Each prompt version is its own Python module in `evaluation/prompt_iterations/prompts/`.
- `prompt_config.py` names the active one, and `loader.py` imports it.
- `data_tools` stamps every agent-written row with a short label (e.g. `v10`), so you can always tell which prompt produced which output.
- Rows with `prompt_version IS NULL` are **human-written**. Human assessments approved by 2LOD (`final_approval_by_lod == 'Yes'`) are the **ground truth**.
- `evaluate_assessment_prompts.py` builds confusion matrices (accuracy/precision/recall/F1) per FRB criterion.
- `evaluate_drafting_prompts.py` uses **LLM-as-judge** to rate each drafted field High/Medium/Low against the human Reg Relations draft.

### Glossary

| Term | Meaning in this codebase |
|---|---|
| **MIMT** | Major Incident Management Team: logs incidents and writes the first human draft (`MIMT Draft`). |
| **1LOD / 2LOD** | First / second line of defence. Reviewers who approve or send feedback; 2LOD sign-off makes a human assessment ground truth. |
| **Reg Relations** | Regulatory Relations: owns the final notification sent to the regulator. |
| **FRB / OCC** | US regulators (Federal Reserve Board / Office of the Comptroller of the Currency). The 4 assessment criteria are FRB criteria. |
| **Template stage** | Which version of the notification a row is: `Drafting Agent Draft`, `1LOD R&C`, `2LOD R&C`, `Reg Relations` (plus human-only `MIMT Draft`). |
| **Revision** | Assessment counter, bumped when new 1LOD/2LOD feedback arrives (`compute_assessment_revision`). |
| **CUSO Applications** | Reference sheet of ~2,600 bank applications (criticality, data class, SOX flags), fuzzy-matched for context. |
| **PIR** | Post-Incident Review, a field copied from the incident. |
| **LISTEN/NOTIFY** | Postgres's built-in pub/sub. DB triggers publish events, and the listener thread subscribes. |
| **LLM-as-judge** | Using a second LLM call to grade free-text output, where exact matching is meaningless. |
| **202 + job_id** | Async pattern: slow endpoints queue a background job and you poll `GET /api/jobs/{job_id}`. |

### Suggested reading order

1. `main.py` → `config.py` → `schemas.py`: the skeleton and data shapes.
2. `routers/` → `services/`: what each endpoint does.
3. `services/workflow_service.py` + `workflow_listener.py`: the automation.
4. `tools/` → `utils/`: where the LLM and DB calls happen.
5. `evaluation/`: how prompts are measured. Start with `prompt_iterations/README.md`.

---

## Contents

1. [`backend/main.py`](#1-backendmainpy)
2. [`backend/config.py`](#2-backendconfigpy)
3. [`backend/logger.py`](#3-backendloggerpy)
4. [`backend/schemas.py`](#4-backendschemaspy)
5. [`backend/agents/__init__.py`](#5-backendagents__init__py)
6. [`backend/agents/assessment_agent.py`](#6-backendagentsassessment_agentpy)
7. [`backend/agents/drafting_agent.py`](#7-backendagentsdrafting_agentpy)
8. [`backend/routers/__init__.py`](#8-backendrouters__init__py)
9. [`backend/routers/intake.py`](#9-backendroutersintakepy)
10. [`backend/routers/assessment.py`](#10-backendroutersassessmentpy)
11. [`backend/routers/drafting.py`](#11-backendroutersdraftingpy)
12. [`backend/routers/review.py`](#12-backendroutersreviewpy)
13. [`backend/routers/operations.py`](#13-backendroutersoperationspy)
14. [`backend/services/__init__.py`](#14-backendservices__init__py)
15. [`backend/services/common.py`](#15-backendservicescommonpy)
16. [`backend/services/intake_service.py`](#16-backendservicesintake_servicepy)
17. [`backend/services/assessment_service.py`](#17-backendservicesassessment_servicepy)
18. [`backend/services/drafting_service.py`](#18-backendservicesdrafting_servicepy)
19. [`backend/services/review_service.py`](#19-backendservicesreview_servicepy)
20. [`backend/services/workflow_service.py`](#20-backendservicesworkflow_servicepy)
21. [`backend/services/workflow_listener.py`](#21-backendservicesworkflow_listenerpy)
22. [`backend/services/job_queue.py`](#22-backendservicesjob_queuepy)
23. [`backend/services/operation_runtime.py`](#23-backendservicesoperation_runtimepy)
24. [`backend/services/eval_cache.py`](#24-backendserviceseval_cachepy)
25. [`backend/tools/__init__.py`](#25-backendtools__init__py)
26. [`backend/tools/data_tools.py`](#26-backendtoolsdata_toolspy)
27. [`backend/tools/assessment_tools.py`](#27-backendtoolsassessment_toolspy)
28. [`backend/tools/drafting_tools.py`](#28-backendtoolsdrafting_toolspy)
29. [`backend/tools/template_schema.json`](#29-backendtoolstemplate_schemajson)
30. [`backend/tools/reference_data_tools.py`](#30-backendtoolsreference_data_toolspy)
31. [`backend/tools/audit_tools.py`](#31-backendtoolsaudit_toolspy)
32. [`backend/utils/__init__.py`](#32-backendutils__init__py)
33. [`backend/utils/_logger.py`](#33-backendutils_loggerpy)
34. [`backend/utils/llm_service.py`](#34-backendutilsllm_servicepy)
35. [`backend/utils/postgres_client.py`](#35-backendutilspostgres_clientpy)
36. [`backend/utils/sql_utils.py`](#36-backendutilssql_utilspy)
37. [`backend/evaluation/prompt_iterations/README.md`](#37-backendevaluationprompt_iterationsreadmemd)
38. [`backend/evaluation/prompt_iterations/prompt_config.py`](#38-backendevaluationprompt_iterationsprompt_configpy)
39. [`backend/evaluation/prompt_iterations/loader.py`](#39-backendevaluationprompt_iterationsloaderpy)
40. [`backend/evaluation/prompt_iterations/prompts/__init__.py`](#40-backendevaluationprompt_iterationsprompts__init__py)
41. [`backend/evaluation/prompt_iterations/prompts/assessment_agent_prompt_v0.py`](#41-backendevaluationprompt_iterationspromptsassessment_agent_prompt_v0py)
42. [`backend/evaluation/prompt_iterations/prompts/drafting_agent_prompt_v0.py`](#42-backendevaluationprompt_iterationspromptsdrafting_agent_prompt_v0py)
43. [`backend/evaluation/confusion_matrix_utils.py`](#43-backendevaluationconfusion_matrix_utilspy)
44. [`backend/evaluation/compare_agent_vs_human.py`](#44-backendevaluationcompare_agent_vs_humanpy)
45. [`backend/evaluation/evaluate_assessment_prompts.py`](#45-backendevaluationevaluate_assessment_promptspy)
46. [`backend/evaluation/evaluate_drafting_prompts.py`](#46-backendevaluationevaluate_drafting_promptspy)
47. [`backend/README_RUN.md`](#47-backendreadme_runmd)


---

## 1. `backend/main.py`

*88 lines · complete*

This is the **entry point of the web server**. It creates the FastAPI `app` object, which `uvicorn` (or root `main.py --server`) runs. It does three things: (1) defines a *lifespan* function that checks the database and starts the Postgres LISTEN/NOTIFY workflow listener when the server starts, then stops both when it shuts down; (2) adds CORS middleware so a browser front-end on another origin can call the API; (3) plugs in the five routers (`assessment`, `drafting`, `intake`, `review`, `operations`) that hold the real endpoints. It contains no business logic. It also re-exports `run_batch_assessment` / `run_batch_drafting` so the older command-line entry point (root `main.py`) can still import them from here.


#### Lines 1–10

````python
 1  """FastAPI application for Anchor AI - Regulatory Notification System.
 2
 3  This module is intentionally thin: it wires up the FastAPI app (lifespan,
 4  CORS, routers) and re-exports the CLI-compatible batch-run functions used by
 5  root main.py. All actual request handling lives in backend/routers/*, and
 6  all domain orchestration lives in backend/services/* - see those packages
 7  for the assessment/drafting workflows, the background job queue, and lazy
 8  agent construction.
 9  """
10
````

- **L1–9** The module docstring (a string literal as the first statement of a file, which Python stores as `__doc__`). It states the design rule for the whole backend: this file only *wires things together*. HTTP handling lives in `backend/routers/*` and domain logic lives in `backend/services/*`. It also mentions the "CLI-compatible batch-run functions used by root main.py". That is the `main.py` at the repo root, a different file from this one.
- **L10** Blank line.


#### Lines 11–23

````python
11  from contextlib import asynccontextmanager
12
13  from fastapi import FastAPI
14  from fastapi.middleware.cors import CORSMiddleware
15
16  from backend.routers import assessment, drafting, operations, intake, review
17  from backend.services.assessment_service import run_batch_assessment
18  from backend.services.drafting_service import run_batch_drafting
19  from backend.services import workflow_listener
20  from backend.utils.postgres_client import get_client
21  from backend.logger import get_logger
22  from backend.config import ENVIRONMENT
23
````

- **L11** `asynccontextmanager` is a decorator from the standard library. It turns an `async def` generator function (one containing `yield`) into an *async context manager*: an object usable with `async with`, where the code before `yield` runs on entry and the code after `yield` runs on exit. FastAPI's lifespan feature expects exactly this shape.
- **L13** `FastAPI` is the class that represents the whole web application.
- **L14** `CORSMiddleware` handles Cross-Origin Resource Sharing headers (explained at L75–82).
- **L16** Imports the five router modules. Each one defines a `router = APIRouter(...)` object holding a group of endpoints. They are mounted at L84–88.
- **L17–18** Imports the CLI batch functions only so they can be re-exported (see `__all__` at L27). `main.py` itself never calls them.
- **L19** `workflow_listener` is the background thread that listens to Postgres `NOTIFY` events and drives the incident lifecycle (MIMT complete → assess → draft → LOD review ...). See `backend/services/workflow_listener.py`.
- **L20** `get_client()` returns the process-wide `PostgresClient` *singleton* (one shared connection-pool object, created on first call; see `backend/utils/postgres_client.py`).
- **L21** `get_logger` is the project's logging helper from `backend/logger.py`.
- **L22** `ENVIRONMENT` (e.g. `"development"`) comes from `backend/config.py`. It is used only in the startup log line.


#### Lines 24–28

````python
24  logger = get_logger(__name__)
25
26  # Re-exported for backward compatibility (root main.py imports these).
27  __all__ = ["app", "run_batch_assessment", "run_batch_drafting"]
28
````

- **L24** Creates a module-level logger named after this module (`__name__` evaluates to `"backend.main"`), so its log lines are tagged with where they came from.
- **L26–27** `__all__` lists the names that `from backend.main import *` exports. It also documents this module's public surface. Listing `run_batch_assessment`/`run_batch_drafting` here is the "backward compatibility" shim: older code (root `main.py`) did `from backend.main import run_batch_assessment`, and that import still works.


#### Lines 29–41

````python
29
30  @asynccontextmanager
31  async def lifespan(app: FastAPI):
32      """Application lifespan manager - startup and shutdown logic."""
33      # Startup
34      logger.info(f"Starting Anchor AI ({ENVIRONMENT})")
35
36      # Initialize database connection
37      client = get_client()
38      if not client.health_check():
39          raise RuntimeError("Database connection failed - cannot start application")
40      logger.info("Database connection verified")
41
````

- **L30** `@asynccontextmanager` decorates the function below. A decorator is a function that wraps another function. The `@name` line is shorthand for `lifespan = asynccontextmanager(lifespan)`.
- **L31** `async def lifespan(app: FastAPI)` is the hook FastAPI calls once at server start-up. `app: FastAPI` is a *type hint*: it documents the parameter's expected type and is not enforced at runtime. The `app` argument isn't used inside the function, but FastAPI always passes it.
- **L34** Logs `Starting Anchor AI (development)`. It is an f-string: the `f"..."` prefix lets `{ENVIRONMENT}` be substituted inline.
- **L37** Gets (and on first call, creates) the shared Postgres client, including its connection pool.
- **L38–39** `client.health_check()` returns `True` if a trivial query succeeds. If the DB is unreachable, the code raises `RuntimeError`, which aborts start-up. This is a deliberate *fail-fast*: the server refuses to start rather than run without a database and fail on every request.
- **L40** Records that the DB check passed.


#### Lines 42–50

````python
42      # LLM construction and OAuth token acquisition happen only when an
43      # assessment, draft, or evaluation job actually needs them.
44
45      # Start the event-driven workflow engine: a LISTEN/NOTIFY background
46      # thread that reacts to incident state changes (MIMT complete, LOD
47      # approve/feedback, reg relations complete) and drives the lifecycle.
48      workflow_listener.start_listener()
49      logger.info("Application startup complete")
50
````

- **L42–43** A comment explaining a design choice. Nothing LLM-related happens at start-up. The LLM client and its OAuth token are created lazily, the first time a job needs them. This keeps start-up fast and means the server still boots even if the LLM gateway is down.
- **L45–47** A comment describing the workflow engine. It is a background thread using Postgres `LISTEN/NOTIFY` (Postgres's built-in pub/sub). When the incident state changes (MIMT marks intake complete, a LOD approves or gives feedback, Reg Relations completes), the database emits a notification and the listener reacts. This is how the pipeline advances automatically, without a human calling `/api/assess` by hand.
- **L48** `start_listener()` creates the single `WorkflowListener` and starts its thread. It is idempotent, so a second call does no harm.
- **L49** Final start-up log line.
- **L50** Blank.


#### Lines 51–58

````python
51      yield
52
53      # Shutdown
54      logger.info("Shutting down Anchor AI")
55      workflow_listener.stop_listener()
56      client.close()
57      logger.info("Application shutdown complete")
58
````

- **L51** `yield` is the split point. Everything above runs before the server accepts requests. FastAPI then pauses here for the entire life of the server, and everything below runs when the server is shutting down (Ctrl-C, container stop).
- **L54** Logs that shutdown has begun.
- **L55** Stops the listener thread and its worker pool, so it doesn't keep running or hold a DB connection after the app exits.
- **L56** Closes the Postgres connection pool cleanly.
- **L57** Final shutdown log line.


#### Lines 59–74

````python
59
60  # Create FastAPI app
61  app = FastAPI(
62      title="Anchor AI - Regulatory Notification System",
63      description="AI-powered regulatory incident notification system for financial institutions",
64      version="0.1.0",
65      lifespan=lifespan,
66      openapi_tags=[
67          {"name": "Assessment", "description": "Run regulatory reportability assessments."},
68          {"name": "Drafting", "description": "Generate regulatory notification drafts."},
69          {"name": "Intake", "description": "Create/update incidents and start the workflow (MIMT)."},
70          {"name": "Review", "description": "Edit drafts and run the 1LOD/2LOD feedback loop."},
71          {"name": "Operations", "description": "Service health and agent metadata."},
72      ],
73  )
74
````

- **L61** Creates the application object. Its name `app` is what uvicorn looks for (e.g. `uvicorn backend.main:app`).
- **L62–64** `title`, `description` and `version` are metadata. They appear at the top of the auto-generated Swagger page at `/docs`.
- **L65** `lifespan=lifespan` registers the start-up/shutdown function defined above.
- **L66–72** `openapi_tags` gives each tag a description in Swagger. Every router sets `tags=[...]` (e.g. `APIRouter(tags=["Assessment"])`), so its endpoints are grouped under these headings in `/docs`. The descriptions summarise the five areas: assessment, drafting, intake (creating incidents / MIMT starting the workflow), review (editing drafts and the 1LOD/2LOD feedback loop), and operations (health, job status, agent metadata).


#### Lines 75–83

````python
75  # Add CORS middleware
76  app.add_middleware(
77      CORSMiddleware,
78      allow_origins=["*"],  # Configure appropriately for production
79      allow_credentials=True,
80      allow_methods=["*"],
81      allow_headers=["*"],
82  )
83
````

- **L76** `add_middleware` wraps every request and response. Middleware is code that runs around every endpoint.
- **L77** `CORSMiddleware`: browsers block a web page on one origin (e.g. `http://localhost:3000`, the UI) from calling an API on another origin (e.g. `:8000`) unless the API returns the right `Access-Control-Allow-*` headers. This middleware adds those headers.
- **L78** `allow_origins=["*"]` allows any website. The inline comment admits it should be tightened for production.
- **L79–81** Allows cookies/credentials, all HTTP methods and all request headers.
- Note: browsers do not accept the wildcard `*` origin together with credentials. Starlette works around this by echoing the caller's origin back, so in effect any site can make credentialed calls. That is fine for local development, and it is the reason for the "configure for production" warning.


#### Lines 84–88

````python
84  app.include_router(assessment.router)
85  app.include_router(drafting.router)
86  app.include_router(intake.router)
87  app.include_router(review.router)
88  app.include_router(operations.router)
````

- **L84–88** `include_router` mounts each router's endpoints onto the app. The routers already contain full paths (e.g. `/api/assess`), so no `prefix=` is passed here. Each line connects one group of endpoints: assessment, drafting, intake, review, operations (`/health`, `/agents`, `/api/jobs/{job_id}`, `/`).


---

## 2. `backend/config.py`

*80 lines · complete*

The **central settings module**. Almost every value is read from an environment variable with `os.getenv("NAME", "default")`, so the same code can run on a laptop, in dev, or in production just by changing the `.env` file or the container's environment. Settings are plain module-level constants that other modules import (`from backend.config import POSTGRES_HOST, ...`). It covers the reference-data workbook path, Postgres connection and pool sizes, logging level/environment name, LLM network timeouts/proxy/TLS options, batch concurrency, SMTP email settings, and the Postgres NOTIFY channel name used by the workflow engine. Prompt versions are deliberately *not* configured here (see L66–69).


#### Lines 1–18

````python
 1  """Configuration for Anchor AI backend."""
 2
 3  import os
 4  from pathlib import Path
 5
 6  # Project paths
 7  PROJECT_ROOT = Path(__file__).parent.parent
 8  BACKEND_ROOT = Path(__file__).parent
 9
10  # Reference data workbook (CUSO Applications / Regulatory Requirements sheets)
11  # consumed in-memory by backend/tools/reference_data_tools.py - not loaded
12  # into the database (see meeting_notes_sep_24.txt: avoid context overflow by
13  # keeping the large CUSO Applications sheet out of the DB/prompt and querying
14  # it via fuzzy lookup instead).
15  REFERENCE_DATA_FILE_PATH = os.getenv(
16      "REFERENCE_DATA_FILE_PATH", str(PROJECT_ROOT / "Data Sample File.xlsx")
17  )
18
````

- **L1** The module docstring: a one-line description of the file.
- **L3** `import os` gives access to `os.getenv`, which every setting below uses to read environment variables.
- **L4** `Path` from `pathlib` represents filesystem paths as objects instead of strings. You can call `.parent` on one, and join paths with `/`.
- **L6–8** Two anchor paths that the rest of the file builds on:
  - `__file__` is the path of this file, `backend/config.py`.
  - `.parent` is the folder containing it (`backend/`), so `BACKEND_ROOT` is the backend folder.
  - `.parent.parent` goes up one more level to the **repo root**, so `PROJECT_ROOT` is the folder holding `backend/`, `frontend/`, `data/` and so on. Locating files relative to the code, rather than to wherever you ran `python` from, makes the paths work from any working directory.
  - Note: `BACKEND_ROOT` isn't used by any other transcribed backend file.
- **L10–14** A comment explaining *why* the reference workbook stays a file:
  - The workbook holds two sheets, CUSO Applications and Regulatory Requirements.
  - `backend/tools/reference_data_tools.py` reads it into memory instead of loading it into Postgres.
  - It points to a team decision (`meeting_notes_sep_24.txt`): the CUSO sheet (~2,600 apps) is too large to put in the DB or a prompt without overflowing the LLM's context window. Instead, only the few best fuzzy-matched rows are pulled in per incident.
- **L15–17** `REFERENCE_DATA_FILE_PATH` is the location of that workbook:
  - It uses the env var if set, otherwise `<repo root>/Data Sample File.xlsx`.
  - `PROJECT_ROOT / "..."` uses `pathlib`'s `/` operator to join paths.
  - `str(...)` turns the resulting `Path` into a plain string, because `os.getenv` defaults are strings.
- **L18** Blank.
- Note: `config.py` never calls `load_dotenv()`; it only reads whatever is already in the process environment. Values from `.env` reach these constants only if something loaded `.env` *before* `backend.config` was first imported. `llm_service.py` and the evaluation scripts call `load_dotenv()` at import time, and the root `main.py` (outside `backend/`) may too. If settings like the DB host fall back to defaults while your `.env` says otherwise, check that import order first.


#### Lines 19–28

````python
19  # PostgreSQL configuration
20  POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
21  POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
22  POSTGRES_DB = os.getenv("POSTGRES_DB", "anchor_ai")
23  POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "public")
24  POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
25  POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
26  POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "10"))
27  POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "5"))
28
````

- **L20–25** The Postgres connection details: host, port, database name (`anchor_ai`), schema (`public` by default; a dev setup may use a schema such as `anchor_ai_dev`), user and password. Environment variables are always strings, so the port is wrapped in `int(...)`. An empty default password is normal for local trust-auth setups.
- **L26–27** Connection-pool sizing for `PostgresClient`. Up to 10 connections are kept ready, plus 5 extra "overflow" connections under load. Pools avoid the cost of opening a new DB connection on every query, and they matter here because batch jobs and the workflow listener query concurrently.


#### Lines 29–32

````python
29  # Application configuration
30  LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
31  ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
32
````

- **L30** `LOG_LEVEL` is used by `backend/logger.py` to set the root logger's level (`DEBUG`, `INFO`, `WARNING` ...).
- **L31** `ENVIRONMENT` labels the run. It appears in the start-up log line in `main.py` and in the log file name in `logger.py`.


#### Lines 33–51

````python
33  # LLM Configuration (loaded directly by llm_service.py from env vars:
34  # LLM_OAUTH_URL, LLM_CLIENT_ID, LLM_CLIENT_SECRET, LLM_BASE_URL, LLM_MODEL)
35  OAUTH_TOKEN_REFRESH_BUFFER_MINUTES = int(os.getenv("OAUTH_TOKEN_REFRESH_BUFFER_MINUTES", "5"))
36  OAUTH_REQUEST_TIMEOUT = (10, 30)  # (connect_timeout, read_timeout) in seconds
37  # Previously unset, which let a single stalled LLM/proxy connection hang a
38  # chat/chat_structured call (and therefore a whole serial batch job) forever
39  # with no exception ever raised - see the batch_assessment job that hung for
40  # 16+ hours on one incident. tenacity's @retry on LLMService.chat() only
41  # kicks in once a timeout actually fires, so this bound is what makes retries
42  # (and eventual per-incident failure, rather than an indefinite hang) possible.
43  LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "120"))
44  LLM_USE_ENV_PROXY = os.getenv("LLM_USE_ENV_PROXY", "false").lower() in {
45      "1", "true", "yes", "on",
46  }
47  LLM_CA_BUNDLE = os.getenv("LLM_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or True
48  LLM_VERIFY_SSL = os.getenv("LLM_VERIFY_SSL", "false").lower() in {
49      "1", "true", "yes", "on",
50  }
51
````

- **L33–34** A comment. The LLM's credentials and endpoint (`LLM_OAUTH_URL`, `LLM_CLIENT_ID`, `LLM_CLIENT_SECRET`, `LLM_BASE_URL`, `LLM_MODEL`) are **not** defined here. `backend/utils/llm_service.py` reads them straight from the environment. Only the networking knobs live in this file.
- **L35** `OAUTH_TOKEN_REFRESH_BUFFER_MINUTES` controls when the LLM client refreshes its OAuth access token: 5 minutes *before* expiry, so no request is sent with a token that expires mid-flight.
- **L36** `OAUTH_REQUEST_TIMEOUT = (10, 30)` is a hard-coded tuple, not from env. In the `requests` library a `(connect, read)` tuple means "give up if the TCP connection takes over 10 s, or if the server goes 30 s without sending data". It is used for the token request.
- **L37–42** A war story in comment form. Without a timeout, one stalled connection to the LLM proxy hung a batch job for 16+ hours with no error raised. The retry library `tenacity` (decorating `LLMService.chat()`) only retries when an exception occurs, so a timeout is what turns a silent hang into an exception that can be retried, or eventually recorded as a failure for that one incident.
- **L43** `LLM_REQUEST_TIMEOUT` (120 s by default) is the per-LLM-call timeout described above. Note that its env var name is `LLM_REQUEST_TIMEOUT_SECONDS`, which differs from the Python constant's name.
- **L44–46** `LLM_USE_ENV_PROXY` turns the env var into a real boolean. `.lower() in {"1", "true", "yes", "on"}` accepts several spellings; `{...}` here is a *set* literal, which gives a fast membership test. When true, the LLM HTTP client honours the `HTTP(S)_PROXY` environment variables (the corporate proxy).
- **L47** `LLM_CA_BUNDLE` is the certificate-authority bundle used to verify the LLM server's TLS certificate. It takes the first value that is set: `LLM_CA_BUNDLE`, then `SSL_CERT_FILE`, then the literal `True`. `True` means "use the library's default trusted CAs". This uses the `or` chain idiom: `a or b` returns `a` if it is truthy, otherwise `b`.
- **L48–50** `LLM_VERIFY_SSL` is parsed the same way as L44–46, but it **defaults to false**. `llm_service.py` does `tls_verify = LLM_CA_BUNDLE if LLM_VERIFY_SSL else False`, so out of the box TLS certificate verification for LLM calls is turned off.
- Note: disabled TLS verification is common behind corporate SSL-inspecting proxies, but it is a security weakness: it allows man-in-the-middle attacks on traffic that carries incident data. It is worth setting `LLM_VERIFY_SSL=true` with a proper CA bundle in any shared or production environment.


#### Lines 52–54

````python
52  # Agent Configuration
53  HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "10"))
54
````

- **L53** `HISTORY_LIMIT` (default 10) is described as "Agent Configuration". It is presumably the number of prior conversation or audit messages kept as agent context. It isn't imported by any of the backend files available here, so it looks like a leftover setting.


#### Lines 55–65

````python
55  # Batch assessment/drafting concurrency (backend/services/assessment_service.py,
56  # drafting_service.py). Previously kept at 1 (fully serial) because the
57  # corporate LLM proxy's auth handshake (see LLM_USE_ENV_PROXY) has been observed
58  # to intermittently fail with "407 Proxy Authentication Required" under
59  # concurrent connections - LLMService._ensure_valid_token() now holds a lock
60  # around token refresh (previously unsynchronized, so concurrent workers could
61  # each kick off their own simultaneous OAuth refresh - a plausible contributor
62  # to those 407s), so bumping to 2 here. Raise further only if your proxy/network
63  # setup is confirmed to handle more concurrent auth reliably.
64  BATCH_MAX_WORKERS = int(os.getenv("BATCH_MAX_WORKERS", "2"))
65
````

- **L55–63** A long comment explaining the concurrency setting. Batch assessment and drafting (`assessment_service.py` and `drafting_service.py`, via `services/common.execute_batch`) used to process one incident at a time. That was because concurrent connections through the corporate proxy sometimes failed with HTTP **407 Proxy Authentication Required**. A suspected cause was that every worker thread refreshed the OAuth token at the same moment. `LLMService._ensure_valid_token()` now holds a lock around the refresh, so only one thread refreshes and the others wait. That made it safe to raise the setting to 2.
- **L64** `BATCH_MAX_WORKERS` (default 2) sets the size of the `ThreadPoolExecutor` in `execute_batch`, i.e. how many incidents are assessed or drafted in parallel within one batch job.


#### Lines 66–70

````python
66  # NOTE: Prompt versioning is NOT configured here. The active prompt version per
67  # agent (which also determines which SYSTEM_PROMPT module actually gets loaded)
68  # lives in backend/evaluation/prompt_iterations/prompt_config.py - see
69  # ASSESSMENT_AGENT_PROMPT_VERSION and DRAFTING_AGENT_PROMPT_VERSION there.
70
````

- **L66–69** A signpost comment. The *active prompt version* for each agent (e.g. `assessment_agent_prompt_v10`) is set in `backend/evaluation/prompt_iterations/prompt_config.py`, not here and not via env vars. Keeping it next to the prompt files means changing the version is a code change that can be reviewed.


#### Lines 71–77

````python
71  # Email configuration (for notifications)
72  SMTP_HOST = os.getenv("SMTP_HOST", "")
73  SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
74  SMTP_USER = os.getenv("SMTP_USER", "")
75  SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
76  SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "noreply@anchor-ai.com")
77
````

- **L72–76** SMTP (outgoing email) settings for sending notifications: server host, port 587 (the standard port for submission with STARTTLS), login user and password, and the "From" address. An empty `SMTP_HOST` effectively means email is not configured. None of the backend modules available here import these settings, so email sending is probably not yet wired up, or happens outside `backend/`.


#### Lines 78–80

````python
78  # Workflow configuration
79  WORKFLOW_EVENT_CHANNEL = os.getenv("WORKFLOW_EVENT_CHANNEL", "workflow_events")
80
````

- **L79** `WORKFLOW_EVENT_CHANNEL` is the name of the Postgres `LISTEN/NOTIFY` channel (`workflow_events`). Database triggers or the services `NOTIFY` on this channel, and `workflow_listener.py` `LISTEN`s on it. Both sides must use the same name, which is why it is a shared config value.


---

## 3. `backend/logger.py`

*157 lines · complete*

The project's **logging setup**. Importing this module, which nearly every backend file does via `from backend.logger import get_logger`, configures Python's root logger once so that every log line goes both to the console (stdout) and to a fresh timestamped file under `logs/`. It also provides small helpers. `get_logger(name)` is the standard per-module logger. `log_tool_call`, `log_llm_call` and `log_agent_step` write structured JSON lines describing tool invocations, LLM calls and agent steps; the LangChain tools in `backend/tools/*` and `backend/utils/llm_service.py` use them. This gives a debuggable trace of what the AI did for each incident.


#### Lines 1–9

````python
1  """Enhanced logging for Anchor AI with structured context."""
2
3  import logging
4  import sys
5  import json
6  from pathlib import Path
7  from datetime import datetime
8  from backend.config import LOG_LEVEL, ENVIRONMENT
9
````

- **L1** Module docstring.
- **L3–7** Standard-library imports. `logging` is Python's built-in logging framework, `sys` is used for `sys.stdout`, `json` serialises the structured log entries, `Path` (from `pathlib`) handles filesystem paths as objects, and `datetime` provides timestamps.
- **L8** Reads `LOG_LEVEL` and `ENVIRONMENT` from `backend/config.py`. Note the import order: `config` is imported *before* logging is configured, so `config` itself can't use this logger.


#### Lines 10–17

````python
10  # Create logs directory
11  LOGS_DIR = Path(__file__).parent.parent / "logs"
12  LOGS_DIR.mkdir(exist_ok=True)
13
14  # Configure logging format
15  LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
16  DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
17
````

- **L11** `Path(__file__)` is this file (`backend/logger.py`). `.parent.parent` goes up two levels to the repo root, and `/ "logs"` appends the folder name. The result is `<repo>/logs`.
- **L12** Creates that folder. `exist_ok=True` means "no error if it already exists". This runs at *import time*, so just importing the module creates the folder.
- **L15** The line format. `%(asctime)s` is the timestamp, `%(name)s` is the logger name (e.g. `backend.services.job_queue`), then the level (INFO/ERROR) and the message. Example: `2026-10-01 12:00:00 - backend.main - INFO - Database connection verified`.
- **L16** The timestamp format used for `%(asctime)s`.


#### Lines 18–22

````python
18  # Create a new timestamped log file for each process run, so separate runs
19  # don't get interleaved/appended into one ever-growing file.
20  _RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
21  UNIFIED_LOG = LOGS_DIR / f"anchor_ai_{ENVIRONMENT}_{_RUN_TIMESTAMP}.log"
22
````

- **L18–19** The comment explains why: every process run gets its own log file, so separate runs aren't mixed together in one huge file.
- **L20** The process start time formatted as e.g. `20261001_120000`. It is a module-level constant computed once, at import.
- **L21** The full log file path, e.g. `logs/anchor_ai_development_20261001_120000.log`. It is an f-string combining the environment name and the timestamp.


#### Lines 23–32

````python
23  # Root logger configuration - everything goes to unified log
24  #
25  # NOTE: We deliberately do NOT use logging.basicConfig() here. basicConfig()
26  # is a no-op if the root logger already has handlers attached, and some
27  # third-party/internal packages (e.g. rbc_security.enable_certs(), imported
28  # by backend.utils.llm_service) call logging.info(...) at import time, which
29  # implicitly calls basicConfig() with Python's defaults (stderr, WARNING
30  # level) before this module gets a chance to run. That silently swallowed
31  # all our INFO-level logs (nothing printed to stdout, nothing written to the
32  # log file). Configuring the root logger directly avoids that race.
````

- **L23–32** A comment that explains a real bug they hit. The usual shortcut, `logging.basicConfig()`, *does nothing if the root logger already has handlers*. An internal RBC package (`rbc_security.enable_certs()`, imported by `llm_service.py`) logs at import time. Calling `logging.info(...)` with no handlers configured silently runs `basicConfig()` with defaults (stderr, WARNING level). If that happened first, the project's own `basicConfig` call became a no-op, and all INFO logs vanished. The fix is to configure the root logger by hand (next lines), wiping any handlers someone else added.


#### Lines 33–37

````python
33  _formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
34  _root_logger = logging.getLogger()
35  _root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
36  _root_logger.handlers.clear()
37
````

- **L33** Builds one `Formatter` with the format and date strings above. Both handlers below share it.
- **L34** `logging.getLogger()` with no name returns the **root logger**. Every named logger (`backend.main`, `tools`, `llm`, ...) forwards its records up to the root by default ("propagation"), so configuring the root once covers the whole app.
- **L35** Sets the minimum level. `getattr(logging, "INFO")` looks up the constant `logging.INFO` by name, and the third argument, `logging.INFO`, is the fallback if `LOG_LEVEL` is misspelled. `.upper()` lets users write `info`.
- **L36** `handlers.clear()` removes any handlers that were attached earlier, e.g. by the implicit `basicConfig` described in L23–32. This is the core of the fix.


#### Lines 38–46

````python
38  _stream_handler = logging.StreamHandler(sys.stdout)
39  _stream_handler.setFormatter(_formatter)
40  _root_logger.addHandler(_stream_handler)
41
42  _file_handler = logging.FileHandler(UNIFIED_LOG)
43  _file_handler.setFormatter(_formatter)
44  _root_logger.addHandler(_file_handler)
45
46
````

- **L38–40** Adds a `StreamHandler` writing to **stdout** (not stderr) with the shared format, so the logs show in the terminal or container logs.
- **L42–44** Adds a `FileHandler` that writes the same lines to the per-run log file from L21. After this, every log record goes to both places.


#### Lines 47–59

````python
47  def get_logger(name: str) -> logging.Logger:
48      """Get a logger with the given name.
49
50      Args:
51          name: Logger name (usually __name__)
52
53      Returns:
54          Configured logger instance
55      """
56      logger = logging.getLogger(name)
57      return logger
58
59
````

- **L47** `get_logger(name: str) -> logging.Logger`. The `-> ...` is a return-type hint.
- **L48–55** Docstring in Google style (`Args:` / `Returns:` sections).
- **L56–57** Simply returns `logging.getLogger(name)`. `logging.getLogger` always returns the same object for the same name, so calling this repeatedly is cheap. The wrapper exists so every module goes through `backend.logger`. Importing this module is what triggers the root configuration above, which guarantees logging is set up before anything logs.


#### Lines 60–74

````python
60  def get_agent_logger() -> logging.Logger:
61      """Get specialized logger for agent activity."""
62      return logging.getLogger("agent")
63
64
65  def get_tool_logger() -> logging.Logger:
66      """Get specialized logger for tool calls."""
67      return logging.getLogger("tools")
68
69
70  def get_llm_logger() -> logging.Logger:
71      """Get specialized logger for LLM calls."""
72      return logging.getLogger("llm")
73
74
````

- **L60–62** `get_agent_logger()` returns a logger named `"agent"`, used by `log_agent_step`.
- **L65–67** `get_tool_logger()` returns a logger named `"tools"`, used by `log_tool_call`.
- **L70–72** `get_llm_logger()` returns a logger named `"llm"`, used by `log_llm_call`.
- These fixed names make it easy to grep the log for one category (e.g. `- tools -`), or to change one category's level independently.


#### Lines 75–100

````python
 75  def log_tool_call(tool_name: str, inputs: dict, outputs: any = None, error: str = None):
 76      """Log a tool call with inputs and outputs.
 77
 78      Args:
 79          tool_name: Name of the tool being called
 80          inputs: Input parameters to the tool
 81          outputs: Output from the tool (optional)
 82          error: Error message if tool failed (optional)
 83      """
 84      tool_logger = get_tool_logger()
 85
 86      log_entry = {
 87          "timestamp": datetime.now().isoformat(),
 88          "tool": tool_name,
 89          "inputs": inputs,
 90      }
 91
 92      if outputs is not None:
 93          log_entry["outputs"] = str(outputs)[:500]  # Truncate long outputs
 94
 95      if error:
 96          log_entry["error"] = error
 97          tool_logger.error(json.dumps(log_entry, default=str))
 98      else:
 99          tool_logger.info(json.dumps(log_entry, default=str))
100
````

- **L75** `log_tool_call(tool_name, inputs, outputs=None, error=None)`. Arguments with `= None` are optional.
- Note: `outputs: any` uses the built-in *function* `any` as a type hint where `typing.Any` was intended. Python doesn't enforce hints, so it runs fine, but type checkers will flag it. The same happens on L102 with `response: any`.
- **L76–83** Docstring.
- **L84** Gets the `"tools"` logger.
- **L86–90** Builds a dict for the entry: an ISO-8601 timestamp, the tool name and its inputs.
- **L92–93** Adds the output only if one was given. `str(outputs)[:500]` converts it to text and keeps the first 500 characters (`[:500]` is slice syntax), so a huge DB result doesn't flood the log.
- **L95–97** If there was an error, records it and logs at **ERROR** level.
- **L98–99** Otherwise logs at INFO. In both cases the dict is serialised with `json.dumps(..., default=str)`. `default=str` tells `json` to call `str()` on anything it can't serialise natively (datetimes, Decimals, custom objects) instead of crashing.


#### Lines 101–128

````python
101
102  def log_llm_call(model: str, messages: list, response: any = None, error: str = None):
103      """Log an LLM API call.
104
105      Args:
106          model: Model name
107          messages: Input messages
108          response: LLM response (optional)
109          error: Error message if call failed (optional)
110      """
111      llm_logger = get_llm_logger()
112
113      log_entry = {
114          "timestamp": datetime.now().isoformat(),
115          "model": model,
116          "messages": messages,
117      }
118
119      if response is not None:
120          log_entry["response"] = str(response)[:500]  # Truncate long responses
121
122      if error:
123          log_entry["error"] = error
124          llm_logger.error(json.dumps(log_entry, default=str))
125      else:
126          llm_logger.info(json.dumps(log_entry, default=str))
127
128
````

- **L102–126** `log_llm_call` has the same structure as `log_tool_call`, applied to LLM calls. It records the model name and the full `messages` list (system/user prompts), plus the response truncated to 500 characters or an error, on the `"llm"` logger. It is called from `backend/utils/llm_service.py`.
- Note: unlike the response, `messages` is logged in full, untruncated. With long system prompts and incident text, each LLM call can add a very large line to the log file. Incident details then also end up in plain-text logs, which is worth keeping in mind for data-handling rules.


#### Lines 129–150

````python
129  def log_agent_step(agent_name: str, step: str, details: dict = None):
130      """Log an agent workflow step.
131
132      Args:
133          agent_name: Name of the agent
134          step: Description of the step
135          details: Additional details (optional)
136      """
137      agent_logger = get_agent_logger()
138
139      log_entry = {
140          "timestamp": datetime.now().isoformat(),
141          "agent": agent_name,
142          "step": step,
143      }
144
145      if details:
146          log_entry["details"] = details
147
148      agent_logger.info(json.dumps(log_entry, default=str))
149
150
````

- **L129** `log_agent_step(agent_name, step, details=None)` records a milestone in an agent's workflow (e.g. "context gathered").
- **L137** Gets the `"agent"` logger.
- **L139–143** Entry with the timestamp, the agent name and the step description.
- **L145–146** Attaches the optional `details` dict if one was given. `if details:` is false for both `None` and an empty dict.
- **L148** Always logs at INFO as JSON. There is no error branch here, unlike the two functions above.


#### Lines 151–157

````python
151  # Suppress verbose third-party loggers
152  logging.getLogger("httpx").setLevel(logging.WARNING)
153  logging.getLogger("httpcore").setLevel(logging.WARNING)
154  logging.getLogger("httpx2").setLevel(logging.WARNING)
155  logging.getLogger("openai").setLevel(logging.WARNING)
156  logging.getLogger("urllib3").setLevel(logging.WARNING)
157
````

- **L151–156** Raises the threshold for noisy third-party libraries to WARNING, so their routine INFO/DEBUG chatter is suppressed: `httpx` and `httpcore` (the HTTP client stack used by the OpenAI SDK), `openai`, and `urllib3` (used by `requests`). Otherwise every HTTP request to the LLM would print several lines. `httpx2` is an unusual name. It may refer to an internal package, or it may be a harmless typo (silencing a logger nobody uses has no effect).
- **L157** End of file.


---

## 4. `backend/schemas.py`

*648 lines · complete*

This is the app's **data-contract file**: every shape of JSON that goes into or comes out of the FastAPI endpoints is declared here as a **Pydantic model** (a class that inherits from `BaseModel`, so Pydantic validates and converts data to match the declared field types). It sits underneath everything else: the routers (`backend/routers/*.py`) use these classes as request bodies and `response_model`s, and the services (`assessment_service`, `drafting_service`, `job_queue`, `review_service`, `common`) build and return them. It also holds a few plain constants for the reviewer UI (`ROLE_TO_STAGE`, `TEMPLATE_CONTENT_FIELDS`). Roughly, the file runs in the same order as the incident lifecycle: assessment, then assessment evaluation, then drafting, then drafting evaluation, then async jobs and health, and finally the operator UI (intake, review, latest assessment and draft).


#### Lines 1–7

````python
1  """Pydantic schemas for API requests and responses."""
2
3  from datetime import datetime
4  from typing import Any, Dict, Literal, Optional, List
5
6  from pydantic import BaseModel, ConfigDict, Field
7
````

- **L1** The module docstring (a string literal at the top of a file that documents it). It says this file only holds Pydantic schemas for API requests and responses.
- **L3** `datetime` is imported because some response models carry timestamps, such as `submitted_at` on jobs. Pydantic will parse ISO strings into `datetime` objects and serialize them back to ISO strings in JSON.
- **L4** These are **type hints** from `typing`:
  - `Any` means any type.
  - `Dict[K, V]` and `List[T]` are a dict and a list with typed contents.
  - `Optional[X]` means "X or `None`".
  - `Literal[...]` means "must be exactly one of these values".

  Pydantic reads these hints at runtime and enforces them.
- **L6** These are the three Pydantic building blocks:
  - `BaseModel` is the base class for every schema.
  - `ConfigDict` sets per-model configuration. Here it is used for OpenAPI examples.
  - `Field(...)` attaches validation rules and documentation to a single field.


#### Lines 8–17

````python
 8  TemplateStage = Literal["Drafting Agent Draft", "1LOD R&C", "2LOD R&C", "Reg Relations"]
 9  JobOperation = Literal[
10      "assessment",
11      "batch_assessment",
12      "draft",
13      "batch_draft",
14      "evaluate_draft",
15      "evaluate_assessment",
16  ]
17
````

- **L8** `TemplateStage` is a **type alias**. Its value is a `Literal` listing the four stages the Drafting Agent can write a template for. The stages follow the review pipeline:
  - The agent's first draft.
  - The 1LOD (first line of defence) Risk & Control review.
  - The 2LOD (second line) review.
  - Regulatory Relations, the team that actually submits to the regulator.

  Declaring the alias once means any field typed `TemplateStage` rejects any other string with a 422 error. "MIMT Draft" (the human MIMT team's own draft) is deliberately absent, because the agent never writes to that stage.
- **L9–16** `JobOperation` is another `Literal` alias. It lists the names of the six kinds of background job `job_queue.submit_job` can run. The routers pass these exact strings: `"draft"`, `"batch_draft"`, `"evaluate_draft"` in `routers/drafting.py`, and the assessment ones in `routers/assessment.py`. The job response models below use the alias, so a job status always reports one of these.


#### Lines 18–30

````python
18  class AssessmentRequest(BaseModel):
19      """Request to assess a single incident's reportability."""
20
21      model_config = ConfigDict(json_schema_extra={"example": {"incident_number": "INC0575428"}})
22
23      incident_number: str = Field(
24          ...,
25          description="Incident number to assess (e.g., INC0575428)",
26          min_length=1,
27          max_length=50,
28          pattern=r"^[A-Za-z0-9_-]+$",
29      )
30
````

- **L18** `class AssessmentRequest(BaseModel)` is the input for assessing one incident. It is used in `routers/assessment.py`.
- **L19** The class docstring. FastAPI shows it in the generated `/docs` page.
- **L21** `model_config = ConfigDict(json_schema_extra=...)` adds an example payload to the model's JSON Schema, so the Swagger UI at `/docs` pre-fills `{"incident_number": "INC0575428"}`. It has no effect on validation.
- **L23–29** `incident_number: str = Field(...)` declares a required string field:
  - The first argument `...` (Python's `Ellipsis`) is Pydantic's way of saying "no default, so this field is required".
  - `description` goes into the API docs.
  - `min_length=1` and `max_length=50` bound the length.
  - `pattern=r"^[A-Za-z0-9_-]+$"` is a regular expression: only letters, digits, underscore and hyphen, from start (`^`) to end (`$`).

  This blocks junk or injection-looking strings before they ever reach the database layer. The `r"..."` prefix makes it a *raw string*, so backslashes are not treated as escapes.


#### Lines 31–55

````python
31
32  class BatchAssessmentRequest(BaseModel):
33      """Request to run batch assessment on multiple or all incidents."""
34
35      model_config = ConfigDict(json_schema_extra={
36          "examples": [
37              {"incident_numbers": None, "force": False, "missing_only": False},
38              {"incident_numbers": ["INC0575428", "INC0600745"], "force": False, "missing_only": False},
39          ]
40      })
41
42      incident_numbers: Optional[List[str]] = Field(
43          None,
44          description="Specific incident numbers to assess. If omitted, all incidents are assessed.",
45          max_length=500,
46      )
47      force: bool = Field(
48          False,
49          description="Re-assess incidents even if an assessment already exists for the active prompt version.",
50      )
51      missing_only: bool = Field(
52          False,
53          description="Only assess incidents that have no agent assessment yet (only human assessments).",
54      )
55
````

- **L32–33** `BatchAssessmentRequest` asks for assessment of many incidents, or all of them.
- **L35–40** These are two OpenAPI examples: the "all incidents" form (`incident_numbers: None`) and an explicit list. The plural `"examples"` key lets Swagger show several examples.
- **L42–46** `incident_numbers: Optional[List[str]]` defaults to `None` (the first positional argument of `Field`), which means "assess all incidents". On a list field, `max_length=500` limits the **number of items**, not the string length. It caps a single batch at 500 incidents.
- **L47–50** `force: bool` defaults to `False`. When `True`, the batch re-runs incidents that already have an assessment for the active prompt version. In the router this is set from `mode == "force"`.
- **L51–54** `missing_only: bool` means "only assess incidents that so far have only a human assessment". Note: the `/api/batch/assess` router always passes `missing_only=False`, so over HTTP this flag is effectively never turned on. It exists for the service-level and CLI callers.


#### Lines 56–68

````python
56
57  class AssessmentResponse(BaseModel):
58      """Response containing assessment results."""
59
60      incident_number: str = Field(..., description="Incident number assessed")
61      reportability_flag: str = Field(..., description="Yes or No")
62      data_system_impact: str = Field(..., description="Assessment of data/system impact")
63      operational_impact: str = Field(..., description="Assessment of operational impact")
64      financial_reputational_impact: str = Field(..., description="Assessment of financial/reputational impact")
65      systemic_risk: str = Field(..., description="Assessment of systemic risk")
66      rationale: str = Field(..., description="Overall reasoning for determination")
67      assessment_id: str = Field(..., description="Database ID of the assessment record")
68
````

- **L57** `AssessmentResponse` is the result of one assessment. `assessment_service` builds it, and it becomes the job `result` you get when polling `/api/jobs/{id}`.
- **L60** The incident that was assessed.
- **L61** `reportability_flag` is the headline answer: `"Yes"` or `"No"`, meaning whether this incident must be reported to the regulator.
- **L62–65** These are the four **FRB criteria** the agent judges separately: data/system impact, operational impact, financial/reputational impact, and systemic risk. (FRB is most likely the US Federal Reserve Board, given the example legal entity "City National Bank (CNB)" later in the file. That is an inference.) Each is a free-text string. Elsewhere the code expects it to *start* with "Yes" or "No"; see `parse_yes_no` in `evaluation/confusion_matrix_utils.py`.
- **L66** `rationale` is the agent's overall reasoning.
- **L67** `assessment_id` is the primary key of the saved `reportability_assessments` row, so the UI or caller can reference it.
- All fields use `Field(..., description=...)`, so every one is required.


#### Lines 69–77

````python
69
70  class BatchIncidentResult(BaseModel):
71      """Result for a single incident in a batch run."""
72
73      incident_number: str = Field(..., description="Incident number")
74      status: Literal["success", "skipped", "error"] = Field(..., description="Per-incident outcome")
75      detail: str = Field(default="", description="Short description of outcome or error")
76
77
````

- **L70** `BatchIncidentResult` is the outcome for one incident inside a batch run. `common.execute_batch`, `common.load_incidents` and the `_process_*_incident` workers in the services build these.
- **L73** Which incident this result refers to.
- **L74** `status` is restricted by a `Literal` to `"success"`, `"skipped"` or `"error"`. The batch counters below are computed by counting these values.
- **L75** `detail` defaults to an empty string. It holds a short human-readable reason, for example "Active Drafting Agent Draft already exists", or a client-safe error from `safe_batch_error`.


#### Lines 78–91

````python
78  class BatchResponse(BaseModel):
79      """Shared result counters for a batch operation."""
80
81      total: int = Field(..., description="Total incidents processed")
82      success: int = Field(..., description="Incidents processed successfully")
83      skipped: int = Field(..., description="Incidents skipped")
84      errors: int = Field(..., description="Incidents that failed")
85      results: List[BatchIncidentResult] = Field(default_factory=list, description="Per-incident results")
86
87
88  class BatchAssessmentResponse(BatchResponse):
89      """Response for a batch assessment run."""
90
91
````

- **L78** `BatchResponse` is a shared base class for both batch result types.
- **L81–84** These are the totals: processed, succeeded, skipped and errored. `common.execute_batch` fills them using `sum(item.status == "success" for item in results)`. That works because `True` counts as 1 in Python.
- **L85** `results: List[BatchIncidentResult]` holds the per-incident detail. `default_factory=list` means each new instance gets its own fresh empty list. A mutable default like `[]` should never be shared between instances, and `default_factory` is Pydantic's safe way to avoid that.
- **L88–89** `BatchAssessmentResponse(BatchResponse)` is a subclass with only a docstring and no extra fields. It inherits everything. It exists so the API docs and type hints can name the assessment-specific response separately from the drafting one (`BatchDraftResponse` at L287).


#### Lines 92–121

````python
 92  class AssessmentEvaluationRequest(BaseModel):
 93      """Request to compare Assessment Agent output against 2LOD-approved human ground truth."""
 94
 95      model_config = ConfigDict(json_schema_extra={
 96          "examples": [
 97              {"incident_numbers": None, "run_agent": False},
 98              {"incident_numbers": ["INC0575428", "INC0600745"], "run_agent": True},
 99          ]
100      })
101
102      incident_numbers: Optional[List[str]] = Field(
103          None,
104          description=(
105              "Specific incident numbers to evaluate. If omitted, all incidents with a "
106              "2LOD-approved human assessment (final_approval_by_lod = 'Yes') are used."
107          ),
108          max_length=500,
109      )
110      run_agent: bool = Field(
111          False,
112          description=(
113              "If False (default), compare against whatever assessment already exists in "
114              "the DB for the active prompt version - fast, read-only, no new LLM calls. "
115              "If True, invoke the assessment agent live for each incident (upserting its "
116              "result for the active prompt version, as a side effect) before comparing - "
117              "slower, using the same deterministic orchestration as /api/assess."
118          ),
119      )
120
121
````

- **L92–93** `AssessmentEvaluationRequest` is the input for the **evaluation suite**: compare the Assessment Agent's answers with human "ground truth" assessments that 2LOD has approved.
- **L95–100** These are two examples: evaluate everything without re-running the agent, or evaluate two incidents and run the agent live.
- **L102–109** `incident_numbers` is an optional list (500 items at most). If it is omitted, the evaluation uses every incident whose human assessment has `final_approval_by_lod = 'Yes'`, meaning only human answers that 2LOD signed off count as ground truth. The `description=( "..." "..." )` form uses Python's **implicit string concatenation**: adjacent string literals inside parentheses are joined into one string.
- **L110–119** `run_agent: bool` defaults to `False`, which is fast and read-only: it compares whatever agent assessment is already in the DB for the active prompt version. `True` calls the LLM agent fresh for each incident first. That is slower, and as a side effect it upserts (insert or update) a new assessment row.


#### Lines 122–132

````python
122  class AssessmentCriterionDetail(BaseModel):
123      """Agent vs. human comparison for a single FRB criterion on one incident."""
124
125      match: bool = Field(..., description="Whether the agent's Yes/No matches the human's Yes/No")
126      agent_value: str = Field(default="", description="Agent's full assessment text for this criterion")
127      human_value: str = Field(default="", description="Human's full assessment text for this criterion")
128      confidence: Optional[Literal["High", "Medium", "Low"]] = Field(
129          None, description="Agent's confidence for this criterion (only available when run_agent=True)"
130      )
131
132
````

- **L122–123** `AssessmentCriterionDetail` is the agent-vs-human comparison for **one** FRB criterion on **one** incident.
- **L125** `match` is true if the leading Yes/No of the agent and the human agree.
- **L126–127** These hold the full text both sides wrote, for display. They default to `""`.
- **L128–130** `confidence` is optional. It can only be `"High"`, `"Medium"`, `"Low"` or `None`. It is only populated when the agent was run live (`run_agent=True`), because the confidence level is not persisted in the DB row.


#### Lines 133–145

````python
133  class AssessmentEvaluationItem(BaseModel):
134      """Full agent-vs-human comparison detail for a single incident."""
135
136      incident_number: str
137      agent_flag: str = Field(..., description="Agent's overall reportability_flag (Yes/No)")
138      human_flag: str = Field(..., description="Human's overall reportability_flag (Yes/No)")
139      flag_match: bool = Field(..., description="Whether agent_flag == human_flag")
140      criteria: Dict[str, AssessmentCriterionDetail] = Field(
141          default_factory=dict,
142          description="Per-FRB-criterion comparison, keyed by criterion name",
143      )
144
145
````

- **L133–134** `AssessmentEvaluationItem` is the full comparison for a single incident.
- **L136** A bare type hint with no `Field` and no default (`incident_number: str`) also makes a field required. `Field` is only needed when you want a description or constraints.
- **L137–139** These hold the overall flags from both sides and whether they agree.
- **L140–143** `criteria: Dict[str, AssessmentCriterionDetail]` maps each criterion name (such as `"operational_impact"`) to its comparison. This shows **nested models**: Pydantic validates the inner objects too.


#### Lines 146–154

````python
146  class CriterionAccuracy(BaseModel):
147      """Aggregate agent-vs-human accuracy for one FRB criterion, across all evaluated incidents."""
148
149      criterion: str
150      matches: int = Field(..., description="Incidents where agent and human agreed on this criterion")
151      total: int = Field(..., description="Total incidents evaluated")
152      accuracy_pct: float = Field(..., description="matches / total * 100")
153
154
````

- **L146–147** `CriterionAccuracy` is an aggregate across all evaluated incidents for one criterion.
- **L149** The criterion name.
- **L150–152** These give how many incidents agreed, out of how many, and the percentage (`matches / total * 100`, per the description).


#### Lines 155–163

````python
155  class ConfidenceTierAccuracy(BaseModel):
156      """Agent accuracy for one FRB criterion, broken down by the agent's stated confidence tier."""
157
158      tier: Literal["High", "Medium", "Low"]
159      n: int = Field(..., description="Incidents where the agent reported this confidence tier")
160      correct: int = Field(..., description="Of those, how many matched the human ground truth")
161      accuracy_pct: Optional[float] = Field(None, description="correct / n * 100, or null if n = 0")
162
163
````

- **L155–156** `ConfidenceTierAccuracy` answers the question "when the agent said it was *High* confidence, how often was it right?". It is calibration data for one criterion.
- **L158** `tier` is the confidence level this row describes.
- **L159–160** `n` is how many times the agent reported this tier. `correct` is how many of those matched the human.
- **L161** `accuracy_pct` is `Optional[float]`, so it can be `None` (JSON `null`) when `n = 0`. That avoids a division by zero.
- **L163** This line was not visible in the screenshots. Since L162 is blank and a new class begins at L164, it was almost certainly the second blank line between classes (inferred).


#### Lines 164–179

````python
164  class ConfusionMatrix(BaseModel):
165      """2x2 Yes/No confusion matrix (predicted = agent, actual = human) plus
166      derived accuracy/precision/recall/specificity/F1."""
167
168      n: int = Field(..., description="Total incidents with both an agent and human Yes/No value for this matrix")
169      tp: int = Field(..., description="True positive: agent=Yes, human=Yes")
170      tn: int = Field(..., description="True negative: agent=No, human=No")
171      fp: int = Field(..., description="False positive: agent=Yes, human=No")
172      fn: int = Field(..., description="False negative: agent=No, human=Yes")
173      accuracy_pct: Optional[float] = Field(None, description="(TP+TN)/n * 100, or null if n = 0")
174      precision_pct: Optional[float] = Field(None, description="TP/(TP+FP) * 100, or null if undefined (no Yes predictions)")
175      recall_pct: Optional[float] = Field(None, description="TP/(TP+FN) * 100, or null if undefined (no actual Yes cases)")
176      specificity_pct: Optional[float] = Field(None, description="TN/(TN+FP) * 100, or null if undefined")
177      f1_pct: Optional[float] = Field(None, description="Harmonic mean of precision/recall * 100, or null if undefined")
178
179
````

- **L164–166** `ConfusionMatrix` is a standard 2x2 Yes/No confusion matrix. "Predicted" is the agent and "actual" is the human, with "Yes" (reportable) as the positive class. The docstring spans two lines.
- **L168** `n` counts incidents where both sides gave a parseable Yes/No.
- **L169–172** These are the four cells:
  - `tp` (both Yes).
  - `tn` (both No).
  - `fp`: the agent said reportable but the human said not, which is over-reporting.
  - `fn`: the agent said not reportable but the human said reportable. For a regulator this is the dangerous one.
- **L173–177** These are the derived metrics, each as a percentage or `None`:
  - Accuracy.
  - Precision: of the agent's Yes answers, how many were right.
  - Recall: of the true Yes cases, how many the agent caught.
  - Specificity: of the true No cases, how many the agent got right.
  - F1: the harmonic mean of precision and recall.

  `None` appears when a denominator would be 0. `metrics_to_json` in `confusion_matrix_utils.py` produces exactly these lower-snake-case keys, turning NaN into `None` and fractions into percentages.


#### Lines 180–204

````python
180  class AssessmentEvaluationResponse(BaseModel):
181      """Aggregate + per-incident results of the Assessment Agent vs. human evaluation."""
182
183      prompt_version: str = Field(..., description="Assessment agent prompt version that was evaluated")
184      incidents_evaluated: int = Field(..., description="Number of incidents with a 2LOD-approved human ground truth that were compare⟦CUT⟧
185      flag_matches: int = Field(..., description="Incidents where the agent's overall reportability_flag matched the human's")
186      flag_accuracy_pct: float = Field(..., description="flag_matches / incidents_evaluated * 100")
187      criterion_accuracy: List[CriterionAccuracy] = Field(default_factory=list, description="Per-FRB-criterion aggregate accuracy")
188      confidence_breakdown: Dict[str, List[ConfidenceTierAccuracy]] = Field(
189          default_factory=dict,
190          description="Per-FRB-criterion accuracy broken down by confidence tier, keyed by criterion name",
191      )
192      confusion_matrices: Dict[str, ConfusionMatrix] = Field(
193          default_factory=dict,
194          description=(
195              "Yes/No confusion matrix for the overall reportability_flag (key "
196              "'reportability_flag'), each of the 4 FRB criteria (keyed by criterion name), "
197              "and all 4 criteria combined (key 'all_criteria_combined')."
198          ),
199      )
200      mismatched_incidents: List[str] = Field(default_factory=list, description="Incident numbers where the overall flag did not match⟦CUT⟧
201      results: List[AssessmentEvaluationItem] = Field(default_factory=list, description="Full per-incident agent-vs-human detail")
202      message: Optional[str] = Field(None, description="Present when no matching incidents were found")
203
204
````

- **L180–181** `AssessmentEvaluationResponse` is the complete result of the assessment evaluation job. `assessment_service` builds it with `AssessmentEvaluationResponse(**summary)`. `**summary` unpacks a dict into keyword arguments.
- **L183** `prompt_version` records which assessment prompt version was evaluated. Prompts are versioned, for example `v1` and `v2`.
- **L184** `incidents_evaluated` is the count of incidents compared. The end of this line ran off the screen (⟦CUT⟧). From the pattern, the description string probably continued with "...compared")` (inferred).
- **L185–186** These give the overall-flag agreement count and the percentage.
- **L187** `criterion_accuracy` holds one `CriterionAccuracy` per FRB criterion.
- **L188–191** `confidence_breakdown` maps each criterion name to a list of tier accuracies (High, Medium, Low).
- **L192–199** `confusion_matrices` is a dict of matrices with these keys:
  - `"reportability_flag"` for the overall answer.
  - Each of the four criterion names.
  - `"all_criteria_combined"`, which pools all criterion decisions into one matrix.
- **L200** `mismatched_incidents` lists the incident numbers where the overall flag disagreed. It is handy for manual review. The end of the line was cut off on screen (⟦CUT⟧), probably `...did not match")` (inferred).
- **L201** `results` holds the full per-incident detail.
- **L202** `message` is optional. It is only set when nothing matched the filter, so the UI can show a friendly "no incidents" note instead of zeros.


#### Lines 205–223

````python
205  class DraftRequest(BaseModel):
206      """Request to draft or revise a single regulatory notification template."""
207
208      model_config = ConfigDict(json_schema_extra={
209          "examples": [
210              {"incident_number": "INC0575428", "template_stage": "Drafting Agent Draft"},
211              {
212                  "incident_number": "INC0575428",
213                  "template_stage": "1LOD R&C",
214                  "feedback": "Clarify the customer impact and incident timeline.",
215              },
216              {
217                  "incident_number": "INC0575428",
218                  "template_stage": "Drafting Agent Draft",
219                  "assessment_prompt_version": "human",
220              },
221          ]
222      })
223
````

- **L205–206** `DraftRequest` is the input for drafting or revising one notification template. The `/api/draft` router builds one from query parameters. `drafting_service._process_draft_incident` and `workflow_service._run_draft` also construct it internally.
- **L208–222** These are three examples:
  - A first agent draft.
  - A 1LOD revision with reviewer `feedback`, so the agent rewrites incorporating the comments.
  - A draft that uses the **human** assessment as context (`assessment_prompt_version: "human"`) instead of the agent's.


#### Lines 224–254

````python
224      incident_number: str = Field(
225          ...,
226          description="Incident number to draft a notification for (e.g., INC0575428)",
227          min_length=1,
228          max_length=50,
229          pattern=r"^[A-Za-z0-9_-]+$",
230      )
231      template_stage: TemplateStage = Field(
232          default="Drafting Agent Draft",
233          description=(
234              "Template stage: 'Drafting Agent Draft' (the agent's own initial draft - "
235              "not to be confused with 'MIMT Draft', the human MIMT team's own draft, "
236              "which this endpoint never writes to), '1LOD R&C', '2LOD R&C', or 'Reg Relations'"
237          )
238      )
239      feedback: Optional[str] = Field(
240          None,
241          description="Reviewer feedback to incorporate when revising an existing template",
242          max_length=10000,
243      )
244      assessment_prompt_version: Optional[str] = Field(
245          None,
246          description=(
247              "Which reportability_assessments row to use as context: omit for the active "
248              "assessment agent prompt version (default), 'human' for the human-provided "
249              "assessment (prompt_version IS NULL), or a specific version label (e.g. 'v1')."
250          ),
251          max_length=50,
252      )
253
254
````

- **L224–230** `incident_number` uses the same required, length-limited, regex-checked rule as in `AssessmentRequest`.
- **L231–238** `template_stage: TemplateStage` uses the L8 alias and defaults to `"Drafting Agent Draft"`. The description warns not to confuse it with `"MIMT Draft"`, which is the human MIMT team's own draft and is never written by this endpoint.
- **L239–243** `feedback` is optional free text, up to 10,000 characters. When present, the service loads the existing template and asks the LLM to revise it rather than draft from scratch.
- **L244–252** `assessment_prompt_version` chooses which `reportability_assessments` row feeds the drafting prompt as context:
  - `None` means the active agent prompt version.
  - `"human"` means the human row, which is stored with `prompt_version` NULL in the DB.
  - Anything else names a specific version such as `"v1"`.

  `drafting_service.resolve_assessment_prompt_version` and `assessment_prompt_version_label` interpret this value.


#### Lines 255–290

````python
255  class BatchDraftRequest(BaseModel):
256      """Request to run batch drafting on multiple or all reportable incidents."""
257
258      model_config = ConfigDict(json_schema_extra={
259          "examples": [
260              {"incident_numbers": None, "force": False},
261              {"incident_numbers": ["INC0575428"], "force": True},
262              {"incident_numbers": None, "force": False, "assessment_prompt_version": "human"},
263          ]
264      })
265
266      incident_numbers: Optional[List[str]] = Field(
267          None,
268          description="Specific incident numbers to draft. If omitted, all P1 and actively assessed reportable incidents are drafted.",
269          max_length=500,
270      )
271      force: bool = Field(
272          False,
273          description="Re-draft incidents even if a Drafting Agent Draft already exists for the active prompt version.",
274      )
275      assessment_prompt_version: Optional[str] = Field(
276          None,
277          description=(
278              "Which reportability_assessments version determines eligibility and drafting "
279              "context: omit for the active assessment agent prompt version (default), 'human' "
280              "for human-provided assessments (prompt_version IS NULL), or a specific version "
281              "label (e.g. 'v1')."
282          ),
283          max_length=50,
284      )
285
286
287  class BatchDraftResponse(BatchResponse):
288      """Response for a batch drafting run."""
289
290
````

- **L255–256** `BatchDraftRequest` drafts many incidents.
- **L258–264** These are three examples: all incidents, force-redrafting one incident, and all incidents using human assessments.
- **L266–270** `incident_numbers` is optional, with at most 500 items. Note: the description says that when it is omitted, "all P1 and actively assessed reportable incidents" are drafted. However, `drafting_service._is_draft_eligible` explicitly says priority is *no longer* a shortcut: even P1 incidents must have a `reportability_flag == 'Yes'` assessment. So the "P1" part of this description is out of date.
- **L271–274** `force` redrafts incidents even if a complete "Drafting Agent Draft" already exists for the active drafting prompt version.
- **L275–284** `assessment_prompt_version` works the same as in `DraftRequest`. Here it decides **eligibility** too: an incident is only drafted if the chosen assessment says "Yes".
- **L287–288** `BatchDraftResponse(BatchResponse)` is a drafting-specific name for the shared batch counters. It has no new fields.


#### Lines 291–331

````python
291  class DraftEvaluationRequest(BaseModel):
292      """Request to run the LLM-as-Judge evaluation of drafting agent output."""
293
294      model_config = ConfigDict(json_schema_extra={
295          "examples": [
296              {
297                  "incident_numbers": None,
298                  "template_stage": None,
299                  "drafting_versions": None,
300                  "assessment_versions": None,
301              },
302              {
303                  "incident_numbers": ["INC0575428"],
304                  "template_stage": "Drafting Agent Draft",
305                  "drafting_versions": ["v1", "v2"],
306                  "assessment_versions": ["v1", "v2", "human"],
307              },
308          ]
309      })
310
311      incident_numbers: Optional[List[str]] = Field(
312          None,
313          description="Specific incident numbers to evaluate. If omitted, all with both agent and human drafts are used.",
314          max_length=500,
315      )
316      template_stage: Optional[TemplateStage] = Field(
317          None,
318          description="Restrict evaluation to a single template stage.",
319      )
320      drafting_versions: Optional[List[str]] = Field(
321          None,
322          description="Drafting prompt versions to evaluate. If omitted, all available versions are used.",
323          max_length=100,
324      )
325      assessment_versions: Optional[List[str]] = Field(
326          None,
327          description="Assessment prompt versions to evaluate. If omitted, all available versions are used.",
328          max_length=100,
329      )
330
331
````

- **L291–292** `DraftEvaluationRequest` is the input for the **LLM-as-Judge** evaluation. A separate "judge" LLM compares each agent draft with the human draft for the same incident and stage, and rates how closely they match.
- **L294–309** These are two examples: evaluate everything, or evaluate one incident at one stage across drafting versions `v1` and `v2` and assessment versions `v1`, `v2` and `human`.
- **L311–315** `incident_numbers` is an optional filter. If it is omitted, the evaluation uses every incident that has *both* an agent and a human draft, since a pair is needed to compare.
- **L316–319** `template_stage: Optional[TemplateStage]` optionally restricts the evaluation to one stage.
- **L320–329** `drafting_versions` and `assessment_versions` are optional lists (at most 100 entries each). They choose which prompt-version **combinations** to judge. The evaluation is a grid: assessment prompt version × drafting prompt version.


#### Lines 332–340

````python
332  class RatingDistribution(BaseModel):
333      """High/Medium/Low match counts with a mean match percentage."""
334
335      high: int = Field(..., description="Count rated High match")
336      medium: int = Field(..., description="Count rated Medium match")
337      low: int = Field(..., description="Count rated Low match")
338      mean_match_pct: float = Field(..., description="Mean match score (High=100, Medium=50, Low=0)")
339
340
````

- **L332–333** `RatingDistribution` summarizes many judge verdicts.
- **L335–337** These count the High, Medium and Low match ratings.
- **L338** `mean_match_pct` turns ratings into a score. Per the description, High = 100, Medium = 50 and Low = 0, and this field is the average. It gives one number to compare prompt versions.


#### Lines 341–354

````python
341  class DraftEvaluationItem(BaseModel):
342      """Per-draft judge verdict."""
343
344      incident_number: str
345      template_stage: str
346      assessment_prompt_version: str
347      drafting_prompt_version: str
348      human_drafted_by: Optional[str] = None
349      overall_rating: Literal["High", "Medium", "Low"]
350      overall_rationale: str
351      field_ratings: dict = Field(default_factory=dict, description="Per-field High/Medium/Low ratings")
352      field_rationales: dict = Field(default_factory=dict, description="Per-field one-line rationales")
353
354
````

- **L341–342** `DraftEvaluationItem` is the judge's verdict for one agent draft.
- **L344–347** These identify which draft was judged: incident, stage, and the assessment and drafting prompt versions that produced it.
- **L348** `human_drafted_by` records who wrote the human reference draft, if known. Its default is `None`.
- **L349** `overall_rating` is High, Medium or Low (a `Literal`).
- **L350** `overall_rationale` is the judge's explanation.
- **L351–352** `field_ratings` and `field_rationales` are plain `dict`s keyed by template field (title, description, and so on). Because they are typed as bare `dict` rather than `Dict[str, str]`, Pydantic does not check their contents. They are loosely typed.


#### Lines 355–364

````python
355  class DraftCombinationSummary(BaseModel):
356      """Aggregate judge results for one assessment/drafting prompt combination."""
357
358      assessment_prompt_version: str
359      drafting_prompt_version: str
360      drafts_judged: int
361      overall: RatingDistribution
362      per_field: dict = Field(default_factory=dict, description="Per authored-field rating distributions")
363
364
````

- **L355–356** `DraftCombinationSummary` holds aggregate results for **one** (assessment version, drafting version) pair.
- **L358–360** These record which combination this is and how many drafts were judged.
- **L361** `overall: RatingDistribution` is a nested model with the High/Medium/Low counts and the mean.
- **L362** `per_field` is a dict of field name to rating distribution. It is a loose `dict`.


#### Lines 365–381

````python
365  class DraftEvaluationResponse(BaseModel):
366      """Aggregate results of the drafting LLM-as-Judge evaluation."""
367
368      prompt_version: str = Field(..., description="Legacy single drafting version, or 'multiple'")
369      drafting_prompt_versions: List[str] = Field(default_factory=list)
370      assessment_prompt_versions: List[str] = Field(default_factory=list)
371      drafts_judged: int = Field(..., description="Number of agent/human draft pairs judged")
372      overall: RatingDistribution = Field(..., description="Overall match rating distribution")
373      per_field: dict = Field(default_factory=dict, description="Per authored-field rating distributions")
374      combinations: List[DraftCombinationSummary] = Field(
375          default_factory=list,
376          description="Independent aggregate results for each assessment/drafting prompt combination",
377      )
378      drafts: List[DraftEvaluationItem] = Field(default_factory=list, description="Per-draft verdicts")
379      message: Optional[str] = Field(None, description="Present when no pairs were found")
380
381
````

- **L365–366** `DraftEvaluationResponse` is the top-level result of the drafting evaluation job. It is also what `/api/evaluate/draft/latest` returns from cache.
- **L368** `prompt_version` is a legacy field kept for backward compatibility. It holds either a single drafting version or the literal `"multiple"`.
- **L369–370** These list the drafting and assessment versions included.
- **L371** `drafts_judged` is the number of agent-vs-human pairs judged.
- **L372–373** These hold the overall and per-field distributions across everything.
- **L374–377** `combinations` gives each version pair's results separately, so you can see which prompt combination works best.
- **L378** `drafts` holds every individual verdict.
- **L379** `message` explains an empty result, for example when no draft pairs were found.


#### Lines 382–400

````python
382  class DraftResponse(BaseModel):
383      """Response containing drafted notification template."""
384
385      incident_number: str = Field(..., description="Incident number drafted")
386      template_stage: str = Field(..., description="Template stage (e.g., Drafting Agent Draft)")
387      title: str = Field(default="", description="Notification title")
388      current_status: str = Field(default="", description="Current incident status")
389      date_of_occurrence: str = Field(default="", description="Date of occurrence")
390      type: str = Field(default="", description="Incident type")
391      legal_entity: str = Field(default="", description="Legal entity name")
392      priority_rating: str = Field(default="", description="Priority rating")
393      description: str = Field(default="", description="Full narrative description")
394      impact: str = Field(default="", description="Impact assessment")
395      management_actions: str = Field(default="", description="Management actions taken")
396      root_cause: str = Field(default="", description="Root cause analysis")
397      pir: str = Field(default="", description="Post-Incident Review status")
398      template_id: str = Field(default="", description="Database ID of the template record")
399
400
````

- **L382–383** `DraftResponse` is the result of a single draft. `drafting_service._draft_response` builds it from the saved `template_details` row, and it becomes the job `result` for `/api/draft`.
- **L385–386** These are the incident and stage.
- **L387–397** These are the **notification template fields**, which mirror the sections of the regulator notification:
  - Title.
  - Current status.
  - Date of occurrence.
  - Incident type.
  - Legal entity.
  - Priority.
  - Narrative description.
  - Impact.
  - Management actions.
  - Root cause.
  - PIR (Post-Incident Review) status.

  All default to `""`, so a missing DB value becomes an empty string rather than `null`.
- **L398** `template_id` is the DB id of the saved row.


#### Lines 401–410

````python
401  class JobAcceptedResponse(BaseModel):
402      """Response returned immediately after an agent job is queued."""
403
404      job_id: str = Field(..., description="Unique ID used to poll the job")
405      operation: JobOperation
406      status: Literal["queued"] = "queued"
407      status_url: str = Field(..., description="Relative URL for retrieving status and results")
408      submitted_at: datetime
409
410
````

- **L401–402** `JobAcceptedResponse` is returned **immediately** (HTTP 202 Accepted) when a long-running agent job is queued. The LLM work happens afterwards in the background.
- **L404** `job_id` is a UUID that the caller uses to poll.
- **L405** `operation: JobOperation` is one of the six names from L9.
- **L406** `status: Literal["queued"] = "queued"` is a field whose only allowed value is `"queued"`, and it defaults to that. At acceptance time a job is always queued.
- **L407** `status_url` is a relative URL such as `/api/jobs/<id>`. `job_queue.submit_job` builds it with an f-string.
- **L408** `submitted_at: datetime` is the UTC timestamp of submission.


#### Lines 411–423

````python
411  class JobStatusResponse(BaseModel):
412      """Current state and eventual result of an asynchronous agent job."""
413
414      job_id: str
415      operation: JobOperation
416      status: Literal["queued", "running", "completed", "failed"]
417      submitted_at: datetime
418      started_at: Optional[datetime] = None
419      completed_at: Optional[datetime] = None
420      result: Optional[Any] = Field(None, description="Agent result when status is completed")
421      error: Optional[str] = Field(None, description="Client-safe error when status is failed")
422
423
````

- **L411–412** `JobStatusResponse` is what `GET /api/jobs/{job_id}` returns. `job_queue.get_job_status` builds it with `JobStatusResponse(**snapshot)` from the in-memory job dict.
- **L414–415** These hold the id and the operation type.
- **L416** `status` is the job's lifecycle: `queued`, then `running`, then `completed` or `failed`.
- **L417–419** These are timestamps. `started_at` and `completed_at` stay `None` until those events happen.
- **L420** `result: Optional[Any]` holds the job's output once it has completed. Its shape depends on the operation: an `AssessmentResponse`, `DraftResponse` or batch response, already converted to a plain dict by `common.serialize_result`. `Any` is used because one model has to hold all of these.
- **L421** `error` is a **client-safe** message on failure. `common.safe_batch_error` hides internal exception details, which stay in the server logs.


#### Lines 424–437

````python
424  class HealthResponse(BaseModel):
425      """Health check response."""
426
427      status: str = Field(..., description="healthy or unhealthy")
428      database: str = Field(..., description="Database connection status")
429      agents: list[str] = Field(..., description="List of initialized agents")
430
431
432  class ErrorResponse(BaseModel):
433      """Error response."""
434
435      detail: str = Field(..., description="Error message")
436
437
````

- **L424–429** `HealthResponse` is used by `/health` in `routers/operations.py`:
  - `status` is "healthy" or "unhealthy".
  - `database` is "connected" or "disconnected".
  - `agents` is the list of registered agent names.

  L429 uses the built-in generic `list[str]` (Python 3.9+), while the rest of the file uses `typing.List`. The two are equivalent. It is just inconsistent style.
- **L432–435** `ErrorResponse` has a single `detail` string. That matches the body FastAPI produces for an `HTTPException` (`{"detail": "..."}`). Routers reference it in `responses={404: {"model": ErrorResponse}}` so that error shapes are documented in `/docs`.


#### Lines 438–452

````python
438  # ===========================================================================
439  # Operator UI: intake, review/feedback, and admin prompt editing
440  # ===========================================================================
441
442  # Reviewer roles map 1:1 to the template_stage they own. Admin is handled
443  # separately (prompt editing) and is not a review stage.
444  ROLE_TO_STAGE: Dict[str, str] = {
445      "MIMT": "MIMT Draft",
446      "1LOD": "1LOD R&C",
447      "2LOD": "2LOD R&C",
448      "Reg_Relations": "Reg Relations",
449  }
450
451  ReviewRole = Literal["MIMT", "1LOD", "2LOD", "Reg_Relations"]
452
````

- **L438–440** This comment banner starts the second half of the file: schemas for the **operator UI**, the web front-end used by the MIMT, 1LOD, 2LOD and Reg Relations people, plus admin.
- **L442–443** These comments explain that each reviewer role "owns" exactly one template stage, and that Admin is not a review role.
- **L444–449** `ROLE_TO_STAGE` is a plain dict constant (not a model) that maps role to stage:
  - `MIMT` to `"MIMT Draft"`.
  - `1LOD` to `"1LOD R&C"`.
  - `2LOD` to `"2LOD R&C"`.
  - `Reg_Relations` to `"Reg Relations"`.

  `review_service` uses it to decide which stage a reviewer's saved edits go to and to list roles (`/api/review/roles`). The annotation `Dict[str, str]` is just a hint here; Pydantic is not involved.
- **L451** `ReviewRole` is a `Literal` of the four role keys. It is used to validate `SaveTemplateRequest.role` (L562).


#### Lines 453–461

````python
453  # Content fields a reviewer/MIMT can edit on a notification template.
454  TEMPLATE_CONTENT_FIELDS = [
455      "title", "current_status", "date_of_occurrence", "type", "legal_entity",
456      "priority_rating", "description", "impact", "management_actions",
457      "root_cause", "pir", "incident_executive_lead_name",
458      "incident_executive_lead_contact",
459  ]
460
461
````

- **L453–459** `TEMPLATE_CONTENT_FIELDS` is the list of template columns a human reviewer can edit. It holds the same notification fields as `DraftResponse` plus the incident executive lead's name and contact. `review_service` loops over this list both to build editor payloads (`_to_draft_out`) and to decide which fields to copy when saving (`save_template`). That way, adding a field here automatically flows through both.
- **L460–461** These lines were not visible in the screenshots. Since L459 closes the list and a class starts at L462, they were almost certainly the two blank lines PEP 8 puts before a top-level class (inferred).


#### Lines 462–476

````python
462  class IncidentUpsertRequest(BaseModel):
463      """MIMT intake form - create or update an incident row.
464
465      Only incident_number is required; any other provided field is written.
466      """
467
468      model_config = ConfigDict(json_schema_extra={"example": {
469          "incident_number": "INC9000001",
470          "priority": "2 - High",
471          "legal_entity": "City National Bank (CNB)",
472          "incident_title": "Online banking unavailable",
473          "incident_description": "Customers could not log in...",
474          "location": "United States",
475      }})
476
````

- **L462** `IncidentUpsertRequest` is the body of `POST /api/incidents`, the **MIMT intake form**. "Upsert" means create the incident if it is new, or update it if it exists.
- **L463–466** The docstring: only `incident_number` is required, and any other field that is provided gets written.
- **L468–475** The example payload shows a realistic incident: priority "2 - High", legal entity City National Bank (CNB), a title, a description and a location. (The double `{{ ... }}` is just a dict literal inside the `ConfigDict(...)` call.)


#### Lines 477–502

````python
477      incident_number: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
478      priority: Optional[str] = None
479      legal_entity: Optional[str] = None
480      notification_time: Optional[str] = None
481      notification_from: Optional[str] = None
482      incident_title: Optional[str] = None
483      location: Optional[str] = None
484      impacted_applications: Optional[str] = None
485      incident_description: Optional[str] = None
486      business_impact: Optional[str] = None
487      user_impact: Optional[str] = None
488      client_impact: Optional[str] = None
489      additional_details: Optional[str] = None
490      technology_impact_start_datetime: Optional[str] = None
491      business_impact_start_datetime: Optional[str] = None
492      incident_manager: Optional[str] = None
493      incident_type: Optional[str] = None
494      change_in_priority_and_details: Optional[str] = None
495      # MIMT additional detail fields
496      management_actions: Optional[str] = None
497      root_cause: Optional[str] = None
498      pir: Optional[str] = None
499      incident_executive_lead_name: Optional[str] = None
500      incident_executive_lead_contact: Optional[str] = None
501
502
````

- **L477** `incident_number` is the only required field, with the usual length and regex validation.
- **L478–494** These are the **core incident attributes** MIMT fills in. Every one is `Optional[str] = None`:
  - Priority.
  - Legal entity.
  - When and who notified.
  - Title.
  - Location.
  - Impacted applications.
  - Description.
  - Business, user and client impact.
  - Extra details.
  - The technology and business impact start times. These are kept as strings, not `datetime`; the DB layer handles conversion.
  - Incident manager.
  - Type.
  - Priority-change notes.

  These columns are what the Assessment Agent later reads to decide reportability.
- **L495–500** The comment marks additional MIMT detail fields: management actions, root cause, PIR, and the executive lead's name and contact. These later feed into the notification template.
- Because the router calls `request.model_dump(exclude_none=True)`, fields left as `None` are **not** sent to the DB. That is what makes this a partial update. A side effect is that the form cannot clear a column back to NULL.


#### Lines 503–520

````python
503  class IncidentSummary(BaseModel):
504      """Lightweight incident row for list views."""
505
506      incident_number: str
507      incident_title: Optional[str] = None
508      priority: Optional[str] = None
509      legal_entity: Optional[str] = None
510      current_status: Optional[str] = None
511      mimt_input_completed: Optional[bool] = None
512
513
514  class RoleInfo(BaseModel):
515      """A reviewer role and the template stage it owns."""
516
517      role: str
518      stage: str
519
520
````

- **L503–511** `IncidentSummary` is a lightweight row for the incident list (`GET /api/incidents`). It has only number, title, priority, legal entity, current workflow status, and `mimt_input_completed`. That last one is the flag that kicks off the automated workflow; see `intake_service.mark_mimt_complete`. Used as a `response_model`, it also *filters*: any other keys in the returned dicts are dropped.
- **L514–518** `RoleInfo` is one `{role, stage}` pair, as returned by `/api/review/roles`.


#### Lines 521–545

````python
521  class TemplateDraftOut(BaseModel):
522      """A draft served to the editor for a given incident + role/stage."""
523
524      incident_number: str
525      role: str
526      template_stage: str
527      source: Literal["human_edit", "agent_draft", "empty"] = Field(
528          ..., description="Where this draft content came from"
529      )
530      template_id: str = ""
531      title: str = ""
532      current_status: str = ""
533      date_of_occurrence: str = ""
534      type: str = ""
535      legal_entity: str = ""
536      priority_rating: str = ""
537      description: str = ""
538      impact: str = ""
539      management_actions: str = ""
540      root_cause: str = ""
541      pir: str = ""
542      incident_executive_lead_name: str = ""
543      incident_executive_lead_contact: str = ""
544
545
````

- **L521–522** `TemplateDraftOut` is the draft sent to the reviewer's **editor** for a given incident and role. `review_service._to_draft_out` builds it.
- **L524–526** These hold the incident, the reviewer role, and the stage that role's saves will be written to.
- **L527–529** `source` tells the UI where the content came from:
  - `"human_edit"`: this role's own saved edit, or the latest human edit.
  - `"agent_draft"`: the Drafting Agent's row.
  - `"empty"`: nothing has been drafted yet, so it is pre-filled from the incident record.
- **L530** `template_id` is `""` if no row exists yet.
- **L531–543** These are all editable template fields, defaulting to `""`. `review_service` passes them through `to_text`, which turns `None` into `""` and dates into ISO strings.


#### Lines 546–557

````python
546  class TemplateVersionSummary(BaseModel):
547      """One historical version of a template at a stage."""
548
549      template_id: str
550      template_stage: str
551      drafted_by: Optional[str] = None
552      prompt_version: Optional[str] = None
553      created_at: Optional[datetime] = None
554      updated_at: Optional[datetime] = None
555      title: str = ""
556
557
````

- **L546–547** `TemplateVersionSummary` is one entry in a stage's edit history, used by `/api/review/template/versions`.
- **L549–550** These are the row id and stage.
- **L551** `drafted_by` is for example `"1LOD (human)"`. `review_service._drafted_by` adds the `(human)` suffix.
- **L552** `prompt_version` is `None` for human rows. That is how human rows are told apart from agent rows throughout the code.
- **L553–554** These are the created and updated timestamps.
- **L555** `title` gives a quick label for the version list.


#### Lines 558–577

````python
558  class SaveTemplateRequest(BaseModel):
559      """Save a reviewer's edited draft as a new versioned human row."""
560
561      incident_number: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
562      role: ReviewRole
563      title: Optional[str] = None
564      current_status: Optional[str] = None
565      date_of_occurrence: Optional[str] = None
566      type: Optional[str] = None
567      legal_entity: Optional[str] = None
568      priority_rating: Optional[str] = None
569      description: Optional[str] = None
570      impact: Optional[str] = None
571      management_actions: Optional[str] = None
572      root_cause: Optional[str] = None
573      pir: Optional[str] = None
574      incident_executive_lead_name: Optional[str] = None
575      incident_executive_lead_contact: Optional[str] = None
576
577
````

- **L558–559** `SaveTemplateRequest` is the body of `POST /api/review/template`. It saves a reviewer's edits. Each save inserts a **new** row, so history is kept.
- **L561** `incident_number` is validated as usual.
- **L562** `role: ReviewRole` must be one of MIMT, 1LOD, 2LOD or Reg_Relations. Anything else is rejected with a 422 before the service runs.
- **L563–575** All editable template fields are optional. `review_service.save_template` only copies fields that are present and not `None` into the new row.


#### Lines 578–583

````python
578  class FeedbackRequest(BaseModel):
579      """Reviewer feedback that re-invokes the Drafting Agent at the next stage."""
580
581      feedback: str = Field(..., min_length=1, max_length=10000)
582
583
````

- **L578–579** `FeedbackRequest` is the body for the 1LOD and 2LOD "return feedback" endpoints. The feedback text ends up in the incident's `lod1_feedback` or `lod2_feedback` column, and the workflow engine re-runs the Drafting Agent with it.
- **L581** `feedback: str` is required (`...`). It must be 1 to 10,000 characters, so empty feedback is rejected.


#### Lines 584–592

````python
584  class CriterionComparison(BaseModel):
585      """Agent vs. human-ground-truth comparison for one FRB criterion."""
586
587      criterion: str
588      agent: Optional[str] = None
589      human: Optional[str] = None
590      match: Optional[bool] = None
591
592
````

- **L584–585** `CriterionComparison` is a single-criterion comparison for the UI's "latest assessment" panel. It is simpler than `AssessmentCriterionDetail`.
- **L587–590** These hold the criterion name, the agent's text, the human's text, and whether they match. All but `criterion` are `Optional`, so they can be `None` when there is no human ground truth.


#### Lines 593–603

````python
593  class AssessmentEvaluationSummary(BaseModel):
594      """Evaluation of the latest agent assessment against 2LOD-approved human
595      ground truth for the same incident, if one exists (same logic as
596      backend/evaluation/evaluate_assessment_prompts.py's compare_assessments)."""
597
598      has_human_ground_truth: bool
599      flag_match: Optional[bool] = None
600      human_reportability_flag: Optional[str] = None
601      criteria: List[CriterionComparison] = Field(default_factory=list)
602
603
````

- **L593–596** `AssessmentEvaluationSummary` is an on-the-fly evaluation of the latest agent assessment for **one** incident against that incident's 2LOD-approved human assessment. The docstring says it reuses `compare_assessments` from `backend/evaluation/evaluate_assessment_prompts.py`, so the UI and the offline evaluation use the same matching logic.
- **L598** `has_human_ground_truth` says whether there is anything to compare against.
- **L599–600** These give whether the overall flags match, and the human's flag. Both are `None` without ground truth.
- **L601** `criteria` holds per-criterion comparisons, defaulting to an empty list.


#### Lines 604–622

````python
604  class LatestAssessmentOut(BaseModel):
605      """Most recent AGENT-generated reportability_assessments row for an
606      incident (provided_by == 'Assessment Agent'), for display in the UI -
607      persists across navigation, unlike job-polling state. Includes an
608      evaluation against human ground truth when one is available."""
609
610      incident_number: str
611      reportability_flag: Optional[str] = None
612      data_system_impact: Optional[str] = None
613      operational_impact: Optional[str] = None
614      financial_reputational_impact: Optional[str] = None
615      systemic_risk: Optional[str] = None
616      provided_by: Optional[str] = None
617      prompt_version: Optional[str] = None
618      revision: Optional[int] = None
619      updated_at: Optional[datetime] = None
620      evaluation: Optional[AssessmentEvaluationSummary] = None
621
622
````

- **L604–608** `LatestAssessmentOut` is the most recent **agent-generated** assessment row for an incident (`provided_by == 'Assessment Agent'`), used by `GET /api/incidents/{n}/assessment`. The docstring explains why it exists: job-polling state lives only in memory and disappears when you navigate away, but this reads what is actually persisted in Postgres.
- **L610–615** These are the incident, the flag, and the four FRB criterion texts. All are optional, because DB columns may be NULL.
- **L616–618** `provided_by` (who produced it), `prompt_version` (which assessment prompt version) and `revision` (a revision counter on the row; the tables are revisioned).
- **L619** `updated_at` is when the row was last written.
- **L620** `evaluation` holds the nested comparison from L593, or `None`. `intake_service.get_latest_assessment` attaches it under the `"evaluation"` key.


#### Lines 623–648

````python
623  class LatestDraftOut(BaseModel):
624      """Most recent AGENT-authored (drafted_by == 'Drafting Agent')
625      template_details row for an incident, for display in the UI - includes
626      every notification template field so the UI can render it in the same
627      layout as the regulatory notification template (Draft Template.msg)."""
628
629      incident_number: str
630      template_stage: Optional[str] = None
631      drafted_by: Optional[str] = None
632      title: Optional[str] = None
633      current_status: Optional[str] = None
634      date_of_occurrence: Optional[str] = None
635      type: Optional[str] = None
636      legal_entity: Optional[str] = None
637      priority_rating: Optional[str] = None
638      description: Optional[str] = None
639      impact: Optional[str] = None
640      management_actions: Optional[str] = None
641      root_cause: Optional[str] = None
642      pir: Optional[str] = None
643      incident_executive_lead_name: Optional[str] = None
644      incident_executive_lead_contact: Optional[str] = None
645      prompt_version: Optional[str] = None
646      assessment_prompt_version: Optional[str] = None
647      updated_at: Optional[datetime] = None
648
````

- **L623–627** `LatestDraftOut` is the most recent **agent-authored** template row (`drafted_by == 'Drafting Agent'`), used by `GET /api/incidents/{n}/draft`. It carries every template field so the UI can render it laid out like the real regulator notification template (the "Draft Template.msg" Outlook file mentioned in the docstring).
- **L629–630** These give which stage the draft is for and who drafted it.
- **L631–643** These are all notification content fields, `Optional[str]`. `date_of_occurrence` is a date column in the DB. It arrives as an ISO string because `intake_service.get_latest_draft` runs each value through `serialize_db_value`.
- **L644–645** These record the drafting prompt version and the assessment prompt version used as context. Together they say exactly which prompt combination produced this draft.
- **L646** `updated_at` is when the row was last written.


---

## 5. `backend/agents/__init__.py`

*27 lines · complete*

The package initialiser for `backend/agents/`. A file named `__init__.py` runs when the package is first imported. This one imports the two agent modules and exposes `get_agent_registry()`, which returns a small dictionary describing each agent (display name, description, tool names). The registry is pure **metadata**. `routers/operations.py` uses it to answer `GET /agents` and to list agent names in `GET /health`. The actual assessment and drafting work is orchestrated in `backend/services/*`.


#### Lines 1–7

````python
1  """Metadata registry for Anchor AI workflows."""
2
3  from backend.agents import assessment_agent, drafting_agent
4
5  # All agent modules - add new agents here
6  _AGENT_MODULES = [assessment_agent, drafting_agent]
7
````

- **L1** Docstring: this package is a "metadata registry".
- **L3** Imports the two agent modules. Importing them runs their top-level code, which loads the active prompt version and prints a confirmation line (see `assessment_agent.py` L21–28). This is also why starting the server triggers prompt loading: `operations.py` imports this package.
- **L5–6** `_AGENT_MODULES` is the list of modules to include. The leading underscore is a Python convention for "private to this module". To add a third agent, you would write a module with the same attributes and append it here, as the comment says.


#### Lines 8–22

````python
 8  def get_agent_registry() -> dict[str, dict]:
 9      """Get registry of all available agents.
10
11      Returns:
12          Dict mapping agent names to their metadata
13      """
14      return {
15          mod.AGENT_NAME: {
16              "display_name": mod.DISPLAY_NAME,
17              "description": mod.DESCRIPTION,
18              "tools": [t.name for t in mod.TOOLS],
19          }
20          for mod in _AGENT_MODULES
21      }
22
````

- **L8** `get_agent_registry() -> dict[str, dict]`. The hint means "a dict whose keys are strings and whose values are dicts". The lowercase built-in generic `dict[...]` needs Python 3.9+.
- **L9–13** Docstring.
- **L14–21** A **dict comprehension**: `{key: value for mod in _AGENT_MODULES}` builds a dict in one expression by looping over the modules.
  - **L15** The key is each module's `AGENT_NAME` (e.g. `"assessment_agent"`).
  - **L16–17** The values come from the module's `DISPLAY_NAME` and `DESCRIPTION`, which are themselves loaded from the active prompt file.
  - **L18** `[t.name for t in mod.TOOLS]` is a list comprehension giving each tool's name. The tools are LangChain `@tool` objects (see `backend/tools/*`), and the decorator gives each one a `.name` equal to its function name, e.g. `"db_query"`.
  - **L20** The loop clause.
- The result looks like `{"assessment_agent": {"display_name": "Assessment Agent", "description": "...", "tools": ["db_query", ...]}, "drafting_agent": {...}}`.


#### Lines 23–27

````python
23
24  __all__ = [
25      "get_agent_registry",
26  ]
27
````

- **L24–26** `__all__` declares `get_agent_registry` as the package's public API. The agent modules themselves are still reachable as `backend.agents.assessment_agent`; for example, `evaluation/compare_agent_vs_human.py` imports `AGENT_NAME` from there.


---

## 6. `backend/agents/assessment_agent.py`

*29 lines · complete*

Defines the **Assessment Agent's identity**: its name, display name, description, system prompt and the list of tools it may use. It has no logic of its own. All prompt text and naming come from whichever versioned prompt file is active in `prompt_config.py` (currently `assessment_agent_prompt_v10`), loaded through `prompt_iterations/loader.py`. In the current code the assessment itself is run by `services/assessment_service.run_single_assessment`, a fixed, deterministic sequence that calls these tools directly and makes one LLM call inside `evaluate_reportability`. This module mostly supplies metadata for `/agents` and `/health`, and the agent name used by the evaluation scripts.


#### Lines 1–6

````python
1  """Assessment Agent — Evaluates regulatory reportability of incidents.
2
3  Prompt is loaded dynamically from the versioned prompt module specified in
4  backend/evaluation/prompt_iterations/prompt_config.py (ASSESSMENT_AGENT_PROMPT_VERSION).
5  """
6
````

- **L1–5** Docstring. The agent decides whether an incident must be reported to a regulator. Its prompt is not hard-coded here; it is loaded from the versioned module named by `ASSESSMENT_AGENT_PROMPT_VERSION` in `prompt_config.py`. This lets the team iterate on prompts (v0, v1 ... v10) and evaluate each version against human ground truth.


#### Lines 7–13

````python
 7  from backend.tools.data_tools import db_query, db_write
 8  from backend.tools.assessment_tools import evaluate_reportability
 9  from backend.tools.audit_tools import audit_log
10  from backend.tools.reference_data_tools import lookup_cuso_applications, get_regulatory_requirements
11  from backend.evaluation.prompt_iterations.loader import load_assessment_prompt_module
12  from backend.evaluation.prompt_iterations.prompt_config import ASSESSMENT_AGENT_PROMPT_VERSION
13
````

- **L7** `db_query` and `db_write` are LangChain tools (from `tools/data_tools.py`) that read rows from and write rows to Postgres tables.
- **L8** `evaluate_reportability` (from `tools/assessment_tools.py`) is the tool that actually calls the LLM, using the active SYSTEM_PROMPT, to judge the FRB criteria.
- **L9** `audit_log` records an audit-trail entry (who or what did what to which incident). This matters for compliance.
- **L10** Reference-data tools that read the Excel workbook set by `REFERENCE_DATA_FILE_PATH`. `lookup_cuso_applications` fuzzy-matches a text query (e.g. an incident's impacted applications) against the ~2,600-row "CUSO Applications" sheet and returns only the few best matches, so the prompt doesn't overflow. `get_regulatory_requirements` returns the small (~15-row) "Regulatory Requirements" sheet in full.
- **L11** `load_assessment_prompt_module()` imports and returns the active prompt module.
- **L12** The version string itself, imported only for the confirmation message on L28.


#### Lines 14–16

````python
14  # Tools available to this agent
15  TOOLS = [db_query, db_write, evaluate_reportability, audit_log, lookup_cuso_applications, get_regulatory_requirements]
16
````

- **L15** `TOOLS` lists the six tool objects this agent is allowed to use. `agents/__init__.py` turns them into tool names for the registry. In a classic LangChain agent this list would be handed to the LLM so it could choose tools; here it mainly documents the agent's capabilities.


#### Lines 17–22

````python
17  # ── Dynamic Prompt Loading ────────────────────────────────────────────────────
18  # Single source of truth: backend/evaluation/prompt_iterations/prompts/<version>.py
19  # No inline fallback — if the versioned prompt can't be loaded, fail loud.
20
21  prompt_module = load_assessment_prompt_module()
22
````

- **L17–19** A comment banner. The prompt file is the "single source of truth", and there is deliberately **no inline fallback** prompt. If the configured version can't be imported, the app should crash ("fail loud") rather than silently run with an outdated default prompt, which in a regulatory setting could produce wrong decisions without anyone noticing.
- **L21** Loads the module. The loader calls `importlib.import_module("backend.evaluation.prompt_iterations.prompts.<version>")`, which imports a module from a name held in a string at runtime. This runs at **import time**, so a typo in the version name breaks server start-up immediately with `ModuleNotFoundError`.


#### Lines 23–29

````python
23  AGENT_NAME = prompt_module.AGENT_NAME
24  DISPLAY_NAME = prompt_module.DISPLAY_NAME
25  DESCRIPTION = prompt_module.DESCRIPTION
26  SYSTEM_PROMPT = prompt_module.SYSTEM_PROMPT
27
28  print(f"✓ Loaded prompt version: {ASSESSMENT_AGENT_PROMPT_VERSION}")
29
````

- **L23–26** Copies four attributes off the prompt module into this module's namespace, so other code can `from backend.agents.assessment_agent import AGENT_NAME`. `AGENT_NAME` is the machine name (`"assessment_agent"`), `DISPLAY_NAME` the human label (`"Assessment Agent"`), and `DESCRIPTION` a one-line summary. `SYSTEM_PROMPT` is the long instruction text for the LLM. No file available here imports `SYSTEM_PROMPT` from this module; `assessment_tools.py` loads it from the prompt module directly.
- **L28** Prints e.g. `✓ Loaded prompt version: assessment_agent_prompt_v10` at import, as a quick visual check of which prompt is live.
- Note: this uses `print`, not the project logger, so the line appears on the console but **not** in the per-run log file in `logs/`. A `logger.info(...)` would record it alongside everything else, which is useful when checking later which prompt version produced a result.


---

## 7. `backend/agents/drafting_agent.py`

*28 lines · complete*

The Drafting Agent's counterpart to `assessment_agent.py`. It declares the drafting agent's tools and loads its name, description and system prompt from the active drafting prompt version (`DRAFTING_AGENT_PROMPT_VERSION` in `prompt_config.py`, currently `drafting_agent_prompt_v4`). The drafting agent fills in the regulatory notification template (title, description, impact, root cause, management actions, ...) for incidents assessed as reportable. As with the assessment agent, the real work is orchestrated in `services/drafting_service.py`; this module provides identity and metadata.


#### Lines 1–6

````python
1  """Drafting Agent — Generates regulatory notification templates.
2
3  Prompt is loaded dynamically from the versioned prompt module specified in
4  backend/evaluation/prompt_iterations/prompt_config.py (DRAFTING_AGENT_PROMPT_VERSION).
5  """
6
````

- **L1–5** Docstring: the agent "generates regulatory notification templates", and its prompt comes from the versioned prompt module.


#### Lines 7–12

````python
 7  from backend.tools.data_tools import db_query, db_write
 8  from backend.tools.drafting_tools import generate_template_content, get_template_schema
 9  from backend.tools.audit_tools import audit_log
10  from backend.evaluation.prompt_iterations.loader import load_drafting_prompt_module
11  from backend.evaluation.prompt_iterations.prompt_config import DRAFTING_AGENT_PROMPT_VERSION
12
````

- **L7** The same `db_query`/`db_write` database tools as the assessment agent.
- **L8** `generate_template_content` (in `tools/drafting_tools.py`) makes the LLM call that writes the notification fields. `get_template_schema` returns the template's field definitions, which come from `tools/template_schema.json`.
- **L9** `audit_log` provides audit-trail entries.
- **L10–11** The drafting-specific prompt loader and the active version string. Unlike the assessment agent, this agent has no reference-data tools; the drafting context comes from the incident and its assessment.


#### Lines 13–15

````python
13  # Tools available to this agent
14  TOOLS = [db_query, db_write, generate_template_content, get_template_schema, audit_log]
15
````

- **L14** `TOOLS` is the drafting agent's five tools, reported by `get_agent_registry()` / `GET /agents`.


#### Lines 16–21

````python
16  # ── Dynamic Prompt Loading ────────────────────────────────────────────────────
17  # Single source of truth: backend/evaluation/prompt_iterations/prompts/<version>.py
18  # No inline fallback — if the versioned prompt can't be loaded, fail loud.
19
20  prompt_module = load_drafting_prompt_module()
21
````

- **L16–18** The same "single source of truth, no inline fallback, fail loud" policy as `assessment_agent.py`.
- **L20** Imports the active drafting prompt module at import time. A wrong version name stops the app from starting.


#### Lines 22–28

````python
22  AGENT_NAME = prompt_module.AGENT_NAME
23  DISPLAY_NAME = prompt_module.DISPLAY_NAME
24  DESCRIPTION = prompt_module.DESCRIPTION
25  SYSTEM_PROMPT = prompt_module.SYSTEM_PROMPT
26
27  print(f"✓ Loaded prompt version: {DRAFTING_AGENT_PROMPT_VERSION}")
28
````

- **L22–25** Re-exports `AGENT_NAME` (`"drafting_agent"`), `DISPLAY_NAME`, `DESCRIPTION` and `SYSTEM_PROMPT` from the prompt module. `drafting_tools.py` loads `SYSTEM_PROMPT` itself via the loader rather than from here.
- **L27** Prints the loaded drafting prompt version. The same `print`-vs-logger caveat applies as in `assessment_agent.py`.


---

## 8. `backend/routers/__init__.py`

*9 lines · complete*

The package initialiser for `backend/routers/`, the HTTP layer. Its docstring states the rule that routers only translate HTTP to and from Python and delegate everything else to `backend/services`. It also holds one shared helper, `parse_csv_list`, which the assessment and drafting routers use to turn a query string like `INC1,INC2` into a list.


#### Lines 1–5

````python
1  """FastAPI routers for Anchor AI — HTTP layer only, delegating to backend/services."""
2
3  from typing import List, Optional
4
5
````

- **L1** Docstring: routers are the "HTTP layer only".
- **L3** `List` and `Optional` from `typing` are used in type hints. `Optional[str]` means "a `str` or `None`".


#### Lines 6–9

````python
6  def parse_csv_list(value: Optional[str]) -> Optional[List[str]]:
7      """Parse an optional comma-separated query parameter into non-empty values."""
8      return [item.strip() for item in value.split(",") if item.strip()] if value else None
9
````

- **L6** `parse_csv_list(value: Optional[str]) -> Optional[List[str]]` takes a possibly-missing string and returns a possibly-missing list of strings.
- **L7** Docstring.
- **L8** One line combining a **conditional expression** (`X if condition else Y`) with a **list comprehension**:
  - `if value else None`: if the parameter was omitted (`None`) or empty (`""`), return `None`, which the services treat as "all incidents".
  - Otherwise, `value.split(",")` splits on commas, `item.strip()` trims spaces (so `"INC1, INC2"` works), and `if item.strip()` drops empty pieces from inputs like `"INC1,,INC2,"`.
  - Example: `" INC0575428 , INC0600745 ,"` becomes `["INC0575428", "INC0600745"]`.
- A string made only of commas (e.g. `","`) returns `[]`. `services/common.load_incidents` checks `if not incident_numbers`, which treats `[]` like `None`, so that input also means "all incidents".


---

## 9. `backend/routers/intake.py`

*90 lines · complete*

This is the **HTTP layer for incident intake and read-only incident views** in the operator UI. The MIMT team (the major-incident management team that first records an incident) uses `POST /api/incidents` to create or update an incident. They then call `POST /api/incidents/{n}/mimt-complete` to say "our input is done". That flips a DB column, and a **Postgres trigger** emits an event that `workflow_listener` picks up, so the workflow engine runs the Assessment Agent (and drafting, if the incident is reportable) **asynchronously**. No agent is called from this file. The other endpoints read incidents and the latest persisted agent assessment and draft for the UI. All logic lives in `backend/services/intake_service.py`. `main.py` mounts this router.


#### Lines 1–7

````python
1  """Incident intake + read endpoints for the operator UI.
2
3  Thin HTTP layer over backend.services.intake_service. Marking MIMT input
4  complete fires the DB trigger that the workflow listener reacts to, so no
5  agent is invoked synchronously here.
6  """
7
````

- **L1–6** The module docstring. It says this is a thin layer over `intake_service`. It also explains the key design point: marking MIMT input complete fires a DB trigger that the workflow listener reacts to, so this endpoint returns quickly and the agents run elsewhere.


#### Lines 8–20

````python
 8  from typing import Any, Dict, List, Optional
 9
10  from fastapi import APIRouter, status
11
12  from backend.schemas import (
13      ErrorResponse,
14      IncidentSummary,
15      IncidentUpsertRequest,
16      LatestAssessmentOut,
17      LatestDraftOut,
18  )
19  from backend.services import intake_service
20  from backend.logger import get_logger
````

- **L8** These are type hints for return values:
  - `Dict[str, Any]` is a JSON-like dict.
  - `List[...]` is a list.
  - `Optional[...]` may be `None`.
- **L10** `APIRouter` groups the routes, and `status` provides HTTP code constants.
- **L12–18** These are the schemas used:
  - `ErrorResponse` documents the 400 and 404 error bodies.
  - `IncidentSummary` is the list-view shape.
  - `IncidentUpsertRequest` is the intake form body.
  - `LatestAssessmentOut` and `LatestDraftOut` are the "latest persisted result" shapes.
- **L19** `intake_service` is the module that actually talks to Postgres.
- **L20** The project's logger factory.


#### Lines 21–26

````python
21
22  logger = get_logger(__name__)
23
24  router = APIRouter(tags=["Intake"])
25
26
````

- **L22** This creates a module logger. Note that it isn't used anywhere in this file; logging happens in the service instead.
- **L24** `router = APIRouter(tags=["Intake"])` groups these endpoints under "Intake" in `/docs`.


#### Lines 27–36

````python
27  @router.post(
28      "/api/incidents",
29      status_code=status.HTTP_200_OK,
30      responses={400: {"model": ErrorResponse}},
31      summary="Create or update an incident (MIMT intake form)",
32  )
33  async def upsert_incident(request: IncidentUpsertRequest) -> Dict[str, Any]:
34      """Create a new incident or update an existing one from the MIMT form."""
35      return intake_service.upsert_incident(request.model_dump(exclude_none=True))
36
````

- **L27–32** This registers `POST /api/incidents`. It returns 200 on success, and a 400 is documented. The service raises 400 if `incident_number` is missing, though the schema already makes that field required, so in practice Pydantic would reject that case first with a 422.
- **L33** `request: IncidentUpsertRequest` is a parameter typed as a Pydantic model. FastAPI therefore reads it from the **JSON request body**, validates it, and passes in a model instance. Compare this with the `Query(...)` parameters in `drafting.py`, which come from the URL. `-> Dict[str, Any]` is the return type hint. There is no `response_model`, so FastAPI simply JSON-encodes whatever dict comes back.
- **L35** `request.model_dump(exclude_none=True)` converts the model back into a plain dict and **drops every field that is `None`**. As a result, only the fields the form actually filled in are written, which turns the write into a partial update rather than a wipe of the other columns. The service writes the row (`db_write` upserts on `incident_number`) and returns the full fresh row through `get_incident`.


#### Lines 37–47

````python
37
38  @router.get(
39      "/api/incidents",
40      response_model=List[IncidentSummary],
41      summary="List incidents",
42  )
43  async def list_incidents() -> List[Dict[str, Any]]:
44      """Return all incidents as lightweight summaries for selection."""
45      return intake_service.list_incidents()
46
47
````

- **L38–42** This registers `GET /api/incidents`. `response_model=List[IncidentSummary]` makes FastAPI validate each row against `IncidentSummary` and **strip any extra columns**, which keeps the list payload small.
- **L43–45** This calls `intake_service.list_incidents()`. That queries the `incidents` table for only the six summary columns (`_SUMMARY_COLUMNS`), so the UI can show a picker.
- **L46** This line was not visible in the screenshots. Since L45 is the function's `return` and L48 starts a new decorator, it was almost certainly a blank line (inferred). **L47** is blank.


#### Lines 48–56

````python
48  @router.get(
49      "/api/incidents/{incident_number}",
50      responses={404: {"model": ErrorResponse}},
51      summary="Get full incident detail",
52  )
53  async def get_incident(incident_number: str) -> Dict[str, Any]:
54      """Return the full incident record."""
55      return intake_service.get_incident(incident_number)
56
````

- **L48–52** This registers `GET /api/incidents/{incident_number}`. The `{incident_number}` in the path is a **path parameter**.
- **L53** `incident_number: str` matches the `{incident_number}` placeholder by name, so FastAPI fills it in from the URL path. Note: unlike the `Query` parameters elsewhere, there is no regex or length check here. Values are passed to the DB layer as SQL parameters, so this is not an injection risk. It is just looser validation.
- **L55** This returns the full incident row, every column. `intake_service.get_incident` raises `HTTPException(404)` if no row exists. FastAPI turns that into a 404 response with `{"detail": "..."}`, matching `ErrorResponse`.


#### Lines 57–69

````python
57
58  @router.get(
59      "/api/incidents/{incident_number}/assessment",
60      response_model=Optional[LatestAssessmentOut],
61      responses={404: {"model": ErrorResponse}},
62      summary="Get the latest persisted assessment for an incident, if any",
63  )
64  async def get_latest_assessment(incident_number: str) -> Optional[Dict[str, Any]]:
65      """Reflects whatever is actually persisted (manual trigger, the automatic
66      workflow engine, or a batch run) — unlike job-polling state, this survives
67      navigating away and back."""
68      return intake_service.get_latest_assessment(incident_number)
69
````

- **L58–63** This registers `GET /api/incidents/{incident_number}/assessment`. `response_model=Optional[LatestAssessmentOut]` means the body is either that shape or `null`, which is returned when the agent has never assessed this incident.
- **L64** The signature, with the path parameter and an `Optional` return.
- **L65–67** The docstring explains the purpose. Job-polling state (`/api/jobs/{id}`) is in memory and tied to one browser session. This endpoint reflects what is actually **persisted** in Postgres, however it got there: a manual trigger, the automatic workflow engine, or a batch run. It therefore survives page navigation.
- **L68** The service does several things:
  - It 404s if the incident does not exist.
  - It fetches all `reportability_assessments` rows and keeps only those with `provided_by == 'Assessment Agent'`.
  - It takes the newest one.
  - It attaches an `evaluation` block comparing it with the 2LOD-approved human assessment, using the same `compare_assessments` logic as the evaluation scripts.


#### Lines 70–79

````python
70
71  @router.get(
72      "/api/incidents/{incident_number}/draft",
73      response_model=Optional[LatestDraftOut],
74      responses={404: {"model": ErrorResponse}},
75      summary="Get the latest persisted draft for an incident, if any",
76  )
77  async def get_latest_draft(incident_number: str) -> Optional[Dict[str, Any]]:
78      return intake_service.get_latest_draft(incident_number)
79
````

- **L71–76** This registers `GET /api/incidents/{incident_number}/draft`, which returns a `LatestDraftOut` or `null`.
- **L77–78** This returns the newest `template_details` row written by the Drafting Agent (`drafted_by == 'Drafting Agent'`), across any stage. Date columns are converted to ISO strings. There is no docstring here, unlike the sibling endpoint.


#### Lines 80–90

````python
80
81  @router.post(
82      "/api/incidents/{incident_number}/mimt-complete",
83      responses={404: {"model": ErrorResponse}},
84      summary="Mark MIMT input complete (starts the workflow)",
85  )
86  async def mimt_complete(incident_number: str) -> Dict[str, Any]:
87      """Flag mimt_input_completed=TRUE; the workflow engine then runs assessment
88      (and drafting if reportable) asynchronously."""
89      return intake_service.mark_mimt_complete(incident_number)
90
````

- **L81–85** This registers `POST /api/incidents/{incident_number}/mimt-complete`. The summary says it explicitly: this is the action that **starts the workflow**.
- **L86** The path parameter. No body is needed.
- **L87–88** The docstring. It sets `mimt_input_completed=TRUE`, and the workflow engine then runs assessment, and drafting if the incident is reportable, in the background.
- **L89** `intake_service.mark_mimt_complete`:
  - It 404s if the incident is missing.
  - It writes `mimt_input_completed: True`.
  - It writes an audit-log entry (`"mimt_input_received"`).
  - It returns the updated row.

  The Postgres trigger only emits its event on the FALSE→TRUE transition, so calling this twice won't run the agents twice.
- Note (applies to every endpoint in this file): they are declared `async def` but call synchronous, blocking database code through `intake_service`. FastAPI runs `async def` endpoints directly on the event loop, so while one of these queries is running, the server cannot handle other requests. With plain `def`, FastAPI would run them in a thread pool instead. The queries are quick, so the impact is small, but it is worth knowing.


---

## 10. `backend/routers/assessment.py`

*168 lines · complete*

The **HTTP endpoints for the Assessment Agent and its evaluation**. There are four routes. `POST /api/assess` handles one incident. `POST /api/batch/assess` handles all or selected incidents. `POST /api/evaluate/assessment` compares agent answers with 2LOD-approved human answers. `GET /api/evaluate/assessment/latest` returns the last saved evaluation. The three POST routes follow one pattern: validate the query parameters, build a Pydantic request object, wrap the real work in a zero-argument `handler()` function, and pass it to `job_queue.submit_job`. That call immediately returns **202 Accepted** with a `job_id`, and the work runs in the background. The domain logic lives in `services/assessment_service.py`; this file only translates HTTP. Assessment jobs are wrapped in `operation_runtime.exclusive_operation("assessment")`, a process-wide lock, so only one assessment job runs at a time.


#### Lines 1–6

````python
1  """Assessment endpoints — thin HTTP layer over backend.services.assessment_service."""
2
3  from typing import Literal, Optional
4
5  from fastapi import APIRouter, BackgroundTasks, Query, status
6
````

- **L1** Docstring: a "thin HTTP layer" over the assessment service.
- **L3** From `typing`: `Literal["a", "b"]` restricts a value to exactly those strings, and `Optional[X]` means "X or None". FastAPI reads these hints to validate input and to document it in Swagger.
- **L5** FastAPI pieces:
  - `APIRouter` is a mini-app that groups endpoints; `main.py` mounts it.
  - `BackgroundTasks` is FastAPI's way to run a function *after* the response has been sent.
  - `Query` declares a query-string parameter with validation rules.
  - `status` holds named HTTP status codes such as `HTTP_202_ACCEPTED`.


#### Lines 7–16

````python
 7  from backend.schemas import (
 8      AssessmentEvaluationRequest,
 9      AssessmentEvaluationResponse,
10      AssessmentRequest,
11      BatchAssessmentRequest,
12      ErrorResponse,
13      JobAcceptedResponse,
14  )
15  from backend.routers import parse_csv_list
16  from backend.services import job_queue, operation_runtime
````

- **L7–14** Pydantic models from `backend/schemas.py`. A Pydantic model is a class whose typed fields are validated when an instance is created. FastAPI also uses these models to generate Swagger docs and to serialise responses.
  - `AssessmentRequest`, `BatchAssessmentRequest` and `AssessmentEvaluationRequest` describe the inputs.
  - `AssessmentEvaluationResponse` is the evaluation summary.
  - `ErrorResponse` is `{"detail": "..."}`.
  - `JobAcceptedResponse` is the 202 body: `job_id`, `operation`, `status`, `status_url`, `submitted_at`.
- **L15** `parse_csv_list` (from `routers/__init__.py`) turns `"INC1,INC2"` into a list.
- **L16** `job_queue` is the in-memory background job registry: `submit_job` and `get_job_status`. `operation_runtime` provides the per-operation locks.


#### Lines 17–28

````python
17  from backend.services.assessment_service import (
18      execute_assessment_evaluation,
19      execute_batch_assessment,
20      get_latest_assessment_evaluation,
21      run_single_assessment,
22  )
23  from backend.logger import get_logger
24
25  logger = get_logger(__name__)
26
27  router = APIRouter(tags=["Assessment"])
28
````

- **L17–22** The service functions that do the real work:
  - `run_single_assessment` assesses one incident: a location rule check, context gathering, one LLM judgment, a DB write and an audit log.
  - `execute_batch_assessment` runs that across many incidents with a thread pool.
  - `execute_assessment_evaluation` compares agent output with human ground truth.
  - `get_latest_assessment_evaluation` reads the cached last evaluation.
- **L23–25** Module logger.
- **L27** Creates the router. `tags=["Assessment"]` groups all of these endpoints under the "Assessment" heading in `/docs`, matching `openapi_tags` in `main.py`.


#### Lines 29–36

````python
29
30  @router.post(
31      "/api/assess",
32      response_model=JobAcceptedResponse,
33      status_code=status.HTTP_202_ACCEPTED,
34      responses={503: {"model": ErrorResponse}},
35      summary="Queue assessment for one incident",
36  )
````

- **L30** `@router.post(...)` is a decorator that registers the function below as the handler for `POST /api/assess` (L31).
- **L32** `response_model=JobAcceptedResponse`: FastAPI validates the returned object against this model and documents it.
- **L33** `status_code=202` (*Accepted*) means "request received, processing later". That matches this endpoint's behaviour, so it doesn't return 200 OK.
- **L34** `responses={503: {...}}` documents an additional possible error response in Swagger. It is documentation only: nothing in this router or `job_queue` raises 503. The queue raises **429** when it is full and **404** for unknown job IDs, so this entry looks like a leftover from an earlier design.
- **L35** `summary` is the one-line title shown in Swagger.


#### Lines 37–46

````python
37  async def assess_incident(
38      background_tasks: BackgroundTasks,
39      incident_number: str = Query(
40          ...,
41          description="Incident number, for example INC0575428",
42          min_length=1,
43          max_length=50,
44          pattern=r"^[A-Za-z0-9_-]+$",
45      ),
46  ):
````

- **L37** `async def` makes this a coroutine endpoint, run on FastAPI's event loop. That is fine here because the function does no blocking work; it only queues a job.
- **L38** `background_tasks: BackgroundTasks` is an example of FastAPI's *dependency injection by type*. Because of the type hint, FastAPI creates a `BackgroundTasks` object and passes it in automatically; the caller never sends it.
- **L39–45** `incident_number` is a required query parameter (`?incident_number=INC0575428`). In `Query(...)`, the first argument `...` (Python's `Ellipsis`) means "no default, required". `description` is shown in Swagger. `min_length`/`max_length` bound the size, and the regex `pattern` allows only letters, digits, `_` and `-`. Invalid input is rejected with **422 Unprocessable Entity** before the function body runs. That rejection is a small but real safety measure, since the value later ends up in DB queries and log lines.


#### Lines 47–57

````python
47      """Queue an incident reportability assessment and return immediately.
48
49      This endpoint invokes the Assessment Agent to evaluate whether an incident
50      meets FRB reportability criteria. The agent will:
51      1. Query the incident details from the database
52      2. Evaluate reportability using the 4 FRB questions
53      3. Write assessment results to the database
54      4. Log the action for audit compliance
55
56      Poll the returned status_url to retrieve the final AssessmentResponse.
57      """
````

- **L47–57** Docstring. FastAPI shows it as the endpoint's description in Swagger. It lists the agent's steps (load incident → evaluate the 4 FRB questions → write results → audit log) and tells the caller to poll `status_url` for the final `AssessmentResponse`.


#### Lines 58–66

````python
58      request = AssessmentRequest(incident_number=incident_number)
59      logger.info("Queueing assessment for incident: %s", incident_number)
60
61      def handler():
62          with operation_runtime.exclusive_operation("assessment"):
63              return run_single_assessment(request.incident_number)
64
65      return job_queue.submit_job(background_tasks, "assessment", handler)
66
````

- **L58** Wraps the input in the `AssessmentRequest` Pydantic model. This repeats the same validation rules (the schema has the same length and pattern constraints) and gives the handler a typed object.
- **L59** Logs the request. `"...%s", incident_number` uses logging's lazy formatting: the string is only built if the log level is enabled.
- **L61–63** A nested function, `handler`. It is a **closure**: it "captures" `request` from the enclosing scope, so it can take zero arguments, which is exactly what `submit_job` expects (`Callable[[], Any]`).
  - **L62** `with operation_runtime.exclusive_operation("assessment"):` uses a **context manager**: code inside the `with` block runs while holding a `threading.Lock`, and the lock is released automatically on exit, even if an exception is raised. It ensures only one assessment operation (single or batch) runs at a time in this process. This protects the LLM gateway and the revision-numbering logic from concurrent runs.
  - **L63** Runs the assessment and returns its `AssessmentResponse`. The job queue stores this as the job's `result`.
- **L65** Registers the job and schedules `handler` to run after the response is sent. Returns the `JobAcceptedResponse`, which FastAPI sends as the 202 body. `"assessment"` is the job's `operation` label.
- Note: if another assessment job already holds the lock, this job's background thread simply **waits** on L62. Its status still says `running` (set before `handler()` is called), so a single assessment queued behind a long batch can look stuck when it is actually waiting its turn.


#### Lines 67–74

````python
67
68  @router.post(
69      "/api/batch/assess",
70      response_model=JobAcceptedResponse,
71      status_code=status.HTTP_202_ACCEPTED,
72      responses={503: {"model": ErrorResponse}},
73      summary="Queue assessment for all or selected incidents",
74  )
````

- **L68–74** Registers `POST /api/batch/assess` with the same 202/`JobAcceptedResponse` contract as above.


#### Lines 75–88

````python
75  async def batch_assess(
76      background_tasks: BackgroundTasks,
77      incident_numbers: Optional[str] = Query(
78          None,
79          description="Optional comma-separated incident numbers; leave blank for all incidents",
80      ),
81      mode: Literal["default", "force"] = Query(
82          "default",
83          description=(
84              "'default' assesses only incidents missing an active-version assessment; "
85              "'force' re-assesses every incident, overwriting existing active-version rows"
86          ),
87      ),
88  ):
````

- **L75–76** The endpoint function and the injected `BackgroundTasks`.
- **L77–80** `incident_numbers` is an optional comma-separated string. `None` as the default means "all incidents".
- **L81–87** `mode` is typed `Literal["default", "force"]`, so FastAPI accepts only those two values and Swagger shows them as a dropdown. `default` skips incidents that already have an assessment for the active prompt version; `force` re-assesses and overwrites. The `description=( "..." "..." )` uses implicit string concatenation: adjacent string literals inside parentheses are joined into one string.


#### Lines 89–96

````python
89      """Queue assessment for every incident or the supplied IDs and return immediately."""
90      parsed_numbers = parse_csv_list(incident_numbers)
91      request = BatchAssessmentRequest(
92          incident_numbers=parsed_numbers,
93          force=(mode == "force"),
94          missing_only=False,
95      )
96
````

- **L89** Docstring.
- **L90** Parses the comma list into a list, or `None`.
- **L91–95** Builds a `BatchAssessmentRequest`. `force=(mode == "force")` converts the two-choice string into a boolean. `missing_only=False` is hard-coded: the service still supports a "skip any incident that has *any* agent assessment" mode, but the HTTP API no longer exposes it. Creating the model also enforces `max_length=500` on the list from the schema, so a larger list fails validation.
- Note: that validation happens in the function body, not in FastAPI's parameter parsing. A `ValidationError` raised here becomes a **500** server error rather than a clean 422, because FastAPI only turns validation errors from its own request parsing into 422s.


#### Lines 97–106

````python
 97      def handler():
 98          with operation_runtime.exclusive_operation("assessment"):
 99              return execute_batch_assessment(
100                  force=request.force,
101                  missing_only=request.missing_only,
102                  incident_numbers=request.incident_numbers,
103              )
104
105      return job_queue.submit_job(background_tasks, "batch_assessment", handler)
106
````

- **L97–103** The closure handler again, holding the same `"assessment"` lock, so a batch and single assessments never overlap. `execute_batch_assessment` loads the incidents, then runs `_process_assessment_incident` on each in a `ThreadPoolExecutor` with `BATCH_MAX_WORKERS` threads. That function applies the skip/force rules and calls `run_single_assessment`. It returns a `BatchAssessmentResponse` with total/success/skipped/error counts and per-incident results. Keyword arguments (`force=...`) make the call order-independent and readable.
- **L105** Queues it as a `"batch_assessment"` job and returns the 202 response.


#### Lines 107–114

````python
107
108  @router.post(
109      "/api/evaluate/assessment",
110      response_model=JobAcceptedResponse,
111      status_code=status.HTTP_202_ACCEPTED,
112      responses={503: {"model": ErrorResponse}},
113      summary="Queue Assessment Agent vs. human evaluation",
114  )
````

- **L108–114** Registers `POST /api/evaluate/assessment`, an evaluation job with the same 202 pattern.


#### Lines 115–135

````python
115  async def evaluate_assessment(
116      background_tasks: BackgroundTasks,
117      incident_numbers: Optional[str] = Query(
118          None,
119          description=(
120              "Optional comma-separated incident numbers; leave blank to evaluate all "
121              "incidents with a 2LOD-approved human assessment (final_approval_by_lod = 'Yes')"
122          ),
123      ),
124      run_agent: bool = Query(
125          False,
126          description=(
127              "If false (default), compare against whatever assessment already exists in the "
128              "DB for the active prompt version — fast, read-only, no new LLM calls (same "
129              "approach as the drafting evaluation). If true, re-invoke the assessment agent "
130              "live for each incident before comparing — slower, and writes a fresh assessment "
131              "row to the DB as a side effect for each incident evaluated, using the same "
132              "deterministic orchestration as /api/assess."
133          ),
134      ),
135  ):
````

- **L115–116** Function and injected background tasks.
- **L117–123** Optional `incident_numbers`. If left blank, the evaluation uses every incident whose human assessment has `final_approval_by_lod = 'Yes'`, i.e. those signed off by the second line of defence. These serve as trustworthy **ground truth**.
- **L124–134** `run_agent: bool`, default `False`. FastAPI converts query strings like `true`, `1`, `yes` into a Python `bool`.
  - `False` makes a fast, read-only comparison of whatever agent assessment is already stored for the active prompt version.
  - `True` re-runs the agent live on each incident first. That is slower, costs LLM calls, and as a side effect writes fresh assessment rows.


#### Lines 136–143

````python
136      """Queue a comparison of Assessment Agent output vs. 2LOD-approved human
137      ground truth for the active assessment prompt version.
138
139      Reports overall reportability_flag accuracy, per-FRB-criterion accuracy,
140      an accuracy breakdown by the agent's confidence tier, and the full
141      per-incident agent-vs-human detail (not just mismatches). Poll the
142      returned status_url for the AssessmentEvaluationResponse summary.
143      """
````

- **L136–143** Docstring describing what the evaluation reports:
  - overall `reportability_flag` accuracy (agent Yes/No against the human Yes/No);
  - accuracy per FRB criterion;
  - accuracy broken down by the agent's confidence tier, which shows whether "high confidence" answers really are more accurate;
  - the full per-incident detail.


#### Lines 144–154

````python
144      parsed_numbers = parse_csv_list(incident_numbers)
145      request = AssessmentEvaluationRequest(incident_numbers=parsed_numbers, run_agent=run_agent)
146
147      def handler():
148          return execute_assessment_evaluation(
149              incident_numbers=request.incident_numbers,
150              run_agent=request.run_agent,
151          )
152
153      return job_queue.submit_job(background_tasks, "evaluate_assessment", handler)
154
````

- **L144–145** Parses the list and builds the validated `AssessmentEvaluationRequest`.
- **L147–151** The handler calls `execute_assessment_evaluation`. That function lazily imports `backend/evaluation/evaluate_assessment_prompts.py`, runs `evaluate(...)` and `build_summary(...)`, saves the summary to a JSON cache file (`save_latest_evaluation`), and returns an `AssessmentEvaluationResponse`.
- **L153** Queues it as an `"evaluate_assessment"` job.
- Note: unlike the two endpoints above, this handler does **not** take `exclusive_operation("assessment")`. With `run_agent=true`, the evaluator calls `run_single_assessment` for each incident, so an evaluation can run assessments at the same time as an `/api/assess` or batch job: exactly the overlap the lock exists to prevent, including concurrent writes to the same incidents' assessment rows. With `run_agent=false` it is read-only and harmless.


#### Lines 155–160

````python
155
156  @router.get(
157      "/api/evaluate/assessment/latest",
158      response_model=Optional[AssessmentEvaluationResponse],
159      summary="Get the last saved Assessment Agent evaluation, if any, for the active prompt version",
160  )
````

- **L156** `@router.get("/api/evaluate/assessment/latest", ...)` is a read-only GET endpoint.
- **L158** `response_model=Optional[AssessmentEvaluationResponse]`: the response is either the summary object or JSON `null`.
- **L159** Swagger summary.


#### Lines 161–168

````python
161  async def get_latest_assessment_evaluation_endpoint():
162      """Returns the most recently run evaluation summary, but only if it was
163      run against the currently active assessment prompt version — null
164      otherwise (e.g. no evaluation has ever been run, or the prompt version
165      was changed since the last one). Lets the Admin page show a prior result
166      immediately instead of requiring a fresh (slow, LLM-calling) run."""
167      return get_latest_assessment_evaluation()
168
````

- **L161** The function is named `..._endpoint` so it doesn't clash with the imported service function `get_latest_assessment_evaluation`.
- **L162–166** Docstring. It returns the last evaluation **only if** it was produced with the currently active prompt version, otherwise `null`; a cached result from an old prompt would be misleading. Its purpose is to let the Admin page show the previous result instantly, without re-running a slow, LLM-heavy evaluation.
- **L167** Returns the service result directly. This call runs synchronously (it is not queued as a job), and FastAPI serialises it using the `response_model`. The service reads `latest_assessment_evaluation.json` from the evaluations folder and compares its `prompt_version` with the active label.


---

## 11. `backend/routers/drafting.py`

*196 lines · complete*

This is the **HTTP layer for the Drafting Agent**. It exposes four endpoints:
- `POST /api/draft` queues one draft or revision.
- `POST /api/batch/draft` queues drafts for many incidents.
- `POST /api/evaluate/draft` queues the LLM-as-Judge evaluation.
- `GET /api/evaluate/draft/latest` returns the cached last evaluation.

It is deliberately "thin": each endpoint only validates inputs, packs them into a schema object, and hands the real work to `backend/services/drafting_service.py`. The three slow, LLM-calling operations are not run inside the request. They are wrapped in a `handler` function and given to `job_queue.submit_job`, which returns **202 Accepted** with a `job_id` straight away. The client then polls `GET /api/jobs/{job_id}` (in `routers/operations.py`) for the result. `main.py` mounts this router with `app.include_router(drafting.router)`.


#### Lines 1–2

````python
1  """Drafting + drafting-evaluation endpoints — thin HTTP layer over backend.services.drafting_service."""
2
````

- **L1** The module docstring. It states the design rule for every router in this repo: no business logic here, just an HTTP wrapper over `drafting_service`.


#### Lines 3–6

````python
3  from typing import Literal, Optional
4
5  from fastapi import APIRouter, BackgroundTasks, Query, status
6
````

- **L3** `Literal` and `Optional` are used to type the query parameters, as explained in `schemas.py`.
- **L5** These are the FastAPI imports:
  - `APIRouter` is a mini-app that groups related routes, which `main.py` later attaches to the main `FastAPI` app.
  - `BackgroundTasks` is a FastAPI object that collects functions to run *after* the HTTP response has been sent.
  - `Query` declares that a parameter comes from the URL query string (`?incident_number=...`) and attaches validation rules.
  - `status` provides named HTTP status code constants such as `HTTP_202_ACCEPTED`.


#### Lines 7–22

````python
 7  from backend.schemas import (
 8      BatchDraftRequest,
 9      DraftEvaluationRequest,
10      DraftEvaluationResponse,
11      DraftRequest,
12      ErrorResponse,
13      JobAcceptedResponse,
14  )
15  from backend.routers import parse_csv_list
16  from backend.services import job_queue, operation_runtime
17  from backend.services.drafting_service import (
18      execute_batch_draft,
19      execute_draft_evaluation,
20      get_latest_draft_evaluation,
21      run_single_draft,
22  )
````

- **L7–14** This imports the Pydantic models used here from `backend/schemas.py`:
  - The request models, used to validate and pack the inputs.
  - `DraftEvaluationResponse`, for the `/latest` endpoint.
  - `ErrorResponse`, which only documents error shapes.
  - `JobAcceptedResponse`, the 202 body.

  The parentheses let one `import` statement span multiple lines.
- **L15** `parse_csv_list` comes from `backend/routers/__init__.py`. It turns `"INC1, INC2,"` into `["INC1", "INC2"]` (trimming spaces and dropping empties), or returns `None` if the parameter was blank.
- **L16** These are two service modules:
  - `job_queue` is the in-memory background job registry.
  - `operation_runtime` holds the per-operation locks, so only one drafting run uses the LLM workflow at a time.
- **L17–22** These are the four drafting service functions that do the actual work. Each endpoint below calls exactly one of them.


#### Lines 23–28

````python
23  from backend.logger import get_logger
24
25  logger = get_logger(__name__)
26
27  router = APIRouter(tags=["Drafting"])
28
````

- **L23–25** These lines get a module-specific logger from the project's `backend/logger.py`. `__name__` is the module's dotted name (`backend.routers.drafting`), so log lines show where they came from.
- **L27** `router = APIRouter(tags=["Drafting"])` creates the router. `tags` groups these endpoints under a "Drafting" heading in the Swagger `/docs` page.


#### Lines 29–36

````python
29
30  @router.post(
31      "/api/draft",
32      response_model=JobAcceptedResponse,
33      status_code=status.HTTP_202_ACCEPTED,
34      responses={503: {"model": ErrorResponse}},
35      summary="Queue draft or revision for one incident",
36  )
````

- **L30** `@router.post(...)` is a **decorator**: a function that wraps the function defined right below it. This one registers `draft_incident` as the handler for HTTP POST requests to `/api/draft`.
- **L32** `response_model=JobAcceptedResponse` tells FastAPI to validate and serialize the return value against that schema, and to document it.
- **L33** `status_code=status.HTTP_202_ACCEPTED` means the success code is 202 ("accepted for processing, not finished yet") instead of the default 200. That is the correct semantic for a queued job.
- **L34** `responses={503: ...}` documents an extra possible error response in the OpenAPI docs. Note: nothing on this path appears to raise a 503. The real "can't accept" error is the **429** that `job_queue.submit_job` raises when the in-memory queue is full, and that one isn't documented here. This is a documentation mismatch only.
- **L35** `summary` is the one-line title shown in `/docs`.


#### Lines 37–49

````python
37  async def draft_incident(
38      background_tasks: BackgroundTasks,
39      incident_number: str = Query(
40          ...,
41          description="Incident number, for example INC0575428",
42          min_length=1,
43          max_length=50,
44          pattern=r"^[A-Za-z0-9_-]+$",
45      ),
46      template_stage: Literal["Drafting Agent Draft", "1LOD R&C", "2LOD R&C", "Reg Relations"] = Query(
47          "Drafting Agent Draft",
48          description="Draft or review stage",
49      ),
````

- **L37** `async def` makes this an **async endpoint**. FastAPI runs it directly on the event loop. That is fine here because the function only builds objects and queues work; it does not block.
- **L38** `background_tasks: BackgroundTasks` is a special parameter. Because of its type hint, FastAPI's dependency injection hands in a fresh `BackgroundTasks` object for this request, rather than reading it from the URL.
- **L39–45** `incident_number: str = Query(...)` is required (`...`). It is read from the query string, 1–50 characters, with the same safe-character regex as the schema. FastAPI rejects bad values with a 422 before this function runs.
- **L46–49** `template_stage` is a `Literal` of the four agent-writable stages, read from the query and defaulting to `"Drafting Agent Draft"`. Swagger shows it as a dropdown. Note: it repeats the list instead of reusing the `TemplateStage` alias from `schemas.py`, so the two could drift apart if a stage is ever added.


#### Lines 50–64

````python
50      feedback: Optional[str] = Query(
51          None,
52          description="Optional reviewer feedback for a revision",
53          max_length=10000,
54      ),
55      assessment_prompt_version: Optional[str] = Query(
56          None,
57          description=(
58              "Which reportability assessment to use as drafting context: omit for the active "
59              "assessment agent prompt version (default), 'human' for the human-provided "
60              "assessment, or a specific version label (e.g. 'v1')."
61          ),
62          max_length=50,
63      ),
64  ):
````

- **L50–54** `feedback` is optional reviewer text, up to 10,000 characters. If given, the service revises the existing template for that stage instead of drafting fresh.
- **L55–63** `assessment_prompt_version` is optional. It chooses which assessment row is fed to the drafting LLM as context: the active version (when omitted), `"human"`, or a label such as `"v1"`.
- **L64** `):` closes the parameter list. All these parameters are query parameters, so a call looks like `POST /api/draft?incident_number=INC0575428&template_stage=1LOD%20R%26C`.


#### Lines 65–73

````python
65      """Queue one notification draft or revision and return immediately."""
66      request = DraftRequest(
67          incident_number=incident_number,
68          template_stage=template_stage,
69          feedback=feedback,
70          assessment_prompt_version=assessment_prompt_version,
71      )
72      logger.info("Queueing draft for incident: %s", incident_number)
73
````

- **L65** The endpoint docstring. FastAPI uses it as the description in `/docs`.
- **L66–71** This packs the query parameters into a `DraftRequest` Pydantic model. Doing this runs the schema's validators a second time and gives the service one tidy object instead of four loose arguments. `run_single_draft` expects exactly this type.
- **L72** This logs that a draft is being queued. The `%s` placeholder style lets the logger skip formatting if INFO logging is turned off.


#### Lines 74–79

````python
74      def handler():
75          with operation_runtime.exclusive_operation("drafting"):
76              return run_single_draft(request)
77
78      return job_queue.submit_job(background_tasks, "draft", handler)
79
````

- **L74** `def handler():` is a **nested function (closure)**. It takes no arguments but can still "see" `request` from the enclosing function. `job_queue.submit_job` requires a no-argument callable precisely so each endpoint can close over its own context like this.
- **L75** `with operation_runtime.exclusive_operation("drafting"):` is a **context manager** (a `with` block). It acquires the `"drafting"` `threading.Lock` on entry and always releases it on exit, even if an exception happens. This serializes drafting jobs, so only one draft or batch draft runs at a time. The automatic workflow engine (`workflow_service._run_draft`) takes the same lock.
- **L76** Inside the lock, this calls the service. `run_single_draft` fills the deterministic template fields, calls the Drafting Agent LLM, writes to `template_details`, audits, and returns a `DraftResponse`.
- **L78** `job_queue.submit_job(background_tasks, "draft", handler)` registers a job with status "queued" and schedules `handler` to run after the response is sent. It then returns the `JobAcceptedResponse` (job id, status URL, timestamp), which becomes this endpoint's 202 body. Note that `handler` itself is passed, not `handler()`: the function is not called here.


#### Lines 80–87

````python
80
81  @router.post(
82      "/api/batch/draft",
83      response_model=JobAcceptedResponse,
84      status_code=status.HTTP_202_ACCEPTED,
85      responses={503: {"model": ErrorResponse}},
86      summary="Queue drafting for all or selected eligible incidents",
87  )
````

- **L81–87** The decorator for `POST /api/batch/draft`. It has the same shape as above: 202, `JobAcceptedResponse`, and the documented 503. The summary says it queues drafting for every *eligible* incident. Eligible means the chosen assessment says "Yes".


#### Lines 88–110

````python
 88  async def batch_draft(
 89      background_tasks: BackgroundTasks,
 90      incident_numbers: Optional[str] = Query(
 91          None,
 92          description="Optional comma-separated incident numbers; leave blank for all eligible incidents",
 93      ),
 94      mode: Literal["default", "force"] = Query(
 95          "default",
 96          description=(
 97              "'default' drafts only incidents missing a complete active-version draft; "
 98              "'force' redrafts every eligible incident, overwriting existing active-version rows"
 99          ),
100      ),
101      assessment_prompt_version: Optional[str] = Query(
102          None,
103          description=(
104              "Which reportability assessment version determines eligibility and drafting "
105              "context: omit for the active assessment agent prompt version (default), 'human' "
106              "for human-provided assessments, or a specific version label (e.g. 'v1')."
107          ),
108          max_length=50,
109      ),
110  ):
````

- **L88–89** This is an async endpoint, again receiving a `BackgroundTasks` object.
- **L90–93** `incident_numbers` is a single **comma-separated** string in the query (for example `INC1,INC2`), because a plain query parameter is easier to type in Swagger than a JSON list. If it is blank, all eligible incidents are drafted.
- **L94–100** `mode: Literal["default", "force"]` is a user-friendly switch:
  - `"default"` only drafts incidents that are missing a *complete* draft for the active drafting prompt version.
  - `"force"` redrafts everything eligible and overwrites existing rows.
- **L101–109** `assessment_prompt_version` works the same as in the single-draft endpoint, except that here it also determines **eligibility**.


#### Lines 111–118

````python
111      """Queue drafting for eligible incidents and return immediately."""
112      parsed_numbers = parse_csv_list(incident_numbers)
113      request = BatchDraftRequest(
114          incident_numbers=parsed_numbers,
115          force=(mode == "force"),
116          assessment_prompt_version=assessment_prompt_version,
117      )
118
````

- **L111** The docstring.
- **L112** This converts the CSV string to a list, or `None`.
- **L113–117** This builds a `BatchDraftRequest`. `force=(mode == "force")` turns the friendly mode string into the boolean the schema and service use. Building the model also enforces the schema's 500-item cap on `incident_numbers`.


#### Lines 119–128

````python
119      def handler():
120          with operation_runtime.exclusive_operation("drafting"):
121              return execute_batch_draft(
122                  incident_numbers=request.incident_numbers,
123                  force=request.force,
124                  assessment_prompt_version=request.assessment_prompt_version,
125              )
126
127      return job_queue.submit_job(background_tasks, "batch_draft", handler)
128
````

- **L119–125** The closure takes the same `"drafting"` lock, then calls `execute_batch_draft` with the three values unpacked as keyword arguments. That service function uses `common.execute_batch`, which runs `_process_draft_incident` over the incidents in a thread pool (`BATCH_MAX_WORKERS` threads) and returns a `BatchDraftResponse` of counts and per-incident results. The lock is held for the **whole batch**, so a single draft queued meanwhile waits until the batch finishes.
- **L127** This queues the job under the operation name `"batch_draft"`.


#### Lines 129–136

````python
129
130  @router.post(
131      "/api/evaluate/draft",
132      response_model=JobAcceptedResponse,
133      status_code=status.HTTP_202_ACCEPTED,
134      responses={503: {"model": ErrorResponse}},
135      summary="Queue LLM-as-Judge evaluation of drafting output",
136  )
````

- **L130–136** The decorator for `POST /api/evaluate/draft`. It queues the LLM-as-Judge evaluation and returns 202.


#### Lines 137–157

````python
137  async def evaluate_draft(
138      background_tasks: BackgroundTasks,
139      incident_numbers: Optional[str] = Query(
140          None,
141          description="Optional comma-separated incident numbers; leave blank to evaluate all with both agent and human drafts",
142      ),
143      template_stage: Optional[Literal["Drafting Agent Draft", "1LOD R&C", "2LOD R&C", "Reg Relations"]] = Query(
144          None,
145          description="Optional single template stage to evaluate",
146      ),
147      drafting_versions: Optional[str] = Query(
148          None,
149          description="Optional comma-separated drafting prompt versions; blank evaluates all available",
150          max_length=1000,
151      ),
152      assessment_versions: Optional[str] = Query(
153          None,
154          description="Optional comma-separated assessment prompt versions; blank evaluates all available",
155          max_length=1000,
156      ),
157  ):
````

- **L137–138** This is an async endpoint with `BackgroundTasks`.
- **L139–142** `incident_numbers` is an optional CSV. If it is blank, the evaluation covers every incident that has both an agent draft and a human draft.
- **L143–146** `template_stage` is optional. It uses the same four-stage `Literal`, wrapped in `Optional`, so leaving it out means "all stages".
- **L147–156** `drafting_versions` and `assessment_versions` are optional CSV strings of prompt versions, up to 1,000 characters each. They select which prompt combinations get judged.


#### Lines 158–164

````python
158      """Queue an LLM-as-Judge comparison of agent drafts vs. human drafts.
159
160      A judge model rates each authored field (title, current_status, description,
161      impact, management_actions) and an overall verdict as High/Medium/Low match
162      against the human reference draft at the same incident and stage. Poll the
163      returned status_url for the DraftEvaluationResponse summary.
164      """
````

- **L158–164** The docstring explains the evaluation. A judge LLM rates each *authored* field (title, current_status, description, impact, management_actions) plus an overall verdict as High, Medium or Low match against the human reference draft at the same incident and stage. You poll `status_url` to get a `DraftEvaluationResponse`.


#### Lines 165–171

````python
165      request = DraftEvaluationRequest(
166          incident_numbers=parse_csv_list(incident_numbers),
167          template_stage=template_stage,
168          drafting_versions=parse_csv_list(drafting_versions),
169          assessment_versions=parse_csv_list(assessment_versions),
170      )
171
````

- **L165–170** This packs everything into a `DraftEvaluationRequest`, converting each CSV parameter to a list (or `None`) with `parse_csv_list`. That validates the list lengths against the schema (at most 500 incidents, at most 100 versions).


#### Lines 172–181

````python
172      def handler():
173          return execute_draft_evaluation(
174              incident_numbers=request.incident_numbers,
175              template_stage=request.template_stage,
176              drafting_versions=request.drafting_versions,
177              assessment_versions=request.assessment_versions,
178          )
179
180      return job_queue.submit_job(background_tasks, "evaluate_draft", handler)
181
````

- **L172–178** The closure calls `execute_draft_evaluation`. That loads `backend/evaluation/evaluate_drafting_prompts.py` lazily, runs the judge, builds the summary, and caches it if the run was unfiltered. Notice that there is **no** `exclusive_operation` lock here, unlike the draft endpoints. The evaluation only *reads* drafts and calls the judge model, so it does not compete with the drafting workflow for the lock and can run alongside a draft job.
- **L180** This queues the job as `"evaluate_draft"` and returns the 202 body.


#### Lines 182–187

````python
182
183  @router.get(
184      "/api/evaluate/draft/latest",
185      response_model=Optional[DraftEvaluationResponse],
186      summary="Get the last saved whole-dataset Drafting Agent evaluation, if any, for the active prompt version",
187  )
````

- **L183–187** `@router.get(...)` registers a GET endpoint at `/api/evaluate/draft/latest`. `response_model=Optional[DraftEvaluationResponse]` means the body is either a full evaluation summary or JSON `null`. No background job is involved, because this is a quick cache read.


#### Lines 188–196

````python
188  async def get_latest_draft_evaluation_endpoint():
189      """Returns the most recently run whole-dataset evaluation summary, but
190      only if the currently active drafting prompt version was part of it —
191      null otherwise (e.g. no evaluation has ever been run, the prompt version
192      was changed since, or the last run was filtered to specific incidents/
193      versions). Lets the Admin page show a prior result immediately instead
194      of requiring a fresh (slow, LLM-calling) run."""
195      return get_latest_draft_evaluation()
196
````

- **L188** The function is named `..._endpoint` so that it does not shadow the imported service function `get_latest_draft_evaluation` (L20). If both had the same name, L195 would recursively call itself.
- **L189–194** The docstring. It returns the last whole-dataset evaluation, but **only** if the currently active drafting prompt version was part of it. Otherwise it returns `null`: for example if nothing has been run yet, if the prompt changed since, or if the last run was filtered. The Admin page uses this to show a previous result instantly instead of starting a slow LLM run.
- **L195** This delegates to the service. The service reads the cache through `eval_cache.load_latest_evaluation("drafting")` and checks the version.


---

## 12. `backend/routers/review.py`

*113 lines · complete*

This is the **HTTP layer for the human review loop** in the operator UI. Each reviewer role (MIMT, 1LOD, 2LOD, Reg_Relations) owns one template stage. Through these endpoints reviewers can:
- Fetch the draft they should edit.
- See their version history.
- Save edits, where each save becomes a new versioned row.
- **Approve** or **return feedback**.

Approve and feedback actions only write the incident's `lod1_*` and `lod2_*` columns (status, timestamp, feedback). A Postgres trigger turns those writes into events, and `workflow_listener` → `workflow_service` reacts. For example, it re-runs the Drafting Agent at "1LOD R&C" with the feedback, or advances the incident to 2LOD review. All logic is in `backend/services/review_service.py`. `main.py` mounts this router.


#### Lines 1–8

````python
1  """Review + feedback endpoints for the operator UI.
2
3  Thin HTTP layer over backend.services.review_service. Approve/feedback actions
4  write the incident's lod1_/lod2_ columns, which the DB trigger -> workflow
5  listener turns into the next lifecycle step (e.g. re-invoking the Drafting
6  Agent with the reviewer's feedback).
7  """
8
````

- **L1–7** The module docstring. It describes the event-driven design: these endpoints never call an agent directly. They write columns, and the DB trigger plus the workflow listener decide the next lifecycle step.


#### Lines 9–27

````python
 9  from typing import Any, Dict, List
10
11  from fastapi import APIRouter, Query, status
12
13  from backend.schemas import (
14      ErrorResponse,
15      FeedbackRequest,
16      RoleInfo,
17      SaveTemplateRequest,
18      TemplateDraftOut,
19      TemplateVersionSummary,
20  )
21  from backend.services import review_service
22  from backend.logger import get_logger
23
24  logger = get_logger(__name__)
25
26  router = APIRouter(tags=["Review"])
27
````

- **L9** These are the type hints for return values.
- **L11** The FastAPI imports. `Query` declares URL query parameters with validation.
- **L13–20** These are the schemas:
  - `FeedbackRequest` is the body for the feedback endpoints.
  - `RoleInfo` is a role/stage pair.
  - `SaveTemplateRequest` is the body for saving edits.
  - `TemplateDraftOut` is the editor payload.
  - `TemplateVersionSummary` is one history entry.
  - `ErrorResponse` is for documentation only.
- **L21** `review_service` is where the real work happens.
- **L22–24** This sets up the logger. As in `intake.py`, it is not used in this file.
- **L26** The router is tagged `"Review"` in `/docs`.


#### Lines 28–33

````python
28
29  @router.get("/api/review/roles", response_model=List[RoleInfo], summary="List reviewer roles and their stages")
30  async def get_roles() -> List[Dict[str, str]]:
31      return review_service.roles()
32
33
````

- **L29** This registers `GET /api/review/roles` in a single-line decorator, with `response_model=List[RoleInfo]`.
- **L30–31** This returns the role → stage list built from `ROLE_TO_STAGE` in `schemas.py` (for example `{"role": "1LOD", "stage": "1LOD R&C"}`). The UI uses it to populate a role selector. Keeping the mapping on the server means the front-end doesn't hard-code it.


#### Lines 34–46

````python
34  @router.get(
35      "/api/review/template",
36      response_model=TemplateDraftOut,
37      responses={404: {"model": ErrorResponse}},
38      summary="Get the draft to edit for an incident + role",
39  )
40  async def get_template(
41      incident_number: str = Query(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$"),
42      role: str = Query(..., description="Reviewer role: MIMT, 1LOD, 2LOD, Reg_Relations"),
43  ) -> Dict[str, Any]:
44      return review_service.get_template_for_role(incident_number, role)
45
46
````

- **L34–39** This registers `GET /api/review/template`, which returns the draft to load into the editor for an incident and role.
- **L41** `incident_number` is a required query parameter with the usual 1–50 character, safe-characters regex validation.
- **L42** `role: str` is a required query parameter. Note: it is typed as a plain `str`, not the `ReviewRole` `Literal` that `SaveTemplateRequest` uses. An unknown role is therefore not caught by FastAPI's 422 validation. Instead the service raises a **400** ("Unknown review role"), and that status isn't listed in this route's `responses`. It still works; it is just inconsistent.
- **L43** The return type annotation.
- **L44** `review_service.get_template_for_role` resolves the stage for the role, 404s if the incident is missing, and returns the first available of:
  1. This role's own latest human edit.
  2. The latest draft at **any** stage. That is the current working draft, for example the agent's draft or the previous reviewer's edit.
  3. An "empty" template pre-filled from the incident record.

  The response's `source` field says which one was used.


#### Lines 47–58

````python
47  @router.get(
48      "/api/review/template/versions",
49      response_model=List[TemplateVersionSummary],
50      responses={404: {"model": ErrorResponse}},
51      summary="List saved human-edit versions for an incident + role",
52  )
53  async def get_versions(
54      incident_number: str = Query(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$"),
55      role: str = Query(...),
56  ) -> List[Dict[str, Any]]:
57      return review_service.list_versions(incident_number, role)
58
````

- **L47–52** This registers `GET /api/review/template/versions`, the edit history for one incident and role.
- **L54** `incident_number` is validated as above.
- **L55** `role: str = Query(...)` is required, with no description or `Literal` (same caveat as L42).
- **L56–57** This returns the human-edit rows (`prompt_version` is `None`) for this role's stage, newest first, each summarized as id, stage, `drafted_by`, `prompt_version`, timestamps and title.


#### Lines 59–68

````python
59
60  @router.post(
61      "/api/review/template",
62      response_model=TemplateDraftOut,
63      responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
64      summary="Save an edited draft as a new versioned human row",
65  )
66  async def save_template(request: SaveTemplateRequest) -> Dict[str, Any]:
67      return review_service.save_template(request.model_dump())
68
````

- **L60–65** This registers `POST /api/review/template`, which saves an edited draft. It documents 400 (unknown role) and 404 (unknown incident).
- **L66** `request: SaveTemplateRequest` is read from the **JSON body**. Pydantic checks that `role` is one of the four `ReviewRole` values.
- **L67** `request.model_dump()` converts the model to a dict, *including* `None` fields. The service skips `None` fields itself. `save_template` then:
  - Inserts a **new** `template_details` row for the role's stage, with `prompt_version=None` and `drafted_by="<role> (human)"`. A NULL key never matches an existing row, so every save inserts a fresh row and the full history is kept.
  - Writes an audit-log entry.
  - Returns the saved row as a `TemplateDraftOut`.


#### Lines 69–77

````python
69
70  @router.post(
71      "/api/review/{incident_number}/1lod/approve",
72      responses={404: {"model": ErrorResponse}},
73      summary="1LOD approves (advances to 2LOD review)",
74  )
75  async def lod1_approve(incident_number: str) -> Dict[str, Any]:
76      return review_service.lod1_approve(incident_number)
77
````

- **L70–74** This registers `POST /api/review/{incident_number}/1lod/approve`. `incident_number` is a path parameter.
- **L75–76** `review_service.lod1_approve` calls `_record_review_decision(..., "1lod", "Approved")`. That writes an audit entry (`"1lod_approved"`) and sets `lod1_approval_status = "Approved"` and `lod1_approval_timestamp = now` on the incident. The DB trigger then fires a `1lod_approved` event, and `workflow_service` moves the incident to 2LOD review.


#### Lines 78–86

````python
78
79  @router.post(
80      "/api/review/{incident_number}/1lod/feedback",
81      responses={404: {"model": ErrorResponse}},
82      summary="1LOD returns feedback (re-invokes drafting at 1LOD R&C)",
83  )
84  async def lod1_feedback(incident_number: str, request: FeedbackRequest) -> Dict[str, Any]:
85      return review_service.lod1_feedback(incident_number, request.feedback)
86
````

- **L79–83** This registers `POST /api/review/{incident_number}/1lod/feedback`.
- **L84** The handler takes **two** inputs at once: `incident_number` from the URL path and `request: FeedbackRequest` from the JSON body. FastAPI works out where each comes from by its type: a simple `str` that matches a path placeholder comes from the path, and a Pydantic model comes from the body.
- **L85** This passes just the feedback string to `review_service.lod1_feedback`. That sets `lod1_approval_status = "Feedback"`, the timestamp, and `lod1_feedback = <text>`. The resulting event makes the workflow engine re-run the Drafting Agent at stage `"1LOD R&C"` with that feedback, and then return the incident to 1LOD review.


#### Lines 87–104

````python
 87
 88  @router.post(
 89      "/api/review/{incident_number}/2lod/approve",
 90      responses={404: {"model": ErrorResponse}},
 91      summary="2LOD approves (advances to Reg Relations)",
 92  )
 93  async def lod2_approve(incident_number: str) -> Dict[str, Any]:
 94      return review_service.lod2_approve(incident_number)
 95
 96
 97  @router.post(
 98      "/api/review/{incident_number}/2lod/feedback",
 99      responses={404: {"model": ErrorResponse}},
100      summary="2LOD returns feedback (re-invokes drafting at 2LOD R&C)",
101  )
102  async def lod2_feedback(incident_number: str, request: FeedbackRequest) -> Dict[str, Any]:
103      return review_service.lod2_feedback(incident_number, request.feedback)
104
````

- **L88–94** `POST /api/review/{incident_number}/2lod/approve` mirrors L70–76 for 2LOD. It writes `lod2_approval_status = "Approved"`, and the workflow advances the incident to Reg Relations. 2LOD approval is the final sign-off before submission.
- **L97–103** `POST /api/review/{incident_number}/2lod/feedback` mirrors L79–85 for 2LOD. It writes `lod2_feedback`, and the workflow re-drafts at `"2LOD R&C"` with that feedback.


#### Lines 105–113

````python
105
106  @router.post(
107      "/api/review/{incident_number}/reg-relations/complete",
108      responses={404: {"model": ErrorResponse}},
109      summary="Reg Relations marks the manual submission complete",
110  )
111  async def reg_relations_complete(incident_number: str) -> Dict[str, Any]:
112      return review_service.reg_relations_complete(incident_number)
113
````

- **L106–110** This registers `POST /api/review/{incident_number}/reg-relations/complete`. Regulatory Relations uses it to record that they have **manually** submitted the notification to the regulator. The app itself never submits anything.
- **L111–112** `review_service.reg_relations_complete` writes an audit entry (`"reg_relations_completed"`) and sets `submitted_to_regulators = True` and `submission_timestamp = now` on the incident. That closes out the lifecycle. The DB trigger emits a `notification_submitted` event, and `workflow_service._handle_reg_submitted` moves the incident's status from Reg Relations to Submitted and then to Completed.
- Like `intake.py`, all these endpoints are `async def` but call blocking DB code. See the note at the end of the `intake.py` explanation.


---

## 13. `backend/routers/operations.py`

*68 lines · complete*

This router holds the **housekeeping endpoints** that are not tied to one agent:
- `GET /api/jobs/{job_id}` is the polling endpoint for every background job queued by the assessment and drafting routers.
- `GET /health` reports database connectivity and the registered agents. It is useful for load balancers and monitoring.
- `GET /agents` lists the agents and their tools.
- `GET /` is an API landing page.

It is a thin layer over `backend/services/job_queue.py`, `backend/agents/__init__.py` (the agent registry) and `backend/utils/postgres_client.py`. `main.py` mounts it last with `app.include_router(operations.router)`.


#### Lines 1–12

````python
 1  """Health/agents/root/job-status endpoints."""
 2
 3  from fastapi import APIRouter
 4
 5  from backend.agents import get_agent_registry
 6  from backend.schemas import ErrorResponse, HealthResponse, JobStatusResponse
 7  from backend.services import job_queue
 8  from backend.utils.postgres_client import get_client
 9  from backend.config import ENVIRONMENT
10
11  router = APIRouter(tags=["Operations"])
12
````

- **L1** The module docstring lists the four endpoint groups.
- **L3** `APIRouter` is FastAPI's way to group routes.
- **L5** `get_agent_registry` comes from `backend/agents/__init__.py`. It builds a dict mapping each agent's name to its display name, description, and list of tool names, by reading module-level constants (`AGENT_NAME`, `DISPLAY_NAME`, `DESCRIPTION`, `TOOLS`) from each agent module.
- **L6** The schemas used: `ErrorResponse` (documents the 404), `HealthResponse` and `JobStatusResponse`.
- **L7** `job_queue` is the in-memory job registry that holds every queued, running and finished job.
- **L8** `get_client` returns the shared Postgres client (a connection pool wrapper).
- **L9** `ENVIRONMENT` is read from the env var of the same name in `config.py`, defaulting to `"development"`. It is shown on the root endpoint.
- **L11** This creates the router with tag `"Operations"` for `/docs`. Unlike the other routers, this file has no logger.


#### Lines 13–23

````python
13
14  @router.get(
15      "/api/jobs/{job_id}",
16      response_model=JobStatusResponse,
17      responses={404: {"model": ErrorResponse}},
18      summary="Get background job status and result",
19  )
20  async def get_job_status(job_id: str):
21      """Return queued/running state or the completed agent result."""
22      return job_queue.get_job_status(job_id)
23
````

- **L14–19** This registers `GET /api/jobs/{job_id}`. It returns a `JobStatusResponse` and documents a 404.
- **L20** `job_id` is a **path parameter**, filled from `{job_id}` in the URL. It is the UUID that the 202 response's `status_url` points to.
- **L21–22** This delegates to `job_queue.get_job_status`. That function takes a thread lock, looks up the job in the `_jobs` dict, raises `HTTPException(404, "Job not found")` if it is missing, copies the entry, and returns `JobStatusResponse(**snapshot)`. The response includes `status`, the timestamps, and, once completed, the `result`. The `result` is the agent's output, already serialized to a dict.
- Because jobs live **only in process memory** (as the `job_queue` module docstring warns), a server restart or a second worker process means previously issued job IDs return 404.


#### Lines 24–40

````python
24
25  @router.get("/health", response_model=HealthResponse)
26  async def health_check():
27      """Health check endpoint.
28
29      Returns the health status of the application, including database
30      connectivity and available agents.
31      """
32      client = get_client()
33      db_healthy = client.health_check()
34
35      return HealthResponse(
36          status="healthy" if db_healthy else "unhealthy",
37          database="connected" if db_healthy else "disconnected",
38          agents=list(get_agent_registry().keys()),
39      )
40
````

- **L25** `@router.get("/health", response_model=HealthResponse)` registers the health endpoint at `/health`, without the `/api` prefix. A conventional path like that makes it easy for infrastructure probes to find.
- **L26–31** The function and its docstring. It reports database connectivity and the available agents.
- **L32** This gets the shared DB client.
- **L33** `client.health_check()` runs `SELECT 1 AS ok` and returns `True` or `False`. It catches and logs any exception, so this endpoint never crashes because of a DB outage.
- **L35–39** This builds the response using **conditional expressions** (`a if cond else b`):
  - `status` is "healthy" or "unhealthy".
  - `database` is "connected" or "disconnected".
  - `agents=list(get_agent_registry().keys())` gives just the agent names. `.keys()` returns a view, and `list(...)` turns it into a list.

  Note that the HTTP status code is always 200 here, even when the body says "unhealthy". A monitor would have to check the body, not the status code.


#### Lines 41–46

````python
41
42  @router.get("/agents")
43  async def list_agents():
44      """List all available agents and their capabilities."""
45      return {"agents": get_agent_registry()}
46
````

- **L42–45** `GET /agents` returns the full registry dict, wrapped as `{"agents": {...}}`. For each agent it shows the display name, description and tool names. There is no `response_model`, so FastAPI just JSON-encodes the dict. It is useful for checking which LangChain tools each agent can call.


#### Lines 47–68

````python
47
48  @router.get("/")
49  async def root():
50      """Root endpoint with API information."""
51      return {
52          "name": "Anchor AI — Regulatory Notification System",
53          "version": "0.1.0",
54          "environment": ENVIRONMENT,
55          "endpoints": {
56              "health": "/health",
57              "agents": "/agents",
58              "assess": "/api/assess",
59              "batch_assess": "/api/batch/assess",
60              "evaluate_assessment": "/api/evaluate/assessment",
61              "draft": "/api/draft",
62              "batch_draft": "/api/batch/draft",
63              "evaluate_draft": "/api/evaluate/draft",
64              "job_status": "/api/jobs/{job_id}",
65              "docs": "/docs",
66          },
67      }
68
````

- **L48–50** `GET /` is the root URL. It is a self-describing landing page.
- **L51–67** This returns a static dict:
  - The app name.
  - A hard-coded version `"0.1.0"`.
  - The current environment.
  - An `endpoints` map of the main routes.

  Note: this map is incomplete and only lists the agent and job endpoints. None of the operator-UI routes from `intake.py` and `review.py` appear (for example `/api/incidents`, `/api/review/...`), and neither do the `/latest` evaluation endpoints. The authoritative list is the auto-generated `/docs` page.


---

## 14. `backend/services/__init__.py`

*10 lines · complete*

`__init__.py` is the file that makes `backend/services/` a Python **package**, so other code can write `from backend.services import job_queue` or `from backend.services.assessment_service import run_single_assessment`. This one contains only a docstring. It runs no code, but it documents what the service layer is: the business-logic ("orchestration") layer that sits between the HTTP routers in `backend/routers/` and the lower-level tools and DB client. The routers handle HTTP and the services do the actual work.


#### Lines 1–10

````python
 1  """Service layer for Anchor AI — domain orchestration used by backend/routers.
 2
 3  Split out of what used to be a single 1,100+ line backend/main.py:
 4    - job_queue.py:        generic in-memory background job store
 5    - operation_runtime.py: concurrency controls for long-running operations
 6    - common.py:           helpers shared by domain services
 7    - assessment_service.py: single/batch reportability assessment orchestration
 8    - drafting_service.py:   single/batch drafting + LLM-as-judge evaluation orchestration
 9  """
10
````

- **L1** The package docstring. The "service layer" holds domain orchestration used by `backend/routers`. In other words, routers parse requests and then call functions here.
- **L3** Explains the history: these modules were split out of a single `backend/main.py` of more than 1,100 lines. Splitting by responsibility makes each piece easier to read and test.
- **L4** `job_queue.py` is an in-memory store of background jobs. Long LLM operations return a job ID immediately, and the client polls for the result.
- **L5** `operation_runtime.py` holds locks so two long-running LLM workflows of the same kind don't run at once.
- **L6** `common.py` holds small helpers shared across services (batch running, serialization, error text).
- **L7** `assessment_service.py` orchestrates single and batch reportability assessments.
- **L8** `drafting_service.py` orchestrates single and batch drafting, plus the LLM-as-judge evaluation of drafts.
- **L9** The closing `"""`. **L10** is blank.
- Note: the list is incomplete. The folder also contains `eval_cache.py`, `intake_service.py`, `review_service.py`, `workflow_service.py` and `workflow_listener.py`, which aren't mentioned. It's a stale docstring, not a functional problem.


---

## 15. `backend/services/common.py`

*97 lines · complete*

A small toolbox of helpers shared by the service modules:
- **error and serialization helpers**, used by `job_queue.py`, `intake_service.py`, `review_service.py` and `drafting_service.py`;
- a **lazy importer** for the evaluation scripts;
- the **generic parallel batch runner** (`execute_batch`), which both `assessment_service` and `drafting_service` use for "run this for many incidents at once".

It sits under the services and above `postgres_client` and `config`. It knows nothing about assessments or drafts specifically. It just deals with incidents, workers and results.


#### Lines 1–16

````python
 1  """Small helpers shared by domain services."""
 2
 3  from concurrent.futures import ThreadPoolExecutor, as_completed
 4  from datetime import date, datetime
 5  from importlib import import_module
 6  from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar
 7
 8  from fastapi import HTTPException
 9  from pydantic import BaseModel
10
11  from backend.config import BATCH_MAX_WORKERS
12  from backend.schemas import BatchIncidentResult
13  from backend.utils.postgres_client import get_client
14
15  BatchResponseT = TypeVar("BatchResponseT", bound=BaseModel)
16
````

The docstring and imports.
- **L1** A one-line module docstring.
- **L3** `ThreadPoolExecutor` runs functions on a pool of worker threads. `as_completed` yields each task as soon as it finishes. Threads work well here because the work is mostly *waiting* on network I/O (LLM API and DB), not CPU.
- **L4** `date` and `datetime` are used to detect date values that need converting to strings.
- **L5** `import_module` imports a module by its string name at runtime.
- **L6** Type-hint helpers. `Callable[[A], R]` means "a function taking A and returning R". `Type[X]` means "the class X itself, not an instance". `Tuple` is a fixed-shape tuple. `TypeVar` declares a generic type parameter.
- **L8–9** `HTTPException` (FastAPI's HTTP error) and `BaseModel` (the base class of all Pydantic models).
- **L11** `BATCH_MAX_WORKERS` is the number of parallel threads for batches. It's read from the environment in `config.py` (default 2).
- **L12** `BatchIncidentResult` is the per-incident outcome model (`status` is `"success"`, `"skipped"` or `"error"`).
- **L13** `get_client()` returns the shared Postgres client.
- **L15** `BatchResponseT = TypeVar("BatchResponseT", bound=BaseModel)` is a **generic type variable** that can be any Pydantic model class. It lets `execute_batch` declare "I return an instance of whatever response class you pass me", so `BatchAssessmentResponse` in gives a `BatchAssessmentResponse` out, for type checkers and editors.


#### Lines 17–23

````python
17
18  def safe_batch_error(exc: Exception) -> str:
19      """Return client-safe batch error text while details remain in server logs."""
20      if isinstance(exc, HTTPException):
21          return str(exc.detail)
22      return "Processing failed; see server logs for details"
23
````

`safe_batch_error`: turns an exception into text that's safe to show to API users.
- **L20–21** `isinstance(exc, HTTPException)` checks the exception's type. HTTP exceptions are raised deliberately with user-facing messages (e.g. "Incident X was not found"), so their `.detail` is returned.
- **L22** Any other exception (a DB error, an LLM error, a bug) gets a generic message. The real details stay in the server logs, which avoids leaking internals such as SQL, file paths or keys to the client. `job_queue.py` uses this for failed jobs too.


#### Lines 24–28

````python
24
25  def serialize_result(result: Any) -> Any:
26      """Convert Pydantic workflow results to JSON-compatible dictionaries."""
27      return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
28
````

`serialize_result`: converts a job's return value into JSON-friendly data so `job_queue` can store it and return it from `/api/jobs/{id}`.
- **L27** If the object has a `model_dump` method (i.e. it's a Pydantic v2 model), it calls `model_dump(mode="json")`, which returns a plain dict with dates and other types already converted to JSON-safe values. Otherwise it returns the value unchanged. `hasattr` is "duck typing": it checks for the capability instead of the exact class.


#### Lines 29–37

````python
29
30  def to_text(value: Any) -> str:
31      """Normalize nullable database values for string response fields."""
32      if value is None:
33          return ""
34      if isinstance(value, (date, datetime)):
35          return value.isoformat()
36      return str(value)
37
````

`to_text`: normalizes DB values for response fields that must be strings. It's used heavily in `review_service.py` and `drafting_service.py`.
- **L32–33** `None` (SQL NULL) becomes `""`.
- **L34–35** Dates and datetimes become ISO-8601 strings (e.g. `"2026-09-24"` or `"2026-09-24T13:05:00"`). `isinstance(value, (date, datetime))` accepts a tuple of types and matches either one.
- **L36** Anything else (numbers, UUIDs, and so on) is passed through `str()`.


#### Lines 38–42

````python
38
39  def serialize_db_value(value: Any) -> Any:
40      """Convert date-like database values while preserving all other types."""
41      return value.isoformat() if isinstance(value, (date, datetime)) else value
42
````

`serialize_db_value`: like `to_text`, but **only** converts dates. Other values keep their types (numbers stay numbers, and `None` stays `None`, which becomes `null` in JSON). `intake_service.py` uses it to make a whole DB row JSON-safe.
- **L41** One line with an inline conditional: `x.isoformat() if isinstance(...) else x`.


#### Lines 43–47

````python
43
44  def load_evaluator(module_name: str) -> Any:
45      """Lazily import an evaluator without loading it at application startup."""
46      return import_module(f"backend.evaluation.{module_name}")
47
````

`load_evaluator`: a lazy import.
- **L46** `import_module(f"backend.evaluation.{module_name}")` imports, for example, `backend.evaluation.evaluate_assessment_prompts` **only when** an evaluation is actually requested. The evaluation modules pull in heavier dependencies and LLM-judge setup, so not importing them at startup keeps the API quick to boot. Python caches imported modules, so repeated calls are cheap.


#### Lines 48–58

````python
48
49  def load_incidents(incident_numbers: Optional[List[str]]) -> Tuple[List[Dict[str, Any]], List[BatchIncidentResult]]:
50      """Load all or selected incidents and report unknown requested IDs."""
51      client = get_client()
52      if not incident_numbers:
53          return client.db_query(
54              "incidents",
55              filters=None,
56              columns=["incident_number", "priority", "legal_entity", "incident_title"],
57          ), []
58
````

`load_incidents`, part 1: **all incidents** mode.
- **L49** It takes an optional list of incident numbers and returns a **tuple** of two lists: the incident rows found, and pre-built "error" results for any numbers that weren't found.
- **L51** Gets the DB client.
- **L52** `if not incident_numbers:` is true for both `None` and an empty list, and means "process everything".
- **L53–57** Queries all incidents (`filters=None`), selecting only 4 light columns. The worker re-fetches the full row itself when needed. It returns `(rows, [])`: the trailing `, []` after the call makes this a 2-tuple, with nothing missing.


#### Lines 59–76

````python
59      incidents = []
60      missing = []
61      for incident_number in dict.fromkeys(incident_numbers):
62          rows = client.db_query(
63              "incidents",
64              {"incident_number": incident_number},
65              ["incident_number", "priority", "legal_entity", "incident_title"],
66          )
67          if rows:
68              incidents.append(rows[0])
69          else:
70              missing.append(BatchIncidentResult(
71                  incident_number=incident_number,
72                  status="error",
73                  detail="Incident not found",
74              ))
75      return incidents, missing
76
````

`load_incidents`, part 2: **selected incidents** mode.
- **L59–60** Two accumulators: found rows and missing results.
- **L61** `dict.fromkeys(incident_numbers)` builds a dict whose keys are the numbers. Iterating over it gives each number once, **in the original order**. It's a neat de-duplication trick that keeps order (unlike `set`).
- **L62–66** Looks up each number individually, with the same 4 columns. This is one query per incident, which is fine at this data scale.
- **L67–68** If the incident is found, its row is kept.
- **L69–74** If it isn't found, the code creates a `BatchIncidentResult(status="error", detail="Incident not found")`, so the batch response reports the unknown ID instead of silently ignoring it.
- **L75** Returns both lists.


#### Lines 77–89

````python
77
78  def execute_batch(
79      incident_numbers: Optional[List[str]],
80      worker: Callable[[Dict[str, Any]], BatchIncidentResult],
81      response_model: Type[BatchResponseT],
82  ) -> BatchResponseT:
83      """Run a per-incident worker concurrently and aggregate its outcomes."""
84      incidents, results = load_incidents(incident_numbers)
85      with ThreadPoolExecutor(max_workers=BATCH_MAX_WORKERS) as executor:
86          futures = [executor.submit(worker, incident) for incident in incidents]
87          for future in as_completed(futures):
88              results.append(future.result())
89
````

`execute_batch`, part 1: the generic parallel runner.
- **L78–82** Parameters:
  - `incident_numbers` is optional and means all incidents when `None`.
  - `worker` is a function that takes one incident dict and returns a `BatchIncidentResult`.
  - `response_model` is the Pydantic class to build at the end.
  
  The return type `BatchResponseT` ties the output to that class.
- **L84** Loads the incidents. `results` starts out holding any "not found" errors.
- **L85** `with ThreadPoolExecutor(max_workers=...) as executor:` is a **context manager**. The `with` block guarantees the pool shuts down cleanly (waiting for all threads) when the block exits, even after an error.
- **L86** `executor.submit(worker, incident)` schedules `worker(incident)` on a thread and returns a **Future**, a placeholder for its eventual result. The list comprehension submits them all up front.
- **L87–88** `as_completed(futures)` yields futures in **finish order**, not submission order. `future.result()` gets the return value (it would re-raise if the worker had raised, but the workers catch everything themselves). So the final `results` order is: missing IDs first, then incidents in the order they finished.


#### Lines 90–97

````python
90      return response_model(
91          total=len(results),
92          success=sum(item.status == "success" for item in results),
93          skipped=sum(item.status == "skipped" for item in results),
94          errors=sum(item.status == "error" for item in results),
95          results=results,
96      )
97
````

`execute_batch`, part 2: building the summary.
- **L90–96** Builds the response model instance.
  - `total` is the count of all results.
  - `success`, `skipped` and `errors` use `sum(item.status == "success" for item in results)`. Each comparison is `True` or `False`, and Python treats those as 1 and 0 when summing, so this counts the matches.
  - `results` holds the full per-incident list.
- This is why `BatchAssessmentResponse` and the drafting batch response share the same shape: both inherit the counters from `BatchResponse` in `schemas.py`.


---

## 16. `backend/services/intake_service.py`

*134 lines · complete*

This service backs the **incident intake and read screens** of the operator UI. The MIMT team (Major Incident Management Team, the humans who first log and describe an incident) uses it to:
- create or update an incident;
- list and view incidents;
- see the agent's latest assessment and draft;
- mark their input as complete.

Marking input complete is what **starts the automated pipeline**. It sets `mimt_input_completed = TRUE`. A Postgres trigger then sends a NOTIFY. `workflow_listener.py` receives it and calls `workflow_service.handle_event`, which runs the assessment agent and, if the incident is reportable, the drafting agent.

All functions are called from `backend/routers/intake.py`. `get_incident` is also reused by `review_service.py` as a "404 if missing" check.


#### Lines 1–6

````python
1  """Incident intake + read helpers for the operator UI.
2
3  MIMT uses these to create/update an incident and mark their input complete,
4  which (via the DB trigger -> workflow listener) kicks off assessment/drafting.
5  """
6
````

- **L1–5** Module docstring. It says what the file is for: intake plus read helpers. It also describes the chain that kicks off the automation: marking MIMT input complete fires a DB trigger, the workflow listener receives it, and assessment and drafting run.


#### Lines 7–16

````python
 7  from typing import Any, Dict, List, Optional
 8
 9  from fastapi import HTTPException
10
11  from backend.services.common import serialize_db_value
12  from backend.utils.postgres_client import get_client
13  from backend.logger import get_logger
14
15  logger = get_logger(__name__)
16
````

- **L7** Type-hint helpers: `Dict[str, Any]` means a dict with string keys and any values, `List[...]` is a list, and `Optional[X]` means `X` or `None`. They document intent. Python doesn't enforce them.
- **L9** `HTTPException` is raised to make FastAPI return an HTTP error such as 400 or 404 to the browser.
- **L11** `serialize_db_value` (from `services/common.py`) turns `date`/`datetime` values into ISO strings and leaves everything else unchanged.
- **L12** `get_client()` returns the shared, connection-pooled `PostgresClient` singleton.
- **L13–15** Module logger, named `backend.services.intake_service` via `__name__`.


#### Lines 17–22

````python
17  _SUMMARY_COLUMNS = [
18      "incident_number", "incident_title", "priority", "legal_entity",
19      "current_status", "mimt_input_completed",
20  ]
21
22
````

- **L17–20** `_SUMMARY_COLUMNS` is a module-level constant (the underscore marks it as private to this file). It lists only the columns needed for the incident list view. Selecting a few columns instead of `SELECT *` keeps the list response small, since the full incident row has long text fields such as descriptions.


#### Lines 23–34

````python
23  def upsert_incident(payload: Dict[str, Any]) -> Dict[str, Any]:
24      """Create or update an incident from the MIMT intake form. Returns the row."""
25      data = {k: v for k, v in payload.items() if v is not None}
26      incident_number = data.get("incident_number")
27      if not incident_number:
28          raise HTTPException(status_code=400, detail="incident_number is required")
29      client = get_client()
30      client.db_write("incidents", data)
31      logger.info("Upserted incident %s", incident_number)
32      return get_incident(incident_number)
33
34
````

- **L23** Create-or-update ("upsert") an incident from the intake form. It receives a plain dict. The router has already validated it against the `IncidentUpsertRequest` Pydantic model and called `.model_dump(exclude_none=True)`.
- **L25** A **dict comprehension** (`{k: v for k, v in ... if ...}`, a compact way to build a filtered dict). It drops keys whose value is `None`, so fields the user left blank don't overwrite existing values with NULL. A side effect is that this endpoint can never *clear* a field back to NULL.
- **L26–28** `incident_number` is the business key. If it's missing or empty, the function returns HTTP 400 Bad Request.
- **L29–30** `client.db_write("incidents", data)`. For the `incidents` table, the Postgres client first looks for a row with this `incident_number`. If one exists it runs an `UPDATE` of the provided columns; otherwise it runs an `INSERT`.
- **L31** Logs the upsert. The `%s` placeholder is filled from the next argument.
- **L32** Re-reads and returns the full row, so the UI gets what's actually stored, including DB defaults like `created_at`.


#### Lines 35–40

````python
35  def list_incidents() -> List[Dict[str, Any]]:
36      """Return all incidents as lightweight summaries, newest first."""
37      rows = get_client().db_query("incidents", None, _SUMMARY_COLUMNS)
38      return rows
39
40
````

- **L35–38** Returns every incident with only the summary columns. Passing `None` as the filter means no WHERE conditions.
  - Note: the docstring says "newest first", but `db_query` only adds `ORDER BY updated_at DESC` for the two `REVISIONED_TABLES` (`reportability_assessments` and `template_details`). Incidents come back in whatever order Postgres chooses, so any sorting must happen in the UI.


#### Lines 41–48

````python
41  def get_incident(incident_number: str) -> Dict[str, Any]:
42      """Return the full incident row or 404."""
43      rows = get_client().db_query("incidents", {"incident_number": incident_number})
44      if not rows:
45          raise HTTPException(status_code=404, detail=f"Incident {incident_number} was not found")
46      return rows[0]
47
48
````

- **L41–46** Fetches the full incident row by number.
  - If the query returns an empty list, it raises 404 with a readable message. The message is an f-string, which inserts `incident_number` into the text.
  - Otherwise it returns the first (only) row.
  - Many other functions call this just for the 404 check, as you'll see below.


#### Lines 49–54

````python
49  def _latest_by_updated_at(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
50      if not rows:
51          return None
52      return sorted(rows, key=lambda r: r.get("updated_at") or r.get("created_at"), reverse=True)[0]
53
54
````

- **L49** A private helper that picks the newest row from a list.
- **L50–51** An empty list returns `None`.
- **L52** `sorted(..., key=..., reverse=True)` sorts descending using a **lambda** (a small anonymous function). The lambda returns each row's `updated_at`, falling back to `created_at` if `updated_at` is missing. `[0]` takes the newest. This is defensive: `db_query` already orders these tables by `updated_at DESC`, but this helper doesn't rely on that.


#### Lines 55–73

````python
55  def get_latest_assessment(incident_number: str) -> Optional[Dict[str, Any]]:
56      """Return the most recent AGENT-generated (provided_by == 'Assessment
57      Agent') reportability_assessments row for an incident, with an
58      `evaluation` comparison against 2LOD-approved human ground truth when one
59      exists (same comparison logic as evaluate_assessment_prompts.py), or None
60      if the agent has never assessed this incident.
61
62      Used by the UI to show assessment progress that survives navigation —
63      unlike the ephemeral job-polling state, this reflects what's actually
64      persisted, regardless of how it got there (manual trigger, the automatic
65      workflow engine, or a batch run)."""
66      # Imported lazily to avoid the evaluation module's sys.path/dotenv setup
67      # running at backend startup for a module only needed by this one path.
68      from backend.evaluation.evaluate_assessment_prompts import (
69          CRITERIA,
70          compare_assessments,
71          get_latest_human_assessment,
72      )
73
````

- **L55** Returns the most recent assessment produced by the **agent** for an incident, annotated with how it compares to the human answer.
- **L56–65** Docstring:
  - It picks the latest row where `provided_by == 'Assessment Agent'`, across all prompt versions.
  - It attaches an `evaluation` block comparing the agent row against a **2LOD-approved human assessment** (the ground truth), if one exists.
  - The UI uses this so assessment progress survives page navigation. It reflects what's persisted in the DB, unlike the temporary job-polling state in `job_queue.py`.
- **L66–72** A **lazy import** inside the function, explained by the comment. The evaluation module does `sys.path`/`.env` setup when imported, so it's imported only when this endpoint is actually hit, not at server startup. The three names imported are:
  - `CRITERIA`: the 4 impact criteria `data_system_impact`, `operational_impact`, `financial_reputational_impact` and `systemic_risk`.
  - `compare_assessments`: compares the agent's flag and each criterion with the human row.
  - `get_latest_human_assessment`: returns the newest human row with `prompt_version` NULL and `final_approval_by_lod == 'Yes'`.


#### Lines 74–80

````python
74      get_incident(incident_number)  # 404 if missing
75      rows = get_client().db_query("reportability_assessments", {"incident_number": incident_number})
76      agent_rows = [r for r in rows if r.get("provided_by") == "Assessment Agent"]
77      latest = _latest_by_updated_at(agent_rows)
78      if not latest:
79          return None
80
````

- **L74** Calls `get_incident` only so that an unknown incident returns 404 instead of `null`. The return value is ignored.
- **L75** Loads all assessment rows for the incident.
- **L76** List comprehension keeping only agent-written rows. Human rows have a person or team in `provided_by`.
- **L77–79** Picks the newest one. If the agent has never assessed the incident, it returns `None`, which FastAPI sends as JSON `null`.


#### Lines 81–99

````python
81      result = dict(latest)
82      human = get_latest_human_assessment(incident_number)
83      comparison = compare_assessments(latest, human) if human else None
84      result["evaluation"] = {
85          "has_human_ground_truth": bool(human),
86          "flag_match": comparison["flag_match"] if comparison else None,
87          "human_reportability_flag": human.get("reportability_flag") if human else None,
88          "criteria": [
89              {
90                  "criterion": c,
91                  "agent": latest.get(c),
92                  "human": human.get(c) if human else None,
93                  "match": comparison["criteria_matches"][c] if comparison else None,
94              }
95              for c in CRITERIA
96          ],
97      }
98      return result
99
````

- **L81** `dict(latest)` makes a **shallow copy**, so adding the `evaluation` key doesn't modify the row object returned from the DB layer.
- **L82** Fetches the approved human ground truth (or `None`).
- **L83** Compares only if a human row exists. This is a conditional expression: `A if cond else B`.
- **L84–97** Builds the `evaluation` sub-object:
  - `has_human_ground_truth` says whether there is a reference to compare against.
  - `flag_match` says whether agent and human agree on the overall Yes/No reportability.
  - `human_reportability_flag` is the human's answer.
  - `criteria` comes from a list comprehension over `CRITERIA` (L88–96). It builds one dict per criterion with the agent's value, the human's value, and whether they match. `compare_assessments` counts a match when both values start with "yes", or both don't, case-insensitively.
  - Every field falls back to `None` when there's no human row, so the UI can show "no ground truth".
- **L98** Returns the agent row plus `evaluation`.


#### Lines 100–114

````python
100
101  def get_latest_draft(incident_number: str) -> Optional[Dict[str, Any]]:
102      """Return the most recent AGENT-authored (drafted_by == 'Drafting Agent')
103      template_details row for an incident, or None if the agent has never
104      drafted this incident. Includes every notification template field, with
105      date/datetime columns (e.g. date_of_occurrence) coerced to ISO strings to
106      match LatestDraftOut."""
107      get_incident(incident_number)  # 404 if missing
108      rows = get_client().db_query("template_details", {"incident_number": incident_number})
109      agent_rows = [r for r in rows if r.get("drafted_by") == "Drafting Agent"]
110      latest = _latest_by_updated_at(agent_rows)
111      if not latest:
112          return None
113      return {key: serialize_db_value(value) for key, value in latest.items()}
114
````

- **L101** Returns the newest **agent-authored** notification draft for an incident.
- **L102–106** The docstring says date columns are converted to ISO strings so the result matches the `LatestDraftOut` response model in `schemas.py`.
- **L107** 404 check.
- **L108** All `template_details` rows for the incident: every stage, and both human and agent rows.
- **L109** Keeps only rows with `drafted_by == "Drafting Agent"`. Human reviewers' saved edits are written with values like `"1LOD (human)"`.
- **L110–112** Takes the newest, or returns `None` if the agent has never drafted this incident. The newest agent row could be at any stage, for example a later "1LOD R&C" re-draft.
- **L113** A dict comprehension that runs every value through `serialize_db_value`, turning `date`/`datetime` into strings and leaving everything else unchanged.


#### Lines 115–122

````python
115
116  def mark_mimt_complete(incident_number: str) -> Dict[str, Any]:
117      """Flag mimt_input_completed = TRUE, which fires the workflow engine.
118
119      The DB trigger emits a 'mimt_completed' event only on the FALSE->TRUE
120      transition, so calling this twice is harmless.
121      """
122      incident = get_incident(incident_number)  # 404 if missing
````

- **L116** The "MIMT is done" button. It is the trigger for the whole automated pipeline.
- **L117–121** Docstring:
  - Setting the flag fires the workflow engine.
  - The DB trigger only emits its event on the FALSE→TRUE change, so calling this twice doesn't run the pipeline twice (it is *idempotent*).
  - Note: the docstring calls the event `'mimt_completed'`, but `workflow_service._HANDLERS` listens for `"mimt_input_completed"`. The trigger SQL (`db_setup.sql`) isn't in `backend/`, so I couldn't confirm which name it emits. Given that the pipeline works, the docstring name is probably just loose wording.
- **L122** Loads the incident, which also gives a 404 if it is missing, and keeps the row so the old status can be logged below.


#### Lines 123–134

````python
123      client = get_client()
124      client.db_write("incidents", {
125          "incident_number": incident_number,
126          "mimt_input_completed": True,
127      })
128      client.audit_log(
129          incident_number, "mimt_input_received", "MIMT",
130          {"previous_status": incident.get("current_status")},
131      )
132      logger.info("MIMT input marked complete for %s", incident_number)
133      return get_incident(incident_number)
134
````

- **L123–127** Updates the incident row to set `mimt_input_completed = True`. The `db_write` client method does an `UPDATE` because the incident exists. This write is what causes Postgres to fire the trigger and `NOTIFY`.
- **L128–131** Writes an audit entry directly through the client's `audit_log` method (not the LangChain tool):
  - action `mimt_input_received`;
  - actor `"MIMT"`;
  - details recording the status before the change, as JSON.
- **L132** Logs the event.
- **L133** Returns the refreshed incident row. Its `current_status` may not have changed yet, because the workflow engine runs asynchronously in the listener's worker threads.


---

## 17. `backend/services/assessment_service.py`

*423 lines · complete*

This is the **heart of the assessment step**. It decides, for one incident or a batch, whether the incident is reportable to a regulator, and it saves the answer to the `reportability_assessments` table. It's called by the HTTP routes in `backend/routers/assessment.py` (`/api/assess`, `/api/batch/assess`, `/api/evaluate/assessment`), by the event-driven `workflow_service.py`, and by the CLI through `run_batch_assessment`.

The flow for one incident:
1. A **rule-based short-circuit**: a known non-US/non-Global location is an automatic "No", with no LLM call.
2. **Compute the revision number** deterministically (has new 1LOD/2LOD feedback arrived?).
3. **Gather context** with plain Python lookups: the human MIMT draft, CUSO app metadata, regulatory requirements, LOD feedback, and similar incidents.
4. Make **one LLM call** (`evaluate_reportability`).
5. **Save, verify and audit-log** the result.

This file replaced an older design in which a ReAct agent decided for itself which lookups to run. That design was slower and sometimes forgot to save its result.


#### Lines 1–18

````python
 1  """Reportability assessment orchestration (single incident + batch).
 2
 3  Moved out of backend/main.py. Contains:
 4    - The US/Global rule-based short-circuit (per Sep 24 meeting notes)
 5    - Deterministic 1LOD/2LOD feedback-loop revision binding (see
 6      backend.tools.assessment_tools.compute_assessment_revision)
 7    - Deterministic context gathering (MIMT Draft, CUSO/regulatory reference
 8      lookups, LOD feedback, similar incidents) feeding a single
 9      evaluate_reportability LLM judgment call — previously a ReAct agent
10      decided whether/when to make each of these as a separate tool call,
11      which (a) cost ~5 extra sequential LLM round-trips per incident for
12      decisions that never actually required judgment, and (b) occasionally
13      let the agent stop early without ever calling evaluate_reportability/
14      db_write, the same reliability bug already fixed in drafting_service.py.
15    - Single-incident and batch assessment orchestration
16    - The CLI-compatible run_batch_assessment wrapper used by root main.py
17  """
18
````

The module docstring.
- **L3** The code was moved out of the old monolithic `backend/main.py`.
- **L4** A **US/Global rule-based short-circuit**, per a Sep 24 meeting decision: only US or Global incidents can be reportable to US regulators.
- **L5–6** Deterministic **revision binding** for 1LOD/2LOD feedback loops. The logic lives in `assessment_tools.compute_assessment_revision`.
- **L7–14** The main design change. Context gathering used to be done by a ReAct agent (an LLM loop that chooses which tool to call next). That cost about 5 extra sequential LLM round-trips per incident for decisions that need no judgment. It also sometimes let the agent stop before calling `evaluate_reportability` or `db_write`, so nothing got saved. Now the lookups are plain Python and only the actual judgment is an LLM call.
- **L15–16** The module also contains single and batch orchestration, plus a CLI-compatible wrapper used by the root `main.py`.


#### Lines 19–28

````python
19  from typing import Any, Dict, List, Literal, Optional
20
21  from fastapi import HTTPException
22
23  from backend.schemas import (
24      AssessmentEvaluationResponse,
25      AssessmentResponse,
26      BatchAssessmentResponse,
27      BatchIncidentResult,
28  )
````

Imports, part 1.
- **L19** Type hints from `typing`. `Dict[str, Any]` means "a dictionary with string keys and any values". `List`, `Optional[X]` (an X or `None`) and `Literal[...]` (only these exact values) work the same way. Type hints don't change how the code runs. They document intent and let editors and type checkers catch mistakes.
- **L21** `HTTPException` is FastAPI's exception that becomes an HTTP error response (for example a 404) when raised.
- **L23–28** Pydantic response models from `backend/schemas.py`. A **Pydantic model** is a class whose fields are declared with types. Pydantic validates data against those types and can turn it into JSON, which is how FastAPI produces typed API responses.
  - `AssessmentResponse` is the result for one incident.
  - `BatchAssessmentResponse` holds the totals plus per-incident results.
  - `BatchIncidentResult` is one incident's success, skipped or error outcome.
  - `AssessmentEvaluationResponse` is the summary of agent-vs-human accuracy.


#### Lines 29–44

````python
29  from backend.tools.audit_tools import audit_log
30  from backend.tools.data_tools import (
31      ASSESSMENT_PROMPT_VERSION_LABEL,
32      db_write,
33      set_current_assessment_revision,
34      reset_current_assessment_revision,
35  )
36  from backend.tools.assessment_tools import compute_assessment_revision, evaluate_reportability
37  from backend.tools.reference_data_tools import get_regulatory_requirements, lookup_cuso_applications
38  from backend.services.common import execute_batch, load_evaluator, safe_batch_error
39  from backend.services.eval_cache import load_latest_evaluation, save_latest_evaluation
40  from backend.utils.postgres_client import get_client
41  from backend.logger import get_logger
42
43  logger = get_logger(__name__)
44
````

Imports, part 2, and the logger.
- **L29** `audit_log` is a LangChain tool that writes to `audit_logs`.
- **L30–35** From `data_tools.py`:
  - `ASSESSMENT_PROMPT_VERSION_LABEL` is the short label of the active assessment prompt, e.g. `"v10"`. It's derived from `prompt_config.py`.
  - `db_write` is the tool version of a DB write. It auto-injects `prompt_version` and `revision`, and validates the required fields.
  - `set_current_assessment_revision` and `reset_current_assessment_revision` bind and unbind a **contextvar** (explained at L264).
- **L36** `compute_assessment_revision` decides the revision number. `evaluate_reportability` is the single LLM judgment call.
- **L37** Reference-data tools that read a spreadsheet: the full regulatory requirements sheet and a fuzzy lookup of CUSO applications.
- **L38** Shared helpers from `common.py`: the parallel batch runner, a lazy module importer, and a client-safe error formatter.
- **L39** Save and load for the "latest evaluation" JSON cache, used by the Admin page.
- **L40** `get_client()` returns the singleton `PostgresClient` (one shared connection-pool object for the whole app).
- **L41–43** Create a logger named after this module (`__name__` is `"backend.services.assessment_service"`), so log lines show where they came from.


#### Lines 45–58

````python
45
46  def _assessment_response(incident_number: str, assessment_data: Dict[str, Any]) -> AssessmentResponse:
47      """Convert a persisted assessment row into its API response model."""
48      return AssessmentResponse(
49          incident_number=incident_number,
50          reportability_flag=assessment_data.get("reportability_flag", "Unknown"),
51          data_system_impact=assessment_data.get("data_system_impact") or "",
52          operational_impact=assessment_data.get("operational_impact") or "",
53          financial_reputational_impact=assessment_data.get("financial_reputational_impact") or "",
54          systemic_risk=assessment_data.get("systemic_risk") or "",
55          rationale=assessment_data.get("rationale") or assessment_data.get("notes") or "",
56          assessment_id=str(assessment_data.get("id") or ""),
57      )
58
````

`_assessment_response`: turns a DB row into the API response model. The leading underscore is a Python convention meaning "private to this module".
- **L46** Takes the incident number and a dict representing a `reportability_assessments` row, and returns an `AssessmentResponse` (the `-> X` part is the return type hint).
- **L50** `.get("reportability_flag", "Unknown")` returns the value, or `"Unknown"` if the key is missing.
- **L51–54** For each criterion text, `x.get(...) or ""` turns both a missing key and a `None` value into an empty string, because the response model requires `str`.
- **L55** The rationale tries `"rationale"` first, then falls back to `"notes"`. The persisted rows store the LLM's rationale in the **`notes`** column (see L224), so in practice `notes` is what's returned.
- **L56** The DB `id` is converted to a string. `or ""` covers a missing id.


#### Lines 59–66

````python
59
60  # Placeholder values the source data uses for "location wasn't captured",
61  # as opposed to an actual known location that happens to be neither US nor
62  # Global (e.g. "CANADA", "UNITED KINGDOM"). These must NOT be treated as a
63  # known non-US/Global location — if we don't actually know the location, the
64  # agent needs to make the call, not a rule.
65  _UNKNOWN_LOCATION_VALUES = {"no data found", "unknown", "n/a", "na", "none", "tbd", "tbc"}
66
````

- **L60–64** The comment explains the idea. The source data uses placeholder strings to mean "location wasn't captured". These must not be confused with a *real* non-US location such as "CANADA". If the location is unknown, the LLM should decide rather than a rule.
- **L65** `_UNKNOWN_LOCATION_VALUES` is a **set** literal (curly braces with no colons) of the lowercase placeholder values. A set gives fast `in` checks. The leading underscore marks it as module-private.


#### Lines 67–86

````python
67
68  def _location_reportability_scope(location: Optional[str]) -> Literal["us_or_global", "known_other", "unknown"]:
69      """Classify the incident's location for the rule-based reportability
70      short-circuit.
71
72      Per Sep 24 meeting notes: only incidents affecting the US or with Global
73      scope are ever reportable — a KNOWN non-US/Global location (e.g.
74      Canada-only, UK-only) is an automatic rule-based "No", skipping the
75      assessment agent entirely. The Location column can contain multiple
76      comma-separated values (e.g. "CANADA, UNITED STATES"), so this checks
77      for a substring match rather than an exact one.
78
79      Critically, a MISSING or placeholder location (None, blank, or values
80      like "no data found") is NOT the same as a known non-US/Global location —
81      we simply don't know the scope, so it must go to the agent to decide
82      rather than being auto-rejected. Returns:
83        - "us_or_global": location explicitly includes US and/or Global.
84        - "known_other": location is populated and does NOT include US/Global.
85        - "unknown": location is missing/blank/a known "no data" placeholder.
86      """
````

The signature and docstring of `_location_reportability_scope`.
- **L68** It takes an `Optional[str]` (a string or `None`). The return type `Literal["us_or_global", "known_other", "unknown"]` says it only ever returns one of those three strings, which works like a lightweight enum.
- **L72–77** The business rule: only US or Global incidents can be reportable, so a *known* non-US/Global location is an automatic "No" that skips the LLM. The `location` column can hold comma-separated lists (e.g. "CANADA, UNITED STATES"), so the code uses **substring** matching.
- **L79–85** A missing or placeholder location is **not** the same as known-other. It returns `"unknown"` and goes to the full evaluation. The three return values are documented here.


#### Lines 87–96

````python
87      if not location or not location.strip():
88          return "unknown"
89      normalized = location.strip().upper()
90      if normalized in {v.upper() for v in _UNKNOWN_LOCATION_VALUES}:⟦?⟧
91          return "unknown"
92      if "UNITED STATES" in normalized or "GLOBAL" in normalized:
93          return "us_or_global"
94      return "known_other"
95
96
````

The body of the location classifier.
- **L87–88** `None`, an empty string, or only whitespace (`.strip()` removes leading and trailing spaces) all return `"unknown"`.
- **L89** Normalizes the text: trims it and upper-cases it, so comparisons ignore case.
- **L90–91** If the whole value is one of the placeholders, the result is `"unknown"`. `{v.upper() for v in _UNKNOWN_LOCATION_VALUES}` is a **set comprehension** that builds an upper-cased copy of the set (rebuilt on each call, which is cheap here). This line is marked ⟦?⟧ in the transcription, but the reading is consistent with the logic.
- **L92–93** If "UNITED STATES" or "GLOBAL" appears anywhere in the text, the result is `"us_or_global"`.
- **L94** Otherwise the location is a real, known, non-US place, so the result is `"known_other"`.


#### Lines 97–101

````python
 97  def _rule_based_not_reportable(incident_number: str) -> Dict[str, Any]:
 98      """Write a rule-based 'No' reportability_assessments row (no LLM call)
 99      for an incident whose location is neither US nor Global."""
100      client = get_client()
101      reason = "No - Rule-based: incident location is not United States or Global, so it is out of scope for US regulatory reportabili⟦CUT⟧
````

`_rule_based_not_reportable`, part 1. It writes an automatic "No" with no LLM.
- **L97–99** The signature and docstring. It returns the saved row as a dict.
- **L100** Gets the shared Postgres client.
- **L101** `reason` is the explanation string reused for all 4 criteria. The line is ⟦CUT⟧ at the screen edge. It almost certainly ends "...reportability." plus the closing quote (inference). It starts with "No -" on purpose: other code, such as the evaluation's Yes/No parsing, reads the leading Yes or No.


#### Lines 102–120

````python
102      payload = {
103          "incident_number": incident_number,
104          "reportability_flag": "No",
105          "data_system_impact": reason,
106          "operational_impact": reason,
107          "financial_reputational_impact": reason,
108          "systemic_risk": reason,
109          "provided_by": "Rule-Based (Location Filter)",
110          "notes": "Automatically determined not reportable - incident location is not US or Global.",
111          "prompt_version": ASSESSMENT_PROMPT_VERSION_LABEL,
112          "revision": 0,
113      }
114      client.db_write("reportability_assessments", payload)
115      assessments = client.db_query(
116          "reportability_assessments",
117          {"incident_number": incident_number, "prompt_version": ASSESSMENT_PROMPT_VERSION_LABEL},
118      )
119      return assessments[0] if assessments else payload
120
````

`_rule_based_not_reportable`, part 2: building, saving and re-reading the row.
- **L102–113** `payload` is a dict whose keys are column names of `reportability_assessments`.
  - `reportability_flag` is `"No"`, and all 4 criteria get `reason`.
  - `provided_by: "Rule-Based (Location Filter)"` shows that a rule made the call, not the agent or a human.
  - `notes` is a short human-readable explanation.
  - `prompt_version` is set explicitly to the active label, so the row counts as "the active version's assessment".
  - `revision: 0` is always used. The rule never takes feedback revisions into account.
- **L114** Calls `client.db_write` on the **raw Postgres client**, not the `db_write` LangChain tool. That's why `prompt_version` and `revision` must be set by hand: the auto-injection lives in the tool wrapper. The client *upserts* on the key (`incident_number`, `prompt_version`, `revision`), meaning it updates the row if one with that key exists and inserts otherwise, so re-running this updates the same row instead of duplicating it.
- **L115–118** Reads the row back, filtered by incident and active prompt version. Results come newest-first (`ORDER BY updated_at DESC` in the client).
- **L119** Returns the freshly saved row (it includes DB-generated fields such as `id`). If the read somehow finds nothing, it falls back to the payload it just wrote. `a if cond else b` is Python's inline conditional expression.
- Note: unlike the LLM path, this path writes **no `audit_log` entry** (although `workflow_service._run_assessment` does log one when it's the caller).


#### Lines 121–136

````python
121
122  def _gather_assessment_context(
123      incident: Dict[str, Any], lod1_response: Optional[str], lod2_r_and_c: Optional[str]
124  ) -> Dict[str, Any]:
125      """Deterministically gather every piece of context evaluate_reportability
126      can use. Previously a ReAct agent decided, one at a time, whether to make
127      each of these as a separate tool call (query MIMT Draft, fuzzy-lookup
128      CUSO Applications, fetch Regulatory Requirements, query existing LOD
129      feedback, query similar incidents) - none of those decisions require LLM
130      judgment, they're plain lookups, so doing them here removes ~5 sequential
131      LLM round-trips per incident. (They also silently went unused before
132      evaluate_reportability was extended to accept them - see assessment_tools.py.)
133      """
134      client = get_client()
135      incident_number = incident["incident_number"]
136
````

The signature and docstring of `_gather_assessment_context`.
- **L122–124** It takes the incident row plus any **unincorporated** 1LOD and 2LOD feedback text (`None` if there's none). It returns a dict of context pieces whose keys match `evaluate_reportability`'s optional parameters exactly.
- **L125–132** Explains the redesign. These five lookups (the MIMT Draft, a fuzzy CUSO app lookup, regulatory requirements, LOD feedback, and similar incidents) need no LLM judgment, so doing them in Python saves about 5 LLM round-trips per incident. The comment also says the lookups' results "silently went unused" until `evaluate_reportability` was extended to accept them.
- **L134–135** Gets the DB client and pulls out `incident_number`.


#### Lines 137–143

````python
137      # Human MIMT Draft (never the agent's own stage - see drafting_tools.py
138      # and meeting_notes_sep_22.txt: MIMT Draft rows are human-authored and
139      # should feed the assessment agent as context, same as the drafting agent).
140      mimt_rows = client.db_query("template_details", {"incident_number": incident_number, "template_stage": "MIMT Draft"})
141      mimt_rows = [r for r in mimt_rows if (r.get("drafted_by") or "") != "Drafting Agent"]
142      mimt_draft_context = mimt_rows[0] if mimt_rows else None
143
````

**Context 1: the human MIMT draft.** MIMT is the team that writes the first human version of the notification template.
- **L137–139** Explains that only **human-authored** "MIMT Draft" rows are used as context, never the agent's own output. The comment cites meeting notes from Sep 22.
- **L140** Queries `template_details` for this incident with stage "MIMT Draft", newest first.
- **L141** A **list comprehension** (`[x for x in items if condition]`) that keeps only rows whose `drafted_by` isn't `"Drafting Agent"`. `(r.get("drafted_by") or "")` guards against `None`.
- **L142** Takes the newest remaining row, or `None` if there are none.


#### Lines 144–153

````python
144      # CUSO Applications fuzzy lookup - a pure in-memory fuzzy string match,
145      # no LLM involved in the lookup itself (see reference_data_tools.py).
146      cuso_context = None
147      impacted_apps = incident.get("impacted_applications")
148      if impacted_apps:
149          try:
150              cuso_context = lookup_cuso_applications.invoke({"query": impacted_apps}) or None
151          except Exception:
152              logger.warning("lookup_cuso_applications failed for %s", incident_number, exc_info=True)
153
````

**Context 2: CUSO application metadata.** CUSO is a reference spreadsheet of about 2,600 bank applications with criticality, data classification, SOX and payments flags, and so on.
- **L146** Defaults to `None`, meaning "no context".
- **L147–148** Only looks something up if the incident lists `impacted_applications`.
- **L150** `lookup_cuso_applications.invoke({"query": impacted_apps})` calls the tool. Functions decorated with LangChain's `@tool` become `StructuredTool` objects, so you call them with `.invoke(dict_of_arguments)` instead of `f(...)`. The tool returns up to 3 fuzzy matches. `or None` turns an empty list into `None`.
- **L151–152** If the lookup fails (for example the spreadsheet is missing), it logs a warning with the full traceback (`exc_info=True`) and carries on. Missing context shouldn't block the assessment.


#### Lines 154–161

````python
154      # Regulatory Requirements - small (~15 rows) fixed reference sheet,
155      # passed through in full so the one real LLM call can match it itself.
156      try:
157          regulatory_context = get_regulatory_requirements.invoke({}) or None
158      except Exception:
159          logger.warning("get_regulatory_requirements failed for %s", incident_number, exc_info=True)
160          regulatory_context = None
161
````

**Context 3: regulatory requirements.**
- **L154–155** This sheet is small (about 15 rows), so the whole thing is passed in and the LLM matches the relevant regulator, region and entity itself.
- **L157** Calls the tool with no arguments (`{}`). An empty result becomes `None`.
- **L158–160** On failure it logs a warning and uses `None`. It's the same "best-effort context" pattern as the CUSO lookup.


#### Lines 162–172

````python
162      # Unincorporated 1LOD/2LOD feedback - already determined deterministically
163      # by compute_assessment_revision before this is called.
164      lod_feedback_context = None
165      if lod1_response or lod2_r_and_c:
166          parts = []
167          if lod1_response:
168              parts.append(f"1LOD feedback: {lod1_response}")
169          if lod2_r_and_c:
170              parts.append(f"2LOD feedback: {lod2_r_and_c}")
171          lod_feedback_context = "\n".join(parts)
172
````

**Context 4: reviewer feedback that hasn't been incorporated yet.**
- **L162–163** Whether feedback is "new" was already decided deterministically by `compute_assessment_revision`. It returns the text only if the feedback timestamp is newer than the latest assessment row.
- **L165–171** If there's any feedback, it builds a list of labelled lines using **f-strings** (`f"1LOD feedback: {lod1_response}"` inserts the variable's value into the string). Then `"\n".join(parts)` glues them into one block with a line break between each.


#### Lines 173–182

````python
173      # Similar past incidents (same legal_entity + priority) with their
174      # active-version agent flags, for decision consistency.
175      similar_incidents_context = None
176      legal_entity = incident.get("legal_entity")
177      priority = incident.get("priority")
178      if legal_entity and priority:
179          similar = client.db_query(
180              "incidents", {"legal_entity": legal_entity, "priority": priority}, ["incident_number"]
181          )
182          similar_numbers = {s["incident_number"] for s in similar if s["incident_number"] != incident_number}
````

**Context 5: similar past incidents**, part 1.
- **L173–174** The idea: show the LLM how the *current prompt version* judged incidents with the same legal entity and priority, for consistency.
- **L176–178** Only runs if both fields are present.
- **L179–181** Gets the `incident_number`s of all incidents with the same `legal_entity` and `priority`. The third argument selects only that one column.
- **L182** A **set comprehension** of those numbers, excluding the current incident itself. A set gives fast `in` lookups at L189.


#### Lines 183–195

````python
183          if similar_numbers:
184              assessed = client.db_query(
185                  "reportability_assessments",
186                  {"provided_by": "Assessment Agent", "prompt_version": ASSESSMENT_PROMPT_VERSION_LABEL},
187                  ["incident_number", "reportability_flag"],
188              )
189              matches = [row for row in assessed if row["incident_number"] in similar_numbers]
190              if matches:
191                  similar_incidents_context = [
192                      {"incident_number": row["incident_number"], "reportability_flag": row.get("reportability_flag")}
193                      for row in matches
194                  ]
195
````

**Context 5: similar past incidents**, part 2.
- **L184–188** Fetches *all* agent assessments for the active prompt version (only `incident_number` and `reportability_flag`). Filtering by `provided_by = "Assessment Agent"` excludes human rows and rule-based rows.
- **L189** Keeps the rows whose incident is in the similar set.
- **L190–194** If there are any, it builds a compact list of `{incident_number, reportability_flag}` dicts. Only the flags are passed, not the full rationale, to keep the prompt small.
- Note: because `reportability_assessments` can hold several **revisions** per incident and version, the same similar incident can appear more than once in this list. Rows come back newest first. This is harmless but adds a little noise to the prompt.


#### Lines 196–204

````python
196      return {
197          "mimt_draft_context": mimt_draft_context,
198          "cuso_context": cuso_context,
199          "regulatory_context": regulatory_context,
200          "lod_feedback_context": lod_feedback_context,
201          "similar_incidents_context": similar_incidents_context,
202      }
203
204
````

- **L196–202** Returns the five context pieces as a dict, each possibly `None`. The keys are named to match `evaluate_reportability`'s keyword parameters (`mimt_draft_context`, `cuso_context`, and so on), so the dict can be spread straight into the tool call at L215.


#### Lines 205–216

````python
205  def _evaluate_and_persist_assessment(incident: Dict[str, Any], context: Dict[str, Any]) -> None:
206      """Make the one real LLM judgment call and persist its result.
207
208      A plain function call, not a decision a ReAct agent gets to make - the
209      agent-driven version of this could (and occasionally did) stop after
210      gathering context without ever calling evaluate_reportability/db_write,
211      the same reliability bug already fixed in drafting_service.py.
212      """
213      assessment = evaluate_reportability.invoke({
214          "incident_data": incident,
215          **context,
216      })
````

`_evaluate_and_persist_assessment`, part 1: the **one real LLM call**.
- **L205** It takes the incident and the gathered context and returns `None` (it works by side effect: writing to the DB).
- **L206–211** The docstring stresses that this is a plain function call. Under the old agent design, the model could stop before calling the evaluation or the write.
- **L213–216** `evaluate_reportability.invoke({...})` runs the tool. `"incident_data": incident` passes the incident row, and `**context` **unpacks** the context dict into the same dict literal. `{"a": 1, **other}` merges `other`'s key/value pairs in. Inside, the tool makes a structured-output LLM call with the active prompt's `SYSTEM_PROMPT`. It returns the four criterion texts and their confidences, and it computes `reportability_flag` and `rationale` itself: "Yes" if any criterion text starts with "yes".


#### Lines 217–229

````python
217      payload = {
218          "incident_number": incident["incident_number"],
219          "reportability_flag": assessment["reportability_flag"],
220          "data_system_impact": assessment["data_system_impact"],
221          "operational_impact": assessment["operational_impact"],
222          "financial_reputational_impact": assessment["financial_reputational_impact"],
223          "systemic_risk": assessment["systemic_risk"],
224          "notes": assessment.get("rationale"),
225          "provided_by": "Assessment Agent",
226      }
227      db_write.invoke({"table": "reportability_assessments", "payload": payload})
228
229
````

`_evaluate_and_persist_assessment`, part 2: saving.
- **L217–226** Builds the row. It copies the flag and the 4 criterion texts, stores the tool's `rationale` in the `notes` column, and sets `provided_by: "Assessment Agent"`. The `confidence_*` fields from the tool aren't included (the table has no columns for them; the `db_write` tool would strip them anyway).
- **L227** Saves through the **`db_write` tool** (not the raw client) on purpose. The tool wrapper auto-injects `prompt_version` (the active label) and `revision` (read from the contextvar bound at L264), and it raises a `ValueError` if a required field is empty.


#### Lines 230–244

````python
230  def run_single_assessment(incident_number: str) -> AssessmentResponse:
231      """Populate deterministic context, make the one real LLM judgment call,
232      and persist the assessment - then verify and return the saved row.
233
234      Incidents with a KNOWN non-US/Global location are automatically flagged
235      not-reportable via a rule (see _location_reportability_scope) - the LLM
236      is never invoked for those, since only US/Global incidents are ever
237      reportable to US regulators. Incidents with a missing/unknown location
238      (e.g. "no data found") are NOT auto-rejected - we don't actually know
239      the scope, so those still go through the full evaluation.
240
241      The revision this run should write to reportability_assessments (see
242      compute_assessment_revision) is computed deterministically and bound via
243      set_current_assessment_revision - nothing decides this at the LLM level.
244      """
````

The signature and docstring of `run_single_assessment`, the **public entry point** for assessing one incident. It's called by `/api/assess` (through a background job), by `workflow_service._run_assessment`, by the batch path below, and by the evaluator when `run_agent=True`.
- **L230** Takes an incident number and returns an `AssessmentResponse`.
- **L231–232** Summary: deterministic context, then one LLM call, then persist, then verify and return the saved row.
- **L234–239** Restates the location rule. Known non-US/Global is rejected by rule with no LLM. Unknown locations still get the full evaluation.
- **L241–243** The revision number is computed in code and bound via `set_current_assessment_revision`. The LLM never decides it.


#### Lines 245–250

````python
245      client = get_client()
246      incidents = client.db_query("incidents", {"incident_number": incident_number})
247      if not incidents:
248          raise HTTPException(status_code=404, detail=f"Incident {incident_number} was not found")
249      incident = incidents[0]
250
````

Loading the incident.
- **L245** Gets the DB client.
- **L246** Fetches the full incident row (all columns, because no column list is given).
- **L247–248** If there's no such incident, it raises `HTTPException(404, ...)`. In a background job, `job_queue` catches this and reports the detail. In the batch path, `safe_batch_error` turns it into a readable message.
- **L249** Takes the single row.


#### Lines 251–262

````python
251      location = incident.get("location")
252      scope = _location_reportability_scope(location)
253      if scope == "known_other":
254          logger.info("Rule-based not-reportable for %s (location=%r)", incident_number, location)
255          assessment_data = _rule_based_not_reportable(incident_number)
256          return _assessment_response(incident_number, assessment_data)
257      if scope == "unknown":
258          logger.info(
259              "Location unknown/missing for %s (location=%r) - deferring to full evaluation instead of rule-based rejection",
260              incident_number, location,
261          )
262
````

The **location short-circuit**.
- **L251–252** Reads `location` and classifies it with `_location_reportability_scope`.
- **L253–256** For `"known_other"`, it logs (`%r` in a log format string shows the value's `repr`, with quotes), writes the rule-based "No" row, and returns immediately. The LLM is never called.
- **L257–261** For `"unknown"`, it only logs an info message saying the full evaluation will decide, then falls through. `"us_or_global"` also falls through silently.
- Using `logger.info("... %s", value)` instead of an f-string is the standard logging style: the string is only formatted if that log level is actually enabled.


#### Lines 263–269

````python
263      next_revision, lod1_response, lod2_r_and_c = compute_assessment_revision(incident_number, ASSESSMENT_PROMPT_VERSION_LABEL)
264      revision_token = set_current_assessment_revision(next_revision)
265      required_fields = ("reportability_flag", "data_system_impact", "operational_impact", "financial_reputational_impact", "systemic_⟦CUT⟧
266      assessment_filter = {"incident_number": incident_number, "prompt_version": ASSESSMENT_PROMPT_VERSION_LABEL, "revision": next_rev⟦CUT⟧
267      try:
268          context = _gather_assessment_context(incident, lod1_response, lod2_r_and_c)
269
````

Revision binding and setup.
- **L263** `compute_assessment_revision` returns a **tuple**, which is unpacked into three variables in one line:
  - the next revision number: 0 if there's no prior row, the same max if nothing changed, or max+1 if new 1LOD/2LOD feedback arrived after the latest assessment;
  - the 1LOD feedback text (or `None`);
  - the 2LOD feedback text (or `None`).
- **L264** `set_current_assessment_revision(next_revision)` sets a **`contextvars.ContextVar`** and returns a *token*. A ContextVar is like a global variable, but each thread or async task gets its own value. That's important because batch mode runs 2 incidents at once in different threads, and each must see its own revision. `db_write` reads it at L227 without it being passed explicitly.
- **L265** The fields that must be non-empty for a saved assessment to count as complete. The line is ⟦CUT⟧. It surely ends `"systemic_risk")` (inference based on the four criteria).
- **L266** The DB filter that finds exactly this run's row: incident, active prompt version, and revision. Also ⟦CUT⟧, almost certainly ending `"revision": next_revision}` (inference).
- **L267** `try:` starts a block whose `finally:` (L300) **always** runs, to un-bind the contextvar even if something fails.
- **L268** Gathers the five context pieces.


#### Lines 270–284

````python
270          # Retry once as a safety net against a transient/incomplete LLM
271          # response or write failure, rather than failing the whole job on
272          # the first hiccup.
273          for attempt in (1, 2):
274              try:
275                  _evaluate_and_persist_assessment(incident, context)
276              except Exception:
277                  if attempt == 2:
278                      raise
279                  logger.warning(
280                      "evaluate_reportability/db_write failed for %s on attempt %s; retrying",
281                      incident_number, attempt,
282                  )
283                  continue
284
````

**The retry loop**, part 1.
- **L270–272** The comment: retry once to ride out a transient LLM or DB hiccup.
- **L273** `for attempt in (1, 2):` runs at most twice.
- **L274–275** Makes the LLM call and saves the result.
- **L276–278** On any exception, if this was attempt 2, `raise` (with no argument) re-raises the same exception upward.
- **L279–283** Otherwise it logs a warning and `continue`s to the next attempt (skipping the verification below).


#### Lines 285–293

````python
285              assessments = client.db_query("reportability_assessments", assessment_filter)
286              if assessments and all(assessments[0].get(field) for field in required_fields):
287                  break
288              if attempt < 2:
289                  logger.warning(
290                      "Assessment persisted for %s was incomplete on attempt %s; retrying",
291                      incident_number, attempt,
292                  )
293
````

**The retry loop**, part 2: verifying the write.
- **L285** Reads back the row for exactly this incident, version and revision.
- **L286** `all(assessments[0].get(field) for field in required_fields)` uses a **generator expression** inside `all()`. It's `True` only if every required field is truthy (not `None` and not empty). If the row exists and is complete, `break` leaves the loop and the job is done.
- **L288–292** If the row is incomplete on attempt 1, it logs a warning and the loop naturally moves on to attempt 2. If it's still incomplete after attempt 2, the loop just ends, and the final check at L304 raises.


#### Lines 294–302

````python
294          audit_log.invoke({
295              "incident_number": incident_number,
296              "action": "reportability_assessed",
297              "actor": "Assessment Agent",
298              "details": {"revision": next_revision, "has_lod_feedback": bool(context["lod_feedback_context"])},
299          })
300      finally:
301          reset_current_assessment_revision(revision_token)
302
````

Audit, then cleanup.
- **L294–299** Writes an audit row through the `audit_log` tool: action `"reportability_assessed"`, actor `"Assessment Agent"`, and details recording the revision and whether LOD feedback was part of the input (`bool(...)` converts the text-or-`None` into `True`/`False`). This keeps a regulator-friendly trail of who decided what and when.
- **L300–301** The `finally:` block always runs, whether the `try` succeeded or raised. `reset_current_assessment_revision(revision_token)` restores the contextvar to its previous value, so a stale revision can't leak into a later write on the same thread (thread-pool threads get reused).


#### Lines 303–308

````python
303      assessments = client.db_query("reportability_assessments", assessment_filter)
304      if not assessments or not all(assessments[0].get(field) for field in required_fields):
305          raise RuntimeError(f"Assessment completed but no persisted result was found for {incident_number}")
306      return _assessment_response(incident_number, assessments[0])
307
308
````

The final verification, then the return.
- **L303–305** Reads the row again (outside the `try`, after the contextvar reset). If it's missing or incomplete, it raises `RuntimeError`. This catches the case where both attempts "succeeded" but left an incomplete row.
- **L306** Converts the saved row into the API response. Returning what's actually **in the DB**, rather than what the LLM returned, guarantees the API reflects the persisted truth.


#### Lines 309–316

````python
309  def _process_assessment_incident(
310      incident: Dict[str, Any],
311      force: bool = False,
312      missing_only: bool = False,
313  ) -> BatchIncidentResult:
314      """Process a single incident for assessment."""
315      client = get_client()
316      incident_number = incident["incident_number"]
````

The signature of `_process_assessment_incident`, the per-incident **worker** for batch runs.
- **L309–313** It takes an incident dict (from `common.load_incidents`, which has only a few columns) and two flags with default values:
  - `force=True` re-assesses even if the active version already has a row.
  - `missing_only=True` skips any incident that has *any* agent assessment, from any version.
  
  It returns a `BatchIncidentResult` and never raises.
- **L315–316** Gets the client and the incident number.


#### Lines 317–330

````python
317      try:
318          assessments = client.db_query(
319              "reportability_assessments",
320              {"incident_number": incident_number},
321              ["prompt_version", "reportability_flag", "provided_by"],
322          )
323          has_any_agent_assessment = any(
324              "assessment agent" in (row.get("provided_by") or "").lower()
325              for row in assessments
326          )
327          active_assessment = next(
328              (row for row in assessments if row.get("prompt_version") == ASSESSMENT_PROMPT_VERSION_LABEL),
329              None,
330          )
````

Looking up existing assessments.
- **L317** `try:`. Every failure becomes an "error" result instead of crashing the whole batch.
- **L318–322** Gets **all** assessment rows for the incident (every prompt version, human rows, rule-based rows), with just three columns. It uses the raw client, so no automatic `prompt_version` filter is applied.
- **L323–326** `any(...)` over a generator: is there at least one row whose `provided_by` contains "assessment agent", compared case-insensitively with `.lower()`?
- **L327–330** `next((row for row in ... if ...), None)` returns the first row whose `prompt_version` is the active label, or `None` if there isn't one. It's a compact "find first match" idiom.


#### Lines 331–343

````python
331          if missing_only and has_any_agent_assessment:
332              return BatchIncidentResult(
333                  incident_number=incident_number,
334                  status="skipped",
335                  detail="An agent assessment already exists",
336              )
337          if active_assessment and not force:
338              return BatchIncidentResult(
339                  incident_number=incident_number,
340                  status="skipped",
341                  detail=f"Active assessment already exists ({active_assessment.get('reportability_flag', 'Unknown')})",
342              )
343
````

The skip rules.
- **L331–336** In `missing_only` mode, any existing agent assessment means "skipped".
- **L337–342** Otherwise, if the active version already has a row and `force` is off, it's also "skipped", and the existing flag is shown in the detail. The nested `'...'` quotes inside the f-string's `{...}` are needed because the f-string itself uses `"`.
- Note: the router (`/api/batch/assess`) always passes `missing_only=False`. A rule-based row (`provided_by = "Rule-Based (Location Filter)"`) isn't counted as an "agent" assessment by L323, but it *is* caught by the active-version check at L337 because it carries the active `prompt_version`.


#### Lines 344–358

````python
344          response = run_single_assessment(incident_number)
345          return BatchIncidentResult(
346              incident_number=incident_number,
347              status="success",
348              detail=f"Reportability: {response.reportability_flag}",
349          )
350      except Exception as exc:
351          logger.exception("Error assessing %s", incident_number)
352          return BatchIncidentResult(
353              incident_number=incident_number,
354              status="error",
355              detail=safe_batch_error(exc),
356          )
357
358
````

Running it and reporting.
- **L344** Calls the full single-incident pipeline.
- **L345–349** On success, it returns a "success" result with the flag in the detail.
- **L350–356** `except Exception as exc:` catches any error. `logger.exception` logs it **with the full stack trace**, and the client only gets `safe_batch_error(exc)`. That function returns the detail for `HTTPException`s, and otherwise a generic "see server logs" message, so internal details (SQL, stack traces) don't leak to the browser.


#### Lines 359–371

````python
359  def execute_batch_assessment(
360      force: bool = False,
361      missing_only: bool = False,
362      incident_numbers: Optional[List[str]] = None,
363  ) -> BatchAssessmentResponse:
364      """Run assessments in parallel (BATCH_MAX_WORKERS workers) and return structured per-incident results."""
365      return execute_batch(
366          incident_numbers,
367          lambda incident: _process_assessment_incident(incident, force, missing_only),
368          BatchAssessmentResponse,
369      )
370
371
````

`execute_batch_assessment`: the batch entry point used by `/api/batch/assess` and the CLI wrapper.
- **L359–363** Takes the same flags plus an optional list of incident numbers. `None` means "all incidents".
- **L364** Docstring: runs in parallel with `BATCH_MAX_WORKERS` threads (default 2, from `config.py`).
- **L365–369** Delegates to `common.execute_batch`. That function loads the incidents, runs the worker for each in a thread pool, and totals the results into `BatchAssessmentResponse`. The worker is a **lambda**, a small anonymous one-line function, `lambda incident: ...`. It "closes over" `force` and `missing_only`, adapting the three-argument `_process_assessment_incident` to the one-argument shape `execute_batch` expects.


#### Lines 372–386

````python
372  def execute_assessment_evaluation(
373      incident_numbers: Optional[List[str]] = None,
374      run_agent: bool = False,
375  ) -> AssessmentEvaluationResponse:
376      """Run the Assessment Agent vs. 2LOD-approved human ground truth
377      comparison and return the full aggregate + per-incident summary.
378
379      run_agent=False (default) compares against whatever assessment already
380      exists in the DB for the active prompt version - fast, read-only, no new
381      LLM calls (matches how the drafting evaluation already works). run_agent
382      =True instead re-invokes the assessment agent live for each incident via
383      the deterministic run_single_assessment orchestration used by /api/assess
384      and upserts a fresh row as a side effect - slower and not read-only, so
385      it's opt-in rather than default.
386      """
````

The signature and docstring of `execute_assessment_evaluation`. This is the "how good is the agent?" job behind `/api/evaluate/assessment`.
- **L372–375** Takes optional incident numbers and `run_agent` (default `False`).
- **L376–377** It compares the Assessment Agent's output with the **2LOD-approved human ground truth** (human rows where `final_approval_by_lod = 'Yes'`), and returns aggregate and per-incident results.
- **L379–381** With `run_agent=False` it's read-only and fast: it compares the rows already saved for the active prompt version.
- **L381–385** With `run_agent=True` it re-runs `run_single_assessment` live for each incident, which is slow and writes new rows. That's why it's opt-in.


#### Lines 387–393

````python
387      evaluator = load_evaluator("evaluate_assessment_prompts")
388      results = evaluator.evaluate(incident_numbers=incident_numbers, run_agent=run_agent)
389      summary = evaluator.build_summary(results)
390      save_latest_evaluation("assessment", summary)
391      return AssessmentEvaluationResponse(**summary)
392
393
````

- **L387** `load_evaluator("evaluate_assessment_prompts")` imports `backend/evaluation/evaluate_assessment_prompts.py` **lazily**, at call time and not at app startup, using `importlib`. This keeps startup fast and avoids loading evaluation-only dependencies unless needed.
- **L388** Runs the comparison and gets per-incident results.
- **L389** Builds the JSON-friendly summary: accuracy, per-criterion accuracy, confidence breakdown and confusion matrices.
- **L390** Caches the summary to disk (`eval_cache.save_latest_evaluation`) so the Admin page can show it later without re-running.
- **L391** `AssessmentEvaluationResponse(**summary)` unpacks the dict as keyword arguments into the Pydantic model, which validates it.
- Note: the router runs this job **without** `exclusive_operation("assessment")`. So with `run_agent=True`, live assessments can overlap with a concurrent `/api/assess` or batch run that does hold the lock.


#### Lines 394–403

````python
394  def get_latest_assessment_evaluation() -> Optional[AssessmentEvaluationResponse]:
395      """Return the last saved Assessment Agent evaluation, but only if it was
396      run against the currently active prompt version - otherwise None (the
397      cached result would be comparing a version that's no longer active)."""
398      summary = load_latest_evaluation("assessment")
399      if not summary or summary.get("prompt_version") != ASSESSMENT_PROMPT_VERSION_LABEL:
400          return None
401      return AssessmentEvaluationResponse(**summary)
402
403
````

`get_latest_assessment_evaluation`: serves the cached result for `/api/evaluate/assessment/latest`.
- **L398** Loads the cached JSON (or `None`).
- **L399–400** Returns `None` if there's no cache, or if it was produced for a different prompt version than the one now active.
- **L401** Otherwise it wraps the dict in the response model.
- Note (likely bug): `build_summary` in `evaluate_assessment_prompts.py` stores `"prompt_version": PROMPT_VERSION`, which is the **full module name** (e.g. `"assessment_agent_prompt_v10"`). This line compares it with `ASSESSMENT_PROMPT_VERSION_LABEL`, the **short label** (`"v10"`). They never match, so this function will always return `None` and the Admin page will never show a cached assessment evaluation. The drafting equivalent in `drafting_service.py` checks a different field and doesn't have this problem.


#### Lines 404–412

````python
404  def run_batch_assessment(force: bool = False, missing_only: bool = False, incident_numbers: list = None):
405      """CLI-compatible wrapper around the structured batch assessment workflow."""
406      logger.info("=" * 60)
407      logger.info("Starting Batch Assessment Process")
408      logger.info("=" * 60)
409      client = get_client()
410      if not client.health_check():
411          logger.error("Database connection failed")
412          return False
````

`run_batch_assessment`: a CLI-friendly wrapper. `backend/main.py` re-exports it for the root `main.py` script.
- **L404** Signature. `incident_numbers: list = None` is a slightly loose type hint (strictly it should be `Optional[list]`), but it's harmless.
- **L406–408** Logs a banner. `"=" * 60` repeats the character 60 times.
- **L409–412** Runs a DB `health_check()` (it executes `SELECT 1`). If that fails, it logs an error and returns `False`, giving a clean exit for scripts instead of a stack trace.


#### Lines 413–423

````python
413      try:
414          result = execute_batch_assessment(force, missing_only, incident_numbers)
415          logger.info(
416              "Batch Assessment Complete: total=%s success=%s skipped=%s errors=%s",
417              result.total, result.success, result.skipped, result.errors,
418          )
419          return result.errors == 0
420      except Exception:
421          logger.exception("Batch assessment failed")
422          return False
423
````

- **L413–414** Runs the structured batch.
- **L415–418** Logs the totals.
- **L419** Returns `True` only if there were zero errors. A CLI can turn that into an exit code.
- **L420–422** Any unexpected failure is logged with its traceback, and the function returns `False`.


---

## 18. `backend/services/drafting_service.py`

*413 lines · complete*

This is the **business logic for the Drafting Agent**: it takes an incident that has been judged reportable and produces a filled-in regulatory notification template (a row in the `template_details` table). It handles three jobs: (1) drafting or revising **one** incident (`run_single_draft`), (2) drafting **many** incidents in parallel (`execute_batch_draft` / `run_batch_drafting`), and (3) running the **LLM-as-Judge evaluation** that scores agent drafts against human drafts (`execute_draft_evaluation`). It is called by the HTTP layer in `backend/routers/drafting.py` (through the background job queue), by the workflow engine in `backend/services/workflow_service.py` (when an incident reaches the Drafting state or a reviewer sends feedback), and by `backend/main.py` for the CLI batch. It calls down into the drafting tools (`populate_template_fields`, `generate_template_content`), the DB tools/client, the audit log, and the evaluation module.


#### Lines 1–6

````python
1  """Drafting orchestration (single incident + batch) and the LLM-as-Judge
2  drafting evaluation job.
3
4  Moved out of backend/main.py.
5  """
6
````

- **L1–5** Module docstring (a triple-quoted string at the very top of a file, which Python stores as the module's `__doc__`). It says the file holds drafting orchestration for one incident and for a batch, plus the LLM-as-Judge drafting evaluation job.
- **L4** "Moved out of backend/main.py" tells you about a refactor: this logic used to live in the FastAPI entry file and was split into a *service* module, so routers stay thin and only handle HTTP.


#### Lines 7–17

````python
 7  from typing import Any, Dict, List, Optional
 8
 9  from fastapi import HTTPException
10
11  from backend.schemas import (
12      BatchDraftResponse,
13      BatchIncidentResult,
14      DraftEvaluationResponse,
15      DraftRequest,
16      DraftResponse,
17  )
````

- **L7** Imports **type hints** from `typing`. `Dict[str, Any]` means "a dict whose keys are strings and whose values can be anything"; `List[...]` is a list of something; `Optional[X]` means "either `X` or `None`". Python does not enforce these at runtime. They are documentation for humans and editors, though Pydantic and FastAPI do use them to validate data.
- **L9** `HTTPException` is FastAPI's way to stop processing and send an HTTP error (for example a 404) back to the caller.
- **L11–17** Imports the **Pydantic models** used as inputs and outputs. A Pydantic model is a class that declares typed fields and validates data when you construct it. `DraftRequest` is the input for one draft. `DraftResponse` is the output for one draft. `BatchIncidentResult` is the per-incident outcome in a batch. `BatchDraftResponse` is the batch totals. `DraftEvaluationResponse` is the judge summary. All are defined in `backend/schemas.py`.


#### Lines 18–37

````python
18  from backend.tools.audit_tools import audit_log
19  from backend.tools.data_tools import (
20      ASSESSMENT_PROMPT_VERSION_LABEL,
21      TEMPLATE_PROMPT_VERSION_LABEL,
22      db_write,
23      set_current_assessment_prompt_version,
24      reset_current_assessment_prompt_version,
25  )
26  from backend.tools.drafting_tools import (
27      generate_template_content,
28      get_template_schema,
29      populate_template_fields,
30  )
31  from backend.services.common import execute_batch, load_evaluator, safe_batch_error, to_text
32  from backend.services.eval_cache import load_latest_evaluation, save_latest_evaluation
33  from backend.utils.postgres_client import get_client
34  from backend.logger import get_logger
35
36  logger = get_logger(__name__)
37
````

- **L18** `audit_log` is a **LangChain tool** (a function wrapped with `@tool` so an LLM agent could call it). Here the code calls it directly to write a row to the `audit_logs` table, which is the compliance trail of who did what.
- **L19–25** From `data_tools`:
  - `ASSESSMENT_PROMPT_VERSION_LABEL` and `TEMPLATE_PROMPT_VERSION_LABEL` are short labels such as `"v2"` for the currently active assessment and drafting prompt versions. They are derived from `prompt_config.py`.
  - `db_write` is the tool version of the DB write. It auto-injects `prompt_version` and `assessment_prompt_version` before calling the Postgres client.
  - `set_`/`reset_current_assessment_prompt_version` manage a **context variable** (explained at L125).
- **L26–30** From `drafting_tools`:
  - `populate_template_fields` is a plain (non-LLM) function that copies factual fields from the incident into the template row.
  - `generate_template_content` is a tool that makes the LLM call to write the narrative fields.
  - `get_template_schema` loads `template_schema.json`.
- **L31** Shared helpers from `services/common.py`:
  - `execute_batch` runs a worker function over many incidents in a thread pool.
  - `load_evaluator` lazily imports an evaluation module.
  - `safe_batch_error` turns an exception into a client-safe message.
  - `to_text` turns `None` into `""` and dates into ISO strings.
- **L32** Read and write a small JSON cache of the latest evaluation result on disk (`eval_cache.py`).
- **L33** `get_client()` returns the process-wide singleton `PostgresClient` (a connection-pooled DB wrapper).
- **L34–36** Creates a module-level logger named after this module (`__name__` evaluates to `"backend.services.drafting_service"`), so log lines show where they came from.


#### Lines 38–57

````python
38
39  def _draft_response(incident_number: str, template_data: Dict[str, Any]) -> DraftResponse:
40      """Convert a persisted template row into its API response model."""
41      return DraftResponse(
42          incident_number=incident_number,
43          template_stage=to_text(template_data.get("template_stage")),
44          title=to_text(template_data.get("title")),
45          current_status=to_text(template_data.get("current_status")),
46          date_of_occurrence=to_text(template_data.get("date_of_occurrence")),
47          type=to_text(template_data.get("type")),
48          legal_entity=to_text(template_data.get("legal_entity")),
49          priority_rating=to_text(template_data.get("priority_rating")),
50          description=to_text(template_data.get("description")),
51          impact=to_text(template_data.get("impact")),
52          management_actions=to_text(template_data.get("management_actions")),
53          root_cause=to_text(template_data.get("root_cause")),
54          pir=to_text(template_data.get("pir")),
55          template_id=to_text(template_data.get("id")),
56      )
57
````

- **L39** A private helper. The leading underscore is a convention meaning "internal to this module". It takes an incident number and a DB row (a dict), and the `-> DraftResponse` return hint says it builds the API response model.
- **L40** Docstring: converts a saved `template_details` row into the response shape.
- **L41–56** Constructs `DraftResponse` with keyword arguments. Each field is pulled from the row with `.get(...)`, which returns `None` instead of raising if the key is missing, and is passed through `to_text`. The `DraftResponse` fields are strings, so a NULL DB column becomes `""` and a `date` column such as `date_of_occurrence` becomes an ISO string like `"2026-05-14"`.
- **L55** The DB primary key column `id` is exposed to the API as `template_id`.


#### Lines 58–78

````python
58
59  def _generate_and_persist_draft_content(
60      request: DraftRequest,
61      incident: Dict[str, Any],
62      assessment_data: Dict[str, Any],
63      mimt_draft: Optional[Dict[str, Any]],
64      existing_template: Optional[Dict[str, Any]],
65      schema: Dict[str, Any],
66  ) -> None:
67      """Generate the 6 LLM-authored template fields and write them onto the
68      template_details row populate_template_fields already created.
69
70      This is a plain function call, not a decision a ReAct agent gets to make
71      - a prior version of this workflow let a drafting agent decide for itself
72      whether/when to call generate_template_content and db_write, and it would
73      occasionally gather context (db_query, get_template_schema) and then stop
74      without ever generating or persisting content, leaving only the
75      deterministic skeleton row from populate_template_fields and failing the
76      completeness check below. Making the generate -> write sequence
77      deterministic removes that failure mode entirely.
78      """
````

- **L59–66** Signature of the function that does the actual LLM generation and saving. Parameters:
  - `request` is the original `DraftRequest`.
  - `incident` is the full `incidents` row.
  - `assessment_data` is the reportability assessment used as context.
  - `mimt_draft` is an optional "baseline" draft for consistency.
  - `existing_template` is an optional current draft to revise.
  - `schema` is the template schema JSON.
  - The return is `-> None`: the function only has side effects.
- **L67–68** Says it generates the LLM-authored fields and writes them onto the row that `populate_template_fields` already created.
- **L70–77** The design rationale, and important history. An earlier version let a **ReAct agent** (an LLM loop that decides which tool to call next) choose when to generate and save. Sometimes the agent gathered context and then stopped without saving, leaving a half-empty row. The fix was to make "generate, then write" a fixed sequence of normal Python calls. The LLM is still used, but only to write text, not to decide control flow.


#### Lines 79–102

````python
 79      generated = generate_template_content.invoke({
 80          "incident_data": incident,
 81          "assessment_data": assessment_data,
 82          "template_schema": schema,
 83          "existing_template": existing_template,
 84          "feedback": request.feedback,
 85          "mimt_draft": mimt_draft,
 86      })
 87      payload = {
 88          "incident_number": request.incident_number,
 89          "template_stage": request.template_stage,
 90          "drafted_by": "Drafting Agent",
 91          "title": generated["title"],
 92          "description": generated["description"],
 93          "impact": generated["impact"],
 94          "management_actions": generated["management_actions"],
 95          "root_cause": generated["root_cause"],
 96      }
 97      # current_status/pir are only LLM-authored at the agent's initial stage -
 98      # at any other stage they're already carried forward by populate_template_fields.
 99      if request.template_stage == "Drafting Agent Draft":
100          payload["current_status"] = generated["current_status"]
101      db_write.invoke({"table": "template_details", "payload": payload})
102
````

- **L79–86** Calls the `generate_template_content` tool. A LangChain tool is called with `.invoke({...})`, passing a dict of its named arguments. It sends everything the LLM needs:
  - the incident, the assessment and the schema;
  - the existing template and the reviewer `feedback`, used when this is a revision;
  - the baseline draft (`mimt_draft`).

  It returns a dict with `title`, `current_status`, `description`, `impact`, `management_actions` and `root_cause`. The LLM output is forced into this shape by the `TemplateContent` Pydantic model in `drafting_tools.py`.
- **L87–96** Builds the DB payload. `incident_number` + `template_stage` + `drafted_by="Drafting Agent"` are part of the row's **upsert key**, so this write lands on the same row `populate_template_fields` created. In `postgres_client.REVISIONED_TABLES` the key is incident_number, template_stage, drafted_by, prompt_version and assessment_prompt_version. The generated narrative fields are copied in.
- **L97–100** `current_status` is written only for the agent's initial stage (`"Drafting Agent Draft"`). For later stages (1LOD/2LOD/Reg Relations), `populate_template_fields` already carried `current_status` forward from the initial draft, so the LLM's version is deliberately thrown away to keep one source of truth.
  - Note: the comment mentions `pir` as LLM-authored at the initial stage, but `pir` is never in `generated`. It is always set deterministically by `populate_template_fields`.
- **L101** Writes the row through the `db_write` tool. The tool fills in `prompt_version` (the active drafting label) and `assessment_prompt_version` (from the context variable set in `run_single_draft`). Then the client sees the key matches the skeleton row and **UPDATEs it** instead of inserting a new one.


#### Lines 103–124

````python
103
104  def run_single_draft(request: DraftRequest) -> DraftResponse:
105      """Populate deterministic fields, generate the LLM-authored content, and
106      persist the draft - then verify and return the saved row."""
107      client = get_client()
108      incidents = client.db_query("incidents", {"incident_number": request.incident_number})
109      if not incidents:
110          raise HTTPException(status_code=404, detail=f"Incident {request.incident_number} was not found")
111      incident = incidents[0]
112
113      assessment_version_label = assessment_prompt_version_label(request.assessment_prompt_version)
114      assessment_data = _get_assessment_for_version(request.incident_number, request.assessment_prompt_version) or {}
115
116      template_filter = {
117          "incident_number": request.incident_number,
118          "template_stage": request.template_stage,
119          "drafted_by": "Drafting Agent",
120          "prompt_version": TEMPLATE_PROMPT_VERSION_LABEL,
121          "assessment_prompt_version": assessment_version_label,
122      }
123      required_fields = ("title", "current_status", "description", "impact")
124
````

- **L104** The main entry point for drafting one incident. It returns a `DraftResponse`. Callers are the `/api/draft` endpoint (via the job queue), the workflow engine and the batch worker below.
- **L105–106** Docstring: fill the deterministic fields, generate the LLM fields, save, then re-read and verify.
- **L107–111** Gets the DB client and loads the incident by number. If none is found, it raises `HTTPException(404)`. Inside a background job, `safe_batch_error` turns that into the message "Incident … was not found". `incidents[0]` takes the single matching row.
- **L113** Computes the **storage label** for which assessment drove this draft: `"v2"`, `"human"`, etc. (see `assessment_prompt_version_label` at L220).
- **L114** Loads the matching assessment row. `or {}` means "if `None`, use an empty dict", so the LLM call still gets a dict.
  - Note: `run_single_draft` itself does **not** check that the incident is reportable. If no assessment exists it drafts with empty assessment context. Only the batch path (`_is_draft_eligible`) and the workflow engine enforce "reportable first".
- **L116–122** `template_filter` is the exact key used later to find "this run's" row: incident, stage, drafted by the agent, the active drafting prompt version and the assessment version label.
- **L123** The fields that must be non-empty for a draft to count as complete. It is a tuple, an immutable list written with parentheses.


#### Lines 125–131

````python
125      token = set_current_assessment_prompt_version(assessment_version_label)
126      try:
127          # Deterministic fields and the LLM-generated fields must land on the
128          # same template_details row, so bind the assessment version first and
129          # populate the deterministic fields inside that context.
130          populate_template_fields(request.incident_number, request.template_stage)
131
````

- **L125** Binds the assessment version label in a **context variable** (`contextvars.ContextVar` in `data_tools.py`). A context variable is a global-looking value that is private to the current thread or async task. Two drafts running at once in different threads won't see each other's value. `set(...)` returns a **token** that can later restore the previous value. This is how `db_write` and `populate_template_fields`, deep inside the call stack, learn which assessment version to stamp on the row without passing it through every function.
- **L126** `try:` opens a block whose matching `finally:` (L194) always runs, even on an exception, so the context variable is always reset.
- **L127–129** Explains the ordering. The context variable must be set *before* `populate_template_fields` so the skeleton row gets the same `assessment_prompt_version`, and therefore the same upsert key, as the later LLM write.
- **L130** Writes the deterministic fields directly from the incident record: date of occurrence, type, legal entity, priority, PIR and executive lead. At later stages it also writes `current_status`/`pir`, carried forward from the initial draft. This creates (or updates) the skeleton row.
- **L131** Blank line separating the skeleton-row write from the baseline-draft lookup below. (Confirmed blank. The start of the comment on L132 was partly hidden behind the editor's sticky header, so its first word or two may be missing, hence the ⟦?⟧.)


#### Lines 132–154

````python
132          # baseline draft for narrative consistency (see⟦?⟧
133          # generate_template_content's docstring). The source differs by stage:
134          #   - 'Drafting Agent Draft' (the agent's own initial draft): baseline
135          #     is the human MIMT team's own 'MIMT Draft' row, if one exists -
136          #     a head start so the agent isn't starting from a blank page.
137          #   - Any later stage (1LOD R&C, 2LOD R&C, Reg Relations): baseline is
138          #     the agent's own prior 'Drafting Agent Draft' row, for continuity.
139          if request.template_stage == "Drafting Agent Draft":
140              baseline_rows = client.db_query(
141                  "template_details",
142                  {"incident_number": request.incident_number, "template_stage": "MIMT Draft"},
143              )
144              baseline_rows = [r for r in baseline_rows if (r.get("drafted_by") or "") != "Drafting Agent"]
145          else:
146              baseline_rows = client.db_query(
147                  "template_details",
148                  {"incident_number": request.incident_number, "template_stage": "Drafting Agent Draft", "drafted_by": "Drafting Agent⟦CUT⟧
149              )
150          # Exclude the just-populated deterministic-only skeleton (title is
151          # None until content is written) when the baseline is the agent's own
152          # row for this exact run.
153          mimt_draft = next((row for row in baseline_rows if row.get("title")), None)
154
````

- **L132–138** Comment explaining the **baseline draft** given to the LLM for narrative consistency. (The ⟦?⟧ on L132 marks an uncertain reading of "(see".) The source depends on the stage:
  - For the agent's initial draft, the baseline is the human MIMT team's own "MIMT Draft", if one exists.
  - For later stages, the baseline is the agent's own earlier "Drafting Agent Draft".
- **L139–144** Initial-stage case. It queries rows at stage `"MIMT Draft"`, then a **list comprehension** (`[r for r in rows if condition]`, a compact way to build a filtered list) drops any row drafted by the agent. `(r.get("drafted_by") or "")` guards against `None`. This is defensive: `populate_template_fields` never writes to the MIMT Draft stage.
- **L145–149** Later-stage case. It queries the agent's "Drafting Agent Draft" rows. The end of L148 ran off the screen (⟦CUT⟧). It almost certainly ends `"drafted_by": "Drafting Agent"},`, closing the filter dict. (Inference.) This filter does not pin the prompt or assessment version, so the baseline is simply the agent's most recent initial draft of any version.
- **L150–153** `next((... for row in rows if ...), None)` is a **generator expression** fed to `next()`. It returns the first row that has a non-empty `title`, or `None` if there isn't one. Because `db_query` sorts `template_details` by `updated_at DESC`, that is the most recent *complete* baseline. Skeleton rows (title still `None`) are skipped. The variable is named `mimt_draft` for historical reasons even when it holds the agent's own earlier draft.


#### Lines 155–161

````python
155          existing_template = None
156          if request.feedback:
157              existing_rows = client.db_query("template_details", template_filter)
158              existing_template = existing_rows[0] if existing_rows else None
159
160          schema = get_template_schema.invoke({})
161
````

- **L155–158** Only on a **revision** (`request.feedback` is set) does it load the current row for this exact key and pass it to the LLM as "Existing Template (to be revised)". `existing_rows[0] if existing_rows else None` is a **conditional expression** (Python's inline if/else).
  - Note: `populate_template_fields` (L130) has already upserted this key. On the *first* revision at a stage such as "1LOD R&C", the row found is the fresh skeleton with `title`/`description`/etc. still `None`. The prompt then shows "Title: None …" as the template to revise, and the real previous text reaches the LLM only through the baseline (`mimt_draft`). On later revisions at the same stage, the previous LLM text is still on the row, because the skeleton upsert only overwrites deterministic columns.
- **L160** Loads the template schema JSON (field definitions and compliance guidelines) through the tool's `.invoke({})`. It takes no arguments.


#### Lines 162–187

````python
162          # Retry once as a safety net against a transient/incomplete LLM
163          # response or write failure, rather than failing the whole job on the
164          # first hiccup.
165          for attempt in (1, 2):
166              try:
167                  _generate_and_persist_draft_content(
168                      request, incident, assessment_data, mimt_draft, existing_template, schema,
169                  )
170              except Exception:
171                  if attempt == 2:
172                      raise
173                  logger.warning(
174                      "generate_template_content/db_write failed for %s on attempt %s; retrying",
175                      request.incident_number, attempt,
176                  )
177                  continue
178
179              templates = client.db_query("template_details", template_filter)
180              if templates and all(templates[0].get(field) for field in required_fields):
181                  break
182              if attempt < 2:
183                  logger.warning(
184                      "Draft persisted for %s was incomplete on attempt %s; retrying",
185                      request.incident_number, attempt,
186                  )
187
````

- **L162–164** Comment: one retry as a safety net against a flaky LLM response or DB write.
- **L165** `for attempt in (1, 2):` loops at most twice.
- **L166–177** Inner `try/except`:
  - It calls the generate-and-save function.
  - On any `Exception` during attempt 2, a bare `raise` re-throws the original error, so the job fails with the real cause.
  - On attempt 1, it logs a warning and `continue`s to attempt 2. The logger uses `%s` placeholders with arguments. This is the standard `logging` style: the string is only built if the message is actually emitted.
- **L179–181** When no exception happened, it re-reads the row and checks that every required field is non-empty. `all(...)` returns True only if every item is truthy. If the draft is complete, `break` leaves the loop.
- **L182–186** If incomplete after attempt 1, it logs and loops again. The second call overwrites the same row, because the upsert key is the same. If attempt 2 is also incomplete, the loop just ends, and the final check at L197–199 raises.


#### Lines 188–196

````python
188          audit_log.invoke({
189              "incident_number": request.incident_number,
190              "action": "template_revised" if request.feedback else "template_created",
191              "actor": "Drafting Agent",
192              "details": {"template_stage": request.template_stage, "has_feedback": bool(request.feedback)},
193          })
194      finally:
195          reset_current_assessment_prompt_version(token)
196
````

- **L188–193** Writes an audit record:
  - The action is `"template_revised"` if feedback was given, else `"template_created"`.
  - The actor is "Drafting Agent".
  - Details are the stage and whether feedback was present. `bool(...)` turns the string or `None` into True/False.
  - Note: this runs even if attempt 2 produced an *incomplete* draft. The audit trail can then say a template was created or revised just before L198–199 raises a failure.
- **L194–195** The `finally` block always resets the context variable with the token, so the version label doesn't leak into later work on the same thread. The thread pool reuses threads.


#### Lines 197–202

````python
197      templates = client.db_query("template_details", template_filter)
198      if not templates or not all(templates[0].get(field) for field in required_fields):
199          raise RuntimeError(f"Drafting completed but no complete persisted draft was found for {request.incident_number}")
200      return _draft_response(request.incident_number, templates[0])
201
202
````

- **L197–199** Final verification outside the `try`. It re-queries the row, and if it is missing or any required field is empty, it raises `RuntimeError`. The job queue marks the job "failed", and the workflow engine moves the incident to "Error".
- **L200** Returns the saved row converted to `DraftResponse`, so the API reports what is actually in the DB, not what the LLM returned.


#### Lines 203–219

````python
203  def resolve_assessment_prompt_version(assessment_prompt_version: Optional[str]) -> Optional[str]:
204      """Resolve the assessment_prompt_version request param to the actual
205      prompt_version value used to select a reportability_assessments row.
206
207      - Omitted (None) -> the active ASSESSMENT_PROMPT_VERSION_LABEL (default,
208        preserves prior behavior where drafting always used the active version).
209      - 'human' (case-insensitive) -> None, meaning the human-provided
210        assessment (prompt_version IS NULL in the database).
211      - Any other string (e.g. 'v1') -> that specific agent prompt version label.
212      """
213      if assessment_prompt_version is None:
214          return ASSESSMENT_PROMPT_VERSION_LABEL
215      if assessment_prompt_version.strip().lower() == "human":
216          return None
217      return assessment_prompt_version.strip()
218
219
````

- **L203** Converts the user-facing `assessment_prompt_version` parameter into the value to match against `reportability_assessments.prompt_version`.
- **L204–212** Docstring listing the three cases. The assessment agent's rows carry a version label like `"v2"`. Human-provided assessments have `prompt_version` = NULL.
- **L213–214** Omitted: use the currently active assessment prompt version, which was the behaviour before this option existed.
- **L215–216** `"human"`, compared case-insensitively after `.strip()` removes surrounding whitespace, returns `None`, meaning "match the NULL-version human row".
- **L217** Anything else, such as `"v1"`, is used as-is, trimmed.


#### Lines 220–237

````python
220  def assessment_prompt_version_label(assessment_prompt_version: Optional[str]) -> str:
221      """Storage label for template_details.assessment_prompt_version - unlike
222      resolve_assessment_prompt_version (which returns None for the human case so
223      it can be matched against the NULL-valued reportability_assessments row),
224      this returns the literal 'human' string so the drafting context is
225      self-describing in template_details.
226
227      - Omitted (None) -> the active ASSESSMENT_PROMPT_VERSION_LABEL (e.g. 'v2').
228      - 'human' (case-insensitive) -> 'human'.
229      - Any other string (e.g. 'v1') -> that specific version label.
230      """
231      if assessment_prompt_version is None:
232          return ASSESSMENT_PROMPT_VERSION_LABEL
233      if assessment_prompt_version.strip().lower() == "human":
234          return "human"
235      return assessment_prompt_version.strip()
236
237
````

- **L220** A near-twin of the function above, but for **storing** on `template_details.assessment_prompt_version`.
- **L221–230** Docstring: the difference is that `"human"` is stored as the literal string `"human"`, not NULL. A NULL would be ambiguous ("unknown") and would also break the upsert, because NULL never equals NULL in SQL, so every write would insert a new row. Storing `"human"` makes the row self-describing.
- **L231–235** The same three branches, returning `"human"` instead of `None` in the middle case.


#### Lines 238–254

````python
238  def _get_assessment_for_version(
239      incident_number: str, assessment_prompt_version: Optional[str]
240  ) -> Optional[Dict[str, Any]]:
241      """Return the most recent reportability_assessments row for an incident
242      matching the resolved assessment prompt version.
243
244      The human-provided row has prompt_version IS NULL in the database, which
245      SQL '= NULL' never matches - so rows are fetched unfiltered and matched
246      in Python instead. db_query already orders REVISIONED_TABLES rows by
247      updated_at DESC, so the first match is the most recent one.
248      """
249      resolved = resolve_assessment_prompt_version(assessment_prompt_version)
250      rows = get_client().db_query("reportability_assessments", {"incident_number": incident_number})
251      matches = [r for r in rows if r.get("prompt_version") == resolved]
252      return matches[0] if matches else None
253
254
````

- **L238–240** Returns the latest assessment row for an incident and a requested version, or `None`.
- **L241–248** Docstring. It explains that rows are fetched unfiltered and matched in Python because `= NULL` never matches in SQL. It also notes that `db_query` already sorts by `updated_at DESC`, so the first match is the newest.
  - Note: `PostgresClient.db_query` does now translate a `None` filter value into `IS NULL`, so the Python-side filtering is no longer strictly necessary. It is harmless.
- **L249** Resolves the version (`None` = human).
- **L250** Fetches all assessment rows for the incident.
- **L251** List comprehension keeping rows whose `prompt_version` equals the resolved value. `None == None` is True in Python, so the human case works here.
- **L252** First (newest) match or `None`.


#### Lines 255–273

````python
255  def _is_draft_eligible(incident: Dict[str, Any], assessment_prompt_version: Optional[str] = None) -> bool:
256      """Return whether an incident has an affirmative assessment for the given
257      assessment prompt version.
258
259      Drafting eligibility depends solely on reportability: an incident must
260      have been assessed (with the resolved assessment version) and flagged
261      reportability_flag == 'Yes'. Priority is no longer a shortcut - even P1
262      incidents must be assessed as reportable before they're drafted.
263
264      Args:
265          incident: Incident record (must include incident_number).
266          assessment_prompt_version: Raw endpoint param - None/omitted for the
267              active assessment prompt version, 'human' for the human-provided
268              assessment (prompt_version IS NULL), or a specific version label.
269      """
270      assessment = _get_assessment_for_version(incident["incident_number"], assessment_prompt_version)
271      return bool(assessment and assessment.get("reportability_flag") == "Yes")
272
273
````

- **L255** Decides whether an incident is eligible for batch drafting. The default `= None` makes the second parameter optional.
- **L256–269** Docstring: eligibility depends **only** on having an assessment, for the chosen version, with `reportability_flag == 'Yes'`. It explicitly notes a rule change: P1 (top-priority) incidents used to skip assessment and now must be assessed too. The `Args:` section is Google-style docstring formatting.
- **L270–271** Looks up the assessment. `bool(a and b)` returns True only if an assessment exists **and** its flag is `"Yes"`. If `assessment` is `None`, the `and` short-circuits and the result is False.


#### Lines 274–289

````python
274  def _process_draft_incident(
275      incident: Dict[str, Any],
276      force: bool = False,
277      assessment_prompt_version: Optional[str] = None,
278  ) -> BatchIncidentResult:
279      """Process a single incident for drafting."""
280      client = get_client()
281      incident_number = incident["incident_number"]
282      try:
283          if not _is_draft_eligible(incident, assessment_prompt_version):
284              return BatchIncidentResult(
285                  incident_number=incident_number,
286                  status="skipped",
287                  detail="Not eligible: no affirmative (reportability_flag = 'Yes') assessment found for the requested assessment_prom⟦CUT⟧
288              )
289
````

- **L274–278** The per-incident **worker** for a batch. It returns a `BatchIncidentResult` with status `success`/`skipped`/`error` instead of raising, so one bad incident can't kill the whole batch. `force` means "re-draft even if a complete draft exists".
- **L280–281** Gets the client and the incident number from the incident summary dict that `execute_batch` passes in.
- **L282** `try:` wraps everything, so any failure becomes an "error" result (L325).
- **L283–288** Not eligible (no "Yes" assessment for the version): returns a "skipped" result with an explanation. The end of the `detail` string on L287 ran off the screen (⟦CUT⟧). It almost certainly finishes "…assessment_prompt_version" followed by `",`. (Inference.)


#### Lines 290–312

````python
290          existing = client.db_query(
291              "template_details",
292              {
293                  "incident_number": incident_number,
294                  "template_stage": "Drafting Agent Draft",
295                  "drafted_by": "Drafting Agent",
296                  "prompt_version": TEMPLATE_PROMPT_VERSION_LABEL,
297                  "assessment_prompt_version": assessment_prompt_version_label(assessment_prompt_version),
298              },
299              ["id", "title", "current_status", "description", "impact"],
300          )
301          required_fields = ("title", "current_status", "description", "impact")
302          has_complete_draft = bool(
303              existing
304              and all(existing[0].get(field) for field in required_fields)
305          )
306          if has_complete_draft and not force:
307              return BatchIncidentResult(
308                  incident_number=incident_number,
309                  status="skipped",
310                  detail="Active Drafting Agent Draft already exists",
311              )
312
````

- **L290–300** Looks for an existing initial draft under the *exact* current key: stage "Drafting Agent Draft", drafted by the agent, active drafting prompt version, and this assessment version label. The third argument to `db_query` is a column list, so only these 5 columns are selected, which is cheaper than `SELECT *`.
- **L301** The same four required fields as in `run_single_draft`.
- **L302–305** `has_complete_draft` is True if a row exists and all required fields are filled.
- **L306–311** If a complete draft exists and the caller didn't pass `force`, it skips with "already exists". This makes re-running the batch cheap and idempotent: it only fills gaps.


#### Lines 313–333

````python
313          response = run_single_draft(
314              DraftRequest(
315                  incident_number=incident_number,
316                  template_stage="Drafting Agent Draft",
317                  assessment_prompt_version=assessment_prompt_version,
318              ),
319          )
320          return BatchIncidentResult(
321              incident_number=incident_number,
322              status="success",
323              detail=f"Draft saved as {response.template_id}",
324          )
325      except Exception as exc:
326          logger.exception("Error drafting %s", incident_number)
327          return BatchIncidentResult(
328              incident_number=incident_number,
329              status="error",
330              detail=safe_batch_error(exc),
331          )
332
333
````

- **L313–319** Calls `run_single_draft` with a freshly built `DraftRequest` for the initial stage, with no feedback and the requested assessment version.
- **L320–324** Success result. The f-string (`f"...{expr}..."` inserts the value of an expression into the string) shows the saved template's ID.
- **L325–331** `except Exception as exc:` catches any error and binds it to `exc`:
  - `logger.exception` logs the message **plus the full stack trace** to the server logs.
  - The result sent back to the client contains only `safe_batch_error(exc)`, so internal details such as SQL errors don't leak to the client. For an `HTTPException` the message is its `detail`; otherwise it is a generic "see server logs".


#### Lines 334–351

````python
334  def execute_batch_draft(
335      incident_numbers: Optional[List[str]] = None,
336      force: bool = False,
337      assessment_prompt_version: Optional[str] = None,
338  ) -> BatchDraftResponse:
339      """Draft all eligible incidents in parallel (up to 3 concurrent).
340
341      assessment_prompt_version selects which reportability_assessments row
342      determines eligibility (and is used as drafting context) - see
343      resolve_assessment_prompt_version for the accepted values.
344      """
345      return execute_batch(
346          incident_numbers,
347          lambda incident: _process_draft_incident(incident, force, assessment_prompt_version),
348          BatchDraftResponse,
349      )
350
351
````

- **L334–338** Public batch entry point used by `/api/batch/draft`. `incident_numbers` is optional (`None` = all incidents).
- **L339–344** Docstring. Note: it says "up to 3 concurrent", but `execute_batch` uses `BATCH_MAX_WORKERS` from `config.py`, which defaults to **2** (env var `BATCH_MAX_WORKERS`). The docstring is out of date unless the env var is set to 3.
- **L345–349** Delegates to the shared `execute_batch(incident_numbers, worker, response_model)`. That helper loads the incidents, runs the worker on each in a `ThreadPoolExecutor`, and counts success/skipped/errors into a `BatchDraftResponse`. The worker is a **lambda**, a tiny anonymous function. This one takes one `incident` and calls `_process_draft_incident` with `force` and the assessment version "baked in" from the enclosing scope (a *closure*). This adapts the 3-argument function to the 1-argument shape `execute_batch` expects.


#### Lines 352–375

````python
352  def run_batch_drafting(force: bool = False, incident_numbers: list = None):
353      """CLI-compatible wrapper around the structured batch drafting workflow."""
354      logger.info("=" * 60)
355      logger.info("Starting Batch Drafting Process")
356      logger.info("=" * 60)
357      client = get_client()
358      if not client.health_check():
359          logger.error("Database connection failed")
360          return False
361      try:
362          result = execute_batch_draft(
363              incident_numbers=incident_numbers,
364              force=force,
365          )
366          logger.info(
367              "Batch Drafting Complete: total=%s success=%s skipped=%s errors=%s",
368              result.total, result.success, result.skipped, result.errors,
369          )
370          return result.errors == 0
371      except Exception:
372          logger.exception("Batch drafting failed")
373          return False
374
375
````

- **L352** A wrapper for the command-line / `main.py` entry point. It returns `True`/`False` instead of a response model, which suits a script exit code. Note: the type hint `list = None` would be more accurately `Optional[list]`. This is purely cosmetic.
- **L354–356** Logs a banner. `"=" * 60` repeats the character 60 times.
- **L357–360** Checks DB connectivity first with `health_check()`, and returns `False` early if the DB is down.
- **L361–365** Runs the batch with the active assessment version (no `assessment_prompt_version` argument is passed).
- **L366–369** Logs the totals.
- **L370** Success means zero errors. Skipped incidents don't count as failures.
- **L371–373** Any unexpected exception is logged with its traceback, and the function returns `False`.


#### Lines 376–403

````python
376  def execute_draft_evaluation(
377      incident_numbers: Optional[list] = None,
378      template_stage: Optional[str] = None,
379      drafting_versions: Optional[list] = None,
380      assessment_versions: Optional[list] = None,
381  ) -> DraftEvaluationResponse:
382      """Run the drafting judge across selected assessment/drafting combinations."""
383      evaluator = load_evaluator("evaluate_drafting_prompts")
384      results = evaluator.evaluate(
385          incident_numbers=incident_numbers,
386          drafting_versions=drafting_versions,
387          assessment_versions=assessment_versions,
388          stage_filter=template_stage,
389          verbose=False,
390          save_reports=True,
391      )
392      summary = evaluator.build_summary(
393          results,
394          drafting_versions=drafting_versions,
395          assessment_versions=assessment_versions,
396      )
397      # Only cache whole-dataset runs (no incident/stage/version filters) - a
398      # filtered one-off run isn't representative of "the" current evaluation
399      # for the active prompt version.
400      if not any([incident_numbers, template_stage, drafting_versions, assessment_versions]):
401          save_latest_evaluation("drafting", summary)
402      return DraftEvaluationResponse(**summary)
403
````

- **L376–381** Runs the **LLM-as-Judge** drafting evaluation. All filters are optional:
  - which incidents to evaluate;
  - which template stage;
  - which drafting prompt versions;
  - which assessment versions.
- **L383** `load_evaluator("evaluate_drafting_prompts")` uses `importlib.import_module` to import `backend.evaluation.evaluate_drafting_prompts` **only now**. Importing a module at call time instead of at file top is called *lazy import*. It keeps the heavy evaluation code (and its setup side effects) out of app startup.
- **L384–391** Calls the evaluator's `evaluate(...)`. It pairs each agent draft with the human reference draft at the same incident and stage, and asks a judge LLM to rate each field High/Medium/Low. `stage_filter` is the module's own name for the stage parameter. `verbose=False` turns off console printing. `save_reports=True` writes the timestamped .txt/.xlsx reports.
- **L392–396** `build_summary` aggregates the per-draft verdicts into overall, per-field and per-combination distributions.
- **L397–401** Caches the summary to disk only for a full, unfiltered run, because a filtered run isn't representative. `any([...])` is True if at least one filter is set.
- **L402** `DraftEvaluationResponse(**summary)` uses `**` to **unpack a dict into keyword arguments**: each key becomes a named field of the Pydantic model, which validates it.


#### Lines 404–413

````python
404
405  def get_latest_draft_evaluation() -> Optional[DraftEvaluationResponse]:
406      """Return the last saved whole-dataset Drafting Agent evaluation, but
407      only if the currently active drafting prompt version was part of it -
408      otherwise None (the cached result predates the current prompt version)."""
409      summary = load_latest_evaluation("drafting")
410      if not summary or TEMPLATE_PROMPT_VERSION_LABEL not in (summary.get("drafting_prompt_versions") or []):
411          return None
412      return DraftEvaluationResponse(**summary)
413
````

- **L405** Used by `GET /api/evaluate/draft/latest`, so the Admin page can show the previous evaluation without re-running the slow LLM judge.
- **L406–408** Docstring. The cached result is returned only if the *currently active* drafting prompt version was included in it. Otherwise it's stale (it predates a prompt change), and the function returns `None` (JSON `null`).
- **L409** Reads `latest_drafting_evaluation.json` through `eval_cache`.
- **L410–411** If there is no cache, or the active label (for example `"v3"`) isn't in the summary's `drafting_prompt_versions`, it returns `None`. `(... or [])` guards against a missing or `None` list.
- **L412** Rebuilds the response model from the cached dict.


---

## 19. `backend/services/review_service.py`

*232 lines · complete*

This service powers the **human review screens**. There are four reviewer roles:
- **MIMT**, the incident team;
- **1LOD**, first line of defence;
- **2LOD**, second line of defence, which gives final approval;
- **Reg_Relations**, the team that actually submits to the regulator.

Each role "owns" one `template_stage` (`ROLE_TO_STAGE` in `schemas.py`). The functions let a reviewer:
- load the draft to edit;
- see their saved edit history;
- save an edited draft as a new versioned row;
- record an **approve / feedback** decision.

Decisions are written to the incident's `lod1_*` / `lod2_*` columns. A Postgres trigger turns those writes into NOTIFY events. The workflow engine (`workflow_listener.py` → `workflow_service.py`) then moves the incident forward or re-runs the Drafting Agent with the feedback. Every function is called from `backend/routers/review.py`.


#### Lines 1–10

````python
 1  """Review + feedback-loop helpers for the operator UI.
 2
 3  Each reviewer role owns one template_stage (ROLE_TO_STAGE). Reviewers can:
 4    * fetch the current draft to edit (latest human edit, else the agent draft),
 5    * save an edited draft as a new versioned human row (prompt_version NULL ->
 6      every save inserts a new row, preserving full history),
 7    * approve or return feedback, which writes the incident's lod1_/lod2_
 8      columns and lets the DB trigger -> workflow engine re-invoke drafting.
 9  """
10
````

- **L1–9** Module docstring summarising the three capabilities:
  - **Fetch** a draft to edit, preferring the reviewer's latest human edit and otherwise the agent's draft.
  - **Save** an edit as a new row. Human rows have `prompt_version` NULL, so they are never upserted and every save is kept, giving full history.
  - **Approve or send feedback**, which writes the `lod1_`/`lod2_` columns and, through the DB trigger, makes the workflow engine re-invoke drafting.


#### Lines 11–23

````python
11  from datetime import datetime, timezone
12  from typing import Any, Dict, List, Optional
13
14  from fastapi import HTTPException
15
16  from backend.schemas import ROLE_TO_STAGE, TEMPLATE_CONTENT_FIELDS
17  from backend.services.common import to_text
18  from backend.services.intake_service import get_incident
19  from backend.utils.postgres_client import get_client
20  from backend.logger import get_logger
21
22  logger = get_logger(__name__)
23
````

- **L11** `datetime`/`timezone` for UTC timestamps on approvals.
- **L12** Type hints: `Dict`, `List`, `Optional` (= value or `None`) and `Any`.
- **L14** `HTTPException` returns HTTP errors (400 or 404) to the UI.
- **L16**:
  - `ROLE_TO_STAGE` maps `MIMT`→`"MIMT Draft"`, `1LOD`→`"1LOD R&C"`, `2LOD`→`"2LOD R&C"` and `Reg_Relations`→`"Reg Relations"`. "R&C" is presumably "review & challenge"; the expansion isn't defined in these files.
  - `TEMPLATE_CONTENT_FIELDS` is the list of 13 editable template fields (title, current_status, date_of_occurrence, type, legal_entity, priority_rating, description, impact, management_actions, root_cause, pir and the executive lead name and contact).
- **L17** `to_text` turns `None` into `""` and dates into ISO strings.
- **L18** Reuses `get_incident` from `intake_service` as a "404 if the incident doesn't exist" check.
- **L19–22** DB client accessor and module logger.


#### Lines 24–26

````python
24  _HUMAN_DRAFTED_SUFFIX = "(human)"
25
26
````

- **L24** The suffix appended to a role name to build the `drafted_by` value for human edits, for example `"1LOD (human)"`. This keeps human rows clearly separate from `"Drafting Agent"` rows, and `drafted_by` is part of the template table's upsert key.


#### Lines 27–30

````python
27  def roles() -> List[Dict[str, str]]:
28      """Return the reviewer roles and the template stage each one owns."""
29      return [{"role": role, "stage": stage} for role, stage in ROLE_TO_STAGE.items()]
30
````

- **L27–29** Returns the role list for the UI dropdown. A list comprehension over `ROLE_TO_STAGE.items()` (`.items()` yields `(key, value)` pairs) builds `[{"role": "MIMT", "stage": "MIMT Draft"}, ...]`.


#### Lines 31–41

````python
31
32  def _stage_for_role(role: str) -> str:
33      stage = ROLE_TO_STAGE.get(role)
34      if stage is None:
35          raise HTTPException(status_code=400, detail=f"Unknown review role: {role}")
36      return stage
37
38
39  def _drafted_by(role: str) -> str:
40      return f"{role} {_HUMAN_DRAFTED_SUFFIX}"
41
````

- **L32–36** Looks up the stage for a role. `.get(role)` returns `None` for an unknown role, and the function then raises HTTP 400 Bad Request with a message. Every role-based function below starts with this, so bad roles are rejected early.
- **L39–40** Builds the `drafted_by` string with an f-string, e.g. `"2LOD (human)"`.


#### Lines 42–58

````python
42
43  def _rows_for_stage(incident_number: str, stage: str) -> List[Dict[str, Any]]:
44      """All template_details rows for an incident+stage, newest first.
45
46      db_query orders REVISIONED_TABLES rows by updated_at DESC already, but we
47      sort defensively here too (created_at then updated_at)."""
48      rows = get_client().db_query(
49          "template_details",
50          {"incident_number": incident_number, "template_stage": stage},
51      )
52      return sorted(
53          rows,
54          key=lambda r: (r.get("updated_at") or r.get("created_at") or datetime.min),
55          reverse=True,
56      )
57
58
````

- **L43** Returns all template rows for one incident at one stage.
- **L44–47** Docstring. `db_query` already sorts these by `updated_at DESC`, but the function sorts again "defensively", so the code doesn't silently depend on how the DB layer behaves.
- **L48–51** Queries `template_details` filtered by incident and stage. These become SQL `WHERE incident_number = %s AND template_stage = %s`, with the values passed as bound parameters. Bound parameters avoid SQL injection.
- **L52–56** `sorted(...)` with a **lambda** key: use `updated_at`, else `created_at`, else `datetime.min` (the earliest possible datetime) so rows with no timestamp sort last. `reverse=True` puts the newest first.


#### Lines 59–71

````python
59  def _to_draft_out(incident_number: str, role: str, stage: str, source: str, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
60      row = row or {}
61      out = {
62          "incident_number": incident_number,
63          "role": role,
64          "template_stage": stage,
65          "source": source,
66          "template_id": to_text(row.get("id")),
67      }
68      for field in TEMPLATE_CONTENT_FIELDS:
69          out[field] = to_text(row.get(field))
70      return out
71
````

- **L59** Builds the response dict the UI editor expects. The router validates it against the `TemplateDraftOut` model. Its parameters are the incident, the role, the stage, a `source` label (`"human_edit"`, `"agent_draft"` or `"empty"`) and the row, which may be `None`.
- **L60** `row or {}` swaps `None` for an empty dict, so `.get` calls below are safe.
- **L61–67** Identification fields, plus `template_id` (the row's DB `id` as text).
- **L68–69** Copies each of the 13 editable fields from the row, converting `None` to `""`. The UI then always gets strings and never `null`.
- **L70** Returns the dict.


#### Lines 72–82

````python
72
73  def _all_rows(incident_number: str) -> List[Dict[str, Any]]:
74      """Every template_details row for an incident, newest first."""
75      rows = get_client().db_query("template_details", {"incident_number": incident_number})
76      return sorted(
77          rows,
78          key=lambda r: (r.get("updated_at") or r.get("created_at") or datetime.min),
79          reverse=True,
80      )
81
82
````

- **L73–80** Same as `_rows_for_stage`, but across **all stages** for the incident, newest first. It is used to find the "current working draft" whichever stage produced it.


#### Lines 83–96

````python
83  def get_template_for_role(incident_number: str, role: str) -> Dict[str, Any]:
84      """Return the draft to seed the editor for this reviewer.
85
86      The reviewer acts on the CURRENT working draft (the most recent template
87      row for the incident, regardless of which stage produced it) — e.g. 1LOD
88      reviews the agent's 'Drafting Agent Draft', 2LOD reviews the latest
89      '1LOD R&C'. Preference:
90      this role's own latest saved edit -> the latest draft overall -> an empty
91      template pre-filled from the incident record. `template_stage` in the
92      response is always this role's own stage (where a save will be written).
93      """
94      stage = _stage_for_role(role)
95      incident = get_incident(incident_number)  # 404 if missing
96
````

- **L83** Decides which draft to load into the editor for a given reviewer.
- **L84–93** Docstring:
  - The reviewer works on the **current working draft**, meaning the most recent template row at any stage. 1LOD sees the agent's draft, and 2LOD sees the latest 1LOD version.
  - The order of preference is: this role's own saved edit, then the latest draft overall, then a blank template seeded from the incident.
  - `template_stage` in the response is always the role's own stage, because that's where a save will go.
- **L94** Validates the role and gets its stage (400 if the role is unknown).
- **L95** Loads the incident (404 if it is missing). The incident row is kept for the seed case below.


#### Lines 97–108

````python
 97      # 1. This role's own in-progress human edits (resume editing).
 98      own = [r for r in _rows_for_stage(incident_number, stage) if r.get("prompt_version") is None]
 99      if own:
100          return _to_draft_out(incident_number, role, stage, "human_edit", own[0])
101
102      # 2. The current working draft overall (latest row at any stage).
103      latest = _all_rows(incident_number)
104      if latest:
105          row = latest[0]
106          source = "human_edit" if row.get("prompt_version") is None else "agent_draft"
107          return _to_draft_out(incident_number, role, stage, source, row)
108
````

- **L97–100** Case 1, resume editing. Among this stage's rows, it keeps those with `prompt_version is None`, which are human-written. If there are any, it returns the newest one, labelled `"human_edit"`.
- **L102–107** Case 2. It takes the newest row of any stage. `source` is `"human_edit"` if that row is human-written (NULL `prompt_version`), else `"agent_draft"`. Agent rows always carry a version like `"v2"`.


#### Lines 109–119

````python
109      # 3. Nothing drafted yet — seed a mostly-empty template from the incident.
110      seed = {
111          "title": incident.get("incident_title"),
112          "current_status": incident.get("current_status"),
113          "type": incident.get("incident_type"),
114          "legal_entity": incident.get("legal_entity"),
115          "priority_rating": incident.get("priority"),
116          "description": incident.get("incident_description"),
117      }
118      return _to_draft_out(incident_number, role, stage, "empty", seed)
119
````

- **L109–117** Case 3, nothing has been drafted yet. It builds a starter dict by mapping incident columns onto template fields:
  - `incident_title`→`title`;
  - `incident_type`→`type`;
  - `priority`→`priority_rating`;
  - `incident_description`→`description`;
  - and so on.

  The other fields stay empty.
- **L118** Returns it with `source="empty"`. There is no `id`, so `template_id` is `""`.


#### Lines 120–139

````python
120
121  def list_versions(incident_number: str, role: str) -> List[Dict[str, Any]]:
122      """Return the human-edit version history for this role's stage, newest first."""
123      stage = _stage_for_role(role)
124      get_incident(incident_number)  # 404 if missing
125      rows = _rows_for_stage(incident_number, stage)
126      human = [r for r in rows if r.get("prompt_version") is None]
127      return [
128          {
129              "template_id": to_text(r.get("id")),
130              "template_stage": r.get("template_stage"),
131              "drafted_by": r.get("drafted_by"),
132              "prompt_version": r.get("prompt_version"),
133              "created_at": r.get("created_at"),
134              "updated_at": r.get("updated_at"),
135              "title": to_text(r.get("title")),
136          }
137          for r in human
138      ]
139
````

- **L121** The "version history" panel for a role.
- **L123–124** Validates the role and checks the incident exists.
- **L125–126** Gets this stage's rows (newest first) and keeps only human rows (`prompt_version is None`). Each human save inserts a new row, so this list *is* the edit history.
- **L127–138** A list comprehension that turns each row into a light summary: ID, stage, author, version, timestamps and title. The full text isn't included, which keeps the list small.


#### Lines 140–152

````python
140
141  def save_template(payload: Dict[str, Any]) -> Dict[str, Any]:
142      """Persist a reviewer's edited draft as a NEW versioned human row.
143
144      Written with prompt_version=NULL and a role-specific drafted_by, so it
145      never collides with the agent's draft and each save inserts a new row
146      (NULL key never upserts), preserving the full edit history.
147      """
148      role = payload["role"]
149      incident_number = payload["incident_number"]
150      stage = _stage_for_role(role)
151      get_incident(incident_number)  # 404 if missing
152
````

- **L141** Saves a reviewer's edited draft.
- **L142–147** Docstring. The row is written with `prompt_version=NULL` and a role-specific `drafted_by`. The client's `_upsert_keyed` only tries to *update* when `prompt_version` is not NULL, so a NULL-version write **always inserts** a new row. Every save becomes a new version, and the agent's own row is never overwritten.
- **L148–149** Reads the role and incident from the payload. The router produced the payload from the `SaveTemplateRequest` model via `.model_dump()`. Square-bracket access raises `KeyError` if a key is missing, but the model guarantees both are present.
- **L150–151** Validates the role (400) and checks the incident exists (404).


#### Lines 153–163

````python
153      record: Dict[str, Any] = {
154          "incident_number": incident_number,
155          "template_stage": stage,
156          "drafted_by": _drafted_by(role),
157          "prompt_version": None,
158          "assessment_prompt_version": None,
159      }
160      for field in TEMPLATE_CONTENT_FIELDS:
161          if field in payload and payload[field] is not None:
162              record[field] = payload[field]
163
````

- **L153–159** Starts the record:
  - the incident;
  - the role's stage;
  - `drafted_by`, such as `"1LOD (human)"`;
  - `prompt_version` and `assessment_prompt_version` explicitly `None`.

  `record: Dict[str, Any] = {...}` is a variable annotation (a type hint on a variable).
- **L160–162** Copies in only the content fields that were actually provided and not `None`, so a partial save doesn't write NULLs over fields the UI didn't send.


#### Lines 164–175

````python
164      client = get_client()
165      record_id = client.db_write("template_details", record)
166      client.audit_log(
167          incident_number, "template_edited", role,
168          {"template_stage": stage, "template_id": record_id},
169      )
170      logger.info("Saved %s human template edit for %s (id=%s)", role, incident_number, record_id)
171
172      rows = get_client().db_query("template_details", {"incident_number": incident_number, "template_stage": stage})
173      saved = next((r for r in rows if to_text(r.get("id")) == to_text(record_id)), None)
174      return _to_draft_out(incident_number, role, stage, "human_edit", saved)
175
````

- **L164–165** Writes the row. `db_write` sees that `template_details` is a keyed ("revisioned") table, and because `prompt_version` is NULL it INSERTs. It returns the new row's `id` as a string.
- **L166–169** Audit entry with action `template_edited`. The actor is the role name. The details record the stage and new template ID.
- **L170** Logs it.
- **L172–173** Re-reads the stage's rows and uses `next((... generator ...), None)` to find the one whose `id` matches the new ID. A generator expression inside `next()` returns the first match, or `None` if nothing matches. Both sides go through `to_text`, because the DB returns a UUID object while `record_id` is a string.
- **L174** Returns the saved row in the editor format, labelled `"human_edit"`.
- Note: these human edits are stored for history and display, but the automated re-draft triggered by feedback does **not** read them. `drafting_service.run_single_draft` uses the agent's own "Drafting Agent Draft" (or, at the initial stage, the human "MIMT Draft") as its baseline, plus the feedback text. A reviewer's direct edits to the 1LOD/2LOD stage text are therefore not fed back into the next agent revision. The exception is MIMT's saves: they land in "MIMT Draft", which the agent does use as its initial baseline.


#### Lines 176–183

````python
176
177  # ── Review actions: write lod columns -> DB trigger -> workflow engine ────────
178
179  def _update_incident(incident_number: str, fields: Dict[str, Any]) -> Dict[str, Any]:
180      get_incident(incident_number)  # 404 if missing
181      get_client().db_write("incidents", {"incident_number": incident_number, **fields})
182      return get_incident(incident_number)
183
````

- **L177** A section-divider comment. It summarises the flow: writing the `lod` columns fires the DB trigger, which wakes the workflow engine.
- **L179–182** A private helper to update incident columns:
  - It checks the incident exists (404).
  - It writes `{"incident_number": ..., **fields}`. The `**fields` inside a dict literal **merges** another dict's keys in.
  - The client sees that the incident exists and runs an `UPDATE`.
  - It returns the refreshed row.


#### Lines 184–188

````python
184
185  def _now() -> datetime:
186      return datetime.now(timezone.utc)
187
188
````

- **L185–186** A small helper that returns the current UTC time as a timezone-aware `datetime`. Putting it in one function keeps timestamps consistent and makes them easy to stub in tests.


#### Lines 189–195

````python
189  def _record_review_decision(
190      incident_number: str,
191      level: str,
192      status: str,
193      feedback: Optional[str] = None,
194  ) -> Dict[str, Any]:
195      """Audit and persist a 1LOD or 2LOD approval decision."""
````

- **L189–194** A shared implementation for all four approve and feedback actions. `level` is `"1lod"` or `"2lod"`. `status` is `"Approved"` or `"Feedback"`. `feedback` is optional text.
- **L195** Docstring.


#### Lines 196–208

````python
196      actor = level.upper()
197      field_prefix = f"lod{level[0]}"
198      action = f"{level}_{'approved' if status == 'Approved' else 'feedback_received'}"
199      details = {"feedback": feedback} if feedback is not None else {}
200      get_client().audit_log(incident_number, action, actor, details)
201      fields = {
202          f"{field_prefix}_approval_status": status,
203          f"{field_prefix}_approval_timestamp": _now(),
204      }
205      if feedback is not None:
206          fields[f"{field_prefix}_feedback"] = feedback
207      return _update_incident(incident_number, fields)
208
````

- **L196** `actor = level.upper()` turns `"1lod"` into `"1LOD"` for the audit log.
- **L197** `field_prefix = f"lod{level[0]}"` takes the first character (`"1"` or `"2"`) to build the column prefix `lod1`/`lod2`. The DB columns are named `lod1_...`, while the level strings start with the digit.
- **L198** Audit action name: `"1lod_approved"` or `"1lod_feedback_received"`, and the same for 2lod. The inner `'approved' if ... else '...'` is a conditional expression inside the f-string.
- **L199** Includes the feedback text in the audit details only if there is some.
- **L200** Writes the audit row first.
- **L201–206** Builds the column updates: `lod1_approval_status`, `lod1_approval_timestamp` and, if given, `lod1_feedback`. The `f"..."` keys build column names dynamically.
- **L207** Writes them to the incident. **This write is the hand-off to automation.** The Postgres trigger sees `lodN_approval_status` change to Approved or Feedback and emits a `workflow_events` NOTIFY, such as `1lod_approved` or `1lod_feedback`.
  - On *approved*, the workflow engine moves the incident to the next review state.
  - On *feedback*, it moves it back to Drafting, re-runs the Drafting Agent for that stage with the `lodN_feedback` text, then returns it to review.
  - The engine then resets the approval column to NULL (`_clear_flag`), so the next identical decision counts as a new change.


#### Lines 209–224

````python
209
210  def lod1_approve(incident_number: str) -> Dict[str, Any]:
211      return _record_review_decision(incident_number, "1lod", "Approved")
212
213
214  def lod1_feedback(incident_number: str, feedback: str) -> Dict[str, Any]:
215      return _record_review_decision(incident_number, "1lod", "Feedback", feedback)
216
217
218  def lod2_approve(incident_number: str) -> Dict[str, Any]:
219      return _record_review_decision(incident_number, "2lod", "Approved")
220
221
222  def lod2_feedback(incident_number: str, feedback: str) -> Dict[str, Any]:
223      return _record_review_decision(incident_number, "2lod", "Feedback", feedback)
224
````

- **L210–223** Four thin public wrappers called by the router endpoints. Each passes the right level and status, plus the feedback text for the feedback variants. Separate functions keep the router code simple and self-describing.


#### Lines 225–232

````python
225
226  def reg_relations_complete(incident_number: str) -> Dict[str, Any]:
227      get_client().audit_log(incident_number, "reg_relations_completed", "Reg_Relations", {})
228      return _update_incident(incident_number, {
229          "submitted_to_regulators": True,
230          "submission_timestamp": _now(),
231      })
232
````

- **L226** Reg Relations marks the notification as actually submitted to the regulator.
- **L227** Audit entry `reg_relations_completed`, actor `Reg_Relations`, no details.
- **L228–231** Sets `submitted_to_regulators = True` and the submission timestamp on the incident. This column change presumably drives the trigger's `notification_submitted` event (the trigger SQL isn't in `backend/`). That event makes the workflow engine move the incident from Reg_Relations to Submitted to Completed.


---

## 20. `backend/services/workflow_service.py`

*268 lines · complete*

This is the **workflow engine**: the "brain" that moves an incident through its lifecycle (Intake -> Assessment -> Drafting -> 1LOD review -> 2LOD review -> Reg Relations -> Completed). It does not poll; instead Postgres triggers (defined in `db_setup.sql`) fire `NOTIFY workflow_events` messages whenever a human writes a decision (e.g. MIMT input done, 1LOD approved/feedback). `backend/services/workflow_listener.py` hears those notifications and calls `handle_event(payload)` here on a worker thread. `handle_event` looks up the right handler, which checks the incident is in the expected status, updates `incidents.current_status`, and (when needed) runs the assessment agent (`assessment_service.run_single_assessment`) or the drafting agent (`drafting_service.run_single_draft`). Every transition is written to the `audit_logs` table, and any failure parks the incident in the `Error` status instead of crashing the listener.


#### Lines 1–20

````python
 1  """Event-driven workflow engine for the bv2 incident lifecycle.
 2
 3  Consumes the `workflow_events` notifications emitted by the pre-existing DB
 4  triggers (trigger_incident_change / trigger_assessment_complete /
 5  trigger_template_complete in db_setup.sql) and advances an incident through
 6  its lifecycle, invoking the Assessment / Drafting agents as needed:
 7
 8      Intake/Awaiting_MIMT_Input
 9          -- mimt_completed -->  Assessment  (run assessment agent)
10              flag No  -->  Not_Reportable
11              flag Yes -->  Drafting (run drafting agent, 'Drafting Agent Draft') --> 1LOD_Review
12      1LOD_Review
13          -- lod1 Approved -->  2LOD_Review
14          -- lod1 Feedback -->  Drafting (re-draft '1LOD R&C' w/ feedback) --> 1LOD_Review
15      2LOD_Review
16          -- lod2 Approved -->  Reg_Relations
17          -- lod2 Feedback -->  Drafting (re-draft '2LOD R&C' w/ feedback) --> 2LOD_Review
18      Reg_Relations
19          -- reg_relations_completed -->  Submitted --> Completed
20
````

- **L1–34** are one big module docstring (a `"""..."""` string at the top of a file that documents the module; tools like `help()` show it). This first part is the lifecycle diagram.
- **L1** says what the file is: an *event-driven* engine (it reacts to events rather than running on a timer) for the "bv2" incident lifecycle (bv2 is the name of the team's design doc / version of the process).
- **L3–6**: the events come from the `workflow_events` Postgres channel, emitted by three DB triggers (`trigger_incident_change`, `trigger_assessment_complete`, `trigger_template_complete`) that live in `db_setup.sql` (not part of `backend/`). The engine calls the Assessment and Drafting agents when the flow requires it.
- **L8–11**: from `Intake` / `Awaiting_MIMT_Input`, the `mimt_completed` event (the MIMT (major incident management) team finished its input) moves the incident to `Assessment` and runs the assessment agent. If the reportability flag is `No`, it ends at `Not_Reportable`; if `Yes`, it goes to `Drafting`, the drafting agent writes the "Drafting Agent Draft" template, and then it moves to `1LOD_Review`.
- **L12–14**: in `1LOD_Review` (first line of defence review), "Approved" sends it to `2LOD_Review`; "Feedback" sends it back through `Drafting` to make a "1LOD R&C" (review & comment) revision using that feedback, then back to `1LOD_Review`.
- **L15–17**: the same pattern for 2LOD (second line of defence): Approved goes to `Reg_Relations`, Feedback re-drafts the "2LOD R&C" stage and returns to `2LOD_Review`.
- **L18–19**: once Regulatory Relations marks the notification submitted, the incident goes `Submitted` then `Completed`.


#### Lines 21–34

````python
21  Design choices:
22  - Agent completions are handled INLINE here (the worker that invoked the
23    agent advances status directly) rather than via further DB notifications,
24    so the engine never reacts to its own / the agents' writes.
25  - Every transition is gated on the expected `current_status`, so a duplicate
26    or out-of-order notification is a harmless no-op (idempotent).
27  - Any agent failure moves the incident to 'Error' and is logged to the audit
28    trail; it never propagates out of handle_event (so the listener survives).
29  - Deviation from the original bv2 doc: assessment runs for ALL priorities
30    (no P1 "skip assessment" shortcut), matching the current drafting-eligibility
31    rule that an incident must be assessed reportable before it is drafted, and
32    2LOD feedback re-drafts the '2LOD R&C' stage directly rather than routing
33    back through 1LOD (the schema has no 1LOD-mediated-2LOD-feedback columns yet).
34  """
````

- **L21–24**: design choice 1. When an agent finishes, the *same worker thread* that called the agent moves the status forward directly. The engine does not wait for a "draft finished" DB notification. This avoids feedback loops where the engine reacts to its own writes.
- **L25–26**: design choice 2. Each handler only acts if `current_status` is what it expects. A duplicate or late notification therefore does nothing; this property is called *idempotency* (doing it twice has the same effect as doing it once).
- **L27–28**: design choice 3. Any agent failure sets the incident to `Error` and writes an audit entry; exceptions never escape `handle_event`, so the background listener thread keeps running.
- **L29–33**: two deliberate deviations from the original bv2 design doc: (a) every priority goes through assessment (no "P1 skips assessment" shortcut), because drafting is only allowed for incidents assessed as reportable; (b) 2LOD feedback re-drafts the "2LOD R&C" stage directly instead of going back through 1LOD, because the DB schema doesn't have the columns for a "1LOD relays 2LOD's feedback" step yet.
- **L34** closes the docstring.


#### Lines 35–47

````python
35
36  from typing import Any, Dict, Optional
37
38  from backend.services import operation_runtime
39  from backend.services.assessment_service import run_single_assessment
40  from backend.services.drafting_service import run_single_draft
41  from backend.schemas import DraftRequest
42  from backend.utils.postgres_client import get_client
43  from backend.logger import get_logger
44
45  logger = get_logger(__name__)
46
47  ACTOR = "Workflow Engine"
````

- **L36**: type-hint helpers. `Dict[str, Any]` means "a dict with string keys and values of any type"; `Optional[str]` means "a `str` or `None`". Type hints don't change runtime behaviour; they document intent and help editors/linters.
- **L38**: `operation_runtime` provides `exclusive_operation(...)`, a context manager wrapping a `threading.Lock` per operation ("assessment" or "drafting") so that only one LLM run of each kind happens at a time.
- **L39**: `run_single_assessment(incident_number)` runs the full assessment pipeline (gather context, call the LLM via `evaluate_reportability`, save to `reportability_assessments`) and returns an `AssessmentResponse` Pydantic object.
- **L40**: `run_single_draft(request)` does the same for drafting: it fills and saves a notification template row in `template_details`.
- **L41**: `DraftRequest` is the Pydantic model (a class that validates and types its fields) describing a draft job: incident number, stage, optional feedback, optional assessment version.
- **L42**: `get_client()` returns the shared (singleton) `PostgresClient`, which has `db_query`, `db_write` and `audit_log` helpers.
- **L43–45**: the project's logger factory. `get_logger(__name__)` creates a logger named after this module (`backend.services.workflow_service`), so log lines show where they came from.
- **L47**: `ACTOR = "Workflow Engine"` is the name written into the `actor` column of every audit row this file creates, so auditors can tell automated transitions apart from human actions.


#### Lines 48–61

````python
48
49  # ── Incident lifecycle states ─────────────────────────────────────────────────
50  INTAKE = "Intake"
51  AWAITING_MIMT = "Awaiting_MIMT_Input"
52  ASSESSMENT = "Assessment"
53  NOT_REPORTABLE = "Not_Reportable"
54  DRAFTING = "Drafting"
55  LOD1_REVIEW = "1LOD_Review"
56  LOD2_REVIEW = "2LOD_Review"
57  REG_RELATIONS = "Reg_Relations"
58  SUBMITTED = "Submitted"
59  COMPLETED = "Completed"
60  ERROR = "Error"
61
````

- **L49** is just a visual section divider comment.
- **L50–60** define each lifecycle status as a module-level constant (UPPER_CASE by Python convention). The string values are exactly what is stored in `incidents.current_status`. Using constants instead of retyping strings avoids typos like `"1LOD_review"` silently breaking a comparison.
- Note the Python names can't start with a digit, so `"1LOD_Review"` is stored in `LOD1_REVIEW` and `"2LOD_Review"` in `LOD2_REVIEW`.
- **L60** `ERROR = "Error"` is the status used when a handler fails.


#### Lines 62–77

````python
62  # Valid state transitions (target set per current state). Mirrors the bv2
63  # state machine; used by validate_state_transition to reject illegal jumps.
64  ALLOWED_TRANSITIONS: Dict[str, set] = {
65      INTAKE: {ASSESSMENT, AWAITING_MIMT, ERROR},
66      AWAITING_MIMT: {ASSESSMENT, ERROR},
67      ASSESSMENT: {DRAFTING, NOT_REPORTABLE, ERROR},
68      NOT_REPORTABLE: set(),
69      DRAFTING: {LOD1_REVIEW, LOD2_REVIEW, ERROR},
70      LOD1_REVIEW: {DRAFTING, LOD2_REVIEW, ERROR},
71      LOD2_REVIEW: {DRAFTING, REG_RELATIONS, LOD1_REVIEW, ERROR},
72      REG_RELATIONS: {SUBMITTED, ERROR},
73      SUBMITTED: {COMPLETED, ERROR},
74      COMPLETED: set(),
75      ERROR: {ASSESSMENT, DRAFTING, LOD1_REVIEW, LOD2_REVIEW},
76  }
77
````

- **L62–63**: comment explaining the table below: for each current status, which statuses it may legally move to. It mirrors the bv2 state machine and is used by `validate_state_transition`.
- **L64**: `ALLOWED_TRANSITIONS: Dict[str, set] = {...}` is a dict whose values are Python `set`s (unordered collections with fast `in` checks). The `: Dict[str, set]` part is a variable type annotation.
- **L65–66**: from `Intake` you can go to Assessment, Awaiting MIMT, or Error; from `Awaiting_MIMT_Input` only to Assessment or Error.
- **L67**: Assessment ends in Drafting (reportable) or Not_Reportable (or Error).
- **L68** and **L74**: `Not_Reportable` and `Completed` map to `set()` (an empty set), so they are terminal: nothing can follow them.
- **L69**: Drafting can return to either review queue, because re-drafts happen from both 1LOD and 2LOD feedback.
- **L70–71**: the review states can go to Drafting (feedback) or onward (approval). `2LOD_Review -> 1LOD_Review` is also allowed, although no handler in this file uses it (probably reserved for the future "route 2LOD feedback via 1LOD" flow mentioned in the docstring; this is an inference).
- **L72–73**: Reg_Relations -> Submitted -> Completed.
- **L75**: from `Error` the incident can be retried into Assessment, Drafting or one of the review states.


#### Lines 78–85

````python
78
79  def validate_state_transition(current: Optional[str], target: str) -> bool:
80      """Return True if `current -> target` is a permitted lifecycle transition."""
81      if current == target:
82          return True
83      return target in ALLOWED_TRANSITIONS.get(current or "", set())
84
85
````

- **L79**: `validate_state_transition(current, target) -> bool` checks if a move is legal. `current` is `Optional[str]` because a brand-new incident may have no status yet.
- **L80**: one-line docstring.
- **L81–82**: "moving" to the same status is always allowed (treated as a harmless no-op).
- **L83**: looks up the allowed targets for `current` in the dict. `current or ""` turns `None` into `""`; `.get(key, set())` returns an empty set if the key isn't found, so unknown or empty statuses allow nothing. Then `target in ...` returns True/False.


#### Lines 86–90

````python
86  def _get_incident(incident_number: str) -> Optional[Dict[str, Any]]:
87      rows = get_client().db_query("incidents", {"incident_number": incident_number})
88      return rows[0] if rows else None
89
90
````

- **L86**: `_get_incident` fetches one incident row. The leading underscore is a Python convention meaning "private to this module; don't import it elsewhere".
- **L87**: `db_query("incidents", {"incident_number": ...})` builds and runs a parameterised `SELECT * FROM incidents WHERE incident_number = %s` and returns a list of dicts (one per row).
- **L88**: returns the first row, or `None` if the list is empty. This is a *conditional expression* (`a if cond else b`).


#### Lines 91–97

````python
91  def _audit(incident_number: str, action: str, details: Dict[str, Any]) -> None:
92      try:
93          get_client().audit_log(incident_number, action, ACTOR, details)
94      except Exception:
95          logger.exception("audit_log failed for %s/%s", incident_number, action)
96
97
````

- **L91**: `_audit` writes one row to the audit trail. `-> None` means it returns nothing.
- **L92–93**: calls `PostgresClient.audit_log(incident_number, action, actor, details)`, which does `INSERT INTO audit_logs (...)` with `details` serialized to JSON (stored in a JSONB column).
- **L94–95**: if auditing fails, it only logs the error. `logger.exception(...)` logs at ERROR level *and* includes the stack trace. The `%s` placeholders are filled lazily by the logging module. The point is that a broken audit write should never block the workflow itself.


#### Lines 98–111

````python
 98  def set_status(incident_number: str, target: str, current: Optional[str] = None, **details: Any) -> None:
 99      """Update incidents.current_status (status-only write -> no re-notify) and
100      audit the transition. Rejects invalid transitions defensively."""
101      if current is None:
102          incident = _get_incident(incident_number)
103          current = incident.get("current_status") if incident else None
104      if not validate_state_transition(current, target):
105          logger.warning("Rejected invalid transition %s: %s -> %s", incident_number, current, target)
106          return
107      get_client().db_write("incidents", {"incident_number": incident_number, "current_status": target})
108      logger.info("Status %s: %s -> %s", incident_number, current, target)
109      _audit(incident_number, "status_transitioned", {"from_status": current, "to_status": target, **details})
110
111
````

- **L98**: `set_status(incident_number, target, current=None, **details)` is the single place where status changes happen. `**details` collects any extra keyword arguments into a dict (e.g. `set_status(..., reason="x")` gives `details == {"reason": "x"}`); they're added to the audit entry.
- **L99–100**: the docstring notes it's a "status-only write", which, according to how the DB trigger is written, does **not** fire a new notification, so the engine doesn't re-trigger itself. It also rejects invalid transitions as a safety check.
- **L101–103**: if the caller didn't pass the current status, it reads it from the DB. `incident.get("current_status")` returns `None` if the key is missing instead of raising `KeyError`.
- **L104–106**: if the move isn't allowed by `ALLOWED_TRANSITIONS`, it logs a warning and returns without writing anything.
- **L107**: performs the update. In `PostgresClient.db_write`, for the `incidents` table with an existing `incident_number`, this becomes `UPDATE incidents SET current_status = %s WHERE incident_number = %s`.
- **L108**: info log of the transition.
- **L109**: audits it as `status_transitioned` with `from_status`/`to_status`. `{..., **details}` *unpacks* the extra details dict into the new dict literal.


#### Lines 112–120

````python
112  def _clear_flag(incident_number: str, column: str) -> None:
113      """Set a human-decision column back to NULL after the engine processed it.
114      trigger_incident_change only notifies when an approval column changes INTO
115      'Approved'/'Feedback'/etc., so resetting to NULL lets the next identical
116      decision be a detectable change that re-fires the event, while the reset
117      itself (-> NULL) matches none of the trigger's conditions and emits nothing."""
118      get_client().db_write("incidents", {"incident_number": incident_number, column: None})
119
120
````

- **L112**: `_clear_flag` resets a human-decision column (e.g. `lod1_approval_status`) back to NULL.
- **L113–117**: the docstring explains why. The DB trigger only fires when an approval column changes *into* a value like `'Approved'`/`'Feedback'`. If the column were left at `'Feedback'`, a second round of feedback (same value) wouldn't count as a change and no event would fire. Resetting to NULL "re-arms" it, and the reset itself matches none of the trigger's conditions, so it emits no event.
- **L118**: writes `{column: None}`. Python `None` becomes SQL `NULL`. Note the dict key is the *variable* `column`, so the column name is decided at runtime.


#### Lines 121–130

````python
121  def _run_assessment(incident_number: str) -> str:
122      """Run the assessment under its lock (serializes concurrent LLM calls);
123      return reportability_flag."""
124      with operation_runtime.exclusive_operation("assessment"):
125          result = run_single_assessment(incident_number)
126      flag = getattr(result, "reportability_flag", None) or "No"
127      _audit(incident_number, "reportability_assessed", {"reportability_flag": flag})
128      return flag
129
130
````

- **L121**: `_run_assessment` runs the assessment agent and returns the flag `"Yes"`/`"No"`.
- **L122–123**: the docstring says it runs "under its lock": only one assessment LLM call happens at a time across the whole process.
- **L124**: `with operation_runtime.exclusive_operation("assessment"):` is a *context manager*. The `with` block acquires a `threading.Lock` on entry and always releases it on exit, even if an exception is raised inside. Concurrent workers (the listener has a pool of 2 threads) or an API call running an assessment will wait here.
- **L125**: runs the assessment pipeline; the result is an `AssessmentResponse` Pydantic object.
- **L126**: `getattr(result, "reportability_flag", None)` safely reads the attribute (returns `None` if missing); `or "No"` then defaults any empty/missing value to `"No"`. This is a conservative default: no clear "Yes" means not reportable.
- **L127**: audits `reportability_assessed` with the flag.
- **L128**: returns the flag to the handler.


#### Lines 131–144

````python
131  def _run_draft(incident_number: str, template_stage: str, feedback: Optional[str] = None) -> None:
132      """Draft or revise a template for a given stage/feedback, under the
133      drafting lock (serializes concurrent LLM calls)."""
134      request = DraftRequest(
135          incident_number=incident_number,
136          template_stage=template_stage,
137          feedback=feedback,
138          assessment_prompt_version=None,
139      )
140      with operation_runtime.exclusive_operation("drafting"):
141          run_single_draft(request)
142      _audit(incident_number, "draft_created", {"template_stage": template_stage, "revision": bool(feedback)})
143
144
````

- **L131**: `_run_draft` drafts (or revises) a template for a given `template_stage` (e.g. `"Drafting Agent Draft"`, `"1LOD R&C"`, `"2LOD R&C"`). `feedback` is optional reviewer text.
- **L132–133**: docstring; like assessment, drafting is serialized with its own lock.
- **L134–139**: builds a `DraftRequest` Pydantic object using keyword arguments. Pydantic validates the types at construction. `assessment_prompt_version=None` means "use the currently active assessment prompt version's assessment" (the default in `drafting_service`).
- **L140–141**: takes the `"drafting"` lock and calls `run_single_draft`, which generates the content with the LLM and writes a `template_details` row.
- **L142**: audits `draft_created`. `"revision": bool(feedback)` is `True` when feedback was given (a revision) and `False` for a first draft. `bool("")` and `bool(None)` are both `False`.


#### Lines 145–162

````python
145  # ── Event handlers (each gated on the expected current_status) ────────────────
146
147  def _handle_mimt_completed(incident: Dict[str, Any]) -> None:
148      incident_number = incident["incident_number"]
149      status = incident.get("current_status")
150      if status not in (INTAKE, AWAITING_MIMT):
151          logger.info("Ignoring mimt_completed for %s (status=%s)", incident_number, status)
152          return
153      set_status(incident_number, ASSESSMENT, current=status)
154      flag = _run_assessment(incident_number)
155      if flag == "Yes":
156          set_status(incident_number, DRAFTING, current=ASSESSMENT)
157          _run_draft(incident_number, "Drafting Agent Draft")
158          set_status(incident_number, LOD1_REVIEW, current=DRAFTING)
159      else:
160          set_status(incident_number, NOT_REPORTABLE, current=ASSESSMENT)
161
162
````

- **L145** is a section comment: every handler below checks the expected status first.
- **L147**: `_handle_mimt_completed(incident)` receives the full incident row (a dict).
- **L148–149**: pulls out the incident number (with `[...]`, so it must exist) and the status (with `.get`, which may be `None`).
- **L150–152**: only acts if the incident is in `Intake` or `Awaiting_MIMT_Input`; `not in (A, B)` checks membership in a tuple. Otherwise it logs and ignores the event, which is what makes duplicates harmless.
- **L153**: moves to `Assessment`. Passing `current=status` avoids an extra DB read.
- **L154**: runs the assessment agent (this can take a while; it's an LLM call).
- **L155–158**: if reportable: status -> `Drafting`, run the first draft with stage `"Drafting Agent Draft"`, then status -> `1LOD_Review` so a human 1LOD reviewer sees it.
- **L159–160**: otherwise: status -> `Not_Reportable` (a terminal state).
- If anything here raises (LLM error, DB error), the exception goes up to `handle_event`, which marks the incident `Error`.


#### Lines 163–172

````python
163  def _handle_lod1_approved(incident: Dict[str, Any]) -> None:
164      incident_number = incident["incident_number"]
165      status = incident.get("current_status")
166      if status != LOD1_REVIEW:
167          logger.info("Ignoring 1lod_approved for %s (status=%s)", incident_number, status)
168          return
169      set_status(incident_number, LOD2_REVIEW, current=LOD1_REVIEW)
170      _clear_flag(incident_number, "lod1_approval_status")
171
172
````

- **L163–165**: same setup pattern: incident number and current status.
- **L166–168**: 1LOD approval only makes sense in `1LOD_Review`; otherwise it is ignored.
- **L169**: moves to `2LOD_Review`.
- **L170**: resets `lod1_approval_status` to NULL so the next decision can fire the trigger again (see `_clear_flag`).


#### Lines 173–184

````python
173  def _handle_lod1_feedback(incident: Dict[str, Any]) -> None:
174      incident_number = incident["incident_number"]
175      status = incident.get("current_status")
176      if status != LOD1_REVIEW:
177          logger.info("Ignoring 1lod_feedback for %s (status=%s)", incident_number, status)
178          return
179      set_status(incident_number, DRAFTING, current=LOD1_REVIEW)
180      _run_draft(incident_number, "1LOD R&C", feedback=incident.get("lod1_feedback"))
181      set_status(incident_number, LOD1_REVIEW, current=DRAFTING)
182      _clear_flag(incident_number, "lod1_approval_status")
183
184
````

- **L176–178**: only valid in `1LOD_Review`.
- **L179**: status -> `Drafting` (the UI can show that the agent is re-working the draft).
- **L180**: re-drafts at stage `"1LOD R&C"`, passing the reviewer's text from the `lod1_feedback` column. That column was written by `review_service.lod1_feedback` together with `lod1_approval_status = 'Feedback'`, which is what fired this event.
- **L181**: back to `1LOD_Review` so the reviewer can check the revision.
- **L182**: re-arms the approval column.


#### Lines 185–194

````python
185  def _handle_lod2_approved(incident: Dict[str, Any]) -> None:
186      incident_number = incident["incident_number"]
187      status = incident.get("current_status")
188      if status != LOD2_REVIEW:
189          logger.info("Ignoring 2lod_approved for %s (status=%s)", incident_number, status)
190          return
191      set_status(incident_number, REG_RELATIONS, current=LOD2_REVIEW)
192      _clear_flag(incident_number, "lod2_approval_status")
193
194
````

- **L188–190**: only valid in `2LOD_Review`.
- **L191**: 2LOD's final approval moves the incident to `Reg_Relations` (the Regulatory Relations team that actually submits to the regulator).
- **L192**: clears `lod2_approval_status`.


#### Lines 195–206

````python
195  def _handle_lod2_feedback(incident: Dict[str, Any]) -> None:
196      incident_number = incident["incident_number"]
197      status = incident.get("current_status")
198      if status != LOD2_REVIEW:
199          logger.info("Ignoring 2lod_feedback for %s (status=%s)", incident_number, status)
200          return
201      set_status(incident_number, DRAFTING, current=LOD2_REVIEW)
202      _run_draft(incident_number, "2LOD R&C", feedback=incident.get("lod2_feedback"))
203      set_status(incident_number, LOD2_REVIEW, current=DRAFTING)
204      _clear_flag(incident_number, "lod2_approval_status")
205
206
````

- **L198–200**: only valid in `2LOD_Review`.
- **L201–203**: status -> `Drafting`, re-draft stage `"2LOD R&C"` using the `lod2_feedback` column, then back to `2LOD_Review` (not 1LOD; this is the deviation described in the module docstring).
- **L204**: clears `lod2_approval_status`.


#### Lines 207–216

````python
207  def _handle_reg_submitted(incident: Dict[str, Any]) -> None:
208      incident_number = incident["incident_number"]
209      status = incident.get("current_status")
210      if status != REG_RELATIONS:
211          logger.info("Ignoring notification_submitted for %s (status=%s)", incident_number, status)
212          return
213      set_status(incident_number, SUBMITTED, current=REG_RELATIONS)
214      set_status(incident_number, COMPLETED, current=SUBMITTED)
215
216
````

- **L207**: handler for the `notification_submitted` event, fired when Reg Relations marks the notification as sent (`review_service.reg_relations_complete` sets `submitted_to_regulators = True`).
- **L210–212**: only valid in `Reg_Relations`.
- **L213–214**: two transitions back to back: `Reg_Relations -> Submitted -> Completed`. Both are audited, so the trail shows both steps even though they happen instantly.


#### Lines 217–224

````python
217  def _ignore(incident: Dict[str, Any]) -> None:
218      """Agent-completion events (assessment_completed / template_completed) are
219      emitted by the DB triggers on INSERT, but the workflow worker already
220      handles agent completion inline (it invoked the agent), so these are
221      intentionally no-ops — acknowledged, not 'unhandled'."""
222      return None
223
224
````

- **L217**: `_ignore` is a deliberate no-op handler.
- **L218–221**: the docstring explains that the DB still emits `assessment_completed`/`template_completed` when agent rows are INSERTed. Since the worker that ran the agent already advanced the status inline (design choice 1), these events are acknowledged and dropped, not treated as "unknown".
- **L222**: `return None` (this is what a function with no `return` does anyway; written explicitly for clarity).
- **L223**: this line was not visible in the screenshots. Given the formatting elsewhere (two blank lines between top-level definitions, per the PEP 8 style guide), it was almost certainly a blank line (inference).


#### Lines 225–239

````python
225  # Keyed by the pre-existing DB trigger event_type contract (see db_setup.sql
226  # trigger_incident_change / trigger_assessment_complete / trigger_template_complete).
227  _HANDLERS = {
228      "mimt_input_completed": _handle_mimt_completed,
229      "1lod_approved": _handle_lod1_approved,
230      "1lod_feedback": _handle_lod1_feedback,
231      "2lod_approved": _handle_lod2_approved,
232      "2lod_feedback": _handle_lod2_feedback,
233      "2lod_returned": _handle_lod2_feedback,
234      "notification_submitted": _handle_reg_submitted,
235      "assessment_completed": _ignore,
236      "template_completed": _ignore,
237  }
238
239
````

- **L225–226**: comment saying the keys must match the `event_type` strings the DB triggers emit. This is a "contract" between the SQL and the Python.
- **L227**: `_HANDLERS` is a *dispatch table*: a dict mapping each event name to the function that handles it. Functions are objects in Python, so they can be stored as dict values and called later with `handler(incident)`. This replaces a long `if/elif` chain.
- **L228–234**: the human-decision events. Note **L233**: `"2lod_returned"` reuses `_handle_lod2_feedback`, so a "returned" decision from 2LOD is treated exactly like feedback.
- **L235–236**: agent-completion events go to `_ignore`.


#### Lines 240–252

````python
240  def handle_event(payload: Dict[str, Any]) -> None:
241      """Entry point for a single workflow event. Never raises — an agent or DB
242      failure moves the incident to 'Error' and is logged, so the listener keeps
243      running. Consumes the DB trigger `event_type` contract from db_setup.sql."""
244      event = payload.get("event_type")
245      incident_number = payload.get("incident_number")
246      handler = _HANDLERS.get(event)
247      if not handler or not incident_number:
248          logger.debug("Ignoring workflow event: %r", payload)
249          return
250      if handler is _ignore:
251          return
252
````

- **L240**: `handle_event(payload)` is the **public entry point**, called by `WorkflowListener._dispatch` on a thread-pool worker with the JSON payload already parsed into a dict.
- **L241–243**: docstring: it never raises; failures become an `Error` status plus logs.
- **L244–245**: read the event type and incident number from the payload with `.get` (missing keys give `None`, not an exception).
- **L246**: look up the handler; unknown events give `None`.
- **L247–249**: if there's no handler or no incident number, log at DEBUG (low importance) and stop. `%r` formats the payload with `repr()`, showing quotes and types, which is useful for debugging.
- **L250–251**: `handler is _ignore` checks *identity* (is it that exact function object?). It skips the DB read for ignored events.


#### Lines 253–258

````python
253      incident = _get_incident(incident_number)
254      if not incident:
255          logger.warning("workflow event for unknown incident %s", incident_number)
256          return
257
258      logger.info("Handling '%s' for %s (status=%s)", event, incident_number, incident.get("current_status"))
````

- **L253**: fetches a *fresh* copy of the incident row from the DB. Handlers make decisions based on the current DB state, not on what the notification said, which keeps the status gating reliable.
- **L254–256**: if the incident doesn't exist (e.g. deleted), it logs a warning and stops.
- **L258**: info log of which event is being handled and the incident's status at that moment.


#### Lines 259–268

````python
259      try:
260          handler(incident)
261      except Exception as exc:
262          logger.exception("Workflow handler failed for %s (%s)", incident_number, event)
263          try:
264              get_client().db_write("incidents", {"incident_number": incident_number, "current_status": ERROR})
265              _audit(incident_number, "workflow_error", {"event": event, "error": str(exc)})
266          except Exception:
267              logger.exception("Failed to mark %s as Error", incident_number)
268
````

- **L259–260**: calls the handler inside `try`.
- **L261**: `except Exception as exc:` catches any normal error (not things like `KeyboardInterrupt`) and binds it to `exc`.
- **L262**: logs the full stack trace.
- **L263–265**: a nested `try` writes `current_status = 'Error'` directly with `db_write` (bypassing `set_status`/`validate_state_transition`, so `Error` can be reached from any state) and audits `workflow_error` with the event name and `str(exc)` (the error message).
- **L266–267**: if even that fails (e.g. the DB is down), it logs and gives up quietly. This guarantees `handle_event` never raises, so the listener's worker threads survive.
- Note: when a handler fails partway (e.g. during a re-draft), `_clear_flag` is never reached, so the `lod*_approval_status` column keeps its old value. Based on `_clear_flag`'s docstring, re-submitting the *same* decision would then not count as a change and would not fire a new event. Also, no handler accepts `Error` as a starting status, even though `ALLOWED_TRANSITIONS` allows leaving `Error`. Recovery from `Error` therefore seems to need a manual status fix or some other code path outside this file.


---

## 21. `backend/services/workflow_listener.py`

*110 lines · complete*

This file is the **"ears" of the automated workflow**. Postgres has a built-in publish/subscribe feature called **LISTEN/NOTIFY**. A session can `LISTEN` on a named channel, and any `NOTIFY channel, 'payload'` (here sent by DB triggers when incidents change) is delivered to every listening session.

This module runs one background thread that:
- holds a dedicated DB connection;
- `LISTEN`s on the `workflow_events` channel;
- parses each JSON payload, for example `{"event_type": "1lod_feedback", "incident_number": "INC…"}`;
- hands the payload to `workflow_service.handle_event` on a small thread pool.

The thread pool means a slow agent run (minutes of LLM calls) never stops the listener from receiving the next event.

`start_listener()` and `stop_listener()` are called from the FastAPI **lifespan** (startup/shutdown) in `backend/main.py`. The events themselves are caused by writes from `intake_service.mark_mimt_complete` and the `review_service` approve, feedback and submit functions.


#### Lines 1–12

````python
 1  """PostgreSQL LISTEN/NOTIFY background listener for the workflow engine.
 2
 3  Opens its own dedicated (autocommit) connection — separate from the shared
 4  pool — LISTENs on the `workflow_events` channel, and dispatches each
 5  notification to backend.services.workflow_service.handle_event on a small
 6  worker pool so a long-running agent invocation never blocks consumption of
 7  further events.
 8
 9  Started/stopped from the FastAPI lifespan in backend/main.py. If the
10  connection drops it reconnects with a short backoff until stopped.
11  """
12
````

- **L1–11** Module docstring:
  - It opens its **own** autocommit connection instead of borrowing from the shared pool, because a listening connection is held open forever and would permanently take a pool slot. Autocommit is needed because notifications are delivered between transactions.
  - It LISTENs on `workflow_events` and dispatches to `workflow_service.handle_event` via a worker pool.
  - It is started and stopped from `main.py`'s lifespan.
  - It reconnects with a short pause if the connection drops.


#### Lines 13–19

````python
13  import json
14  import threading
15  import time
16  from concurrent.futures import ThreadPoolExecutor
17
18  import psycopg
19
````

- **L13** `json` parses the notification payload text.
- **L14** `threading` provides the background thread and an `Event` used as a thread-safe stop flag.
- **L15** `time` is used for `time.sleep` during reconnect backoff.
- **L16** `ThreadPoolExecutor` from `concurrent.futures` keeps a fixed pool of worker threads. You `submit(fn, arg)` and a free worker runs it.
- **L18** `psycopg` is the Postgres driver (version 3). It is used directly here, not through `PostgresClient`'s pool.


#### Lines 20–33

````python
20  from backend.config import (
21      POSTGRES_HOST,
22      POSTGRES_PORT,
23      POSTGRES_DB,
24      POSTGRES_SCHEMA,
25      POSTGRES_USER,
26      POSTGRES_PASSWORD,
27      WORKFLOW_EVENT_CHANNEL,
28  )
29  from backend.services import workflow_service
30  from backend.logger import get_logger
31
32  logger = get_logger(__name__)
33
````

- **L20–28** Connection settings from `backend/config.py`. These are read from environment variables. `POSTGRES_SCHEMA` defaults to `"public"` and `WORKFLOW_EVENT_CHANNEL` defaults to `"workflow_events"`.
- **L29** Imports the workflow engine module whose `handle_event` does the actual state transitions and agent runs.
- **L30–32** Module logger.


#### Lines 34–42

````python
34
35  class WorkflowListener:
36      """Background thread that turns DB NOTIFYs into workflow_service calls."""
37
38      def __init__(self) -> None:
39          self._stop = threading.Event()
40          self._thread: threading.Thread | None = None
41          self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wf-worker")
42
````

- **L35–36** `class WorkflowListener:` defines a class that bundles the thread, its stop flag and its worker pool together.
- **L38** `__init__` is the constructor, run when you call `WorkflowListener()`. `-> None` is the usual hint for it.
- **L39** `threading.Event()` is a thread-safe boolean flag. One thread calls `.set()`, and others check `.is_set()`. It is used to tell the loop to stop.
- **L40** Will hold the background `Thread` object. `threading.Thread | None` is the Python 3.10+ way of writing `Optional[threading.Thread]`.
- **L41** A pool of **2** worker threads named `wf-worker-0`, `wf-worker-1`, so they are easy to spot in logs. At most two workflow events are processed at the same time. The drafting and assessment locks in `operation_runtime` still serialise the LLM work itself.


#### Lines 43–48

````python
43      def _conninfo(self) -> str:
44          return (
45              f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
46              f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} sslmode=prefer"
47          )
48
````

- **L43–47** Builds a libpq "conninfo" string (`key=value key=value …`) from the config. `sslmode=prefer` uses SSL if the server supports it and falls back to plain otherwise.
  - Note: the values are inserted unquoted. If `POSTGRES_PASSWORD` ever contains a space, `'` or `\`, this string will not parse correctly. Passing keyword arguments to `psycopg.connect(host=..., password=...)`, or quoting the values, would avoid that.


#### Lines 49–56

````python
49      def _dispatch(self, payload_text: str) -> None:
50          try:
51              payload = json.loads(payload_text)
52          except (ValueError, TypeError):
53              logger.warning("Ignoring malformed workflow event payload: %r", payload_text)
54              return
55          self._executor.submit(workflow_service.handle_event, payload)
56
````

- **L49** Handles one raw notification payload, which is a string.
- **L50–54** Tries to parse it as JSON. `json.loads` raises `ValueError` (`JSONDecodeError` is a subclass) for bad text and `TypeError` for a non-string. The tuple in `except (A, B)` catches either. A malformed event is logged with `%r`, which shows the raw `repr`, and skipped instead of crashing the listener.
- **L55** Hands the parsed dict to a worker thread: `executor.submit(fn, arg)` queues `handle_event(payload)` and returns immediately. The returned *Future* (a handle for the eventual result) is ignored. That is safe only because `handle_event` is designed never to raise: it catches its own errors and moves the incident to "Error".


#### Lines 57–64

````python
57      def _run(self) -> None:
58          logger.info("Workflow listener starting (channel=%s)", WORKFLOW_EVENT_CHANNEL)
59          while not self._stop.is_set():
60              try:
61                  with psycopg.connect(self._conninfo(), autocommit=True) as conn:
62                      conn.execute(f"SET search_path TO {POSTGRES_SCHEMA}, public")
63                      conn.execute(f"LISTEN {WORKFLOW_EVENT_CHANNEL}")
64                      logger.info("Workflow listener connected and LISTENing")
````

- **L57** The function that runs inside the background thread.
- **L58** Logs which channel it's listening on.
- **L59** Outer loop. It runs until stop is requested, and its job is **reconnecting**.
- **L61** Opens a dedicated connection with `autocommit=True`. `with ... as conn:` uses the connection as a **context manager**: in psycopg 3 the connection is closed automatically when the block exits, normally or by exception.
- **L62** Sets the `search_path` so unqualified table names resolve to the app's schema first, then `public`. The f-string inserts a value from config, not user input, so this isn't an injection risk.
- **L63** `LISTEN workflow_events` subscribes this session to the channel. The channel name can't be passed as a bound `%s` parameter in Postgres, which is why it's an f-string.
- **L64** Logs that it's connected.


#### Lines 65–71

````python
65                      while not self._stop.is_set():
66                          # Yields queued notifications, then returns when the
67                          # timeout elapses so we can re-check the stop flag.
68                          for notify in conn.notifies(timeout=1.0):
69                              if notify.channel == WORKFLOW_EVENT_CHANNEL:
70                                  logger.info("Workflow event received: %s", notify.payload)
71                                  self._dispatch(notify.payload)
````

- **L65** Inner loop. It keeps reading notifications until stop is requested.
- **L66–67** Comment. `notifies(timeout=1.0)` yields any queued notifications, then returns after 1 second of quiet, so the loop can re-check the stop flag. The `timeout` parameter requires psycopg ≥ 3.2.
- **L68** `for notify in conn.notifies(...)` iterates over a **generator** (an object that produces values lazily, one at a time). Each `notify` has `.channel` and `.payload`.
- **L69** Checks the channel name. This is defensive, since only one channel is LISTENed.
- **L70–71** Logs the raw payload and dispatches it to the worker pool.


#### Lines 72–78

````python
72              except Exception:
73                  if self._stop.is_set():
74                      break
75                  logger.exception("Workflow listener connection error; reconnecting in 3s")
76                  time.sleep(3)
77          logger.info("Workflow listener stopped")
78
````

- **L72** Any exception (network drop, DB restart, auth failure…) lands here.
- **L73–74** If we're shutting down, the error is probably caused by the shutdown itself, so it `break`s out of the outer loop.
- **L75–76** Otherwise it logs the full traceback, waits 3 seconds, and the outer `while` loop reconnects. The fixed 3-second pause stops it from hammering a down database.
  - Note: Postgres NOTIFY is not durable. Events emitted while the listener is disconnected, or before the app starts, are **lost**, not queued. An incident whose event was missed would stay in its current state until someone triggers the change again.
- **L77** Logged once the loop exits.


#### Lines 79–85

````python
79      def start(self) -> None:
80          if self._thread and self._thread.is_alive():
81              return
82          self._stop.clear()
83          self._thread = threading.Thread(target=self._run, name="workflow-listener", daemon=True)
84          self._thread.start()
85
````

- **L79–81** `start()` is **idempotent**: if a thread already exists and is alive, it does nothing.
- **L82** Clears the stop flag, in case this instance was stopped before.
- **L83** Creates the thread to run `self._run`:
  - It is named `workflow-listener` for logs.
  - `daemon=True` means the thread won't keep the Python process alive at exit, so shutdown can't hang on it.
- **L84** Starts the thread. `_run` now executes concurrently.


#### Lines 86–92

````python
86      def stop(self) -> None:
87          self._stop.set()
88          if self._thread:
89              self._thread.join(timeout=5)
90          self._executor.shutdown(wait=False, cancel_futures=True)
91
92
````

- **L87** Sets the stop flag. The loops notice it within about 1 second, the `notifies` timeout.
- **L88–89** Waits up to 5 seconds for the thread to finish (`join(timeout=5)`).
- **L90** Shuts the worker pool down:
  - `wait=False` returns immediately instead of waiting for in-progress agent runs.
  - `cancel_futures=True` drops events that are queued but not yet started.
  - An agent run already in progress keeps going until it finishes.


#### Lines 93–95

````python
93  _listener: WorkflowListener | None = None
94
95
````

- **L93** `_listener` is a module-level variable holding the single listener for the whole process (a *singleton*). It starts as `None`.


#### Lines 96–103

````python
 96  def start_listener() -> None:
 97      """Start the process-wide workflow listener (idempotent)."""
 98      global _listener
 99      if _listener is None:
100          _listener = WorkflowListener()
101      _listener.start()
102
103
````

- **L96–101** `start_listener()` is called once from `main.py` at startup:
  - `global _listener` lets the function assign to the module-level variable. Without `global`, assigning would create a local variable instead.
  - It creates the listener the first time and then calls `start()`, which is safe to call repeatedly.


#### Lines 104–110

````python
104  def stop_listener() -> None:
105      """Stop the process-wide workflow listener if running."""
106      global _listener
107      if _listener is not None:
108          _listener.stop()
109          _listener = None
110
````

- **L104–109** `stop_listener()` is called from `main.py` at shutdown, after the lifespan's `yield`. If a listener exists, it stops it and sets the variable back to `None`. A later `start_listener()` then builds a fresh instance with a new thread pool, which matters because a shut-down `ThreadPoolExecutor` can't be reused.


---

## 22. `backend/services/job_queue.py`

*94 lines · complete*

A small **in-memory background job system**. Assessment, drafting and evaluation can take minutes because they make LLM calls, which is far too long to keep an HTTP request open. So the endpoints in `routers/assessment.py` and `routers/drafting.py` call `submit_job(...)` and immediately return HTTP 202 with a `job_id` and a `status_url`. The actual work then runs in FastAPI's background thread pool via `_execute_job`. The UI polls `GET /api/jobs/{job_id}` (`routers/operations.py`), which calls `get_job_status` until the status is `completed` or `failed`. Job state lives in a Python dict guarded by a lock, so it is lost on restart and not shared between processes, as the docstring warns.


#### Lines 1–11

````python
 1  """Generic in-memory background job queue for long-running agent operations.
 2
 3  NOTE: jobs live only in this process's memory (`_jobs` below) — they do not
 4  survive a process restart and are not shared across multiple worker
 5  processes. That's fine for the current single-instance deployment, but if
 6  this app is ever run behind multiple uvicorn workers or a process manager
 7  that can restart it between a client submitting a job and polling for its
 8  result, job state needs to move into Postgres (there's already a client)
 9  instead.
10  """
11
````

- **L1–10** Module docstring. It explains a real deployment limitation:
  - The `_jobs` dict exists only inside this one Python process. If the server restarts, or runs with several uvicorn worker processes, a client polling a job ID might hit a process that has never heard of it.
  - That's acceptable for the current single-instance deployment.
  - The suggested fix is to store jobs in Postgres instead.


#### Lines 12–24

````python
12  import threading
13  from datetime import datetime, timezone
14  from typing import Any, Callable, Dict
15  from uuid import uuid4
16
17  from fastapi import BackgroundTasks, HTTPException
18
19  from backend.schemas import JobAcceptedResponse, JobStatusResponse
20  from backend.services.common import safe_batch_error, serialize_result
21  from backend.logger import get_logger
22
23  logger = get_logger(__name__)
24
````

- **L12** `threading` gives access to `Lock`, used to make the shared dict safe when multiple threads touch it.
- **L13** `datetime.now(timezone.utc)` produces **timezone-aware** UTC timestamps, which serialize unambiguously in JSON.
- **L14** Type hints. `Callable[[], Any]` means "a function that takes no arguments and returns anything".
- **L15** `uuid4()` generates a random, practically unique ID for each job.
- **L17**:
  - `BackgroundTasks` is FastAPI's mechanism for "run this function *after* sending the response". FastAPI injects it into an endpoint when you declare a parameter of that type.
  - `HTTPException` produces HTTP error responses.
- **L19** The Pydantic response models. `JobAcceptedResponse` is the 202 body. `JobStatusResponse` is the polling body. Both are in `schemas.py`, where `operation` must be one of the `JobOperation` literals: `"assessment"`, `"draft"`, `"batch_draft"`, `"evaluate_draft"`, etc.
- **L20** `safe_batch_error` gives a client-safe error string. `serialize_result` turns a Pydantic result into a JSON-friendly dict via `model_dump(mode="json")`.
- **L21–23** Module logger.


#### Lines 25–28

````python
25  _jobs: Dict[str, Dict[str, Any]] = {}
26  _jobs_lock = threading.Lock()
27  _MAX_RETAINED_JOBS = 1000
28
````

- **L25** `_jobs` is the job store: a dict from job ID to a dict of job fields. The `: Dict[str, Dict[str, Any]]` part is a **variable annotation**, a type hint on a variable. It is module-level, so it is shared by every request in this process.
- **L26** One lock protects `_jobs`. The HTTP request threads and the background threads both read and write it. Without a lock, two threads could modify the dict at the same moment and corrupt or miss data.
- **L27** A cap of 1000 retained jobs, so memory can't grow forever.


#### Lines 29–35

````python
29
30  def submit_job(background_tasks: BackgroundTasks, operation: str, handler: Callable[[], Any]) -> JobAcceptedResponse:
31      """Register a job and schedule `handler` to run after the HTTP response
32      is sent. `handler` takes no arguments — callers close over whatever
33      request/agent context it needs."""
34      job_id = str(uuid4())
35      submitted_at = datetime.now(timezone.utc)
````

- **L30** `submit_job` takes FastAPI's `background_tasks`, an `operation` name (stored and echoed back) and a `handler`.
- **L31–33** Docstring. The handler takes **no arguments**. Callers wrap the real call in a small inner function that **closes over** the request data. In `routers/drafting.py`, for example, `def handler(): ... return run_single_draft(request)` uses `request` from the surrounding function. A function that remembers variables from where it was defined is called a *closure*.
- **L34** New random job ID as a string.
- **L35** Submission time in UTC.


#### Lines 36–45

````python
36      with _jobs_lock:
37          if len(_jobs) >= _MAX_RETAINED_JOBS:
38              completed = [
39                  key for key, value in _jobs.items()
40                  if value["status"] in {"completed", "failed"}
41              ]
42              for old_job_id in completed[: max(1, len(_jobs) - _MAX_RETAINED_JOBS + 1)]:
43                  _jobs.pop(old_job_id, None)
44          if len(_jobs) >= _MAX_RETAINED_JOBS:
45              raise HTTPException(status_code=429, detail="Job queue is full; retry later")
````

- **L36** `with _jobs_lock:` acquires the lock and **automatically releases it** at the end of the indented block, even if an exception is raised. `with` uses a *context manager*, an object that runs setup and cleanup code around a block.
- **L37–43** Eviction when the store is full (≥ 1000 jobs):
  - The list comprehension collects IDs of jobs that are already `completed` or `failed`. `{...}` here is a set literal, used for a fast membership test.
  - Python dicts keep insertion order, so this list goes from oldest to newest.
  - The slice `completed[: n]` takes the first `n` items. `n` is `max(1, len(_jobs) - 1000 + 1)`, which is at least 1 and enough to get back under the cap.
  - Those jobs are removed with `.pop(id, None)`. The `None` default avoids a `KeyError` if the job is already gone.
  - Queued or running jobs are never evicted.
- **L44–45** If the store is still full (all 1000 jobs are queued or running), the function refuses with HTTP **429 Too Many Requests** so the client retries later.


#### Lines 46–56

````python
46          _jobs[job_id] = {
47              "job_id": job_id,
48              "operation": operation,
49              "status": "queued",
50              "submitted_at": submitted_at,
51              "started_at": None,
52              "completed_at": None,
53              "result": None,
54              "error": None,
55          }
56      background_tasks.add_task(_execute_job, job_id, operation, handler)
````

- **L46–55** Registers the new job record:
  - status `"queued"`;
  - timestamps for start and finish, initially `None`;
  - `result`/`error` slots that are filled in later.
- **L56** `background_tasks.add_task(func, *args)` schedules `_execute_job(job_id, operation, handler)` to run **after** the HTTP response is sent. For a normal (non-`async`) function like this one, Starlette/FastAPI runs it in its worker **thread pool**, so the slow LLM work doesn't block the server's event loop.


#### Lines 57–63

````python
57      return JobAcceptedResponse(
58          job_id=job_id,
59          operation=operation,
60          status_url=f"/api/jobs/{job_id}",
61          submitted_at=submitted_at,
62      )
63
````

- **L57–62** Returns the 202 response body: the job ID, the operation, and `status_url`, the relative URL the UI should poll (handled by `routers/operations.py`), plus the submission time. `status` defaults to `"queued"` in the model.


#### Lines 64–70

````python
64
65  def _execute_job(job_id: str, operation: str, handler: Callable[[], Any]) -> None:
66      """Run one queued job in FastAPI's background thread pool."""
67      with _jobs_lock:
68          _jobs[job_id]["status"] = "running"
69          _jobs[job_id]["started_at"] = datetime.now(timezone.utc)
70
````

- **L65** The function that actually runs a job in the background thread.
- **L67–69** Under the lock, it marks the job `"running"` and records `started_at`. The lock is held only briefly, never during the long `handler()` call, so polling requests aren't blocked while a job runs.


#### Lines 71–77

````python
71      try:
72          result = handler()
73          with _jobs_lock:
74              _jobs[job_id]["status"] = "completed"
75              _jobs[job_id]["result"] = serialize_result(result)
76              _jobs[job_id]["completed_at"] = datetime.now(timezone.utc)
77          logger.info("Background job %s completed (%s)", job_id, operation)
````

- **L71–72** Calls the handler: the real assessment, drafting or evaluation work. Routers typically also wrap this in `operation_runtime.exclusive_operation(...)`, a lock that makes drafting jobs (or assessment jobs) run one at a time.
- **L73–76** On success, under the lock, it sets the status to `"completed"`, stores the JSON-serialised result (`serialize_result` turns a Pydantic model into a plain dict) and records the finish time.
- **L77** Logs completion.


#### Lines 78–84

````python
78      except Exception as exc:
79          logger.exception("Background job %s failed (%s)", job_id, operation)
80          with _jobs_lock:
81              _jobs[job_id]["status"] = "failed"
82              _jobs[job_id]["error"] = safe_batch_error(exc)
83              _jobs[job_id]["completed_at"] = datetime.now(timezone.utc)
84
````

- **L78** `except Exception as exc:` catches any error from the handler.
- **L79** `logger.exception` logs the message **with the full traceback**, for developers.
- **L80–83** It marks the job `"failed"` and stores only a **client-safe** error string. For an `HTTPException` (such as "Incident X was not found") that is its detail; for anything else it is the generic "Processing failed; see server logs for details". Internal details stay in the logs.


#### Lines 85–94

````python
85
86  def get_job_status(job_id: str) -> JobStatusResponse:
87      """Return the queued/running/completed/failed state for a job."""
88      with _jobs_lock:
89          job = _jobs.get(job_id)
90          if not job:
91              raise HTTPException(status_code=404, detail="Job not found")
92          snapshot = dict(job)
93      return JobStatusResponse(**snapshot)
94
````

- **L86** Used by the polling endpoint.
- **L88–92** Under the lock:
  - It looks up the job and returns 404 if the ID is unknown (or was evicted, or the server restarted).
  - `dict(job)` takes a **copy** (a "snapshot"), so the code below works on a stable copy while the background thread may keep updating the original.
- **L93** Builds `JobStatusResponse(**snapshot)`. `**` unpacks the dict keys into keyword arguments, and Pydantic validates them, for example that `status` is one of the allowed literals. This happens outside the lock, keeping the locked section short.


---

## 23. `backend/services/operation_runtime.py`

*23 lines · complete*

Provides **one lock per kind of long-running LLM operation** ("assessment" and "drafting") and a small helper, `exclusive_operation`, for running code while holding that lock. The routers (`routers/assessment.py`, `routers/drafting.py`) and `workflow_service.py` wrap their work in `with operation_runtime.exclusive_operation("assessment"):`. Background jobs run on FastAPI's thread pool, so without this lock two users could start two assessments (or a single run and a batch) at the same time, multiplying LLM load and racing on the same DB rows.


#### Lines 1–6

````python
1  """Concurrency controls for long-running assessment and drafting operations."""
2
3  import threading
4  from contextlib import contextmanager
5  from typing import Iterator
6
````

- **L1** The docstring: concurrency controls for long-running assessment and drafting operations.
- **L3** `threading` is the standard library for threads. It provides `Lock`.
- **L4** `contextmanager` is a decorator that turns a generator function (one that uses `yield`) into something you can use in a `with` statement.
- **L5** `Iterator[None]` is the type hint for a generator that yields nothing meaningful. That's the conventional annotation for `@contextmanager` functions.


#### Lines 7–12

````python
 7
 8  _OPERATION_LOCKS = {
 9      "assessment": threading.Lock(),
10      "drafting": threading.Lock(),
11  }
12
````

- **L8–11** `_OPERATION_LOCKS` is a module-level dict mapping an operation name to a `threading.Lock()`. A **Lock** is a mutex: only one thread can hold it at a time, and any other thread that tries to acquire it **waits** until it's released. Because it's created once when the module is imported, every request in this server process shares these same two locks. Assessment and drafting have *separate* locks, so one assessment and one draft can still run at the same time.
- Note: these are in-process locks. If the API ran as several worker processes or replicas, each would have its own locks, so they wouldn't serialize across processes.


#### Lines 13–23

````python
13
14  @contextmanager
15  def exclusive_operation(operation: str) -> Iterator[None]:
16      """Serialize operations that use the same LLM workflow."""
17      try:
18          lock = _OPERATION_LOCKS[operation]
19      except KeyError as exc:
20          raise ValueError(f"Unknown operation: {operation}") from exc
21      with lock:
22          yield
23
````

`exclusive_operation`: the context manager.
- **L14** `@contextmanager` is a **decorator**. The `@name` line above a function wraps it in another function. Here it turns the generator below into a context manager, so `with exclusive_operation("assessment"):` runs the code before `yield` on entry and the code after it on exit.
- **L15** Takes the operation name. The return hint is explained at L5.
- **L16** Docstring: serialize (run one at a time) operations that use the same LLM workflow.
- **L17–20** Looks up the lock. An unknown name raises `KeyError`, which is turned into a clearer `ValueError("Unknown operation: ...")`. `raise ... from exc` keeps the original error attached as the cause, which helps debugging.
- **L21** `with lock:` acquires the lock, blocking until it's free, and guarantees it's released when the block exits, even if an exception occurs.
- **L22** `yield` hands control back to the caller's `with` body while the lock is held. When that body finishes or raises, execution resumes here, the inner `with lock:` exits, and the lock is released.
- In effect, a batch of 50 incidents holds the "assessment" lock for the whole batch. Its internal parallelism (`BATCH_MAX_WORKERS` threads in `common.execute_batch`) happens *inside* that one lock, and any other assessment request waits until the batch finishes.


---

## 24. `backend/services/eval_cache.py`

*49 lines · complete*

A tiny **file-based cache** that keeps the most recent evaluation summary (agent output vs. human ground truth) for each agent, stored as a JSON file on disk. `assessment_service.execute_assessment_evaluation` and the drafting equivalent call `save_latest_evaluation` after running an evaluation. `get_latest_assessment_evaluation` and the drafting version call `load_latest_evaluation`, so the Admin page can show the previous result instantly instead of re-running a slow, LLM-heavy evaluation.


#### Lines 1–12

````python
 1  """Tiny on-disk cache of the latest LLM-as-judge evaluation result per agent.
 2
 3  The evaluation jobs (execute_assessment_evaluation / execute_draft_evaluation)
 4  already write timestamped .txt/.xlsx reports to
 5  backend/evaluation/prompt_iterations/evaluations/ for human reading, but
 6  nothing persisted the JSON summary itself - so the Admin page had no way to
 7  show a previous result without the user re-running the (slow, LLM-calling)
 8  evaluation every time they just wanted to look at it again. This stores the
 9  most recent summary per agent as JSON, so the Admin page can show it
10  immediately on load if it's still for the currently active prompt version.
11  """
12
````

The module docstring, which explains the "why".
- **L3–5** The evaluation jobs already write timestamped `.txt` and `.xlsx` reports to `backend/evaluation/prompt_iterations/evaluations/`, but those are for people to read.
- **L5–8** The JSON summary itself was never saved, so the Admin page couldn't show a past result without re-running the evaluation.
- **L8–10** This module stores the latest summary per agent, and the Admin page shows it on load if it matches the currently active prompt version.


#### Lines 13–21

````python
13  import json
14  from pathlib import Path
15  from typing import Any, Dict, Optional
16
17  from backend.evaluation.prompt_iterations.prompt_config import EVALUATIONS_DIR
18  from backend.logger import get_logger
19
20  logger = get_logger(__name__)
21
````

Imports and logger.
- **L13** `json` is the standard-library JSON encoder and decoder.
- **L14** `Path` (from `pathlib`) is an object-oriented file path. You join paths with `/` and call methods like `.exists()`, `.read_text()` and `.write_text()` directly on it.
- **L15** Type hints. `Optional[Dict[str, Any]]` means "a dict, or `None`".
- **L17** `EVALUATIONS_DIR` is defined in `prompt_config.py` as `<prompt_iterations folder>/evaluations`, the same folder the human-readable reports go to.
- **L18–20** The project's logger factory, and this module's logger.


#### Lines 22–25

````python
22
23  def _cache_path(agent: str) -> Path:
24      return EVALUATIONS_DIR / f"latest_{agent}_evaluation.json"
25
````

`_cache_path`: builds the file path for an agent.
- **L24** `EVALUATIONS_DIR / f"latest_{agent}_evaluation.json"` uses `Path`'s `/` operator to join the folder and file name. For example, `agent="assessment"` gives `.../evaluations/latest_assessment_evaluation.json`. There's one fixed file per agent, so each save overwrites the previous one.


#### Lines 26–36

````python
26
27  def save_latest_evaluation(agent: str, summary: Dict[str, Any]) -> None:
28      """Persist the most recent evaluation summary for an agent, overwriting
29      any previous one. Best-effort - a cache write failure shouldn't fail the
30      evaluation job itself."""
31      try:
32          EVALUATIONS_DIR.mkdir(parents=True, exist_ok=True)
33          _cache_path(agent).write_text(json.dumps(summary, default=str, indent=2))
34      except Exception:
35          logger.exception("Failed to cache latest %s evaluation", agent)
36
````

`save_latest_evaluation`: writes the cache.
- **L27** Takes the agent key (`"assessment"` or `"drafting"`) and the summary dict, and returns nothing.
- **L28–30** The docstring says the write is **best-effort**: a failure to cache must not fail the evaluation job.
- **L32** `mkdir(parents=True, exist_ok=True)` creates the folder, and any missing parent folders, without erroring if it already exists.
- **L33** `json.dumps(summary, default=str, indent=2)` converts the dict to pretty-printed JSON. `default=str` is a fallback: any value JSON can't encode natively (e.g. a `datetime`) is converted with `str()` instead of raising. `.write_text(...)` writes the string to the file, replacing any existing content.
- **L34–35** `except Exception:` catches anything (disk full, permissions). `logger.exception(...)` logs the message **plus the stack trace**, and the function quietly returns.


#### Lines 37–49

````python
37
38  def load_latest_evaluation(agent: str) -> Optional[Dict[str, Any]]:
39      """Return the most recently saved evaluation summary for an agent, or
40      None if none has ever been saved (or it failed to load)."""
41      path = _cache_path(agent)
42      if not path.exists():
43          return None
44      try:
45          return json.loads(path.read_text())
46      except Exception:
47          logger.exception("Failed to load cached %s evaluation", agent)
48          return None
49
````

`load_latest_evaluation`: reads the cache.
- **L38** Returns the dict, or `None`.
- **L41–43** If the file doesn't exist (no evaluation has ever run), it returns `None`.
- **L44–45** Reads the file and parses the JSON back into a dict.
- **L46–48** If the file is corrupt or unreadable, it logs the error with a traceback and returns `None`, so callers just behave as if there's no cache.
- Note: the file holds the summary dict exactly as the evaluator built it. For the assessment agent, that dict's `prompt_version` is the full module name, which is why the version check in `assessment_service.get_latest_assessment_evaluation` never matches (see the note there).
- Note: the cache lives on the server's local disk, so it's per-machine and is lost if the container is rebuilt. That's acceptable for a convenience cache.


---

## 25. `backend/tools/__init__.py`

*28 lines · complete*

`__init__.py` turns the `backend/tools/` folder into a Python *package* and runs when anyone imports `backend.tools`. This one re-exports the main LangChain tools from the submodules (`data_tools`, `audit_tools`, `assessment_tools`, `drafting_tools`), so other code can write `from backend.tools import db_query`. It also groups them into ready-made lists (`COMMON_TOOLS`, `ASSESSMENT_TOOLS`, `DRAFTING_TOOLS`) meant to be handed to an agent. In the current code the agents (`backend/agents/*.py`) and services import tools from the submodules directly and build their own `TOOLS` lists, so these grouped lists look unused.


#### Lines 1–7

````python
1  """Tools package for Anchor AI agents."""
2
3  from backend.tools.data_tools import db_query, db_write
4  from backend.tools.audit_tools import audit_log
5  from backend.tools.assessment_tools import evaluate_reportability
6  from backend.tools.drafting_tools import generate_template_content, get_template_schema
7
````

- **L1**: package docstring.
- **L3**: imports the generic DB tools `db_query` (read rows) and `db_write` (insert/update rows) from `data_tools.py`. Each is a LangChain *tool* object (created by the `@tool` decorator), not a plain function.
- **L4**: imports the `audit_log` tool, which writes to the `audit_logs` table.
- **L5**: imports `evaluate_reportability`, the tool that makes the LLM call scoring an incident against the 4 FRB (Federal Reserve Board) reportability criteria.
- **L6**: imports the drafting tools: `generate_template_content` (the LLM writes the notification text) and `get_template_schema` (loads `template_schema.json`).
- Side effect: importing `backend.tools` runs all four submodules, including `data_tools`, which computes the prompt-version labels at import time.
- Note: `reference_data_tools.py` (which defines `lookup_cuso_applications` / `get_regulatory_requirements`, used by `assessment_agent.py`) is not re-exported here.


#### Lines 8–16

````python
 8  # Common tools shared by all agents
 9  COMMON_TOOLS = [db_query, db_write, audit_log]
10
11  # Assessment-specific tools
12  ASSESSMENT_TOOLS = [evaluate_reportability]
13
14  # Drafting-specific tools
15  DRAFTING_TOOLS = [generate_template_content, get_template_schema]
16
````

- **L9**: `COMMON_TOOLS` lists the tools every agent should have: read DB, write DB, audit.
- **L12**: `ASSESSMENT_TOOLS` lists the assessment-only tool.
- **L15**: `DRAFTING_TOOLS` lists the drafting-only tools.
- The intent is that an agent can be built with `COMMON_TOOLS + ASSESSMENT_TOOLS`. In practice `backend/agents/assessment_agent.py` and `drafting_agent.py` declare their own `TOOLS = [...]` lists, so these constants are not referenced anywhere else in `backend/` (they're kept for convenience or backward compatibility).


#### Lines 17–28

````python
17  __all__ = [
18      "db_query",
19      "db_write",
20      "audit_log",
21      "evaluate_reportability",
22      "generate_template_content",
23      "get_template_schema",
24      "COMMON_TOOLS",
25      "ASSESSMENT_TOOLS",
26      "DRAFTING_TOOLS",
27  ]
28
````

- **L17–27**: `__all__` is a special list naming the module's "public" names. It controls what `from backend.tools import *` brings in, and it signals to readers and linters which names are intended for outside use. Without it, re-exported imports could be flagged as "unused imports".
- It lists the six tools plus the three grouped lists.


---

## 26. `backend/tools/data_tools.py`

*256 lines · complete*

This file provides the two generic database tools, `db_query` and `db_write`, which LLM agents (and the service code, via `.invoke`) use to read and write Postgres. They wrap `PostgresClient.db_query`/`db_write` and add app-specific behaviour automatically:
- reads of `reportability_assessments` default to the **active prompt version**;
- writes are stamped with `prompt_version`, the assessment `revision`, and the `assessment_prompt_version` that drove a draft;
- non-persisted `confidence_*` keys are stripped;
- required assessment fields are validated.

The "which revision / which assessment version" values are passed in invisibly through **context variables** set by `assessment_service` and `drafting_service`, so the LLM never has to decide them. The file also computes the short version labels (`'v10'`, `'v4'`) from `prompt_config.py`; these are imported across services and the evaluation scripts.


#### Lines 1–11

````python
 1  """Generic database tools for querying and writing data."""
 2
 3  import contextvars
 4  import re
 5  from typing import Dict, Any, List, Optional
 6  from langchain_core.tools import tool
 7  from backend.utils.postgres_client import get_client
 8  from backend.logger import get_logger, log_tool_call
 9
10  logger = get_logger(__name__)
11
````

- **L1**: module docstring.
- **L3**: `contextvars` is a standard-library module for *context variables*: values that are "global" but private to the current thread or asyncio task. Code deep in a call stack can read a value set higher up without it being passed through every function, and concurrent runs don't see each other's values.
- **L4**: `re`, regular expressions (used to pull `v<N>` out of module names).
- **L5**: typing helpers.
- **L6**: LangChain `@tool` decorator.
- **L7–8**: DB client getter, logger factory, and `log_tool_call` (structured tool logging).
- **L10**: module logger.


#### Lines 12–19

````python
12  # Set by the drafting orchestration (main._run_single_draft) so that every
13  # template_details write during that draft records which reportability
14  # assessment version drove it ('human' for the human-provided assessment, or
15  # an assessment agent prompt_version like 'v2'). Default None => the column is
16  # left unset (e.g. human-uploaded templates or non-drafting writes).
17  _current_assessment_prompt_version: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
18      "current_assessment_prompt_version", default=None
19  )
````

- **L12–16**: comment: during a drafting run, every `template_details` write should record *which assessment* the draft was based on: `'human'` (the human-provided assessment) or an agent prompt version like `'v2'`. The default `None` leaves the column unset (e.g. human-uploaded templates). It says the value is set by `main._run_single_draft`; Note: that code now lives in `backend/services/drafting_service.py::run_single_draft` (stale reference).
- **L17–19**: creates the context variable. `contextvars.ContextVar[Optional[str]]` is the type annotation (holds a string or None); `"current_assessment_prompt_version"` is its debug name; `default=None` is returned by `.get()` when nothing was set in the current context.


#### Lines 20–30

````python
20
21  # Set by the assessment orchestration (main._run_single_assessment, via
22  # backend.tools.assessment_tools.compute_assessment_revision) BEFORE the
23  # assessment agent is invoked, so db_write's revision auto-injection below is
24  # fully deterministic. The agent itself never decides the revision number -
25  # it just reads lod1_response/lod2_r_and_c (if present) as plain context; see
26  # compute_assessment_revision for the feedback-staleness/revision logic.
27  _current_assessment_revision: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
28      "current_assessment_revision", default=None
29  )
30
````

- **L21–26**: comment: a second context variable holds the **revision number** for assessment writes. The assessment orchestration (now `assessment_service.run_single_assessment`; the comment's `main._run_single_assessment` is stale) calls `compute_assessment_revision` *before* running the assessment and binds the result here, so the revision logic is deterministic and the LLM never chooses it.
- **L27–29**: the `ContextVar[Optional[int]]` for the revision, defaulting to `None`.


#### Lines 31–46

````python
31
32  def set_current_assessment_prompt_version(value: Optional[str]):
33      """Bind the assessment version for the current drafting context. Returns a
34      token to pass to reset_current_assessment_prompt_version when done."""
35      return _current_assessment_prompt_version.set(value)
36
37
38  def reset_current_assessment_prompt_version(token) -> None:
39      """Clear the bound assessment version using the token from set_..."""
40      _current_assessment_prompt_version.reset(token)
41
42
43  def get_current_assessment_prompt_version() -> Optional[str]:
44      """Return the assessment version bound for the current drafting context."""
45      return _current_assessment_prompt_version.get()
46
````

- These three small functions are the "API" for the first context variable, so other modules don't touch the private `_current_...` variable directly.
- **L32–35**: `set_current_assessment_prompt_version(value)` calls `.set(value)`, which returns a **Token** recording the previous value.
- **L38–40**: `reset_...(token)` restores the value from before that `set`. Callers use the set/reset pair around a block, typically `token = set_...(x)` then `try: ... finally: reset_...(token)` (that's what `drafting_service` does). This guarantees the binding doesn't leak into the next job running on the same thread.
- **L43–45**: `get_...()` returns the current value (or the `None` default).


#### Lines 47–64

````python
47
48  def set_current_assessment_revision(value: Optional[int]):
49      """Bind the deterministically-computed revision for the current assessment
50      context. Returns a token to pass to reset_current_assessment_revision."""
51      return _current_assessment_revision.set(value)
52
53
54  def reset_current_assessment_revision(token) -> None:
55      """Clear the bound revision using the token from set_current_assessment_revision."""
56      _current_assessment_revision.reset(token)
57
58
59  def get_current_assessment_revision() -> Optional[int]:
60      """Return the revision bound for the current assessment context, or None
61      if unset (callers not going through the assessment orchestration, e.g.
62      ad-hoc scripts, fall back to the default of 0 in db_write)."""
63      return _current_assessment_revision.get()
64
````

- **L48–51**: `set_current_assessment_revision(value)` binds the revision and returns the token.
- **L54–56**: `reset_current_assessment_revision(token)` undoes it (used in a `finally` block in `assessment_service`).
- **L59–63**: `get_current_assessment_revision()` returns the bound revision, or `None` if nothing set it (e.g. an ad-hoc script); `db_write` then falls back to 0.
- Because the workflow listener runs jobs on worker threads, each thread has its own context, so two incidents being processed at once can't mix up their revision numbers.


#### Lines 65–76

````python
65  # The active prompt version for each agent lives in
66  # backend/evaluation/prompt_iterations/prompt_config.py (same place that determines
67  # which SYSTEM_PROMPT module actually gets loaded - see backend/agents/assessment_agent.py)
68  # - that's the single source of truth. We used to also read a separate
69  # ASSESSMENT_PROMPT_VERSION/TEMPLATE_PROMPT_VERSION from .env just for labeling DB rows,
70  # but that let the label drift out of sync with the prompt that was actually running, so
71  # it's been removed in favor of deriving directly from here.
72  from backend.evaluation.prompt_iterations.prompt_config import (
73      ASSESSMENT_AGENT_PROMPT_VERSION,
74      DRAFTING_AGENT_PROMPT_VERSION,
75  )
76
````

- **L65–71**: comment explaining a past bug fix. The active prompt version used to be read separately from `.env` just for labelling DB rows, which could drift from the prompt actually running. Now there's one source of truth: `backend/evaluation/prompt_iterations/prompt_config.py`, the same file that decides which prompt module is loaded.
- **L72–75**: imports the two constants from it, currently `"assessment_agent_prompt_v10"` and `"drafting_agent_prompt_v4"`. This import sits mid-file instead of at the top, presumably to keep it next to the explanatory comment. Python allows imports anywhere.


#### Lines 77–86

````python
77
78  def _short_prompt_version(module_name: str) -> str:
79      """Shorten a prompt module name to just its 'v<N>' suffix for DB labeling
80      (e.g. 'assessment_agent_prompt_v1' -> 'v1'), keeping the short v0/v1/v2...
81      naming convention used elsewhere. Falls back to the full module name if it
82      doesn't end in v<N>, so a naming mismatch fails loud instead of silently
83      mislabeling."""
84      m = re.search(r"(v\d+)$", module_name)
85      return m.group(1) if m else module_name
86
````

- **L78**: `_short_prompt_version(module_name)` shortens a module name to its version suffix.
- **L79–83**: docstring: `'assessment_agent_prompt_v1'` becomes `'v1'`. If the name doesn't end in `v<N>`, the full name is used, so a naming mistake shows up clearly instead of mislabelling rows.
- **L84**: `re.search(r"(v\d+)$", module_name)`. The `r"..."` is a *raw string* (backslashes aren't escapes). The pattern means "a `v` followed by one or more digits (`\d+`) at the very end (`$`)"; the parentheses form *capture group 1*.
- **L85**: `m.group(1)` returns the captured text if there was a match (`m` is a match object); otherwise returns the input unchanged.


#### Lines 87–91

````python
87
88  ASSESSMENT_PROMPT_VERSION_LABEL = _short_prompt_version(ASSESSMENT_AGENT_PROMPT_VERSION)
89  TEMPLATE_PROMPT_VERSION_LABEL = _short_prompt_version(DRAFTING_AGENT_PROMPT_VERSION)
90
91
````

- **L88**: `ASSESSMENT_PROMPT_VERSION_LABEL`, e.g. `'v10'`. It is computed once, at import time. It's what gets written into `reportability_assessments.prompt_version` and used to filter reads. `assessment_service`, `drafting_service` and `evaluation/compare_agent_vs_human.py` import it.
- **L89**: `TEMPLATE_PROMPT_VERSION_LABEL`, e.g. `'v4'`, used for `template_details.prompt_version`.
- Note that changing the active prompt requires a process restart for these labels to update.


#### Lines 92–110

````python
 92  @tool
 93  def db_query(table: str, filters: Optional[Dict[str, Any]] = None, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
 94      """Query database with filters and column selection.
 95
 96      This is a generic database query tool that constructs SQL from parameters.
 97      The LLM provides the table name, filters, and columns - the tool builds the SQL.
 98
 99      Args:
100          table: Table name (incidents, reportability_assessments, template_details, users, audit_logs)
101          filters: Dict of column=value filters (AND combined). Example: {"incident_number": "INC0575428", "priority": "1 - Significan⟦CUT⟧
102              A filter value of null/None matches rows where that column IS NULL
103              (e.g. {"prompt_version": null} finds the human-provided row for
104              reportability_assessments/template_details, since human-provided
105              rows always have prompt_version = NULL, not an agent version label).
106          columns: List of columns to select. If None, selects all columns. Example: ["incident_number", "description", "priority"]
107
108      Returns:
109          List of dicts with query results. Each dict represents one row.
110
````

- **L92**: `@tool` makes `db_query` a LangChain tool; the docstring below is what the LLM reads to learn how to use it.
- **L93**: signature: `table` (name), optional `filters` (column -> value, combined with AND), optional `columns` (which columns to return; all if None). Returns a list of row dicts.
- **L94–97**: explains the idea: the LLM supplies table, filters and columns; the tool builds the SQL.
- **L99–106**: `Args`. Allowed tables are listed (incidents, reportability_assessments, template_details, users, audit_logs). The end of L101 was cut off in the screenshots (it's an example filter dict). **L102–105** are important: a filter value of `None` matches `IS NULL`, which is how you find the *human-provided* assessment or template rows (they have `prompt_version = NULL`). In SQL, `= NULL` never matches, so `PostgresClient.db_query` special-cases it.


#### Lines 111–123

````python
111      Examples:
112          # Get full incident record
113          db_query("incidents", {"incident_number": "INC0575428"})
114
115          # Get specific columns
116          db_query("incidents", {"incident_number": "INC0575428"}, ["priority", "description"])
117
118          # Query similar incidents
119          db_query("incidents", {"legal_entity": "CNB", "priority": "1 - Significant"}, ["incident_number", "incident_title"])
120
121          # Get the human-provided assessment (prompt_version IS NULL)
122          db_query("reportability_assessments", {"incident_number": "INC0575428", "prompt_version": None})
123      """
````

- **L111–122**: examples: full incident row, specific columns, "similar incidents" by legal entity and priority, and the human assessment via `prompt_version: None`. They teach the LLM the calling conventions.


#### Lines 124–135

````python
124      try:
125          filters = dict(filters) if filters else {}
126
127          # reportability_assessments holds one row per assessment prompt_version
128          # (plus a human-provided row where prompt_version IS NULL). Reads default
129          # to the currently active ASSESSMENT_AGENT_PROMPT_VERSION so callers (e.g.
130          # the Drafting Agent) always draft off the assessment the team currently
131          # trusts, unless they explicitly ask for a specific prompt_version.
132          if table == "reportability_assessments" and "prompt_version" not in filters:
133              filters["prompt_version"] = ASSESSMENT_PROMPT_VERSION_LABEL
134              logger.debug(f"Auto-filtered prompt_version={ASSESSMENT_PROMPT_VERSION_LABEL} for reportability_assessments read")
135
````

- **L125**: `dict(filters)` makes a *copy* so the auto-filter below doesn't modify the caller's dict; a missing/empty `filters` becomes `{}`.
- **L127–131**: comment: `reportability_assessments` has one row per prompt version plus the human row. Unless the caller asks for a specific version, reads default to the active version, so e.g. the drafting agent drafts from the assessment the team currently trusts.
- **L132–134**: `"prompt_version" not in filters` checks whether the key exists. If not, it injects the active label and logs at DEBUG. Passing `{"prompt_version": None}` explicitly counts as "specified" (the key exists), which is how the human row stays reachable.


#### Lines 136–146

````python
136          inputs = {"table": table, "filters": filters, "columns": columns}
137          client = get_client()
138          results = client.db_query(table, filters, columns)
139          logger.info(f"db_query: table={table}, filters={filters}, returned {len(results)} rows")
140          log_tool_call("db_query", inputs, outputs=f"Returned {len(results)} rows")
141          return results
142      except Exception as e:
143          logger.error(f"db_query failed: {e}")
144          log_tool_call("db_query", {"table": table, "filters": filters}, error=str(e))
145          raise
146
````

- **L136**: snapshot of inputs for the tool log.
- **L137–138**: runs the query through `PostgresClient.db_query`. That method builds `SELECT <columns> FROM <table> WHERE col = %s AND ...` with values passed as **SQL parameters** (`%s` placeholders filled safely by the psycopg driver), and orders versioned tables by `updated_at DESC`.
- **L139–141**: logs the row count, writes the tool log, and returns the rows (list of dicts).
- **L142–145**: on error: log, record the error in the tool log, re-raise.
- Note: only the *values* are parameterised. The `table` and `columns` strings are inserted directly into the SQL text by `PostgresClient.db_query`, with no allow-list check in either file. Since the docstring says the LLM supplies them, a bad or manipulated tool call could inject SQL through those identifiers. An allow-list of table and column names would close this.


#### Lines 147–168

````python
147
148  @tool
149  def db_write(table: str, payload: Dict[str, Any]) -> str:
150      """Insert or update database record.
151
152      This tool writes data to the database. It automatically determines whether to
153      INSERT (new record) or UPDATE (existing record) based on the table's primary key.
154
155      The tool automatically injects prompt_version from config for assessment and template tables.
156      For reportability_assessments, revision is ALSO auto-injected - it is never something you
157      (the agent) need to compute or pass explicitly. The system determines, before you're even
158      invoked, whether this is a fresh assessment or a re-assessment driven by new 1LOD/2LOD
159      feedback (lod1_response/lod2_r_and_c on the incident's existing assessment row), and binds
160      the correct revision number for you. Just write your assessment normally.
161
162      Args:
163          table: Table name to write to
164          payload: Dict of column=value pairs to write
165
166      Returns:
167          ID of the inserted or updated record
168
````

- **L148–149**: `@tool db_write(table, payload) -> str`: write one record; returns its id as a string.
- **L150–153**: it decides INSERT vs UPDATE itself. In `PostgresClient.db_write`: `incidents` updates by `incident_number` if the row exists; `reportability_assessments`/`template_details` *upsert* on their key columns (`REVISIONED_TABLES`); otherwise it INSERTs.
- **L155–160**: tells the LLM that `prompt_version` and (for assessments) `revision` are injected automatically, and that the system already worked out whether this is a fresh assessment or a feedback-driven re-assessment. "Just write your assessment normally."
- **L162–167**: `Args`/`Returns`.


#### Lines 169–187

````python
169      Examples:
170          # Write assessment results (prompt_version and revision auto-injected -
171          # do not pass revision yourself)
172          db_write("reportability_assessments", {
173              "incident_number": "INC0575428",
174              "reportability_flag": "Yes",
175              "data_system_impact": "Yes - significant data loss",
176              "operational_impact": "Yes - 4 hour outage",
177              "financial_reputational_impact": "No",
178              "systemic_risk": "No",
179              "provided_by": "Assessment Agent"
180          })
181
182          # Update incident status
183          db_write("incidents", {
184              "incident_number": "INC0575428",
185              "current_status": "Assessment"
186          })
187      """
````

- **L169–186**: examples: writing an assessment (all 4 FRB fields, the flag and `provided_by`; no revision), and updating an incident's `current_status`.


#### Lines 188–196

````python
188      try:
189          # Auto-inject prompt_version for assessment and template tables
190          if table == "reportability_assessments" and "prompt_version" not in payload:
191              payload["prompt_version"] = ASSESSMENT_PROMPT_VERSION_LABEL
192              logger.debug(f"Auto-injected prompt_version={ASSESSMENT_PROMPT_VERSION_LABEL} for reportability_assessments")
193          elif table == "template_details" and "prompt_version" not in payload:
194              payload["prompt_version"] = TEMPLATE_PROMPT_VERSION_LABEL
195              logger.debug(f"Auto-injected prompt_version={TEMPLATE_PROMPT_VERSION_LABEL} for template_details")
196
````

- **L188**: `try`, for the usual log-then-re-raise error handling.
- **L190–192**: for `reportability_assessments` without an explicit `prompt_version`, stamp the active assessment label (e.g. `'v10'`).
- **L193–195**: `elif`: for `template_details`, stamp the drafting label (e.g. `'v4'`).
- Callers that *want* NULL (human rows) pass `"prompt_version": None` explicitly; the key then exists, so nothing is injected (`review_service` does exactly that).


#### Lines 197–211

````python
197          # revision is part of reportability_assessments' upsert key (see
198          # REVISIONED_TABLES in postgres_client.py) so that a feedback-driven
199          # re-assessment (new lod1_response/lod2_r_and_c after the original
200          # assessment) can insert a NEW row instead of overwriting it. This is
201          # fully deterministic - not something the LLM decides. The assessment
202          # orchestration (main._run_single_assessment) calls
203          # assessment_tools.compute_assessment_revision BEFORE invoking the
204          # agent and binds the result via set_current_assessment_revision, so
205          # it's simply read here. Falls back to 0 if nothing bound (e.g. a
206          # script invoking the agent directly, outside the orchestration).
207          if table == "reportability_assessments" and "revision" not in payload:
208              bound_revision = get_current_assessment_revision()
209              payload["revision"] = bound_revision if bound_revision is not None else 0
210              logger.debug(f"Auto-injected revision={payload['revision']} for reportability_assessments")
211
````

- **L197–206**: comment: `revision` is part of the assessment upsert key, so a feedback-driven re-assessment gets a *new row* instead of overwriting the old one. The value is computed deterministically beforehand and only read here; it falls back to 0 if no orchestration bound one.
- **L207**: only for assessments where the caller didn't pass `revision`.
- **L208**: reads the context variable bound by `assessment_service` (via `set_current_assessment_revision`).
- **L209**: uses it, or 0 if it's `None`. The explicit `is not None` matters: `bound or 0` would also work for 0, but `is not None` states the intent precisely.
- **L210**: DEBUG log.


#### Lines 212–221

````python
212          # Auto-inject the assessment version that drove this draft (set by the
213          # drafting orchestration via set_current_assessment_prompt_version), so
214          # a v2-assessment draft and a human-assessment draft for the same
215          # incident/stage are distinguishable and kept as separate rows.
216          if table == "template_details" and "assessment_prompt_version" not in payload:
217              _apv = get_current_assessment_prompt_version()
218              if _apv is not None:
219                  payload["assessment_prompt_version"] = _apv
220                  logger.debug(f"Auto-injected assessment_prompt_version={_apv} for template_details")
221
````

- **L212–215**: comment: record which assessment version drove this draft, so a draft based on the v2 assessment and one based on the human assessment for the same incident and stage are separate rows. `assessment_prompt_version` is part of the `template_details` upsert key.
- **L216–220**: for `template_details` writes without an explicit value, read the context variable set by `drafting_service`. Only inject it if it's not `None`. `_apv` is just a short local name.


#### Lines 222–229

````python
222          # reportability_assessments has no confidence_* columns - confidence is
223          # for evaluation/tuning only (see evaluate_reportability tool output).
224          # Strip it defensively in case the agent includes it in the payload.
225          if table == "reportability_assessments":
226              for key in [k for k in payload if k.startswith("confidence_")]:
227                  logger.debug(f"Stripping non-persisted field '{key}' from reportability_assessments payload")
228                  payload.pop(key)
229
````

- **L222–224**: comment: the assessment table has no `confidence_*` columns (confidence is only for evaluation), so remove them in case the LLM included them. Otherwise the INSERT would fail with "column does not exist".
- **L225–228**: `[k for k in payload if k.startswith("confidence_")]` builds a *separate list* of keys first. Deleting keys from a dict while looping over the dict itself would raise `RuntimeError: dictionary changed size during iteration`. `payload.pop(key)` then removes each one.


#### Lines 230–246

````python
230          # Validate required fields for reportability_assessments
231          if table == "reportability_assessments":
232              required_fields = [
233                  "incident_number",
234                  "reportability_flag",
235                  "data_system_impact",
236                  "operational_impact",
237                  "financial_reputational_impact",
238                  "systemic_risk",
239                  "provided_by"
240              ]
241              missing = [f for f in required_fields if f not in payload or not payload[f]]
242              if missing:
243                  error_msg = f"Missing required fields for reportability_assessments: {missing}. You MUST include all 4 FRB impact as⟦CUT⟧
244                  logger.error(error_msg)
245                  raise ValueError(error_msg)
246
````

- **L231–240**: for assessments, define the fields that must be present: incident number, the flag, all 4 FRB answers, and `provided_by`.
- **L241**: list comprehension finding fields that are missing *or empty* (`not payload[f]` is True for `None`, `""`, etc.).
- **L242–245**: if any are missing, build an error message telling the LLM it must include all 4 FRB assessments (the end of L243 was cut off in the screenshots), log it, and `raise ValueError`. In an agent loop, the error text goes back to the LLM so it can retry with a complete payload.


#### Lines 247–256

````python
247          client = get_client()
248          record_id = client.db_write(table, payload)
249          logger.info(f"db_write: table={table}, record_id={record_id}")
250          log_tool_call("db_write", {"table": table, "payload": payload}, outputs=f"Record ID: {record_id}")
251          return record_id
252      except Exception as e:
253          logger.error(f"db_write failed: {e}")
254          log_tool_call("db_write", {"table": table, "payload": payload}, error=str(e))
255          raise
256
````

- **L247–248**: performs the write through `PostgresClient.db_write`; it returns the record id.
- **L249–251**: logs, writes the tool log (including the full payload, truncated to 500 chars by `log_tool_call`), and returns the id.
- **L252–255**: on any error: log, tool-log the error, re-raise.


---

## 27. `backend/tools/assessment_tools.py`

*339 lines · complete*

This file holds the assessment-side logic that decides **whether an incident is reportable to the regulator**. It has three pieces. `compute_assessment_revision` deterministically works out which "revision" number the next assessment row should get (bumped when 1LOD/2LOD feedback hasn't been addressed yet). `ReportabilityAssessment` is a Pydantic schema forcing the LLM to answer the 4 FRB (Federal Reserve Board) criteria, each with a confidence level. `evaluate_reportability` is a LangChain `@tool` that builds the prompt from the incident plus optional context, makes one structured LLM call, then applies a **rule** (any criterion "Yes" means reportable) to set `reportability_flag`. All three are called by `backend/services/assessment_service.py` (`run_single_assessment` and helpers), which then saves the result through `data_tools.db_write`.


#### Lines 1–11

````python
 1  """Assessment-specific tools for evaluating regulatory reportability."""
 2
 3  from typing import Any, Dict, Literal, Optional, Tuple
 4  from langchain_core.tools import tool
 5  from pydantic import BaseModel, Field
 6  from backend.utils.llm_service import LLMService
 7  from backend.utils.postgres_client import get_client
 8  from backend.logger import get_logger, log_tool_call
 9
10  logger = get_logger(__name__)
11
````

- **L1**: module docstring.
- **L3**: typing helpers. `Literal["High","Medium","Low"]` (used later) means "only exactly one of these strings"; `Tuple[int, Optional[str], Optional[str]]` describes a 3-item tuple return.
- **L4**: `tool` is LangChain's decorator that wraps a function as a *Tool*: an object with a name, a description (taken from the docstring) and an argument schema (built from the type hints), which an LLM agent can call. Tools are invoked with `.invoke({...args...})`.
- **L5**: Pydantic `BaseModel` (a class whose fields are typed and validated) and `Field` (adds metadata such as a `description` to a field).
- **L6**: `LLMService` is the project's wrapper around the OpenAI-compatible chat API (it handles auth tokens and model names).
- **L7**: `get_client()` returns the shared Postgres client.
- **L8**: `get_logger` for normal logs; `log_tool_call` writes a JSON line to the dedicated `tools` logger recording each tool's inputs, outputs (truncated to 500 chars) or error.
- **L10**: module logger.


#### Lines 12–25

````python
12
13  def compute_assessment_revision(incident_number: str, prompt_version_label: str) -> Tuple[int, Optional[str], Optional[str]]:
14      """Deterministically decide the revision number for the assessment agent's
15      NEXT write to reportability_assessments, and surface any unincorporated
16      1LOD/2LOD feedback text — so neither has to be worked out by the LLM.
17
18      Called by the orchestration (main._run_single_assessment) BEFORE the
19      agent runs. The result's revision is bound via
20      data_tools.set_current_assessment_revision so db_write picks it up
21      automatically; the agent itself never sees or sets `revision`, and it
22      doesn't need to compare timestamps either — it just reads
23      lod1_response/lod2_r_and_c off the existing assessment row (via its own
24      db_query calls) like any other field. If they're empty, there's nothing
25      to incorporate.
````

- **L13**: `compute_assessment_revision(incident_number, prompt_version_label)` returns a tuple `(next_revision, lod1_response, lod2_r_and_c)`.
- **L14–16**: it decides the revision number for the *next* write to `reportability_assessments` and surfaces any reviewer feedback that hasn't been incorporated yet, so the LLM doesn't have to work either of these out.
- **L18–25**: it runs *before* the assessment; the revision is stored in a context variable via `data_tools.set_current_assessment_revision`, and `db_write` reads it automatically. The agent never sets `revision` itself.
- Note: the docstring says the caller is `main._run_single_assessment`, but that code has since moved. The real caller is `backend/services/assessment_service.py::run_single_assessment` (its own docstring says it was "Moved out of backend/main.py"), so the reference is stale.


#### Lines 26–41

````python
26
27      Args:
28          incident_number: Incident to check.
29          prompt_version_label: Active ASSESSMENT_PROMPT_VERSION_LABEL (e.g. 'v4').
30
31      Returns:
32          (next_revision, lod1_response, lod2_r_and_c)
33          - next_revision: existing max revision for (incident_number,
34            prompt_version_label), or that max + 1 if 1LOD/2LOD provided
35            feedback newer than the latest row's created_at (i.e. feedback the
36            agent hasn't incorporated into an assessment yet). 0 if there's no
37            prior row at all.
38          - lod1_response / lod2_r_and_c: the feedback text carried on the
39            latest row when it's newer than that row (so it's worth
40            re-assessing), else None/None.
41      """
````

- **L27–29**: arguments: the incident and the active prompt-version label (e.g. `'v10'`, derived from `prompt_config.ASSESSMENT_AGENT_PROMPT_VERSION`).
- **L31–40**: return value:
  - `next_revision` is the current highest revision for this incident and prompt version, or that number + 1 if reviewers gave feedback *after* the latest row was created (so a fresh, feedback-aware row is needed). It is 0 if no row exists.
  - The two feedback strings are returned only in that "unincorporated feedback" case; otherwise `None, None`.
- Why revisions matter: `reportability_assessments` is upserted on the key `(incident_number, prompt_version, revision)` (see `REVISIONED_TABLES` in `postgres_client.py`). The same revision number overwrites the existing row; a new revision inserts a new row, which keeps the original assessment as history.


#### Lines 42–48

````python
42      client = get_client()
43      rows = client.db_query(
44          "reportability_assessments",
45          {"incident_number": incident_number, "prompt_version": prompt_version_label},
46      )
47      if not rows:
48          return 0, None, None
````

- **L42**: gets the DB client.
- **L43–46**: reads all assessment rows for this incident *and* this prompt version. The filter dict becomes parameterised SQL (`WHERE incident_number = %s AND prompt_version = %s`), which avoids SQL injection.
- **L47–48**: no prior row means this is the first assessment: revision 0, no feedback.


#### Lines 49–65

````python
49
50      # db_query orders REVISIONED_TABLES rows by updated_at DESC, so rows[0]
51      # is the most recently written row for this incident/prompt_version.
52      latest = rows[0]
53      max_revision = max((row.get("revision") or 0) for row in rows)
54
55      feedback_provided_at = latest.get("feedback_provided_at")
56      created_at = latest.get("created_at")
57      has_feedback_text = bool(latest.get("lod1_response") or latest.get("lod2_r_and_c"))
58      feedback_is_unincorporated = bool(
59          has_feedback_text and feedback_provided_at and created_at and feedback_provided_at > created_at
60      )
61
62      if feedback_is_unincorporated:
63          return max_revision + 1, latest.get("lod1_response"), latest.get("lod2_r_and_c")
64      return max_revision, None, None
65
````

- **L50–52**: `PostgresClient.db_query` adds `ORDER BY updated_at DESC` for this table, so `rows[0]` is the most recently written row.
- **L53**: `max(... for row in rows)` uses a *generator expression* (like a list comprehension but lazy) to find the highest revision; `row.get("revision") or 0` treats NULL as 0.
- **L55–56**: timestamps for when feedback was given and when the latest row was created. psycopg returns these as Python `datetime` objects, so they compare with `>`.
- **L57**: `has_feedback_text` is True if either the 1LOD response or the 2LOD R&C text is non-empty.
- **L58–60**: feedback is "unincorporated" only if there is feedback text, both timestamps exist, and the feedback is newer than the row. The `and` chain short-circuits, so a `None` timestamp never reaches the `>` comparison (which would raise a `TypeError`). `bool(...)` makes the result a clean True/False.
- **L62–63**: if so, bump the revision and return the feedback text so it can go into the prompt.
- **L64**: otherwise reuse the current max revision. The re-run *overwrites* that row in place instead of creating a new one.


#### Lines 66–85

````python
66
67  def _get_assessment_system_prompt() -> str:
68      """Load the FRB criteria from the active assessment agent prompt version.
69
70      The evaluate_reportability tool makes its own LLM call (separate from
71      the agent's conversation), so we must explicitly pass the FRB criteria
72      as a system message. This helper imports the active prompt version
73      and extracts its SYSTEM_PROMPT.
74      """
75      try:
76          from backend.evaluation.prompt_iterations.loader import load_assessment_prompt_module
77          return load_assessment_prompt_module().SYSTEM_PROMPT
78      except Exception as e:
79          logger.warning(f"Could not load active prompt version, using fallback: {e}")
80          return (
81              "You are a regulatory compliance analyst. Evaluate incidents "
82              "against the 4 FRB reportability criteria: Data/System Impact, "
83              "Operational Impact, Financial/Reputational Impact, Systemic Risk."
84          )
85
````

- **L67**: `_get_assessment_system_prompt()` returns the system prompt (the instructions and FRB criteria) for the LLM call.
- **L68–74**: docstring: `evaluate_reportability` makes its own separate LLM call, so it must pass the full criteria explicitly as a system message. They come from the *active prompt version* module (e.g. `assessment_agent_prompt_v10.py`).
- **L75–77**: the import is done *inside* the function (a "lazy import"), typically to avoid circular imports or heavy loading at module import time. `load_assessment_prompt_module()` dynamically imports the module named in `prompt_config.py`, and `.SYSTEM_PROMPT` reads its constant.
- **L78–84**: if loading fails, it logs a warning and falls back to a short generic prompt listing the 4 FRB criteria: Data/System Impact, Operational Impact, Financial/Reputational Impact, Systemic Risk. The f-string `f"...{e}"` embeds the error message. The fallback keeps the system running but with much weaker guidance.


#### Lines 86–96

````python
86
87  class ReportabilityAssessment(BaseModel):
88      """Structured output for reportability assessment.
89
90      Field ordering is load-bearing: each assessment text comes before its
91      confidence so the LLM writes its reasoning before deciding confidence
92      (same pattern as grm-taxonomy).
93
94      The reportability_flag is automatically determined by the system based
95      on these 4 assessments - the agent does not set it.
96      """
````

- **L87**: `class ReportabilityAssessment(BaseModel)` is the *structured output schema*. The LLM is forced to reply with JSON matching these fields, and the OpenAI SDK parses it into an instance of this class.
- **L90–92**: "field ordering is load-bearing". LLMs generate left to right, so putting each explanation *before* its confidence makes the model reason first and then judge confidence (a chain-of-thought trick, also used in another internal project, "grm-taxonomy").
- **L94–95**: there is deliberately no `reportability_flag` field; the code computes it with a rule later.


#### Lines 97–104

````python
 97      data_system_impact: str = Field(description="Assessment of data/system impact with Yes/No and detailed explanation")
 98      confidence_data_system_impact: Literal["High", "Medium", "Low"] = Field(
 99          description="High: evidence clearly supports the Yes/No answer. Medium: requires interpretation but logic is sound. Low: amb⟦CUT⟧
100      )
101      operational_impact: str = Field(description="Assessment of operational impact with Yes/No and detailed explanation")
102      confidence_operational_impact: Literal["High", "Medium", "Low"] = Field(
103          description="High: evidence clearly supports the Yes/No answer. Medium: requires interpretation but logic is sound. Low: amb⟦CUT⟧
104      )
````

- **L97**: `data_system_impact: str = Field(description=...)`. The `description` is sent to the LLM as part of the JSON schema, so it acts as a mini-instruction: answer Yes/No plus an explanation.
- **L98–100**: `confidence_data_system_impact: Literal["High","Medium","Low"]` restricts the value to those three strings. The description defines High (evidence clearly supports), Medium (needs interpretation), Low (ambiguous...). The end of each description string ran off the screen (⟦CUT⟧), so the full wording of "Low" wasn't visible; it presumably continues "ambiguous/insufficient evidence" (inference, matching the prompt text at L253).
- **L101–104**: same pair for operational impact.


#### Lines 105–113

````python
105      financial_reputational_impact: str = Field(description="Assessment of financial/reputational impact with Yes/No and detailed exp⟦CUT⟧
106      confidence_financial_reputational_impact: Literal["High", "Medium", "Low"] = Field(
107          description="High: evidence clearly supports the Yes/No answer. Medium: requires interpretation but logic is sound. Low: amb⟦CUT⟧
108      )
109      systemic_risk: str = Field(description="Assessment of systemic risk with Yes/No and detailed explanation")
110      confidence_systemic_risk: Literal["High", "Medium", "Low"] = Field(
111          description="High: evidence clearly supports the Yes/No answer. Medium: requires interpretation but logic is sound. Low: amb⟦CUT⟧
112      )
113
````

- **L105–108**: same pair for financial/reputational impact (the end of L105's and L107's descriptions was cut off in the screenshots).
- **L109–112**: same pair for systemic risk.
- So the model returns 8 fields: 4 assessments and 4 confidences.


#### Lines 114–123

````python
114
115  @tool
116  def evaluate_reportability(
117      incident_data: Dict[str, Any],
118      mimt_draft_context: Optional[Dict[str, Any]] = None,
119      cuso_context: Optional[list] = None,
120      regulatory_context: Optional[list] = None,
121      lod_feedback_context: Optional[str] = None,
122      similar_incidents_context: Optional[list] = None,
123  ) -> Dict[str, str]:
````

- **L115**: `@tool` is a *decorator*: syntax that passes the function below into `tool(...)` and replaces it with the result. Here it becomes a LangChain `StructuredTool` named `evaluate_reportability`. Callers use `evaluate_reportability.invoke({"incident_data": ..., ...})`, which is exactly what `assessment_service._evaluate_and_persist_assessment` does.
- **L116–122**: parameters. Only `incident_data` (the incident row as a dict) is required. The others are optional extra context, each defaulting to `None`:
  - `mimt_draft_context`: the human MIMT team's draft.
  - `cuso_context`: CUSO application reference rows (criticality, Crown Jewel status, etc.).
  - `regulatory_context`: regulator requirement rows.
  - `lod_feedback_context`: reviewer feedback text.
  - `similar_incidents_context`: past incidents with their flags.
- **L123**: annotated to return `Dict[str, str]`.


#### Lines 124–155

````python
124      """Evaluate incident reportability using FRB criteria with LLM reasoning.
125
126      This is the core assessment tool that applies regulatory judgment to determine
127      if an incident is reportable to regulators. It uses an LLM to evaluate the
128      4 FRB reportability questions and provide detailed rationale.
129
130      Args:
131          incident_data: Dict with incident details. Should include:
132              - incident_number: Incident ID
133              - priority: Priority level
134              - legal_entity: Legal entity name
135              - incident_description: Detailed description
136              - business_impact: Business impact description
137              - user_impact: User impact description
138              - client_impact: Client impact description
139              - incident_type: Type of incident
140              - impacted_applications: Affected systems
141          mimt_draft_context: Optional human MIMT team draft row (title,
142              current_status, description, impact, management_actions,
143              root_cause) - may contain refinements not present in the raw
144              incident record.
145          cuso_context: Optional list of matching CUSO Applications reference
146              rows (criticality, data classification, Crown Jewel status, etc.)
147              for the incident's impacted application(s).
148          regulatory_context: Optional list of Regulatory Requirements
149              reference rows (region/legal-entity reporting thresholds).
150          lod_feedback_context: Optional unincorporated 1LOD/2LOD reviewer
151              feedback text to directly address in this (re-)assessment.
152          similar_incidents_context: Optional list of past incidents with the
153              same legal_entity/priority (incident_number + reportability_flag)
154              for decision consistency.
155
````

- **L124–128**: summary: this is the core tool applying regulatory judgment with an LLM over the 4 FRB questions.
- **L130–154**: `Args` section describing each parameter. The expected keys of `incident_data` are listed (incident number, priority, legal entity, description, business/user/client impact, type, impacted applications). The context parameters are described in plain words; for example, `similar_incidents_context` is past incidents with the same legal entity and priority, to keep decisions consistent. Because of `@tool`, this docstring is also the description an LLM agent would see.


#### Lines 156–176

````python
156      Returns:
157          Dict with assessment results:
158              - reportability_flag: "Yes" or "No"
159              - data_system_impact: Assessment of data/system impact
160              - operational_impact: Assessment of operational impact
161              - financial_reputational_impact: Assessment of financial/reputational impact
162              - systemic_risk: Assessment of systemic risk
163              - rationale: Overall reasoning for the determination
164
165      Example:
166          result = evaluate_reportability({
167              "incident_number": "INC0575428",
168              "priority": "1 - Significant",
169              "legal_entity": "City National Bank (CNB)",
170              "incident_description": "Power outage at DC03 caused 4-hour outage...",
171              "business_impact": "Multiple applications unavailable for 4 hours",
172              "user_impact": "Internal users unable to access systems",
173              "client_impact": "External clients experienced service degradation"
174          })
175          # Returns: {"reportability_flag": "Yes", "data_system_impact": "...", ...}
176      """
````

- **L156–163**: `Returns`: a dict with `reportability_flag`, the 4 assessments, and `rationale`. (It also returns the 4 `confidence_*` keys, which the docstring doesn't mention.)
- **L165–175**: usage example with a sample CNB (City National Bank) P1 power-outage incident. The example calls it like a plain function; in real code it's called with `.invoke({...})`.


#### Lines 177–183

````python
177      try:
178          llm = LLMService()
179
180          # Load the active prompt version's FRB criteria as a system message
181          # so the tool's own LLM call has the full evaluation guidance.
182          system_prompt = _get_assessment_system_prompt()
183
````

- **L177**: everything is inside `try` so failures can be logged before being re-raised (see L331).
- **L178**: creates an `LLMService` instance (sets up the API client).
- **L180–182**: loads the system prompt from the active prompt version.


#### Lines 184–204

````python
184          prompt_parts = [f"""Evaluate this incident against the 4 FRB reportability questions using the criteria provided in your sys⟦CUT⟧
185
186  **Incident Details:**
187  - Incident Number: {incident_data.get('incident_number', 'N/A')}
188  - Priority: {incident_data.get('priority', 'N/A')}
189  - Legal Entity: {incident_data.get('legal_entity', 'N/A')}
190  - Incident Type: {incident_data.get('incident_type', 'N/A')}
191  - Impacted Applications: {incident_data.get('impacted_applications', 'N/A')}
192
193  **Description:**
194  {incident_data.get('incident_description', 'N/A')}
195
196  **Impact Analysis:**
197  - Business Impact: {incident_data.get('business_impact', 'N/A')}
198  - User Impact: {incident_data.get('user_impact', 'N/A')}
199  - Client Impact: {incident_data.get('client_impact', 'N/A')}
200
201  **Timing:**
202  - Technology Impact Start: {incident_data.get('technology_impact_start_datetime', 'N/A')}
203  - Business Impact Start: {incident_data.get('business_impact_start_datetime', 'N/A')}"""]
204
````

- **L184**: starts `prompt_parts`, a list of text chunks to be joined at the end. The first chunk is a triple-quoted *f-string* (`f"""..."""`): a multi-line string where `{expression}` is replaced by its value. The rest of L184 (the instruction sentence) ran off the screen; it begins "Evaluate this incident against the 4 FRB reportability questions using the criteria provided in your sys[tem prompt]".
- **L186–191**: an "Incident Details" section in Markdown. Each `incident_data.get('key', 'N/A')` inserts the field, or `'N/A'` if missing, so the prompt never crashes on missing data.
- **L193–194**: the full incident description.
- **L196–199**: business, user and client impact.
- **L201–203**: timing: technology impact start and business impact start datetimes. Duration is often key to thresholds. L203's `"""]` closes the string and the list.


#### Lines 205–214

````python
205          if mimt_draft_context:
206              prompt_parts.append(f"""
207  **MIMT Team Draft (human-authored, additional context - may contain refinements not in the raw incident record):**
208  - Title: {mimt_draft_context.get('title', 'N/A')}
209  - Current Status: {mimt_draft_context.get('current_status', 'N/A')}
210  - Description: {mimt_draft_context.get('description', 'N/A')}
211  - Impact: {mimt_draft_context.get('impact', 'N/A')}
212  - Management Actions: {mimt_draft_context.get('management_actions', 'N/A')}
213  - Root Cause: {mimt_draft_context.get('root_cause', 'N/A')}""")
214
````

- **L205**: `if mimt_draft_context:` is truthy only if a non-empty dict was passed.
- **L206–213**: appends a section with the human MIMT draft's title, status, description, impact, management actions and root cause. The label tells the model it's human-authored and may contain refinements.
- **L214**: not visible in the screenshots; most likely a blank line separating the `if` blocks (inference).


#### Lines 215–229

````python
215          if cuso_context:
216              prompt_parts.append(
217                  "\n**CUSO Application Reference Context** (describes the application in general - "
218                  "do not treat a criticality/Crown Jewel label alone as evidence this incident meets "
219                  "a threshold; check what was ACTUALLY impacted):\n" + str(cuso_context)
220              )
221
222          if regulatory_context:
223              prompt_parts.append(
224                  "\n**Regulatory Requirements Reference** (all known regulator/region/legal-entity "
225                  "requirements - identify any row(s) whose Region/Location/Country or legal entity "
226                  "scope matches this incident, and note any applicable reporting threshold):\n"
227                  + str(regulatory_context)
228              )
229
````

- **L215–220**: if CUSO reference data exists, appends it. `str(cuso_context)` dumps the Python list as text. The instruction warns the model not to treat a "critical" or "Crown Jewel" label alone as proof that a threshold is met, and to check what was actually impacted. This guards against a known failure mode (over-flagging critical apps).
- Python joins adjacent string literals automatically (`"a " "b"` becomes `"a b"`), which is how these long messages are split across lines.
- **L222–228**: appends the full regulatory requirements table and tells the model to find rows matching the incident's region or legal entity and note any thresholds.


#### Lines 230–241

````python
230          if lod_feedback_context:
231              prompt_parts.append(
232                  f"\n**Reviewer Feedback to Address (1LOD/2LOD):**\n{lod_feedback_context}\n"
233                  "This is a re-assessment - your evaluation MUST directly address this feedback."
234              )
235
236          if similar_incidents_context:
237              prompt_parts.append(
238                  "\n**Similar Past Incidents (same legal_entity/priority, for decision consistency):**\n"
239                  + str(similar_incidents_context)
240              )
241
````

- **L230–234**: if there is reviewer feedback (built in `assessment_service` as `"1LOD feedback: ...\n2LOD feedback: ..."`), it's added with a firm instruction that this is a re-assessment and must address the feedback.
- **L236–240**: appends similar past incidents (incident number + flag), for consistency with earlier decisions.


#### Lines 242–263

````python
242          prompt_parts.append("""
243  Evaluate each of the 4 FRB criteria independently. Start each answer with "Yes" or "No", followed by a detailed explanation. Then as⟦CUT⟧
244
245  **Important:** You only need to assess the 4 criteria below. The system will automatically determine overall reportability based on⟦CUT⟧
246
247  **Confidence Calibration:**
248  Confidence is a CONCLUSION drawn from your reasoning, not a first impression.
249  Write your Yes/No assessment and explanation FIRST, then assign confidence based on what you wrote.
250
251  - **High:** The incident data explicitly and unambiguously supports your Yes/No answer. You can point to specific evidence (stated d⟦CUT⟧
252  - **Medium:** The evidence supports your answer but requires interpretation or inference. Examples: duration is close to a threshold⟦CUT⟧
253  - **Low:** The incident data is insufficient, ambiguous, or contradictory for this criterion. You made a judgment call but acknowled⟦CUT⟧
254
255  Provide your assessment in this EXACT format (assessment + confidence for each):
256
257  1. Data/System Impact: [Yes/No + detailed explanation] → Confidence: [High/Medium/Low]
258  2. Operational Impact: [Yes/No + detailed explanation] → Confidence: [High/Medium/Low]
259  3. Financial/Reputational Impact: [Yes/No + detailed explanation] → Confidence: [High/Medium/Low]
260  4. Systemic Risk: [Yes/No + detailed explanation] → Confidence: [High/Medium/Low]
261  """)
262          prompt = "\n".join(prompt_parts)
263
````

- **L242–261**: the closing instruction block (a plain triple-quoted string, not an f-string):
  - **L243**: evaluate each of the 4 criteria independently and start each answer with "Yes" or "No" (this matters for the rule at L292). The rest of the line was cut off.
  - **L245**: the model only assesses the criteria; the system decides overall reportability (rest of line cut off).
  - **L247–253**: confidence calibration. Confidence is a conclusion reached *after* writing the reasoning. High means explicit evidence, Medium means interpretation is needed, Low means insufficient or contradictory data. The ends of L251–253 were cut off.
  - **L255–260**: the exact expected answer format, one line per criterion with `→ Confidence: [...]`. With structured output, the actual reply is JSON per the Pydantic schema, so this mostly reinforces the content of each field.
- **L262**: `"\n".join(prompt_parts)` concatenates all chunks with newlines into the final user prompt.


#### Lines 264–277

````python
264          # Invoke LLM with structured output
265          logger.info(f"Evaluating reportability for incident {incident_data.get('incident_number')}")
266
267          # Use structured output to ensure all fields are returned.
268          # Include the active prompt version as a system message so the LLM
269          # has the full FRB criteria, few-shot examples, and evaluation guidance.
270          assessment_obj = llm.chat_structured(
271              messages=[
272                  {"role": "system", "content": system_prompt},
273                  {"role": "user", "content": prompt},
274              ],
275              response_format=ReportabilityAssessment
276          )
277
````

- **L265**: logs which incident is being evaluated.
- **L270–276**: `llm.chat_structured(messages=[...], response_format=ReportabilityAssessment)`. `messages` is the standard chat format: a `system` message (rules/criteria) and a `user` message (this incident). In `LLMService`, passing a Pydantic class uses the OpenAI SDK's `beta.chat.completions.parse`, which forces JSON matching the schema and returns a `ReportabilityAssessment` instance.


#### Lines 278–289

````python
278          # Convert to dict
279          assessment = {
280              "data_system_impact": assessment_obj.data_system_impact,
281              "confidence_data_system_impact": assessment_obj.confidence_data_system_impact,
282              "operational_impact": assessment_obj.operational_impact,
283              "confidence_operational_impact": assessment_obj.confidence_operational_impact,
284              "financial_reputational_impact": assessment_obj.financial_reputational_impact,
285              "confidence_financial_reputational_impact": assessment_obj.confidence_financial_reputational_impact,
286              "systemic_risk": assessment_obj.systemic_risk,
287              "confidence_systemic_risk": assessment_obj.confidence_systemic_risk,
288          }
289
````

- **L279–288**: converts the Pydantic object into a plain dict, copying the 8 fields by attribute access (`assessment_obj.data_system_impact`, ...). Plain dicts are easier to serialize and pass through LangChain. (`assessment_obj.model_dump()` would do the same in one call.)


#### Lines 290–298

````python
290          # RULE-BASED: Automatically determine reportability flag
291          # If ANY of the 4 criteria starts with "Yes", the incident is reportable
292          any_yes = any(
293              str(assessment[field]).strip().lower().startswith('yes')
294              for field in ['data_system_impact', 'operational_impact', 'financial_reputational_impact', 'systemic_risk']
295          )
296
297          assessment['reportability_flag'] = 'Yes' if any_yes else 'No'
298
````

- **L290–291**: the reportability decision is **rule-based, not left to the LLM**: if *any* of the 4 criteria starts with "Yes", the incident is reportable.
- **L292–295**: `any(...)` returns True if at least one item of the generator is truthy. For each of the 4 field names, `str(...).strip().lower().startswith('yes')` normalises the text (removes surrounding whitespace, lowercases) and checks the prefix.
- **L297**: sets `reportability_flag` to `'Yes'` or `'No'`.
- Note: the check relies on the answer *literally starting* with "yes". An answer like "**Yes** - ..." (Markdown bold) or "Answer: Yes" would be counted as No. The prompt (L243) asks the model to start with Yes/No, so this normally works, but it's a fragile point.


#### Lines 299–310

````python
299          # Generate rationale based on which criteria are Yes
300          yes_criteria = [
301              field.replace('_', ' ').title()
302              for field in ['data_system_impact', 'operational_impact', 'financial_reputational_impact', 'systemic_risk']
303              if str(assessment[field]).strip().lower().startswith('yes')
304          ]
305
306          if yes_criteria:
307              assessment['rationale'] = f"REPORTABLE: The following FRB criteria are met: {', '.join(yes_criteria)}."
308          else:
309              assessment['rationale'] = "NOT REPORTABLE: None of the 4 FRB criteria are met."
310
````

- **L300–304**: a *list comprehension with a filter* (`[expr for x in items if cond]`) collecting the names of criteria that are "Yes". `field.replace('_',' ').title()` turns `operational_impact` into `"Operational Impact"`.
- **L306–309**: builds a human-readable `rationale`: `"REPORTABLE: The following FRB criteria are met: ..."` or `"NOT REPORTABLE: None of the 4 FRB criteria are met."`. `', '.join(list)` makes a comma-separated string. `assessment_service` later saves this into the `notes` column.


#### Lines 311–322

````python
311          confidences = [
312              assessment['confidence_data_system_impact'],
313              assessment['confidence_operational_impact'],
314              assessment['confidence_financial_reputational_impact'],
315              assessment['confidence_systemic_risk'],
316          ]
317          logger.info(
318              f"Assessment complete: {incident_data.get('incident_number')} -> "
319              f"{assessment['reportability_flag']} (Rule-based determination), "
320              f"confidence=[{', '.join(confidences)}]"
321          )
322
````

- **L311–316**: collects the 4 confidence values into a list.
- **L317–321**: logs the result, e.g. `INC... -> Yes (Rule-based determination), confidence=[High, Medium, ...]`. Adjacent f-strings inside the parentheses are joined into one message.
- The confidences are not stored in `reportability_assessments` (`db_write` strips `confidence_*` keys); they're used for logs and evaluation/tuning.


#### Lines 323–330

````python
323          log_tool_call(
324              "evaluate_reportability",
325              {"incident_number": incident_data.get('incident_number')},
326              outputs=f"Flag: {assessment['reportability_flag']}, confidence=[{', '.join(confidences)}]"
327          )
328
329          return assessment
330
````

- **L323–327**: records the tool call in the structured `tools` log: tool name, minimal inputs (only the incident number, to avoid logging the whole incident), and a short output summary.
- **L329**: returns the assessment dict to the caller.


#### Lines 331–339

````python
331      except Exception as e:
332          logger.error(f"evaluate_reportability failed: {e}")
333          log_tool_call(
334              "evaluate_reportability",
335              {"incident_number": incident_data.get('incident_number')},
336              error=str(e)
337          )
338          raise
339
````

- **L331**: catches any error (LLM timeout, schema parse failure, etc.).
- **L332**: logs it to the normal log.
- **L333–337**: records the failure in the tools log with `error=str(e)`.
- **L338**: a bare `raise` re-raises the *same* exception with its original traceback, so the caller (`assessment_service`, which has retry logic) knows it failed. This "log then re-raise" pattern is used in every tool in this package.


---

## 28. `backend/tools/drafting_tools.py`

*487 lines · complete*

This module holds the **drafting-stage tools**: the code that actually builds a regulatory notification template for an incident that has already been judged reportable. It splits the work in two:
- `populate_template_fields` is a plain Python function that copies the "factual" fields (date, type, legal entity, priority, PIR, executive lead) straight from the `incidents` table into a `template_details` row. No LLM is involved.
- `generate_template_content` is a LangChain `@tool` that makes its own structured LLM call to write the six "prose" fields (title, current status, description, impact, management actions, root cause).

`get_template_schema` reads `template_schema.json` so the prompt can include each field's definition and the compliance guidelines. In the current flow, `backend/services/drafting_service.py` calls all three directly: first `populate_template_fields`, then `get_template_schema.invoke({})`, then `generate_template_content.invoke({...})`, and finally writes the result. `backend/agents/drafting_agent.py` also lists the two `@tool` functions in its tool list. The module relies on `LLMService` (the LLM gateway), `get_client()` (Postgres) and the logging helpers.


#### Lines 1–17

````python
 1  """Drafting-specific tools for generating regulatory notification templates."""
 2
 3  import json
 4  import re
 5  from datetime import date, datetime
 6  from pathlib import Path
 7  from typing import Dict, Any, Optional
 8  from langchain_core.tools import tool
 9  from pydantic import BaseModel, Field
10  from backend.utils.llm_service import LLMService
11  from backend.utils.postgres_client import get_client
12  from backend.logger import get_logger, log_tool_call
13
14  logger = get_logger(__name__)
15
16  SCHEMA_PATH = Path(__file__).parent / "template_schema.json"
17
````

- **L1** The module docstring, a one-line description of the file.
- **L3–7** Standard-library imports. `json` reads the schema file. `re` provides the regular expressions used to parse priorities and dates. `date`/`datetime` are used to return real date objects. `Path` builds the schema file path. `Dict, Any, Optional` are **type hints**: annotations such as `Dict[str, Any]` that document the expected types. Python does not enforce them at runtime, but LangChain and Pydantic read them.
- **L8** `tool` is LangChain's decorator. Putting `@tool` above a function turns it into a `StructuredTool` object. The function's docstring becomes the description the LLM sees, and its type hints become the argument schema. You call such a tool with `.invoke({...})`.
- **L9** `BaseModel` and `Field` come from **Pydantic**. A Pydantic model is a class whose attributes have declared types and get validated. Here it defines the exact JSON shape the LLM must return.
- **L10** `LLMService` (from `backend/utils/llm_service.py`) is the team's wrapper around the corporate OpenAI-compatible gateway, including OAuth.
- **L11** `get_client` returns the shared `PostgresClient` singleton, used for DB reads and writes.
- **L12** `get_logger` gives a normal Python logger. `log_tool_call` writes a structured JSON line describing each tool invocation (inputs, outputs or error) to the tool log, which is useful for auditing what the agents did.
- **L14** Creates a module-level logger named after the module (`__name__` = `backend.tools.drafting_tools`).
- **L16** `SCHEMA_PATH` is the absolute path to `template_schema.json`, which sits in the same folder as this file. `Path(__file__).parent` means "the directory this .py file lives in", and the `/` operator joins path parts.


#### Lines 18–39

````python
18
19  def _get_drafting_system_prompt() -> str:
20      """Load the style guide from the active drafting agent prompt version.
21
22      The generate_template_content tool makes its own LLM call (separate from
23      the agent's conversation), so we must explicitly pass the style guide
24      (word count target, neutral-language rules, field formats, etc.) as a
25      system message. This helper imports the active prompt version and
26      extracts its SYSTEM_PROMPT, mirroring
27      assessment_tools._get_assessment_system_prompt().
28      """
29      try:
30          from backend.evaluation.prompt_iterations.loader import load_drafting_prompt_module
31          return load_drafting_prompt_module().SYSTEM_PROMPT
32      except Exception as e:
33          logger.warning(f"Could not load active prompt version, using fallback: {e}")
34          return (
35              "You are a regulatory notification author for a financial institution. "
36              "Draft clear, compliant, factual notifications for FRB/OCC submission "
37              "based strictly on the incident data and assessment provided."
38          )
39
````

- **L19** A private helper (the leading `_` is the Python convention for "internal use") that returns the drafting agent's system prompt as a string.
- **L20–28** The docstring explains why it exists. `generate_template_content` makes a **separate** LLM call that is outside the agent's own conversation, so it would otherwise not see the style rules (word counts, neutral language, field formats). This helper fetches them so they can be sent as the system message. It mirrors the equivalent helper in `assessment_tools.py`.
- **L29–31** Inside `try`, it imports `load_drafting_prompt_module` lazily, i.e. inside the function rather than at the top of the file. This avoids loading the evaluation package at import time and avoids possible circular imports. That loader (in `backend/evaluation/prompt_iterations/loader.py`) picks the module for the currently active `DRAFTING_AGENT_PROMPT_VERSION` (e.g. `drafting_agent_prompt_v0`). The helper then returns that module's `SYSTEM_PROMPT` constant. This is how the team switches prompt versions without code changes.
- **L32–38** If anything fails (missing module, typo in config), it logs a warning and returns a short hard-coded fallback system prompt instead of crashing. The fallback describes the task as drafting FRB/OCC notifications. The parenthesised adjacent strings on L35–37 are joined automatically by Python into one string.


#### Lines 40–58

````python
40
41  class TemplateContent(BaseModel):
42      """Structured output for the LLM-generated template fields.
43
44      Only the fields that require synthesis/authoring by the LLM. All other
45      template fields (date_of_occurrence, type, legal_entity, priority_rating,
46      pir, incident_executive_lead_name/contact) are populated
47      deterministically by the populate_template_fields tool. root_cause is
48      LLM-authored (see field below) rather than copied verbatim from
49      incidents.root_cause.
50      """
51      title: str = Field(description="Concise notification title summarizing the incident,⟦?⟧ written in formal regulatory language (not⟦CUT⟧
52      current_status: str = Field(description="Current operational status of the incident at time of notification, including resolutio⟦CUT⟧
53      description: str = Field(description="Full narrative description for regulatory submission including what happened, timeline, sc⟦CUT⟧
54      impact: str = Field(description="Comprehensive impact assessment covering financial, operational, and client impact as consolida⟦CUT⟧
55      management_actions: str = Field(default="", description="Actions taken by management to resolve the incident and prevent recurre⟦CUT⟧
56      root_cause: str = Field(default="", description="Root cause analysis or preliminary findings, authored from incident details. Us⟦CUT⟧
57
58
````

- **L41** `TemplateContent` is a Pydantic model that describes the structured output expected from the LLM. It is passed as `response_format` (L455), and the OpenAI SDK turns it into a JSON schema the model must follow. The reply is then parsed back into an instance of this class.
- **L42–50** The docstring explains that only the six fields needing "authoring" are here. The other template fields are filled deterministically by `populate_template_fields`. `root_cause` is written by the LLM rather than copied from `incidents.root_cause`.
- **L51–54** `title`, `current_status`, `description` and `impact` are required `str` fields. They have no default, so the model must supply them. `Field(description=...)` attaches a per-field instruction that ends up in the JSON schema the model sees. **Every one of these lines ran off the screen (⟦CUT⟧)**, so the end of each description string is not visible. From the visible part: title is formal and concise, current status covers resolution state, description is the full narrative with a timeline, and impact covers financial, operational and client impact. The `⟦?⟧` on L51 marks an uncertain character reading.
- **L55–56** `management_actions` and `root_cause` have `default=""`. They are optional, so the parse still succeeds if the model leaves them out. Their descriptions are also cut off on screen. The visible part says root cause is "authored from incident details".


#### Lines 59–75

````python
59  def _format_priority(raw_priority: str) -> str:
60      """Transform incidents.priority to template display format.
61
62      '1 - Significant' -> 'P1 – Significant'
63      '2 - High'        -> 'P2 – High'
64
65      Falls back to the raw value prefixed with 'P' if the pattern doesn't
66      match, so an unexpected format fails visibly rather than silently.
67      """
68      if not raw_priority:
69          return "TBC"
70      m = re.match(r"^(\d+)\s*[-–]\s*(.+)$", raw_priority.strip())
71      if m:
72          return f"P{m.group(1)} – {m.group(2).strip()}"
73      return raw_priority
74
75
````

- **L59** Converts a raw priority string from the incidents table into the template's display style.
- **L60–67** Docstring with examples: `'1 - Significant'` becomes `'P1 – Significant'` (note the en-dash `–` in the output).
- **L68–69** An empty or missing priority becomes the placeholder `"TBC"` (to be confirmed), which is used throughout the template.
- **L70** Regex `^(\d+)\s*[-–]\s*(.+)$`: group 1 is the leading number, then a hyphen or en-dash with optional spaces, and group 2 is the rest of the label. `.strip()` removes surrounding whitespace first.
- **L71–72** If it matches, an **f-string** (`f"..."`, which inserts `{expressions}` into the text) builds `P<number> – <label>`.
- **L73** Otherwise the raw value is returned unchanged.
- Note: the docstring (L65–66) says the fallback returns the raw value "prefixed with 'P'", but the code returns it **without** any prefix. The behaviour is harmless, but the docstring is inaccurate.


#### Lines 76–114

````python
 76  def _extract_date(value: Any) -> Optional[date]:
 77      """Extract a date suitable for the PostgreSQL DATE column.
 78
 79      Handles common formats:
 80        '2026-05-14 08:30:00' -> '2026-05-14'
 81        '05/14/2026 08:30 AM' -> '2026-05-14'
 82        'May 14, 2026'        -> '2026-05-14'
 83
 84      Missing or unparseable values become SQL NULL rather than the display
 85      placeholder ``TBC``, which PostgreSQL cannot store in a DATE column.
 86      """
 87      if value is None or value == "":
 88          return None
 89      if isinstance(value, datetime):
 90          return value.date()
 91      if isinstance(value, date):
 92          return value
 93
 94      datetime_str = str(value).strip()
 95      # Try ISO-ish format first (YYYY-MM-DD anywhere in the string)
 96      iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", datetime_str)
 97      if iso_match:
 98          try:
 99              return date.fromisoformat(iso_match.group(1))
100          except ValueError:
101              return None
102      # Try MM/DD/YYYY
103      us_match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", datetime_str)
104      if us_match:
105          try:
106              return date(
107                  int(us_match.group(3)),
108                  int(us_match.group(1)),
109                  int(us_match.group(2)),
110              )
111          except ValueError:
112              return None
113      return None
114
````

- **L76** Takes any value (`Any`) and returns a `date` or `None` (`Optional[date]`). The result goes into the Postgres `DATE` column `date_of_occurrence`.
- **L77–86** The docstring lists the formats it intends to handle. It also explains why missing values become `None`: Postgres can store SQL `NULL` in a DATE column but cannot store the text `"TBC"`.
- **L87–88** `None` or an empty string gives `None`.
- **L89–92** If the DB driver already returned a `datetime`, take `.date()`. If it is already a `date`, return it as is. The `datetime` check must come first because `datetime` is a subclass of `date`.
- **L94** Otherwise, convert to a string and trim it.
- **L96–101** `re.search` (match anywhere in the string) looks for `YYYY-MM-DD`. If found, `date.fromisoformat` converts it to a date. An impossible date such as `2026-13-45` raises `ValueError`, which is caught and turned into `None`.
- **L103–112** Next it tries a US-style `M/D/YYYY`. Note the argument order in `date(year, month, day)`: group 3 is the year, group 1 the month, group 2 the day. Invalid values again become `None`.
- **L113** Anything else becomes `None`.
- Note: the docstring example `'May 14, 2026' -> '2026-05-14'` is **not** handled by the code. No branch parses month names, so that input returns `None` (SQL NULL). Also, the function returns a `date` object rather than the strings shown in the docstring, but psycopg converts it correctly.


#### Lines 115–149

````python
115
116  def populate_template_fields(incident_number: str, template_stage: str = "Drafting Agent Draft") -> Dict[str, Any]:
117      """Populate deterministic template fields directly from the incidents table.
118
119      Called as a pre-step before the drafting agent runs - NOT an LLM tool.
120      Reads the incident record and maps factual/copy-verbatim fields into the
121      template_details table. These fields do NOT require LLM reasoning - they
122      are either copied verbatim, undergo a simple format transformation, or
123      (for current_status/pir at non-initial stages) are carried forward
124      from the incident's own 'Drafting Agent Draft' row.
125
126      Note: 'MIMT Draft' is the human MIMT team's own stage (drafted_by a
127      person's name) - this function never writes there. The agent's own
128      initial draft stage is 'Drafting Agent Draft'.
129
130      Fields populated:
131          date_of_occurrence, type, legal_entity, priority_rating, pir,
132          incident_executive_lead_name, incident_executive_lead_contact, and
133          (for template_stage != 'Drafting Agent Draft') current_status and pir
134          carried forward from the Drafting Agent Draft row's current_status.
135
136      The remaining fields (title, current_status [Drafting Agent Draft stage
137      only], description, impact, management_actions, root_cause) require LLM
138      synthesis and are handled by the drafting agent via
139      generate_template_content.
140
141      Args:
142          incident_number: Incident number to populate template for.
143          template_stage: Template stage to write to (default 'Drafting Agent Draft').
144
145      Returns:
146          Dict with the populated fields and the template record ID:
147              - template_id: UUID of the created/updated template_details row
148              - fields: dict of the deterministic fields that were written
149      """
````

- **L116** `populate_template_fields(incident_number, template_stage="Drafting Agent Draft")` returns a dict. It is **not** decorated with `@tool`, so the LLM can never call it. The service calls it directly before any LLM work.
- **L117–149** The docstring defines the contract:
  - It reads the incident and writes the deterministic fields to `template_details`.
  - At later stages (`1LOD R&C`, `2LOD R&C`, `Reg Relations`), `current_status` and `pir` are **carried forward** from the agent's own `'Drafting Agent Draft'` row instead of being regenerated.
  - `'MIMT Draft'` is the human MIMT team's stage, and this function never writes there.
  - It returns `{"template_id": ..., "fields": {...}}`.


#### Lines 150–159

````python
150      try:
151          client = get_client()
152          rows = client.db_query("incidents", {"incident_number": incident_number})
153          if not rows:
154              error_msg = f"Incident {incident_number} not found in incidents table"
155              logger.error(error_msg)
156              raise ValueError(error_msg)
157
158          incident = rows[0]
159
````

- **L150** Everything is wrapped in `try`, so failures can be logged and re-raised (L232–239).
- **L151** Gets the shared Postgres client.
- **L152** `db_query("incidents", {"incident_number": ...})` runs a parameterised `SELECT * FROM incidents WHERE incident_number = %s` and returns a list of dicts.
- **L153–156** If there is no row, it logs and raises `ValueError`, because a template can't be drafted for an incident that doesn't exist.
- **L158** Takes the first (and normally only) row.


#### Lines 160–173

````python
160          # Map deterministic fields with simple transformations
161          fields = {
162              "date_of_occurrence": _extract_date(
163                  incident.get("technology_impact_start_datetime")
164                  or incident.get("business_impact_start_datetime")
165                  or ""
166              ),
167              "type": incident.get("incident_type") or "TBC",
168              "legal_entity": incident.get("legal_entity") or "TBC",
169              "priority_rating": _format_priority(incident.get("priority") or ""),
170              "incident_executive_lead_name": incident.get("incident_executive_lead_name") or "TBC",
171              "incident_executive_lead_contact": incident.get("incident_executive_lead_contact") or "TBC",
172          }
173
````

- **L161** Builds the `fields` dict of deterministic values. Its keys are `template_details` column names.
- **L162–166** `date_of_occurrence` uses `technology_impact_start_datetime`, falling back to `business_impact_start_datetime`, falling back to `""`. The `or` chain picks the first truthy value. The result goes through `_extract_date`.
- **L167–168** `type` and `legal_entity` are copied verbatim, with `"TBC"` if empty.
- **L169** `priority_rating` is reformatted by `_format_priority`.
- **L170–171** The executive lead's name and contact are copied verbatim, with `"TBC"` as the fallback.


#### Lines 174–199

````python
174          # For any stage after the agent's initial 'Drafting Agent Draft',
175          # current_status and pir are carried forward verbatim from that row
176          # rather than being re-generated by the LLM at each stage. This keeps
177          # a single source of truth for "what happened and when it was
178          # resolved" across the review pipeline.
179          if template_stage != "Drafting Agent Draft":
180              baseline_rows = client.db_query(
181                  "template_details",
182                  {
183                      "incident_number": incident_number,
184                      "template_stage": "Drafting Agent Draft",
185                      "drafted_by": "Drafting Agent",
186                  },
187                  ["current_status", "updated_at"],
188              )
189              baseline_status = "TBC"
190              if baseline_rows:
191                  latest_baseline = sorted(
192                      baseline_rows, key=lambda r: r.get("updated_at") or "", reverse=True
193                  )[0]
194                  baseline_status = latest_baseline.get("current_status") or "TBC"
195              fields["current_status"] = baseline_status
196              fields["pir"] = baseline_status
197          else:
198              fields["pir"] = incident.get("pir") or "TBC"
199
````

- **L174–178** The comment explains the design: after the first draft, "what happened and when it was resolved" should have one source of truth, so it is not re-generated at each review stage.
- **L179** This branch only runs for stages other than the agent's initial draft.
- **L180–188** It queries `template_details` for this incident's row where `template_stage = 'Drafting Agent Draft'` and `drafted_by = 'Drafting Agent'`. It selects only `current_status` and `updated_at` (the third argument is the column list).
- **L189** The default is `"TBC"` if no baseline exists.
- **L190–194** If rows exist, it sorts them newest-first by `updated_at` and takes the first. A **lambda** is a small anonymous function, used here as the sort key. Then it reads that row's `current_status`.
- **L195–196** Both `current_status` **and** `pir` are set to that baseline status. The docstring (L133–134) states this deliberately, but PIR is presumably a different field in the source data, so mapping it to the status text is a business choice worth knowing about.
- **L197–198** At the initial stage, `pir` is copied from `incidents.pir`, and `current_status` is left for the LLM to write.
- Note: the sort on L191 is redundant because `PostgresClient.db_query` already adds `ORDER BY updated_at DESC` for `template_details`, though it is harmless. The baseline lookup also doesn't filter by `prompt_version`, so it takes the newest agent draft across all prompt versions.


#### Lines 200–220

````python
200          # Write to template_details (upsert keyed on incident_number +
201          # template_stage + drafted_by + prompt_version)
202          from backend.tools.data_tools import TEMPLATE_PROMPT_VERSION_LABEL
203          prompt_label = TEMPLATE_PROMPT_VERSION_LABEL
204
205          payload = {
206              "incident_number": incident_number,
207              "template_stage": template_stage,
208              "drafted_by": "Drafting Agent",
209              "prompt_version": prompt_label,
210              **fields,
211          }
212          # Record which reportability assessment version drives this draft (set
213          # by the drafting orchestration), so the deterministic row lands on the
214          # same key as the agent's later content write for this assessment.
215          from backend.tools.data_tools import get_current_assessment_prompt_version
216          _apv = get_current_assessment_prompt_version()
217          if _apv is not None:
218              payload["assessment_prompt_version"] = _apv
219          record_id = client.db_write("template_details", payload)
220
````

- **L200–201** The comment says the write is an **upsert** (update the row if it exists, otherwise insert it). The key columns are defined in `REVISIONED_TABLES` in `postgres_client.py`.
- **L202–203** Lazily imports `TEMPLATE_PROMPT_VERSION_LABEL` from `data_tools`. This is the short label of the active drafting prompt (e.g. `'v0'`), derived from `DRAFTING_AGENT_PROMPT_VERSION`. The import is inside the function, probably to avoid an import cycle between tool modules.
- **L205–211** Builds the row: key columns (`incident_number`, `template_stage`, `drafted_by="Drafting Agent"`, `prompt_version`) plus `**fields`. The `**` operator *unpacks* a dict, merging its key/value pairs into this new dict literal.
- **L212–214** The comment explains the next step: record which reportability assessment version this draft is based on, so this skeleton row and the later LLM-content write land on the **same** row key.
- **L215–218** `get_current_assessment_prompt_version()` reads a **contextvar**. A `contextvars.ContextVar` is like a global variable that is private to the current thread or async task. `drafting_service` sets it before calling this function, which lets concurrent drafts for different incidents each have their own value. If it is set, it goes into the payload as `assessment_prompt_version`.
- **L219** `db_write("template_details", payload)` runs `_upsert_keyed`. A row is matched on `incident_number + template_stage + drafted_by + prompt_version + assessment_prompt_version` and updated, or a new row is inserted. It returns the row id as a string.


#### Lines 221–240

````python
221          logger.info(
222              "populate_template_fields: incident=%s, stage=%s, record=%s",
223              incident_number, template_stage, record_id,
224          )
225          log_tool_call(
226              "populate_template_fields",
227              {"incident_number": incident_number, "template_stage": template_stage},
228              outputs=f"Populated {len(fields)} deterministic fields, record_id={record_id}",
229          )
230          return {"template_id": record_id, "fields": fields}
231
232      except Exception as e:
233          logger.error("populate_template_fields failed: %s", e)
234          log_tool_call(
235              "populate_template_fields",
236              {"incident_number": incident_number, "template_stage": template_stage},
237              error=str(e),
238          )
239          raise
240
````

- **L221–224** An info log. It uses `%s` placeholders with arguments rather than an f-string, so the logging library formats the message only if the log level is enabled.
- **L225–229** Records the tool call (inputs plus a short summary of outputs) in the structured tool log.
- **L230** Returns the new or updated row id and the fields that were written.
- **L232–239** If anything fails, it logs the error, records it in the tool log with `error=...`, and re-raises with a bare `raise` (same exception, original traceback). The caller (`drafting_service`) sees the failure and marks the job as failed.


#### Lines 241–267

````python
241
242  @tool
243  def get_template_schema() -> Dict[str, Any]:
244      """Retrieve the regulatory notification template schema.
245
246      Returns the FRB/OCC notification template structure including field
247      definitions, required sections, and compliance guidelines. Use this to
248      understand the expected format before generating template content.
249
250      Returns:
251          Dict with template schema including fields, guidelines, and stages.
252
253      Example:
254          schema = get_template_schema()
255          # Returns: {"template_name": "...", "fields": {...}, "compliance_guidelines": [...], ...}
256      """
257      try:
258          with open(SCHEMA_PATH, "r") as f:
259              schema = json.load(f)
260          logger.info("Loaded template schema from %s", SCHEMA_PATH)
261          log_tool_call("get_template_schema", {}, outputs="Schema loaded successfully")
262          return schema
263      except Exception as e:
264          logger.error("get_template_schema failed: %s", e)
265          log_tool_call("get_template_schema", {}, error=str(e))
266          raise
267
````

- **L242** `@tool` exposes `get_template_schema` to LangChain agents. `drafting_service` calls it as `get_template_schema.invoke({})`, with an empty dict because it takes no arguments.
- **L243** It takes no parameters and returns a dict.
- **L244–256** The docstring doubles as the tool description shown to the LLM. It returns the FRB/OCC template structure: `template_name`, `fields` (each with `description`, `required`, `source`, `source_notes`, `example`, and sometimes `guidelines`), and `compliance_guidelines`.
- **L257–259** `with open(...) as f:` is a **context manager**. The file is closed automatically when the block ends, even after an error. `json.load` parses it into a Python dict.
- **L260–262** Logs, records the tool call, and returns the schema.
- **L263–266** On failure (missing file, invalid JSON), it logs, records the error and re-raises.


#### Lines 268–316

````python
268
269  @tool
270  def generate_template_content(
271      incident_data: Dict[str, Any],
272      assessment_data: Dict[str, Any],
273      template_schema: Dict[str, Any],
274      existing_template: Optional[Dict[str, Any]] = None,
275      feedback: Optional[str] = None,
276      mimt_draft: Optional[Dict[str, Any]] = None,
277  ) -> Dict[str, str]:
278      """Generate the LLM-authored fields of a regulatory notification template.
279
280      Produces only the fields that require synthesis/reasoning: title,
281      current_status, description, impact, management_actions, and root_cause.
282      All other template fields are populated deterministically by
283      populate_template_fields and should NOT be generated here.
284
285      Args:
286          incident_data: Dict with incident details (from incidents table).
287          assessment_data: Dict with reportability assessment (from
288              reportability_assessments table). Every incident reaching this
289              tool has already been assessed as reportable.
290          template_schema: Dict with template structure from get_template_schema().
291          existing_template: Optional dict with the current template content
292              when revising. None for initial drafts.
293          feedback: Optional reviewer feedback string to incorporate when
294              revising an existing template.
295          mimt_draft: Optional dict with a baseline template row (title,
296              current_status, description, impact, management_actions,
297              root_cause) used as context so the draft stays consistent with
298              that baseline. Its source depends on the stage being drafted:
299              the human MIMT team's own 'MIMT Draft' row when generating the
300              agent's initial 'Drafting Agent Draft', or the agent's own prior
301              'Drafting Agent Draft' row when generating a later revision
302              ('1LOD R&C', '2LOD R&C', 'Reg Relations'). None if no baseline
303              is available.
304
305      Returns:
306          Dict with 6 LLM-generated fields:
307              - title, current_status, description, impact, management_actions, root_cause
308
309      Example:
310          result = generate_template_content(
311              incident_data={"incident_number": "INC0575428", ...},
312              assessment_data={"reportability_flag": "Yes", ...},
313              template_schema=schema,
314          )
315          # Returns: {"title": "...", "description": "...", "impact": "...", ...}
316      """
````

- **L269** `generate_template_content` is also a LangChain tool. Its typed parameters become the tool's argument schema.
- **L271–273** These arguments are required. `incident_data` is the incident row, `assessment_data` is the reportability assessment row, and `template_schema` is the output of `get_template_schema`.
- **L274–276** These are optional and default to `None`:
  - `existing_template` is the current draft when revising.
  - `feedback` is the reviewer's comments.
  - `mimt_draft` is a "baseline" draft for consistency.
- **L277** It returns a dict of strings.
- **L278–316** The docstring states that the function produces only the six prose fields. It also explains where the baseline (`mimt_draft`) comes from. For the agent's initial `'Drafting Agent Draft'`, the baseline is the human MIMT team's `'MIMT Draft'` row. For later stages (`1LOD R&C`, `2LOD R&C`, `Reg Relations`), it is the agent's own earlier `'Drafting Agent Draft'` row. Every incident that reaches this tool has already been assessed as reportable.


#### Lines 317–340

````python
317      try:
318          llm = LLMService()
319
320          # Only include schema field descriptions for the 6 LLM-generated fields
321          llm_fields = {"title", "current_status", "description", "impact", "management_actions", "root_cause"}
322          field_descriptions = []
323          for name, spec in template_schema.get("fields", {}).items():
324              if name not in llm_fields:
325                  continue
326              desc = spec.get("description", "")
327              source_notes = spec.get("source_notes")
328              guidelines = spec.get("guidelines", [])
329              entry = f"- **{name}**: {desc}"
330              if source_notes:
331                  entry += f"\n  {source_notes}"
332              if guidelines:
333                  entry += "\n  Guidelines: " + "; ".join(guidelines)
334              field_descriptions.append(entry)
335          fields_text = "\n".join(field_descriptions)
336
337          compliance_text = "\n".join(
338              f"- {g}" for g in template_schema.get("compliance_guidelines", [])
339          )
340
````

- **L318** Creates an `LLMService` instance. Its constructor isn't visible in the screenshots, but it presumably reads the gateway settings from environment variables. The OAuth token is obtained or refreshed when needed.
- **L321** `llm_fields` is a **set literal** `{...}` of the six field names the LLM is allowed to write. Sets give fast membership checks.
- **L322–334** Loops over `template_schema["fields"].items()`, which yields (name, spec) pairs:
  - **L324–325** Skips fields the LLM shouldn't touch (date, type, etc.).
  - **L326–328** Reads the field's `description`, optional `source_notes`, and optional `guidelines` list. `.get(key, default)` avoids a `KeyError` if a key is missing.
  - **L329** Starts a Markdown bullet such as `- **title**: <description>`.
  - **L330–333** Appends the source notes, and the guidelines joined with `"; "`, on indented continuation lines.
  - **L334** Collects the entry.
- **L335** Joins all entries into one block of prompt text.
- **L337–339** Builds the compliance guidelines as bullet lines. The argument to `"\n".join(...)` is a **generator expression** (like a list comprehension but lazy), which yields one `- <guideline>` string per item.


#### Lines 341–380

````python
341          # Build the core prompt sections - only requesting the 6 LLM fields
342          prompt_parts = [
343              "Generate ONLY the following 6 fields for a regulatory notification template.",
344              "All other template fields (date_of_occurrence, type, legal_entity, priority_rating,",
345              "pir, incident_executive_lead_name/contact) are handled separately",
346              "by a deterministic tool and must NOT be included in your output.",
347              "",
348              "**Fields to Generate:**",
349              fields_text,
350              "",
351              "**Compliance Guidelines:**",
352              compliance_text,
353              "",
354              "**Incident Details:**",
355              f"- Incident Number: {incident_data.get('incident_number', 'N/A')}",
356              f"- Priority: {incident_data.get('priority', 'N/A')}",
357              f"- Legal Entity: {incident_data.get('legal_entity', 'N/A')}",
358              f"- Incident Type: {incident_data.get('incident_type', 'N/A')}",
359              f"- Incident Title: {incident_data.get('incident_title', 'N/A')}",
360              f"- Impacted Applications: {incident_data.get('impacted_applications', 'N/A')}",
361              f"- Location: {incident_data.get('location', 'N/A')}",
362              f"- Incident Manager: {incident_data.get('incident_manager', 'N/A')}",
363              "",
364              "**Description:**",
365              str(incident_data.get("incident_description", "N/A")),
366              "",
367              "**Impact Analysis:**",
368              f"- Business Impact: {incident_data.get('business_impact', 'N/A')}",
369              f"- User Impact: {incident_data.get('user_impact', 'N/A')}",
370              f"- Client Impact: {incident_data.get('client_impact', 'N/A')}",
371              "",
372              "**Timing:**",
373              f"- Technology Impact Start: {incident_data.get('technology_impact_start_datetime', 'N/A')}",
374              f"- Business Impact Start: {incident_data.get('business_impact_start_datetime', 'N/A')}",
375              "",
376              "**Additional Context:**",
377              f"- Additional Details: {incident_data.get('additional_details', 'N/A')}",
378              f"- Management Actions (source): {incident_data.get('management_actions', 'N/A')}",
379          ]
380
````

- **L342** `prompt_parts` is a list of lines that will be joined with newlines at L437. Building a prompt as a list makes optional sections easy to add.
- **L343–346** These lines tell the model to output only the six fields and to leave the deterministic fields alone.
- **L348–352** Insert the field definitions and compliance guidelines built above.
- **L354–362** The "Incident Details" section. Each f-string pulls one incident column and shows `'N/A'` if it is missing: number, priority, legal entity, type, title, impacted applications, location and incident manager.
- **L364–365** The free-text incident description. It is wrapped in `str(...)` in case the value is not a string.
- **L367–370** The business, user and client impact text.
- **L372–374** The technology and business impact start timestamps, used to build the timeline.
- **L376–378** Additional details, and the incident's own `management_actions`. The label "(source)" tells the model this is raw material to rewrite, not the final text.


#### Lines 381–409

````python
381          # Add assessment context if available
382          if assessment_data:
383              prompt_parts.extend([
384                  "",
385                  "**Reportability Assessment:**",
386                  f"- Reportability Flag: {assessment_data.get('reportability_flag', 'N/A')}",
387                  f"- Data/System Impact: {assessment_data.get('data_system_impact', 'N/A')}",
388                  f"- Operational Impact: {assessment_data.get('operational_impact', 'N/A')}",
389                  f"- Financial/Reputational Impact: {assessment_data.get('financial_reputational_impact', 'N/A')}",
390                  f"- Systemic Risk: {assessment_data.get('systemic_risk', 'N/A')}",
391                  f"- Rationale: {assessment_data.get('rationale', 'N/A')}",
392              ])
393
394          # Add baseline draft context if available (keeps root_cause and
395          # description narrative continuity with the human MIMT Draft at the
396          # initial stage, or with the agent's own prior Drafting Agent Draft
397          # at later revision stages)
398          if mimt_draft:
399              prompt_parts.extend([
400                  "",
401                  "**Baseline Draft (for consistency - not the template being written now):**",
402                  f"- Title: {mimt_draft.get('title', 'N/A')}",
403                  f"- Current Status: {mimt_draft.get('current_status', 'N/A')}",
404                  f"- Description: {mimt_draft.get('description', 'N/A')}",
405                  f"- Impact: {mimt_draft.get('impact', 'N/A')}",
406                  f"- Management Actions: {mimt_draft.get('management_actions', 'N/A')}",
407                  f"- Root Cause: {mimt_draft.get('root_cause', 'N/A')}",
408              ])
409
````

- **L382** The assessment section is added only if `assessment_data` is truthy (a non-empty dict).
- **L383–392** `list.extend` appends several lines at once. They contain the assessment's reportability flag, the four FRB criteria fields (data/system impact, operational impact, financial/reputational impact, systemic risk) and the rationale. This gives the model the reasoning behind why the incident is reportable, which helps it write the impact section.
- **L394–397** The comment explains why the baseline is included: to keep the root-cause and description narrative consistent with the human MIMT draft (initial stage) or the agent's own earlier draft (later stages).
- **L398–408** If a baseline exists, its six fields are added under a header. The header says explicitly that this is "not the template being written now", so the model doesn't think it is editing that row.


#### Lines 410–438

````python
410          # Add revision context if this is a revision
411          if existing_template:
412              prompt_parts.extend([
413                  "",
414                  "**Existing Template (to be revised - only the 6 generated fields):**",
415                  f"- Title: {existing_template.get('title', 'N/A')}",
416                  f"- Current Status: {existing_template.get('current_status', 'N/A')}",
417                  f"- Description: {existing_template.get('description', 'N/A')}",
418                  f"- Impact: {existing_template.get('impact', 'N/A')}",
419                  f"- Management Actions: {existing_template.get('management_actions', 'N/A')}",
420                  f"- Root Cause: {existing_template.get('root_cause', 'N/A')}",
421              ])
422
423          if feedback:
424              prompt_parts.extend([
425                  "",
426                  "**Reviewer Feedback (MUST be incorporated in the revision):**",
427                  feedback,
428                  "",
429                  "IMPORTANT: This is a REVISION. Incorporate the reviewer feedback above while maintaining factual accuracy and compl⟦CUT⟧
430              ])
431          else:
432              prompt_parts.extend([
433                  "",
434                  "Generate the initial content using formal regulatory prose. Be factual, precise, and compliant.",
435              ])
436
437          prompt = "\n".join(prompt_parts)
438
````

- **L411–421** For revisions, this adds the current version of the six fields under "Existing Template (to be revised…)".
- **L423–430** If reviewer feedback exists, it is added under a header saying it **MUST** be incorporated, followed by an "IMPORTANT: This is a REVISION…" instruction. **L429 ran off the screen (⟦CUT⟧)**. The visible text is "…while maintaining factual accuracy and compl", which was most likely "compliance…", but that is an inference.
- **L431–435** Without feedback, it adds a closing instruction for a first draft: formal regulatory prose, factual, precise and compliant.
- **L437** Joins all parts into one prompt string with newline separators.


#### Lines 439–457

````python
439          logger.info(
440              "Generating template content for incident %s (revision=%s)",
441              incident_data.get("incident_number"),
442              bool(existing_template),
443          )
444
445          # Include the active drafting agent prompt version's style guide as a
446          # system message so this tool's own LLM call follows the same word
447          # count, neutral-language, and field-format rules as the agent prompt.
448          system_prompt = _get_drafting_system_prompt()
449
450          template_obj = llm.chat_structured(
451              messages=[
452                  {"role": "system", "content": system_prompt},
453                  {"role": "user", "content": prompt},
454              ],
455              response_format=TemplateContent,
456          )
457
````

- **L439–443** Logs which incident is being drafted and whether this is a revision. `bool(existing_template)` turns the dict or `None` into True or False.
- **L445–448** Loads the system prompt (the active prompt version's style guide, or the fallback) through `_get_drafting_system_prompt`.
- **L450–456** `llm.chat_structured(messages=[...], response_format=TemplateContent)` sends a two-message chat in the OpenAI format: a `system` message with the style guide and a `user` message with the prompt. Passing the Pydantic class makes `LLMService.chat` use the SDK's `beta.chat.completions.parse`, which forces JSON-schema output and returns a **validated `TemplateContent` instance** instead of raw text.


#### Lines 458–478

````python
458          template = {
459              "title": template_obj.title,
460              "current_status": template_obj.current_status,
461              "description": template_obj.description,
462              "impact": template_obj.impact,
463              "management_actions": template_obj.management_actions,
464              "root_cause": template_obj.root_cause,
465          }
466
467          logger.info(
468              "Template content generated for incident %s (6 LLM fields)",
469              incident_data.get("incident_number"),
470          )
471          log_tool_call(
472              "generate_template_content",
473              {"incident_number": incident_data.get("incident_number"), "is_revision": bool(existing_template)},
474              outputs=f"Generated 6 fields: title={template['title'][:80]}",
475          )
476
477          return template
478
````

- **L458–465** Converts the Pydantic object into a plain dict, reading the six attributes. A plain dict is easier to serialise and to merge into the DB payload. `template_obj.model_dump()` would do the same.
- **L467–470** Logs success.
- **L471–475** Records the tool call, including whether it was a revision and the first 80 characters of the generated title (`[:80]` is a string slice).
- **L477** Returns the dict. `drafting_service._generate_and_persist_draft_content` writes it to `template_details`. It writes `current_status` only at the `'Drafting Agent Draft'` stage, because at later stages that value was already carried forward by `populate_template_fields`.


#### Lines 479–487

````python
479      except Exception as e:
480          logger.error("generate_template_content failed: %s", e)
481          log_tool_call(
482              "generate_template_content",
483              {"incident_number": incident_data.get("incident_number")},
484              error=str(e),
485          )
486          raise
487
````

- **L479–486** Error path, following the same pattern as the other tools: log the error, record it in the tool log with the incident number, and re-raise so the caller's retry or failure handling runs. `drafting_service` retries the generate-and-write step once.


---

## 29. `backend/tools/template_schema.json`

*144 lines · complete*

This JSON file is the **specification of the regulatory notification template**: the FRB/OCC "Regulatory Technology and Cyber Security Notification" that the drafting agent fills in. It's data, not code. `drafting_tools.get_template_schema()` loads it (`SCHEMA_PATH = Path(__file__).parent / "template_schema.json"`) and `drafting_service` passes it to the LLM, so the model knows each field's meaning, where its content should come from, examples and writing guidelines. Each field's `source` says who fills it: `"generated"` means the LLM writes it (in `generate_template_content`); a value like `"incidents.legal_entity"` means it is copied deterministically from the `incidents` table by `populate_template_fields`, not by the LLM. The file ends with global compliance guidelines and the list of template **stages** (MIMT Draft -> Drafting Agent Draft -> 1LOD R&C -> 2LOD R&C -> Reg Relations) that match the `template_stage` values used throughout the backend. Many long lines ran off the screen (⟦CUT⟧), and lines 87–109 and 111–113 were not captured at all.


#### Lines 1–4

````json
1  {
2    "template_name": "Regulatory Technology and Cyber Security Notification",
3    "description": "Standard FRB/OCC notification template used for reportable technology and cyber security incidents at CNB/RBC enti⟦CUT⟧
4    "fields": {
````

- **L1**: `{` opens the top-level JSON object. (JSON is a strict subset of JavaScript object syntax: keys must be double-quoted strings; values are strings, numbers, `true`/`false`, `null`, arrays `[...]` or objects `{...}`. `json.load` turns it into Python dicts and lists.)
- **L2**: `template_name`: the official notification name.
- **L3**: `description`: the standard FRB/OCC notification used for reportable technology and cyber incidents at CNB/RBC entities (the rest of the line ran off the screen).
- **L4**: `"fields": {` opens the per-field definitions. Each key is a template field name that matches a column in `template_details`.


#### Lines 5–11

````json
 5      "title": {
 6        "description": "Concise notification title summarizing the incident, including what happened and (if relevant) what/who caused⟦CUT⟧
 7        "required": true,
 8        "source": "generated",
 9        "source_notes": "Author from incidents.incident_title + incidents.incident_description. Do not just copy incident_title verbat⟦CUT⟧
10        "example": "CNB Customer Call Disruption on NICE CXOne Voice Platform following Avaya Certificate Change"
11      },
````

- **L5–11**: the `title` field.
  - `description`: a concise title saying what happened and (if relevant) what or who caused it (line cut off).
  - `required: true`: it must appear in the notification.
  - `source: "generated"`: the LLM writes it.
  - `source_notes`: build it from `incidents.incident_title` + `incident_description` and don't just copy the title verbatim (cut off).
  - `example`: a realistic sample title (a NICE CXOne voice platform disruption after an Avaya certificate change).
- This shape (description / required / source / source_notes / example, sometimes `guidelines`) repeats for every field.


#### Lines 12–18

````json
12      "current_status": {
13        "description": "Current operational status of the incident at time of notification: whether the issue is ongoing, mitigated, o⟦CUT⟧
14        "required": true,
15        "source": "generated",
16        "source_notes": "Derive from incidents.current_status plus any resolution time mentioned in incidents.incident_description / incidents.business⟦?⟧⟦CUT⟧
17        "example": "Service Restored at 06:36 AM PT"
18      },
````

- **L12–18**: `current_status`: whether the issue is ongoing, mitigated or resolved at notification time. It is generated, derived from `incidents.current_status` plus any resolution time mentioned in the description or business impact (L16 partly uncertain and cut off). Example: "Service Restored at 06:36 AM PT".
- In code, for stages after "Drafting Agent Draft", `populate_template_fields` carries `current_status` forward from the agent's first draft instead of regenerating it.


#### Lines 19–25

````json
19      "date_of_occurrence": {
20        "description": "Date the incident first occurred, in YYYY-MM-DD format (for storage; displayed to reviewers in long form, e.g. 'May 14, 2026').⟦?⟧⟦CUT⟧
21        "required": true,
22        "source": "incidents.technology_impact_start_datetime",
23        "source_notes": "Populated by populate_template_fields tool. Extracts the date portion of incidents.technology_impact_start_datetime, falling⟦?⟧⟦CUT⟧
24        "example": "2026-05-14"
25      },
````

- **L19–25**: `date_of_occurrence`, stored as `YYYY-MM-DD` and shown to reviewers in long form (e.g. "May 14, 2026"). Its `source` is `incidents.technology_impact_start_datetime`. `source_notes` says it is filled by the `populate_template_fields` tool by extracting the date part, "falling" back (cut off) to another field; per `drafting_tools.py` that fallback is `business_impact_start_datetime`. Some of this text is marked uncertain.


#### Lines 26–32

````json
26      "type": {
27        "description": "Classification of the incident. Use one of: 'Service Outage', 'Service Degradation', 'Data Breach', 'Security Incident', 'Syste⟦?⟧⟦CUT⟧
28        "required": true,
29        "source": "incidents.incident_type",
30        "source_notes": "Populated by populate_template_fields tool. Copied verbatim from incidents.incident_type. Not generated by the LLM.",
31        "example": "Service Outage"
32      },
````

- **L26–32**: `type`, the incident classification. The description lists allowed values ("Service Outage", "Service Degradation", "Data Breach", "Security Incident", "Syste[m ...]"; cut off). It is copied verbatim from `incidents.incident_type` by `populate_template_fields`; "Not generated by the LLM."


#### Lines 33–39

````json
33      "legal_entity": {
34        "description": "Full legal entity name as registered with regulators.",
35        "required": true,
36        "source": "incidents.legal_entity",
37        "source_notes": "Populated by populate_template_fields tool. Copied verbatim from incidents.legal_entity. Not generated by the LLM.",
38        "example": "City National Bank (CNB)"
39      },
````

- **L33–39**: `legal_entity`, the full registered legal entity name (e.g. "City National Bank (CNB)"), copied verbatim from `incidents.legal_entity`.


#### Lines 40–46

````json
40      "priority_rating": {
41        "description": "Priority rating of the incident, formatted as 'P<N> – <Level>' using an en dash.",
42        "required": true,
43        "source": "incidents.priority",
44        "source_notes": "Populated by populate_template_fields tool. Transforms incidents.priority (e.g. '1 - Significant' -> 'P1 \u20⟦CUT⟧
45        "example": "P1 – Significant"
46      },
````

- **L40–46**: `priority_rating`, formatted `P<N> – <Level>` with an en dash. The source is `incidents.priority`, transformed (e.g. `'1 - Significant'` becomes `'P1 – Significant'`) by `_format_priority` in `drafting_tools.py`. `\u20..` on L44 is a JSON Unicode escape for the en dash (`–`); the line was cut off there.


#### Lines 47–59

````json
47      "description": {
48        "description": "Full narrative description of the incident for regulatory submission. Must include: (1) What happened - clear⟦CUT⟧
49        "required": true,
50        "source": "generated",
51        "source_notes": "Synthesize from incidents.incident_description, incidents.business_impact, incidents.user_impact, incidents.c⟦CUT⟧
52        "guidelines": [
53          "Use past tense for resolved events, present tense for ongoing situations",
54          "Include specific times with timezone",
55          "Reference specific systems and applications by name",
56          "Quantify impact where possible (number of users, transactions, hours)",
57          "Do not include internal jargon without explanation"
58        ]
59      },
````

- **L47–51**: `description`, the full narrative for the regulator. It must include what happened and more (the numbered list was cut off). It is generated by synthesizing the incident description, business, user and client impact, etc.
- **L52–58**: `guidelines` is an array of writing rules for the LLM: tense (past for resolved, present for ongoing), times with time zones, naming specific systems, quantifying impact, and no unexplained internal jargon.


#### Lines 60–71

````json
60      "impact": {
61        "description": "Comprehensive impact assessment (labeled 'Impact (Financial/Non-Financial)' in the notification) covering: (1) Financial impac⟦?⟧⟦CUT⟧
62        "required": true,
63        "source": "generated",
64        "source_notes": "Synthesize from incidents.business_impact, incidents.user_impact, incidents.client_impact, and (when available) the reportabil⟦?⟧⟦CUT⟧
65        "guidelines": [
66          "Separate financial from non-financial impact",
67          "Be specific about client-facing vs internal-only impact",
68          "Quantify where possible",
69          "Note if impact assessment is preliminary or final"
70        ]
71      },
````

- **L60–64**: `impact`, labelled "Impact (Financial/Non-Financial)" in the notification. It is generated from the business, user and client impact fields and, when available, the reportability assessment (text partly cut off or uncertain).
- **L65–70**: guidelines: separate financial from non-financial impact, distinguish client-facing from internal impact, quantify, and say whether the assessment is preliminary or final.


#### Lines 72–82

````json
72      "management_actions": {
73        "description": "Actions taken by management to resolve the incident and prevent recurrence. Include: (1) Immediate response actions, (2) Remedi⟦?⟧⟦CUT⟧
74        "required": false,
75        "source": "incidents.management_actions",
76        "source_notes": "If incidents.management_actions is populated (filled in by the MIMT team), copy it verbatim (light formatting only). If it is⟦?⟧⟦CUT⟧
77        "guidelines": [
78          "List actions in chronological order",
79          "Include who took each action if known",
80          "Note any escalation decisions"
81        ]
82      },
````

- **L72–76**: `management_actions`, the actions taken to resolve the incident and prevent recurrence. It is `required: false`. Its `source` is `incidents.management_actions`: if the MIMT team has filled that column, copy it verbatim with light formatting only; otherwise (cut off) presumably the LLM drafts it from the incident details (inference; the matching Pydantic field in `drafting_tools.TemplateContent` has a default of `""`).
- **L77–81**: guidelines: chronological order, who did what if known, note escalation decisions.


#### Lines 83–109

````json
 83      "root_cause": {
 84        "description": "Root cause analysis or preliminary findings. If root cause is not yet determined, state that a Post-Incident Review is underway⟦?⟧⟦CUT⟧
 85        "required": false,
 86        "source": "generated",
 87        "source_notes": "Synthesize from incidents.root_cause plus any preliminary findings mentioned in incidents.incident_descriptio⟦CUT⟧
 88        "example": "TBC: Microsoft to provide Root Cause Analysis (RCA) within five business days."
 89      },
 90      "pir": {
 91        "description": "Post-Incident Review (PIR) status and expected completion date. Format: 'PIR [status]. Expected completion: [d⟦CUT⟧
 92        "required": false,
 93        "source": "incidents.pir",
 94        "source_notes": "Populated by populate_template_fields tool. For the 'Drafting Agent Draft' stage, copied verbatim from incide⟦CUT⟧
 95        "example": "PIR in progress. Expected completion: April 15, 2025."
 96      },
 97      "incident_executive_lead_name": {
 98        "description": "Name of the executive responsible for the incident response.",
 99        "required": false,
100        "source": "incidents.incident_executive_lead_name",
101        "source_notes": "Populated by populate_template_fields tool. Copied verbatim from incidents.incident_executive_lead_name; uses ⟦CUT⟧
102      },
103      "incident_executive_lead_contact": {
104        "description": "Contact email of the executive lead.",
105        "required": false,
106        "source": "incidents.incident_executive_lead_contact",
107        "source_notes": "Populated by populate_template_fields tool. Copied verbatim from incidents.incident_executive_lead_contact; u⟦CUT⟧
108      }
109    },
````

- **L83–89** `root_cause`:
  - **What it holds:** the root cause, or preliminary findings. If the cause isn't known yet, the text should say a Post-Incident Review is underway (the description is cut off at the screen edge).
  - **Who writes it:** it is `required: false` with `source: "generated"`, so the LLM writes it. This is consistent with `drafting_tools.py`, which treats root_cause as LLM-authored.
  - **L87 `source_notes`:** tells the model to synthesize it from `incidents.root_cause` plus any preliminary findings mentioned in `incidents.incident_description` (the line is cut off at the edge).
  - **L88 `example`:** "TBC: Microsoft to provide Root Cause Analysis (RCA) within five business days." It shows how to word a root cause that depends on a third-party vendor and isn't known yet.
- **L90–96** `pir` (Post-Incident Review):
  - **Description and format:** the PIR's status and expected completion date, in the form `'PIR [status]. Expected completion: [date]'` (cut off).
  - **Source:** `source: "incidents.pir"` means it is **copied, not generated**. `populate_template_fields` fills it deterministically.
  - **L94 `source_notes`:** at the `'Drafting Agent Draft'` stage it is copied verbatim from the incident. The line is cut off; *inference*: the rest describes later stages, which matches `drafting_tools.py` setting `pir` from the baseline draft at later review stages.
  - **L95:** an example value.
- **L97–102** `incident_executive_lead_name`:
  - The executive accountable for the incident response, copied verbatim from `incidents.incident_executive_lead_name` by `populate_template_fields`.
  - The note is cut off after "uses". *Inference*: it ends with using `"TBC"` when the column is empty, matching the drafting prompt's "or "TBC" if empty" rule (faint text in the photo also suggests this).
- **L103–108** `incident_executive_lead_contact`: the same as the lead's name, but for their contact email.
  - L108's `}` has no trailing comma because this is the last field. JSON forbids trailing commas.
- **L109** `},` closes the `"fields"` object opened on L4.


#### Lines 110–120

````json
110    "compliance_guidelines": [
111      "Use formal, professional regulatory prose throughout",
112      "Be factual and evidence-based - do not speculate or editorialize",
113      "Include specific dates, times (with timezone), and quantities where available",
114      "Reference impacted systems, applications, and business units by name",
115      "Clearly distinguish between confirmed facts and preliminary findings",
116      "If information is not available, state 'Information not yet available' (or the field-specific placeholder noted in source_notes⟦CUT⟧
117      "Fields sourced directly from the incidents table (date_of_occurrence, type, legal_entity, priority_rating, pir, incident_execut⟦CUT⟧
118      "Maintain consistency with the reportability assessment rationale",
119      "For revisions: clearly incorporate reviewer feedback while maintaining factual accuracy"
120    ],
````

- **L110** `compliance_guidelines`: an array of global writing rules that apply to the whole notification. `generate_template_content` includes them in its prompt.
- **L111–113** The first three rules:
  - formal, professional regulatory prose;
  - factual and evidence-based, with no speculating or editorializing;
  - include specific dates, times **with timezone**, and quantities where available. Regulators care about exact timelines.
- **L114–119** The remaining rules:
  - name impacted systems, applications and business units;
  - distinguish confirmed facts from preliminary findings;
  - use "Information not yet available" (or a field-specific placeholder) when data is missing;
  - fields sourced directly from the incidents table (date_of_occurrence, type, legal_entity, priority_rating, pir, incident_execut[ive lead …]) are handled deterministically. The line was cut off, so the exact instruction is an inference.
  - stay consistent with the reportability assessment's rationale;
  - for revisions, work in reviewer feedback while staying factually accurate.
- **L120** `],` closes the array.


#### Lines 121–144

````json
121    "template_stages": [
122      {
123        "stage": "MIMT Draft",
124        "description": "Initial draft authored by the human MIMT team - not written by the Drafting Agent, but used as optional input⟦CUT⟧
125      },
126      {
127        "stage": "Drafting Agent Draft",
128        "description": "Initial draft created by the Drafting Agent for 1LOD review, informed by the human MIMT Draft when available"
129      },
130      {
131        "stage": "1LOD R&C",
132        "description": "Revised draft incorporating 1LOD reviewer feedback"
133      },
134      {
135        "stage": "2LOD R&C",
136        "description": "Revised draft incorporating 2LOD reviewer feedback accepted by 1LOD"
137      },
138      {
139        "stage": "Reg Relations",
140        "description": "Final version prepared for regulatory submission"
141      }
142    ]
143  }
144
````

- **L121**: `template_stages` lists the lifecycle stages a notification goes through. These strings are exactly the `template_stage` values used in `template_details` and in `workflow_service`/`drafting_service`.
- **L123–124**: "MIMT Draft": the human MIMT team's initial draft. It is not written by the agent, but used as optional input (cut off).
- **L127–128**: "Drafting Agent Draft": the agent's first draft for 1LOD review, informed by the MIMT draft when available.
- **L131–132**: "1LOD R&C": the revision incorporating 1LOD feedback.
- **L135–136**: "2LOD R&C": the revision incorporating 2LOD feedback "accepted by 1LOD". Note: in the current `workflow_service`, 2LOD feedback triggers this re-draft directly, without a 1LOD acceptance step; the module docstring there calls this a deliberate deviation.
- **L139–140**: "Reg Relations": the final version prepared for regulatory submission.
- **L142–143**: close the array and the top-level object; **L144** is the trailing empty line.


---

## 30. `backend/tools/reference_data_tools.py`

*212 lines · complete*

This module gives the **assessment agent** two extra pieces of reference context from the Excel workbook at `REFERENCE_DATA_FILE_PATH` (by default `Data Sample File.xlsx` at the project root):
- **CUSO Applications** (~2,600 apps, with metadata such as criticality, data classification and SOX/payment flags). It is too big to put in a prompt, so `lookup_cuso_applications` fuzzy-matches only the few most relevant rows.
- **Regulatory Requirements** (~15 rows: which regulator applies, and the thresholds and notification rules by region or legal entity). It is small, so `get_regulatory_requirements` returns all of it.

Both sheets are loaded lazily and cached in memory for the life of the process; they are never put in Postgres. `backend/services/assessment_service.py` calls both tools directly with `.invoke(...)` and passes the results to the assessment as context. `backend/agents/assessment_agent.py` also lists them in its `TOOLS`, so the agent can call them itself.


#### Lines 1–21

````python
 1  """In-memory reference data tools for the Assessment Agent.
 2
 3  Per Sep 24 meeting notes: the CUSO Applications and Regulatory Requirements
 4  sheets in the reference workbook (backend.config.REFERENCE_DATA_FILE_PATH)
 5  need to be available as additional context to the assessment agent, without
 6  risking prompt/context overflow.
 7
 8  - CUSO Applications is ~2,600 rows with long free-text descriptions - far too
 9    large to inline. It's loaded into memory once (module-level cache) and
10    exposed via `lookup_cuso_applications`, a fuzzy-match tool that returns only
11    the handful of rows most relevant to a text query (e.g. an incident's
12    impacted_applications value), with descriptions truncated to a preview.
13  - Regulatory Requirements is small (~15 rows) - small enough to return in
14    full via `get_regulatory_requirements`, letting the agent reason over all
15    of it (e.g. matching by region/legal entity) without a lossy fuzzy match.
16
17  Neither sheet is loaded into the database (see plan.md) - both are read
18  directly from the Excel workbook and cached in memory for the life of the
19  process.
20  """
21
````

- **L1–20** The module docstring, which records the design decision:
  - **L3–6** The requirement came from Sep 24 meeting notes: give the assessment agent these two sheets as context without overflowing the LLM's context window.
  - **L8–12** CUSO Applications is large (~2,600 rows with long descriptions), so it is cached in memory and searched with a fuzzy-match tool that returns a handful of rows with truncated descriptions.
  - **L13–15** Regulatory Requirements is small enough to return in full, which avoids a lossy fuzzy match.
  - **L17–19** Neither sheet goes into the DB. Both are read from Excel and cached for the life of the process.


#### Lines 22–34

````python
22  import difflib
23  from pathlib import Path
24  from threading import Lock
25  from typing import Any, Dict, List, Optional
26
27  import pandas as pd
28  from langchain_core.tools import tool
29
30  from backend.config import REFERENCE_DATA_FILE_PATH
31  from backend.logger import get_logger, log_tool_call
32
33  logger = get_logger(__name__)
34
````

- **L22** `difflib` is the standard-library module for string similarity. `SequenceMatcher` scores how alike two strings are.
- **L23** `Path` is used to check that the workbook exists.
- **L24** `Lock` from `threading` makes sure only one thread loads a sheet at a time. FastAPI and the batch workers run requests concurrently.
- **L25** Type-hint helpers.
- **L27** `pandas`, imported as `pd`, reads Excel sheets into DataFrames (table objects).
- **L28** LangChain's `@tool` decorator, explained below.
- **L30** The configured path of the reference workbook. It can be overridden with an environment variable.
- **L31** The project's logger factory and the structured tool-call logger.
- **L33** The module logger.


#### Lines 35–43

````python
35  _CUSO_SHEET_NAME = "CUSO Applications"
36  _REGULATORY_SHEET_NAME = "Regulatory Requirements"
37  _APP_DESCRIPTION_PREVIEW_CHARS = 600
38
39  _cache_lock = Lock()
40  _cuso_df: Optional[pd.DataFrame] = None
41  _regulatory_rows: Optional[List[Dict[str, Any]]] = None
42
43
````

- **L35–36** Constants for the two sheet (tab) names in the workbook.
- **L37** The maximum length of an app description returned to the LLM, 600 characters, which keeps tool output small.
- **L39** A single lock shared by both caches.
- **L40–41** Module-level caches, starting as `None`. `Optional[pd.DataFrame]` means "a DataFrame or None". The CUSO sheet stays a DataFrame for searching. The regulatory sheet is stored as a list of row dicts, ready to return.


#### Lines 44–55

````python
44  def _load_sheet(sheet_name: str) -> pd.DataFrame:
45      path = Path(REFERENCE_DATA_FILE_PATH)
46      if not path.exists():
47          raise FileNotFoundError(
48              f"Reference data workbook not found at {path}. Set REFERENCE_DATA_FILE_PATH "
49              "or place 'Data Sample File.xlsx' at the project root."
50          )
51      df = pd.read_excel(path, sheet_name=sheet_name)
52      df.columns = [str(c).strip() for c in df.columns]
53      return df
54
55
````

- **L44** A helper that loads one sheet by name.
- **L45–50** Converts the config string into a `Path`. If the file is missing, it raises `FileNotFoundError` with a clear message telling you to set the env var or place the file at the project root.
- **L51** `pd.read_excel(path, sheet_name=...)` reads that tab into a DataFrame. It needs an Excel engine such as `openpyxl` to be installed.
- **L52** Normalises column headers by converting each to `str` and stripping stray spaces. Header names with trailing spaces are common in hand-edited Excel files, and the code later looks columns up by exact name (`"App Code"`, etc.). This is a **list comprehension**, `[expr for x in iterable]`, which builds a new list in one line.
- **L53** Returns the DataFrame.


#### Lines 56–66

````python
56  def _get_cuso_df() -> pd.DataFrame:
57      """Lazily load and cache the CUSO Applications sheet in memory."""
58      global _cuso_df
59      if _cuso_df is None:
60          with _cache_lock:
61              if _cuso_df is None:
62                  _cuso_df = _load_sheet(_CUSO_SHEET_NAME)
63                  logger.info(f"Loaded {len(_cuso_df)} rows from '{_CUSO_SHEET_NAME}' sheet into memory")
64      return _cuso_df
65
66
````

- **L56–57** Returns the CUSO DataFrame, loading it on first use ("lazily").
- **L58** `global _cuso_df` lets the function assign to the module-level variable rather than create a local one.
- **L59–62** This is **double-checked locking**. The cheap check without the lock (L59) handles the common case where the data is already loaded. If it isn't, the code takes the lock, `with _cache_lock:` (a context manager that releases the lock automatically), then checks again (L61), because another thread may have loaded the sheet while this one was waiting. Only then does it load.
- **L63** Logs how many rows were loaded.
- **L64** Returns the cached DataFrame.


#### Lines 67–82

````python
67  def _clean_record(record: Dict[str, Any]) -> Dict[str, Any]:
68      """Replace NaN/NaT values with None and stringify datetimes/Timestamps so
69      the record is safe to serialize as tool output."""
70      cleaned = {}
71      for key, value in record.items():
72          if value is None:
73              cleaned[key] = None
74          elif isinstance(value, float) and pd.isna(value):
75              cleaned[key] = None
76          elif isinstance(value, pd.Timestamp):
77              cleaned[key] = None if pd.isna(value) else value.isoformat()
78          else:
79              cleaned[key] = value
80      return cleaned
81
82
````

- **L67–69** Cleans one row dict so it can be safely serialised as tool output, for example to JSON for the LLM. pandas uses `NaN` (a float "not a number") for empty cells and `Timestamp` for dates. Neither serialises cleanly.
- **L70–71** Builds a new dict and loops over each key/value.
- **L72–73** Keeps `None` as is.
- **L74–75** A float that is NaN becomes `None`. `pd.isna` is the reliable NaN test, because `NaN == NaN` is False. numpy's `float64` is a subclass of `float`, so pandas' numeric cells are caught here too.
- **L76–77** A pandas `Timestamp` becomes an ISO-8601 string such as `2026-05-14T00:00:00`, or `None` if it is missing.
- **L78–79** Any other value is copied unchanged.
- **L80** Returns the cleaned dict.


#### Lines 83–94

````python
83  def _get_regulatory_rows() -> List[Dict[str, Any]]:
84      """Lazily load and cache the full Regulatory Requirements sheet in memory."""
85      global _regulatory_rows
86      if _regulatory_rows is None:
87          with _cache_lock:
88              if _regulatory_rows is None:
89                  df = _load_sheet(_REGULATORY_SHEET_NAME)
90                  _regulatory_rows = [_clean_record(r) for r in df.to_dict("records")]
91                  logger.info(f"Loaded {len(_regulatory_rows)} rows from '{_REGULATORY_SHEET_NAME}' sheet into memory")
92      return _regulatory_rows
93
94
````

- **L83–92** Same lazy, double-checked-locking pattern as `_get_cuso_df`, but for the regulatory sheet:
  - **L89** Loads the sheet.
  - **L90** `df.to_dict("records")` turns the DataFrame into a list of `{column: value}` dicts, one per row. Each row is cleaned with `_clean_record`, so the cache already holds safe, ready-to-return data.
  - **L91** Logs the row count.
- **L92** Returns the cached list. The same list object is returned on every call.


#### Lines 95–107

````python
 95  def _match_score(query: str, app_code: Optional[str], app_name: Optional[str], app_description: Optional[str]) -> float:
 96      """Score how relevant a CUSO Applications row is to a text query.
 97
 98      App Code/App Name are short identifiers, so an exact substring match (in
 99      either direction) is a strong signal. App Description is long free text -
100      a naive substring check there would match on common words, so it instead
101      uses a word-overlap ratio (fraction of the query's significant words that
102      appear in the description).
103      """
104      query_lower = query.strip().lower()
105      if not query_lower or len(query_lower) < 3:
106          return 0.0
107
````

- **L95** Scores how relevant one CUSO row is to a text query. It returns a float from 0 to 1, and `lookup_cuso_applications` keeps only rows scoring at least 0.55.
- **L96–103** The docstring explains the strategy. `App Code` and `App Name` are short identifiers, so substring and similarity matches are strong signals. `App Description` is long free text, so it uses word overlap instead of substring checks, which would match on common words.
- **L104** Lower-cases and trims the query so matching ignores case.
- **L105–106** Empty queries or queries shorter than 3 characters score 0. This avoids meaningless matches on tiny strings.


#### Lines 108–119

````python
108      best = 0.0
109      for field in (app_code, app_name):
110          if not field:
111              continue
112          field_lower = str(field).strip().lower()
113          if not field_lower:
114              continue
115          ratio = difflib.SequenceMatcher(None, query_lower, field_lower).ratio()
116          if query_lower in field_lower or field_lower in query_lower:
117              ratio = max(ratio, 0.9)
118          best = max(best, ratio)
119
````

- **L108** `best` tracks the highest score seen.
- **L109** Loops over the two short identifier fields, the code and the name, using a tuple `(app_code, app_name)`.
- **L110–114** Skips empty values, then lower-cases and trims the field.
- **L115** `difflib.SequenceMatcher(None, a, b).ratio()` returns a similarity between 0 and 1 based on matching character blocks. `None` means "no characters are treated as junk".
- **L116–117** If either string contains the other, the score is raised to at least 0.9. This is a strong signal, e.g. the query "Workday HCM" inside the name "Workday HCM RPA".
- **L118** Keeps the best score so far.
- Note: the check works in both directions, so a short field that appears anywhere inside a long query also scores 0.9. For example, a 4-character App Code that happens to occur inside a longer `impacted_applications` string would match. This is usually intended, but it can occasionally produce false positives.


#### Lines 120–131

````python
120      if app_description:
121          query_words = {w for w in query_lower.split() if len(w) >= 3}
122          if query_words:
123              description_lower = str(app_description).lower()
124              matched_words = sum(1 for w in query_words if w in description_lower)
125              overlap_ratio = matched_words / len(query_words)
126              # Word-overlap in a long description is a weaker signal than a
127              # name/code match, so scale it down relative to the 0.55 cutoff.
128              best = max(best, overlap_ratio * 0.6)
129
130      return best
131
````

- **L120** The description is scored only if the row has one.
- **L121** `{w for w in ... if len(w) >= 3}` is a **set comprehension** of the query's words that have 3 or more characters. Short words such as "of" or "to" are dropped as noise.
- **L122–125** Counts how many of those words appear in the lower-cased description, and divides by the number of query words to get an overlap ratio between 0 and 1. `sum(1 for ...)` is a common counting idiom.
- **L126–128** The comment explains that description overlap is a weaker signal, so the ratio is multiplied by 0.6. The effect is that a description-only match reaches the 0.55 cut-off only if about 92% or more of the query's words appear (0.55 ÷ 0.6 ≈ 0.917).
- **L130** Returns the best of the name, code and description scores.


#### Lines 132–160

````python
132
133  @tool
134  def lookup_cuso_applications(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
135      """Fuzzy-search the CUSO Applications reference sheet (~2,600 apps) for
136      entries relevant to a text query, without loading the entire sheet into
137      context.
138
139      Use this to find CUSO application metadata (criticality, data
140      classification, internet-facing status, SOX/payment-processing flags,
141      regional compliance, etc.) for an incident's impacted application(s) -
142      this context can help reason about the 4 FRB reportability criteria
143      (e.g. a Crown Jewel or payment-processing app is more likely to meet the
144      financial/operational impact criteria).
145
146      Args:
147          query: Free text to match against App Name, App Code, and App
148              Description - typically an incident's impacted_applications value.
149          top_k: Max number of matching rows to return (default 3, keeps the
150              result small enough to avoid context overflow).
151
152      Returns:
153          List of up to top_k matching rows as dicts (App Description is
154          truncated to a preview if very long). Empty list if no reasonable
155          match was found.
156
157      Example:
158          lookup_cuso_applications("Workday HCM")
159          # -> [{"App Code": "0AM0", "App Name": "Workday HCM RPA", ...}]
160      """
````

- **L133** `@tool` turns the function into a LangChain `StructuredTool`. The docstring becomes the description the LLM reads, and the typed arguments (`query: str`, `top_k: int = 3`) become the tool's input schema.
- **L134** Returns a list of row dicts.
- **L135–160** The docstring, written for the LLM:
  - **L139–144** Use the tool to find app metadata (criticality, data classification, internet-facing status, SOX and payment-processing flags) for the incident's impacted applications, because that helps reason about the **4 FRB reportability criteria**. For example, a "Crown Jewel" or payment app makes financial or operational impact more likely.
  - **L146–150** Arguments: the query is usually the incident's `impacted_applications` value. `top_k` defaults to 3 to keep the output small.
  - **L152–155** Returns up to `top_k` rows with descriptions truncated, or an empty list.
  - **L157–159** A usage example.


#### Lines 161–185

````python
161      try:
162          df = _get_cuso_df()
163          scored = []
164          for _, row in df.iterrows():
165              score = _match_score(query, row.get("App Code"), row.get("App Name"), row.get("App Description"))
166              if score >= 0.55:
167                  scored.append((score, row))
168
169          scored.sort(key=lambda pair: pair[0], reverse=True)
170          results = []
171          for score, row in scored[:top_k]:
172              record = _clean_record(row.to_dict())
173              description = record.get("App Description")
174              if isinstance(description, str) and len(description) > _APP_DESCRIPTION_PREVIEW_CHARS:
175                  record["App Description"] = description[:_APP_DESCRIPTION_PREVIEW_CHARS] + "... [truncated]"
176              results.append(record)
177
178          logger.info(f"lookup_cuso_applications: query={query!r}, matched {len(results)}/{len(scored)} candidates")
179          log_tool_call("lookup_cuso_applications", {"query": query, "top_k": top_k}, outputs=f"{len(results)} rows")
180          return results
181      except Exception as e:
182          logger.error(f"lookup_cuso_applications failed: {e}")
183          log_tool_call("lookup_cuso_applications", {"query": query, "top_k": top_k}, error=str(e))
184          raise
185
````

- **L161–162** Gets the cached DataFrame, loading it on first call.
- **L163–167** Scores every row. `df.iterrows()` yields `(index, row)` pairs; the index is discarded with `_`. Each `row` is a pandas `Series`, and `row.get("App Code")` reads a column like a dict lookup, returning `None` if the column doesn't exist. Rows scoring 0.55 or more are kept as `(score, row)` tuples.
- **L169** Sorts candidates highest score first. The key `lambda pair: pair[0]` picks the score out of each tuple.
- **L170–176** Builds results from the top `top_k` rows (`scored[:top_k]` is a slice):
  - **L172** Converts the Series to a dict and cleans NaN and Timestamp values.
  - **L173–175** If the `App Description` is longer than 600 characters, it is cut to 600 and `"... [truncated]"` is appended, so the LLM knows the text is incomplete.
  - **L176** Adds the record to the results.
- **L178** Logs the query (`{query!r}` uses `repr`, so the string appears with quotes) and how many rows were returned out of how many candidates.
- **L179–180** Records the tool call and returns the results.
- **L181–184** On any error (e.g. the workbook is missing), it logs, records the error and re-raises. `assessment_service` wraps the call in its own `try` and just logs a warning, so a missing workbook doesn't stop an assessment.
- (Performance aside: `iterrows` over ~2,600 rows runs on every call. That's fine at this scale but would be slow for much larger sheets.)


#### Lines 186–212

````python
186
187  @tool
188  def get_regulatory_requirements() -> List[Dict[str, Any]]:
189      """Return the full Regulatory Requirements reference sheet (~15 rows).
190
191      Unlike CUSO Applications, this sheet is small enough to return in full so
192      the agent can reason over all regulator/region/legal-entity combinations
193      directly, rather than relying on a fuzzy match that might miss the
194      relevant row. Use this to determine which regulator(s), reporting
195      thresholds, and notification requirements apply given an incident's
196      legal_entity/region, as additional context for the 4 FRB criteria.
197
198      Returns:
199          List of all rows in the sheet as dicts (columns include Region,
200          Location/Country, Regulator / Member Associations, Regulatory body,
201          LOB Affected / In scope Legal Entity, Reporting Requirement/Threshold,
202          Incident Notification Requirement, etc.).
203      """
204      try:
205          rows = _get_regulatory_rows()
206          log_tool_call("get_regulatory_requirements", {}, outputs=f"{len(rows)} rows")
207          return rows
208      except Exception as e:
209          logger.error(f"get_regulatory_requirements failed: {e}")
210          log_tool_call("get_regulatory_requirements", {}, error=str(e))
211          raise
212
````

- **L187–188** A second LangChain tool. It takes no arguments and returns all regulatory rows.
- **L189–203** The docstring for the LLM explains why it returns everything: the sheet is small, and a fuzzy match might miss the relevant row. It should be used to work out which regulators, thresholds and notification rules apply given the incident's legal entity or region. **L199–202** list the main columns: Region, Location/Country, Regulator, Regulatory body, LOB / in-scope legal entity, Reporting Requirement/Threshold, and Incident Notification Requirement.
- **L204–207** Gets the cached rows (loading them on first call), logs the call with the row count, and returns them.
- **L208–211** Error path: log, record the error, and re-raise.


---

## 31. `backend/tools/audit_tools.py`

*59 lines · complete*

A tiny file that exposes the audit trail as a LangChain **tool**, so an LLM agent can record "who did what, when" in the `audit_logs` table. For a bank's regulatory process, an audit trail is a compliance requirement: reviewers and regulators must be able to reconstruct every decision. The tool is a thin wrapper around `PostgresClient.audit_log` (which does the actual `INSERT`), adding normal logging and tool-call logging. The assessment and drafting agents include it in their tool lists (`backend/agents/*.py`). Service code such as `workflow_service.py` calls `get_client().audit_log(...)` directly instead of going through this tool.


#### Lines 1–9

````python
1  """Audit logging tools for compliance tracking."""
2
3  from typing import Dict, Any, Optional
4  from langchain_core.tools import tool
5  from backend.utils.postgres_client import get_client
6  from backend.logger import get_logger, log_tool_call
7
8  logger = get_logger(__name__)
9
````

- **L1**: module docstring.
- **L3**: typing helpers for the signature.
- **L4**: LangChain's `@tool` decorator (it turns a function into a tool an LLM can call; the docstring becomes the tool description the model reads).
- **L5**: the shared Postgres client getter.
- **L6**: the regular logger factory and `log_tool_call`, which writes a JSON record of each tool invocation to the `tools` logger.
- **L8**: module-level logger.


#### Lines 10–17

````python
10
11  @tool
12  def audit_log(incident_number: str, action: str, actor: str, details: Dict[str, Any], actor_id: Optional[str] = None) -> None:
13      """Log action to audit trail for compliance.
14
15      Every significant action in the system should be logged to the audit_logs table.
16      This creates an immutable record of who did what and when.
17
````

- **L11**: `@tool` wraps `audit_log` as a LangChain `StructuredTool`. Its argument schema is generated from the type hints on L12, so the LLM knows which arguments to send.
- **L12**: parameters:
  - `incident_number`: which incident.
  - `action`: event name.
  - `actor`: who did it.
  - `details`: a dict saved as JSONB.
  - `actor_id`: optional; only for humans.
  It returns `None`.
- **L13–16**: docstring opening: every significant action should be logged, creating an "immutable record" (append-only: rows are inserted, never updated by this code).


#### Lines 18–24

````python
18      Args:
19          incident_number: Incident number this action relates to
20          action: Action type (e.g., "reportability_assessed", "status_transitioned", "template_created")
21          actor: Actor name (e.g., "Assessment Agent", "Drafting Agent", or user name)
22          details: JSONB details about the action (e.g., {"reportability_flag": "Yes", "rationale": "..."})
23          actor_id: Optional user ID if actor is human (None for agents)
24
````

- **L18–23**: the `Args` section, with conventions for each field: action names like `reportability_assessed`, `status_transitioned`, `template_created`; actors like `"Assessment Agent"`, `"Drafting Agent"` or a user's name; `details` as free-form JSON; `actor_id` only for humans (`None` for agents). The LLM reads this text, so these examples steer what it writes.


#### Lines 25–41

````python
25      Examples:
26          # Log assessment completion
27          audit_log(
28              incident_number="INC0575428",
29              action="reportability_assessed",
30              actor="Assessment Agent",
31              details={"reportability_flag": "Yes", "provided_by": "Assessment Agent"}
32          )
33
34          # Log status transition
35          audit_log(
36              incident_number="INC0575428",
37              action="status_transitioned",
38              actor="Workflow Agent",
39              details={"from_status": "Intake", "to_status": "Assessment"}
40          )
41      """
````

- **L25–40**: two usage examples, one logging an assessment result and one logging a status change from `Intake` to `Assessment`. Note the second example uses the actor `"Workflow Agent"`, while the real workflow engine uses `"Workflow Engine"` (`ACTOR` in `workflow_service.py`). This only matters if someone filters the audit table by actor name.
- **L41** closes the docstring.


#### Lines 42–50

````python
42      try:
43          client = get_client()
44          client.audit_log(incident_number, action, actor, details, actor_id)
45          logger.info(f"audit_log: incident={incident_number}, action={action}, actor={actor}")
46          log_tool_call(
47              "audit_log",
48              {"incident_number": incident_number, "action": action, "actor": actor},
49              outputs="Audit log written successfully"
50          )
````

- **L42**: `try` so that failures can be logged and then re-raised.
- **L43**: gets the singleton DB client.
- **L44**: delegates to `PostgresClient.audit_log`, which runs a parameterised `INSERT INTO audit_logs (incident_number, action, actor, actor_id, details) VALUES (%s, ...)` with `details` converted to JSON text by `json.dumps`. Using `%s` placeholders lets the driver escape the values safely (no SQL injection).
- **L45**: info log (an f-string with the incident, action and actor).
- **L46–50**: structured tool-call log entry with the key inputs and a success message.


#### Lines 51–59

````python
51      except Exception as e:
52          logger.error(f"audit_log failed: {e}")
53          log_tool_call(
54              "audit_log",
55              {"incident_number": incident_number, "action": action, "actor": actor},
56              error=str(e)
57          )
58          raise
59
````

- **L51**: catches any exception (e.g. the DB is unreachable).
- **L52**: error log.
- **L53–57**: tool-call log with `error=str(e)`.
- **L58**: `raise` re-throws the original exception so the caller knows the audit write failed. This is unlike `workflow_service._audit`, which swallows audit errors so the workflow keeps going.


---

## 32. `backend/utils/__init__.py`

*37 lines · complete*

This is the package initialiser for `backend/utils/`. Python runs it the first time anything under `backend.utils` is imported, including `from backend.utils.llm_service import LLMService`. It re-exports `PostgresClient` and `get_client` so callers *could* write `from backend.utils import get_client`. It also defines three small helpers: UTC timestamps, ISO conversion and a standard error-response dict. In the backend files transcribed here, every caller imports `get_client` from `backend.utils.postgres_client` directly, and the three helpers are not referenced anywhere else. They look like leftover or general-purpose utilities.


#### Lines 1–7

````python
1  """Utilities package for Anchor AI backend."""
2
3  from datetime import datetime, timezone
4  from typing import Any
5
6  from .postgres_client import PostgresClient, get_client
7
````

- **L1** The package docstring.
- **L3** `datetime` and `timezone` are used to build timezone-aware UTC timestamps.
- **L4** `Any` is a type hint meaning "any type".
- **L6** A **relative import**: the leading `.` means "from this same package". It pulls `PostgresClient` and `get_client` into the package namespace. A side effect is that importing *anything* from `backend.utils` also imports `postgres_client` (and therefore `psycopg_pool` and `backend.config`). That only loads code. No DB connection is opened until `get_client()` is first called.


#### Lines 8–17

````python
 8
 9  def utc_timestamp() -> str:
10      """Generate a UTC timestamp in ISO 8601 format with 'Z' suffix."""
11      return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
12
13
14  def to_isoformat(value: Any) -> str:
15      """Convert datetime object to ISO 8601 string, or return as-is if already string."""
16      return value.isoformat() if hasattr(value, 'isoformat') else value
17
````

- **L9–11** `utc_timestamp()` returns the current time as an ISO-8601 string in UTC with a `Z` suffix, e.g. `2026-10-29T14:03:12.123456Z`. `datetime.now(timezone.utc)` gives a timezone-aware time, and `.isoformat()` produces `...+00:00`. The `.replace('+00:00', 'Z')` swaps that for the shorter "Zulu" notation that JavaScript front ends and many APIs expect.
- **L14–16** `to_isoformat(value)` converts `datetime` or `date` objects to ISO strings, and returns anything else (e.g. an already-formatted string or `None`) unchanged. `hasattr(value, 'isoformat')` is **duck typing**: it checks for the method rather than the class. The one-line `x if cond else y` is Python's conditional expression. This is useful when returning DB rows as JSON, because datetimes aren't JSON-serialisable.


#### Lines 18–28

````python
18
19  def build_error_response(error_code: str, message: str, **details) -> dict[str, Any]:
20      """Build a consistent error response dictionary."""
21      response = {
22          "error": error_code,
23          "message": message
24      }
25      if details:
26          response["details"] = details
27      return response
28
````

- **L19** `build_error_response(error_code, message, **details)` builds a consistent error dict. `**details` collects any extra keyword arguments into a dict. For example, `build_error_response("NOT_FOUND", "Incident missing", incident_number="INC1")` gives `details={"incident_number": "INC1"}`. The return type `dict[str, Any]` uses the built-in generic syntax available from Python 3.9.
- **L21–24** The base response has `error` (a machine-readable code) and `message` (human-readable text).
- **L25–26** A `details` key is added only if extra keyword arguments were passed.
- **L27** Returns the dict.


#### Lines 29–37

````python
29
30  __all__ = [
31      'utc_timestamp',
32      'to_isoformat',
33      'build_error_response',
34      'PostgresClient',
35      'get_client',
36  ]
37
````

- **L30–36** `__all__` lists the names exported by `from backend.utils import *`. It also documents the package's public API: the three helpers plus the re-exported Postgres client and factory.


---

## 33. `backend/utils/_logger.py`

*9 lines · complete*

This is a tiny logger helper for modules inside `backend/utils/`. Only `postgres_client.py` uses it. It does the same thing as `backend.logger.get_logger`, but importing it has **no side effects**. Importing `backend/logger.py` creates the `logs/` directory, opens a timestamped log file and reconfigures the root logger. Utility modules can use this helper to get a logger without triggering that setup. Because Python loggers are hierarchical, messages from these loggers still reach the root handlers that `backend.logger` sets up once the app has imported it.


#### Lines 1–9

````python
1  """Simple logger helper for backend.utils modules."""
2
3  import logging
4
5
6  def get_logger(name: str) -> logging.Logger:
7      """Return a logger with the given name."""
8      return logging.getLogger(name)
9
````

- **L1** The docstring says this is a simple logger helper for `backend.utils` modules.
- **L3** Imports Python's standard `logging` module.
- **L6–8** `get_logger(name)` simply returns `logging.getLogger(name)`. The type hint `-> logging.Logger` documents the return type. `getLogger` always returns the **same** logger object for the same name, and dotted names such as `backend.utils.postgres_client` form a tree. Child loggers pass their records up to the root logger, which is where the app's stdout and file handlers live. Adding the leading underscore to the module name (`_logger`) marks it as internal to the package.


---

## 34. `backend/utils/llm_service.py`

*243 lines · complete*

`LLMService` is the app's single gateway to the language model. Every LLM call goes through it: the assessment tool's `evaluate_reportability`, the drafting tool's `generate_template_content`, and the LLM-as-judge in `evaluate_drafting_prompts.py`. The model sits behind a corporate (RBC) gateway that uses **OAuth client-credentials** authentication. The service gets a short-lived access token from the OAuth URL, refreshes it before it expires (with a thread lock so concurrent workers don't all refresh at once), and builds an `openai.OpenAI` client pointed at the gateway with that token. It provides `chat` (plain text, tool-calling, or Pydantic-structured output), `chat_structured` (a typed shortcut) and `get_embeddings`.


#### Lines 1–17

````python
 1  """
 2  LLM service for chat completions and embeddings with OAuth token management.
 3  """
 4
 5  import os
 6  import requests
 7  import datetime
 8  import logging
 9  import threading
10  import urllib3
11  import httpx
12  from openai import OpenAI, APIError, RateLimitError, APIConnectionError
13  from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
14  from typing import List, Dict, Optional, Type, TypeVar
15  from pydantic import BaseModel
16
17  logger = logging.getLogger(__name__)
````

- **L1–3** The module docstring: chat completions and embeddings, with OAuth token management.
- **L5** `os` is not used in the visible lines. It is most likely used in the hidden constructor to read `LLM_OAUTH_URL`, `LLM_CLIENT_ID`, `LLM_CLIENT_SECRET`, `LLM_BASE_URL` and `LLM_MODEL` with `os.getenv`. `backend/config.py` says this file loads those variables directly.
- **L6** `requests` is a simple HTTP library, used for the OAuth token POST (L108).
- **L7** `datetime` is used to compute and compare token expiry times.
- **L8** `logging` is Python's standard logging module.
- **L9** `threading` is most likely used to create `self._token_lock` (a `threading.Lock`) in the hidden constructor. The lock is used on L88.
- **L10** `urllib3` is the low-level HTTP library underneath `requests`. It is imported here only to silence its TLS warnings (L20).
- **L11** `httpx` is the HTTP library the OpenAI SDK uses internally. It is not used in the visible lines. It was probably used in the hidden L132–153 to pass a custom `http_client=httpx.Client(verify=..., trust_env=...)` to `OpenAI(...)`, so that the TLS and proxy settings apply to LLM calls too (inference).
- **L12** `OpenAI` is the official SDK client, which works with any OpenAI-compatible gateway. `APIError`, `RateLimitError` and `APIConnectionError` are the SDK's exception types. They aren't used in the visible lines, and were most likely passed to tenacity's `retry_if_exception_type` (inference).
- **L13** **tenacity** is a retry library. `@retry(stop=stop_after_attempt(n), wait=wait_exponential(...), retry=retry_if_exception_type(...))` re-runs a function when it fails, waiting longer each time (exponential backoff). A comment in `backend/config.py` confirms there is a tenacity `@retry` on `LLMService.chat()`. It sits in the hidden lines just before L154.
- **L14** Type hints. `Type[T]` means "the class T itself, not an instance". `TypeVar` is explained at L29.
- **L15** `BaseModel` from Pydantic is used as the bound for `T`.
- **L17** A module logger. It uses `logging.getLogger` directly, which is equivalent to the project's `get_logger`.


#### Lines 18–33

````python
18
19  # Disable SSL warnings for enterprise environments
20  urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
21
22  # Enable RBC corporate certificate handling for proxy authentication
23  try:
24      from rbc_security import enable_certs
25      enable_certs()
26  except ImportError:
27      logger.warning("rbc_security not available - proxy authentication may fail in corporate network")
28
29  T = TypeVar('T', bound=BaseModel)
30
31  # Load environment variables from .env file if present
32  from dotenv import load_dotenv
33  load_dotenv()
````

- **L19–20** Turns off urllib3's `InsecureRequestWarning`. Without this, every HTTPS request made with `verify=False` would print a warning. TLS verification is off by default here (see L99 and the note there).
- **L22–27** Tries to import `enable_certs` from `rbc_security`, an internal RBC package, and calls it. This most likely installs the corporate root CA certificates so that HTTPS through RBC's proxies and internal hosts works. On a machine without the package (e.g. outside the corporate network), the `ImportError` is caught and a warning is logged, so the app still imports. `backend/logger.py` mentions that this call logs at import time, which is why that module configures the root logger by hand instead of with `basicConfig()`.
- **L29** `T = TypeVar('T', bound=BaseModel)` declares a **generic type variable**: "some subclass of `BaseModel`". In `chat_structured(..., response_format: Type[T]) -> T` (L213), it tells type checkers and IDEs that passing `TemplateContent` returns a `TemplateContent` instance.
- **L32–33** `load_dotenv()` reads a `.env` file, if there is one, into `os.environ`. By default it does not override variables that are already set. This lets developers keep the `LLM_*` credentials in a local `.env` file. The import is placed mid-file, which is unusual style but works.


#### Lines 34–45

````python
34
35  # Import logging helpers and config
36  from backend.logger import log_llm_call
37  from backend.config import (
38      LLM_CA_BUNDLE,
39      LLM_REQUEST_TIMEOUT,
40      LLM_USE_ENV_PROXY,
41      LLM_VERIFY_SSL,
42      OAUTH_REQUEST_TIMEOUT,
43      OAUTH_TOKEN_REFRESH_BUFFER_MINUTES,
44  )
45
````

- **L36** `log_llm_call` writes a structured JSON line for each LLM call (model, messages, truncated response or error) to the LLM log, for auditing.
- **L37–44** Settings from `backend/config.py`:
  - `LLM_CA_BUNDLE` is the CA certificate file for TLS. It defaults to `SSL_CERT_FILE` or `True`, where `True` means use the default certificates.
  - `LLM_REQUEST_TIMEOUT` is a per-request timeout (default 120 s). The config comment explains it was added after a stalled connection hung a batch job for more than 16 hours. A timeout turns a hang into an exception that tenacity can retry.
  - `LLM_USE_ENV_PROXY` controls whether shell `HTTP(S)_PROXY` variables are honoured (default false).
  - `LLM_VERIFY_SSL` controls whether certificates are verified (default **false**).
  - `OAUTH_REQUEST_TIMEOUT` is a `(10, 30)` tuple: 10 s to connect and 30 s to read.
  - `OAUTH_TOKEN_REFRESH_BUFFER_MINUTES` (default 5) is how early to refresh the token.


#### Lines 46–81

````python
46  class LLMService:
47      """
48      LLM service for chat completions and embeddings.
49      Handles OAuth token management and API retries.
50      """
51
52      def __init__(self):
53          """Initialize LLM service with configuration from environment variables."""
54
55          # Validate required environment variables
56          missing = [v for v in ["LLM_OAUTH_URL", "LLM_CLIENT_ID", "LLM_CLIENT_SECRET", "LLM_BASE_URL"] if not os.getenv(v)]
57          if missing:
58              raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
59
60          # Load configuration from environment
61          self.oauth_url = os.getenv("LLM_OAUTH_URL")
62          self.client_id = os.getenv("LLM_CLIENT_ID")
63          self.client_secret = os.getenv("LLM_CLIENT_SECRET")
64          self.gateway_url = os.getenv("LLM_BASE_URL")
65          self.model_name = os.getenv("LLM_MODEL", "gpt-4.1-2025-04-14-eastus-dz")
66          self.max_output_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))
67          self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
68
69          self._oauth_token: Optional[str] = None
70          self._token_expiry: Optional[datetime.datetime] = None
71          self.client: Optional[OpenAI] = None
72          # Protect token refresh if one LLMService instance is shared by
73          # concurrent callers. Without this lock, callers seeing an expired token
74          # could issue duplicate OAuth requests and race while replacing the client.
75          self._token_lock = threading.Lock()
76
77      @retry(
78          stop=stop_after_attempt(3),
79          wait=wait_exponential(multiplier=1, min=2, max=10),
80          retry=retry_if_exception_type((requests.exceptions.ConnectionError, requests.exceptions.Timeout))
81      )
````

- **L46** `class LLMService:` starts the class definition. Other modules create one instance and call its methods.
- **L47–50** The class docstring: chat completions and embeddings, plus OAuth token management and API retries.
- **L52–53** `__init__` is the **constructor**. It runs when someone writes `LLMService()`, and `self` is the new object being set up.
- **L55–58** **Fail fast on missing configuration:**
  - L56 is a *list comprehension*. It loops over the four required variable names and keeps each one where `os.getenv(v)` is unset or empty.
  - If any are missing, L58 raises `ValueError` listing them all. `', '.join(missing)` glues the names together with commas inside the f-string.
  - The point is to get a clear "Missing required environment variables: LLM_CLIENT_SECRET" when the service is created, instead of a confusing authentication failure halfway through a batch job.
  - `LLM_MODEL` isn't required because it has a default.
- **L60–67** Copy the settings onto the object:
  - `oauth_url`, `client_id` and `client_secret` are used for the token request.
  - `gateway_url` (from `LLM_BASE_URL`) is the bank's OpenAI-compatible gateway endpoint.
  - `model_name` defaults to `"gpt-4.1-2025-04-14-eastus-dz"`: the GPT-4.1 snapshot from 2025-04-14, deployed in the East US region. The suffix looks like a gateway or Azure deployment name; *inference*: "dz" probably means a data-zone deployment.
  - `max_output_tokens` (default 4096) caps how long a reply can be when the caller doesn't pass `max_tokens`.
  - `embedding_model` (default `text-embedding-3-small`) is used by `get_embeddings`.
  - Unlike the network settings (timeouts, proxy, TLS), these are read straight from the environment rather than from `backend/config.py`. The `load_dotenv()` on L33 is what makes `.env` values visible here.
- **L69–71** Typed attribute declarations. `name: Type = value` is a type hint plus an assignment. The token, its expiry and the OpenAI client all start as `None`.
  - The constructor does **not** fetch a token. The first call to `_ensure_valid_token()` does that, and also builds `self.client` (L131).
- **L72–75** A `threading.Lock` guarding the token refresh. The comment gives the reason: one `LLMService` can be shared by several worker threads (batch runs), and without the lock several threads could see an expired token at the same moment, each send an OAuth request, and race to replace `self.client`.
- **L77–81** A **tenacity `@retry` decorator** on the next method. A decorator wraps a function, so calling `_ensure_valid_token()` actually runs tenacity's retry loop around it.
  - `stop_after_attempt(3)` allows at most 3 tries.
  - `wait_exponential(multiplier=1, min=2, max=10)` waits between tries with growing delays, at least 2 s and at most 10 s.
  - `retry_if_exception_type((...ConnectionError, ...Timeout))` retries **only** network-level `requests` failures during the OAuth call. A clean rejection (the `Exception` raised on L121 for a bad status) is not retried.
  - When all 3 tries fail, tenacity raises a `RetryError` wrapping the last exception, because `reraise=True` isn't set.


#### Lines 82–95

````python
82      def _ensure_valid_token(self) -> None:
83          """
84          Ensure OAuth token is valid, refreshing if expired or missing.
85          Automatically retries on connection errors. Thread-safe: concurrent
86          callers block on the same refresh rather than each starting their own.
87          """
88          with self._token_lock:
89              if self._oauth_token and self._token_expiry:
90                  buffer = datetime.timedelta(minutes=OAUTH_TOKEN_REFRESH_BUFFER_MINUTES)
91                  if datetime.datetime.now() < (self._token_expiry - buffer):
92                      logger.debug("OAuth token still valid")
93                      return
94              self._refresh_token()
95
````

- **L82** `_ensure_valid_token()` makes sure a usable OAuth token exists before an API call. `-> None` means it returns nothing.
- **L83–87** The docstring. It refreshes the token if it is expired or missing, retries automatically on connection errors (that's the `@retry` above), and is thread-safe: concurrent callers wait on the same refresh instead of each starting their own.
- **L88** `with self._token_lock:` acquires the lock for the whole check-and-refresh.
  - If several threads (e.g. the `BATCH_MAX_WORKERS` batch workers) find the token expired at the same moment, only one refreshes it. The others wait, then see the fresh token.
  - `backend/config.py` notes the lock was added because simultaneous refreshes probably caused intermittent "407 Proxy Authentication Required" errors.
- **L89–93** If there is a token and an expiry time:
  - It computes a safety buffer (`timedelta(minutes=...)`).
  - If "now" is still earlier than "expiry minus buffer", the token is good: it logs at debug level and returns early.
- **L94** Otherwise, it refreshes the token while still holding the lock.


#### Lines 96–102

````python
 96      def _refresh_token(self) -> None:
 97          """Actually perform the OAuth refresh. Only call while holding _token_lock."""
 98          logger.info("Refreshing OAuth token...")
 99          tls_verify = LLM_CA_BUNDLE if LLM_VERIFY_SSL else False
100          if not LLM_VERIFY_SSL:
101              logger.warning("LLM TLS certificate verification is disabled")
102
````

- **L96–97** `_refresh_token()` performs the actual OAuth call. The docstring warns that it must only be called while holding `_token_lock`.
- **L98** Logs that a refresh is starting.
- **L99** Chooses the `verify=` value for HTTPS. If verification is enabled, it uses the CA bundle (a file path, or `True`). Otherwise it uses `False`, which means "don't check the server certificate".
- **L100–101** Logs a warning when verification is disabled.
- Note: because `LLM_VERIFY_SSL` defaults to `"false"` in `config.py`, **TLS certificate verification is off unless someone sets `LLM_VERIFY_SSL=true`**, and L20 hides the related warnings. That makes the connection open to man-in-the-middle interception. It is probably a workaround for corporate proxy certificates (which is what `LLM_CA_BUNDLE` and `rbc_security` are for), and it's worth confirming with the team that it is turned on in deployed environments.


#### Lines 103–119

````python
103          with requests.Session() as session:
104              # The RBC OAuth and gateway hosts are directly reachable. By
105              # default, do not inherit shell proxy variables that can route
106              # these internal requests through an unauthenticated proxy.
107              session.trust_env = LLM_USE_ENV_PROXY
108              response = session.post(
109                  url=self.oauth_url,
110                  data={
111                      "grant_type": "client_credentials",
112                      "client_id": self.client_id,
113                      "client_secret": self.client_secret,
114                  },
115                  headers={"Content-Type": "application/x-www-form-urlencoded"},
116                  timeout=OAUTH_REQUEST_TIMEOUT,
117                  verify=tls_verify,
118              )
119
````

- **L103** `with requests.Session() as session:` opens an HTTP session as a context manager, so its connections are closed when the block ends.
- **L104–107** The comment and `session.trust_env = LLM_USE_ENV_PROXY` work together. With `trust_env=False` (the default here), `requests` ignores `HTTP_PROXY`/`HTTPS_PROXY` environment variables and `.netrc`. The OAuth host is reachable directly, and a shell proxy could otherwise route the request through an unauthenticated corporate proxy.
- **L108–118** A `POST` to `self.oauth_url` using the standard **OAuth 2.0 client-credentials grant**:
  - **L110–114** The form body contains `grant_type=client_credentials` and the app's `client_id` and `client_secret`, which identify the application itself rather than a user.
  - **L115** The body is sent form-encoded, as the OAuth spec requires.
  - **L116** Connect and read timeouts.
  - **L117** The TLS verification setting from L99.


#### Lines 120–130

````python
120          if not response.ok:
121              raise Exception(f"Failed to obtain OAuth token: {response.status_code} - {response.text}")
122
123          token_data = response.json()
124          self._oauth_token = token_data["access_token"]
125
126          expires_in = token_data.get("expires_in", 3600)
127          self._token_expiry = datetime.datetime.now() + datetime.timedelta(seconds=expires_in - 300)
128
129          logger.debug(f"OAuth token refreshed, expires at {self._token_expiry}")
130
````

- **L120–121** `response.ok` is true for HTTP status codes below 400. On failure, it raises an `Exception` that includes the status code and response body, to help debugging.
- **L123–124** Parses the JSON reply and stores `access_token`. A missing key raises `KeyError`.
- **L126** `expires_in` is the token lifetime in seconds. It defaults to 3600 (1 hour) if the server omits it.
- **L127** The expiry is stored as now + lifetime − **300 seconds**.
- Note: L90 then subtracts another `OAUTH_TOKEN_REFRESH_BUFFER_MINUTES` (default 5 min), so in practice the token is refreshed about 10 minutes before it really expires. This is harmless double padding, not a bug.
- **L129** Debug log of the new expiry time.


#### Lines 131–153

````python
131          self.client = OpenAI(
132              api_key=self._oauth_token,
133              base_url=self.gateway_url,
134              http_client=httpx.Client(
135                  trust_env=LLM_USE_ENV_PROXY,
136                  verify=tls_verify,
137                  # No timeout here previously let a single stalled connection
138                  # (e.g. a corporate proxy that accepts the connection but
139                  # never responds) block this call - and therefore an entire
140                  # serial batch job - forever, with no exception ever raised
141                  # for @retry/callers to act on. Bounding it means a stuck
142                  # call eventually raises APITimeoutError (a subclass of
143                  # APIConnectionError, so still covered by chat()'s @retry)
144                  # instead of hanging indefinitely.
145                  timeout=httpx.Timeout(LLM_REQUEST_TIMEOUT, connect=10.0),
146              ),
147          )
148
149      @retry(
150          stop=stop_after_attempt(3),
151          wait=wait_exponential(multiplier=1, min=2, max=10),
152          retry=retry_if_exception_type((RateLimitError, APIConnectionError, APIError))
153      )
````

- **L131** `self.client = OpenAI(` rebuilds the OpenAI SDK client **every time the token is refreshed**, because the token is baked into the client as its API key.
- **L132** `api_key=self._oauth_token`: the SDK sends `api_key` as a bearer token in the `Authorization` header, which is what the gateway expects.
- **L133** `base_url=self.gateway_url` sends requests to the bank's gateway instead of `api.openai.com`.
- **L134–136** A custom `httpx.Client`. `httpx` is the HTTP library the OpenAI SDK uses internally, and passing your own client lets you control networking.
  - `trust_env=LLM_USE_ENV_PROXY` applies the same "ignore shell proxy variables" policy as the OAuth session (L107).
  - `verify=tls_verify` applies the same TLS setting as L99.
- **L137–144** The comment records a past incident:
  - With no timeout, one stalled connection (e.g. a corporate proxy that accepts the connection and never answers) could block this call, and therefore a whole serial batch job, **forever**, with no exception for anything to react to.
  - A bounded timeout makes a stuck call raise `APITimeoutError` instead.
  - Because `APITimeoutError` is a subclass of `APIConnectionError`, `chat()`'s `@retry` (L149–153) will retry it.
- **L145** `httpx.Timeout(LLM_REQUEST_TIMEOUT, connect=10.0)` sets read, write and pool waits to `LLM_REQUEST_TIMEOUT` (default 120 s, from `config.py`), but caps the initial *connect* at 10 s, since a healthy server accepts connections quickly.
- **L146–147** Close the `httpx.Client(...)` and `OpenAI(...)` calls.
- Note: the editor flags a type-checker error on L134 (an `httpx.Client` "cannot be assigned to parameter `http_client`"), and another on L109 (`str | None` passed as `url`). The L109 one happens because `os.getenv` returns `Optional[str]`; L56–58 guarantee the value exists at runtime, but the type checker can't know that. The L134 one usually means the type checker sees a different `httpx` version from the one `openai`'s type hints expect. Both are warnings from static analysis, not runtime errors. They account for some of the "7 problems" shown on this file's tab.
- **L149–153** The `@retry` decorator for `chat` (the next method):
  - Up to 3 attempts, with the same 2–10 s exponential backoff.
  - It retries on `RateLimitError` (HTTP 429, too many requests), `APIConnectionError` (network failures, including the timeouts above) and `APIError`.
  - Each retry calls `chat` again, which runs `_ensure_valid_token()` again, so a token that expired mid-batch gets refreshed on the retry.
  - Note: `APIError` is the base class of almost every OpenAI SDK error, including `BadRequestError` (400) and `AuthenticationError` (401). So requests that can never succeed (a malformed request, a bad key) are also retried twice, adding delay before the failure surfaces.


#### Lines 154–174

````python
154      def chat(
155          self,
156          messages: List[Dict[str, str]],
157          max_tokens: Optional[int] = None,
158          response_format: Optional[Type[T]] = None,
159          tools: Optional[List[Dict[str, str]]] = None,
160          tool_choice: Optional[str] = None,
161          **kwargs
162      ):
163          """Generate chat completion with optional structured output or tool calling."""
164          self._ensure_valid_token()
165
166          logger.debug(f"LLM chat request: model={self.model_name}, messages={len(messages)}, max_tokens={max_tokens}")
167
168          api_params = {
169              "model": self.model_name,
170              "messages": messages,
171              "max_tokens": max_tokens if max_tokens is not None else self.max_output_tokens,
172              **kwargs
173          }
174
````

- **L154–162** The `chat` signature:
  - **`messages`** is the conversation, as a list of dicts like `{"role": "system", "content": "..."}` / `{"role": "user", ...}`.
  - **`max_tokens`** is an optional per-call cap on reply length.
  - **`response_format: Optional[Type[T]]`** is a Pydantic model **class** (not an instance) to force structured output. `T` is the `TypeVar` bound to `BaseModel` from L29, so type checkers know the parsed result will be an instance of whatever class you passed.
  - **`tools`** is a list of OpenAI function-calling schemas. Note: the hint `Dict[str, str]` is looser than reality, since real tool schemas are nested dicts. It only affects type checking.
  - **`tool_choice`** is an optional `"auto"` / `"none"` / a specific tool.
  - **`**kwargs`** collects any extra keyword arguments (e.g. `temperature=0`) so they can be passed through to the API unchanged.
- **L163** Docstring: chat completion with optional structured output or tool calling.
- **L164** `self._ensure_valid_token()` makes sure a fresh token and client exist before **every** call.
- **L166** A debug log with the model name, how many messages are being sent, and the requested `max_tokens`. It records the count, not the content.
- **L168–173** Builds `api_params`, the keyword arguments for the SDK call:
  - `model` and `messages`.
  - `max_tokens` uses the caller's value if given, otherwise the 4096 default (L66). `x if cond else y` is Python's conditional expression, and `is not None` means an explicit `0` would still be respected.
  - `**kwargs` unpacks any extra arguments into the dict.
  - Lines L175 onward add `tools` and `response_format` to this same dict when they're needed.
- **L174** Blank.


#### Lines 175–179

````python
175          if tools:
176              api_params["tools"] = tools
177              if tool_choice:
178                  api_params["tool_choice"] = tool_choice
179
````

- **L175–178** If the caller passed tool definitions (OpenAI "function calling" schemas), they are added to the request. `tool_choice` is added only if given; it can force a particular tool or `"auto"`.


#### Lines 180–192

````python
180          try:
181              if response_format:
182                  api_params["response_format"] = response_format
183                  completion = self.client.beta.chat.completions.parse(**api_params)
184                  result = completion.choices[0].message.parsed
185              elif tools:
186                  completion = self.client.chat.completions.create(**api_params)
187                  result = completion
188              else:
189                  completion = self.client.chat.completions.create(**api_params)
190                  result = completion.choices[0].message.content
191                  result = result.strip() if result else ""
192
````

- **L180** `try:` starts the call. Errors are logged at L208.
- **L181–184** The **structured output** path. If `response_format` (a Pydantic class) was given, it is added to the params and `self.client.beta.chat.completions.parse(**api_params)` is called. `**api_params` *unpacks* the dict into keyword arguments, i.e. `parse(model=..., messages=..., response_format=...)`. The SDK converts the Pydantic model to a JSON schema, the model is forced to answer in that shape, and `.choices[0].message.parsed` is already a validated Pydantic instance. This is what `chat_structured` and the drafting and assessment tools rely on.
- **L185–187** The **tool-calling** path returns the whole `completion` object, not just text, because the caller needs `message.tool_calls`. So the return type of `chat` depends on which arguments were passed.
- **L188–191** The **plain text** path takes `message.content` and strips whitespace. It returns `""` if the content is `None`.


#### Lines 193–206

````python
193              usage = completion.usage
194              if usage:
195                  logger.debug(
196                      f"LLM usage: {usage.prompt_tokens} prompt + {usage.completion_tokens} completion = {usage.total_tokens} total to⟦CUT⟧
197                  )
198
199              # Log LLM call
200              log_llm_call(
201                  model=self.model_name,
202                  messages=messages,
203                  response=str(result)[:200] if result else None
204              )
205
206              return result
````

- **L193–197** If the response includes token usage, it logs prompt, completion and total token counts at debug level. **L196 ran off the screen (⟦CUT⟧)**. The visible text ends "total to", almost certainly "tokens".
- **L199–204** `log_llm_call` records the model, the input messages and the first 200 characters of the result (`str(result)[:200]`) in the LLM audit log.
- **L206** Returns the result: a Pydantic object, a completion object, or a string, depending on the path taken.


#### Lines 207–212

````python
207
208          except Exception as e:
209              logger.error(f"LLM chat failed: {e}")
210              log_llm_call(model=self.model_name, messages=messages, error=str(e))
211              raise
212
````

- **L208–211** On any exception (timeout, HTTP error, schema-parse failure), it logs the error, records it in the LLM audit log, and re-raises with a bare `raise`. Re-raising lets the tenacity decorator above `chat` (inferred) see the exception and retry, and once retries are used up, the caller sees the failure.


#### Lines 213–224

````python
213      def chat_structured(self, messages: List[Dict[str, str]], response_format: Type[T]) -> T:
214          """Chat with structured output using Pydantic model.
215
216          Args:
217              messages: List of message dicts with 'role' and 'content'
218              response_format: Pydantic model class for structured output
219
220          Returns:
221              Instance of the Pydantic model with parsed response
222          """
223          return self.chat(messages=messages, response_format=response_format)
224
````

- **L213** `chat_structured(messages, response_format: Type[T]) -> T`. Because of the `TypeVar`, passing the *class* `TemplateContent` is declared to return a `TemplateContent` *instance*. This gives editors and type checkers correct autocompletion on the result.
- **L214–222** The docstring: the messages are role/content dicts, and the response is parsed into the given Pydantic model.
- **L223** A thin wrapper that calls `self.chat(...)` with `response_format`, so it always takes the structured `parse` path (L181–184). It inherits the retry and logging behaviour.


#### Lines 225–239

````python
225      def get_embeddings(self, texts: List[str]) -> List[List[float]]:
226          """Generate embeddings for list of texts."""
227          self._ensure_valid_token()
228
229          logger.debug(f"Generating embeddings for {len(texts)} texts using {self.embedding_model}")
230
231          response = self.client.embeddings.create(
232              model=self.embedding_model,
233              input=texts
234          )
235
236          embeddings = [item.embedding for item in response.data]
237          logger.debug(f"Generated {len(embeddings)} embeddings (dim={len(embeddings[0])})")
238          return embeddings
239
````

- **L225** `get_embeddings(texts)` returns one vector (a list of floats) per input text. Embeddings are numeric representations of text, used for similarity search. No caller in the transcribed backend uses this method.
- **L227** Makes sure the token is fresh first.
- **L229** Debug log of the number of texts and the embedding model name. `self.embedding_model` is set in the hidden constructor.
- **L231–234** Calls the gateway's embeddings endpoint with the model and the list of texts.
- **L236** A list comprehension that extracts the `.embedding` vector from each item in `response.data`.
- **L237** Logs the count and the vector dimension (`len(embeddings[0])`).
- **L238** Returns the vectors.
- Note: unlike `chat`, this method has no `try`/`log_llm_call` and no visible retry. An empty `texts` list would also make L237 raise `IndexError`. This is minor because nothing in the transcribed backend calls it.


#### Lines 240–243

````python
240
241  __all__ = ["LLMService"]
242
243
````

- **L241** `__all__ = ["LLMService"]` declares the class as the module's only public export.
- **L242–243** Trailing blank lines.


---

## 35. `backend/utils/postgres_client.py`

*341 lines · complete*

This is the app's **single database access layer**. It wraps a `psycopg` (v3) **connection pool** in a `PostgresClient` class and exposes a process-wide singleton through `get_client()`. Almost everything that touches Postgres goes through it: the routers (`operations` health check), the services (intake, assessment, drafting, review), the agent tools (`data_tools.db_query`/`db_write`, `audit_tools`, `drafting_tools`) and the evaluation scripts.

It provides:
- low-level helpers: `get_connection`, `get_cursor` (auto commit/rollback), `execute`, `fetch_one` and `fetch_all`
- generic `db_query`/`db_write` methods that build SQL from a table name and dicts
- **versioned upsert** logic (`REVISIONED_TABLES` + `_upsert_keyed`), which keeps one row per agent prompt version while keeping every human-provided row
- an `audit_log` writer, a `LISTEN` helper, a health check and `close()`


#### Lines 1–15

````python
 1  """Unified PostgreSQL client for Anchor AI.
 2
 3  Provides a singleton connection pool using psycopg3 with automatic
 4  commit/rollback and event notification support.
 5
 6  Usage:
 7      from backend.utils.postgres_client import get_client
 8
 9      client = get_client()
10      with client.get_connection() as conn:
11          with conn.cursor() as cur:
12              cur.execute("SELECT * FROM incidents LIMIT 10")
13              rows = cur.fetchall()
14  """
15
````

- **L1–14** The module docstring: a unified PostgreSQL client with a singleton connection pool (psycopg3), automatic commit/rollback, and event-notification (LISTEN/NOTIFY) support. **L6–13** show typical usage: get the client, borrow a connection with `with client.get_connection() as conn:`, open a cursor, run a query, and fetch the rows.


#### Lines 16–36

````python
16  import threading
17  from contextlib import contextmanager
18  from typing import Optional, Dict, List, Any
19
20  from psycopg_pool import ConnectionPool
21  from psycopg.rows import dict_row
22
23  from backend.config import (
24      POSTGRES_HOST,
25      POSTGRES_PORT,
26      POSTGRES_DB,
27      POSTGRES_SCHEMA,
28      POSTGRES_USER,
29      POSTGRES_PASSWORD,
30      POSTGRES_POOL_SIZE,
31      POSTGRES_MAX_OVERFLOW,
32  )
33  from backend.utils._logger import get_logger
34
35  logger = get_logger(__name__)
36
````

- **L16** `threading` provides the lock that protects singleton creation (L330).
- **L17** `contextmanager` is a decorator that turns a **generator function** (one containing `yield`) into something usable with `with`. Code before the `yield` runs on entering the block, the yielded value is what `as x` receives, and code after it runs on leaving the block.
- **L18** Type hints.
- **L20** `ConnectionPool` from `psycopg_pool` keeps a set of open DB connections that are reused, rather than opening a new TCP and authentication handshake for every query.
- **L21** `dict_row` is a psycopg **row factory** that makes cursors return each row as a `dict` (`{"incident_number": ..., ...}`) instead of a tuple. That's why the rest of the code can do `row["id"]` or `row.get("title")`.
- **L23–32** Connection settings from `backend/config.py`. They come from env vars, with defaults `localhost:5432`, DB `anchor_ai`, schema `public`, user `postgres`, pool size 10 and max overflow 5.
- **L33–35** Gets a module logger through the side-effect-free `backend.utils._logger` helper.


#### Lines 37–62

````python
37  # Tables keyed on a set of columns that include prompt_version (see db_write).
38  # A write whose key matches an existing row UPSERTS it in place; a write with
39  # a new key value (e.g. a different prompt_version) inserts a new row. Since
40  # NULL never equals NULL in SQL, human-provided rows (prompt_version IS NULL)
41  # are never matched/upserted - each one always inserts a new row, preserving
42  # full history for human input while agent runs stay deduplicated per
43  # prompt_version.
44  #
45  # template_details additionally keys on drafted_by so that an agent-authored
46  # draft at a given stage/prompt_version never collides with a human-authored
47  # template at that same stage (e.g. a human "Reg Relations" upload sitting
48  # alongside the agent's own "Reg Relations" attempt) - each drafted_by gets
49  # its own row, deduplicated only against its own prior writes. It also keys on
50  # assessment_prompt_version so a draft generated from the human assessment and
51  # one generated from an agent assessment (e.g. 'v2') stay as distinct rows.
52  #
53  # reportability_assessments additionally keys on revision so that an agent
54  # re-assessment triggered by new 1LOD/2LOD feedback (see
55  # get_current_assessment_prompt_version / assessment_agent_prompt_v4) inserts
56  # a new row (revision = prior max + 1) instead of overwriting the original
57  # assessment - preserving the full feedback/re-assessment history per incident.
58  REVISIONED_TABLES = {
59      "reportability_assessments": ["incident_number", "prompt_version", "revision"],
60      "template_details": ["incident_number", "template_stage", "drafted_by", "prompt_version", "assessment_prompt_version"],
61  }
62
````

- **L37–57** A long comment that explains the **versioning model**, which is central to how the app stores agent output against human output:
  - **L37–43** For "revisioned" tables, a write whose key columns match an existing row **updates** it. A write with a new key (e.g. a new `prompt_version`) **inserts** a new row. Human-provided rows have `prompt_version = NULL`, and in SQL `NULL = NULL` is never true, so they are never matched and every human submission becomes a new row. The result is a full history of human input, with one deduplicated row per prompt version for agent runs.
  - **L45–51** `template_details` also keys on `drafted_by`, so an agent draft and a human draft at the same stage never overwrite each other. It also keys on `assessment_prompt_version`, so drafts built from the human assessment and from an agent assessment (e.g. `v2`) stay separate.
  - **L53–57** `reportability_assessments` also keys on `revision`, so a re-assessment triggered by new 1LOD/2LOD feedback inserts a new row (previous max + 1) instead of overwriting the original. This keeps the full feedback history.
- **L58–61** `REVISIONED_TABLES` is a dict that maps each table name to its list of key columns. `db_query` and `db_write` check `table in REVISIONED_TABLES`.


#### Lines 63–94

````python
63
64  class PostgresClient:
65      """PostgreSQL client with connection pooling for Anchor AI."""
66
67      def __init__(self):
68          self.schema = POSTGRES_SCHEMA
69          conninfo = (
70              f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
71              f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} "
72              f"sslmode=prefer"
73          )
74          def _configure_conn(conn):
75              conn.execute(f"SET search_path TO {POSTGRES_SCHEMA}, public")
76              conn.commit()
77
78          self._pool = ConnectionPool(
79              conninfo,
80              min_size=POSTGRES_POOL_SIZE,
81              max_size=POSTGRES_POOL_SIZE + POSTGRES_MAX_OVERFLOW,
82              kwargs={"row_factory": dict_row},
83              configure=_configure_conn,
84          )
85          logger.info(
86              "PostgreSQL pool: %s:%s/%s.%s (size: %s, max: %s)",
87              POSTGRES_HOST,
88              POSTGRES_PORT,
89              POSTGRES_DB,
90              POSTGRES_SCHEMA,
91              POSTGRES_POOL_SIZE,
92              POSTGRES_POOL_SIZE + POSTGRES_MAX_OVERFLOW,
93          )
94
````

- **L64–65** The `PostgresClient` class.
- **L67** `__init__` runs once, when `get_client()` first creates the singleton.
- **L68** Stores the schema name.
- **L69–73** Builds a libpq **conninfo** string (`host=... port=... dbname=... user=... password=... sslmode=prefer`). Adjacent f-strings inside parentheses are concatenated. `sslmode=prefer` means "use TLS if the server supports it, otherwise plain".
- **L74–76** `_configure_conn` is a nested function (defined inside `__init__`) that the pool runs on **every new connection**. It sets `search_path` to the configured schema, then `public`, so queries can say `incidents` instead of `myschema.incidents`. It calls `commit()` because psycopg opens a transaction on the first statement, and the pool expects a connection to be idle (not inside a transaction) after configuration.
- **L78–84** Creates the pool:
  - `min_size` is the number of connections kept open (`POSTGRES_POOL_SIZE`, default 10).
  - `max_size` is the size plus overflow (default 15), the cap under load.
  - `kwargs={"row_factory": dict_row}` is passed to every `psycopg.connect`, so all cursors return dicts.
  - `configure=_configure_conn` is the hook described above.
- **L85–93** Logs the target (`host:port/db.schema`) and the pool sizes. It never logs the password.
- Note: the conninfo values are not quoted, so a password containing spaces or quotes would break the connection string. Building it with `psycopg.conninfo.make_conninfo(...)` would handle that.


#### Lines 95–113

````python
 95      @contextmanager
 96      def get_connection(self):
 97          """Get a connection from the pool with automatic return."""
 98          with self._pool.connection() as conn:
 99              yield conn
100
101      @contextmanager
102      def get_cursor(self):
103          """Get a cursor with automatic commit/rollback."""
104          with self._pool.connection() as conn:
105              with conn.cursor() as cur:
106                  try:
107                      yield cur
108                      conn.commit()
109                  except Exception as e:
110                      conn.rollback()
111                      logger.error("Transaction rollback: %s", str(e))
112                      raise
113
````

- **L95–99** `get_connection()` is a `@contextmanager`. `self._pool.connection()` borrows a connection, which is yielded to the caller's `with` block and returned to the pool afterwards. psycopg_pool's own context manager also **commits** on a clean exit and **rolls back** on an exception.
- **L101–112** `get_cursor()` is the workhorse behind `execute`, `fetch_one` and `fetch_all`:
  - **L104–105** Borrows a connection and opens a cursor, both in nested `with` blocks so they are cleaned up.
  - **L107** `yield cur` hands the cursor to the caller's `with` block.
  - **L108** If the caller's block finishes normally, the transaction is **committed**. That includes leaving it with `return`.
  - **L109–112** If the block raises, the transaction is **rolled back** (so no partial write), the error is logged, and it is re-raised. With a `@contextmanager` generator, an exception inside the caller's `with` block is re-thrown at the `yield` line, which is why the `try/except` wraps the `yield`.


#### Lines 114–130

````python
114      def execute(self, query: str, params=None):
115          """Execute a single statement (INSERT/UPDATE/DELETE)."""
116          with self.get_cursor() as cur:
117              cur.execute(query, params)
118
119      def fetch_one(self, query: str, params=None) -> Optional[Dict[str, Any]]:
120          """Execute query and return single row as dict."""
121          with self.get_cursor() as cur:
122              cur.execute(query, params)
123              return cur.fetchone()
124
125      def fetch_all(self, query: str, params=None) -> List[Dict[str, Any]]:
126          """Execute query and return all rows as list of dicts."""
127          with self.get_cursor() as cur:
128              cur.execute(query, params)
129              return cur.fetchall()
130
````

- **L114–117** `execute(query, params)` runs one statement (INSERT, UPDATE or DELETE) and commits it through `get_cursor`. `params` is a tuple or list of values that psycopg puts in place of the `%s` placeholders. psycopg sends the values separately from the SQL text (it never builds SQL from them by string concatenation), which is what protects against SQL injection *for values*.
- **L119–123** `fetch_one(query, params)` executes the query and returns the first row as a dict, or `None`. The `return` happens inside the `with` block, which still exits cleanly, so the commit on L108 still runs. That matters for `UPDATE ... RETURNING id` and `INSERT ... RETURNING id`, which use this method.
- **L125–129** `fetch_all(query, params)` executes the query and returns all rows as a list of dicts.


#### Lines 131–150

````python
131      def db_query(self, table: str, filters: Optional[Dict[str, Any]] = None, columns: Optional[List[str]] = None) -> List[Dict[str,⟦CUT⟧
132          """Generic db_query tool - query with filters and column selection.
133
134          Args:
135              table: Table name
136              filters: Dict of column=value filters (AND combined)
137              columns: List of columns to select (default: all)
138
139          Returns:
140              List of dicts
141
142          Example:
143              client.db_query("incidents", {"incident_number": "INC0575428"}, ["priority", "description"])
144          """
145          # Build SELECT clause
146          if columns:
147              select_clause = ", ".join(columns)
148          else:
149              select_clause = "*"
150
````

- **L131** `db_query(table, filters=None, columns=None)` is a generic SELECT. **The end of this line ran off the screen (⟦CUT⟧)**. It almost certainly finishes `-> List[Dict[str, Any]]:` (inference). The LLM-facing `db_query` tool in `data_tools.py` passes its arguments straight to this method.
- **L132–144** The docstring: the filters are column=value pairs combined with AND, and the columns default to all. It includes an example.
- **L146–149** Builds the SELECT list: the requested columns joined with commas, or `*`.


#### Lines 151–176

````python
151          # Build WHERE clause
152          where_parts = []
153          params = []
154          if filters:
155              for col, val in filters.items():
156                  if val is None:
157                      # SQL '= NULL' is always unknown/false, even for a row
158                      # that genuinely has NULL in that column (e.g. the
159                      # human-provided reportability_assessments/template_details
160                      # rows where prompt_version IS NULL) - must use IS NULL
161                      # instead, with no bound parameter.
162                      where_parts.append(f"{col} IS NULL")
163                  else:
164                      where_parts.append(f"{col} = %s")
165                      params.append(val)
166
167          where_clause = " AND ".join(where_parts) if where_parts else "1=1"
168
169          query = f"SELECT {select_clause} FROM {table} WHERE {where_clause}"
170          if table in REVISIONED_TABLES:
171              # Most-recently-written row first, so callers taking results[0]
172              # get the current one (there can be several rows per incident -
173              # one per distinct prompt_version, plus human-provided rows).
174              query += " ORDER BY updated_at DESC"
175          return self.fetch_all(query, params if params else None)
176
````

- **L152–153** Accumulators for the WHERE fragments and the bound parameter values.
- **L154–165** For each filter:
  - **L156–162** If the value is `None`, it emits `col IS NULL` with no parameter. The comment explains why: `col = NULL` is never true in SQL. This is how callers find the human-provided rows (`prompt_version IS NULL`).
  - **L163–165** Otherwise, it emits `col = %s` and records the value as a bound parameter.
- **L167** Joins the fragments with ` AND `. With no filters it uses `1=1`, a no-op condition that's always true, so the `WHERE` keyword can always be present.
- **L169** Assembles the SQL with an f-string.
- **L170–174** For revisioned tables, it appends `ORDER BY updated_at DESC`, so callers that take `results[0]` get the most recent row. There can be several rows per incident.
- **L175** Runs the query. It passes `None` instead of an empty list when there are no params.
- Note: table and column **names** are put into the SQL with f-strings. Only **values** are parameterised, because SQL placeholders can't stand in for identifiers. Since the agent-facing `db_query`/`db_write` tools take `table`, `filters` keys and `columns` from LLM output, a malformed or prompt-injected identifier would go straight into the SQL. `backend/utils/sql_utils.py` already has `sanitize_sql_identifier`, but this file never uses it. An allow-list of table names, or `psycopg.sql.Identifier`, would close the gap.


#### Lines 177–195

````python
177      def db_write(self, table: str, payload: Dict[str, Any]) -> str:
178          """Generic db_write tool - insert, update, or upsert based on the table.
179
180          Args:
181              table: Table name
182              payload: Dict of column=value pairs
183
184          Returns:
185              ID of inserted/updated record
186
187          Example:
188              client.db_write("incidents", {"incident_number": "INC0575428", "current_status": "Drafting"})
189
190          Note:
191              For keyed tables (reportability_assessments, template_details), the
192              columns in REVISIONED_TABLES[table] (which include prompt_version)
193              form the write's key - see _upsert_keyed for the upsert logic.
194          """
195          # Determine if INSERT or UPDATE based on table's primary key
````

- **L177** `db_write(table, payload) -> str` is a generic write that decides between INSERT, UPDATE and upsert and returns the row `id` as a string.
- **L178–194** The docstring, with an example. The note at **L190–193** points out that for keyed tables, the `REVISIONED_TABLES` columns form the upsert key, and the details are in `_upsert_keyed`.


#### Lines 196–216

````python
196          # For incidents, use incident_number; for others, use id
197          if table == "incidents" and "incident_number" in payload:
198              # Check if exists
199              existing = self.fetch_one(
200                  f"SELECT id FROM {table} WHERE incident_number = %s",
201                  (payload["incident_number"],)
202              )
203              if existing:
204                  # UPDATE
205                  set_parts = []
206                  params = []
207                  for col, val in payload.items():
208                      if col != "incident_number":  # Don't update primary key
209                          set_parts.append(f"{col} = %s")
210                          params.append(val)
211                  params.append(payload["incident_number"])
212
213                  query = f"UPDATE {table} SET {', '.join(set_parts)} WHERE incident_number = %s RETURNING id"
214                  result = self.fetch_one(query, params)
215                  return str(result["id"])
216
````

- **L196–197** (The comments on L195–196 describe the intent.) Special case: the `incidents` table, which is keyed by its business key `incident_number` (e.g. `INC0575428`).
- **L199–202** Checks whether the incident already exists. Note the one-element tuple `(value,)`: the trailing comma is what makes it a tuple.
- **L203–215** If it exists, it **updates** it:
  - **L205–210** Builds `col = %s` for every payload key except `incident_number`. The comment says the key isn't updated.
  - **L211** Adds the incident number as the last parameter, for the WHERE clause.
  - **L213** `UPDATE incidents SET ... WHERE incident_number = %s RETURNING id`. `RETURNING` makes Postgres send back the updated row's `id` in the same round trip.
  - **L214–215** Fetches it and returns `id` as a string. `id` is most likely a UUID.
- If the incident does not exist, execution falls through to the generic INSERT below.


#### Lines 217–228

````python
217          if table in REVISIONED_TABLES:
218              return self._upsert_keyed(table, payload)
219
220          # INSERT
221          columns = list(payload.keys())
222          placeholders = ["%s"] * len(columns)
223          values = list(payload.values())
224
225          query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING id"
226          result = self.fetch_one(query, values)
227          return str(result["id"])
228
````

- **L217–218** For `reportability_assessments` and `template_details`, it delegates to the versioned upsert.
- **L220–227** For every other table (or a new incident), it does a plain INSERT:
  - **L221–223** Gets the column names, one `%s` placeholder per column, and the values in the same order.
  - **L225** `INSERT INTO t (a, b, ...) VALUES (%s, %s, ...) RETURNING id`.
  - **L226–227** Executes, commits, and returns the new id as a string.


#### Lines 229–243

````python
229      def _upsert_keyed(self, table: str, payload: Dict[str, Any]) -> str:
230          """Insert or update a row in a REVISIONED_TABLES table, keyed on
231          REVISIONED_TABLES[table] (e.g. incident_number + prompt_version).
232
233          - If a row already matches the key columns, UPDATE it in place
234            (including bumping updated_at).
235          - Otherwise INSERT a new row.
236          - A write whose prompt_version is NULL (human-provided data - no
237            agent/prompt involved) never matches an existing row, since NULL
238            never equals NULL in SQL - it always inserts, preserving full
239            history for human input.
240          """
241          key_cols = REVISIONED_TABLES[table]
242          key_values = {col: payload.get(col) for col in key_cols}
243
````

- **L229** `_upsert_keyed(table, payload)` is the private implementation of the versioning model.
- **L230–240** The docstring: if the key matches, update in place and bump `updated_at`; otherwise insert. A NULL `prompt_version` (human data) never matches, so it always inserts.
- **L241** Looks up this table's key columns.
- **L242** A **dict comprehension**, `{k: v for ...}`, collects the payload's value for each key column. A key column missing from the payload becomes `None`.


#### Lines 244–259

````python
244          existing = None
245          if key_values.get("prompt_version") is not None:
246              where_parts = []
247              params = []
248              for col in key_cols:
249                  val = key_values[col]
250                  if val is None:
251                      where_parts.append(f"{col} IS NULL")
252                  else:
253                      where_parts.append(f"{col} = %s")
254                      params.append(val)
255              existing = self.fetch_one(
256                  f"SELECT id FROM {table} WHERE {' AND '.join(where_parts)}",
257                  params
258              )
259
````

- **L244** `existing` defaults to `None`, meaning "no match".
- **L245** It only searches for an existing row when `prompt_version` is not None, i.e. for agent-written data. Human rows skip straight to INSERT, which preserves their history.
- **L246–254** Builds the WHERE clause over all key columns. Other key columns that are `None` (e.g. a missing `assessment_prompt_version` or `revision`) use `IS NULL`, so they match rows where that column is also NULL. Non-null values use `= %s`.
- **L255–258** `SELECT id ... WHERE <key match>` returns the matching row or `None`.


#### Lines 260–278

````python
260          if existing:
261              set_parts = ["updated_at = NOW()"]
262              params = []
263              for col, val in payload.items():
264                  if col not in key_cols:
265                      set_parts.append(f"{col} = %s")
266                      params.append(val)
267              params.append(existing["id"])
268              query = f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = %s RETURNING id"
269              result = self.fetch_one(query, params)
270              return str(result["id"])
271
272          columns = list(payload.keys())
273          placeholders = ["%s"] * len(columns)
274          values = list(payload.values())
275          query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING id"
276          result = self.fetch_one(query, values)
277          return str(result["id"])
278
````

- **L260–270** If a row matched, it **updates in place**:
  - **L261** Always sets `updated_at = NOW()`, the DB server's current time. This is what keeps `ORDER BY updated_at DESC` in `db_query` correct.
  - **L263–266** Sets every *non-key* payload column. Key columns are left alone because they're what identified the row.
  - **L267–270** `WHERE id = %s RETURNING id` returns the id as a string.
- **L272–277** Otherwise it **inserts** a new row, using the same pattern as the generic INSERT.
- (Side note: the select-then-update/insert is two separate transactions, so two concurrent writers with the same key could in theory both insert. A database `UNIQUE` constraint plus `INSERT ... ON CONFLICT` would make it atomic. With NULLable key columns that needs Postgres 15's `NULLS NOT DISTINCT`, which may be why it's done in Python.)


#### Lines 279–297

````python
279      def audit_log(self, incident_number: str, action: str, actor: str, details: Dict[str, Any], actor_id: Optional[str] = None):
280          """Log action to audit_logs table.
281
282          Args:
283              incident_number: Incident number
284              action: Action type (e.g., "incident_ingested", "status_transitioned")
285              actor: Actor name (e.g., "Workflow Agent", "Assessment Agent", user name)
286              details: JSONB details
287              actor_id: Optional user ID if actor is human
288          """
289          import json
290          self.execute(
291              """
292              INSERT INTO audit_logs (incident_number, action, actor, actor_id, details)
293              VALUES (%s, %s, %s, %s, %s)
294              """,
295              (incident_number, action, actor, actor_id, json.dumps(details))
296          )
297
````

- **L279** `audit_log(incident_number, action, actor, details, actor_id=None)` appends an entry to the `audit_logs` table, the compliance trail of who did what to which incident.
- **L280–288** The docstring lists the arguments: action names such as `incident_ingested` or `status_transitioned`; actors such as "Workflow Agent", "Assessment Agent" or a user name; a free-form `details` dict stored as JSONB; and an optional user id for humans.
- **L289** `import json` inside the method is unusual (a top-of-file import is more common) but works.
- **L290–296** A parameterised INSERT through `execute` (auto-commit). `json.dumps(details)` serialises the dict to a JSON string, which Postgres stores in the JSONB column. The triple-quoted string `"""..."""` lets the SQL span several lines.


#### Lines 298–312

````python
298      @contextmanager
299      def listen(self, channel: str = "workflow_events"):
300          """Listen for PostgreSQL NOTIFY events.
301
302          Usage:
303              with client.listen() as conn:
304                  for notify in conn.notifies():
305                      print(f"Event: {notify.payload}")
306          """
307          with self._pool.connection() as conn:
308              with conn.cursor() as cur:
309                  cur.execute(f"LISTEN {channel}")
310              conn.commit()
311              yield conn
312
````

- **L298–299** `listen(channel="workflow_events")` is a `@contextmanager` for Postgres **LISTEN/NOTIFY**, a built-in publish/subscribe mechanism. Any session can run `NOTIFY workflow_events, 'payload'`, and every connection that has run `LISTEN workflow_events` receives the message.
- **L300–306** The docstring shows usage: loop over `conn.notifies()` and read `notify.payload`.
- **L307–311** Borrows a pooled connection, runs `LISTEN <channel>` (the channel name is put in with an f-string), and commits, because notifications are delivered only once the LISTEN is committed. It then yields the connection to the caller.
- No code in the transcribed backend calls this method. `backend/services/workflow_listener.py` deliberately opens its **own dedicated autocommit connection** instead, which avoids holding a pool slot indefinitely.


#### Lines 313–327

````python
313      def health_check(self) -> bool:
314          """Verify database connectivity."""
315          try:
316              row = self.fetch_one("SELECT 1 AS ok")
317              return row is not None and row.get("ok") == 1
318          except Exception as e:
319              logger.error("Database health check failed: %s", e)
320              return False
321
322      def close(self):
323          """Close all connections in pool."""
324          if self._pool:
325              self._pool.close()
326              logger.info("PostgreSQL pool closed")
327
````

- **L313–320** `health_check()` runs `SELECT 1 AS ok` and returns `True` only if a row comes back with `ok == 1`. Any exception (DB down, wrong password) is logged and gives `False` rather than an error. The FastAPI app (`main.py`) and the `operations` router use it for health endpoints and startup checks.
- **L322–326** `close()` shuts down all pooled connections. It's called on shutdown (e.g. from the FastAPI lifespan or at the end of `evaluate_drafting_prompts.py`). `if self._pool:` guards against a missing pool.


#### Lines 328–341

````python
328
329  _instance: Optional[PostgresClient] = None
330  _lock = threading.Lock()
331
332
333  def get_client() -> PostgresClient:
334      """Get PostgreSQL client singleton."""
335      global _instance
336      if _instance is None:
337          with _lock:
338              if _instance is None:
339                  _instance = PostgresClient()
340      return _instance
341
````

- **L329** `_instance` is the module-level singleton slot, initially `None`.
- **L330** `_lock` is a `threading.Lock` that makes creating the singleton thread-safe.
- **L333–340** `get_client()` uses **double-checked locking**. The first `if` (L336) avoids taking the lock on every call once the client exists. The second `if` (L338), inside the lock, handles two threads that both saw `None` at the same moment: only the first one creates the client. `global _instance` lets the function assign the module variable. As a result, the whole process shares one `PostgresClient` and therefore one connection pool.


---

## 36. `backend/utils/sql_utils.py`

*152 lines · complete*

This file holds small helpers for building SQL safely: loading a `.sql` file with `{placeholder}` substitution, validating identifiers (table and column names), and building WHERE, UPDATE SET and INSERT fragments with `%s` placeholders for psycopg. **None of these functions is imported anywhere else in the transcribed backend.** `PostgresClient` builds the same fragments inline in its own methods, without the identifier check. So this file appears to be unused or legacy code. Its `sanitize_sql_identifier` would be a natural fit for hardening `PostgresClient.db_query`/`db_write`.


#### Lines 1–6

````python
1  """SQL query utilities for Anchor AI."""
2
3  from pathlib import Path
4  from typing import Optional, Dict, Any
5
6
````

- **L1** The module docstring.
- **L3** `Path` is used for the SQL file path argument.
- **L4** Type hints. `Optional[Dict[str, str]]` means "a dict of strings, or None".


#### Lines 7–27

````python
 7  def load_sql_template(sql_file: Path, params: Optional[Dict[str, str]] = None) -> str:
 8      """Load SQL from file and optionally substitute parameters.
 9
10      Args:
11          sql_file: Path to SQL file (must be within project directory)
12          params: Optional dict of parameters to substitute using .format()
13
14      Returns:
15          SQL string with parameters substituted
16
17      Raises:
18          ValueError: If sql_file path is invalid or contains path traversal
19      """
20      # Validate that the path doesn't contain path traversal attempts
21      sql_file_resolved = sql_file.resolve()
22      if ".." in str(sql_file) or not sql_file_resolved.exists():
23          raise ValueError(f"Invalid SQL file path: {sql_file}")
24
25      with open(sql_file_resolved, "r") as f:
26          sql = f.read()
27
````

- **L7** `load_sql_template(sql_file, params=None) -> str` reads SQL text from a file and optionally fills in placeholders.
- **L8–19** The docstring says the file must be inside the project directory, that parameters are substituted with `.format()`, and that a `ValueError` is raised for invalid paths or path traversal.
- **L21** `.resolve()` turns the path into an absolute, normalised path and follows symlinks.
- **L22–23** Rejects the path if the original text contains `..` (a crude path-traversal check) or if the resolved file doesn't exist.
- **L25–26** Opens the file with a context manager and reads it all into `sql`.
- Note: the "must be within project directory" rule in the docstring isn't actually enforced. Any absolute path without `..` (e.g. `/etc/...`) passes. A real check would compare `sql_file_resolved` against the project root, e.g. with `is_relative_to`.


#### Lines 28–39

````python
28      if params:
29          # Sanitize parameters to prevent SQL injection
30          sanitized_params = {}
31          for key, value in params.items():
32              if isinstance(value, str):
33                  # Escape single quotes for SQL strings
34                  value = value.replace("'", "''")
35              sanitized_params[key] = value
36          sql = sql.format(**sanitized_params)
37
38      return sql
39
````

- **L28** Substitution happens only if `params` was given.
- **L29–35** "Sanitises" each value. Strings have every `'` doubled (`'` becomes `''`), which is SQL's escape for a quote inside a string literal. Other types are copied unchanged.
- **L36** `sql.format(**sanitized_params)` replaces `{name}` placeholders in the SQL with the values. `**` unpacks the dict into keyword arguments.
- **L38** Returns the finished SQL.
- Note: quote-escaping plus string formatting is much weaker than real bound parameters (`%s` with a params tuple), which the rest of the codebase uses. It also means any literal `{` or `}` in the SQL file, such as a JSON literal, would make `.format` raise. This is probably why the function isn't used.


#### Lines 40–57

````python
40
41  def sanitize_sql_identifier(identifier: str) -> str:
42      """Sanitize a SQL identifier to prevent injection.
43
44      Args:
45          identifier: Table name, column name, etc.
46
47      Returns:
48          Sanitized identifier
49
50      Raises:
51          ValueError: If identifier contains invalid characters
52      """
53      # Only allow alphanumeric, underscore, and dot
54      if not all(c.isalnum() or c in ('_', '.') for c in identifier):
55          raise ValueError(f"Invalid SQL identifier: {identifier}")
56      return identifier
57
````

- **L41** `sanitize_sql_identifier(identifier) -> str` validates a table or column name before it is put into SQL text. Placeholders can't be used for identifiers, so the only safe approach is to reject suspicious names.
- **L42–52** The docstring: it returns the identifier unchanged or raises `ValueError`.
- **L54** `all(c.isalnum() or c in ('_', '.') for c in identifier)` uses a generator expression to check every character: letters and digits, underscore, or a dot (the dot allows `schema.table`). Anything else, such as spaces, quotes, `;` or `--`, fails, which blocks injection like `incidents; DROP TABLE ...`.
- **L55** Raises `ValueError` on failure.
- **L56** Returns the identifier unchanged when it's valid.
- (Edge cases: `all()` of an empty sequence is `True`, so an empty string passes. `isalnum()` also accepts non-ASCII letters. Neither matters for normal column names.)


#### Lines 58–86

````python
58
59  def build_where_clause(filters: Dict[str, Any]) -> tuple[str, list]:
60      """Build SQL WHERE clause from filters dict.
61
62      Args:
63          filters: Dict of column=value filters (AND combined)
64
65      Returns:
66          Tuple of (where_clause, params_list)
67
68      Example:
69          where, params = build_where_clause({"priority": "P1", "legal_entity": "CNB"})
70          # Returns: ("priority = %s AND legal_entity = %s", ["P1", "CNB"])
71      """
72      if not filters:
73          return "1=1", []
74
75      where_parts = []
76      params = []
77
78      for col, val in filters.items():
79          # Sanitize column name
80          col = sanitize_sql_identifier(col)
81          where_parts.append(f"{col} = %s")
82          params.append(val)
83
84      where_clause = " AND ".join(where_parts)
85      return where_clause, params
86
````

- **L59** `build_where_clause(filters) -> tuple[str, list]` returns a WHERE fragment and its parameter list. `tuple[str, list]` is the built-in generic syntax (Python 3.9+).
- **L60–71** The docstring, with an example: `{"priority": "P1", "legal_entity": "CNB"}` gives `("priority = %s AND legal_entity = %s", ["P1", "CNB"])`.
- **L72–73** No filters gives `"1=1"` (always true) and no params.
- **L75–82** For each filter it validates the column name, appends `col = %s`, and records the value as a bound parameter.
- **L84–85** Joins the fragments with ` AND ` and returns `(clause, params)`.
- Unlike `PostgresClient.db_query`, a `None` value here becomes `col = %s` with a NULL parameter, which never matches. `db_query` correctly emits `IS NULL` instead.


#### Lines 87–119

````python
 87
 88  def build_update_clause(payload: Dict[str, Any], exclude_keys: Optional[list] = None) -> tuple[str, list]:
 89      """Build SQL UPDATE SET clause from payload dict.
 90
 91      Args:
 92          payload: Dict of column=value pairs
 93          exclude_keys: List of keys to exclude from UPDATE (e.g., primary keys)
 94
 95      Returns:
 96          Tuple of (set_clause, params_list)
 97
 98      Example:
 99          set_clause, params = build_update_clause(
100              {"current_status": "Drafting", "updated_at": "NOW()"},
101              exclude_keys=["incident_number"]
102          )
103          # Returns: ("current_status = %s, updated_at = %s", ["Drafting", "NOW()"])
104      """
105      exclude_keys = exclude_keys or []
106
107      set_parts = []
108      params = []
109
110      for col, val in payload.items():
111          if col not in exclude_keys:
112              # Sanitize column name
113              col = sanitize_sql_identifier(col)
114              set_parts.append(f"{col} = %s")
115              params.append(val)
116
117      set_clause = ", ".join(set_parts)
118      return set_clause, params
119
````

- **L88** `build_update_clause(payload, exclude_keys=None)` builds the `SET a = %s, b = %s` part of an UPDATE.
- **L89–104** The docstring, with an example that excludes the primary key `incident_number`.
- **L105** `exclude_keys or []` replaces `None` with an empty list. Using `None` as the default instead of `[]` avoids Python's shared-mutable-default pitfall.
- **L107–115** For every column not excluded, it validates the name, appends `col = %s`, and records the value.
- **L117–118** Joins with `, ` and returns `(set_clause, params)`.
- Note: the docstring example passes `"updated_at": "NOW()"`. That would be sent as the *string* `'NOW()'` in a bound parameter, not as a call to the SQL `NOW()` function, so the example is misleading. `PostgresClient._upsert_keyed` handles this correctly by writing `updated_at = NOW()` directly into the SQL text.


#### Lines 120–152

````python
120
121  def build_insert_clause(payload: Dict[str, Any]) -> tuple[str, str, list]:
122      """Build SQL INSERT clause from payload dict.
123
124      Args:
125          payload: Dict of column=value pairs
126
127      Returns:
128          Tuple of (columns_clause, placeholders_clause, params_list)
129
130      Example:
131          cols, placeholders, params = build_insert_clause({
132              "incident_number": "INC001",
133              "priority": "P1"
134          })
135          # Returns: ("incident_number, priority", "%s, %s", ["INC001", "P1"])
136      """
137      columns = []
138      placeholders = []
139      params = []
140
141      for col, val in payload.items():
142          # Sanitize column name
143          col = sanitize_sql_identifier(col)
144          columns.append(col)
145          placeholders.append("%s")
146          params.append(val)
147
148      columns_clause = ", ".join(columns)
149      placeholders_clause = ", ".join(placeholders)
150
151      return columns_clause, placeholders_clause, params
152
````

- **L121** `build_insert_clause(payload) -> tuple[str, str, list]` returns the column list, the matching placeholders and the values, ready for `INSERT INTO t ({cols}) VALUES ({placeholders})`.
- **L122–136** The docstring, with an example: `("incident_number, priority", "%s, %s", ["INC001", "P1"])`.
- **L137–146** Loops over the payload in insertion order (Python dicts preserve it, so columns and values stay aligned). It validates each column name and appends the column, a `%s` and the value.
- **L148–151** Joins the columns and placeholders with commas and returns all three pieces.


---

## 37. `backend/evaluation/prompt_iterations/README.md`

*190 lines · complete*

This is a **Markdown documentation file**, not code. It explains the team's workflow for improving the agents' LLM prompts:
- each prompt version lives in its own Python module, `prompts/<agent>_prompt_v<N>.py`;
- `prompt_config.py` picks which version is active;
- evaluation scripts score the active version against human ground truth;
- results are logged here as a version history.

It is the "lab notebook" for prompt engineering on the Assessment Agent. Parts of it are **out of date** compared with the code:
- It says assessment v2 is "currently active", but `prompt_config.py` now sets `assessment_agent_prompt_v10` and `drafting_agent_prompt_v4`.
- It points at `local_dev/test_scripts/evaluate_prompts.py`, whereas the current evaluator in `backend/` is `backend/evaluation/evaluate_assessment_prompts.py`.
- It doesn't cover the drafting evaluation (`evaluate_drafting_prompts.py`) at all.

Read it for the *process* and the reasoning behind prompt changes, not as a current status report.


#### Lines 1–7

````markdown
1  # Prompt Iteration & Evaluation
2
3  This directory contains versioned prompts and evaluation results for Anchor AI's agents
4  (Assessment Agent, and later the Template Drafting Agent).
5
6  **Design Pattern:** Inspired by `qrk0-operation-risk-texanomy-classification` project
7
````

- **L1** The page title, "Prompt Iteration & Evaluation".
- **L3–4** The folder holds versioned prompts and evaluation results for Anchor AI's agents. At the time of writing, only the Assessment Agent was covered, with the "Template Drafting Agent" planned for later. That agent now exists as the Drafting Agent.
- **L6** Credits the design pattern to another internal project, `qrk0-operation-risk-texanomy-classification` (an operational-risk taxonomy classifier; "texanomy" is a typo for "taxonomy" in the original). The team reused its prompt-versioning approach.


#### Lines 8–21

````markdown
 8  ## Naming Convention
 9
10  Prompt modules are named per-agent so multiple agents can be iterated on independently:
11
12  ```
13  <agent_name>_prompt_v<N>.py
14
15  e.g.
16    assessment_agent_prompt_v0.py
17    assessment_agent_prompt_v1.py
18    template_agent_prompt_v0.py     (future)
19    template_agent_prompt_v1.py     (future)
20  ```
21
````

- **L8–10** The naming convention. Each agent's prompts are named separately so different agents can be iterated on independently.
- **L12–20** A fenced code block (triple backticks) showing the pattern `<agent_name>_prompt_v<N>.py` with examples.
  - The `template_agent_prompt_*` names are marked "(future)". In the actual code the drafting prompts ended up named `drafting_agent_prompt_v<N>.py` (e.g. `drafting_agent_prompt_v0.py`), so the example name is stale.
  - The trailing `v<N>` matters in code: `data_tools._short_prompt_version` extracts it with a regex to get the short `v1`/`v2` labels stored in the DB.


#### Lines 22–39

````markdown
22  ## Structure
23
24  ```
25  prompt_iterations/
26  ├── prompts/                            # Versioned prompt modules (imported at runtime)
27  │   ├── __init__.py
28  │   ├── assessment_agent_prompt_v0.py   # original baseline
29  │   ├── assessment_agent_prompt_v1.py   # exact regulatory wording
30  │   ├── assessment_agent_prompt_v2.py   # evidence-based + few-shot examples (current)
31  │   ├── template_agent_prompt_v0.py     (future)
32  │   └── ...
33  ├── evaluations/                        # Evaluation results, one file per run
34  │   ├── eval_assessment_agent_prompt_v0_20260901_135645.txt
35  │   └── ...
36  ├── prompt_config.py                    # Per-agent active prompt version selector
37  └── README.md                           # This file
38  ```
39
````

- **L22–38** A directory tree of `prompt_iterations/`:
  - `prompts/` holds the versioned prompt modules. They are "imported at runtime": `loader.py` uses `importlib.import_module` to load whichever module name `prompt_config.py` specifies. The comments list v0 (baseline), v1 (exact regulatory wording) and v2 (evidence-based + few-shot, labelled "current" at the time).
  - `evaluations/` gets one output file per run, timestamped. The assessment and drafting evaluators both write here.
  - `prompt_config.py` is the per-agent "which version is active" switch.
- The tree doesn't show `loader.py` or the `versions/` directory referenced in `prompt_config.py` (`VERSIONS_DIR`), which were presumably added later.


#### Lines 40–55

````markdown
40  ## Workflow
41
42  ### 1. Create a New Prompt Version
43
44  Copy the current version for the agent you're iterating on:
45
46  ```bash
47  cp prompt_iterations/prompts/assessment_agent_prompt_v0.py \
48     prompt_iterations/prompts/assessment_agent_prompt_v1.py
49  ```
50
51  Edit the new file:
52  - Update the docstring with what changed, why, and (once evaluated) the results
53  - Modify `SYSTEM_PROMPT` with your improvements
54  - Keep `AGENT_NAME`, `DISPLAY_NAME`, `DESCRIPTION` unchanged unless intentionally renaming the agent
55
````

**Workflow step 1: create a new prompt version.**
- **L44–49** Copy the current prompt file to a new `v<N+1>` file. The `cp` command is shown in a bash code block.
- **L51–54** Then edit the copy:
  - Update its docstring with what changed, why, and the results once measured.
  - Change `SYSTEM_PROMPT`, the actual instruction text sent to the LLM.
  - Leave `AGENT_NAME` / `DISPLAY_NAME` / `DESCRIPTION` alone. Those are metadata constants each prompt module defines. `backend/agents/__init__.py` builds its agent registry keyed by `AGENT_NAME`, with `DISPLAY_NAME`/`DESCRIPTION` as labels, so changing them would effectively rename the agent.


#### Lines 56–65

````markdown
56  ### 2. Activate the New Prompt
57
58  Edit `prompt_iterations/prompt_config.py`:
59
60  ```python
61  ASSESSMENT_AGENT_PROMPT_VERSION = "assessment_agent_prompt_v1"  # was v0
62  ```
63
64  (For the drafting agent, use `DRAFTING_AGENT_PROMPT_VERSION` instead.)
65
````

**Step 2: activate it.**
- **L58–62** Change the version constant in `prompt_config.py`, e.g. `ASSESSMENT_AGENT_PROMPT_VERSION = "assessment_agent_prompt_v1"`. That single string decides which module the agent and tools load.
- **L64** The drafting agent has its own constant, `DRAFTING_AGENT_PROMPT_VERSION`, so the two agents can be on different versions.


#### Lines 66–76

````markdown
66  ### 3. Test the Prompt
67
68  The agent loads its prompt dynamically at import time — no code changes needed:
69
70  ```bash
71  # Test on a single incident
72  python main.py --incidents INC0600745
73
74  # stdout will show: "✓ Loaded prompt version: assessment_agent_prompt_v1"
75  ```
76
````

**Step 3: smoke-test.**
- **L68** The prompt is loaded dynamically when the agent module is imported, so switching versions needs no code change.
- **L70–75** Run the pipeline on one incident (`python main.py --incidents INC0600745`).
  - This `main.py` is the **repo-root** CLI script, not `backend/main.py`, which is the FastAPI app with no CLI arguments. `backend/main.py`'s docstring mentions that a root `main.py` imports its batch functions.
  - The expected console line `✓ Loaded prompt version: ...` is printed by `backend/agents/assessment_agent.py` (and the drafting equivalent) at import time. It is a quick way to confirm which version actually loaded.


#### Lines 77–93

````markdown
77  ### 4. Evaluate Against Ground Truth
78
79  ```bash
80  # Run the agent fresh + compare against human ground truth (writes new revisions to DB)
81  python local_dev/test_scripts/evaluate_prompts.py
82
83  # Just re-score existing agent assessments already in the DB (no new LLM calls)
84  python local_dev/test_scripts/evaluate_prompts.py --no-run
85
86  # Restrict to specific incidents
87  python local_dev/test_scripts/evaluate_prompts.py --incidents INC0600745,INC0633882
88  ```
89
90  Human ground truth is any `reportability_assessments` row where `provided_by != "Assessment Agent"`
91  (i.e., extracted from 1LOD/2LOD review and draft template emails — see
92  `local_dev/ingestion/extract_human_assessments.py`).
93
````

**Step 4: evaluate against ground truth.**
- **L79–88** Three ways to run the evaluator:
  - Default: run the agent fresh, writing new assessment revisions to the DB, then compare.
  - `--no-run`: only re-score assessments already in the DB, with no new LLM calls (cheaper and faster).
  - `--incidents`: restrict the run to specific incidents.
  - Note: the path `local_dev/test_scripts/evaluate_prompts.py` isn't in `backend/`. The equivalent current script is `backend/evaluation/evaluate_assessment_prompts.py`, which has the same `--incidents` and `--no-run` flags. The README path looks stale.
- **L90–92** Defines **human ground truth**: any row in the `reportability_assessments` table whose `provided_by` is not `"Assessment Agent"`. Those rows were extracted from 1LOD/2LOD review emails and draft-template emails by an ingestion script under `local_dev/`. In other words, the "answer key" comes from what humans actually decided in past email chains.


#### Lines 94–106

````markdown
 94  ### 5. Review Results
 95
 96  Check `prompt_iterations/evaluations/eval_<version>_<timestamp>.txt`:
 97  - Overall reportability flag accuracy
 98  - Per-criterion accuracy (Data/System, Operational, Financial/Reputational, Systemic)
 99  - List of mismatched incidents
100
101  ### 6. Iterate
102
103  - **Improved** → Keep the new version active, create the next version for further iteration
104  - **Worse** → Revert `prompt_config.py` to the previous version
105  - **Analyze** → Study mismatches (both false positives and false negatives) to guide the next version
106
````

**Step 5: review the results file.** The file `evaluations/eval_<version>_<timestamp>.txt` reports:
- **L97** overall accuracy of the yes/no "reportable" flag;
- **L98** accuracy for each of the four regulatory criteria: Data/System, Operational, Financial/Reputational and Systemic;
- **L99** the list of incidents where agent and human disagree.

**Step 6: iterate.**
- **L103** If the new version is better, keep it and start the next one.
- **L104** If it is worse, revert the config.
- **L105** Study both kinds of mismatch:
  - **false positives**: the agent says reportable, the human says not;
  - **false negatives**: the reverse.


#### Lines 107–122

````markdown
107  ## Version History — Assessment Agent
108
109  ### v0 (original baseline)
110  - Original prompt with generic FRB reportability questions (paraphrased, not exact
111    regulatory wording); only explicit numeric threshold was the 4-hour operational rule
112  - This is the prompt that was active when the 42 existing agent assessments in the DB
113    were generated
114  - **Measured accuracy (n=17 vs human ground truth):**
115    - Overall flag accuracy: 35.3%
116    - Data/System Impact: 35.3%
117    - Operational Impact: 58.8%
118    - Financial/Reputational Impact: 70.6%
119    - Systemic Risk: 41.2%
120  - Key issue: agent skews conservative (6 false negatives — human said reportable, agent
121    said not) and over-triggers on Systemic Risk in a few cases (4 false positives)
122
````

**Version history: v0 (the original baseline).**
- **L110–111** v0 used paraphrased, generic FRB (Federal Reserve Board) reportability questions rather than the exact regulatory wording. Its only numeric threshold was a 4-hour rule for operational impact.
- **L112–113** v0 produced the 42 agent assessments that were already in the DB.
- **L114–119** Measured accuracy on 17 incidents with human ground truth: 35.3% overall flag accuracy, with the per-criterion accuracies listed. "Accuracy" here is the share of incidents where the agent's answer equals the human's.
- **L120–121** Diagnosis: the agent was too conservative in one direction (6 false negatives) and over-triggered Systemic Risk (4 false positives).


#### Lines 123–137

````markdown
123  ### v1 (2026-09-01) — Exact Regulatory Wording
124  - Rewrote each criterion to quote the literal regulatory question and "Report if Yes"
125    example phrasing from the FRB guidance
126  - Added explicit numeric indicators matching the regulatory grid format: >X hours/days,
127    >Y% of customer base, >$Z financial loss
128  - **Measured accuracy (n=23 vs human ground truth):**
129    - Overall flag accuracy: 47.8% (up from 33.3% in v0)
130    - Data/System Impact: 61.9%
131    - Operational Impact: 42.9% (12 false positives — still the biggest problem)
132    - Financial/Reputational Impact: 76.2%
133    - Systemic Risk: 95.2%
134  - Key issue: massive false positive rate on Operational Impact. Agent says "Yes"
135    for nearly every P2 outage; humans apply a much higher bar. The "Be conservative"
136    instruction compounds this.
137
````

**v1 (2026-09-01): exact regulatory wording.**
- **L124–127** The changes:
  - each criterion now quotes the literal FRB question and its "Report if Yes" examples;
  - explicit numeric thresholds were added in the regulator's grid style (more than X hours, more than Y% of customers, more than $Z loss).
- **L128–133** Results on n=23. Overall rose to 47.8%. Systemic Risk jumped to 95.2%, but Operational Impact fell to 42.9%.
- **L134–136** Diagnosis: a huge false-positive rate on Operational Impact. The agent called almost every P2 (priority-2) outage reportable, while humans apply a much higher bar. The prompt's "Be conservative" instruction made this worse.
- Note: L129 says "up from 33.3% in v0", but the v0 section above reports 35.3%. The likely explanation is a different incident sample (n=17 vs n=23), but the README doesn't say.


#### Lines 138–161

````markdown
138  ### v2 (2026-09-08) — Evidence-Based with Few-Shot Examples — **currently active**
139  - **Root cause analysis** of 24 agent-vs-human mismatches from Jan–Aug⟦?⟧ 2026 data
140  - **Removed "Be conservative / lean toward reportability" bias** — this directly
141    caused the false positive epidemic. Replaced with evidence-based evaluation mindset.
142  - **Rewrote Operational Impact** with dramatically higher bar:
143    - Now requires BOTH extended duration (4+ hours total unavailability) AND broad
144      customer impact (>10% of customers or core banking/payments)
145    - Added explicit "NOT operational impact" examples: back-office systems, market data
146      feeds, batch processing, internal tools, limited user groups
147    - Added self-check question the agent must ask before answering Yes
148  - **Rewrote Data/System Impact** to broadly cover CIA triad "Availability":
149    - "Information system" interpreted broadly: ANY bank system (email, SharePoint,
150      call center, workstations, etc.) whose total outage disrupted operations
151    - Key distinction: "total outage" = Yes vs "degraded performance" = No
152    - Added self-check: "Did the system go DOWN or just slow down?"
153  - **Tightened Financial/Reputational Impact** to require stated evidence (not inferred
154    "potential" harm)
155  - **Added 6 concrete few-shot examples** drawn from real incident patterns:
156    - A: P1 power outage, core banking down → REPORTABLE
157    - B: P2 market data system delay → NOT REPORTABLE
158    - C: P2 compliance voice recording down → REPORTABLE
159    - D: P2 Citrix VDA issue → NOT REPORTABLE
160    - E: P2 online banking unavailable → REPORTABLE
161    - F: P2 cloud application intermittent issues → NOT REPORTABLE
````

**v2 (2026-09-08): evidence-based with few-shot examples.** It was marked "currently active" when written. It is no longer active; the config is now at v10.
- **L139** Based on a root-cause review of 24 agent-vs-human mismatches. The month range "Jan–Aug" was flagged as an uncertain reading (⟦?⟧) in the screenshot transcription.
- **L140–141** Removed the "be conservative / lean toward reportability" instruction, which was identified as the cause of the false positives.
- **L142–147** Operational Impact now requires **both** at least 4 hours of total unavailability **and** broad customer impact (more than 10% of customers, or core banking/payments). It adds explicit counter-examples (back-office, market data, batch, internal tools) and a self-check question.
- **L148–152** Data/System Impact is now read through the "Availability" part of the CIA triad (Confidentiality, Integrity, Availability, a standard security model). Any bank system counts, but only a *total outage* is a Yes, while *degraded performance* is a No. The self-check is "did it go down or just slow down?".
- **L153–154** Financial/Reputational now needs *stated* evidence, not speculative "potential" harm.
- **L155–161** Six **few-shot examples** (A–F), short worked examples embedded in the prompt to show the model where the boundary sits, three reportable and three not. Few-shot prompting is one of the most effective ways to calibrate an LLM's judgement.


#### Lines 162–178

````markdown
162  - **Critical infrastructure fix:** The `evaluate_reportability` tool was making its
163    own LLM call with NO system prompt — the agent's SYSTEM_PROMPT never reached
164    the actual assessment. Fixed by loading the active prompt version as a system
165    message in the tool's `chat_structured()` call (`_get_assessment_system_prompt()`).
166  - **Measured accuracy (n=16 vs human ground truth):**
167    - Overall flag accuracy: **68.8%** (up from 47.8% in v1, 33.3% in v0)
168    - Data/System Impact: 62.5%
169    - Operational Impact: **87.5%** (was 42.9% in v1, 18.2% in v0 — biggest win)
170    - Financial/Reputational Impact: **87.5%** (was 76.2% in v1)
171    - Systemic Risk: 93.8%
172  - Remaining mismatches:
173    - Data/System Impact: agent still over-reports on some short-duration outages
174      (e.g., 2.5-hour portal outage where humans said No because no actual harm)
175      and under-reports on one email outage (INC0600745 — human counted 8-hour
176      email delays as Yes)
177    - 5 false positives on overall flag, 1 false negative
178
````

- **L162–165** The most important engineering finding here: a **bug**. The `evaluate_reportability` tool (in `backend/tools/assessment_tools.py`) was making its own LLM call *without* a system prompt, so the carefully versioned `SYSTEM_PROMPT` never actually reached the step that made the decision. The fix was to load the active prompt version and pass it as the system message in the tool's `chat_structured()` call, via `_get_assessment_system_prompt()`. The lesson: with agent + tool architectures, check *which* LLM call your prompt is really attached to.
- **L166–171** v2 results on n=16: 68.8% overall, with Operational at 87.5% and Financial/Reputational at 87.5%.
  - Note: L169 says Operational was "18.2% in v0", but the v0 section lists 58.8% for Operational. These numbers are inconsistent within the README, probably from re-measuring v0 on a different sample. Treat the cross-version comparisons as approximate.
- **L172–177** The remaining errors: Data/System still over-reports short outages and under-reports one 8-hour email-delay case (INC0600745). There are 5 false positives and 1 false negative on the overall flag.


#### Lines 179–181

````markdown
179  ### v3 (TBD)
180  - [Document changes from v2 here, once v2 is evaluated]
181
````

- **L179–180** A placeholder section for v3 that was never filled in. Versions v3 through v10 exist per `prompt_config.py`, but none of them is documented here, so their change history presumably lives in each prompt module's docstring instead, as step 1 of the workflow asks.


#### Lines 182–190

````markdown
182  ## Tips for Prompt Improvement
183
184  1. **Analyze Mismatches**: Look at incidents where agent disagrees with human, both directions
185  2. **Add Examples**: Include specific examples from past incidents (especially near-boundary cases)
186  3. **Clarify Thresholds**: Make quantitative thresholds more explicit and numeric
187  4. **Adjust Tone**: Conservative vs. liberal interpretation of criteria
188  5. **Test Incrementally**: Change one thing at a time to isolate impact
189  6. **Watch Systemic Risk**: v0 shows this criterion is the most over-triggered — consider tightening it
190
````

General prompt-engineering tips:
- **L184** Study disagreements in both directions.
- **L185** Add real examples, especially borderline ones.
- **L186** Make thresholds explicit and numeric.
- **L187** Tune how strict or lenient the interpretation is.
- **L188** Change one thing at a time so you know what caused an improvement. This is the same principle as a controlled experiment.
- **L189** Watch Systemic Risk, which the v0 run showed over-triggering. By v1/v2 it was at 93–95%, so this tip is also dated.


---

## 38. `backend/evaluation/prompt_iterations/prompt_config.py`

*25 lines · complete*

This is the **"which prompt is live" switch** for both LLM agents. It holds two constants naming the active assessment and drafting prompt modules, plus three folder paths used by the prompt-iteration and evaluation tooling.

Almost everything prompt-related reads from here:
- `loader.py` uses it to import the right prompt.
- `data_tools.py` derives the short `v<N>` labels written to the DB `prompt_version` columns.
- The evaluation scripts use it to decide which version to judge.
- `eval_cache.py` uses `EVALUATIONS_DIR` for its JSON cache.

To ship a new prompt you change one string here.


#### Lines 1–13

````python
 1  """
 2  Prompt Configuration
 3
 4  Specify which prompt version each agent should use.
 5  Naming convention: prompts/<agent_name>_prompt_v<N>.py
 6
 7  To iterate on a prompt:
 8    1. Copy the current version, e.g.
 9       cp prompts/assessment_agent_prompt_v0.py prompts/assessment_agent_prompt_v1.py
10    2. Edit the new file's SYSTEM_PROMPT and update its docstring with rationale/results.
11    3. Bump the corresponding *_PROMPT_VERSION constant below.
12  """
13
````

**Module docstring**, which doubles as a how-to:
- **L5**: The naming convention is `prompts/<agent_name>_prompt_v<N>.py`. The `v<N>` suffix matters, because `_short_prompt_version` in `data_tools.py` extracts it with a regex to label DB rows.
- **L7–11**: The iteration workflow:
  1. Copy the current version file to a new number.
  2. Edit its `SYSTEM_PROMPT` and record why you changed it, and the results, in its docstring.
  3. Bump the constant below.


#### Lines 14–20

````python
14  from pathlib import Path
15
16  # ── Active prompt version per agent ───────────────────────────────────────────
17  # Module name (without .py) inside prompt_iterations/prompts/
18  ASSESSMENT_AGENT_PROMPT_VERSION = "assessment_agent_prompt_v10"
19  DRAFTING_AGENT_PROMPT_VERSION = "drafting_agent_prompt_v4"
20
````

- **L14**: `Path` (from `pathlib`) is used for the folder constants further down.
- **L16–17**: A section-divider comment. The values are module names *without* `.py`, relative to `prompt_iterations/prompts/`.
- **L18**: `ASSESSMENT_AGENT_PROMPT_VERSION = "assessment_agent_prompt_v10"`. The assessment agent is on its 11th prompt iteration (counting from v0). The DB label derived from this is `'v10'`.
- **L19**: `DRAFTING_AGENT_PROMPT_VERSION = "drafting_agent_prompt_v4"`. The drafting agent's active prompt is v4, and its DB label (`TEMPLATE_PROMPT_VERSION_LABEL`) is `'v4'`.


#### Lines 21–25

````python
21  # ── Paths ──────────────────────────────────────────────────────────────────────
22  PROMPTS_DIR = Path(__file__).parent / "prompts"
23  EVALUATIONS_DIR = Path(__file__).parent / "evaluations"
24  VERSIONS_DIR = Path(__file__).parent / "versions"
25
````

Folder constants, all relative to this file's own folder (`Path(__file__).parent`, i.e. `backend/evaluation/prompt_iterations/`), so they work no matter which directory you run from:
- **L22**: `PROMPTS_DIR` is where the versioned prompt modules live.
- **L23**: `EVALUATIONS_DIR` is where evaluation reports are written. `eval_cache.py` also stores `latest_<agent>_evaluation.json` here. (`compare_agent_vs_human.py` builds the same path itself instead of importing this.)
- **L24**: `VERSIONS_DIR` points to a `versions/` folder. Nothing in the transcribed backend references `PROMPTS_DIR` or `VERSIONS_DIR`, so they look unused or kept for future use.


---

## 39. `backend/evaluation/prompt_iterations/loader.py`

*37 lines · complete*

This is the **single place that turns "which prompt version is active" into an actual Python module**. `prompt_config.py` holds the active version names as strings (e.g. `"assessment_agent_prompt_v10"`). This file imports `backend.evaluation.prompt_iterations.prompts.<that name>` and returns the module object, so callers can read `SYSTEM_PROMPT`, `AGENT_NAME`, `DISPLAY_NAME` and similar from it.

Callers:
- `backend/agents/assessment_agent.py` and `backend/agents/drafting_agent.py`, at import time.
- `backend/tools/assessment_tools.py` and `backend/tools/drafting_tools.py`, lazily inside helper functions, to give their own LLM calls the same system prompt.

This indirection is what lets the team swap prompt versions by editing one constant.


#### Lines 1–12

````python
 1  """Shared loader for the active per-agent prompt module.
 2
 3  Every agent/tool that needs the active assessment or drafting prompt used to
 4  duplicate the same `sys.path.insert(...)` + `importlib.import_module(...)`
 5  dance (backend/agents/assessment_agent.py, backend/agents/drafting_agent.py,
 6  backend/tools/assessment_tools.py, backend/tools/drafting_tools.py,
 7  backend/tools/data_tools.py, and the evaluation scripts). Centralizing it
 8  here means there's exactly one place that resolves a *_PROMPT_VERSION
 9  constant from prompt_config.py to its module, so the resolution logic can't
10  drift out of sync between call sites.
11  """
12
````

**Module docstring.** It explains the motivation. Before this file existed, every agent and tool (and the eval scripts) repeated the same `sys.path.insert(...)` + `importlib.import_module(...)` code. Centralising it means the mapping from version constant to module lives in one place and can't drift between call sites.


#### Lines 13–22

````python
13  import importlib
14  from types import ModuleType
15
16  from backend.evaluation.prompt_iterations.prompt_config import (
17      ASSESSMENT_AGENT_PROMPT_VERSION,
18      DRAFTING_AGENT_PROMPT_VERSION,
19  )
20
21  _PROMPTS_PACKAGE = "backend.evaluation.prompt_iterations.prompts"
22
````

- **L13**: `importlib` is the standard-library module for importing modules *by name at runtime*, when the name is a string you only know while the program is running. A normal `import` statement needs the name written into the source.
- **L14**: `ModuleType` is the type of a module object, used only in the return-type hints below.
- **L16–19**: Imports the two active-version strings from `prompt_config.py`. Because this is a plain import, the versions are fixed when `prompt_config` is first imported. Changing the constant needs a process restart to take effect, which matches "edit the config, then run".
- **L21**: `_PROMPTS_PACKAGE` is the dotted package path where the prompt modules live. The leading underscore marks it as internal to this file.


#### Lines 23–28

````python
23
24  def load_prompt_module(version_name: str) -> ModuleType:
25      """Import and return prompts/<version_name>.py by module name."""
26      return importlib.import_module(f"{_PROMPTS_PACKAGE}.{version_name}")
27
28
````

**`load_prompt_module(version_name)`** is the generic loader.
- **L24**: Takes a module name such as `"assessment_agent_prompt_v10"` and returns a module.
- **L26**: Builds the full dotted name with an f-string (e.g. `backend.evaluation.prompt_iterations.prompts.assessment_agent_prompt_v10`) and imports it. Python caches imported modules in `sys.modules`, so repeated calls are cheap and return the same object. If the file doesn't exist, this raises `ModuleNotFoundError`, which the docstring in `assessment_agent.py` calls "fail loud".


#### Lines 29–37

````python
29  def load_assessment_prompt_module() -> ModuleType:
30      """Return the active assessment agent prompt module."""
31      return load_prompt_module(ASSESSMENT_AGENT_PROMPT_VERSION)
32
33
34  def load_drafting_prompt_module() -> ModuleType:
35      """Return the active drafting agent prompt module."""
36      return load_prompt_module(DRAFTING_AGENT_PROMPT_VERSION)
37
````

Two convenience wrappers, so callers don't have to know which constant to pass:
- **L29–31**: `load_assessment_prompt_module()` loads whatever `ASSESSMENT_AGENT_PROMPT_VERSION` names.
- **L34–36**: `load_drafting_prompt_module()` does the same with `DRAFTING_AGENT_PROMPT_VERSION`.

Note: the transcribed backend files only include `assessment_agent_prompt_v0.py` and `drafting_agent_prompt_v0.py`, while the config points at `v10` and `v4`. Those newer prompt files presumably exist in the real repo but weren't part of the screenshots. If they were missing, these loaders would raise on import.


---

## 40. `backend/evaluation/prompt_iterations/prompts/__init__.py`

*2 lines · complete*

This is the package marker for the `prompts/` folder. An `__init__.py` makes a directory a regular Python **package**, so its prompt files can be imported with dotted names such as `backend.evaluation.prompt_iterations.prompts.assessment_agent_prompt_v10`, which is exactly what `loader.py` does with `importlib`. The file has no code, only a docstring.


#### Lines 1–2

````python
1  """Prompt versions for assessment agent."""
2
````

- **L1**: A one-line module docstring, "Prompt versions for assessment agent." Note: it is slightly out of date. The same folder also holds the **drafting** agent's prompts (`drafting_agent_prompt_v0.py` ... `v4`), not only the assessment agent's.
- **L2**: A blank line at the end of the file.


---

## 41. `backend/evaluation/prompt_iterations/prompts/assessment_agent_prompt_v0.py`

*151 lines · complete*

This is **version 0** (the original baseline) of the Assessment Agent's prompt. It's one file in a folder of numbered prompt versions (`v0`, `v1`, … `v10`). The team copies the previous version, edits it, measures it against human ground truth, and records the result in the docstring. Which version is live is set in `prompt_config.py` (`ASSESSMENT_AGENT_PROMPT_VERSION`, currently `assessment_agent_prompt_v10`). `loader.py` imports that module by name, and `backend/agents/assessment_agent.py` and `backend/tools/assessment_tools.py` read its `AGENT_NAME`, `DISPLAY_NAME`, `DESCRIPTION` and `SYSTEM_PROMPT`.

So this file is **not active today**. It's kept as history and as the starting point of the prompt-iteration story. The module is almost entirely one big string (`SYSTEM_PROMPT`): it tells an LLM how to judge the 4 FRB (Federal Reserve Board) reportability questions and what tool-calling workflow to follow.


#### Lines 1–15

````python
 1  """
 2  Assessment Agent - Prompt v0 (Original Baseline)
 3
 4  Original prompt with generic FRB reportability questions (no exact regulatory
 5  wording, no explicit numeric thresholds beyond the 4-hour operational impact
 6  rule). This is the prompt that was active when the 42 existing agent
 7  assessments in the DB were generated.
 8
 9  Performance (n=17 vs human ground truth, measured against these 42 assessments):
10    Overall flag accuracy: 35.3%
11    Data/System Impact: 35.3%
12    Operational Impact: 58.8%
13    Financial/Reputational Impact: 70.6%
14    Systemic Risk: 41.2%
15  """
````

The module docstring, a triple-quoted string at the very top of a file that Python stores as the module's `__doc__`. Here it doubles as a lab notebook entry for this prompt version.
- **L2** Names this as the "Original Baseline" assessment prompt.
- **L4–7** Says what's special about v0. The questions are *generic* (paraphrased, not the regulator's exact wording), and the only hard number is the "4-hour" operational-outage rule. It was also the prompt live when the 42 agent assessments already in the database were produced. That matters because the later versions are measured against those rows.
- **L9–14** The measured accuracy against human ground truth on 17 incidents. "Overall flag accuracy" is how often the agent's final Yes/No reportability flag matched the human's. The other four lines give per-criterion accuracy. 35.3% overall is poor, which is why v1, v2 and later versions exist. These are the same figures as the v0 section of `prompt_iterations/README.md`.
  - Note: the README's v1 section says "up from 33.3% in v0", which slightly contradicts the 35.3% here. It's a documentation inconsistency only.


#### Lines 16–26

````python
16
17  from backend.tools.data_tools import db_query, db_write
18  from backend.tools.assessment_tools import evaluate_reportability
19  from backend.tools.audit_tools import audit_log
20
21  AGENT_NAME = "assessment_agent"
22  DISPLAY_NAME = "Assessment Agent"
23  DESCRIPTION = "Evaluates regulatory reportability of incidents using FRB criteria"
24
25  # Tools available to this agent
26  TOOLS = [db_query, db_write, evaluate_reportability, audit_log]
````

Imports, module metadata and the tool list.
- **L17** Imports `db_query` and `db_write` from `backend/tools/data_tools.py`. These are LangChain `@tool`-decorated functions that let an LLM read and write Postgres tables by name, e.g. `db_query("incidents", {"incident_number": ...})`.
- **L18** Imports `evaluate_reportability`, the tool that makes its own LLM call to answer the 4 FRB questions and return structured text.
- **L19** Imports `audit_log`, the tool that writes a row to the `audit_logs` table for compliance.
- **L21–23** Three plain string constants every prompt module must define. `AGENT_NAME` is the internal key, `DISPLAY_NAME` is the human-friendly label, and `DESCRIPTION` is a one-liner. `backend/agents/assessment_agent.py` copies these from whichever prompt version is active, and `backend/agents/__init__.py` uses them to build the agent registry the UI shows.
- **L25–26** `TOOLS` is the list of tool objects this agent version was meant to have.
  - Note: the current `backend/agents/assessment_agent.py` defines its *own* `TOOLS` list (it adds `lookup_cuso_applications` and `get_regulatory_requirements`) and does **not** read this one. In practice this list is documentation, and importing it just means importing those tool modules.


#### Lines 27–34

````python
27
28  # System prompt defining the agent's role and behavior
29  SYSTEM_PROMPT = """You are a regulatory compliance analyst for a financial institution.
30  Your role is to evaluate whether incidents meet FRB (Federal Reserve Board) reportability criteria.
31
32  ## FRB Reportability Criteria
33
34  You must evaluate incidents against 4 critical questions:
````

The start of the big system prompt string.
- **L28** A comment. The "system prompt" is the first, hidden message given to an LLM. It sets the model's role and rules before any user or task message.
- **L29** `SYSTEM_PROMPT = """...` opens a multi-line triple-quoted string that runs to L150. Everything until the closing `"""` is plain English text sent to the model, not Python code.
- **L29–30** Sets the persona: a regulatory compliance analyst whose job is to decide whether an incident meets FRB reportability criteria.
- **L32–34** A Markdown heading and the framing that every incident is judged on **4 questions**. Markdown (`##`, `**bold**`) is used because LLMs follow structured, headed prompts more reliably.


#### Lines 35–51

````python
35
36  ### 1. Data/System Impact
37  **Question:** Has there been significant data loss, data breach, or system compromise?
38
39  **Evaluate as "Yes" if ANY of these apply:**
40  - Unauthorized access to sensitive customer data (PII, financial records)
41  - Data breach affecting customer information
42  - Loss or corruption of critical business data
43  - System compromise or security breach
44  - Ransomware or malware affecting systems
45  - Unauthorized modification of data or systems
46
47  **Evaluate as "No" if:**
48  - No data loss or breach occurred
49  - Only internal systems affected with no data exposure
50  - Incident contained before data compromise
51
````

**Criterion 1: Data/System Impact.**
- **L36–37** The question: has there been significant data loss, a data breach, or a system compromise?
- **L39–45** The "Yes" triggers. Any single one is enough: unauthorized access to PII or financial records, a breach of customer info, loss or corruption of critical data, a system compromise, ransomware or malware, or unauthorized modification.
- **L47–50** The "No" conditions: no loss or breach, internal-only with no exposure, or contained before compromise.
- Why it's structured this way: giving the model explicit Yes and No examples makes its answers more consistent. The downstream code (`evaluate_reportability` in `assessment_tools.py`) checks whether each criterion's text **starts with "Yes"**, so a clear Yes/No framing matters mechanically too.


#### Lines 52–68

````python
52  ### 2. Operational Impact
53  **Question:** Has there been material disruption to business operations?
54
55  **Evaluate as "Yes" if ANY of these apply:**
56  - Service outage lasting **4+ hours** for critical systems
57  - Inability to process customer transactions for extended period
58  - Disruption to core banking functions (payments, wire transfers, ATM services)
59  - Multiple business units or applications affected simultaneously
60  - Customer-facing services unavailable or degraded
61  - Significant backlog of transactions or processing delays
62
63  **Evaluate as "No" if:**
64  - Outage duration less than 4 hours
65  - Only internal systems affected with no customer impact
66  - Minimal business disruption with quick recovery
67  - Redundant systems maintained service continuity
68
````

**Criterion 2: Operational Impact.**
- **L53** The question: was there *material* disruption to business operations?
- **L55–61** The "Yes" triggers. **L56** holds the only hard numeric threshold in v0: an outage of **4+ hours** on critical systems. The others are qualitative: unable to process customer transactions, disruption to payments, wires or ATMs, multiple business units hit, customer-facing services down, or big backlogs.
- **L63–67** The "No" conditions mirror these: under 4 hours, internal-only, quick recovery, or redundancy kept service up.
- Words like "extended period" and "significant" are vague, and the docstring's 58.8% accuracy on this criterion reflects that. Later versions add explicit numbers.


#### Lines 69–86

````python
69  ### 3. Financial/Reputational Impact
70  **Question:** Has there been material financial loss or reputational harm?
71
72  **Evaluate as "Yes" if ANY of these apply:**
73  - Direct financial losses exceeding materiality thresholds
74  - Potential regulatory fines or penalties
75  - Significant customer complaints or dissatisfaction
76  - Media attention or public disclosure likely
77  - Loss of customer trust or business relationships
78  - Potential litigation or legal exposure
79  - Impact on institution's reputation in the market
80
81  **Evaluate as "No" if:**
82  - Minimal or no financial impact
83  - No customer complaints or negative feedback
84  - Internal incident with no external visibility
85  - Quick resolution prevented reputational harm
86
````

**Criterion 3: Financial/Reputational Impact.**
- **L70** The question: was there material financial loss or reputational harm?
- **L72–79** The "Yes" triggers: losses above "materiality thresholds" (never defined here), possible fines, many complaints, media attention, loss of trust, litigation risk, or market reputation damage.
- **L81–85** The "No" conditions: minimal financial impact, no complaints, no external visibility, or quickly resolved.
- **L86** A blank line that separates sections inside the string.


#### Lines 87–103

````python
 87  ### 4. Systemic Risk
 88  **Question:** Could this incident affect the stability of the financial system?
 89
 90  **Evaluate as "Yes" if ANY of these apply:**
 91  - Incident affects critical payment systems or infrastructure
 92  - Potential contagion to other financial institutions
 93  - Disruption to interbank transactions or clearing systems
 94  - Impact on market liquidity or functioning
 95  - Affects systemically important financial services
 96  - Could undermine confidence in the financial system
 97
 98  **Evaluate as "No" if:**
 99  - Incident isolated to single institution
100  - No impact on broader financial infrastructure
101  - No systemic implications or contagion risk
102  - Standard operational issue with no market impact
103
````

**Criterion 4: Systemic Risk.**
- **L88** The question: could this incident affect the stability of the *wider financial system*, not just this bank?
- **L90–96** The "Yes" triggers: critical payment infrastructure, contagion to other institutions, interbank or clearing disruption, market liquidity, systemically important services, or undermining confidence in the system.
- **L98–102** The "No" conditions: isolated to one institution, no infrastructure or market impact.
- Per the README, v0 tended to over-trigger on this criterion (false positives). The bullet list is broad enough that a large bank's payment outage can look "systemic".


#### Lines 104–110

````python
104  ## Decision Logic
105  - **If ANY question is "Yes" → Incident is REPORTABLE**
106  - **If ALL questions are "No" → Incident is NOT REPORTABLE**
107  - Provide specific evidence and reasoning for each question
108  - Reference the criteria above in your rationale
109  - Be conservative: when in doubt, lean toward reportability
110
````

**Decision logic.** These are the rules for combining the 4 answers.
- **L105–106** An OR rule: any "Yes" makes the incident REPORTABLE, and all "No" makes it NOT REPORTABLE.
- **L107–108** Asks for specific evidence and reasoning per question that references the criteria.
- **L109** "Be conservative: when in doubt, lean toward reportability." For a regulator-facing process, a missed report is worse than an extra one.
- Today the OR rule is also enforced **in code**: `evaluate_reportability` computes `reportability_flag` itself from whether any of the 4 texts starts with "yes". The model doesn't set the flag.


#### Lines 111–123

````python
111  ## Your Responsibilities
112  1. Query incident details from the database using the incident number
113  2. Review similar past incidents for consistency in decision-making
114  3. Evaluate reportability using the 4 FRB questions via the evaluate_reportability tool
115  4. Write comprehensive assessment results to the reportability_assessments table
116  5. Log all actions to the audit trail for regulatory compliance
117
118  ## Guidelines
119  - **Be thorough:** Consider all aspects of the incident - severity, duration, scope, impact
120  - **Be consistent:** Query similar past incidents to ensure consistent decision-making
121  - **Be clear:** Provide detailed rationale for each FRB question assessment
122  - **Be compliant:** Always log your actions for the audit trail
123
````

The responsibilities and style guidelines.
- **L111–116** Five numbered responsibilities that describe an *agentic* (tool-calling) workflow. The model is expected to query the DB itself, look at similar past incidents, call `evaluate_reportability`, write to `reportability_assessments`, and log to the audit trail.
- **L118–122** Four "Be …" guidelines: be thorough (severity, duration, scope), be consistent (compare with precedent), be clear (rationale per question), and be compliant (always audit-log).
- Context: this "the agent decides which tools to call" design is what `backend/services/assessment_service.py` later **replaced** with deterministic Python orchestration, because the agent sometimes stopped early without saving anything.


#### Lines 124–143

````python
124  ## Workflow
125  When asked to assess an incident:
126
127  1. **Query the incident** - Use db_query to get full incident details by incident_number
128
129  2. **Query similar incidents for context** - Look for incidents with same legal_entity and priority
130
131  3. **Evaluate reportability** - Call evaluate_reportability tool with all incident data fields. This returns a structured assessment⟦CUT⟧
132
133  4. **Write assessment results** - Use db_write to save to reportability_assessments table. CRITICAL: You MUST include ALL fields fro⟦CUT⟧
134     - incident_number
135     - reportability_flag (Yes or No)
136     - data_system_impact (full text from tool)
137     - operational_impact (full text from tool)
138     - financial_reputational_impact (full text from tool)
139     - systemic_risk (full text from tool)
140     - provided_by (set to "Assessment Agent")
141
142  5. **Log the assessment** - Use audit_log to record the action for compliance
143
````

**The step-by-step workflow** the agent was told to follow (a ReAct-style loop: reason, call a tool, read the result, repeat).
- **L127** Step 1: call `db_query` to fetch the full incident by `incident_number`.
- **L129** Step 2: query similar incidents with the same `legal_entity` and `priority`, for consistency.
- **L131** Step 3: call `evaluate_reportability` with all the incident fields. This line was ⟦CUT⟧ off the right edge of the screenshot. It probably continued with something like "...structured assessment with the 4 FRB answers" (inference).
- **L133–140** Step 4: save with `db_write` to `reportability_assessments`. L133 is also ⟦CUT⟧. It most likely read "...ALL fields from the tool output" (inference), followed by the required column list on **L134–140**: `incident_number`, `reportability_flag`, the four criterion texts, and `provided_by = "Assessment Agent"`. Those are exactly the fields the `db_write` tool validates as required for that table in `data_tools.py`.
- **L142** Step 5: call `audit_log` to record the action.


#### Lines 144–151

````python
144  ## Important Notes
145  - Always query the FULL incident record before assessment - don't make assumptions
146  - Consider precedent from similar incidents but make independent judgments
147  - If any FRB question is "Yes", the incident is reportable
148  - Provide detailed, specific rationale - avoid generic statements
149  - Log every assessment for compliance and audit purposes
150  """
151
````

Closing reminders, and the end of the string.
- **L145–149** Restates the key rules: always fetch the full record, use precedent but judge independently, any "Yes" means reportable, give specific (not generic) rationale, and log everything. LLM prompts often repeat critical rules at the end because models weigh the start and end of a prompt more heavily.
- **L150** The closing `"""` ends `SYSTEM_PROMPT`.
- **L151** A trailing blank line.


---

## 42. `backend/evaluation/prompt_iterations/prompts/drafting_agent_prompt_v0.py`

*138 lines · complete*

This is **version 0** of the Drafting Agent's prompt. The Drafting Agent writes the regulatory notification (the "template") that eventually goes to the FRB/OCC regulators. Like the assessment prompts, each version lives in its own module, and `prompt_config.py` picks the active one (currently `drafting_agent_prompt_v4`, so this v0 file is historical). `loader.load_drafting_prompt_module()` imports the active version, and `backend/agents/drafting_agent.py` plus `backend/tools/drafting_tools.py` read its `SYSTEM_PROMPT` and metadata.

Most of the file is one prompt string. It describes two tool-calling workflows: an **initial draft**, and a **revision** that takes in reviewer feedback from 1LOD/2LOD (first and second line of defence).


#### Lines 1–9

````python
1  """
2  Drafting Agent - Prompt v0 (Initial Baseline)
3
4  Initial prompt for generating regulatory notification templates from incident
5  data and reportability assessments. Supports both initial draft creation and
6  revision mode (incorporating reviewer feedback).
7
8  Performance: Not yet evaluated (new agent).
9  """
````

The module docstring, which works as the version's changelog entry.
- **L2** Identifies this as the initial baseline drafting prompt.
- **L4–6** Says the prompt turns incident data plus the reportability assessment into a regulatory notification template. It supports both a first draft and "revision mode" (applying reviewer feedback).
- **L8** "Not yet evaluated". When v0 was written, there was no drafting evaluation yet. The LLM-as-judge drafting evaluation came later.


#### Lines 10–21

````python
10
11  from backend.tools.data_tools import db_query, db_write
12  from backend.tools.drafting_tools import generate_template_content, get_template_schema
13  from backend.tools.audit_tools import audit_log
14
15  AGENT_NAME = "drafting_agent"
16  DISPLAY_NAME = "Drafting Agent"
17  DESCRIPTION = "Generates regulatory notification templates for FRB/OCC submission"
18
19  # Tools available to this agent
20  TOOLS = [db_query, db_write, generate_template_content, get_template_schema, audit_log]
21
````

Imports, metadata and the tool list.
- **L11** `db_query` and `db_write` are generic LangChain tools from `data_tools.py` for reading and writing Postgres tables by name.
- **L12** `generate_template_content` is the tool that makes its own LLM call to write the notification's prose fields (title, description, impact, and so on). `get_template_schema` loads `backend/tools/template_schema.json`, the field definitions and compliance guidelines for the notification format.
- **L13** `audit_log` writes compliance audit rows.
- **L15–17** The required metadata constants (`AGENT_NAME`, `DISPLAY_NAME`, `DESCRIPTION`). `backend/agents/drafting_agent.py` copies them from whichever prompt version is active, for the agent registry.
- **L19–20** `TOOLS` lists the five tools this version expected. The current `backend/agents/drafting_agent.py` defines an identical list of its own and doesn't read this one, so here it mostly serves as documentation.


#### Lines 22–26

````python
22  # System prompt defining the agent's role and behavior
23  SYSTEM_PROMPT = """You are a regulatory notification author for a financial institution.
24  Your role is to draft clear, compliant notifications for FRB/OCC submission based on
25  incident data and reportability assessments.
26
````

The start of `SYSTEM_PROMPT`, a triple-quoted multi-line string that runs to L137. Everything inside is English instructions for the LLM.
- **L22** A comment explaining that the system prompt defines the agent's role.
- **L23–25** The persona: a "regulatory notification author" who writes clear, compliant notifications for FRB/OCC submission, based on incident data and the reportability assessment.


#### Lines 27–34

````python
27  ## Your Responsibilities
28  1. Query incident details and reportability assessment from the database
29  2. Retrieve the template schema to understand the required notification format
30  3. Check for any existing templates (for revision workflows)
31  4. Generate notification template content using the generate_template_content tool
32  5. Write the completed template to the template_details table
33  6. Log all actions to the audit trail for regulatory compliance
34
````

**Responsibilities.** A six-step overview of the job.
- **L28** Read the incident and its reportability assessment from the DB.
- **L29** Fetch the template schema so the model knows the required format.
- **L30** Check for existing templates, which signals a revision.
- **L31** Generate content via `generate_template_content`.
- **L32** Write the finished template to the `template_details` table.
- **L33** Audit-log everything.


#### Lines 35–41

````python
35  ## Drafting Principles
36  - **Be factual:** Base all content strictly on the incident data and assessment findings
37  - **Be precise:** Include specific dates, times (with timezone), quantities, and system names
38  - **Be compliant:** Follow the template schema's compliance guidelines exactly
39  - **Be consistent:** Ensure the notification aligns with the reportability assessment rationale
40  - **Be professional:** Use formal regulatory prose suitable for FRB/OCC submission
41
````

**Drafting principles.** These are style rules for the generated text.
- **L36** Be factual: only use what's in the incident data and assessment. This guards against the LLM inventing ("hallucinating") details in a regulatory filing.
- **L37** Be precise: specific dates, times *with timezone*, quantities and system names.
- **L38** Be compliant: follow the schema's compliance guidelines exactly.
- **L39** Be consistent: the notification must not contradict the assessment's reasoning.
- **L40** Be professional: formal regulatory prose.


#### Lines 42–52

````python
42  ## Workflow - Initial Draft
43  When asked to draft a notification template for an incident:
44
45  1. **Query the incident** - Use db_query to get full incident details:
46     `db_query("incidents", {"incident_number": "<INC_NUMBER>"})`
47
48  2. **Query the assessment** - Use db_query to get the reportability assessment:
49     `db_query("reportability_assessments", {"incident_number": "<INC_NUMBER>"})`
50     Note: P1 incidents may not have an assessment (they skip assessment as auto-reportable).
51     If no assessment exists, proceed with an empty assessment_data dict.
52
````

**Initial-draft workflow, steps 1–2.**
- **L45–46** Step 1 fetches the incident with an example call, `db_query("incidents", {"incident_number": "<INC_NUMBER>"})`. Showing the literal call syntax helps the model produce correctly shaped tool arguments.
- **L48–49** Step 2 fetches the reportability assessment for that incident. (Today the `db_query` tool automatically adds a `prompt_version` filter for this table, so it returns the active assessment version's row.)
- **L50–51** An important special case: **P1 incidents** (top priority) may skip assessment because they're automatically reportable. If no assessment exists, the model should carry on with an empty `assessment_data` dict instead of failing.


#### Lines 53–65

````python
53  3. **Get the template schema** - Call get_template_schema() to retrieve the notification format
54
55  4. **Check for existing templates** - Use db_query to check if a template already exists:
56     `db_query("template_details", {"incident_number": "<INC_NUMBER>"})`
57     If templates exist, this may be a revision workflow.
58
59  5. **Generate template content** - Call generate_template_content with:
60     - incident_data: full incident record from step 1
61     - assessment_data: assessment record from step 2 (or empty dict for P1)
62     - template_schema: schema from step 3
63     - existing_template: None for initial draft
64     - feedback: None for initial draft
65
````

**Initial-draft workflow, steps 3–5.**
- **L53** Step 3 calls `get_template_schema()` to load the notification format from `template_schema.json`.
- **L55–57** Step 4 checks whether a `template_details` row already exists for the incident. If it does, this may really be a revision.
- **L59–64** Step 5 calls `generate_template_content` with five named inputs: the incident record, the assessment (or `{}` for P1), the schema, and `existing_template=None` and `feedback=None`, because this is a first draft. These names match the tool's real parameters in `drafting_tools.py`. The current version of that tool also takes an extra optional `mimt_draft`, which v0 didn't know about.


#### Lines 66–85

````python
66  6. **Write template to database** - Use db_write to save to template_details:
67     `db_write("template_details", {
68        "incident_number": "<INC_NUMBER>",
69        "template_stage": "MIMT Draft",
70        "title": <from generate_template_content>,
71        "current_status": <from generate_template_content>,
72        "date_of_occurrence": <from generate_template_content>,
73        "type": <from generate_template_content>,
74        "legal_entity": <from generate_template_content>,
75        "priority_rating": <from generate_template_content>,
76        "description": <from generate_template_content>,
77        "impact": <from generate_template_content>,
78        "management_actions": <from generate_template_content>,
79        "root_cause": <from generate_template_content>,
80        "pir": <from generate_template_content>,
81        "incident_executive_lead_name": <copy verbatim from incident_data.incident_executive_lead_name, or "TBC" if empty>,
82        "incident_executive_lead_contact": <copy verbatim from incident_data.incident_executive_lead_contact, or "TBC" if empty>,
83        "drafted_by": "Drafting Agent"
84     })`
85
````

**Initial-draft workflow, step 6.** Saving the draft.
- **L66–67** Use `db_write("template_details", {...})`. The example payload spans L67–84.
- **L68–69** `incident_number`, and `template_stage: "MIMT Draft"`. The stage marks which review phase a template row belongs to.
  - Note: in today's code, `"MIMT Draft"` rows are treated as **human-authored**: `assessment_service._gather_assessment_context` explicitly drops MIMT Draft rows written by the Drafting Agent, and the agent's own first draft now uses the stage `"Drafting Agent Draft"`. So this v0 instruction reflects an older convention, which later versions changed.
- **L70–80** The content fields (`title`, `current_status`, `date_of_occurrence`, `type`, `legal_entity`, `priority_rating`, `description`, `impact`, `management_actions`, `root_cause`, `pir`). All are placeholders meaning "take this from the `generate_template_content` output". (PIR is the post-incident review.)
- **L81–82** The incident executive lead's name and contact must be copied **verbatim** from the incident, or set to `"TBC"` ("to be confirmed") if empty. This stops the LLM inventing a person's name or email in a regulatory filing.
- **L83** `drafted_by: "Drafting Agent"` records authorship. It's also part of the `template_details` upsert key in `postgres_client.py`, so agent drafts and human drafts never overwrite each other.
- **L84** Closes the example call.


#### Lines 86–93

````python
86  7. **Log the action** - Use audit_log to record:
87     `audit_log(
88        incident_number="<INC_NUMBER>",
89        action="template_created",
90        actor="Drafting Agent",
91        details={"template_stage": "MIMT Draft", "incident_number": "<INC_NUMBER>"}
92     )`
93
````

**Initial-draft workflow, step 7.** Logging.
- **L86** The last step of the initial draft: record what happened with the `audit_log` tool. In a bank's regulatory process, every automated action has to leave an audit trail.
- **L87–92** An example `audit_log(...)` call for the model to copy. It is wrapped in backticks so it reads as code inside the prompt.
  - `incident_number="<INC_NUMBER>"` is a placeholder the agent replaces with the real ticket number.
  - `action="template_created"` names the event.
  - `actor="Drafting Agent"` records that the AI, not a human, did it.
  - `details={...}` is extra context stored as JSON: which template stage was written (`"MIMT Draft"`) and the incident number.
  - This becomes a row in the `audit_logs` table via `tools/audit_tools.py` → `PostgresClient.audit_log`.
  - Note: like step 6, it uses the old `"MIMT Draft"` stage name. In today's code the agent's first draft is `"Drafting Agent Draft"`, and `drafting_service.py` writes the `template_created`/`template_revised` audit row itself, instead of relying on the LLM to call the tool.
- **L93** A blank line inside the prompt string.


#### Lines 94–106

````python
 94  ## Workflow - Revision (with Reviewer Feedback)
 95  When asked to revise a template with feedback:
 96
 97  1. **Query the incident** - Same as initial draft (step 1)
 98
 99  2. **Query the assessment** - Same as initial draft (step 2)
100
101  3. **Get the template schema** - Same as initial draft (step 3)
102
103  4. **Get the existing template** - Use db_query to retrieve the current template:
104     `db_query("template_details", {"incident_number": "<INC_NUMBER>"})`
105     Use the most recent template (first result, ordered by updated_at DESC).
106
````

**Revision workflow, steps 1–4.** This path is used when a reviewer has sent feedback.
- **L94–95** The heading and trigger: "When asked to revise a template with feedback".
- **L97–101** Steps 1–3 repeat the initial-draft steps: fetch the incident, the assessment and the schema.
- **L103–105** Step 4 fetches the existing template and uses the **most recent** one ("first result, ordered by updated_at DESC"). That matches what `PostgresClient.db_query` really does: for `template_details` and `reportability_assessments` it appends `ORDER BY updated_at DESC`, so `results[0]` is the newest row.


#### Lines 107–127

````python
107  5. **Generate revised content** - Call generate_template_content with:
108     - incident_data: full incident record
109     - assessment_data: assessment record
110     - template_schema: schema
111     - existing_template: the current template from step 4
112     - feedback: the reviewer feedback string
113
114  6. **Write revised template** - Use db_write with the appropriate template_stage:
115     - If revising after 1LOD feedback: template_stage = "1LOD R&C"
116     - If revising after 2LOD feedback accepted by 1LOD: template_stage = "2LOD R&C"
117     Include all fields from generate_template_content plus:
118     - "drafted_by": "Drafting Agent"
119
120  7. **Log the action** - Use audit_log to record the revision:
121     `audit_log(
122        incident_number="<INC_NUMBER>",
123        action="template_revised",
124        actor="Drafting Agent",
125        details={"template_stage": "<STAGE>", "has_feedback": true}
126     )`
127
````

**Revision workflow, steps 5–7.**
- **L107–112** Step 5 calls `generate_template_content` again, this time passing `existing_template` (the current draft) and `feedback` (the reviewer's comments as a string), so the LLM edits rather than starting from scratch.
- **L114–116** Step 6 picks the stage to write. Revising after 1LOD feedback produces stage `"1LOD R&C"`, and revising after 2LOD feedback (which 1LOD has accepted) produces `"2LOD R&C"`. "R&C" is presumably "Review & Comment" (inference). Writing to a new stage, instead of overwriting the old row, keeps each review round's version.
- **L117–118** Include every generated field plus `drafted_by: "Drafting Agent"`.
- **L120–126** Step 7 audit-logs with a concrete example: `action="template_revised"`, `actor="Drafting Agent"`, and `details` noting the stage and that feedback existed. Note that `true` (lowercase) appears because this is JSON-style example text inside a prompt. It's not Python code, so it's never executed.


#### Lines 128–138

````python
128  ## Important Notes
129  - Always query the FULL incident record before drafting - don't make assumptions
130  - For P1 incidents without assessment, proceed with drafting using incident data alone
131  - Include ALL fields from generate_template_content in the db_write payload
132  - Copy incident_executive_lead_name and incident_executive_lead_contact verbatim from
133    incident data, using "TBC" if either is empty - never invent a name or email
134  - The template_stage determines which review phase the draft belongs to
135  - Log every action for compliance and audit purposes
136  - When revising, ensure reviewer feedback is meaningfully incorporated
137  """
138
````

Closing reminders, and the end of the string.
- **L129** Always fetch the full incident first.
- **L130** P1 without an assessment: draft from the incident data alone.
- **L131** Put **all** generated fields in the `db_write` payload. Earlier agents sometimes dropped fields.
- **L132–133** Repeats the verbatim-or-"TBC" rule for the executive lead name and contact: "never invent a name or email".
- **L134** The `template_stage` decides which review phase the row belongs to.
- **L135–136** Log every action, and make sure feedback is "meaningfully incorporated" when revising.
- **L137** The closing `"""` ends `SYSTEM_PROMPT`. **L138** is a trailing blank line.


---

## 43. `backend/evaluation/confusion_matrix_utils.py`

*81 lines · complete*

This is a small **pure-math helper module** for Yes/No (binary) classification metrics. It turns the agent's and human's Yes/No answers into a 2×2 **confusion matrix** (TP/TN/FP/FN) and derives accuracy, precision, recall, specificity and F1 from it. It can render the result as text, or convert it to JSON-safe percentages for the API.

It was split out of `compare_agent_vs_human.py` so that script, `evaluate_assessment_prompts.py`, and through that the `/api/evaluate/assessment` endpoint, all share the same math. It has no database or LLM dependencies.

Throughout, **positive = "Yes" (reportable)**, *predicted* = the agent and *actual* = the human. So a false negative (FN) is an incident the human said was reportable but the agent said wasn't. That is usually the costliest mistake for a regulatory-notification tool.


#### Lines 1–12

````python
 1  """Shared confusion-matrix helpers for the assessment evaluation scripts.
 2
 3  Extracted from compare_agent_vs_human.py so evaluate_assessment_prompts.py
 4  (and anything else that needs a Yes/No confusion matrix) can reuse the same
 5  TP/TN/FP/FN + precision/recall/specificity/F1 math instead of duplicating it.
 6  """
 7
 8  import re
 9  from typing import Dict, List, Optional, Tuple
10
11  YES_NO_RE = re.compile(r"^\s*(yes|no)\b", re.IGNORECASE)
12
````

- **L1–6**: The module docstring: why this file exists (shared TP/TN/FP/FN plus precision/recall/specificity/F1 math, no duplication).
- **L8**: `re` is Python's regular-expression module.
- **L9**: Type-hint helpers. `Dict`, `List`, `Optional[X]` (meaning "X or None") and `Tuple` describe argument and return types. Python doesn't enforce them at runtime; they serve as documentation and help IDEs and linters.
- **L11**: A **compiled regex**, built once at import time and reused. Read piece by piece:
  - `^\s*` means start of string, then optional whitespace.
  - `(yes|no)` captures either word.
  - `\b` is a word boundary, so "Yesterday" doesn't count as "Yes".
  - `re.IGNORECASE` makes "YES", "yes" and "Yes" all match.

  The `r"..."` prefix makes it a raw string, so the backslashes reach the regex engine as written.


#### Lines 13–21

````python
13
14  def parse_yes_no(text: Optional[str]) -> Optional[str]:
15      """Extract the leading Yes/No token from a rationale string. Returns None if absent."""
16      if not text:
17          return None
18      m = YES_NO_RE.match(text)
19      return m.group(1).capitalize() if m else None
20
21
````

**`parse_yes_no(text)`** pulls the leading Yes/No out of a criterion rationale such as "Yes – the outage exceeded 4 hours...".
- **L14**: The signature with type hints. It takes a string or `None` and returns a string or `None`.
- **L16–17**: Empty or `None` text gives `None`, meaning "no label".
- **L18**: `.match` only checks at the *start* of the string, which is what the "leading token" rule needs.
- **L19**: If there is a match, `m.group(1)` is the captured word, and `.capitalize()` normalises it to exactly `"Yes"` or `"No"` so it compares cleanly with `reportability_flag` values. If there is no match, it returns `None`, and the callers leave that pair out of the matrices.


#### Lines 22–31

````python
22  def confusion_counts(pairs: List[Tuple[str, str]]) -> Tuple[int, int, int, int]:
23      """pairs: list of (predicted, actual) as 'Yes'/'No'. Positive class = 'Yes'.
24      Returns (tp, tn, fp, fn)."""
25      tp = sum(1 for p, a in pairs if p == "Yes" and a == "Yes")
26      tn = sum(1 for p, a in pairs if p == "No" and a == "No")
27      fp = sum(1 for p, a in pairs if p == "Yes" and a == "No")
28      fn = sum(1 for p, a in pairs if p == "No" and a == "Yes")
29      return tp, tn, fp, fn
30
31
````

**`confusion_counts(pairs)`** counts the four cells of the 2×2 matrix.
- **L22–24**: The input is a list of `(predicted, actual)` tuples. The output is a tuple `(tp, tn, fp, fn)`.
- **L25–28**: Each line is a **generator expression** inside `sum()`, which adds a 1 for every pair that meets the condition. `for p, a in pairs` unpacks each tuple into two names.
  - **TP**: both said Yes.
  - **TN**: both said No.
  - **FP**: the agent said Yes but the human said No (over-reporting).
  - **FN**: the agent said No but the human said Yes (a missed reportable incident).
- Any value other than "Yes" or "No" (e.g. `None`) falls into no cell and is effectively ignored.


#### Lines 32–50

````python
32  def metrics_from_counts(tp: int, tn: int, fp: int, fn: int) -> Dict:
33      """Compute n/accuracy/precision/recall/specificity/f1 from a 2x2 confusion
34      matrix. Values that are undefined (e.g. no positive predictions) are
35      float('nan') rather than raising, so callers can render "n/a"."""
36      n = tp + tn + fp + fn
37      accuracy = (tp + tn) / n if n else float("nan")
38      precision = tp / (tp + fp) if (tp + fp) else float("nan")
39      recall = tp / (tp + fn) if (tp + fn) else float("nan")
40      specificity = tn / (tn + fp) if (tn + fp) else float("nan")
41      f1 = (2 * precision * recall / (precision + recall)
42            if (precision + recall) and precision == precision and recall == recall and (precision + recall) > 0
43            else float("nan"))
44      return {
45          "n": n, "TP": tp, "TN": tn, "FP": fp, "FN": fn,
46          "accuracy": accuracy, "precision": precision, "recall": recall,
47          "specificity": specificity, "f1": f1,
48      }
49
50
````

**`metrics_from_counts`** derives the standard metrics.
- **L33–35**: The docstring states that undefined metrics become `float('nan')` instead of raising `ZeroDivisionError`, so callers can show "n/a".
- **L36**: `n` is the total number of compared pairs.
- **L37**: **Accuracy** = (TP+TN)/n, the fraction where the agent agreed with the human.
- **L38**: **Precision** = TP/(TP+FP): of the incidents the agent called reportable, how many really were. Each line uses the conditional expression `X if cond else nan` to guard against division by zero.
- **L39**: **Recall** (sensitivity) = TP/(TP+FN): of the truly reportable incidents, how many the agent caught. This is the critical one for compliance.
- **L40**: **Specificity** = TN/(TN+FP): of the truly non-reportable incidents, how many the agent correctly cleared.
- **L41–43**: **F1** = 2·P·R/(P+R), the harmonic mean of precision and recall. The guard checks that neither P nor R is NaN (`x == x` is false only for NaN) and that their sum is above 0. The first `(precision + recall)` check is redundant, because NaN is truthy in Python, but it does no harm.
- **L44–48**: Returns a dict holding the raw counts (uppercase keys `TP`/`TN`/...) and the metrics as fractions between 0 and 1.


#### Lines 51–66

````python
51  def format_confusion_matrix(label: str, m: Dict) -> str:
52      """Render a 2x2 confusion matrix (rows=human/actual, cols=agent/predicted)
53      plus precision/recall/accuracy/F1 for a single column, as text lines."""
54      def pct(x):
55          return "n/a" if x != x else f"{x:.1%}"
56      lines = [
57          f"{label}  (n={m['n']})",
58          f"{'':>16}{'Pred: Yes':>12}{'Pred: No':>12}",
59          f"{'Actual: Yes':>16}{m['TP']:>12}{m['FN']:>12}",
60          f"{'Actual: No':>16}{m['FP']:>12}{m['TN']:>12}",
61          f"  accuracy={pct(m['accuracy'])}  precision={pct(m['precision'])}  "
62          f"recall={pct(m['recall'])}  specificity={pct(m['specificity'])}  f1={pct(m['f1'])}",
63      ]
64      return "\n".join(lines)
65
66
````

**`format_confusion_matrix(label, m)`** renders one matrix as aligned text.
- **L54–55**: Nested helper `pct`: NaN becomes `"n/a"`, and anything else is formatted with `:.1%` (e.g. 0.8235 becomes `82.4%`).
- **L56–63**: Builds a list of lines with **f-string format specs**:
  - `{'':>16}` is an empty string right-aligned in 16 characters, which acts as the top-left blank corner.
  - `{'Pred: Yes':>12}` and similar are column headers 12 characters wide.
  - The rows are *Actual: Yes* → `TP | FN` and *Actual: No* → `FP | TN`. So rows are the human's answer and columns are the agent's.
  - The last line lists all five metrics. It is two adjacent f-strings that Python joins into one.
- **L64**: Joins the lines with newlines into one string.


#### Lines 67–81

````python
67  def metrics_to_json(m: Dict) -> Dict:
68      """Convert a metrics_from_counts() dict (which may contain float('nan'))
69      into a JSON-serializable dict with None instead of NaN, and lower_snake
70      percentage keys - for API responses (see AssessmentEvaluationResponse)."""
71      def pct_or_none(x):
72          return None if x != x else x * 100
73      return {
74          "n": m["n"], "tp": m["TP"], "tn": m["TN"], "fp": m["FP"], "fn": m["FN"],
75          "accuracy_pct": pct_or_none(m["accuracy"]),
76          "precision_pct": pct_or_none(m["precision"]),
77          "recall_pct": pct_or_none(m["recall"]),
78          "specificity_pct": pct_or_none(m["specificity"]),
79          "f1_pct": pct_or_none(m["f1"]),
80      }
81
````

**`metrics_to_json(m)`** prepares the metrics for the API response.
- **L68–70**: JSON has no NaN value, so this converts NaN to `None` (which becomes JSON `null`), multiplies fractions by 100, and renames keys to lowercase `snake_case` with a `_pct` suffix. That shape matches the `ConfusionMatrix` Pydantic model in `schemas.py`, whose fields are `Optional[float]`.
- **L71–72**: Helper: `None` if NaN, else the value × 100.
- **L73–80**: Builds the output dict: lowercase count keys (`tp`, `tn`, ...) and `*_pct` metric keys. `build_summary` in `evaluate_assessment_prompts.py` calls this once per matrix.


---

## 44. `backend/evaluation/compare_agent_vs_human.py`

*170 lines · complete*

This is a **standalone, read-only CLI report** that compares the Assessment Agent's stored answers with human ground truth for the active prompt version. It never calls the LLM. It reads `reportability_assessments`, pairs each incident's latest agent row (`prompt_version == ASSESSMENT_PROMPT_VERSION_LABEL`) with its latest human row (`prompt_version IS NULL`), and prints per-incident ✅/❌ lines plus confusion matrices for the overall flag, for each of the 4 FRB criteria, and for all 4 combined.

It is the older sibling of `evaluate_assessment_prompts.py`. The confusion-matrix helpers were originally written here and later moved out to `confusion_matrix_utils.py`. Nothing else in the backend imports it; you run it by hand.

One important difference from `evaluate_assessment_prompts.py`: this script treats *every* human row as ground truth. It does **not** require 2LOD approval (`final_approval_by_lod == 'Yes'`), so the two scripts can report different numbers for the same data.


#### Lines 1–23

````python
 1  """
 2  Compare Assessment Agent output against human-provided ground truth (loaded
 3  from 'Incident Notification Data.xlsx') in reportability_assessments. Rows
 4  are classified by prompt_version: NULL means human-provided (no agent/prompt
 5  involved - this is the ground truth); this script compares against agent
 6  rows for prompt_version. Incidents that haven't been assessed under the
 7  active prompt version yet are skipped.
 8
 9  Computes accuracy, precision, recall, F1, and specificity for:
10    - The overall reportability_flag (Yes/No)
11    - Each of the 4 FRB sub-criteria (data_system_impact, operational_impact,
12      financial_reputational_impact, systemic_risk), parsed from the leading
13      Yes/No token of each free-text rationale.
14
15  Writes a text report (per-column confusion matrices + precision/recall/F1/
16  accuracy) to backend/evaluation/prompt_iterations/evaluations/<agent_name>_<version>_<timestamp>.txt,
17  in addition to printing to stdout.
18
19  Usage:
20      python backend/evaluation/compare_agent_vs_human.py
21      python backend/evaluation/compare_agent_vs_human.py --output /path/to/report.txt
22  """
23
````

**Module docstring.**
- **L2–7**: Explains the data source. The human ground truth originally came from the spreadsheet *Incident Notification Data.xlsx* and was loaded into `reportability_assessments` with `prompt_version` NULL. Agent rows carry a version label. Incidents with no agent row for the active version are skipped.
- **L9–13**: The metrics it computes: accuracy, precision, recall, F1 and specificity, for the overall `reportability_flag` and for each FRB sub-criterion. Each criterion's Yes/No is parsed from the start of its free-text rationale.
- **L15–17**: Where the report is written: `prompt_iterations/evaluations/<agent_name>_<version>_<timestamp>.txt`, in addition to being printed.
- **L19–21**: Usage, with an optional `--output` path.


#### Lines 24–34

````python
24  import sys
25  import os
26  import argparse
27  from datetime import datetime
28  from pathlib import Path
29
30  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
31
32  from dotenv import load_dotenv
33  load_dotenv()
34
````

- **L24–28**: Standard-library imports: `sys` and `os` for path handling, `argparse` for CLI flags, `datetime` for timestamps and `Path` for file paths.
- **L30**: The same "make `backend` importable" trick as the other evaluation script, written with `os.path` instead of `pathlib`. `os.path.abspath(__file__)` is this file's full path. Each `dirname` goes up one level (to `evaluation/`, then `backend/`, then the project root), and that root is put first on `sys.path`.
- **L32–33**: Loads `.env` into environment variables *before* importing backend modules that need DB credentials.


#### Lines 35–44

````python
35  from backend.utils.postgres_client import get_client
36  from backend.tools.data_tools import ASSESSMENT_PROMPT_VERSION_LABEL
37  from backend.agents.assessment_agent import AGENT_NAME
38  from backend.evaluation.confusion_matrix_utils import (
39      confusion_counts,
40      format_confusion_matrix,
41      metrics_from_counts,
42      parse_yes_no,
43  )
44
````

- **L35**: `get_client()` returns the shared `PostgresClient`. Its raw `db_query` does *not* add the automatic prompt-version filter that the LLM tool version in `data_tools.py` adds, so human rows are visible.
- **L36**: `ASSESSMENT_PROMPT_VERSION_LABEL` is the short label of the active assessment prompt (e.g. `'v10'`), computed in `data_tools.py` from `prompt_config.py`.
- **L37**: `AGENT_NAME` comes from `assessment_agent.py`, which copies it from the active prompt module (`"assessment_agent"`). It is only used in report text and the output filename. Importing `assessment_agent` has side effects: it imports the agent's tool modules, loads the active prompt module through `loader.py`, and prints `✓ Loaded prompt version: ...`. That is why that line appears at the top of this script's output.
- **L38–43**: The shared confusion-matrix helpers.


#### Lines 45–54

````python
45  CRITERIA = [
46      "data_system_impact",
47      "operational_impact",
48      "financial_reputational_impact",
49      "systemic_risk",
50  ]
51
52  EVALUATIONS_DIR = Path(__file__).parent / "prompt_iterations" / "evaluations"
53
54
````

- **L45–50**: `CRITERIA` lists the four FRB sub-question column names. It is duplicated in `evaluate_assessment_prompts.py`.
- **L52**: The default report folder: `<this dir>/prompt_iterations/evaluations`. `Path` objects join with `/`.


#### Lines 55–62

````python
55  def fmt(m):
56      def pct(x):
57          return "n/a" if x != x else f"{x:.1%}"
58      return (f"n={m['n']}  TP={m['TP']} TN={m['TN']} FP={m['FP']} FN={m['FN']}  "
59              f"accuracy={pct(m['accuracy'])} precision={pct(m['precision'])} "
60              f"recall={pct(m['recall'])} specificity={pct(m['specificity'])} f1={pct(m['f1'])}")
61
62
````

**`fmt(m)`** formats a metrics dict as a single line.
- **L56–57**: A nested helper `pct` formats a fraction as a percentage (`:.1%` multiplies by 100 and adds `%`). `x != x` is the classic **NaN test**: NaN is the only float that isn't equal to itself, and such values print as "n/a".
- **L58–60**: Several adjacent f-strings inside parentheses are joined automatically into one string.
- Note: `fmt` is **never called** in this file. It is left over from before `format_confusion_matrix` was moved into `confusion_matrix_utils.py`.


#### Lines 63–74

````python
63  def main():
64      parser = argparse.ArgumentParser(description="Compare agent vs human reportability assessments")
65      parser.add_argument("--output", type=str, default=None,
66                          help="Path to write the report to (default: auto-generated under backend/evaluation/prompt_iterations/evalu⟦CUT⟧
67      args = parser.parse_args()
68
69      out = []  # accumulated report lines, printed and written to file
70
71      def emit(line=""):
72          print(line)
73          out.append(line)
74
````

**`main()`**, setup.
- **L64–66**: Sets up a CLI parser with an optional `--output` path. The end of the help string on L66 ran off the screen (⟦CUT⟧). It probably ended with `...evaluations/)")` to close the string and the call, but that is an inference.
- **L67**: Reads the command-line arguments.
- **L69–73**: `out` collects every report line. `emit` is a **nested function (closure)**: it can see `out` from the enclosing scope, so each call both prints a line and stores it for the file write at the end. `line=""` is a default, so `emit()` writes a blank line.


#### Lines 75–84

````python
75      client = get_client()
76      rows = client.db_query(
77          "reportability_assessments", None,
78          ["incident_number", "provided_by", "prompt_version", "updated_at", "reportability_flag"] + CRITERIA,
79      )
80
81      by_incident = {}
82      for r in rows:
83          by_incident.setdefault(r["incident_number"], []).append(r)
84
````

- **L75–79**: One query fetches *all* rows of `reportability_assessments` (filter `None`), with just the columns needed: identity and audit fields, the overall flag, and the four criteria. `[...] + CRITERIA` joins the two lists.
- **L81–83**: Groups rows by incident. `dict.setdefault(key, [])` returns the existing list for that key, or inserts and returns a new empty one, so `.append(r)` always works. The result looks like `{"INC...": [row, row, ...]}`.


#### Lines 85–93

````python
85      emit("=" * 90)
86      emit(f"Agent vs Human ground truth — per incident  (agent: {AGENT_NAME}, prompt_version: {ASSESSMENT_PROMPT_VERSION_LABEL})")
87      emit("=" * 90)
88
89      flag_pairs = []
90      criteria_pairs = {c: [] for c in CRITERIA}
91      compared_incidents = 0
92      skipped_incidents = 0
93
````

- **L85–87**: Prints the report banner, showing which agent and prompt version are being judged.
- **L89–92**: Sets up the accumulators:
  - `flag_pairs` for the overall-flag matrix.
  - `criteria_pairs`, a dict comprehension with one list per criterion.
  - Counters for compared and skipped incidents.


#### Lines 94–108

````python
 94      for incident_number in sorted(by_incident):
 95          incident_rows = by_incident[incident_number]
 96          # prompt_version IS NULL => human-provided (ground truth); only compare
 97          # against agent rows for the currently active prompt version, so the
 98          # report reflects exactly one agent version's performance.
 99          agent_rows = [r for r in incident_rows if r.get("prompt_version") == ASSESSMENT_PROMPT_VERSION_LABEL]
100          human_rows = [r for r in incident_rows if r.get("prompt_version") is None]
101          agent = max(agent_rows, key=lambda r: r["updated_at"]) if agent_rows else None
102          human = max(human_rows, key=lambda r: r["updated_at"]) if human_rows else None
103          if not agent or not human:
104              emit(f"{incident_number}: skipping (missing agent[{ASSESSMENT_PROMPT_VERSION_LABEL}] or human assessment)")
105              skipped_incidents += 1
106              continue
107          compared_incidents += 1
108
````

**Per-incident pairing.**
- **L94**: Goes through incidents in sorted order, so the report is stable between runs.
- **L96–98**: The comment explains the rule: NULL `prompt_version` means human, and only the active agent version is compared, so the report reflects one prompt version.
- **L99–100**: Splits the incident's rows into agent rows (exact label match) and human rows (`is None`).
- **L101–102**: `max(rows, key=lambda r: r["updated_at"])` picks the most recently updated row on each side. `... if rows else None` avoids `max()` raising on an empty list.
- **L103–106**: If either side is missing, it logs a skip line, increments the counter, and uses `continue` to jump to the next incident.
- **L107**: Otherwise the incident counts as compared.


#### Lines 109–125

````python
109          agent_flag = agent["reportability_flag"]
110          human_flag = human["reportability_flag"]
111          match = "✅" if agent_flag == human_flag else "❌"
112          emit(f"\n{incident_number} (human reviewer: {human.get('provided_by') or 'N/A'})")
113          emit(f"  reportability_flag: agent={agent_flag:<4} human={human_flag:<4} {match}")
114          flag_pairs.append((agent_flag, human_flag))
115
116          for c in CRITERIA:
117              a_val = parse_yes_no(agent.get(c))
118              h_val = parse_yes_no(human.get(c))
119              if a_val is None or h_val is None:
120                  emit(f"  {c}: agent={a_val or 'N/A':<4} human={h_val or 'N/A':<4} (skipped — missing label)")
121                  continue
122              c_match = "✅" if a_val == h_val else "❌"
123              emit(f"  {c}: agent={a_val:<4} human={h_val:<4} {c_match}")
124              criteria_pairs[c].append((a_val, h_val))
125
````

- **L109–111**: Compares the overall Yes/No flags and picks ✅ or ❌.
- **L112**: Prints the incident header with the human reviewer's name (`provided_by`, or `'N/A'` when it is empty, using `or` as a fallback).
- **L113**: `{agent_flag:<4}` left-aligns the value in a 4-character field so the columns line up. Note: if `reportability_flag` were ever NULL, `f"{None:<4}"` raises `TypeError`, because `None` doesn't accept a format spec. The criteria lines avoid this with `a_val or 'N/A'`.
- **L114**: Records `(predicted, actual)` = `(agent, human)` for the flag matrix.
- **L116–124**: For each criterion:
  - It parses the leading Yes/No from both rationales.
  - If either side has no clear label, it prints a "skipped" line and leaves the pair out of the matrix. That is why each criterion's `n` can be smaller than the incident count.
  - Otherwise it prints ✅ or ❌ and appends the pair.


#### Lines 126–142

````python
126      emit("\n" + "=" * 90)
127      emit("Confusion Matrix — overall reportability_flag")
128      emit("(rows = human ground truth, columns = agent prediction)")
129      emit("=" * 90)
130      emit(format_confusion_matrix("reportability_flag", metrics_from_counts(*confusion_counts(flag_pairs))))
131
132      emit("\n" + "=" * 90)
133      emit("Confusion Matrices — per FRB sub-criterion")
134      emit("(rows = human ground truth, columns = agent prediction)")
135      emit("=" * 90)
136      all_pairs = []
137      for c in CRITERIA:
138          pairs = criteria_pairs[c]
139          all_pairs.extend(pairs)
140          emit(format_confusion_matrix(c, metrics_from_counts(*confusion_counts(pairs))))
141          emit()
142
````

**Confusion matrices.**
- **L126–130**: Prints the overall-flag matrix. The pipeline reads inside out: `confusion_counts(pairs)` gives `(tp, tn, fp, fn)`, `*` unpacks that into `metrics_from_counts`, and `format_confusion_matrix` renders the table and metric line. The note "rows = human, columns = agent" tells the reader how to read the table.
- **L132–141**: Prints one matrix per FRB criterion. While doing so it gathers every criterion pair into `all_pairs` (`list.extend` adds all elements of another list) for the combined matrix.


#### Lines 143–155

````python
143      emit("=" * 90)
144      emit("Confusion Matrix — all 4 sub-criteria combined")
145      emit("=" * 90)
146      emit(format_confusion_matrix("all 4 sub-criteria", metrics_from_counts(*confusion_counts(all_pairs))))
147
148      emit(f"\nCompared {compared_incidents} incidents ({skipped_incidents} skipped — missing agent "
149           f"and/or human assessment). Per-criterion n varies because some incidents only have "
150           f"an overall reportability_flag on record, without the 4-question breakdown.")
151      if compared_incidents < 10:
152          emit("Note: sample size is still small, so these metrics are directional only, not")
153          emit("statistically robust. Useful for spotting patterns, not for concluding agent")
154          emit("quality with confidence.")
155
````

- **L143–146**: Prints the "all 4 sub-criteria combined" matrix, which pools every criterion decision into one 2×2 table for an overall view of criterion-level accuracy.
- **L148–150**: Prints a summary line with the compared and skipped counts. It explains that per-criterion `n` varies because some human rows only recorded the overall flag, without the 4-question breakdown.
- **L151–154**: With fewer than 10 incidents, it adds a caveat that the numbers are directional only, not statistically meaningful.


#### Lines 156–170

````python
156      if args.output:
157          output_path = Path(args.output)
158      else:
159          EVALUATIONS_DIR.mkdir(parents=True, exist_ok=True)
160          timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
161          output_path = EVALUATIONS_DIR / f"{AGENT_NAME}_{ASSESSMENT_PROMPT_VERSION_LABEL}_{timestamp}.txt"
162
163      output_path.parent.mkdir(parents=True, exist_ok=True)
164      output_path.write_text("\n".join(out) + "\n")
165      print(f"\nReport written to: {output_path}")
166
167
168  if __name__ == "__main__":
169      main()
170
````

**Writing the report file.**
- **L156–157**: If `--output` was given, it uses that path.
- **L158–161**: Otherwise it creates the default `evaluations/` folder and builds a timestamped filename such as `assessment_agent_v10_20261029_143000.txt`.
- **L163**: Makes sure the parent folder exists. This matters for a custom `--output` path.
- **L164**: `Path.write_text` writes the whole report in one call. The lines are joined with newlines, with a trailing newline at the end.
- **L165**: Tells the user where the file went. This is a plain `print`, so it isn't part of the saved report.
- **L168–169**: The standard "run `main()` only when executed as a script" guard.


---

## 45. `backend/evaluation/evaluate_assessment_prompts.py`

*467 lines · complete*

This is the **main evaluation harness for the Assessment Agent**. It measures how well the currently active assessment prompt (set in `prompt_iterations/prompt_config.py`) agrees with **human ground truth**, meaning human-written rows in the `reportability_assessments` table that 2LOD has signed off (`final_approval_by_lod == 'Yes'`).

It has two front doors:
- **CLI**: `python backend/evaluation/evaluate_assessment_prompts.py [--no-run] [--incidents ...]`. It prints a report and saves a `.txt` file under `prompt_iterations/evaluations/`.
- **API**: `backend/services/assessment_service.execute_assessment_evaluation` loads this module lazily (through `load_evaluator`) and calls `evaluate()` and then `build_summary()` to produce the JSON for `/api/evaluate/assessment`.

`backend/services/intake_service.get_latest_assessment` also reuses `CRITERIA`, `compare_assessments` and `get_latest_human_assessment` to show agent-vs-human matches in the UI. For the confusion-matrix math it calls into `confusion_matrix_utils.py`. When asked to re-run the agent, it calls the production `run_single_assessment`.


#### Lines 1–23

````python
 1  """
 2  Prompt Evaluation Script
 3
 4  Compares assessment agent output against human ground truth stored in the
 5  reportability_assessments table. Rows with prompt_version IS NULL are
 6  human-provided; of those, only rows where final_approval_by_lod == 'Yes'
 7  (2LOD has signed off on the assessment) count as validated ground truth.
 8  Rows with a non-NULL prompt_version are agent-generated.
 9
10  To switch which prompt is evaluated, edit backend/evaluation/prompt_iterations/prompt_config.py
11  (PROMPT_VERSION) before running -- the assessment agent loads its SYSTEM_PROMPT
12  from that versioned module at import time.
13
14  Usage:
15      # Run agent + compare against ground truth for all incidents with human assessments
16      python backend/evaluation/evaluate_assessment_prompts.py
17
18      # Only compare existing agent assessments already in the DB (no new LLM calls)
19      python backend/evaluation/evaluate_assessment_prompts.py --no-run
20
21      # Test specific incidents
22      python backend/evaluation/evaluate_assessment_prompts.py --incidents INC0600745,INC0633882
23  """
````

**Module docstring.** A triple-quoted string at the very top of a file is the module's documentation. Python stores it in `__doc__`, and it has no runtime effect.
- **L2–8**: The data model this script relies on. One table, `reportability_assessments`, holds both kinds of row. `prompt_version IS NULL` means a human wrote the row. A non-NULL value (e.g. `'v10'`) means the agent wrote it under that prompt version. Only human rows with `final_approval_by_lod == 'Yes'` count as validated ground truth.
- **L10–12**: To evaluate a different prompt, you change `prompt_config.py`. The agent picks its `SYSTEM_PROMPT` at *import time*, so you choose the version before you run the script. Note: the docstring calls the constant `PROMPT_VERSION`, but in `prompt_config.py` it is actually named `ASSESSMENT_AGENT_PROMPT_VERSION`. This file only aliases it to `PROMPT_VERSION` on L38.
- **L14–22**: The three ways to run it:
  - The default re-runs the agent (this costs LLM calls and writes new rows).
  - `--no-run` only compares rows that already exist.
  - `--incidents` limits the run to specific INC numbers.


#### Lines 24–35

````python
24
25  import sys
26  import argparse
27  from datetime import datetime
28  from pathlib import Path
29
30  # Add project root to path so the `backend` package is importable
31  sys.path.insert(0, str(Path(__file__).parent.parent.parent))
32
33  from dotenv import load_dotenv
34  load_dotenv()
35
````

- **L25–28**: Standard-library imports:
  - `sys` is used to edit the import path.
  - `argparse` parses CLI flags.
  - `datetime` makes timestamps for report filenames.
  - `pathlib.Path` is an object-oriented file path type that supports the `/` operator for joining paths.
- **L30–31**: `Path(__file__)` is this file (`backend/evaluation/evaluate_assessment_prompts.py`). Going up three `.parent`s reaches the **project root**, the folder that *contains* `backend/`. `sys.path.insert(0, ...)` puts that folder at the front of Python's module search path, so `import backend....` works when you run the file directly as a script. Running it that way would otherwise only put `backend/evaluation/` on the path.
- **L33–34**: `load_dotenv()` (from the `python-dotenv` package) reads a `.env` file and copies its keys into `os.environ`. It has to run *before* the backend imports below, because those modules read env vars such as DB credentials and LLM keys when they are imported.


#### Lines 36–45

````python
36  from backend.utils.postgres_client import get_client
37  from backend.tools.data_tools import _short_prompt_version
38  from backend.evaluation.prompt_iterations.prompt_config import ASSESSMENT_AGENT_PROMPT_VERSION as PROMPT_VERSION
39  from backend.evaluation.confusion_matrix_utils import (
40      confusion_counts,
41      format_confusion_matrix,
42      metrics_from_counts,
43      metrics_to_json,
44      parse_yes_no,
45  )
````

- **L36**: `get_client()` returns the shared `PostgresClient` singleton from `backend/utils/postgres_client.py`. This script calls that client's raw `db_query` directly. It does *not* use the `@tool`-decorated `db_query` in `data_tools.py`, which silently adds `prompt_version = <active label>` to reads of `reportability_assessments`. Using the raw client lets the script see human rows (NULL version) too.
- **L37**: `_short_prompt_version` (in `data_tools.py`) uses the regex `(v\d+)$` to turn `'assessment_agent_prompt_v10'` into `'v10'`. That short form is the label stored in the DB's `prompt_version` column.
- **L38**: Imports the active assessment prompt module name, aliased with `as` to the shorter local name `PROMPT_VERSION`.
- **L39–45**: The shared confusion-matrix helpers (see `confusion_matrix_utils.py`). The parentheses let the import list span several lines.


#### Lines 46–54

````python
46
47  # DB rows store the short label (e.g. 'v5'), not the full module name
48  # ('assessment_agent_prompt_v5') - this is what get_latest_agent_assessment
49  # filters on, so evaluation only ever compares against the prompt version
50  # currently active in prompt_config.py, never a stale v3/v4/etc. row.
51  PROMPT_VERSION_LABEL = _short_prompt_version(PROMPT_VERSION)
52
53  CRITERIA = ['data_system_impact', 'operational_impact', 'financial_reputational_impact', 'systemic_risk']
54
````

- **L47–50**: Explains *why* the label is needed. DB rows store `'v5'`-style labels, not module names, and evaluation must only ever compare against rows from the *currently active* prompt, never a stale `v3` or `v4` row.
- **L51**: `PROMPT_VERSION_LABEL` is computed once, at import time, as a module-level constant (e.g. `'v10'`).
- **L53**: `CRITERIA` lists the four FRB (Federal Reserve Board) sub-questions the agent answers. Each one is a column in `reportability_assessments` holding free text that starts with "Yes" or "No". The rest of the module loops over this list.


#### Lines 55–78

````python
55
56  def get_incidents_with_human_assessments(incident_numbers=None):
57      """Get incident numbers that have at least one human-provided assessment
58      (prompt_version IS NULL) that 2LOD has given final approval to
59      (final_approval_by_lod == 'Yes'). Per Sep 24 meeting notes: only
60      2LOD-approved human assessments count as validated ground truth for
61      evaluation - an unapproved/pending human row isn't a reliable target for
62      the agent to be compared against."""
63      client = get_client()
64
65      all_assessments = client.db_query(
66          'reportability_assessments', None,
67          ['incident_number', 'prompt_version', 'final_approval_by_lod'],
68      )
69      human_incidents = {
70          a['incident_number'] for a in all_assessments
71          if a.get('prompt_version') is None and a.get('final_approval_by_lod') == 'Yes'
72      }
73
74      if incident_numbers:
75          human_incidents = human_incidents & set(incident_numbers)
76
77      return sorted(human_incidents)
78
````

**`get_incidents_with_human_assessments`** finds which incidents can be evaluated at all.
- **L56**: `incident_numbers=None` is a default argument. Leaving it out means "all incidents".
- **L57–62**: The docstring records a business rule from the Sep 24 meeting: only 2LOD-approved human rows are trusted as ground truth.
- **L65–68**: Selects three columns from *every* row of the table. `None` as the filter means no WHERE clause.
- **L69–72**: A **set comprehension** (`{expr for x in xs if cond}`). It builds a set of incident numbers that have at least one human row (`prompt_version is None`) with 2LOD approval. `.get()` returns `None` instead of raising if the key is missing. Using a set removes duplicates automatically.
- **L74–75**: If the caller passed specific incidents, `&` (set intersection) keeps only those that also have ground truth.
- **L77**: Returns a sorted list, so the processing order is deterministic.


#### Lines 79–93

````python
79
80  def get_latest_human_assessment(incident_number):
81      """Get the most recent 2LOD-approved human-provided assessment
82      (prompt_version IS NULL AND final_approval_by_lod == 'Yes') for an
83      incident."""
84      client = get_client()
85      assessments = client.db_query('reportability_assessments', {'incident_number': incident_number}, ['*'])
86      human = [
87          a for a in assessments
88          if a.get('prompt_version') is None and a.get('final_approval_by_lod') == 'Yes'
89      ]
90      if not human:
91          return None
92      return sorted(human, key=lambda a: a['updated_at'], reverse=True)[0]
93
````

**`get_latest_human_assessment`** returns the newest 2LOD-approved human row for one incident.
- **L85**: Queries that incident's rows with all columns (`['*']`).
- **L86–89**: A **list comprehension** that keeps only human rows with 2LOD approval.
- **L90–91**: Returns `None` if there are none. The callers treat `None` as "skip".
- **L92**: `sorted(..., key=lambda a: a['updated_at'], reverse=True)[0]` sorts newest-first by `updated_at` and takes the first. A `lambda` is a small anonymous function, used here to tell `sorted` which field to compare on.

This function is also imported by `intake_service.get_latest_assessment` to power the UI's "agent vs human" comparison.


#### Lines 94–114

````python
 94
 95  def get_latest_agent_assessment(incident_number):
 96      """Get the most recent agent-generated assessment for the ACTIVE prompt
 97      version (prompt_version == PROMPT_VERSION_LABEL, e.g. 'v5') for an
 98      incident.
 99
100      Deliberately filters to PROMPT_VERSION_LABEL rather than "any
101      agent-generated row" - an incident can accumulate rows from several
102      prompt versions over time (v3, v4, v5, ...), and picking whichever has
103      the most recent updated_at regardless of version could silently compare
104      against a stale version instead of the one set in prompt_config.py.
105      """
106      client = get_client()
107      assessments = client.db_query(
108          'reportability_assessments',
109          {'incident_number': incident_number, 'prompt_version': PROMPT_VERSION_LABEL},
110      )
111      if not assessments:
112          return None
113      return sorted(assessments, key=lambda a: a['updated_at'], reverse=True)[0]
114
````

**`get_latest_agent_assessment`** returns the newest agent row *for the active prompt version only*.
- **L96–105**: The docstring explains the design choice. One incident can collect rows from many prompt versions over time. Picking "most recent of any version" could quietly evaluate the wrong prompt.
- **L107–110**: The filter dict `{'incident_number': ..., 'prompt_version': PROMPT_VERSION_LABEL}` becomes a parameterised SQL `WHERE incident_number = %s AND prompt_version = %s` inside `PostgresClient.db_query`. No columns are given, so it selects `*`.
- **L111–113**: Same "None if empty, else newest by `updated_at`" pattern as above.


#### Lines 115–127

````python
115
116  def run_agent_assessment(incident_number):
117      """Run production assessment orchestration and return its persisted row."""
118      from backend.services.assessment_service import run_single_assessment
119
120      try:
121          run_single_assessment(incident_number)
122          return get_latest_agent_assessment(incident_number), {}
123      except Exception as e:
124          print(f"  ❌ Error assessing {incident_number}: {e}")
125          return None, {}
126
127
````

**`run_agent_assessment`** runs the real production pipeline for one incident.
- **L118**: A **lazy import** inside the function. `assessment_service` pulls in FastAPI, the LLM stack and so on, so it is only loaded when someone actually wants to run the agent. This also keeps `--no-run` and the `intake_service` reuse lightweight.
- **L120–122**: `run_single_assessment` (in `assessment_service.py`) gathers context, makes the LLM call and upserts a row. It also applies the rule that non-US/Global incidents are auto-flagged not reportable. Its return value is ignored. Instead the persisted row is re-read with `get_latest_agent_assessment`, so the evaluation uses exactly what is in the DB. The function returns a **tuple** `(row, {})`, where the second element is meant to be the agent's per-criterion confidence.
- **L123–125**: `try/except Exception` catches any failure (for example an `HTTPException` 404 or an LLM error), prints it, and returns `(None, {})`, so one bad incident doesn't abort the whole evaluation.
- Note: the confidence dict is **always empty** here. `data_tools.db_write` strips `confidence_*` keys before saving (they have no DB column), and the return value of `run_single_assessment` is thrown away. So the "accuracy by confidence tier" sections further down will always say confidence is unavailable, even in run-agent mode, and the message telling you to "re-run without --no-run" won't help.


#### Lines 128–148

````python
128  def compare_assessments(agent_assessment, human_assessment):
129      """Compare agent vs human assessment and return match metrics."""
130      if not agent_assessment or not human_assessment:
131          return None
132
133      flag_match = agent_assessment['reportability_flag'] == human_assessment['reportability_flag']
134
135      criteria_matches = {}
136      for criterion in CRITERIA:
137          agent_yes = str(agent_assessment.get(criterion, '')).strip().lower().startswith('yes')
138          human_yes = str(human_assessment.get(criterion, '')).strip().lower().startswith('yes')
139          criteria_matches[criterion] = agent_yes == human_yes
140
141      return {
142          'flag_match': flag_match,
143          'criteria_matches': criteria_matches,
144          'agent_flag': agent_assessment['reportability_flag'],
145          'human_flag': human_assessment['reportability_flag'],
146      }
147
148
````

**`compare_assessments`** compares one agent row with one human row.
- **L130–131**: If either side is missing, it returns `None`, meaning "can't compare".
- **L133**: `flag_match` is `True` when the overall `reportability_flag` ("Yes"/"No") is the same on both sides.
- **L135–139**: For each criterion, it converts the free-text rationale to a boolean: does the text start with "yes" once whitespace is stripped and it is lowercased? It then records whether the agent and human booleans agree.
  - `str(... .get(criterion, ''))` guards against a missing key.
  - Note: if the value is `None`, `str(None)` is `'None'`, which counts as "not yes". So a blank criterion is treated as "No", and two blanks count as a *match*. The confusion matrices instead use `parse_yes_no`, which *skips* blanks. That means per-criterion "accuracy" here and the confusion-matrix accuracy can disagree on the same data.
- **L141–146**: Returns a plain dict with the flag match, the per-criterion matches and both raw flags. Later code reads these keys, and so does the UI (through `intake_service`).


#### Lines 149–167

````python
149  def evaluate(incident_numbers=None, run_agent=True):
150      """Evaluate the currently active prompt version against human ground truth."""
151      print(f"\n{'='*80}")
152      print(f"Evaluating Prompt Version: {PROMPT_VERSION}")
153      print(f"Mode: {'RUN AGENT + COMPARE' if run_agent else 'COMPARE EXISTING ONLY (no new LLM calls)'}")
154      print(f"{'='*80}\n")
155
156      incidents = get_incidents_with_human_assessments(incident_numbers)
157      print(f"Found {len(incidents)} incidents with human assessments\n")
158
159      results = []
160      for i, inc_num in enumerate(incidents, 1):
161          print(f"[{i}/{len(incidents)}] {inc_num}...")
162
163          human = get_latest_human_assessment(inc_num)
164          if not human:
165              print(f"  ⚠️ No human assessment found, skipping")
166              continue
167
````

**`evaluate`**, part 1: the main loop's setup. This is the function the API service calls.
- **L149**: `run_agent=True` is the CLI default. Note that the API (`execute_assessment_evaluation`) defaults to `run_agent=False`.
- **L151–154**: Prints a banner. `{'='*80}` repeats `=` 80 times. The conditional expression `A if cond else B` picks the mode text.
- **L156–157**: Gets the list of evaluable incidents (those with ground truth), optionally filtered.
- **L159–161**: `results` collects one dict per successfully compared incident. `enumerate(incidents, 1)` yields `(index, item)` pairs starting at 1, which is used for the `[i/N]` progress display.
- **L163–166**: Loads the ground-truth row and skips the incident if there isn't one. This rarely triggers, because the incident list was already filtered on the same condition.


#### Lines 168–199

````python
168          confidence = {}
169          if run_agent:
170              agent_result, confidence = run_agent_assessment(inc_num)
171          else:
172              agent_result = get_latest_agent_assessment(inc_num)
173              if not agent_result:
174                  print(f"  ⚠️ No existing agent assessment found (run without --no-run first)")
175                  continue
176
177          if not agent_result:
178              print(f"  ❌ Assessment failed")
179              continue
180
181          comparison = compare_assessments(agent_result, human)
182          if not comparison:
183              print(f"  ⚠️ Could not compare results")
184              continue
185
186          results.append({
187              'incident_number': inc_num,
188              'comparison': comparison,
189              'agent': agent_result,
190              'human': human,
191              'confidence': confidence,
192          })
193
194          match_icon = "✅" if comparison['flag_match'] else "❌"
195          print(f"  {match_icon} Agent: {comparison['agent_flag']}, Human: {comparison['human_flag']}")
196
197      _report_results(results)
198      return results
199
````

**`evaluate`**, part 2: getting the agent's answer and comparing.
- **L168**: `confidence` defaults to an empty dict.
- **L169–170**: In run mode, it calls the agent and unpacks the tuple `(row, confidence)` into two variables.
- **L171–175**: In `--no-run` mode, it uses whatever agent row for the active version already exists, and skips the incident (with a hint) if there is none.
- **L177–179**: Covers the case where the agent run failed (`None`).
- **L181–184**: Runs the comparison and skips the incident if it couldn't be done.
- **L186–192**: Appends a record with everything later reporting needs: the comparison, both full rows and the confidence.
- **L194–195**: Prints a ✅ or ❌ line per incident.
- **L197–198**: Prints and saves the text report (`_report_results`), then returns the raw `results` list. The API path passes that list on to `build_summary`.


#### Lines 200–228

````python
200
201  def _confusion_matrices(results):
202      """Compute Yes/No confusion matrices from evaluate()'s results list:
203      one for the overall reportability_flag, one per FRB criterion, and one
204      combining all 4 criteria together. Predicted = agent, Actual = human
205      (matches compare_agent_vs_human.py's convention).
206
207      Returns a dict keyed by matrix name -> a metrics_from_counts() dict
208      (n/TP/TN/FP/FN/accuracy/precision/recall/specificity/f1).
209      """
210      flag_pairs = [(r['comparison']['agent_flag'], r['comparison']['human_flag']) for r in results]
211
212      criteria_pairs = {criterion: [] for criterion in CRITERIA}
213      for r in results:
214          for criterion in CRITERIA:
215              agent_val = parse_yes_no(r['agent'].get(criterion))
216              human_val = parse_yes_no(r['human'].get(criterion))
217              if agent_val is not None and human_val is not None:
218                  criteria_pairs[criterion].append((agent_val, human_val))
219
220      all_criteria_pairs = [pair for pairs in criteria_pairs.values() for pair in pairs]
221
222      matrices = {"reportability_flag": metrics_from_counts(*confusion_counts(flag_pairs))}
223      for criterion in CRITERIA:
224          matrices[criterion] = metrics_from_counts(*confusion_counts(criteria_pairs[criterion]))
225      matrices["all_criteria_combined"] = metrics_from_counts(*confusion_counts(all_criteria_pairs))
226      return matrices
227
228
````

**`_confusion_matrices`** builds every confusion matrix from `results`. The leading underscore marks it as module-private by convention.
- **L202–209**: The convention used is *predicted = agent, actual = human*, with "Yes" as the positive class. It returns a dict mapping a matrix name to a metrics dict.
- **L210**: A list comprehension of `(agent_flag, human_flag)` pairs, one per incident.
- **L212**: A **dict comprehension** that starts an empty list per criterion.
- **L213–218**: For each incident and criterion, `parse_yes_no` pulls the leading Yes/No from each rationale. The pair is kept only when *both* sides have a label, so missing labels are excluded rather than guessed.
- **L220**: A nested comprehension that flattens all criterion pairs into one list, for the "all 4 combined" matrix.
- **L222–225**: `confusion_counts(...)` returns a 4-tuple `(tp, tn, fp, fn)`. The `*` in `metrics_from_counts(*...)` **unpacks** that tuple into four positional arguments. The function produces one matrix for the flag, one per criterion and one combined.


#### Lines 229–254

````python
229  def _confusion_matrix_report_lines(results):
230      """Render all confusion matrices (overall flag, per-criterion, combined)
231      as text lines for the CLI/report output."""
232      matrices = _confusion_matrices(results)
233      lines = []
234      lines.append(f"\n{'='*80}")
235      lines.append("Confusion Matrix - overall reportability_flag")
236      lines.append("(rows = human ground truth, columns = agent prediction)")
237      lines.append(f"{'='*80}")
238      lines.append(format_confusion_matrix("reportability_flag", matrices["reportability_flag"]))
239
240      lines.append(f"\n{'='*80}")
241      lines.append("Confusion Matrices - per FRB sub-criterion")
242      lines.append("(rows = human ground truth, columns = agent prediction)")
243      lines.append(f"{'='*80}")
244      for criterion in CRITERIA:
245          lines.append(format_confusion_matrix(criterion, matrices[criterion]))
246          lines.append("")
247
248      lines.append(f"{'='*80}")
249      lines.append("Confusion Matrix - all 4 sub-criteria combined")
250      lines.append(f"{'='*80}")
251      lines.append(format_confusion_matrix("all 4 sub-criteria", matrices["all_criteria_combined"]))
252      return lines
253
254
````

**`_confusion_matrix_report_lines`** formats all the matrices as text lines for the CLI and the saved report.
- **L232**: Computes the matrices once.
- **L234–238**: Adds a section header, then the overall-flag matrix rendered by `format_confusion_matrix`.
- **L240–246**: Adds a section for each FRB criterion, with a blank line between matrices.
- **L248–251**: Adds the combined matrix.
- **L252**: Returns the list. The caller both prints it and writes it to file.


#### Lines 255–280

````python
255  def build_summary(results):
256      """Build a JSON-serializable aggregate + full per-incident summary, for
257      the /api/evaluate/assessment endpoint (see backend/routers/assessment.py
258      and backend/services/assessment_service.execute_assessment_evaluation).
259
260      Unlike _report_results (which prints/writes a text report), this returns
261      every per-incident agent-vs-human detail (not just mismatches), so API
262      consumers get the same visibility as the CLI's saved report file.
263      """
264      if not results:
265          return {
266              "prompt_version": PROMPT_VERSION,
267              "incidents_evaluated": 0,
268              "flag_matches": 0,
269              "flag_accuracy_pct": 0.0,
270              "criterion_accuracy": [],
271              "confidence_breakdown": {criterion: [] for criterion in CRITERIA},
272              "confusion_matrices": {},
273              "mismatched_incidents": [],
274              "results": [],
275              "message": (
276                  "No incidents with a 2LOD-approved human assessment "
277                  "(final_approval_by_lod = 'Yes') were found for the given filters."
278              ),
279          }
280
````

**`build_summary`** produces the JSON-friendly summary for the API. Its shape matches the Pydantic model `AssessmentEvaluationResponse` in `schemas.py`.
- **L256–263**: Unlike the text report, this includes *every* incident's details, not only mismatches, so the Admin UI can show everything.
- **L264–279**: If there were no results, it returns a fully populated "empty" response with an explanatory `message`. That way the endpoint still returns a valid object instead of crashing on a divide-by-zero.
- Note (L266, and L334 below): `prompt_version` is set to the **full module name** (e.g. `'assessment_agent_prompt_v10'`). However, `assessment_service.get_latest_assessment_evaluation` only returns the cached summary when `summary["prompt_version"] == ASSESSMENT_PROMPT_VERSION_LABEL`, which is the *short* label `'v10'`. As written, the two never match, so the cached "latest evaluation" for the Admin page would always be discarded.


#### Lines 281–310

````python
281      total = len(results)
282      flag_matches = sum(1 for r in results if r['comparison']['flag_match'])
283
284      criterion_accuracy = []
285      for criterion in CRITERIA:
286          matches = sum(1 for r in results if r['comparison']['criteria_matches'][criterion])
287          criterion_accuracy.append({
288              "criterion": criterion,
289              "matches": matches,
290              "total": total,
291              "accuracy_pct": matches / total * 100,
292          })
293
294      has_confidence = any(r.get('confidence') for r in results)
295      confidence_breakdown = {criterion: [] for criterion in CRITERIA}
296      if has_confidence:
297          for criterion in CRITERIA:
298              tiers = []
299              for tier in ["High", "Medium", "Low"]:
300                  tier_results = [r for r in results if r.get('confidence', {}).get(criterion) == tier]
301                  n = len(tier_results)
302                  correct = sum(1 for r in tier_results if r['comparison']['criteria_matches'][criterion])
303                  tiers.append({
304                      "tier": tier,
305                      "n": n,
306                      "correct": correct,
307                      "accuracy_pct": (correct / n * 100) if n else None,
308                  })
309              confidence_breakdown[criterion] = tiers
310
````

- **L281–282**: `total` is the number of incidents compared. `sum(1 for r in ... if ...)` is a **generator expression**, a concise way to count items matching a condition.
- **L284–292**: For each criterion, it counts matches (from `compare_assessments`'s boolean view) and builds a dict like `{"criterion", "matches", "total", "accuracy_pct"}`, which maps onto `CriterionAccuracy` in `schemas.py`.
- **L294**: `any(...)` checks whether *any* result carries confidence data. As noted under L115–127, it never does.
- **L295–309**: If confidence exists, then for each criterion and each tier (High/Medium/Low) it counts how many incidents the agent put in that tier and how many of those it got right. `accuracy_pct` is `None` when the tier is empty. The aim is to check whether the agent's confidence is well-calibrated, i.e. whether "High" really means more accurate.


#### Lines 311–346

````python
311      per_incident = []
312      for r in results:
313          criteria_detail = {}
314          for criterion in CRITERIA:
315              criteria_detail[criterion] = {
316                  "match": r['comparison']['criteria_matches'][criterion],
317                  "agent_value": str(r['agent'].get(criterion) or ""),
318                  "human_value": str(r['human'].get(criterion) or ""),
319                  "confidence": r.get('confidence', {}).get(criterion),
320              }
321          per_incident.append({
322              "incident_number": r['incident_number'],
323              "agent_flag": r['comparison']['agent_flag'],
324              "human_flag": r['comparison']['human_flag'],
325              "flag_match": r['comparison']['flag_match'],
326              "criteria": criteria_detail,
327          })
328
329      confusion_matrices = {
330          name: metrics_to_json(m) for name, m in _confusion_matrices(results).items()
331      }
332
333      return {
334          "prompt_version": PROMPT_VERSION,
335          "incidents_evaluated": total,
336          "flag_matches": flag_matches,
337          "flag_accuracy_pct": flag_matches / total * 100,
338          "criterion_accuracy": criterion_accuracy,
339          "confidence_breakdown": confidence_breakdown,
340          "confusion_matrices": confusion_matrices,
341          "mismatched_incidents": [r['incident_number'] for r in results if not r['comparison']['flag_match']],
342          "results": per_incident,
343          "message": None,
344      }
345
346
````

- **L311–327**: Builds the per-incident detail list. For each incident there is a `criteria_detail` dict with `match`, the agent's and human's full text, and confidence. `str(x or "")` turns `None` into `""` so the Pydantic `str` fields validate. The outer dict adds the flags and `flag_match`, and maps onto `AssessmentEvaluationItem`.
- **L329–331**: A dict comprehension that converts every confusion matrix to JSON form with `metrics_to_json`, which turns NaN into `None` and uses percentage keys. JSON has no NaN, so this conversion is required.
- **L333–344**: The final response dict:
  - `flag_accuracy_pct` is the overall match rate.
  - `mismatched_incidents` lists only the INC numbers whose overall flag disagreed.
  - `message: None` because there is nothing to explain.

  `execute_assessment_evaluation` saves this dict to the JSON cache and passes it as `AssessmentEvaluationResponse(**summary)`. The `**` unpacks the dict into keyword arguments.


#### Lines 347–372

````python
347  def _confidence_breakdown_lines(results):
348      """Build report lines showing accuracy broken down by confidence tier
349      (High/Medium/Low) for each FRB criterion. Confidence is only available
350      when the agent was actually invoked (not with --no-run), since it's not
351      persisted to the database.
352      """
353      has_confidence = any(r.get('confidence') for r in results)
354      if not has_confidence:
355          return ["(confidence breakdown unavailable - re-run without --no-run to capture confidence)"]
356
357      lines = []
358      for criterion in CRITERIA:
359          lines.append(f"\n{criterion}:")
360          lines.append(f"{'':>10}{'n':>6}{'correct':>10}{'accuracy':>10}")
361          for tier in ["High", "Medium", "Low"]:
362              tier_results = [r for r in results if r.get('confidence', {}).get(criterion) == tier]
363              n = len(tier_results)
364              if n == 0:
365                  lines.append(f"{tier:>10}{0:>6}{0:>10}{'n/a':>10}")
366                  continue
367              correct = sum(1 for r in tier_results if r['comparison']['criteria_matches'][criterion])
368              accuracy = correct / n * 100
369              lines.append(f"{tier:>10}{n:>6}{correct:>10}{f'{accuracy:.1f}%':>10}")
370      return lines
371
372
````

**`_confidence_breakdown_lines`** is the text-report version of the confidence-tier table built in `build_summary`.
- **L353–355**: If no confidence was captured, it returns a one-line explanation.
- **L357–369**: For each criterion, it prints a small table. The f-string format specs like `{'':>10}` and `{n:>6}` right-align values in fixed-width columns. Empty tiers show `n/a`. `f'{accuracy:.1f}%'` formats one decimal place and is nested inside the outer f-string so the whole cell can be right-aligned.


#### Lines 373–396

````python
373  def _report_results(results):
374      if not results:
375          print("\nNo results to report.")
376          return
377
378      print(f"\n{'='*80}")
379      print(f"Results Summary")
380      print(f"{'='*80}\n")
381
382      total = len(results)
383      flag_matches = sum(1 for r in results if r['comparison']['flag_match'])
384      flag_accuracy = flag_matches / total * 100
385
386      print(f"Overall Reportability Flag Accuracy: {flag_matches}/{total} ({flag_accuracy:.1f}%)\n")
387
388      print("Per-Criterion Accuracy:")
389      criterion_accuracy = {}
390      for criterion in CRITERIA:
391          matches = sum(1 for r in results if r['comparison']['criteria_matches'][criterion])
392          accuracy = matches / total * 100
393          criterion_accuracy[criterion] = accuracy
394          print(f"  {criterion.replace('_', ' ').title()}: {matches}/{total} ({accuracy:.1f}%)")
395      print()
396
````

**`_report_results`** prints the CLI report and saves it to disk.
- **L374–376**: With nothing to report, it prints a message and exits early.
- **L378–380**: Prints a header. Note that `f"Results Summary"` is an f-string with no placeholders. It is harmless, but the `f` is unnecessary.
- **L382–386**: Computes and prints overall flag accuracy. `:.1f` means one decimal place.
- **L388–395**: Computes per-criterion accuracy (the boolean-match view again), keeps it in `criterion_accuracy` for the file write later, and prints it. `.replace('_', ' ').title()` turns `data_system_impact` into "Data System Impact" for display.


#### Lines 397–413

````python
397      confidence_lines = _confidence_breakdown_lines(results)
398      print("Accuracy by Confidence Tier (per criterion):")
399      for line in confidence_lines:
400          print(line)
401      print()
402
403      confusion_lines = _confusion_matrix_report_lines(results)
404      for line in confusion_lines:
405          print(line)
406      print()
407
408      mismatches = [r for r in results if not r['comparison']['flag_match']]
409      if mismatches:
410          print(f"Mismatches ({len(mismatches)}):")
411          for r in mismatches:
412              print(f"  {r['incident_number']}: Agent={r['comparison']['agent_flag']}, Human={r['comparison']['human_flag']}")
413
````

- **L397–401**: Builds the confidence-tier lines once and prints them. The same list is reused for the file below.
- **L403–406**: Builds and prints the confusion-matrix lines.
- **L408–412**: `mismatches` lists the incidents where the overall flag disagreed, and each one is printed as Agent vs Human. These are the cases a prompt engineer would study before writing the next prompt version.


#### Lines 414–452

````python
414      # Save report
415      output_dir = Path(__file__).parent / 'prompt_iterations' / 'evaluations'
416      output_dir.mkdir(parents=True, exist_ok=True)
417      timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
418      output_file = output_dir / f'eval_{PROMPT_VERSION}_{timestamp}.txt'
419
420      with open(output_file, 'w') as f:
421          f.write(f"Prompt Evaluation: {PROMPT_VERSION}\n")
422          f.write(f"Timestamp: {datetime.now()}\n")
423          f.write(f"Incidents tested: {total}\n\n")
424          f.write(f"Overall Flag Accuracy: {flag_matches}/{total} ({flag_accuracy:.1f}%)\n\n")
425          f.write("Per-Criterion Accuracy:\n")
426          for criterion, accuracy in criterion_accuracy.items():
427              f.write(f"  {criterion}: {accuracy:.1f}%\n")
428          f.write("\n")
429
430          f.write("Accuracy by Confidence Tier (per criterion):\n")
431          for line in confidence_lines:
432              f.write(f"{line}\n")
433          f.write("\n")
434
435          for line in confusion_lines:
436              f.write(f"{line}\n")
437          f.write("\n")
438
439          if mismatches:
440              f.write("Mismatches:\n")
441              for r in mismatches:
442                  f.write(f"\n{r['incident_number']}:\n")
443                  f.write(f"  Agent: {r['comparison']['agent_flag']}\n")
444                  f.write(f"  Human: {r['comparison']['human_flag']}\n")
445                  for criterion, match in r['comparison']['criteria_matches'].items():
446                      if not match:
447                          f.write(f"  {criterion}:\n")
448                          f.write(f"    Agent: {r['agent'].get(criterion, 'N/A')}\n")
449                          f.write(f"    Human: {r['human'].get(criterion, 'N/A')}\n")
450
451      print(f"\nResults saved to: {output_file}")
452
````

**Saving the report.**
- **L415–416**: The output folder is `backend/evaluation/prompt_iterations/evaluations/`. `mkdir(parents=True, exist_ok=True)` creates any missing parent folders and doesn't fail if the folder already exists.
- **L417–418**: The filename is like `eval_assessment_agent_prompt_v10_20261029_143000.txt`. `strftime` formats the datetime, so every run gets its own file.
- **L420**: `with open(...) as f:` is a **context manager**. The file is closed automatically when the block ends, even if an error occurs.
- **L421–437**: Writes the same summary that was printed: header, overall accuracy, per-criterion accuracy, confidence tiers and confusion matrices. `.items()` iterates over a dict's key/value pairs.
- **L439–449**: For each mismatched incident, it also writes the **full rationale text** of every criterion where the agent and human disagreed. This is the material you need to understand *why* the agent got an incident wrong.
- **L451**: Prints where the file went.


#### Lines 453–467

````python
453
454  def main():
455      parser = argparse.ArgumentParser(description='Evaluate assessment agent prompts against human ground truth')
456      parser.add_argument('--incidents', help='Comma-separated list of incident numbers to test (default: all with human assessments)'⟦CUT⟧
457      parser.add_argument('--no-run', action='store_true', help='Skip invoking the agent; compare existing agent assessments already i⟦CUT⟧
458
459      args = parser.parse_args()
460      incident_numbers = args.incidents.split(',') if args.incidents else None
461
462      evaluate(incident_numbers=incident_numbers, run_agent=not args.no_run)
463
464
465  if __name__ == '__main__':
466      main()
467
````

**CLI entry point.**
- **L455**: Creates an `argparse` parser. `description` appears in `--help`.
- **L456–457**: Defines `--incidents` (a comma-separated string) and `--no-run` (`action='store_true'` makes it a boolean flag that is `False` unless given). The end of each help string ran off the screen (⟦CUT⟧ in the screenshots). It probably finished the sentences and closed the call with `)`, but that is an inference.
- **L459–460**: Parses the args. `--incidents` becomes a list via `.split(',')`, or `None` if it wasn't given. argparse converts `--no-run` into the attribute `args.no_run`.
- **L462**: Runs the evaluation. `run_agent=not args.no_run` inverts the flag.
- **L465–466**: `if __name__ == '__main__':` runs `main()` only when the file is executed directly. When it is imported (by the API service or by `intake_service`), nothing runs automatically.


---

## 46. `backend/evaluation/evaluate_drafting_prompts.py`

*727 lines · complete*

This is the **evaluation script for the Drafting Agent**. It uses a technique called "LLM as judge": instead of checking whether the agent's notification text matches the human text word for word (useless for free text), it asks a separate LLM call to rate each field of the agent's draft as High / Medium / Low against the human-written draft for the same incident.

It reads every row of the `template_details` table. Rows with `prompt_version IS NULL` are human drafts, and rows with a version label like `v2` are agent drafts. It pairs each agent draft with the human "Reg Relations" draft for that incident, sends each pair to the judge, then aggregates the ratings per (assessment prompt version, drafting prompt version) combination. It writes a `.txt` and a `.xlsx` report into `prompt_iterations/evaluations/`.

It can run two ways:
- **From the command line:** `python backend/evaluation/evaluate_drafting_prompts.py ...`, via `main()`.
- **Through the API:** `backend/services/drafting_service.py::execute_draft_evaluation` lazy-imports this module with `load_evaluator("evaluate_drafting_prompts")`, calls `evaluate(..., verbose=False)` and then `build_summary(...)`. The result is turned into a `DraftEvaluationResponse` (in `schemas.py`) and served by the `/api/evaluate/draft` endpoint in `routers/drafting.py`.


#### Lines 1–43

````python
 1  """
 2  Drafting Prompt Evaluation - LLM as Judge
 3
 4  Measures the Drafting Agent's performance by comparing its generated
 5  notification drafts against the human-authored drafts stored in the
 6  template_details table. Agent drafts are evaluated separately for every
 7  available (assessment_prompt_version, prompt_version) combination:
 8
 9    - Human drafts  : prompt_version IS NULL   (drafted_by = a person's name)
10      - Agent drafts  : prompt_version = 'v2'/... and assessment_prompt_version =
11                                          'v0'/'v1'/'v2'/'human'/...
12
13  The 6 authored fields (title, current_status, description, impact,
14  management_actions, root_cause) are free text, so an exact string match is
15  meaningless. Instead, an LLM judge rates how well each agent field matches the
16  human reference on a High / Medium / Low scale, with a short rationale.
17  Ratings map to numeric scores (High=1.0, Medium=0.5, Low=0.0) for aggregation.
18
19  The human reference is ALWAYS the incident's "Reg Relations" stage row
20  (the final, most authoritative human draft), regardless of which stage the
21  agent draft being judged is for. This lets earlier-stage agent drafts (e.g.
22  Drafting Agent Draft) be judged against the eventual regulator-facing
23  submission, since the human review process only refines a draft rather than
24  starting from scratch, so the same reference is the best available ground
25  truth for any stage.
26
27  Usage:
28      # Judge every assessment/drafting prompt combination found in the table
29      python backend/evaluation/evaluate_drafting_prompts.py
30
31      # Restrict to specific incidents
32      python backend/evaluation/evaluate_drafting_prompts.py --incidents INC0575428,INC0600745
33
34      # Restrict to one stage
35      python backend/evaluation/evaluate_drafting_prompts.py --stage "Drafting Agent Draft"
36
37      # Evaluate selected combinations (comma-separated filters)
38      python backend/evaluation/evaluate_drafting_prompts.py \
39          --drafting-versions v0,v1,v2 --assessment-versions v0,v1,v2,human
40
41      # Backward-compatible alias for one drafting version
42      python backend/evaluation/evaluate_drafting_prompts.py --prompt-version v0
43  """
````

This is the module **docstring**: a triple-quoted string at the top of the file that Python stores as the module's documentation. It doesn't run; it's for humans.
- **L1–11** State the purpose: compare Drafting Agent drafts with human drafts in `template_details`, separately for every combination of the assessment prompt version and the drafting prompt version. The combination matters because the drafting agent builds on an assessment, and that assessment came from some version of the assessment prompt, or from a human (`'human'`).
  - **L9:** human rows are recognised by `prompt_version IS NULL`, and their `drafted_by` holds a person's name.
  - **L10–11:** agent rows have a version label in both columns.
- **L13–17** Why an LLM judge is needed. The six authored fields are free text, so exact matching is meaningless. Each field is instead rated High/Medium/Low, and those ratings map to 1.0/0.5/0.0 so they can be averaged into a "mean match %".
- **L19–25** A key design decision. The human reference is **always** the incident's "Reg Relations" stage row, i.e. the final regulator-facing version, whatever stage the agent draft belongs to. The reasoning given is that human review only refines a draft, so the final version is the best ground truth for every stage.
- **L27–42** Usage examples for the CLI flags that `main()` defines further down: `--incidents`, `--stage`, `--drafting-versions`, `--assessment-versions`, and the older single-version alias `--prompt-version`.


#### Lines 44–51

````python
44
45  import os
46  import sys
47  import argparse
48  from datetime import datetime
49  from pathlib import Path
50  from typing import Dict, List, Optional, Sequence, Tuple
51
````

Standard-library imports.
- **L45** `os`, used at the very end for `os._exit(0)`.
- **L46** `sys`, used to modify the import path (`sys.path`) and to flush stdout.
- **L47** `argparse`, Python's built-in command-line argument parser, used in `main()`.
- **L48** `datetime`, used for report timestamps and as a fallback sort key.
- **L49** `Path` from `pathlib`, an object-oriented way to build file paths (`a / "b"` joins paths).
- **L50** **Type hints**: `Dict`, `List`, `Optional[X]` (meaning "X or None"), `Sequence` (any ordered collection such as a list or tuple) and `Tuple`. Python doesn't enforce these at runtime. They document intent and help editors and linters.


#### Lines 52–63

````python
52  # Add project root to path so the `backend` package is importable
53  sys.path.insert(0, str(Path(__file__).parent.parent.parent))
54
55  from dotenv import load_dotenv
56  load_dotenv()
57
58  from pydantic import BaseModel, Field
59  from typing import Literal
60
61  from backend.utils.postgres_client import get_client
62  from backend.utils.llm_service import LLMService
63  from backend.evaluation.prompt_iterations.prompt_config import DRAFTING_AGENT_PROMPT_VERSION
````

- **L53** `sys.path.insert(0, ...)` puts the **repo root** at the front of Python's module search path. `Path(__file__)` is this file, and three `.parent`s climb out of `evaluation/` and then `backend/` to the repo root. This is what lets `import backend.…` work when the file is run directly as a script (`python backend/evaluation/...`) rather than as part of the package.
- **L55–56** `load_dotenv()` reads a `.env` file into environment variables. That file holds secrets and settings such as DB credentials and LLM endpoint/keys, which the Postgres client and `LLMService` read. It is called *before* the `backend` imports so those modules see the variables when they load.
- **L58** Imports from **Pydantic**, a library for declaring data shapes as classes with automatic validation. `BaseModel` is the base class, and `Field` attaches metadata such as a description.
- **L59** `Literal` lets a type say "only these exact values are allowed", e.g. `Literal["High","Medium","Low"]`. (It could have been on the L50 import line; having it separate is just style.)
- **L61** `get_client` returns the shared singleton `PostgresClient` from `backend/utils/postgres_client.py` (created once, behind a thread lock).
- **L62** `LLMService` is the project's wrapper around the OpenAI-style chat API (`backend/utils/llm_service.py`).
- **L63** `DRAFTING_AGENT_PROMPT_VERSION` is the active drafting prompt module name from `prompt_config.py`, currently `"drafting_agent_prompt_v4"`.


#### Lines 64–81

````python
64
65  # The 6 free-text fields the drafting agent authors. These are the only fields
66  # evaluated here. The deterministic fields (date_of_occurrence, type,
67  # legal_entity, priority_rating, pir, incident_executive_lead_*) are copied
68  # straight from the incidents table (or carried forward from the agent's own
69  # Drafting Agent Draft row) by populate_template_fields (no LLM involved),
70  # so they are intentionally NOT scored in this evaluation.
71  AUTHORED_FIELDS = [
72      "title",
73      "current_status",
74      "description",
75      "impact",
76      "management_actions",
77      "root_cause",
78  ]
79
80  RATING_SCORE = {"High": 1.0, "Medium": 0.5, "Low": 0.0}
81
````

- **L65–70** The comment explains the scope. Only the six free-text fields the LLM actually writes are judged. Deterministic fields such as `date_of_occurrence`, `legal_entity` and `priority_rating` are copied straight from the `incidents` table by `populate_template_fields` without any LLM, so scoring them would tell you nothing about prompt quality.
- **L71–78** `AUTHORED_FIELDS` is a constant list of those six column names: `title`, `current_status`, `description`, `impact`, `management_actions` and `root_cause`. The whole file loops over this list, so adding a field here would extend the prompt, reports and Excel automatically. You'd also have to add matching `*_rating`/`*_rationale` attributes to `DraftJudgment`, though, or `getattr` at L345 would fail.
- **L80** `RATING_SCORE` is a dict mapping each label to a number, used to compute the "mean match %" (High=1, Medium=0.5, Low=0).


#### Lines 82–88

````python
82
83  def _active_version_label() -> str:
84      """Return the short 'v<N>' label for the active drafting prompt version."""
85      from backend.tools.data_tools import _short_prompt_version
86      return _short_prompt_version(DRAFTING_AGENT_PROMPT_VERSION)
87
88
````

- **L83–86** `_active_version_label()` returns the short label (e.g. `"v4"`) for the active drafting prompt.
  - The leading underscore is a Python convention meaning "private/internal helper".
  - **L85:** the import is done *inside* the function (a "lazy import"), so `backend.tools.data_tools` is only loaded if this function is actually called.
  - `_short_prompt_version` uses a regex to pull the trailing `v<N>` off a module name like `drafting_agent_prompt_v4`.
- Note: nothing in this file calls `_active_version_label()`, and no other backend file references it either. It looks like leftover code from before the multi-version filtering was added.


#### Lines 89–96

````python
89  # ── Structured judge output ───────────────────────────────────────────────────
90  class DraftJudgment(BaseModel):
91      """LLM judge verdict comparing an agent draft to the human reference.
92
93      Each field gets a High/Medium/Low match rating plus a one-line rationale.
94      A flat model (rather than nested) is used for robust structured parsing.
95      """
96
````

- **L89** A decorative section-divider comment.
- **L90** `class DraftJudgment(BaseModel)` defines the **structured output schema** for the judge. Because it's a Pydantic model, it can be handed to the LLM API as a `response_format`. The API then forces the model to reply with JSON in exactly this shape, which comes back already parsed into a `DraftJudgment` object. Under the hood, `LLMService.chat` calls `client.beta.chat.completions.parse(...)` and returns `.message.parsed`.
- **L91–95** The docstring. Note the design remark: a **flat** model (`title_rating`, `title_rationale`, …) is used instead of nested objects, because flat schemas parse more reliably with structured-output LLM calls.


#### Lines 97–117

````python
 97      title_rating: Literal["High", "Medium", "Low"]
 98      title_rationale: str = Field(description="One sentence explaining the title rating")
 99
100      current_status_rating: Literal["High", "Medium", "Low"]
101      current_status_rationale: str = Field(description="One sentence explaining the current_status rating")
102
103      description_rating: Literal["High", "Medium", "Low"]
104      description_rationale: str = Field(description="One sentence explaining the description rating")
105
106      impact_rating: Literal["High", "Medium", "Low"]
107      impact_rationale: str = Field(description="One sentence explaining the impact rating")
108
109      management_actions_rating: Literal["High", "Medium", "Low"]
110      management_actions_rationale: str = Field(description="One sentence explaining the management_actions rating")
111
112      root_cause_rating: Literal["High", "Medium", "Low"]
113      root_cause_rationale: str = Field(description="One sentence explaining the root_cause rating")
114
115      overall_rating: Literal["High", "Medium", "Low"]
116      overall_rationale: str = Field(description="One to two sentences summarizing the overall match")
117
````

Each pair of lines defines one field's verdict.
- **L97** `title_rating: Literal["High", "Medium", "Low"]` is a **type-annotated class attribute**. In Pydantic that declares a required field, and `Literal` means the LLM can only answer one of those three strings, so anything else fails validation.
- **L98** `title_rationale: str = Field(description=...)` is a required string. The `description` goes into the JSON schema sent to the LLM, so it doubles as an instruction ("one sentence explaining…").
- **L100–113** The same rating + rationale pattern for `current_status`, `description`, `impact`, `management_actions` and `root_cause`. The names must be exactly `<field>_rating` / `<field>_rationale` because `evaluate()` builds those names dynamically with an f-string (L345–346).
- **L115–116** `overall_rating` and `overall_rationale` are the judge's whole-draft verdict.


#### Lines 118–141

````python
118
119  JUDGE_SYSTEM_PROMPT = """You are an expert regulatory-compliance reviewer evaluating an AI-generated
120  incident notification draft against a human-authored reference draft for the same incident.
121
122  The human draft is the GROUND TRUTH. Your job is to rate how well each field of the AI draft
123  MATCHES the human reference in factual content, coverage of key points, and regulatory tone.
124
125  Rate every field on this scale:
126  - "High": The AI field conveys substantially the same key facts, timeline, systems, and impact
127    as the human reference. Minor wording or ordering differences are fine. No material fact is
128    missing, contradicted, or invented.
129  - "Medium": The AI field captures the general gist but misses or adds some notable details,
130    differs in emphasis, or is partially vague where the human is specific (or vice versa).
131  - "Low": The AI field diverges significantly - it misses key facts, contradicts the human
132    reference, invents details not in the human draft, or is largely off-target.
133
134  Judge on meaning and factual coverage, NOT on exact wording, length, or formatting style.
135  If the human reference field is empty or placeholder-only, rate "High" when the AI field is a
136  reasonable neutral placeholder, otherwise "Medium".
137
138  Provide a concise one-sentence rationale per field and a one-to-two sentence overall rationale.
139  The overall rating should reflect the weighted importance of description and impact (the core
140  narrative fields), not a naive average."""
141
````

`JUDGE_SYSTEM_PROMPT` is the **system message** sent to the judge LLM, a triple-quoted multi-line string. In chat APIs, the system message sets the model's role and rules, and the user message carries the actual data.
- **L119–123** Role and task. The model is a regulatory-compliance reviewer, the human draft is the **ground truth**, and it must rate how well each AI field *matches* it on facts, coverage and regulatory tone.
- **L125–132** The rubric:
  - **High:** same key facts, timeline, systems and impact, with nothing material missing, contradicted or invented.
  - **Medium:** the gist is right, but with notable omissions or additions, or vaguer/more specific.
  - **Low:** significant divergence, contradiction or invented details.
- **L134–136** Judge on meaning, not wording, length or formatting. There is a special rule for empty or placeholder human fields: a neutral AI placeholder gets High, otherwise Medium.
- **L138–140** Rationales must be short. The overall rating should weight `description` and `impact` most (the "core narrative fields") rather than being a plain average. That is why the code takes the judge's `overall_rating` as given instead of computing it.


#### Lines 142–155

````python
142
143  def _build_judge_prompt(incident_number: str, stage: str,
144                          agent: Dict, human: Dict) -> str:
145      """Assemble the user prompt pairing each agent field with its human reference."""
146      parts = [
147          f"Incident: {incident_number}",
148          f"AI Candidate Draft Stage: {stage}",
149          "Human Reference Draft Stage: Reg Relations (the final, most authoritative",
150          "human-authored draft for this incident, used as ground truth for every",
151          "AI candidate stage).",
152          "",
153          "Compare the AI draft (candidate) against the Human draft (reference) field by field.",
154          "",
155      ]
````

- **L143–144** `_build_judge_prompt(incident_number, stage, agent, human)` builds the **user message** text. `agent` and `human` are database rows (dicts of column → value) from `template_details`.
- **L146–155** `parts` is a list of lines that will be joined with newlines at the end. The lines use **f-strings** (`f"Incident: {incident_number}"`), where `{...}` inserts a variable's value into the string. They tell the judge the incident number, which stage the AI draft is from, and that the reference is always the Reg Relations human draft (the design rule from the docstring). Empty strings `""` become blank lines for readability.


#### Lines 156–169

````python
156      for field in AUTHORED_FIELDS:
157          label = field.replace("_", " ").title()
158          human_text = (human.get(field) or "").strip() or "(empty)"
159          agent_text = (agent.get(field) or "").strip() or "(empty)"
160          parts.extend([
161              f"===== {label} =====",
162              "--- Human reference ---",
163              human_text,
164              "--- AI candidate ---",
165              agent_text,
166              "",
167          ])
168      return "\n".join(parts)
169
````

- **L156** Loops over the six authored fields.
- **L157** Turns a column name into a heading: `"management_actions"` becomes `"Management Actions"` (underscores become spaces, then `.title()` capitalises each word).
- **L158–159** Gets each side's text safely. `human.get(field)` returns `None` if the key is missing. `or ""` turns `None` into an empty string, `.strip()` trims whitespace, and the final `or "(empty)"` substitutes a visible marker when nothing is left. The marker is how the judge knows a field is empty, which matters for the placeholder rule in the system prompt.
- **L160–167** `parts.extend([...])` appends several lines at once. Each field becomes a block: a `===== Label =====` header, the human reference, then the AI candidate, then a blank line. Putting human and AI text side by side per field makes field-by-field comparison easy for the model.
- **L168** `"\n".join(parts)` glues the list into one string separated by newlines, and returns it.


#### Lines 170–179

````python
170
171  # ── Data loading ──────────────────────────────────────────────────────────────
172  def _load_template_rows(incident_numbers: Optional[List[str]]) -> List[Dict]:
173      client = get_client()
174      rows = client.db_query("template_details", None, ["*"])
175      if incident_numbers:
176          wanted = set(incident_numbers)
177          rows = [r for r in rows if r["incident_number"] in wanted]
178      return rows
179
````

- **L171** A section divider: data loading.
- **L172–178** `_load_template_rows(incident_numbers)` fetches the raw data.
  - **L173** Gets the shared Postgres client.
  - **L174** `client.db_query("template_details", None, ["*"])` is the project's generic query helper: table name, `filters=None` (no WHERE conditions) and columns `["*"]` (all). It returns a list of dicts, one per row, so this loads the **entire** `template_details` table.
  - **L175–177** If specific incidents were requested, it builds a `set` (fast membership test) and keeps only matching rows with a **list comprehension** (`[r for r in rows if ...]`, a compact way to filter a list). The filtering happens in Python rather than SQL, which is fine for a small evaluation table.


#### Lines 180–197

````python
180
181  def _most_recent(rows: List[Dict]) -> Dict:
182      """Return the row with the latest updated_at (falls back to first)."""
183      return sorted(rows, key=lambda r: r.get("updated_at") or datetime.min, reverse=True)[0]
184
185
186  def _version_label(value: Optional[str]) -> str:
187      """Return a stable report label for nullable version metadata."""
188      return value if value is not None else "unspecified"
189
190
191  def _normalise_versions(versions: Optional[Sequence[str]]) -> Optional[set]:
192      """Strip version filters and return None when all versions are requested."""
193      if versions is None:
194          return None
195      return {version.strip() for version in versions if version and version.strip()}
196
197
````

- **L181–183** `_most_recent(rows)` returns the newest row. It sorts by `updated_at` in descending order (`reverse=True`) and takes element `[0]`.
  - `key=lambda r: ...` is an inline anonymous function that tells `sorted` what to compare.
  - `or datetime.min` gives rows with a NULL `updated_at` the oldest possible date, so they sort last.
  - Note: if `updated_at` comes back as a timezone-aware datetime (typical for a Postgres `timestamptz` column) and *some* rows are NULL, Python will raise `TypeError` when comparing aware and naive datetimes, because `datetime.min` is naive. It only bites with that mix of NULL and non-NULL values.
- **L186–188** `_version_label(value)` replaces a NULL version with the string `"unspecified"`, so old (legacy) agent rows that never recorded an assessment version still get a readable group label in reports.
- **L191–195** `_normalise_versions(versions)` cleans up a user-supplied version filter.
  - It returns `None` (meaning "no filter, take everything") if nothing was given.
  - Otherwise it returns a **set comprehension** `{...}` of stripped, non-empty strings, so `" v2 "` becomes `"v2"` and blank entries are dropped.


#### Lines 198–213

````python
198  def build_pairs(
199      incident_numbers: Optional[List[str]],
200      drafting_versions: Optional[Sequence[str]] = None,
201      assessment_versions: Optional[Sequence[str]] = None,
202      stage_filter: Optional[str] = None,
203  ) -> List[Dict]:
204      """Build one human-reference pair per incident, stage, and prompt combination.
205
206      When a version filter is omitted, every non-null drafting version represented
207      in the template table is included. Legacy agent rows with a null assessment
208      version are retained under the ``unspecified`` label.
209      """
210      rows = _load_template_rows(incident_numbers)
211      wanted_drafting = _normalise_versions(drafting_versions)
212      wanted_assessment = _normalise_versions(assessment_versions)
213
````

- **L198–203** The signature of `build_pairs`, the core matching logic. It takes optional incident, drafting-version, assessment-version and stage filters. `= None` defaults make every filter optional.
- **L204–209** The docstring. It produces one (agent, human) pair per incident × stage × prompt combination. Without filters, every drafting version found in the table is included, and legacy rows with no assessment version are kept under `unspecified`.
- **L210** Loads the rows, already filtered by incident.
- **L211–212** Normalises both version filters into sets, or `None`.


#### Lines 214–221

````python
214      # Index human drafts by incident, always keyed to the "Reg Relations" stage
215      # (the final, most authoritative human draft) regardless of which stage an
216      # agent draft being judged is for.
217      human_by_key: Dict[str, List[Dict]] = {}
218      for r in rows:
219          if r.get("prompt_version") is None and r["template_stage"] == "Reg Relations":
220              human_by_key.setdefault(r["incident_number"], []).append(r)
221
````

This block builds the index of **human reference drafts**.
- **L214–216** The comment restates the rule: the human side is always the "Reg Relations" stage.
- **L217** `human_by_key` is a dict mapping incident number → list of human rows. The annotation `Dict[str, List[Dict]]` documents that.
- **L218–220** For each row that is human (`prompt_version` is `None`) **and** at the Reg Relations stage, `setdefault(key, [])` returns the existing list for that incident, or inserts and returns a new empty one. `.append(r)` then adds the row. This is a common Python idiom for grouping items into lists.
- Notice the `stage_filter` is deliberately *not* applied here. Even if you only evaluate "Drafting Agent Draft" rows, the reference is still Reg Relations.


#### Lines 222–243

````python
222      # NULL columns do not conflict in PostgreSQL unique constraints, so select
223      # only the latest row if legacy data has duplicate prompt combinations.
224      agent_by_key: Dict[Tuple[str, str, str, str], List[Dict]] = {}
225      for r in rows:
226          drafting_version = r.get("prompt_version")
227          if drafting_version is None:
228              continue
229          assessment_version = _version_label(r.get("assessment_prompt_version"))
230          if wanted_drafting is not None and drafting_version not in wanted_drafting:
231              continue
232          if wanted_assessment is not None and assessment_version not in wanted_assessment:
233              continue
234          if stage_filter and r["template_stage"] != stage_filter:
235              continue
236          key = (
237              r["incident_number"],
238              r["template_stage"],
239              assessment_version,
240              drafting_version,
241          )
242          agent_by_key.setdefault(key, []).append(r)
243
````

This block builds the index of **agent drafts**, grouped by a 4-part key.
- **L222–223** The comment explains why grouping and "take the latest" are needed. In PostgreSQL, a UNIQUE constraint treats NULLs as distinct from each other. So two legacy rows with the same incident/stage/drafting version but a NULL assessment version don't count as duplicates, and both can exist. The code therefore keeps only the most recent one later.
- **L224** `agent_by_key` is keyed by a **tuple** `(incident, stage, assessment_version, drafting_version)`. Tuples can be dict keys because they are immutable.
- **L226–228** Reads the drafting version and skips human rows (`None`) with `continue`, which jumps to the next loop iteration.
- **L229** Turns the assessment version into a label, with NULL becoming `"unspecified"`. It can also be `"human"`, meaning the draft was generated from a human assessment rather than an agent one.
- **L230–235** Applies the three optional filters. Each check only applies when the filter is set (`is not None` or truthy), and a row that fails it is skipped.
- **L236–242** Builds the composite key and appends the row to that group with the same `setdefault` idiom.


#### Lines 244–262

````python
244      pairs = []
245      for (incident_number, template_stage, assessment_version, drafting_version), agent_rows in sorted(
246          agent_by_key.items()
247      ):
248          humans = human_by_key.get(incident_number)
249          if not humans:
250              continue
251          pairs.append({
252              "incident_number": incident_number,
253              "template_stage": template_stage,
254              "assessment_prompt_version": assessment_version,
255              "drafting_prompt_version": drafting_version,
256              "agent": _most_recent(agent_rows),
257              "human": _most_recent(humans),
258          })
259      return pairs
260
261
262
````

- **L244** Starts the output list.
- **L245–247** Iterates over the groups in sorted order, so output is stable and grouped alphabetically by incident and stage. The `for (a, b, c, d), agent_rows in ...` syntax **unpacks** each dict item into the four key parts plus the list of rows.
- **L248–250** Looks up the human Reg Relations draft for that incident. If there isn't one, there is nothing to compare against, so the agent draft is skipped.
- **L251–258** Appends a pair dict holding the identifying fields plus the actual rows. `_most_recent(...)` is applied to both sides to pick a single agent row and a single human row if duplicates exist.
- **L259** Returns the list of pairs. L260–262 are blank lines.


#### Lines 263–289

````python
263  # ── Evaluation ────────────────────────────────────────────────────────────────
264  def evaluate(incident_numbers: Optional[List[str]] = None,
265               prompt_version: Optional[str] = None,
266               drafting_versions: Optional[Sequence[str]] = None,
267               assessment_versions: Optional[Sequence[str]] = None,
268               stage_filter: Optional[str] = None,
269               verbose: bool = True,
270               save_reports: bool = True) -> List[Dict]:
271      """Judge agent drafts against human references.
272
273      Args:
274          incident_numbers: Restrict to these incidents (default: all).
275          prompt_version: Backward-compatible filter for one drafting version.
276          drafting_versions: Drafting prompt versions to evaluate (default: all).
277          assessment_versions: Assessment prompt versions to evaluate (default: all).
278          stage_filter: Restrict to a single template stage.
279          verbose: Print progress to stdout (CLI use). Set False for API calls.
280          save_reports: Write .txt and .xlsx reports to backend/evaluation/prompt_iterations/evaluations.
281
282      Returns:
283          List of per-draft result dicts (includes raw rows for reporting).
284      """
285      if prompt_version and drafting_versions:
286          raise ValueError("Use either prompt_version or drafting_versions, not both")
287      if prompt_version:
288          drafting_versions = [prompt_version]
289
````

- **L263** A section divider: evaluation.
- **L264–270** The signature of `evaluate`, the main entry point used by both the CLI and the API service.
  - `prompt_version` is an older single-version parameter kept for backward compatibility.
  - `verbose` controls printing.
  - `save_reports` controls writing the `.txt`/`.xlsx` files.
  - `-> List[Dict]` says it returns a list of result dicts.
- **L271–284** A Google-style docstring documenting each argument. Note that the API caller passes `verbose=False` because console prints are useless inside a web server.
- **L285–286** Raises `ValueError` if the caller passes both the old `prompt_version` and the new `drafting_versions`, since it would be ambiguous which wins.
- **L287–288** If only the old parameter was given, it is converted into a one-item `drafting_versions` list, so the rest of the code only deals with the new form.


#### Lines 290–305

````python
290      def _say(msg: str):
291          if verbose:
292              print(msg)
293
294      _say(f"\n{'='*80}")
295      _say(f"Drafting Agent Evaluation - LLM as Judge")
296      _say(
297          "Drafting prompt versions: "
298          + (", ".join(drafting_versions) if drafting_versions else "all available")
299      )
300      _say(
301          "Assessment prompt versions: "
302          + (", ".join(assessment_versions) if assessment_versions else "all available")
303      )
304      _say(f"{'='*80}\n")
305
````

- **L290–292** `_say` is a **nested function** (defined inside `evaluate`). It prints only when `verbose` is true. It can see `verbose` because inner functions capture variables from the enclosing function (a "closure"). This avoids sprinkling `if verbose:` everywhere.
- **L294–304** Prints a banner. `'='*80` repeats `=` 80 times to make a rule line. The `", ".join(...) if ... else "all available"` is a **conditional expression** (Python's inline if/else) that shows either the requested versions or "all available".
  - Note: L295 is an f-string with no `{}` placeholders. That's harmless, but the `f` prefix is unnecessary.


#### Lines 306–319

````python
306      pairs = build_pairs(
307          incident_numbers,
308          drafting_versions=drafting_versions,
309          assessment_versions=assessment_versions,
310          stage_filter=stage_filter,
311      )
312      _say(f"Found {len(pairs)} agent/human draft pairs to judge\n")
313      if not pairs:
314          _say(
315              "No matching pairs. Ensure both agent and human drafts exist for the same "
316              "incident + stage and that the requested prompt combinations exist."
317          )
318          return []
319
````

- **L306–311** Calls `build_pairs` with the filters, passing the version and stage filters as **keyword arguments** (`name=value`) for clarity.
- **L312** Reports how many pairs were found.
- **L313–318** If there are no pairs, it prints a hint and returns an empty list early, which avoids constructing an LLM client for nothing.
  - Note: the hint says drafts must exist "for the same incident + stage". The human reference is in fact always the Reg Relations stage, so the stage doesn't have to match. The message is slightly out of date with the logic. The same wording appears in `build_summary` (L449–450).


#### Lines 320–331

````python
320      llm = LLMService()
321      results = []
322      for i, pair in enumerate(pairs, 1):
323          inc = pair["incident_number"]
324          stage = pair["template_stage"]
325          assessment_version = pair["assessment_prompt_version"]
326          drafting_version = pair["drafting_prompt_version"]
327          _say(
328              f"[{i}/{len(pairs)}] {inc} / {stage} "
329              f"(assessment={assessment_version}, drafting={drafting_version}) ..."
330          )
331
````

- **L320** Creates the `LLMService` client once and reuses it for all judge calls.
- **L321** `results` accumulates one dict per successfully judged draft.
- **L322** `enumerate(pairs, 1)` yields `(index, item)` with the counter starting at 1, which is used for the `[i/N]` progress display.
- **L323–326** Pulls the identifying fields out of the pair into short local names.
- **L327–330** Prints progress, e.g. `[3/20] INC0575428 / Drafting Agent Draft (assessment=v2, drafting=v4) ...`. Two adjacent f-strings inside the parentheses are automatically concatenated by Python.


#### Lines 332–344

````python
332          prompt = _build_judge_prompt(inc, stage, pair["agent"], pair["human"])
333          try:
334              verdict: DraftJudgment = llm.chat_structured(
335                  messages=[
336                      {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
337                      {"role": "user", "content": prompt},
338                  ],
339                  response_format=DraftJudgment,
340              )
341          except Exception as e:
342              _say(f"  ❌ Judge failed: {e}")
343              continue
344
````

- **L332** Builds the user-message text for this pair.
- **L333** `try:` starts a block whose errors will be caught, so one bad LLM call doesn't kill the whole run.
- **L334–340** Calls the judge.
  - `messages` is the standard chat format: a list of `{"role": ..., "content": ...}` dicts, with the system prompt first and the field-by-field comparison second.
  - `response_format=DraftJudgment` asks for structured output, so `verdict` comes back as a validated `DraftJudgment` instance.
  - `verdict: DraftJudgment = ...` is a variable annotation (a type hint on a local variable).
- **L341–343** `except Exception as e` catches any error (network, rate limit, schema-validation failure), prints it, and uses `continue` to skip this pair. Failed pairs are simply left out of the results rather than counted as Low.


#### Lines 345–364

````python
345          field_ratings = {f: getattr(verdict, f"{f}_rating") for f in AUTHORED_FIELDS}
346          field_rationales = {f: getattr(verdict, f"{f}_rationale") for f in AUTHORED_FIELDS}
347
348          results.append({
349              "incident_number": inc,
350              "template_stage": stage,
351              "assessment_prompt_version": assessment_version,
352              "drafting_prompt_version": drafting_version,
353              "human_drafted_by": pair["human"].get("drafted_by"),
354              "agent": pair["agent"],
355              "human": pair["human"],
356              "field_ratings": field_ratings,
357              "field_rationales": field_rationales,
358              "overall_rating": verdict.overall_rating,
359              "overall_rationale": verdict.overall_rationale,
360          })
361
362          _say(f"  ✅ Overall: {verdict.overall_rating}  "
363               f"({', '.join(f'{f}={field_ratings[f]}' for f in AUTHORED_FIELDS)})")
364
````

- **L345–346** Two **dict comprehensions** (`{key: value for ...}`) that convert the flat Pydantic object into two dicts keyed by field name. `getattr(verdict, f"{f}_rating")` reads an attribute whose name is built at runtime, e.g. `verdict.title_rating`. This is why the `DraftJudgment` attribute names had to follow the `<field>_rating` pattern exactly.
- **L348–360** Appends a result dict containing:
  - the identifiers;
  - `human_drafted_by` (the person who wrote the reference);
  - the full `agent` and `human` rows, which are kept so the Excel export can show the actual texts side by side;
  - the per-field ratings and rationales;
  - the overall verdict.
- **L362–363** Prints a one-line verdict such as `Overall: Medium (title=High, current_status=Medium, ...)`. The inner `', '.join(f'{f}={field_ratings[f]}' for f in AUTHORED_FIELDS)` uses a **generator expression**, which is like a list comprehension but without building the list first.


#### Lines 365–369

````python
365      if save_reports:
366          _report(results, drafting_versions, assessment_versions)
367      return results
368
369
````

- **L365–366** If `save_reports` is set, it calls `_report` (defined below) to print the full report and save the `.txt`/`.xlsx` files. The API path passes `save_reports=True`, so API-triggered runs also leave files on disk.
- **L367** Returns the raw results. The API service then passes them to `build_summary`.


#### Lines 370–383

````python
370  def _distribution_dict(ratings: List[str]) -> Dict:
371      """Return a JSON-serializable High/Medium/Low distribution with mean score."""
372      from collections import Counter
373      counts = Counter(ratings)
374      n = len(ratings)
375      mean = sum(RATING_SCORE[r] for r in ratings) / n * 100 if n else 0.0
376      return {
377          "high": counts.get("High", 0),
378          "medium": counts.get("Medium", 0),
379          "low": counts.get("Low", 0),
380          "mean_match_pct": round(mean, 1),
381      }
382
383
````

- **L370–371** `_distribution_dict(ratings)` turns a list like `["High","Low","High"]` into a JSON-friendly summary dict. "JSON-serializable" means it contains only plain types that can go straight into an API response.
- **L372** A lazy import of `Counter`, a dict subclass that counts occurrences of each item.
- **L373–374** Counts the ratings and gets the total `n`.
- **L375** The mean score as a percentage: sum of 1.0/0.5/0.0 values ÷ n × 100. The `if n else 0.0` avoids dividing by zero when the list is empty.
- **L376–381** Returns the counts for each tier (using `.get(..., 0)` so missing tiers show 0) and `mean_match_pct` rounded to 1 decimal. These keys match the `RatingDistribution` Pydantic schema in `schemas.py`.


#### Lines 384–395

````python
384  def _result_summary(results: List[Dict]) -> Dict:
385      """Build rating distributions for a result subset."""
386      return {
387          "drafts_judged": len(results),
388          "overall": _distribution_dict([r["overall_rating"] for r in results]),
389          "per_field": {
390              field: _distribution_dict([r["field_ratings"][field] for r in results])
391              for field in AUTHORED_FIELDS
392          },  ⟦inferred⟧
393      }  ⟦inferred⟧
394
395
````

- **L384–385** `_result_summary(results)` builds the aggregate block for any subset of results.
- **L387** `drafts_judged` is the count.
- **L388** `overall` is the distribution of the judge's overall ratings.
- **L389–391** `per_field` is a dict comprehension that builds one distribution per authored field.
- **L392–393** These lines weren't visible in the screenshots. *Inference:* from the open brackets they almost certainly just close the `per_field` dict comprehension (`},`) and the returned dict (`}`). Nothing else is needed for the function to work.
- L394–395 are blank.


#### Lines 396–415

````python
396  def _combination_summaries(results: List[Dict]) -> List[Dict]:
397      """Summarize results independently for each assessment/drafting combination."""
398      grouped: Dict[Tuple[str, str], List[Dict]] = {}
399      for result in results:
400          key = (
401              result["assessment_prompt_version"],
402              result["drafting_prompt_version"],
403          )
404          grouped.setdefault(key, []).append(result)
405
406      summaries = []
407      for (assessment_version, drafting_version), combination_results in sorted(grouped.items()):
408          summaries.append({
409              "assessment_prompt_version": assessment_version,
410              "drafting_prompt_version": drafting_version,
411              **_result_summary(combination_results),
412          })
413      return summaries
414
415
````

- **L397–398** `_combination_summaries(results)` produces a separate summary for each (assessment version, drafting version) combination. This is how you compare, say, "assessment v2 + drafting v4" with "human assessment + drafting v4".
- **L399–405** Groups results into a dict keyed by the `(assessment, drafting)` tuple, using the same `setdefault(...).append(...)` idiom as before.
- **L407–413** For each group, sorted for stable output, it builds a dict with the two version labels plus `**_result_summary(...)`. The `**` here **unpacks** another dict's keys and values into this dict literal, merging in `drafts_judged`, `overall` and `per_field`.
- **L414** Returns the list. It matches `DraftCombinationSummary` in `schemas.py`.


#### Lines 416–433

````python
416  def build_summary(
417      results: List[Dict],
418      prompt_version: Optional[str] = None,
419      drafting_versions: Optional[Sequence[str]] = None,
420      assessment_versions: Optional[Sequence[str]] = None,
421  ) -> Dict:
422      """Build a JSON-serializable aggregate and per-combination API summary."""
423      if prompt_version and drafting_versions:
424          raise ValueError("Use either prompt_version or drafting_versions, not both")
425      if prompt_version:
426          drafting_versions = [prompt_version]
427
428      selected_drafting = sorted(set(drafting_versions or []))
429      selected_assessment = sorted(set(assessment_versions or []))
430      if not selected_drafting:
431          selected_drafting = sorted({r["drafting_prompt_version"] for r in results})
432      if not selected_assessment:
433          selected_assessment = sorted({r["assessment_prompt_version"] for r in results})
````

- **L416–421** The signature of `build_summary`, which turns raw `results` into the dict that becomes the API's `DraftEvaluationResponse`.
- **L422** The docstring.
- **L423–426** The same backward-compatibility handling of `prompt_version` as in `evaluate`.
- **L428–429** `sorted(set(x or []))` de-duplicates and sorts the requested version lists. `or []` handles `None`.
- **L430–433** If no filter was requested, it reports instead which versions actually appeared in the results. It uses a **set comprehension** to collect the unique values, then sorts them.
  - This matters for caching: `drafting_service.get_latest_draft_evaluation` checks whether the active prompt label (e.g. `v4`) is in `drafting_prompt_versions` before serving a cached result.


#### Lines 434–453

````python
434
435      legacy_prompt_version = (
436          selected_drafting[0] if len(selected_drafting) == 1 else "multiple"
437      )
438      if not results:
439          return {
440              "prompt_version": legacy_prompt_version,
441              "drafting_prompt_versions": selected_drafting,
442              "assessment_prompt_versions": selected_assessment,
443              "drafts_judged": 0,
444              "overall": _distribution_dict([]),
445              "per_field": {f: _distribution_dict([]) for f in AUTHORED_FIELDS},
446              "combinations": [],
447              "drafts": [],
448              "message": (
449                  "No matching agent/human draft pairs found for the selected prompt versions. "
450                  "Ensure both an agent draft and a human draft exist for the same incident + stage."
451              ),
452          }
453
````

- **L435–437** `legacy_prompt_version` fills the older single-value `prompt_version` field in the API response. It holds the one version if exactly one was evaluated, or the string `"multiple"` otherwise. It is kept so older front-end code that reads `prompt_version` keeps working.
- **L438–452** When there are no results, it returns a well-formed "empty" response rather than crashing:
  - zero counts;
  - an empty distribution for overall and for each field (via a dict comprehension);
  - empty `combinations`/`drafts` lists;
  - a human-readable `message`.
  - The response schema marks `message` as "present when no pairs were found".


#### Lines 454–477

````python
454      aggregate = _result_summary(results)
455      return {
456          "prompt_version": legacy_prompt_version,
457          "drafting_prompt_versions": selected_drafting,
458          "assessment_prompt_versions": selected_assessment,
459          **aggregate,
460          "combinations": _combination_summaries(results),
461          "drafts": [
462              {
463                  "incident_number": r["incident_number"],
464                  "template_stage": r["template_stage"],
465                  "assessment_prompt_version": r["assessment_prompt_version"],
466                  "drafting_prompt_version": r["drafting_prompt_version"],
467                  "human_drafted_by": r["human_drafted_by"],
468                  "overall_rating": r["overall_rating"],
469                  "overall_rationale": r["overall_rationale"],
470                  "field_ratings": r["field_ratings"],
471                  "field_rationales": r["field_rationales"],
472              }
473              for r in results
474          ],
475      }
476
477
````

- **L454** Computes the aggregate across *all* results.
- **L455–476** Returns the full summary dict:
  - **L456–458** The version metadata.
  - **L459** `**aggregate` merges in `drafts_judged`, `overall` and `per_field`.
  - **L460** `combinations` holds the per-combination summaries.
  - **L461–474** `drafts` is a list comprehension producing one compact dict per judged draft. It deliberately leaves out the bulky raw `agent`/`human` rows, so the API response stays small, and its keys match `DraftEvaluationItem` in `schemas.py`.


#### Lines 478–487

````python
478  # ── Reporting ───────────────────────────────────────────────────────────────
479  def _rating_distribution(ratings: List[str]) -> str:
480      from collections import Counter
481      counts = Counter(ratings)
482      n = len(ratings)
483      parts = [f"{tier}={counts.get(tier, 0)}" for tier in ("High", "Medium", "Low")]
484      mean = sum(RATING_SCORE[r] for r in ratings) / n * 100 if n else 0.0
485      return f"{', '.join(parts)}  (mean match {mean:.1f}%)"
486
487
````

- **L478** A section divider: reporting (human-readable text output).
- **L479–485** `_rating_distribution(ratings)` is the **text** counterpart of `_distribution_dict`. It returns a string like `High=5, Medium=3, Low=1  (mean match 72.2%)`.
  - **L483** A list comprehension over the three tiers in fixed order.
  - **L485** `{mean:.1f}` is an f-string **format spec** meaning "float with 1 decimal place".
- Note: this duplicates the math in `_distribution_dict`. That's harmless, but the two must be kept in sync by hand.


#### Lines 488–503

````python
488  def _report(
489      results: List[Dict],
490      drafting_versions: Optional[Sequence[str]],
491      assessment_versions: Optional[Sequence[str]],
492  ) -> None:
493      if not results:
494          print("\nNo results to report.")
495          return
496
497      lines = []
498      total = len(results)
499      lines.append(f"\n{'='*80}")
500      lines.append("Drafting Prompt Evaluation - Results by Assessment/Drafting Combination")
501      lines.append(f"{'='*80}\n")
502      lines.append(f"Total drafts judged: {total}\n")
503
````

- **L488–492** The signature of `_report`. It receives the results and the filters so the file header can record what was requested. `-> None` means it returns nothing.
- **L493–495** If nothing was judged, it prints a message and stops.
- **L497–502** Starts building the report as a list of lines: a banner, a title and the total count. Each `"\n"` inside a line adds an extra blank line.


#### Lines 504–516

````python
504      # Group results by combination
505      combinations = _combination_summaries(results)
506      lines.append(f"Combinations evaluated: {len(combinations)}\n")
507
508      grouped: Dict[Tuple[str, str], List[Dict]] = {}
509      for result in results:
510          key = (
511              result["assessment_prompt_version"],
512              result["drafting_prompt_version"],
513          )
514          grouped.setdefault(key, []).append(result)
515
516      # Detailed report per combination
````

- **L505–506** Computes the combination summaries just to print how many combinations there are. Only `len(...)` is used here.
- **L508–514** Groups results by `(assessment, drafting)` again, the same logic as in `_combination_summaries`. The grouping is redone because this function needs the full result dicts (with rationales), not just the aggregate numbers.


#### Lines 517–532

````python
517      for (assessment_version, drafting_version), combination_results in sorted(grouped.items()):
518          lines.append(f"\n{'='*80}")
519          lines.append(f"Assessment: {assessment_version} + Drafting: {drafting_version}")
520          lines.append(f"{'='*80}\n")
521
522          lines.append(f"Drafts in this combination: {len(combination_results)}\n")
523
524          lines.append("Overall match rating for this combination:")
525          lines.append(f"  {_rating_distribution([r['overall_rating'] for r in combination_results])}\n")
526
527          lines.append("Per-field match rating for this combination:")
528          for field in AUTHORED_FIELDS:
529              ratings = [r["field_ratings"][field] for r in combination_results]
530              lines.append(f"  {field.replace('_', ' ').title():<20} {_rating_distribution(ratings)}")
531          lines.append("")
532
````

For each combination, sorted, the report prints:
- **L518–520** A banner like `Assessment: v2 + Drafting: v4`.
- **L522** How many drafts are in the group.
- **L524–525** The overall rating distribution for that group.
- **L527–531** The per-field distributions, one line per field.
  - `{...:<20}` is a format spec meaning "left-align in a 20-character column", so the numbers line up in a table.
  - `field.replace('_',' ').title()` makes a readable label.


#### Lines 533–545

````python
533          lines.append("Per-draft detail for this combination:")
534          for r in combination_results:
535              lines.append(f"\n  {r['incident_number']} / {r['template_stage']}  "
536                           f"(human: {r['human_drafted_by']})  -> Overall: {r['overall_rating']}")
537              lines.append(f"    {r['overall_rationale']}")
538              for field in AUTHORED_FIELDS:
539                  lines.append(f"      {field:<20} {r['field_ratings'][field]:<7} "
540                               f"{r['field_rationales'][field]}")
541          lines.append("")
542
543      report_text = "\n".join(lines)
544      print(report_text)
545
````

- **L533–541** The per-draft detail. For each result it prints:
  - the incident, stage and human author, with the overall rating;
  - the overall rationale, indented;
  - one line per field with the name (padded to 20 characters), the rating (padded to 7) and the judge's one-sentence rationale.
  This is the section you read to understand *why* a prompt scored badly.
- **L543–544** Joins all lines into one string and prints it to the console.


#### Lines 546–558

````python
546      # Save text report
547      output_dir = Path(__file__).parent / "prompt_iterations" / "evaluations"
548      output_dir.mkdir(parents=True, exist_ok=True)
549      timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
550      text_file = output_dir / f"eval_drafting_combinations_{timestamp}.txt"
551      header = (f"Drafting Prompt Evaluation (LLM as Judge)\n"
552                f"Drafting prompt filter: {', '.join(drafting_versions) if drafting_versions else 'all available'}\n"
553                f"Assessment prompt filter: {', '.join(assessment_versions) if assessment_versions else 'all available'}\n"
554                f"Timestamp: {datetime.now()}\n"
555                f"Total drafts judged: {total}\n\n")
556      text_file.write_text(header + report_text)
557      print(f"\nText report saved to: {text_file}")
558
````

- **L547** Builds the output folder path: this file's directory → `prompt_iterations/evaluations`.
- **L548** `mkdir(parents=True, exist_ok=True)` creates the folder (and any missing parents) and does nothing if it already exists.
- **L549** A timestamp string like `20261029_143005`. `strftime` formats a datetime using codes such as `%Y` (year) and `%H` (hour).
- **L550** The file name `eval_drafting_combinations_<timestamp>.txt`, so each run gets its own file and nothing is overwritten.
- **L551–555** A header block recording the filters used, the run time and the total. The adjacent f-strings inside parentheses concatenate into one string.
- **L556** `Path.write_text` writes header + report to the file in one call.
- **L557** Tells the user where it went.


#### Lines 559–567

````python
559      # Save Excel export
560      xlsx_file = output_dir / f"eval_drafting_combinations_{timestamp}.xlsx"
561      try:
562          _export_excel(results, xlsx_file, drafting_versions, assessment_versions)
563          print(f"Excel report saved to: {xlsx_file}")
564      except Exception as e:
565          print(f"(Excel export skipped: {e})")
566
567
````

- **L560** The Excel file shares the timestamp, so the `.txt` and `.xlsx` of one run pair up.
- **L561–565** Calls `_export_excel` inside `try/except`. If Excel export fails for any reason (most commonly the optional `openpyxl` package not being installed), the run doesn't crash. It prints that the export was skipped, and the text report is still saved.


#### Lines 568–590

````python
568  def _export_excel(
569      results: List[Dict],
570      path: Path,
571      drafting_versions: Optional[Sequence[str]],
572      assessment_versions: Optional[Sequence[str]],
573  ) -> None:
574      """Write a per-field detail sheet and a summary sheet to an .xlsx file."""
575      from openpyxl import Workbook
576      from openpyxl.styles import Font, PatternFill
577
578      wb = Workbook()
579
580      # Sheet 1: per-field detail
581      detail = wb.active
582      detail.title = "Field Ratings"
583      headers = [
584          "Incident", "Stage", "Assessment Prompt", "Drafting Prompt", "Human Author", "Field",
585          "Rating", "Rationale", "Agent Text", "Human Text",
586      ]
587      detail.append(headers)
588      for cell in detail[1]:
589          cell.font = Font(bold=True)
590
````

- **L568–573** The signature of `_export_excel`: it takes the results, the target `path` and the filters.
- **L574** The docstring. Note that the function actually creates three sheets (detail, summary and combination summary), not the two the docstring says.
- **L575–576** Lazy imports from `openpyxl`, the library for writing `.xlsx` files: `Workbook` (the file), `Font` (bold text) and `PatternFill` (cell background colour). They are imported inside the function so `openpyxl` is only required when exporting, and a missing install is caught by the `try` in `_report`.
- **L578** Creates a new in-memory workbook.
- **L581–582** `wb.active` is the default first sheet, renamed to "Field Ratings".
- **L583–587** Defines the column headers and writes them as row 1. `sheet.append(list)` adds a row at the bottom.
- **L588–589** `detail[1]` is row 1. Each header cell gets a bold font.


#### Lines 591–596

````python
591      fills = {
592          "High": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
593          "Medium": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
594          "Low": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
595      }
596
````

- **L591–595** A dict mapping each rating to a solid cell background, using Excel's familiar conditional-format colours: green `C6EFCE` for High, yellow `FFEB9C` for Medium and red `FFC7CE` for Low. They are applied to the Rating column so you can scan the sheet by colour.


#### Lines 597–622

````python
597      for r in results:
598          for field in AUTHORED_FIELDS:
599              rating = r["field_ratings"][field]
600              detail.append([
601                  r["incident_number"],
602                  r["template_stage"],
603                  r["assessment_prompt_version"],
604                  r["drafting_prompt_version"],
605                  r["human_drafted_by"],
606                  field,
607                  rating,
608                  r["field_rationales"][field],
609                  (r["agent"].get(field) or "")[:4000],
610                  (r["human"].get(field) or "")[:4000],
611              ])
612              detail.cell(row=detail.max_row, column=7).fill = fills.get(rating)
613          # Overall row
614          detail.append([
615              r["incident_number"], r["template_stage"],
616              r["assessment_prompt_version"], r["drafting_prompt_version"], r["human_drafted_by"],
617              "OVERALL", r["overall_rating"], r["overall_rationale"], "", "",
618          ])
619          detail.cell(row=detail.max_row, column=7).fill = fills.get(r["overall_rating"])
620          for cell in detail[detail.max_row]:
621              cell.font = Font(bold=True)
622
````

- **L597–598** For every result, and for every field within it, the code writes one row per field.
- **L600–611** The row values: the identifiers, the field name, the rating, the rationale, and then the actual agent text and human text.
  - `[:4000]` **slices** the text to at most 4,000 characters to keep cells manageable. Excel's hard limit is 32,767 characters per cell.
  - `or ""` guards against NULL values.
- **L612** Colours column 7 (Rating) of the row just written. `detail.max_row` is the last row number. `fills.get(rating)` would return `None` for an unexpected label, but `Literal` validation guarantees the rating is one of the three.
- **L613–619** After the six field rows, an "OVERALL" row with the overall rating and rationale, and empty text columns.
- **L620–621** The OVERALL row is made bold across all cells so each draft's block is visually closed off.


#### Lines 623–627

````python
623      widths = [14, 14, 18, 18, 20, 20, 10, 60, 60, 60]
624      for i, width in enumerate(widths, 1):
625          detail.column_dimensions[detail.cell(row=1, column=i).column_letter].width = width
626
627      # Sheet 2: summary
````

- **L623** Column widths in Excel character units: narrow for IDs, 60 for the three long text columns.
- **L624–625** `enumerate(widths, 1)` pairs column numbers 1..10 with widths. `.column_letter` converts a cell's column number to a letter (1→A), because `column_dimensions` is indexed by letter.
- L626 is blank. **L627** starts sheet 2.


#### Lines 628–638

````python
628      summary = wb.create_sheet("Summary")
629      summary.append([f"Drafting Prompt Evaluation (LLM as Judge)"])
630      summary.append(["Drafting prompt filter", ", ".join(drafting_versions) if drafting_versions else "all available"])
631      summary.append(["Assessment prompt filter", ", ".join(assessment_versions) if assessment_versions else "all available"])
632      summary.append([f"Drafts judged", len(results)])
633      summary.append([])
634      summary.append(["Field", "High", "Medium", "Low", "Mean Match %"])
635      for cell in summary[6]:
636          cell.font = Font(bold=True)
637
638      from collections import Counter
````

- **L628** `wb.create_sheet("Summary")` adds a second sheet.
- **L629–632** Metadata rows: the title, both filters and the number of drafts judged. Note that `f"Drafting Prompt Evaluation (LLM as Judge)"` and `f"Drafts judged"` are f-strings with no placeholders. They're harmless, but the `f` is unnecessary.
- **L633** An empty row as a spacer.
- **L634–636** A header row for the summary table. It is row 6 (after 4 metadata rows and 1 blank), which is why `summary[6]` is the row made bold.
- **L638** Imports `Counter` again, locally, for the helper below.


#### Lines 639–655

````python
639
640      def add_summary_row(label: str, ratings: List[str]):
641          counts = Counter(ratings)
642          n = len(ratings) or 1
643          mean = sum(RATING_SCORE[r] for r in ratings) / n * 100
644          summary.append([
645              label, counts.get("High", 0), counts.get("Medium", 0),
646              counts.get("Low", 0), round(mean, 1),
647          ])
648
649      for field in AUTHORED_FIELDS:
650          add_summary_row(field, [r["field_ratings"][field] for r in results])
651      add_summary_row("OVERALL", [r["overall_rating"] for r in results])
652
653      for i, width in enumerate([20, 8, 8, 8, 14], 1):
654          summary.column_dimensions[summary.cell(row=6, column=i).column_letter].width = width
655
````

- **L640–647** `add_summary_row(label, ratings)` is a nested helper that appends one row: the label, High/Medium/Low counts and the mean match %.
  - **L642** `n = len(ratings) or 1` avoids division by zero. If the list is empty, the sum is 0 anyway, so the mean is 0.
  - **L643** The mean % uses `RATING_SCORE`, the same formula as elsewhere.
  - The helper can use `summary` from the outer function because of the closure behaviour seen earlier.
- **L649–651** One row per authored field, then an OVERALL row, aggregated across *all* combinations together.
- **L653–654** Sets the column widths for this sheet, using row 6 cells to get the column letters.


#### Lines 656–678

````python
656      combinations = wb.create_sheet("Combination Summary")
657      combinations.append([
658          "Assessment Prompt", "Drafting Prompt", "Drafts Judged",
659          "Overall High", "Overall Medium", "Overall Low", "Overall Mean Match %",
660      ])
661      for cell in combinations[1]:
662          cell.font = Font(bold=True)
663      for combination in _combination_summaries(results):
664          overall = combination["overall"]
665          combinations.append([
666              combination["assessment_prompt_version"],
667              combination["drafting_prompt_version"],
668              combination["drafts_judged"],
669              overall["high"],
670              overall["medium"],
671              overall["low"],
672              overall["mean_match_pct"],
673          ])
674      for i, width in enumerate([20, 20, 16, 14, 16, 14, 22], 1):
675          combinations.column_dimensions[combinations.cell(row=1, column=i).column_letter].width = width
676
677      wb.save(path)
678
````

- **L656** A third sheet, "Combination Summary".
- **L657–660** Its header row.
- **L661–662** Bolds the header row.
- **L663–673** For each combination from `_combination_summaries`, it writes one row with the versions, the count and the overall High/Medium/Low counts plus the mean %. This is the sheet to look at when comparing prompt versions head-to-head.
- **L674–675** Column widths.
- **L677** `wb.save(path)` writes the workbook to disk. Nothing is written until this line.


#### Lines 679–698

````python
679
680  def main():
681      parser = argparse.ArgumentParser(
682          description="Evaluate drafting agent output against human drafts using an LLM judge"
683      )
684      parser.add_argument("--incidents", help="Comma-separated incident numbers (default: all)")
685      parser.add_argument("--stage", help="Restrict to one template stage (e.g. 'Drafting Agent Draft')")
686      parser.add_argument(
687          "--prompt-version",
688          help="Backward-compatible alias selecting one drafting prompt version (e.g. v2)",
689      )
690      parser.add_argument(
691          "--drafting-versions",
692          help="Comma-separated drafting prompt versions (default: all available)",
693      )
694      parser.add_argument(
695          "--assessment-versions",
696          help="Comma-separated assessment prompt versions, including 'human' (default: all available)",
697      )
698
````

- **L680** `main()` is the CLI entry point.
- **L681–683** Creates an `ArgumentParser`. Its `description` shows up in `--help`.
- **L684–697** Defines the options with `add_argument`. Each is a `--flag` whose value arrives as a string, with `help` text for `--help`.
  - `--incidents` and `--stage`.
  - The legacy `--prompt-version`.
  - The newer comma-separated `--drafting-versions` and `--assessment-versions` (which can include `human`).
  - argparse turns dashes into underscores, so `--drafting-versions` becomes `args.drafting_versions`.


#### Lines 699–705

````python
699      args = parser.parse_args()
700      if args.prompt_version and args.drafting_versions:
701          parser.error("use either --prompt-version or --drafting-versions, not both")
702
703      def _split(value: Optional[str]) -> Optional[List[str]]:
704          return [item.strip() for item in value.split(",") if item.strip()] if value else None
705
````

- **L699** `parse_args()` reads `sys.argv` and returns an object with one attribute per option (`None` if not given).
- **L700–701** Rejects using both the legacy and new version flags. `parser.error` prints a usage message and exits.
- **L703–704** `_split` turns `"v1, v2,"` into `["v1", "v2"]`: it splits on commas, strips each piece and drops empties. It returns `None` when the flag wasn't given, which `evaluate` treats as "all".


#### Lines 706–714

````python
706      evaluate(
707          incident_numbers=_split(args.incidents),
708          prompt_version=args.prompt_version,
709          drafting_versions=_split(args.drafting_versions),
710          assessment_versions=_split(args.assessment_versions),
711          stage_filter=args.stage,
712      )
713
714
````

- **L706–712** Calls `evaluate` with the parsed arguments, using the defaults `verbose=True` and `save_reports=True`, so CLI runs print progress and write the reports. The return value is ignored because the CLI only cares about the side effects (console output and files).


#### Lines 715–727

````python
715  if __name__ == "__main__":
716      try:
717          main()
718      finally:
719          # Close the pool explicitly to avoid the psycopg_pool finalizer warning
720          # on Python 3.13+/3.14 ("cannot join thread at interpreter shutdown").
721          try:
722              get_client().close()
723          except Exception:
724              pass
725          sys.stdout.flush()
726          os._exit(0)
727
````

- **L715** `if __name__ == "__main__":` is true only when this file is run directly (`python .../evaluate_drafting_prompts.py`), not when it is imported by the API service. So importing the module never triggers a run.
- **L716–717** Runs `main()`.
- **L718** `finally:` always runs, whether `main()` succeeded or raised an error.
- **L719–724** Closes the Postgres connection pool explicitly. The comment explains why: on Python 3.13+, letting `psycopg_pool` clean itself up at interpreter shutdown emits a "cannot join thread" warning. The inner `try/except: pass` ignores any failure while closing.
- **L725** Flushes stdout so all printed output appears before the process ends.
- **L726** `os._exit(0)` kills the process immediately with exit code 0, skipping normal interpreter shutdown, which avoids any remaining background-thread or finalizer noise.
- Note: because `os._exit(0)` sits in `finally`, it runs **before** an exception from `main()` can propagate. If `main()` crashes (for example the DB is unreachable), the traceback is never printed, and the process still exits with code **0** ("success"). The same applies to `parser.error` (normally exit code 2). If you're debugging a silent failure from the CLI, that's why. A safer pattern would catch the exception, print it, and exit non-zero.


---

## 47. `backend/README_RUN.md`

*151 lines · complete*

A **how-to-run guide** in Markdown, not code. It covers two ways of using the system: a one-shot command-line batch run, and running the FastAPI server and calling its endpoints. It also explains the asynchronous *job* pattern (every agent endpoint returns `202` plus a `job_id` that you poll), the `mode=default|force` switch, the `assessment_prompt_version` option for drafting, the required `.env` settings, and a `psql` query to check results. It is a good map of the API, but some parts have fallen behind the code. Those spots are flagged with "Note:" below.


#### Lines 1–2

````markdown
1  # Running the Assessment and Drafting Agents
2
````

- **L1** The document title. "Assessment" and "Drafting" are the two LLM agents: one decides reportability, the other writes the regulator notification.


#### Lines 3–21

````markdown
 3  ## Batch Assessment Mode (Default)
 4
 5  Run assessments on all incidents in the database:
 6
 7  ```bash
 8  # Activate virtual environment
 9  source venv/bin/activate
10
11  # Run batch assessment
12  python backend/main.py
13  ```
14
15  This will:
16  1. Read all incidents from the `incidents` table
17  2. For each incident without an existing assessment, invoke the assessment agent
18  3. Agent evaluates reportability using the 4 FRB criteria
19  4. Write results to `reportability_assessments` table
20  5. Log all actions for audit trail
21
````

- **L3–5** The section on the default batch mode, which assesses every incident in the database.
- **L7–13** A fenced `bash` code block. It activates the Python virtual environment (`source venv/bin/activate`, so the project's installed packages are used) and then runs `python backend/main.py`.
- Note: as written, this probably doesn't work. `backend/main.py` has no `if __name__ == "__main__":` block, so running it only defines `app` and exits. Running a file inside the `backend` package directly also usually breaks its `from backend....` imports, because Python puts `backend/` on the import path rather than the repo root. The batch CLI lives in the **root** `main.py`, which imports `run_batch_assessment` from `backend.main` (see `__all__` in `backend/main.py`). The command is most likely meant to be `python main.py`, consistent with L27.
- **L15–20** What a batch run does:
  1. Read the `incidents` table.
  2. Skip incidents that already have an assessment (for the active prompt version).
  3. Evaluate the **4 FRB criteria** (Federal Reserve Board reportability questions). In the code these are the `data_system_impact`, `operational_impact`, `financial_reputational_impact` and `systemic_risk` fields.
  4. Write to `reportability_assessments`.
  5. Write an audit log entry. In a regulated bank, being able to show who or what decided, and when, is a compliance requirement.


#### Lines 22–29

````markdown
22  ## FastAPI Server Mode
23
24  To run the FastAPI server for API access:
25
26  ```bash
27  python main.py --server
28  ```
29
````

- **L22–28** Server mode: `python main.py --server`. This is the **root** `main.py`, which isn't among the backend files; judging from the URLs below, the `--server` flag presumably starts uvicorn on port 8000 serving `backend.main:app`.


#### Lines 30–39

````markdown
30  Then access:
31  - API docs: http://localhost:8000/docs
32  - Health check: http://localhost:8000/health
33  - Assess one incident: `POST /api/assess`
34  - Assess all or selected incidents: `POST /api/batch/assess`
35  - Draft one incident: `POST /api/draft`
36  - Draft all or selected eligible incidents: `POST /api/batch/draft`
37  - Poll job status/results: `GET /api/jobs/{job_id}`
38
39  Swagger displays validated request fields and runnable examples for each endpoint.
````

- **L31** `/docs` is FastAPI's auto-generated Swagger UI. You can try every endpoint from the browser there.
- **L32** `/health` is defined in `routers/operations.py`. It runs a DB health check and lists the agent names from `get_agent_registry()`.
- **L33–36** The four agent endpoints: assess one (`/api/assess`), batch assess (`/api/batch/assess`), draft one (`/api/draft`), batch draft (`/api/batch/draft`).
- **L37** `GET /api/jobs/{job_id}` is the polling endpoint for the asynchronous jobs those four return.
- **L39** Swagger shows validated fields and examples. These come from the `Query(...)` parameters and from `json_schema_extra` on the Pydantic models in `schemas.py`.


#### Lines 40–47

````markdown
40  The two batch endpoints (`/api/batch/assess`, `/api/batch/draft`) take an
41  optional `incident_numbers` query param (leave blank for the full eligible
42  batch) and a `mode` query param restricted to exactly two choices:
43  - `default` - process only incidents missing a result for the active prompt
44    version (skips existing complete assessments/drafts)
45  - `force` - reprocess every eligible incident, overwriting existing
46    active-prompt-version rows
47
````

- **L40–42** Both batch endpoints accept an optional `incident_numbers` query parameter, a comma-separated list parsed by `routers.parse_csv_list`. Leaving it empty means the whole batch.
- **L42–46** `mode` is typed as `Literal["default", "force"]` in the routers, so FastAPI rejects anything else with a 422 error. `default` processes only incidents that don't yet have a result for the **active prompt version**. `force` reprocesses everything and overwrites those rows. Results are stored per prompt version, which is what lets the evaluation suite compare v9 against v10, for example.


#### Lines 48–52

````markdown
48  All four agent endpoints are asynchronous. They immediately return HTTP `202`
49  with a `job_id` and `status_url`; the assessment or drafting work continues in
50  the background. Call the returned `status_url` until `status` is `completed` or
51  `failed`. A completed job includes its final output in `result`.
52
````

- **L48–51** The asynchronous contract. The endpoint returns HTTP **202 Accepted** immediately, with a `job_id` and a `status_url`. The real work runs in the background (`services/job_queue.py` uses FastAPI `BackgroundTasks`). The client polls until `status` is `completed` (with `result` filled in) or `failed` (with `error` filled in). The reason for this design is that LLM calls can take minutes, much longer than a typical HTTP timeout.


#### Lines 53–64

````markdown
53  Example accepted response:
54
55  ```json
56  {
57  	"job_id": "d20fc7ee-f3cf-47ca-a9c5-5d64e66e44cb",
58  	"operation": "draft",
59  	"status": "queued",
60  	"status_url": "/api/jobs/d20fc7ee-f3cf-47ca-a9c5-5d64e66e44cb",
61  	"submitted_at": "2026-09-17T16:30:00Z"
62  }
63  ```
64
````

- **L53–63** An example of the 202 response body (the `JobAcceptedResponse` schema). It contains a UUID `job_id`, the `operation` name, `status: "queued"`, a relative `status_url` and an ISO-8601 UTC `submitted_at` (the `Z` suffix means UTC).


#### Lines 65–72

````markdown
65  ### API request examples
66
67  Assess one incident:
68
69  ```json
70  {"incident_number": "INC0575428"}
71  ```
72
````

- **L65–71** An example request for assessing one incident, shown as a JSON body `{"incident_number": "INC0575428"}`.
- Note: the current `routers/assessment.py` takes `incident_number` as a **query parameter** (`Query(...)`), not a JSON body. The working call is `POST /api/assess?incident_number=INC0575428`. Swagger shows the correct form.


#### Lines 73–86

````markdown
73  Assess the entire batch, skipping assessments already generated with the active
74  prompt version (`mode=default`, the default):
75
76  ```
77  POST /api/batch/assess
78  ```
79
80  Assess selected incidents and overwrite their active-prompt assessment rows
81  (`mode=force`):
82
83  ```
84  POST /api/batch/assess?incident_numbers=INC0575428,INC0600745&mode=force
85  ```
86
````

- **L73–78** Assess the entire batch with the default mode, so incidents already assessed with the active prompt version are skipped.
- **L80–85** Assess two specific incidents and overwrite their existing active-version rows (`mode=force`). This is useful after a prompt change, when you want to re-run only a handful of incidents.


#### Lines 87–97

````markdown
87  Draft or revise one incident. Supported stages are `MIMT Draft`, `1LOD R&C`,
88  `2LOD R&C`, and `Reg Relations`:
89
90  ```json
91  {
92  	"incident_number": "INC0575428",
93  	"template_stage": "MIMT Draft",
94  	"feedback": null
95  }
96  ```
97
````

- **L87–88** Lists the supported draft stages: `MIMT Draft`, `1LOD R&C`, `2LOD R&C`, `Reg Relations`. These follow the review chain: first draft by MIMT (major incident management), then risk & control review by the 1st line of defence, then the 2nd line, then Regulatory Relations.
- **L90–96** An example draft request (incident, stage, and `feedback: null` for a first draft; reviewer text goes in `feedback` when asking for a revision).
- Note: in `routers/drafting.py` the allowed `template_stage` values are `"Drafting Agent Draft"` (the default), `"1LOD R&C"`, `"2LOD R&C"` and `"Reg Relations"`. Passing `MIMT Draft` to `/api/draft` would be rejected with a 422 error. That endpoint also takes query parameters, not a JSON body.


#### Lines 98–109

````markdown
 98  Both `/api/draft` and `/api/batch/draft` also accept `assessment_prompt_version`,
 99  which selects the reportability assessment used as drafting context (and, for
100  batch drafting, which assessment determines eligibility). It is no longer
101  tied to the active assessment prompt version:
102  - omitted - the active assessment agent prompt version (default, prior behavior)
103  - `human` - the human-provided assessment (`prompt_version IS NULL`)
104  - a specific version label, e.g. `v1` - that agent prompt version's assessment
105
106  ```
107  POST /api/draft?incident_number=INC0575428&assessment_prompt_version=human
108  ```
109
````

- **L98–104** `assessment_prompt_version` selects which reportability assessment the drafting agent uses as context. For batch drafting it also decides which incidents are eligible. Omit it to use the active agent version. `human` uses the human assessment, which is stored with `prompt_version IS NULL` (SQL's way of saying "no value"). A label such as `v1` uses that agent version's output. The point is to separate drafting quality from assessment quality: you can draft from the human ground-truth assessment and evaluate the drafting agent on its own.
- **L106–108** An example using query parameters (this one does match the code).


#### Lines 110–122

````markdown
110  Draft the entire eligible batch (`mode=default`, the default):
111
112  ```
113  POST /api/batch/draft
114  ```
115
116  Redraft the entire eligible batch, overwriting existing active-prompt drafts
117  (`mode=force`):
118
119  ```
120  POST /api/batch/draft?mode=force
121  ```
122
````

- **L110–114** Draft every eligible incident and skip existing drafts.
- **L116–121** Redraft every eligible incident and overwrite existing drafts (`mode=force`).


#### Lines 123–133

````markdown
123  Draft using the human-provided assessments instead of the active agent version:
124
125  ```
126  POST /api/batch/draft?assessment_prompt_version=human
127  ```
128
129  Batch drafting includes only incidents whose assessment (per
130  `assessment_prompt_version`) has `reportability_flag` set to `Yes` (priority is
131  no longer a shortcut - even P1 incidents must be assessed as reportable
132  first). Existing active-prompt MIMT drafts are skipped unless `mode=force`.
133
````

- **L123–127** Batch-draft using the human assessments.
- **L129–132** The eligibility rule: an incident is drafted only if the selected assessment has `reportability_flag = Yes`. Before, a P1 (top-priority) incident was drafted automatically. That shortcut has been removed, so every incident must be assessed as reportable first. Incidents that already have an active-version draft are skipped unless `mode=force`.


#### Lines 134–139

````markdown
134  ## Prerequisites
135
136  Ensure your `.env` file has valid credentials:
137  - PostgreSQL connection details
138  - LLM OAuth credentials (LLM_OAUTH_URL, LLM_CLIENT_ID, LLM_CLIENT_SECRET, LLM_BASE_URL)
139
````

- **L134–138** Prerequisites: a `.env` file containing the Postgres connection settings (read by `config.py`) and the LLM OAuth client-credentials settings (read by `utils/llm_service.py`).


#### Lines 140–151

````markdown
140  ## Testing
141
142  To check database contents after assessment:
143
144  ```bash
145  psql -h localhost -p 5432 -U postgres -d anchor_ai -c "
146  SET search_path TO anchor_ai_dev, public;
147  SELECT incident_number, reportability_flag, provided_by
148  FROM reportability_assessments;
149  "
150  ```
151
````

- **L142–150** A `psql` one-liner for checking results. `-h/-p/-U/-d` give the host, port, user and database. `SET search_path TO anchor_ai_dev, public;` makes unqualified table names resolve in the `anchor_ai_dev` schema first, which matches setting `POSTGRES_SCHEMA=anchor_ai_dev` in `.env`. The query then lists each incident's `reportability_flag` and `provided_by` (e.g. "Assessment Agent", or a human reviewer).


---

## Appendix: things that looked off while reading

These were spotted while explaining the code and are flagged inline as **Note:** in the relevant sections. They come from reading screenshots, not from running the code, so treat them as leads to verify, not confirmed bugs.

### Likely real bugs

- **The cached assessment evaluation never shows on the Admin page.**
  - `evaluate_assessment_prompts.build_summary` stores `prompt_version` as the full module name (`assessment_agent_prompt_v10`).
  - `assessment_service.get_latest_assessment_evaluation` compares it with the short label (`v10`), so it always returns `None`.
  - Two separate read-throughs found this independently.
- **`/api/evaluate/assessment` with `run_agent=true` doesn't take the `exclusive_operation("assessment")` lock.** It can run live assessments at the same time as `/api/assess` or batch jobs.
- **Event name mismatch.** `intake_service.mark_mimt_complete` says the trigger emits `mimt_completed`, but `workflow_service` handles `mimt_input_completed`. Check the trigger SQL in `db_setup.sql` (not in `backend/`).
- **First revision at `1LOD R&C` sends a blank current template.** `run_single_draft` reads the template only after `populate_template_fields` has written a skeleton row. The earlier text only arrives via the baseline draft.
- **Crashes exit silently.** `evaluate_drafting_prompts.py` calls `os._exit(0)` in a `finally`, so a crash in `main()` exits with code 0 and no traceback.

### Robustness and security

- **Unvalidated SQL identifiers.**
  - `PostgresClient.db_query`/`db_write` put table and column names into SQL with f-strings.
  - The `db_query` tool can receive those names from LLM output.
  - Only the values are parameterised. `sql_utils.sanitize_sql_identifier` exists but is unused.
- **`config.py` never calls `load_dotenv()`.** Its settings only see `.env` values if another module loaded `.env` before `backend.config` was first imported. Worth checking if settings silently fall back to defaults.
- **`chat()` retries errors that can't succeed.** Its `@retry` includes `APIError`, the base class of nearly all OpenAI SDK errors, so 400/401 responses are retried twice before failing.
- **TLS verification is off by default.** `LLM_VERIFY_SSL` defaults to false, and the warnings are suppressed.
- **Full prompts in plain-text logs.** `logger.log_llm_call` writes the whole message list, including incident text.
- **Fragile Postgres connection string.** It puts the password in unquoted (`workflow_listener`, `postgres_client`), so a password with a space or quote breaks it.
- **NOTIFY events are lost while the listener is disconnected.** Postgres doesn't store them.
- **No way out of `Error`.** A failed workflow handler can leave the approval flag set, and no handler starts from `Error`, so recovery looks manual.
- **Single-process assumptions.** Locks (`operation_runtime`) and jobs (`job_queue`) live in process memory, so neither works across several API workers, and jobs are lost on restart.
- **Blocking calls in async endpoints.** Some `async def` endpoints (intake, review) call blocking DB code.

### Docs and comments that drifted from the code

- **`README_RUN.md`:**
  - Says to run `python backend/main.py` for batch mode, but that file has no `__main__`.
  - Shows JSON bodies, but the endpoints take query params.
  - Lists stage `MIMT Draft`, which the drafting router rejects.
- **`prompt_iterations/README.md`:**
  - Says assessment v2 is active (it's v10) and uses old script paths and file names.
  - Its v0 accuracy figures disagree with each other (33.3% vs 35.3%).
- **Stale names and descriptions:**
  - Several docstrings still point to `main._run_single_assessment` / `main._run_single_draft`, which now live in `services/`.
  - `BatchDraftRequest` still describes P1 incidents as auto-drafted.
- **Wrong error codes documented:** a 503 is documented that nothing raises. The real "queue full" error is an undocumented 429.
- **Dead code:**
  - `COMMON_TOOLS`/`ASSESSMENT_TOOLS`/`DRAFTING_TOOLS`, `sql_utils.py`, the `utils/__init__` helpers.
  - `get_embeddings`, `PostgresClient.listen()`, `_active_version_label()`, `fmt()` in `compare_agent_vs_human.py`.
  - `HISTORY_LIMIT` and the SMTP settings.

### Evaluation methodology

- **Two accuracy numbers can disagree.** `compare_assessments` treats a blank criterion as "No" (two blanks = match), while the confusion matrices skip blanks.
- **Different ground truth.** `compare_agent_vs_human.py` doesn't require 2LOD approval; `evaluate_assessment_prompts.py` does.
- **The confidence breakdown is always empty.** `run_agent_assessment` returns `{}`, and `db_write` strips `confidence_*` before saving.
- **Fragile "Yes" check.** The rule-based flag only counts an answer as "Yes" if its text starts with `yes`, so a Markdown-bold `**Yes**` would be read as No.
