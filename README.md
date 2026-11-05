You are working in the Anchor AI codebase (regulatory notification system, FastAPI backend + frontend). Fix and complete the comment workflow between 1LOD and 2LOD end to end, then verify it with Playwright MCP. Work autonomously and don't stop to ask questions. Where something is ambiguous, pick the most reasonable option and list it in your final summary.

## Step 1: Explore first (use graphify)
Use graphify to map the codebase before changing anything. Build or refresh the graph for the repo, then query it to find:
- the comments feature (frontend components, state, API routes, DB models)
- how notifications are handed off between 1LOD and 2LOD
- how user roles are modeled and checked
- every place that decides whether edit/resolve/delete controls are shown

## Step 2: Root cause
Currently, edit/resolve/delete controls are tied to whoever currently holds the notification instead of to the comment itself. Result: after a hand-off, the other LOD sees no actions, and an author whose comment was sent back loses their own controls. Fix permissions so they derive from (comment.author_role, comment.status, viewer_role), never from the workflow stage.

## Step 3: Required behavior

Scope: single comments only. NO replies or threads.

Data model (backend + frontend types):
- id, notification_id, author_role ("1LOD" | "2LOD"), author_id if available, body, created_at, edited_at (nullable), status ("open" | "resolved"), resolved_by_role, resolved_by_id, resolved_at, deleted_at (soft delete).
- Add an append-only audit log table/collection for comment events (created, edited, resolved, reopened, deleted) with actor, role, timestamp, and the previous body on edit/delete. Soft-deleted comments are hidden from the UI but remain in the audit log.

Visibility:
- Both 1LOD and 2LOD see ALL comments (open and resolved) at EVERY workflow stage.

Permissions (enforce on the backend too, return 403 on violation, don't rely on UI hiding):
- Edit: author role only, only while status = open. Show an "edited" tag after editing.
- Delete (soft): author role only, only while status = open. Add a confirm step.
- Resolve: the author OR the other LOD. Anyone who can resolve can reopen.
- Resolved comments are read-only except for Reopen.
- This must hold across hand-offs: if 2LOD wrote a comment, sent it to 1LOD, and 1LOD sends it back without resolving, 2LOD still has Edit, Delete and Resolve.

Display:
- Each comment shows a role badge (1LOD / 2LOD) and timestamp (relative, full timestamp on hover). No individual author name needed.
- Open comments appear at the top, highlighted.
- Resolved comments move into a separate "Resolved (n)" section below, COLLAPSED by default, dimmed and un-highlighted. Expanding shows each with "Resolved by <role> · <time>" and a Reopen button.
- Resolving/reopening updates the UI immediately (optimistic or refetch) with no page refresh. Reopened comments return to the open section and get re-highlighted.

Hand-off:
- When sending a notification forward or back, show "N open comments" in the confirmation. Do NOT block the send.
- Show an open-comment count badge on the notification for the recipient.

## Step 4: Implement
Make backend changes (models/migrations, endpoints, permission checks, audit logging), frontend changes (components, state, permission helper, collapsed Resolved section, badges, confirm dialogs), and update types and API clients. Centralize the permission logic in one function (e.g. canEdit/canDelete/canResolve/canReopen(comment, viewerRole)) and unit test it.

## Step 5: Tests
1. Backend: pytest tests for every permission rule (allowed and 403 cases), soft delete, audit log entries, and visibility across stages.
2. Frontend: unit tests for the permission helper and the open/resolved grouping.
3. END-TO-END with Playwright MCP. Start the backend and frontend, seed (or log in as) a 1LOD user and a 2LOD user in two separate browser contexts/sessions, and actually drive the browser through these scenarios. Take a screenshot at each key step and assert on the DOM:
   a. 1LOD adds a comment. Sees Edit/Resolve/Delete, role badge "1LOD", and timestamp.
   b. 1LOD sends the notification to 2LOD. The confirmation shows "1 open comment". 2LOD sees the comment but NO Edit/Delete. 2LOD does see Resolve.
   c. 2LOD adds their own comment. 2LOD sees Edit/Resolve/Delete on it. 1LOD sees only Resolve on it.
   d. 2LOD sends it back to 1LOD WITHOUT anyone resolving. 1LOD sees both comments. Then 1LOD sends it back to 2LOD again unresolved. 2LOD STILL has Edit/Delete/Resolve on their own comment (this is the original bug, so assert it explicitly).
   e. 2LOD edits their comment: "edited" tag appears; 1LOD sees the updated text after refresh/poll.
   f. 1LOD resolves 2LOD's comment: it leaves the open section, is dimmed, un-highlighted, and lands in a collapsed "Resolved (1)" section. Expand it to verify "Resolved by 1LOD · time" and Reopen. Edit/Delete are absent.
   g. 2LOD reopens it: it returns to the open section, highlighted.
   h. 2LOD deletes their open comment (confirm dialog): it disappears from both views. Verify via API/DB that the audit log still has the original text and the deleted_at/actor.
   i. Negative checks: call the edit/delete API directly as the non-author role and confirm 403.
4. Run everything. If any test fails, fix the code (not the test, unless the test is wrong) and rerun until all pass. Check the browser console for errors during the E2E run.

## Step 6: Deliverables
- All code changes, migrations, and tests committed on a new branch (don't push to main).
- Save the Playwright E2E scenarios as a re-runnable spec in the repo's test directory if the project uses Playwright test; otherwise add a short README section on how to rerun them.
- Final summary (concise): what was changed, the permission matrix as a table, test results (pass/fail counts, including the E2E scenarios a-i), screenshots' locations, and any assumptions or decisions you made.
