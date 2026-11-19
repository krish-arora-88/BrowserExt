Repo: nfr0-eim-ai-chatbot. Work in the branch that is currently checked out. Do not create or switch branches. Do not commit, push or open a PR until I explicitly tell you to; leave all your changes uncommitted in the working tree. Do not stash, reset, restore or otherwise discard any uncommitted changes that are already there, since some of them are my own work in progress.

Read docs/admin-portal/BRIEF.md and AUDIT.md first, then the roles and capability code from slice 1.

Change of plan: login moves from SAML SSO to LDAP username and password until SSO is ready. ldap_auth_service.py at the repo root (untracked) is the reference my manager supplied for how the LDAP check works: bind over LDAPS to the global catalog as username@domain across the forest's domains, then read givenName, sn, mail and memberOf. Read it fully before designing anything.

Goal: the first page is a login form. Nothing else in the UI or the backend API is reachable without a logged-in session. AD groups returned by LDAP decide the user's role.

Build:
1. Auth mode. Add a config setting for the auth mode (saml | ldap | local). Keep the SAML code path intact and switchable; do not delete it. LDAP_ENV selects the forest; we will run it as prod in every environment for now.
2. LDAP verification inside the backend, as a module behind a small interface (verify(username, password) -> identity with groups), not as a second deployed service. Port the reference script's logic, with these changes:
   - Distinguish "wrong credentials" (401) from "LDAP unreachable, timeout or TLS failure" (503, logged as an error). The reference returns 401 for both.
   - Return full group DNs, not only CNs.
   - Keep certificate validation required. Keep the empty-password rejection.
   - Never log passwords or request bodies. Hold the password in a type that does not print (for example SecretStr) and make sure validation errors and exception logs cannot contain it.
3. Login endpoint: verifies through that module, then creates the same server-side session the SAML flow creates today, storing name, email and resolved role. Rate-limit it per username and per client address. Logout and the existing idle timeout must keep working.
4. Role mapping: a config map of AD group DN -> role, feeding the slice 1 capability map unchanged. Several groups may map to the same role (there will be four AD groups and three roles). A user with no mapped group can log in and use the chat but has no admin capabilities. Add a separate allowlist of username -> role that is honoured only when the environment is not prod, so the portal can be tested before the AD groups exist. The existing SAML_ADMIN_GROUP_ID behaviour must not grant access in ldap mode.
5. Default-deny on the backend: every route requires a logged-in session, except the login endpoint, the auth-config endpoint, health checks and the static files the login page needs.
   - KB routes (/kb/upload, /kb/approve, catalogue and content) accept a session only, with the slice 1 capability checks. Remove KB_API_KEY authentication from them. scripts/bulk_seed_kb.py is a retired setup utility: do not adapt it to the new auth, do not delete it, and note in your report that it no longer works.
   - Do not change API-key access on any non-KB route. List every route that still accepts an API key in your report so I can decide on them.
   - Add a test that walks every registered route and fails if one is reachable without credentials and is not on that explicit allowlist.
6. Frontend: in ldap mode LoginPage shows a username and password form; every other route redirects to /login when there is no session; show distinct messages for wrong credentials and for "sign-in service unavailable".

Tests: fake the LDAP module (no real directory in tests). Cover success, wrong password, LDAP down returns 503, empty password, no mapped group, each role, two groups mapping to one role, the non-prod allowlist being ignored in prod, and the route walk in step 5.

Rules:
- Only this slice. If something outside it looks wrong, note it in your report and leave it.
- system_prompt.txt and config.py have uncommitted changes of mine. Do not touch system_prompt.txt. If you need to add settings to config.py, tell me what you want to add and wait for my go-ahead before editing it.
- Keep LDAP hostnames and domains in config only. No credentials in code, tests or logs.
- If the reference script and the existing session code conflict, or a requirement is ambiguous, ask me before building.

When you are done, stop and send me a report instead of committing:
- every file you created, changed or deleted, with one line on why;
- what you changed in slice 1's code and why;
- the lint, type-check and test commands you ran and their results;
- what you could not verify (you cannot reach the real LDAP from your environment) and the exact manual checks I need to run against the real directory;
- the routes that still accept an API key;
- a proposed commit message, for me to approve.
