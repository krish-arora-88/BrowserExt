# RBCAssist Browser Extension — Build Spec for Devin

**Context:** 5-hour internal RBC hackathon, Friday Oct 23, 2026. Team of 5. This spec covers the **core scope only**. Everything else is explicitly out of scope.

**Goal:** A Chrome/Edge (Manifest v3) extension that opens RBCAssist in a side panel, can read the current tab's content when the user enables it, and offers right-click Explain/Summarize on selected text. Must run against a local mock server when the real RBCAssist endpoint is unavailable.

---

## 1. Scope

### In scope (build in this order)
1. **Extension scaffold** — Manifest v3, loads unpacked in Edge and Chrome without errors.
2. **Side-panel chat** — opens via toolbar button; streams responses; keeps in-session chat history.
3. **"Use this page" toggle** — content script extracts clean readable text from the active tab and includes it as context in the next request.
4. **Right-click selection actions** — context menu on selected text: **Explain** and **Summarize**. Opens the side panel and sends the selected text with the chosen action.
5. **Mock backend** — small Node server that streams canned/echo responses so the UI can be built and demoed without RBCAssist access.
6. **Pluggable endpoint config** — switching from mock to real RBCAssist is a config change (URL + auth header), not a code change.

### Out of scope (do not build, do not stub)
- Email, calendar, browsing-history integration
- `@tab` mentions / multi-tab context
- Extract tables to CSV
- Inline rewrite/complete buttons inside email or ticket systems
- Rewrite / Translate context-menu actions
- Smart citations that jump to page passages
- Omnibox keyword search
- Persistent chat history across browser restarts
- Any login/SSO flow beyond a static bearer token in config

---

## 2. Hard constraints

- **No external AI APIs.** No OpenAI, Anthropic, Gemini, etc. The only outbound call is to the configured endpoint (mock or RBCAssist).
- **Minimal permissions.** Do **not** request `tabs`, `history`, `<all_urls>` host permission, `webRequest`, or `cookies`. Use `activeTab` + `scripting` for page reads.
- **No build step required to load.** Plain HTML/CSS/JS. No bundler, no framework, no npm packages in the extension itself. (npm may be blocked on corporate machines.) The mock server may use Node built-ins only (`http`), no dependencies.
- **Page content is only read when the user has toggled "Use this page" on, or when they explicitly right-click a selection.** Never read page content silently.
- **Keep everything in one repo folder** so it can be zipped and shared.

---

## 3. Repository layout

```
rbcassist-extension/
├── README.md                  # how to load, run mock, switch endpoint
├── extension/
│   ├── manifest.json
│   ├── config.js              # endpoint URL, auth token, model name
│   ├── background.js          # service worker: side panel, context menus, messaging
│   ├── content.js             # page text extraction
│   ├── sidepanel/
│   │   ├── sidepanel.html
│   │   ├── sidepanel.css
│   │   └── sidepanel.js       # chat UI, streaming, toggle, history
│   └── icons/
│       ├── icon16.png
│       ├── icon48.png
│       └── icon128.png        # any placeholder PNGs are fine
└── mock-server/
    └── server.js              # Node http server, streams fake responses
```

---

## 4. `manifest.json`

```json
{
  "manifest_version": 3,
  "name": "RBCAssist",
  "version": "0.1.0",
  "description": "Ask RBCAssist about the page you're on, without leaving the tab.",
  "permissions": ["sidePanel", "activeTab", "scripting", "storage", "contextMenus"],
  "host_permissions": ["http://localhost:8787/*"],
  "background": { "service_worker": "background.js", "type": "module" },
  "action": { "default_title": "Open RBCAssist" },
  "side_panel": { "default_path": "sidepanel/sidepanel.html" },
  "icons": { "16": "icons/icon16.png", "48": "icons/icon48.png", "128": "icons/icon128.png" }
}
```

- When the real RBCAssist URL is known, add its origin to `host_permissions` (e.g. `"https://rbcassist.internal.rbc.com/*"`). Document this in README.
- `content.js` is **not** declared as a static content script. It is injected on demand with `chrome.scripting.executeScript` so it only runs when the user asks.

---

## 5. `config.js`

```js
// Single place to switch between mock and real RBCAssist.
export const CONFIG = {
  endpoint: "http://localhost:8787/chat",   // replace with RBCAssist chat endpoint
  authHeader: "Authorization",               // header name for auth
  authToken: "",                             // e.g. "Bearer <token>"; empty for mock
  streaming: true,                           // false = wait for full JSON response
  maxPageChars: 12000,                       // truncate extracted page text
  systemPrompt: "You are RBCAssist, an internal assistant for RBC employees. Answer using the provided page context when available. Be concise."
};
```

`sidepanel.js` imports this. `background.js` imports it for host checks. Nothing else hardcodes a URL.

