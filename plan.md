# Plan: Anchor AI — Frontend v2 (new, HIG-based)

A new frontend built from scratch in `frontend-v2/`, designed around the incident lifecycle rather than the API. Original `frontend/` untouched. Runs on **:3001** (original on :3000), both against the backend on :8000.

## What this app is, from the user's side

Three roles, one object (the incident), one direction of travel:

```
MIMT logs incident → AI assesses reportability → AI drafts notification
→ 1LOD review → 2LOD review → Reg Relations submits → Done
```

The UI's only job: show where every incident is, what needs a human right now, and let that human act in one screen. Everything else (jobs, agents, health) is secondary.

## Stack

Vite + React + TypeScript, plain CSS with tokens. No UI library, no state library. Fetch wrapper + React Query for polling jobs. That's it.

## Screens (5)

### 1. Inbox — home (`/`)
HIG: sidebar + large title + inset grouped list.
- Sidebar: **Needs Action**, **All Incidents**, **Completed**, **Not Reportable**, divider, **Jobs**, **System**.
- Main: list of incidents, each row = number · title · priority badge · status pill · "waiting on: MIMT / AI / 1LOD / 2LOD / Reg Relations" · updated-ago.
- Top: search field, segmented control for status, "New Incident" button (accent).
- "Needs Action" is the default view — it's the whole reason to open the app.

### 2. Incident (`/incidents/:n`)
The one screen that matters. Split view, HIG detail layout.
- **Header**: large title (incident number), subtitle (title), status pill, location/legal entity, priority. Primary action button in the header changes by stage (see §Actions).
- **Timeline strip** across the top: Intake → Assessment → Drafting → 1LOD → 2LOD → Reg Relations → Submitted, current step filled, errors in red.
- **Left column (content)**, grouped sections:
  - Incident facts (MIMT fields) — read-only after MIMT complete.
  - **Assessment** — reportable Yes/No as a prominent verdict card, the 4 FRB criteria as a checklist with reasoning disclosure rows, prompt version + revision in footnote text.
  - **Notification draft** — the template fields (title, description, impact, management actions, root cause, PIR…) rendered as a document, with a segmented control to flip between stages (Drafting Agent Draft / 1LOD R&C / 2LOD R&C / Reg Relations).
- **Right column (inspector)**: audit log as a vertical timeline; feedback history; similar past incidents.

### 3. New / Edit Incident (`/incidents/new`)
HIG form: grouped fields, inline labels, one column, "Mark MIMT input complete" as a confirmation sheet ("This starts the AI assessment. You won't be able to edit afterwards.").

### 4. Jobs (`/jobs`)
Batch assessment/drafting runs. Inset list with progress bars; tap → per-incident results. Polls `/api/jobs/{id}`.

### 5. System (`/system`)
`/health` and `/agents`. Status dots, agent cards with tool lists. One screen, no tabs.

## Actions (role-aware, one primary button per stage)

| Stage | Primary button | HIG pattern |
|---|---|---|
| Awaiting MIMT | Mark input complete | Confirmation sheet |
| Assessment / Drafting (AI running) | — (spinner + "Assessing…") | Indeterminate progress |
| 1LOD / 2LOD review | **Approve** · Send feedback | Approve = filled accent; feedback opens a sheet with a text area |
| Reg Relations | Mark submitted | Confirmation sheet, destructive-style (can't undo) |
| Error | Retry / View log | Red status, alert |

Role picker in the sidebar footer (MIMT / 1LOD / 2LOD / Reg Relations) — purely controls which buttons show; no auth.

## HIG rules applied

- **Type**: system font stack; Large Title 34, Title 2 22, Headline 17/600, Body 17, Subheadline 15, Footnote 13.
- **Color**: `label` / `secondaryLabel`, `systemBackground` / `secondarySystemGroupedBackground`, `separator`. Accent `systemBlue`. Status only: green approved, orange pending, red error/not approved, gray not reportable. Light + dark via `prefers-color-scheme`.
- **Layout**: 8-pt grid, inset grouped cards radius 12, sidebar 240px, content max 1100px, inspector 320px.
- **Controls**: filled / tinted / plain buttons, segmented controls, native-style search field, disclosure chevrons, 44pt hit targets.
- **Feedback**: sheets for anything that commits; alerts only for errors; toasts for "saved".
- **Motion**: 200ms ease, respects `prefers-reduced-motion`.

## Build order

1. Scaffold `frontend-v2/` on :3001, tokens, app shell (sidebar + large title).
2. Inbox list wired to `GET /api/incidents`.
3. Incident screen: header + timeline + assessment + draft sections.
4. Actions: MIMT complete, approve / feedback, reg submit.
5. New-incident form.
6. Jobs + System.
7. Empty / loading / error states; dark mode pass.

## Out of scope

Auth, backend changes, charts/analytics, mobile layout (desktop-first; it's an operator tool).

## Run

```
cd frontend && npm run dev        # :3000 original
cd frontend-v2 && npm run dev     # :3001 new
```
