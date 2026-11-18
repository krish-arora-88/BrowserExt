"""
07_compare_runs.py - compare golden-question runs (script 06 CSVs) against the
SME-preferred reference answer and against each other.

Reference answer per question (SME decision, Nov 2026):
  Q36-Q42 -> Golden Answer (column C)
  all other questions -> Updated Chatbot Response, the GPT-5 baseline (column D)
Change with --golden-preferred "36-42" (ranges and commas allowed).

What it computes
  1. Escalation: per model, agreement with the baseline flag (column F), and
     a matrix of which questions each model escalated.
  2. Similarity to the reference: ADA-002 cosine (same model as column H),
     ROUGE-L F1 and token F1. Escalated canned answers are excluded, because
     the canned text says nothing about content.
  3. Paired comparison vs a comparator run (default: the GPT-5 run on the new
     KB): mean / median difference, bootstrap 95% CI, non-inferiority against
     a margin, Wilcoxon signed-rank (if scipy is installed), win/tie/loss.
  4. Citations: every [Source N] must point to a returned source.
  5. Knowledge base drift: document titles the baseline cited (column E) that
     never appear in any new run's sources. Missing documents mean the new KB
     is not the baseline KB.
  6. Latency: median, p90, max per model.

Usage (from Report/):
  python3 code/07_compare_runs.py \
      --run "GPT-5=tables/run_gpt_5_2025_08_07_eastus_dz.csv" \
      --run "Haiku 5.5=tables/run_us_anthropic_claude_haiku_5_5.csv" \
      --run "Sonnet 5.5=tables/run_us_anthropic_claude_sonnet_5_5.csv" \
      --comparator "GPT-5"
  Add --no-embed to skip cosine (no gateway calls).

Embedding config (env or Report/.env): EMBED_API_KEY, EMBED_BASE_URL,
EMBED_MODEL (default text-embedding-ada-002-eastus-dz). Falls back to
OPENAI_API_KEY / OPENAI_BASE_URL. Embeddings are cached in
tables/embed_cache.json so reruns are free.

Output: excel/run_comparison.xlsx (sheets: summary, per_question,
escalation, missing_docs, paired) and a printed summary.
"""
import argparse, hashlib, json, os, random, re, statistics, sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from common import GOLDEN_XLSX, EXCEL_DIR  # same paths as scripts 01-06
except Exception:  # standalone fallback
    GOLDEN_XLSX = HERE.parent / "excel" / "Golden Q-A.xlsx"
    EXCEL_DIR = HERE.parent / "excel"
TABLES_DIR = HERE.parent / "tables"

CANNED = "lacks confidence in its answer"
CITE_RE = re.compile(r"\[Source\s+(\d+)\]", re.I)


# ---------- helpers ----------
def parse_ranges(text):
    out = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def to_bool(v):
    return str(v).strip().lower() in ("true", "yes", "y", "1")


