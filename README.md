Selection toolbar (floating bubble menu)

When the user selects text in the editor, show a small floating toolbar anchored just above the selection, horizontally centered on it. It disappears when the selection collapses or the user clicks elsewhere.

Container

Single horizontal pill with fully rounded corners (~14px radius), ~44px tall
Dark translucent surface (~
#2a2a2a at 90-95% opacity) with backdrop blur, a 1px subtle border (white at ~8% opacity), and a soft drop shadow
~8px inner padding, ~4px gap between buttons

Buttons (icon-only, ~32px square hit targets, rounded hover state with a faint white background, light gray icons ~
#e5e5e5, 1.5px stroke line icons)

Edit - sparkle/asterisk icon. Opens an inline prompt input or menu to ask AI to rewrite, shorten, expand, or fix the selected text.
Comment - speech bubble outline icon. Opens a comment input anchored to the selection and highlights the selected range.

Behavior

Use mousedown with preventDefault on the buttons so the selection isn’t lost on click
Show a tooltip on hover (“Edit with AI”, “Comment”)
Reposition on scroll and resize; flip below the selection if there’s no room above
Keep the selection highlighted while the edit or comment input is open

Prompt tips

Attach the screenshot and say “match this visual style, but only include the first two buttons.”
Name your editor library if you use one (e.g. “use Tiptap’s BubbleMenu”).
Name your icon set (e.g. Lucide: Sparkles, MessageCircle).
