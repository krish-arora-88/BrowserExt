Refine the Anchor AI incident app UI. Keep the existing structure, layout and all functionality. Work through the phases in order; do not start a phase until the previous one is complete.

HARD CONSTRAINTS (apply to every phase)
- The Notification Draft is the official template exported to Word as-is. Do not change its styling, colours, fields, order, column widths or markup. All improvements happen AROUND it.
- Colours: use ONLY the 5 colours defined in Preferences.tps. Read that file first. Derive tints/shades of those 5 for backgrounds, borders and dark mode, but never introduce a sixth hue. Never hard-code a colour not derived from the file.
- Do not change how times or time zones are displayed or handled.
- Keep "Acting as" and Appearance in the sidebar exactly as they are, no added tags.

PHASE 0 — Design standard (reference only, no code)
The app follows Apple's Human Interface Guidelines. You cannot fetch the web, so apply these HIG principles from memory and from this summary:
- Clarity: text legible at every size, icons precise, decoration subordinate to content. Negative space is a tool, not waste.
- Deference: the UI frames content; it never competes with it. Thin bars, translucent or flat surfaces, restrained ornamentation.
- Depth: layering and motion convey hierarchy. Modals and popovers sit on a distinct layer; everything else is flat.
- Hierarchy via typography and weight, not colour. System font stack (SF Pro / -apple-system), a small type scale (11/13/15/17/22/28), semibold for emphasis.
- One primary action per view. Secondary actions are plain or bordered. Destructive actions are red text or outline, never filled unless confirming.
- Controls: 44pt minimum hit target, 8pt spacing grid, consistent corner radius (8–12pt), 1pt hairline separators over shadows.
- Lists: leading content, trailing metadata and chevron, subtle row separators, no zebra striping.
- Feedback is immediate and inline; empty and loading states are designed, not blank.
- Dark mode is a first-class appearance with the same hierarchy, not an inverted palette.
Avoid anything that reads as generic AI output: gradients, glassmorphism, drop shadows on cards, oversized rounded corners, emoji in UI, multiple accent colours, decorative illustrations.

PHASE 1 — Tokens
Create a single token layer (CSS variables or equivalent): the 5 palette colours plus derived tints/shades, semantic roles (surface, border, text-primary, text-secondary, accent, danger, warning, success — each mapped to one of the 5 or a derivative), type scale, spacing scale, radius, and light/dark values. Replace every hard-coded colour, font size and spacing in the app chrome with these tokens. Comment any semantic role whose mapping was a judgement call.

PHASE 2 — Shared components
Build or refactor: one Pill component (status, priority, criteria) with consistent semantics — Critical/Significant = danger, High = warning, Elevated = neutral; Met = success check, Not met = neutral dash; never red for success. One Button component with primary / secondary / destructive-outline variants and a single disabled style. One Card and one list Row component following the HIG list pattern.

PHASE 3 — Incident list
Priority pill beside the status pill. Sort Needs Action by priority. Rows with no title/entity collapsed into an "Incomplete data (n)" group instead of showing "Untitled / —". "Waiting on" filter gets a visible label and per-chip counts.

PHASE 4 — Incident detail
Header: keep INC number, title, pills, stepper; add a compact meta row (incident manager, occurred, last updated).
Draft: full width of the content column; comments never shrink it. Sticky toolbar above it. Rename "Download Word" to a secondary "Export" button with a download icon; disable while there are unsaved edits. Edit-mode fields use app tokens, not template styling.
Comments: all comment UI lives in the right rail (~360px fixed, scrolls with the draft). Numbered marker in the draft ↔ numbered card in the rail (author, role, timestamp, quoted text); clicking either scrolls to the other. The add-comment popover is a light card in the rail aligned to the selection, themed to the current mode — never a dark box over the draft. "Sent back by 2LOD" appears once, as a banner above the decision section.
Decision bar: sticky footer; Reject = destructive outline, Approve = the single filled primary, Send feedback = secondary; "reason required" hint only when Reject is attempted without a note.

PHASE 5 — Sidebar and polish
Meaningful icon per nav item, clear active state. Full dark-mode pass over all app chrome using Phase 1 tokens (template unchanged in both modes). Final sweep: 44pt targets, 8pt grid, hairline borders, no shadows on cards, no stray colours.
