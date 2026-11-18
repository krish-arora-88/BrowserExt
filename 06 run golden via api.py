"""Step 6: run the Golden Q-A questions through the real chatbot API (real retrieval, KB, system prompt).

Use it once per model: set LLM_MODEL_NAME on the backend, restart it, confirm the startup log line
"LLM model: <name>", then run this script with the matching label.

  python 06_run_golden_via_api.py --model-label "Haiku 5.5"
  python 06_run_golden_via_api.py --model-label "Sonnet 5.5"
  python 06_run_golden_via_api.py --model-label "GPT-5 (baseline)"

Needs in .env (or the environment):
  EIM_BASE_URL         e.g. http://localhost:8000
  Local backend (default): EIM_TEST_USERNAME + EIM_TEST_PASSWORD, used for POST /auth/test/login
                           (needs LOCAL_LOGIN_ENABLED on the backend); the session cookie is reused.
  Other backends:          EIM_API_KEY + EIM_USER_ID, sent as X-API-Key / X-User-Id.

WARNING: the backend treats ANY error while embedding, retrieving or calling the model (a 401 from the
gateway, say) as an escalation with ~0 ms latency. Rows that look like that are flagged "CHECK" and their
Escalated cell is left blank, so a broken key cannot silently inflate the escalation rate.

Each question gets a FRESH session (no chat history carried over). Sessions are kept by default as audit
evidence; add --delete-sessions to remove them. Results go to tables/run_<label>.csv, and (unless
--no-fill) the Answer / Escalated / Notes cells of excel/manual_test_sheet.xlsx are filled for that model.
"""
import argparse
import csv
import os
import re
import time

import openpyxl
import requests

from common import EXCEL_DIR, GOLDEN_XLSX, TABLES_DIR  # importing common also loads ../.env

SHEET = EXCEL_DIR / "manual_test_sheet.xlsx"
ANSWER_OFFSETS = {"Answer": 0, "Escalated (Y/N)": 4, "Notes": 5}  # columns relative to the model's first column


def read_questions():
    ws = openpyxl.load_workbook(GOLDEN_XLSX, read_only=True, data_only=True).worksheets[0]
    return [(int(r[0]), str(r[1]).strip()) for r in ws.iter_rows(min_row=2, values_only=True)
            if r[0] is not None and r[1] and str(r[1]).strip()]


SUSPECT_MS = 1500  # an escalation this fast, with no sources, is almost certainly a swallowed backend error


def make_session():
    """Logged-in requests.Session: test-user login (local) or API-key headers (other backends)."""
    base = os.environ["EIM_BASE_URL"].rstrip("/")
    s = requests.Session()
    if os.environ.get("EIM_TEST_USERNAME"):
        r = s.post(f"{base}/auth/test/login", timeout=30,
                   json={"username": os.environ["EIM_TEST_USERNAME"], "password": os.environ["EIM_TEST_PASSWORD"]})
        r.raise_for_status()
    else:
        s.headers.update({"X-API-Key": os.environ["EIM_API_KEY"], "X-User-Id": os.environ["EIM_USER_ID"]})
    return base, s


def ask(base, http, question, timeout):
    s = http.post(f"{base}/api/v1/sessions", timeout=30)
    s.raise_for_status()
    sid = s.json()["session_id"]
    t0 = time.time()
    r = http.post(f"{base}/api/v1/chat", json={"session_id": sid, "message": question}, timeout=timeout)
    ms = int((time.time() - t0) * 1000)
    r.raise_for_status()
    return sid, ms, r.json()


def fill_sheet(label, results):
    wb = openpyxl.load_workbook(SHEET)
    ws = wb["Run 1"]
    start = next((c.column for c in ws[1] if c.value == label), None)
    if start is None:
        print(f"label {label!r} not found in row 1 of {SHEET.name}; sheet not filled")
        return
    rows = {str(ws.cell(row=r, column=1).value): r for r in range(3, ws.max_row + 1)}
    for res in results:
        r = rows.get(f"Q{res['question_no']:03d}")
        if r is None:
            continue
        ws.cell(row=r, column=start + ANSWER_OFFSETS["Answer"]).value = res["answer"]
        esc = ws.cell(row=r, column=start + ANSWER_OFFSETS["Escalated (Y/N)"])
        note = ws.cell(row=r, column=start + ANSWER_OFFSETS["Notes"])
        if res["suspect_error"]:
            esc.value = None  # decide by hand after checking the backend log
            note.value = f"CHECK: possible backend error treated as escalation ({res['latency_ms']} ms, no sources)"
        else:
            esc.value = "Y" if res["escalation_required"] else "N"
            note.value = f"cited sources: {res['sources']}"
    wb.save(SHEET)
    print("filled", SHEET.name, "for", label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-label", required=True)
    ap.add_argument("--delete-sessions", action="store_true")
    ap.add_argument("--no-fill", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--only", type=int, nargs="*", help="run only these question numbers (e.g. to repeat failures)")
    a = ap.parse_args()

    base, http = make_session()
    qs = [q for q in read_questions() if not a.only or q[0] in a.only]
    print(f"{len(qs)} questions -> {base} as model label {a.model_label!r}")

    results = []
    for no, q in qs:
        try:
            sid, ms, body = ask(base, http, q, a.timeout)
            srcs = "; ".join(f"{s.get('doc_title', '')} > {s.get('section_title', '')}" for s in body.get("sources", []))
            esc = bool(body.get("escalation_required"))
            res = dict(question_no=no, question=q, answer=body["content"], escalation_required=esc,
                       sources=srcs, latency_ms=ms, message_id=body.get("message_id", ""), session_id=sid, error="",
                       suspect_error=esc and not srcs and ms < SUSPECT_MS)
            if a.delete_sessions:
                http.delete(f"{base}/api/v1/sessions/{sid}", timeout=30)
        except Exception as e:  # keep going, record the failure
            res = dict(question_no=no, question=q, answer="", escalation_required=False, sources="", latency_ms=0,
                       message_id="", session_id="", error=str(e), suspect_error=True)
        results.append(res)
        flag = "  <-- CHECK (possible swallowed error)" if res["suspect_error"] else ""
        print(f"Q{no:03d} {res['latency_ms']:>6} ms  escalate={res['escalation_required']}  {res['error']}{flag}")

    out = TABLES_DIR / f"run_{re.sub(r'[^A-Za-z0-9]+', '_', a.model_label).strip('_')}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print("saved", out)
    n_sus = sum(r["suspect_error"] for r in results)
    if n_sus:
        print(f"WARNING: {n_sus} of {len(results)} rows look like backend errors, not real escalations. Check the backend log and the gateway key before trusting any rate.")
    if not a.no_fill and SHEET.exists():
        fill_sheet(a.model_label, results)


if __name__ == "__main__":
    main()
