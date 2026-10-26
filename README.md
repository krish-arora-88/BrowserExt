# TASK: Rebuild the LX50 Sensitivity Chatbot backend from scratch

You are working in the `lx50-sensitivity-chatbot` repo (internal RBC tool). The current backend at `src/backend/` is a 3-agent LangGraph ReAct system (Supervisor -> Data Agent -> Analysis Agent). It is slow, non-deterministic, and burns tokens because LLMs do work that plain code should do. Replace it with a simpler, deterministic-first design.

Deliver two things, in this order:
1. An ARCHITECTURE doc at `docs/ARCHITECTURE.md`.
2. A working MVP backend implementing it.

## Step 0: Read before deleting (mandatory)
Read the entire current backend, `main.py`, `config.yaml`, `data/*` (especially `ATOM Payloads.txt`, `ATOM_API_Documentation.md`, `ATOMInterface.py`, `Swagger.json`), `frontend/src/hooks/useSSE.ts`, `frontend/src/types.ts`, `Dockerfile`, `docker-entrypoint.sh`, `configs/`, and `tests/test_smoke.py`. Write a short "behavior spec" of what must be preserved (see Contract below). Then delete the old backend (`git rm -r src/backend`; git history keeps it). Do not port its code. Re-derive the logic.

## Keep (do not touch unless required)
- `frontend/` (the SSE contract below is fixed; frontend must work unchanged)
- `data/` reference Excel files and docs
- `Dockerfile`, `start.sh`, `docker-entrypoint.sh`, `configs/` (Kustomize), `catalog-info.yaml`, `config.yaml` (extend, don't break)
- Env/secrets names: `ATOM_BASE_URL`, `ATOM_USERNAME/PASSWORD`, `LLM_*`, `API_KEY`, `RATE_LIMIT_*` (from `.env` locally, Vault in OCP)
- Deps only from RBC Artifactory (`requirements.txt`); `rbc-security` for certs; Python 3.12

## Design principles
1. **LLM only where language understanding is needed.** Code does everything deterministic: fuzzy matching, ID resolution, payload construction, casing, validation, date formatting, API calls, chart shaping.
2. **No ReAct loops, no multi-agent hops.** One explicit pipeline (state machine) with at most 2 LLM calls per request: (a) structured intent extraction, (b) optional narrative summary. Drop LangGraph unless you can justify it in the doc; plain async Python + Pydantic is preferred.
3. **LLM never writes ATOM payloads.** It outputs a validated `QueryIntent` (Pydantic, JSON-schema constrained / function-calling). A deterministic `PayloadBuilder` produces the payload. Hallucinated field names or schemas become impossible by construction.
4. **No global mutable state.** Replace `PENDING_API_DATA` / `PENDING_CHARTS` globals and contextvar tricks with a per-request `RequestContext` passed explicitly.
5. **Everything swappable behind interfaces** (Protocols): `SessionStore` (in-memory now, Redis later), `LLMClient`, `AtomClient`, `ReferenceData`.
6. **Testable without network.** Every stage is a pure function or takes an injected client. Eval harness runs with a mocked LLM and mocked ATOM.

## Domain rules to encode in code (not in prompts)
ATOM supports exactly TWO endpoints (query schemas). `IRM_Sensitivity_Analytics` is permanently removed; any other schema string must be rejected before the HTTP call.

**IRMComplexAnalytics** (aggregated, keyed by numeric AnalyticsId from `list_of_questions.xlsx`; ANALYTIC_TYPE Complex_Generic / Complex_Query_Based)
- Required FILTERs: `effDate` (capital D), `AnalyticsId` (op IN, numeric string)
- Optional FILTERs: `NodeId` (CAPITAL N), `hierarchy`, `ReportingCurrency` (3-letter code)

**IRMSensitivityDim** (position-level drilldown, keyed by `rmi_measure_id` + `risk_class` from `senstivity RMI flattened file.xlsx`; NEVER AnalyticsId)
- Required FILTERs, in order: `viewMode`="DISCRETE", `effDate`, `hierarchy`, `nodeId` (lowercase n), `rmiFlattened.rmi_measure_id`, `rmiFlattened.risk_class`
- Optional FILTERs: `rmiFlattened.risk_type` (Delta/Gamma/Vega/...), `masterCrif.risk_factor_1_qualifier_1`, `bookHierarchy.sourcebookname`
- FILTER entries come first, then SELECT entries. SELECT entries have only "Query Field" (no operation/value).
- Always SELECT: `position.effdate`, `bookHierarchy.sourcebookname`, `measures.metric_usd_value` (prevents timeouts)
- Dimension -> SELECT mapping (implement as a data table in code):
  - counterparty/party/client -> `position.involvedpartyid`
  - legal entity -> `bookHierarchy.legalentityname`
  - region -> `bookHierarchy.region`
  - sub-book -> `bookHierarchy.sourcesubbookname`
  - currency pair/qualifier -> `masterCrif.risk_factor_1_qualifier_1`
  - tenor/bucket/label -> `masterCrif.risk_factor_1_label_1`
  - risk metric name -> `masterCrif.source_risk_metric_name`
  - position level/drill down -> `position.eodpositionid`, `position.involvedpartyid`
  - buy/sell/direction -> `position.buysellflag`
  - local currency -> `measures.metric_local_value`, `measures.metric_local_value_currency` (replaces usd)
  - both USD and local -> usd + local value + local currency
- The LLM emits dimensions as an enum list (`group_by: [counterparty, tenor, ...]`); code maps enum -> SELECT fields.

Common rules:
- Payload row shape: `{"Query Schema", "Query Field Type": "FILTER"|"SELECT", "Query Field", ["Query Operation", "Query Value"]}`; all Query Values are strings.
- Dates: strict `YYYY-MM-DD`; multi-date requests become multiple calls/rows. If year truly absent, ask the user; "31st mar 2026" is fully specified.
- Hierarchy must match `WORD_NUMBER` (e.g. FINANCE_80000, CREDIT_70000). Never derive from phrases like "eur tier", "us", "cad" (those are analytics qualifiers). Default `FINANCE_80000` when absent and tell the user the default was used.
- AnalyticsId and rmi_measure_id come ONLY from reference-data lookup, never from user text or LLM output.
- Analytics rows with ANALYTIC_TYPE `Hierarchy_Sensitivity` / `Hierarchy_LossMetrics`: re-route to IRMSensitivityDim by searching the sensitivity catalog for the same measure; if no match, ask/inform the user that aggregated sensitivity is unavailable.
- Routing: "positions / drilldown / breakdown / by source book / by counterparty..." => IRMSensitivityDim. Plain named analytics at node level => IRMComplexAnalytics. If a name matches both catalogs, decide using drilldown intent; if still ambiguous, ask.
- Multiple fuzzy matches above threshold and no clear winner => clarification (top N candidates, user picks).

## Pipeline (the architecture to implement)