def tokens(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


def token_f1(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return None
    common = {}
    for t in ta:
        common[t] = common.get(t, 0) + 1
    hits = 0
    for t in tb:
        if common.get(t, 0) > 0:
            hits += 1
            common[t] -= 1
    if hits == 0:
        return 0.0
    p, r = hits / len(tb), hits / len(ta)
    return 2 * p * r / (p + r)


def rouge_l(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return None
    prev = [0] * (len(tb) + 1)
    for x in ta:  # LCS by dynamic programming, O(n*m) memory-light
        cur = [0]
        for j, y in enumerate(tb, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    lcs = prev[-1]
    if lcs == 0:
        return 0.0
    p, r = lcs / len(tb), lcs / len(ta)
    return 2 * p * r / (p + r)


def doc_key(title):
    """Normalise a source title to its document name."""
    t = str(title)
    t = re.sub(r"^\s*Source\s*\d+\s*:\s*", "", t, flags=re.I)
    for sep in (" | RBC Connect", " > ", " — ", " – ", " - "):
        t = t.split(sep)[0]
    t = re.sub(r"\(updated[^)]*\)", "", t, flags=re.I)  # ignore FAQ version
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def split_sources(cell):
    if not isinstance(cell, str) or not cell.strip():
        return []
    parts = re.split(r";\s|\n", cell)
    return [p.strip() for p in parts if p.strip()]


def citation_check(answer, sources):
    cited = [int(n) for n in CITE_RE.findall(str(answer))]
    if not cited:
        return 0, True
    return len(cited), max(cited) <= len(sources)


# ---------- embeddings ----------
class Embedder:
    def __init__(self, cache_path):
        self.cache_path = cache_path
        self.cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        try:
            from dotenv import load_dotenv
            load_dotenv(HERE.parent / ".env")
        except Exception:
            pass
        from openai import OpenAI
        key = os.environ.get("EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base = os.environ.get("EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if not key:
            raise SystemExit("Set EMBED_API_KEY (or use --no-embed)")
        self.client = OpenAI(api_key=key, base_url=base)
        self.model = os.environ.get("EMBED_MODEL", "text-embedding-ada-002-eastus-dz")

    def vec(self, text):
        k = hashlib.sha1((self.model + "|" + text).encode()).hexdigest()
        if k not in self.cache:
            r = self.client.embeddings.create(model=self.model, input=text[:30000])
            self.cache[k] = r.data[0].embedding
        return self.cache[k]

    def cos(self, a, b):
        va, vb = self.vec(a), self.vec(b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = sum(x * x for x in va) ** 0.5
        nb = sum(y * y for y in vb) ** 0.5
        return dot / (na * nb)

    def save(self):
        self.cache_path.write_text(json.dumps(self.cache))


# ---------- statistics ----------
def bootstrap_ci(diffs, n=10000, seed=7):
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n)
    )
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def wilcoxon_p(diffs):
    try:
        from scipy.stats import wilcoxon
        nz = [d for d in diffs if d != 0]
        return float(wilcoxon(nz).pvalue) if len(nz) >= 6 else None
    except Exception:
        return None


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True,
                    help='"Label=path/to/run.csv" (repeat per model)')
    ap.add_argument("--comparator", help="label of the run to compare others against")
    ap.add_argument("--golden", default=str(GOLDEN_XLSX))
    ap.add_argument("--golden-preferred", default="36-42",
                    help="questions where the Golden Answer (col C) is the reference")
    ap.add_argument("--margin", type=float, default=0.01,
                    help="non-inferiority margin on mean cosine difference")
    ap.add_argument("--tie", type=float, default=0.02, help="win/tie/loss tolerance")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--out", default=str(Path(EXCEL_DIR) / "run_comparison.xlsx"))
    a = ap.parse_args()

    # Golden sheet by column position: A no, B question, C golden, D baseline
    # response, E sources, F escalated, H new similarity score.
    g = pd.read_excel(a.golden, header=0)
    g = g.iloc[:, :8]
    g.columns = ["no", "question", "golden", "baseline", "baseline_sources",
                 "baseline_escalated", "prev_sim", "new_sim"]
    g = g[pd.to_numeric(g["no"], errors="coerce").notna()].copy()
    g["no"] = g["no"].astype(int)
    golden_pref = parse_ranges(a.golden_preferred)
    g["ref_source"] = g["no"].map(lambda n: "golden" if n in golden_pref else "baseline")
    g["reference"] = g.apply(lambda r: r["golden"] if r["ref_source"] == "golden"
                             else r["baseline"], axis=1)
    g["baseline_escalated"] = g["baseline_escalated"].map(to_bool)

    runs = {}
    for spec in a.run:
        label, path = spec.split("=", 1)
        df = pd.read_csv(path)
        df["question_no"] = df["question_no"].astype(int)
        runs[label.strip()] = df.set_index("question_no")
    labels = list(runs)
    comp = a.comparator if a.comparator in runs else None

    emb = None if a.no_embed else Embedder(TABLES_DIR / "embed_cache.json")

    rows = []
    for _, q in g.iterrows():
        row = {"no": q["no"], "question": q["question"], "reference": q["ref_source"],
               "baseline_escalated": q["baseline_escalated"]}
        for lab in labels:
            df = runs[lab]
            if q["no"] not in df.index:
                row[f"{lab} | missing"] = True
                continue
            r = df.loc[q["no"]]
            ans = str(r["answer"])
            srcs = split_sources(r.get("sources", ""))
            esc = to_bool(r["escalation_required"])
            canned = CANNED in ans.lower()
            n_cites, cites_ok = citation_check(ans, srcs)
            row[f"{lab} | escalated"] = esc
            row[f"{lab} | canned"] = canned
            row[f"{lab} | n_sources"] = len(srcs)
            row[f"{lab} | citations_ok"] = cites_ok
            row[f"{lab} | latency_s"] = round(float(r["latency_ms"]) / 1000, 2)
            row[f"{lab} | suspect_error"] = to_bool(r.get("suspect_error", False))
            if not canned:
                row[f"{lab} | rougeL"] = rouge_l(q["reference"], ans)
                row[f"{lab} | tokenF1"] = token_f1(q["reference"], ans)
                if emb:
                    row[f"{lab} | cosine"] = emb.cos(str(q["reference"]), ans)
        rows.append(row)
    pq = pd.DataFrame(rows)
    if emb:
        emb.save()

    # ----- summary per model -----
    summ = []
    for lab in labels:
        e = pq[f"{lab} | escalated"]
        lat = pq[f"{lab} | latency_s"].dropna()
        s = {
            "model": lab,
            "questions": int(e.notna().sum()),
            "escalated": int(e.sum()),
            "escalation_rate": round(e.mean(), 3),
            "canned_answers": int(pq[f"{lab} | canned"].sum()),
            "agree_with_baseline_flag": int((e == pq["baseline_escalated"]).sum()),
            "new_escalations_vs_baseline": int((e & ~pq["baseline_escalated"]).sum()),
            "dropped_escalations_vs_baseline": int((~e & pq["baseline_escalated"]).sum()),
            "citation_errors": int((~pq[f"{lab} | citations_ok"]).sum()),
            "suspect_errors": int(pq[f"{lab} | suspect_error"].sum()),
            "latency_median_s": round(lat.median(), 2),
            "latency_p90_s": round(lat.quantile(0.9), 2),
            "latency_max_s": round(lat.max(), 2),
        }
        for m in ("cosine", "rougeL", "tokenF1"):
            col = f"{lab} | {m}"
            if col in pq:
                s[f"{m}_mean"] = round(pq[col].mean(), 4)
                s[f"{m}_n"] = int(pq[col].notna().sum())
        summ.append(s)
    summary = pd.DataFrame(summ)

    # ----- paired comparison vs comparator (questions both answered) -----
    paired = []
    metric = "cosine" if emb else "rougeL"
    if comp:
        for lab in labels:
            if lab == comp:
                continue
            c1, c2 = f"{lab} | {metric}", f"{comp} | {metric}"
            both = pq[[c1, c2, "no"]].dropna()
            d = (both[c1] - both[c2]).tolist()
            if len(d) < 3:
                continue
            lo, hi = bootstrap_ci(d)
            worst = both.assign(d=d).nsmallest(5, "d")
            paired.append({
                "model": lab, "vs": comp, "metric": metric, "n_paired": len(d),
                "mean_diff": round(statistics.fmean(d), 4),
                "median_diff": round(statistics.median(d), 4),
                "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                "margin": -a.margin,
                "non_inferior": lo > -a.margin,
                "wilcoxon_p": wilcoxon_p(d),
                "wins": sum(x > a.tie for x in d),
                "ties": sum(abs(x) <= a.tie for x in d),
                "losses": sum(x < -a.tie for x in d),
                "worst_5_questions": ", ".join(
                    f"Q{int(n)} ({x:+.3f})" for n, x in zip(worst["no"], worst["d"])),
            })
    paired = pd.DataFrame(paired)

    # ----- escalation matrix -----
    esc = pq[["no", "question", "baseline_escalated"]
             + [f"{l} | escalated" for l in labels]].copy()
    esc.columns = ["no", "question", "baseline"] + labels
    esc = esc[esc[["baseline"] + labels].any(axis=1)]

    # ----- KB drift: documents the baseline cited that no new run cited -----
    new_docs = set()
    for df in runs.values():
        for cell in df.get("sources", pd.Series(dtype=str)).fillna(""):
            new_docs.update(doc_key(s) for s in split_sources(cell))
    missing = {}
    for _, q in g.iterrows():
        for s in split_sources(q["baseline_sources"]):
            k = doc_key(s)
            if k and k not in new_docs:
                missing.setdefault(k, set()).add(int(q["no"]))
    missing_df = pd.DataFrame(
        [{"document (normalised)": k, "cited by baseline for questions":
          ", ".join(f"Q{n}" for n in sorted(v)), "count": len(v)}
         for k, v in sorted(missing.items(), key=lambda kv: -len(kv[1]))])

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(a.out) as xw:
        summary.to_excel(xw, "summary", index=False)
        paired.to_excel(xw, "paired", index=False)
        pq.to_excel(xw, "per_question", index=False)
        esc.to_excel(xw, "escalation", index=False)
        missing_df.to_excel(xw, "missing_docs", index=False)

    pd.set_option("display.width", 200, "display.max_columns", 30)
    print("\n== Summary ==\n", summary.T.to_string())
    if len(paired):
        print("\n== Paired vs", comp, "==\n", paired.T.to_string())
    print(f"\n== Baseline documents not cited in any new run: {len(missing_df)} ==")
    if len(missing_df):
        print(missing_df.to_string(index=False))
    print("\nSaved", a.out)


if __name__ == "__main__":
    main()
