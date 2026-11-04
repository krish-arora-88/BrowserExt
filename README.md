Refine the Anchor AI incident app UI. Keep the existing structure, layout and all functionality. Apply these changes consistently:

HARD CONSTRAINT: The Notification Draft is the official template exported to Word as-is. Do not change its styling, colours, fields, order, column widths or markup in any way. Treat it as fixed. All improvements happen AROUND it, never inside it.

PALETTE SOURCE: Use the company's official colour palette defined in Preferences.tps. Read that file first and map every colour token in the app chrome to it. Do not invent or hard-code colours that aren't in the file.

1. Design system: all app chrome colours come from Preferences.tps — neutrals, the primary accent, and the semantic red/amber/green equivalents. If the palette lacks a semantic colour, choose the closest match from the file and note the mapping in a comment. All status, priority and criteria indicators use one pill component with consistent semantics: Critical/Significant = palette red, High = palette amber, Elevated = palette neutral; Met = palette green check, Not met = palette grey dash. Never use red for success.

2. Incident list: priority as a coloured pill beside the status pill. Sort Needs Action by priority. Move rows with no title/entity into a collapsed "Incomplete data (n)" group instead of showing "Untitled / —". Add counts and a visible label to the "Waiting on" filter chips.

3. Draft layout: the draft gets the full width of the content column; comments never shrink it. Add a small sticky toolbar above it. Rename "Download Word" to a secondary button labelled "Export" with a download icon. Edit mode form fields stay in the app's own style; disable Export while there are unsaved edits.

4. Comments: all comment UI lives in the right-hand rail, which currently sits mostly empty. The rail is a fixed-width column (~360px) beside the draft, scrolling in sync with it. Each inline comment is a numbered marker in the draft with a matching numbered card in the rail (author, role, timestamp, quoted text); clicking either scrolls to the other. The "add comment" popover is a light card in the rail, aligned to the selection, themed to the current light/dark mode — never a dark box floating over the draft. Remove the duplicated "Sent back by 2LOD" block; show it once as a banner above the decision section.

5. Decision bar: sticky footer in the content column. One disabled style for all buttons. Reject = destructive outline, Approve = the single filled primary, Send feedback = secondary. Show the "reason required" hint only when Reject is attempted without a note.

6. Detail header: keep INC number, title, pills and stepper; add a compact meta row (incident manager, occurred, last updated). Do not change how times or time zones are displayed or handled anywhere.

7. Sidebar: meaningful icons for each nav item and a clear active state. Keep "Acting as" and Appearance exactly where they are (dev environment), unchanged and with no added tags.

8. Typography/spacing: 8px scale, 13–14px body, 44px min touch targets, consistent radius, 1px borders over shadows, full dark-mode parity for the app chrome (the template stays unchanged in both themes).
