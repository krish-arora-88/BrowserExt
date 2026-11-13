Implement the re-draft workflow for 1LOD. Read the existing comment, draft, and drafting-agent code first and reuse it. No over-engineering: write only the code this needs. No new tables, wrappers, or util files unless unavoidable.

## Re-draft button (1LOD only)
- Show a "Re-draft" button to 1LOD on the draft view. 2LOD does not get it; 2LOD's existing "send back to 1LOD" flow stays exactly as it is.
- Disabled unless the current draft has at least one unresolved comment, from either 1LOD or 2LOD. Enable/disable reactively as comments are added, resolved, or re-opened.
- While a re-draft is running, disable the button and show a loading state.

## On click
Trigger the existing drafting agent in re-draft mode with this context:
1. The entire current draft (all template fields).
2. Every unresolved comment on that draft, from both 1LOD and 2LOD, and nothing resolved:
   - Targeted comment: the comment text + the exact highlighted passage + the field it sits in.
   - General comment: the comment text only.
   - Label each comment with the role that wrote it.

Add a re-draft instruction to the drafting prompt:
- Change only the parts the comments ask for.
- Return every other field and sentence verbatim, with no rephrasing or reformatting.
- Return the full draft in the same template structure as a normal draft.

Reuse the existing drafting agent call and add the comments as extra context. Do not build a second agent or pipeline.

## After the agent returns
- Save the result as the new current draft using the existing draft-versioning mechanism, keeping the previous version so it can be diffed.
- Do NOT resolve, delete, or modify any comment. Comments stay unresolved until 1LOD or 2LOD manually clicks "Resolve".
- Refresh the draft view.

## Diff button
- Next to the existing "Edit draft" and "Export" buttons, show a "Diff" button to 1LOD and 2LOD, only when the current draft was produced by a re-draft (i.e. a previous version exists).
- Clicking it opens a small modal: old version on the left, new version on the right, field by field.
- Word-level diff: removed text highlighted red on the old side, added text highlighted green on the new side. Unchanged text unstyled.
- Compute the diff on the frontend. Use a diff library already in the project if there is one; otherwise add one small one (e.g. jsdiff). Do not write a diff algorithm.
- Diff always compares the current draft with the one immediately before it.

## Constraints
- Do not change how comments are created, stored, resolved, or re-opened.
- Do not change edit, export, or any 2LOD behaviour.
- No new endpoints beyond one to trigger the re-draft (and one to fetch the previous version, only if the existing draft endpoint can't return it).

## Done when
- 1LOD sees the button; 2LOD does not.
- Button is disabled with zero unresolved comments and enabled with one or more, whether the comment came from 1LOD or 2LOD.
- The agent receives unresolved comments from both roles and no resolved ones.
- A targeted comment on one sentence changes that sentence and leaves the other fields identical.
- After a re-draft, all comments are still unresolved.
- Diff modal shows only the real changes in red/green.
- Tell me which files you changed and why.
