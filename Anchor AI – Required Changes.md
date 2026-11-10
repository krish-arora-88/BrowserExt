# Anchor AI – Required Changes

Source: RBC Assist Notetaker notes, Nov 9, 2026 (backend/feedback meeting; MIMT UI demo with Satish, Akarsh, Sparsha).

## 1. Backend and database

- Cut the database to **five tables**. Remove Copilot-generated tables, services and routers that the frontend doesn't use. Meeting named `audit_logs`, `avoidability_assessments` and `template_details`; the dev schema has 11 tables and uses `reportability_assessments`, so the final five need confirming.
- Defer `audit_logs` enhancements; it keeps being written to as is.

## 2. Feedback and re-draft workflow

- Add `LOD1_feedback` and `LOD2_feedback` columns to `template_details`. The dev schema currently has lod1/lod2 feedback and comments columns on `incidents` instead, so decide where they live.
- Frontend sends the comment plus the user's role (LOD1/LOD2) as JSON through an existing endpoint. Backend writes to the matching column.
- Append new comments to the column. Don't overwrite and don't insert a row per comment. Each append updates `updated_at`.
- Change the DB trigger to fire on `updated_at` changes instead of new-row inserts, and have it re-run the **Drafting Agent** (not Assessment). It must fire only on feedback changes, or manual draft edits will also re-trigger drafting.
- Fix the feedback/reassessment flow. It is currently broken.
- Storage design is **not final**. Krish brings 2–3 options to the Wednesday review with Satish.

## 3. Workflow rules

- **P1 incidents:** Assessment Agent always outputs "yes" (reportable). If its own result would be "no", show a red flag and escalate to One LOD, who can still override.
- **Edit permissions:** Only the current ticket holder can edit the draft. MIMT cannot edit once the incident is with Two LOD. Restrict the draft edit feature by role. Edits don't re-trigger the Drafting Agent.
- **MIMT view:** show the assessment and the draft.
- **Not reportable:** still requires One LOD, then Two LOD approval, for audit. One LOD rejecting a reportable incident (with a reason) goes to Two LOD, not back to MIMT. Two LOD has the final say, and confirmed items go to the not-reportable file.
- **Reject as Not Reportable button:** move next to the assessment section if Sparsha prefers.
- No attachments. The notification template is fixed, so nothing to build.

## 4. Dashboard and export

- Build a dashboard with KPIs, filters (timeline, role, date) and an export button.
- Export: incidents approved by Two LOD as reportable in a month, for FRB reporting. This replaces a roughly 3-week email-based process.
- Key KPI: time from incident creation to Two LOD completion, against the FRB 5-hour limit. Per-incident time is already tracked.

## 5. ServiceNow integration

- Pilot with RBC, then add CNB, which is a separate ServiceNow instance.
- Pull priority (P1/P2/P3) from ServiceNow. Sparsha will supply the field list for the intake form.
- Keep a manual MIMT enrichment step; MIMT will still add details beyond ServiceNow.
- Undecided: whether MIMT edits push back to ServiceNow or stay in Anchor only.

## Pending inputs

| Item | Owner | When |
| --- | --- | --- |
| Feedback design options reviewed | Krish, Akarsh, Satish | Wednesday |
| ServiceNow field list for intake form | Sparsha | Before next meeting |
| MIMT resource for ServiceNow questions | Sparsha | Friday call |
| Intake form screenshot shared with Amrit (One LOD) for feedback | Akarsh | Not set |
