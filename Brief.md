## Decisions (Nov 18)
3. kb.view goes to both MBO and MDO (identical permissions). The MDO performs the audit review.
5. Rating set stays Correct / Incomplete / Has Errors / Unclear.
6. Deleting the accepted feedback version removes the correction from the chatbot. If other versions remain, the message returns to unresolved. No automatic fallback.
7. Add a KB draft status: upload -> draft -> (submit) -> pending -> approve. bulk_seed_kb.py's upload-then-approve path must keep working.
8. Export uses confident / not confident (the stored boolean). No high/medium/low.
9. Escalation rate = escalations a user submitted with the Escalate button / sessions, whether or not the AI flagged the answer. The AI flag alone counts only in the AI confidence tile. Messages per session = user messages / sessions (existing backend metric).
11. Golden Q&A: manual "Run now" only, runs stored so a scheduler can be added later.
12. KB review tile shows the last published date.
13. Add Vitest in its own PR before the screen slices. [confirm with Satish]

## Still open (do not guess; keep current behaviour and keep values in config)
1. 1st and 2nd line: one role or two.
2. Entra group to role mapping and the groups claim. Blocks the roles slice.
4. Corrections live immediately or after 2nd-line approval.
10. Owner of the performance targets, and whether they change without a release.
11. Golden Q&A run frequency, and target 90% vs 85%.