---

## 6. `background.js` (service worker)

Responsibilities:
1. **Open side panel on toolbar click**
   ```js
   chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
   ```
2. **Register context menus on install**
   - `rbcassist-explain` — title "RBCAssist: Explain", contexts `["selection"]`
   - `rbcassist-summarize` — title "RBCAssist: Summarize", contexts `["selection"]`
3. **On context-menu click**
   - Open the side panel for that tab: `chrome.sidePanel.open({ tabId: tab.id })`
   - Store the pending action in `chrome.storage.session`:
     `{ pendingAction: { type: "explain" | "summarize", text: info.selectionText, url: tab.url, title: tab.title } }`
   - The side panel reads this on load and on `chrome.storage.onChanged`, then sends the request and clears the key.
4. **Handle `GET_PAGE_CONTENT` messages from the side panel**
   - Query the active tab in the current window.
   - `chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] })`
   - Then `chrome.tabs.sendMessage(tabId, { type: "EXTRACT" })` and return `{ title, url, text }` to the side panel.
   - If injection fails (e.g. `chrome://`, Edge internal pages, PDF viewer), return `{ error: "Cannot read this page." }`.

Note: because `content.js` is injected on demand, guard it so re-injection is a no-op (e.g. `if (window.__rbcassistInjected) return;`).

---

## 7. `content.js` (page extraction)

Readability-style extraction **without external libraries**:

1. Clone `document.body`.
2. Remove `script, style, noscript, nav, header, footer, aside, iframe, svg, form, [role="navigation"], [role="banner"], [role="contentinfo"], [aria-hidden="true"]`.
3. Prefer, in order: `<main>`, `<article>`, `[role="main"]`, then the largest `<div>` by `innerText.length`; fall back to `body`.
4. Return `innerText`, collapse runs of whitespace/blank lines, trim.
5. Respond to `{ type: "EXTRACT" }` with `{ title: document.title, url: location.href, text }`.

Truncation to `maxPageChars` happens in the side panel, not here.

---

## 8. Side panel

### `sidepanel.html` layout (top to bottom)
- Header: "RBCAssist" + small status pill (`Mock` or `Live`, from CONFIG)
- Context bar:
  - Toggle switch **"Use this page"** (off by default)
  - When on: shows the page title and a character count of extracted text; a "Refresh" link re-extracts
  - When extraction fails: show the error inline, toggle resets to off
- Message list (scrollable): user bubbles right, assistant bubbles left; assistant bubbles render plain text with line breaks (no HTML injection — use `textContent`)
- Composer: textarea (Enter sends, Shift+Enter newline), Send button, "Clear chat" link

### `sidepanel.js` behavior
- **Session history:** `messages = [{ role, content }]` in memory; also mirror to `chrome.storage.session` so reopening the panel in the same browser session restores it.
- **Toggle on:** send `GET_PAGE_CONTENT` to background; cache the result as `pageContext`. Re-extract when the active tab changes (`chrome.tabs.onActivated` is not available without `tabs` permission, so instead re-extract on **send** if the toggle is on — simpler and permission-free).
- **Send:**
  1. Push user message to `messages`.
  2. Build request body (see §9). If toggle on, truncate `pageContext.text` to `CONFIG.maxPageChars` and include it.
  3. POST to `CONFIG.endpoint` with `Content-Type: application/json` and, if `authToken` set, `[authHeader]: authToken`.
  4. If `CONFIG.streaming`, read the response body with a `ReadableStream` reader, append chunks to the current assistant bubble as they arrive. Otherwise parse JSON and render `reply`.
  5. On finish, push the assistant message to `messages`.
  6. On error, render an assistant bubble: "Couldn't reach RBCAssist: <message>".
- **Pending context-menu action:** on load and on `storage.onChanged`, if `pendingAction` exists, construct the user message:
  - explain → `Explain this:\n\n"<text>"`
  - summarize → `Summarize this:\n\n"<text>"`
  - Include `{ title, url }` of the source tab as context, then send as a normal message and clear `pendingAction`.
- Disable Send while a request is in flight; show a typing indicator.

---

## 9. Request / response contract

The extension speaks one contract. The mock server implements it. Adapting to RBCAssist's real schema should only require changing a `buildRequest()` and `parseChunk()` function in `sidepanel.js`.

**Request** `POST {endpoint}`
```json
{
  "messages": [
    { "role": "system", "content": "<CONFIG.systemPrompt>" },
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." },
    { "role": "user", "content": "<latest message>" }
  ],
  "context": {
    "page": { "title": "...", "url": "...", "text": "<truncated extracted text>" }
  },
  "stream": true
}
```
`context.page` is omitted when the toggle is off.

