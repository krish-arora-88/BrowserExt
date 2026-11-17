# EIM AI Chatbot: Model Migration and KB Reseed Test Plan (DRAFT)

Nov 17, 2026 · Hitesh Malhotra · For review with manager before execution

## 1. Question this plan answers
Can Haiku 5.5 or Sonnet 5.5 replace GPT-5 (being deprecated) in the EIM chatbot with no loss in answer quality, groundedness, citation accuracy or escalation behavior, and did reseeding and re-embedding the knowledge base (KB) preserve retrieval quality?

## 2. What changes (the variables)
| Change | Detail |
|---|---|
| Generation model | GPT-5 (baseline) vs Haiku 5.5 vs Sonnet 5.5 |
| KB index | Reseeded: all KB documents re-chunked and re-embedded from scratch with the text embedding model |

Kept constant: system prompt, retrieval settings (top-k, thresholds), chunking, temperature, golden dataset.
**Confirm:** embedding model and dimension are unchanged. If they change, query-time embedding must change with them.

## 3. Test design: three layers
1. **Retrieval (no LLM).** For each question, does the correct section appear in the top-k chunks? Isolates reseed and embedding problems from model problems.
2. **Generation (retrieval held fixed).** Same retrieved chunks to each model; compare answers.
3. **End to end and operational.** Full chatbot behavior: escalation, refusals, citations, latency, tokens, cost.

## 4. Datasets
| Set | Purpose | Source |
|---|---|---|
| Golden Q&A (43) | Regression vs the approved baseline | Validation report (SME-approved) |
| Paraphrase set | Same intent, different wording, typos, short or vague queries | Written by me from the golden set |
| Unanswerable set | Questions the KB cannot answer; tests hallucination and escalation | Written by me, reviewed with policy owner |
| Multi-document or ambiguous | Needs several sections or has a missing premise | Written by me |
| Adversarial | Prompt injection, off-topic, requests to ignore policy | Written by me |
| Real queries (optional) | Realistic phrasing | Chat logs, if access is allowed |

Each item is labeled with the expected source document and section, and the expected behavior (answer, escalate or decline).

## 5. Metrics and pass criteria (proposed, to be agreed before running)
| Metric | How measured | Proposed pass rule |
|---|---|---|
| Retrieval hit@k | Correct section in top-k, new index vs old index | No drop vs old index |
| Answer similarity / correctness | Pantheon built-in evaluator vs golden answers | Average at least 90% (approval threshold) and not worse than GPT-5 baseline by more than a tolerance X |
| Groundedness | Pantheon built-in evaluator; manual check of failures | No increase in ungrounded claims vs baseline |
| Citation accuracy | `[Source n]` present and points to the right section | At least baseline |
| Escalation behavior | Correct escalate / no escalate on labeled items | Auto-escalation rate below 5%; no missed escalations on the unanswerable set |
| Refusal / injection handling | Adversarial set | No policy leakage |
| Latency, tokens, cost | Per-run logs | Reported, used to choose between Haiku and Sonnet |

Tolerance X is to be set before the first run, not after seeing results.

## 6. Experiment matrix
| Run | Model | Index | Purpose |
|---|---|---|---|
| R0 (optional) | GPT-5 | Old | Reference at approval time |
| R1 | GPT-5 | Reseeded | Baseline; shows whether the reseed alone changed quality |
| R2 | Haiku 5.5 | Reseeded | Candidate |
| R3 | Sonnet 5.5 | Reseeded | Candidate |

Run each at least 3 times (LLM output varies) and report mean and spread.

## 7. Execution steps
**A. Prepare**
1. Record the embedding model and dimension, chunking settings and `LLM_MODEL_NAME` from `config.py`.
2. Back up the chunks table and `documents_catalogue`.
3. Record baseline counts (`/health`, `/api/v1/documents/info`; expect 34 documents).

**B. Reseed**
4. Create a new table or index; do not overwrite the live one.
5. Re-chunk and re-embed all active catalogue documents.
6. Re-add accepted feedback corrections (separate chunks).
7. Verify counts, no null or zero embeddings, and spot-check retrieval on known questions.

**C. Add models**
8. Add Haiku 5.5 and Sonnet 5.5 as configurable values; keep GPT-5 until the comparison is complete.
9. Check prompt format, citation format and escalation detection on the new models.

**D. Evaluate in Pantheon**
10. Load the datasets with expected sections and behaviors.
11. Register the reseeded KB as the asset.
12. Run R1 to R3 with identical settings; use built-in evaluators.
13. Compute outside Pantheon what its built-ins do not cover (similarity threshold roll-up, escalation rate). Governance evaluators are on the Pantheon roadmap but not available yet.

**E. Decide and record**
14. Build one comparison table across runs.
15. Manually read every failing or low-scoring answer.
16. Apply the pass rules from section 5, recommend a model, and archive run IDs and the table as audit evidence.

## 8. Risks and open questions
- Does a model change require re-validation by model risk (EMRM) under the approved MPT thresholds? Confirm with manager.
- LLM-as-judge bias: check which model the Pantheon evaluators use; a judge from one vendor may favor that vendor's outputs.
- How the KB is supplied to Pantheon (upload as asset vs connect to the Postgres index) is unconfirmed.
- Golden questions are known and easy; the extra sets exist to cover that.
- Nondeterminism: single runs can mislead; repeat runs.
- Prompt changes: new models may need small prompt tweaks. Any change must be applied to all candidates and logged.

## 9. Deliverables
- Reseeded index and a verification log
- Pantheon run IDs and results for R1 to R3
- Comparison table and written recommendation (Haiku vs Sonnet, cost vs quality)
- List of failure cases with causes (retrieval vs generation)
