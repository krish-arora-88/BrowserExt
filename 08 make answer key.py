"""
08_make_answer_key.py - build excel/answer_key.xlsx for hand scoring.

Sheets
  key      one row per question. You fill: answerable (Yes/Partial/No), fact1-4
           (critical facts), and per model: hits (how many of the facts the
           answer states correctly) and contradiction (Y/N). Escalation flags
           are prefilled from the run CSVs. expected_esc, n_facts and
           esc_check are formulas.
  answers  question, reference text, and each model's answer, to read side by
           side while filling `key`.
  summary  formulas: escalations, false escalations, missed escalations,
           fact recall on answered questions, contradictions.

Reference text: Golden Answer for Q36-42, GPT-5 baseline response otherwise
(--golden-preferred to change).

Usage (from Report/):
  python3 code/08_make_answer_key.py \
    --run "GPT-5=tables/run_gpt_5_2025_08_07_eastus_dz.csv" \
    --run "Haiku 5.5=tables/run_us_anthropic_claude_haiku_5_5.csv" \
    --run "Sonnet 5.5=tables/run_us_anthropic_claude_sonnet_5_5.csv"
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from common import GOLDEN_XLSX, EXCEL_DIR
except Exception:
    GOLDEN_XLSX = HERE.parent / "excel" / "Golden Q-A.xlsx"
    EXCEL_DIR = HERE.parent / "excel"


def ranges(text):
    out = set()
    for p in text.split(","):
        p = p.strip()
        if "-" in p:
            a, b = p.split("-")
            out.update(range(int(a), int(b) + 1))
        elif p:
            out.add(int(p))
    return out


def yn(v):
    return "Yes" if str(v).strip().lower() in ("true", "yes", "y", "1") else "No"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--golden", default=str(GOLDEN_XLSX))
    ap.add_argument("--golden-preferred", default="36-42")
    ap.add_argument("--out", default=str(Path(EXCEL_DIR) / "answer_key.xlsx"))
    a = ap.parse_args()

    g = pd.read_excel(a.golden, header=0).iloc[:, :4]
    g.columns = ["no", "question", "golden", "baseline"]
    g = g[pd.to_numeric(g["no"], errors="coerce").notna()].copy()
    g["no"] = g["no"].astype(int)
    pref = ranges(a.golden_preferred)

    runs = {}
    for spec in a.run:
        label, path = spec.split("=", 1)
        df = pd.read_csv(path)
        df["question_no"] = df["question_no"].astype(int)
        runs[label.strip()] = df.set_index("question_no")
    models = list(runs)

    wb = Workbook()
    key = wb.active
    key.title = "key"
    head = ["no", "question", "answerable (Yes/Partial/No)", "expected_esc",
            "fact1", "fact2", "fact3", "fact4", "n_facts"]
    for m in models:
        head += [f"{m} | escalated", f"{m} | hits", f"{m} | contradiction (Y/N)",
                 f"{m} | esc_check"]
    key.append(head)
    for c in key[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(wrap_text=True, vertical="top")

    n = len(g)
    for i, (_, q) in enumerate(g.iterrows(), start=2):
        row = [int(q["no"]), q["question"], None,
               f'=IF(C{i}="","",IF(C{i}="No","Yes","No"))',
               None, None, None, None, f"=COUNTA(E{i}:H{i})"]
        for m in models:
            df = runs[m]
            esc = yn(df.loc[q["no"], "escalation_required"]) if q["no"] in df.index else ""
            ci = 10 + 4 * models.index(m)
            esc_cell = f"{L(ci)}{i}"
            row += [esc, None, None,
                    f'=IF(OR(D{i}="",{esc_cell}=""),"",IF({esc_cell}=D{i},"OK","WRONG"))']
        key.append(row)

    dv_ans = DataValidation(type="list", formula1='"Yes,Partial,No"', allow_blank=True)
    dv_yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    key.add_data_validation(dv_ans)
    key.add_data_validation(dv_yn)
    dv_ans.add(f"C2:C{n+1}")
    for k in range(len(models)):
        dv_yn.add(f"{L(12+4*k)}2:{L(12+4*k)}{n+1}")
    key.freeze_panes = "C2"
    key.column_dimensions["B"].width = 45
    for col in "EFGH":
        key.column_dimensions[col].width = 38

    ans = wb.create_sheet("answers")
    ans.append(["no", "question", "reference used", "reference text"] + models)
    for c in ans[1]:
        c.font = Font(bold=True)
    for _, q in g.iterrows():
        use = "golden" if q["no"] in pref else "baseline"
        ref = q["golden"] if use == "golden" else q["baseline"]
        row = [int(q["no"]), q["question"], use, ref]
        for m in models:
            df = runs[m]
            row.append(str(df.loc[q["no"], "answer"]) if q["no"] in df.index else "")
        ans.append(row)
    for r in ans.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABCD", (6, 40, 12, 70)):
        ans.column_dimensions[col].width = w
    for k in range(len(models)):
        ans.column_dimensions[L(5 + k)].width = 70

    s = wb.create_sheet("summary")
    s.append(["metric"] + models)
    last = n + 1
    labels = ["Escalated", "Answered", "False escalations (answerable, escalated)",
              "Missed escalations (not answerable, answered)",
              "Fact recall on answered, answerable questions",
              "Contradictions (Y)", "Escalation flag correct (OK)", "Questions judged"]
    for lab in labels:
        s.append([lab])
    for k, m in enumerate(models):
        e = f"key!{L(10+4*k)}$2:{L(10+4*k)}${last}"
        h = f"key!{L(11+4*k)}$2:{L(11+4*k)}${last}"
        x = f"key!{L(12+4*k)}$2:{L(12+4*k)}${last}"
        ck = f"key!{L(13+4*k)}$2:{L(13+4*k)}${last}"
        exp = f"key!$D$2:$D${last}"
        nf = f"key!$I$2:$I${last}"
        col = L(2 + k)
        f = [
            f'=COUNTIF({e},"Yes")',
            f'=COUNTIF({e},"No")',
            f'=COUNTIFS({e},"Yes",{exp},"No")',
            f'=COUNTIFS({e},"No",{exp},"Yes")',
            f'=IFERROR(SUMIFS({h},{e},"No",{exp},"No")/SUMIFS({nf},{e},"No",{exp},"No"),"")',
            f'=COUNTIF({x},"Y")',
            f'=COUNTIF({ck},"OK")',
            f'=COUNTIF({exp},"Yes")+COUNTIF({exp},"No")',
        ]
        for r, formula in enumerate(f, start=2):
            s[f"{col}{r}"] = formula
        s[f"{col}6"].number_format = "0.0%"
    s.column_dimensions["A"].width = 52
    for k in range(len(models)):
        s.column_dimensions[L(2 + k)].width = 16

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(a.out)
    print("saved", a.out, f"({n} questions, models: {', '.join(models)})")


if __name__ == "__main__":
    main()
