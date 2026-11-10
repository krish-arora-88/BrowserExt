TASK: Consolidate review-comment storage and make re-drafting use all unresolved comments.

CONTEXT
This repo is Anchor AI (FastAPI backend, Postgres schema anchor_ai_dev). Reviewers (1LOD, 2LOD) leave comments on a notification draft, then send it back; a Postgres trigger then makes the workflow engine re-run the drafting agent with the feedback. Today comments are stored in three overlapping places and the re-draft does not reliably get them:
- tables incident_comments and incident_comment_events (one row per comment / per event)
- incidents.lod1_comments, incidents.lod2_comments (jsonb)
- incidents.lod1_feedback, incidents.lod2_feedback (text)
We want one store, no row-per-comment tables, and exactly one re-draft per send-back that sees every unresolved comment.

THE DESIGN (fixed; do not redesign or propose alternatives)

1. Source of truth: incidents.lod1_comments and incidents.lod2_comments, each a jsonb array of comment objects. A top-level comment goes in the array of its author's role. A reply goes in the same array as its parent, whoever wrote it.

2. Comment object, exactly these keys:
   id (uuid), author_role, author_id, body, anchor (same structure incident_comments.anchor uses today), reply_to (parent comment id or null), stage (template_stage of the draft being commented on), status ("open" | "resolved"), created_at, edited_at, resolved_by_role, resolved_by_id, resolved_at
   Use the same role strings the existing comment code already uses. Status is only meaningful on top-level comments; a reply follows its parent.

3. Writes:
   - Create: one atomic statement, column = COALESCE(column, '[]'::jsonb) || new_object. No read-modify-write in Python.
   - Edit, resolve, reopen, delete: one transaction with SELECT ... FOR UPDATE on the incident row, then rewrite the array. Delete removes the object (and its replies).
   - Resolve sets status "resolved" and fills resolved_by_role, resolved_by_id, resolved_at. Reopen sets status "open" and nulls those three.
   - Every one of these writes one audit_logs row through the existing audit_log function: action comment_created / comment_edited / comment_resolved / comment_reopened / comment_deleted, details containing comment_id, plus previous_body for edit and delete.
   - None of these may start a re-draft or change lodN_approval_status.
   - Only the resolve and reopen actions ever change a comment's status. Nothing else does, including send-back and the re-draft.

4. Send-back (the existing 1LOD/2LOD "feedback" decision, currently _record_review_decision or wherever it now lives). In one transaction:
   a. Collect every top-level comment with status "open" from BOTH arrays, with its replies. Resolved comments are never sent to the agent.
   b. Build plain text in this format and write it to the acting level's lodN_feedback column (overwrite):

      Reviewer note: <free-text feedback from the request, omit line if none>

      Unresolved comments to address:
      1. [<author_role>] On "<anchored text>": <body>
         - reply [<author_role>]: <body>

   c. Set lodN_approval_status / timestamp exactly as today, so the existing trigger fires once.

5. Re-draft: the drafting agent must receive incidents.lodN_feedback as the `feedback` argument it already accepts. Change nothing in the agent prompt or tools unless this path is broken; if it is broken, fix only what stops the text from step 4 reaching the agent.

6. Existing comment API routes (including resolve and reopen): keep URLs and request/response shapes identical so neither frontend changes. Only swap their storage to the jsonb arrays.

7. Remove incident_comments and incident_comment_events: drop them in the DB setup/migration SQL and delete every model, service, router and import that exists only for them. Do not migrate their rows.

OUT OF SCOPE (do not touch)
Any other table or column; template_details; changing the trigger to fire on updated_at; workflow states or transitions; role/permission rules; frontend code; refactors, renames, or cleanup outside the files this task requires. Do not add new tables, new files, wrapper classes, or helper functions used only once. Prefer deleting code to adding it.

STEP 1: READ, THEN POST A PLAN AND STOP
Read: the DB setup SQL (schema and all triggers), the incident/review router containing the approve/feedback endpoints, every file referencing incident_comments, incident_comment_events, lod1_comments, lod2_comments, lod1_feedback or lod2_feedback, the workflow service and listener, and the drafting service.
Then post a plan containing only:
- The list of files you will change or delete, one line each saying what changes.
- What currently writes and reads lodN_comments, and in what shape.
- Whether any trigger fires a workflow event when lodN_comments or lodN_feedback alone changes (it must not; if it does, state the minimal condition change).
- Whether the re-draft path currently passes lodN_feedback to the agent, and if not, the exact break.
- Anything in the repo that conflicts with the design above. If something conflicts, do not pick a workaround; describe it and ask.
Do not write code until I reply "go".

STEP 2: IMPLEMENT EXACTLY THE APPROVED PLAN

DONE WHEN
- Posting 3 comments as 1LOD creates 3 objects in lod1_comments, 3 audit_logs rows, and no re-draft.
- A 2LOD reply to a 1LOD comment lands in lod1_comments with author_role 2LOD and reply_to set.
- With 2 open and 1 resolved comment, one send-back writes only the 2 open ones into lodN_feedback, changes no comment's status, and triggers exactly one re-draft whose agent input contains that text.
- Re-opening the resolved comment and sending back again includes all 3.
- incident_comments and incident_comment_events no longer exist in the schema or anywhere in the code.
- Existing tests pass; add tests only for the four behaviours above.
In your final message, list what you changed, what you verified by running it, and anything you could not verify.
