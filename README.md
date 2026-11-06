Best practice is an **event-driven background job**, not a synchronous call inside the save handler. Three parts:

**1. Save handler stays thin and transactional**
`POST /incidents` does exactly two things in one DB transaction: insert the incident with `status = "ASSESSING"` and insert an `assessment_jobs` row (`incident_id`, `status = "QUEUED"`, `attempts = 0`). Return 201 immediately. MIMT sees the incident already in the assessing stage with a spinner/"Agent assessing…" badge. Never run the LLM call inside the request — it's slow, it can fail, and a browser timeout would leave you with a half-saved incident.

**2. A worker picks up the job**
Pick based on what you already run:
- *Simplest (what I'd do for Anchor now):* FastAPI `BackgroundTasks` or a `asyncio.create_task` on startup that polls `assessment_jobs WHERE status='QUEUED'` every few seconds. Zero new infra.
- *Proper:* Celery/RQ/ARQ with Redis, or Postgres `LISTEN/NOTIFY`. Use this once you need retries, concurrency limits, or more than one worker.

The job row is the key either way — it's the outbox pattern. If the worker crashes mid-assessment, the job is still `QUEUED`/`RUNNING` with a timestamp, so a sweeper can retry it. Enqueuing from the same transaction as the insert means you can never save an incident and lose the trigger.

**3. Worker writes results back and notifies the UI**
Agent finishes → updates `incidents.assessment`, sets `status = "ASSESSED"` (or `"NEEDS_REVIEW"` on low confidence / failure), marks the job `DONE`/`FAILED`. Frontend either polls `GET /incidents/{id}` every ~5s while status is `ASSESSING`, or you add a WebSocket/SSE endpoint so the card updates live. Polling is fine to start.

**Rules of thumb**
- Idempotent agent: running it twice on the same incident should overwrite, not duplicate.
- Cap retries (3) with backoff; after that, flag for MIMT rather than loop forever.
- Keep a manual "Re-run assessment" button — the auto-trigger replaces the first click, not human override.
- Log `job_id`, prompt version, and model used on each run so you can audit why an assessment said what it said (regulatory context).

This also drops straight into the ServiceNow auto-pull you planned: that poller just calls the same internal `create_incident()` function, so ServiceNow-discovered incidents get assessed through the identical path as manually entered ones.