**Streaming response** — `Content-Type: text/event-stream`, one event per line:
```
data: {"delta":"Hello"}
data: {"delta":" there"}
data: {"done":true}
```

**Non-streaming response** — `application/json`:
```json
{ "reply": "full answer text" }
```

Keep `buildRequest()` and `parseChunk()` isolated with a comment: `// ADAPT HERE for RBCAssist schema`.

---

## 10. `mock-server/server.js`

- Node built-ins only. `node mock-server/server.js` starts on port 8787.
- Handle `OPTIONS` with CORS headers: `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers: Content-Type, Authorization`, `Access-Control-Allow-Methods: POST, OPTIONS`.
- `POST /chat`:
  - Parse body. Let `last` = last user message, `page` = `context.page` if present.
  - Compose a canned reply that **proves the plumbing works**, e.g.:
    - If `page` present: `"(Mock) I can see the page "${page.title}" (${page.text.length} chars). You asked: "${last}". Here is a sample answer based on the first lines: ${page.text.slice(0,200)}..."`
    - If `last` starts with "Explain this": `"(Mock) Explanation of the selected text: ..."` + first 150 chars of the selection.
    - If `last` starts with "Summarize this": `"(Mock) Summary: ..."` + first 150 chars.
    - Else: `"(Mock) You said: ${last}. The real RBCAssist endpoint will respond here."`
  - If `stream` true: send as SSE, 3-6 words per chunk, ~60 ms apart, then `{"done":true}`.
  - Else: respond `{ reply }` as JSON.
- Log each request's `messages.length` and whether page context was included, so the team can see it working in the terminal during the demo.

---

## 11. README.md must include

1. **Load the extension:** open `edge://extensions` (or `chrome://extensions`) → enable Developer mode → Load unpacked → select `extension/`.
2. **Run the mock:** `node mock-server/server.js`.
3. **Use it:** click the toolbar icon → side panel opens → type a message. Toggle "Use this page" on a normal web page and ask "What is this page about?". Select text on a page → right-click → RBCAssist: Summarize.
4. **Switch to real RBCAssist:** edit `config.js` (`endpoint`, `authToken`, `streaming`), add the endpoint origin to `host_permissions` in `manifest.json`, adjust `buildRequest()`/`parseChunk()` if the schema differs, reload the extension.
5. **Known limitations:** cannot read `chrome://`/`edge://` pages, the extension store, or PDF viewer; history is per-browser-session only.

---

## 12. Acceptance criteria (demo checklist)

- [ ] Loads unpacked in Edge **and** Chrome with zero errors on `edge://extensions` / `chrome://extensions`.
- [ ] Toolbar click opens the side panel.
- [ ] Sending a message shows the user bubble, a typing indicator, then a streamed assistant reply from the mock.
- [ ] Chat history persists while the panel is closed and reopened in the same session; "Clear chat" empties it.
- [ ] "Use this page" on a Confluence page / news article shows the page title + char count; the mock reply quotes the page title and first lines.
- [ ] "Use this page" on `edge://extensions` shows a friendly error and the toggle resets.
- [ ] Selecting text → right-click → "RBCAssist: Explain" opens the panel and sends the selection with the Explain prefix; same for Summarize.
- [ ] Changing `config.js` endpoint to a bad URL produces the "Couldn't reach RBCAssist" bubble, not a crash.
- [ ] No permissions beyond those listed in §4.
- [ ] Whole project zips to a single folder and runs on another machine with only Node installed.

---

## 13. Suggested build order and time boxes (5 h total)

| Time | Deliverable |
|---|---|
| 0:00–0:30 | Repo layout, manifest, placeholder icons, side panel opens, mock server streams "hello" |
| 0:30–1:30 | Chat UI + streaming + session history |
| 1:30–2:30 | `content.js` extraction + "Use this page" toggle end-to-end against mock |
| 2:30–3:15 | Context menus → side panel → auto-send |
| 3:15–3:45 | Error handling, status pill, Clear chat, README |
| 3:45–4:15 | Swap in real RBCAssist if credentials are available; otherwise polish mock replies for demo |
| 4:15–5:00 | Run the acceptance checklist on a clean machine; fix; freeze code |

---

## 14. Demo script (3 minutes)

1. Open a Confluence page. Click the RBCAssist icon → side panel appears beside the page. *"Same assistant, no separate tab."*
2. Ask "What are the open work items on this page?" with the toggle **off** → generic reply. Toggle **on** → reply references the page. *"It only reads the page when you tell it to."*
3. Open a long article, select a dense paragraph → right-click → Summarize. *"Two clicks, no copy-paste."*
4. Show `config.js`: *"Pointing this at RBCAssist is one URL and one token; the security and data-handling review is the next step."*
5. Close with Phase 2 list (multi-tab, citations, email/ticket drafting) on the Solution Whiteboard.
