# EIM Model Migration: what we did, what it means, why

## The goal
GPT-5 is being deprecated. You need to know whether Haiku 5.5 or Sonnet 5.5 can replace it with no loss in quality, groundedness, citations or escalation behavior. You also need to show that reseeding the KB doesn't hurt retrieval. Two things change the answers: the **KB** (what gets retrieved) and the **model** (what gets written from it). You test them separately so you know which one caused any change.

## A. Understanding the system
**What it is:** a RAG chatbot.
1. The question is embedded (turned into 1536 numbers).
2. The 10 closest KB chunks are fetched, and those with cosine ≥ 0.70 are kept.
3. Chunks from the same section are stitched together.
4. The model answers from them using `system_prompt.txt`.
5. It returns an answer and an `escalation_required` flag. Only cited sources are shown.

**Why map it first:** you can't test what you can't draw. It showed the two places a test can fail (retrieval and generation). It also showed that errors in embedding, retrieval or the model call are caught and displayed as escalations, so a broken key can look like a cautious model.

## B. Recording the baseline
- **What:** you wrote down the embedding model (`text-embedding-ada-002-eastus-dz`, 1536 dims), chunking (1200 characters, 200 overlap), top-k 10, min score 0.70, GPT-5 in the deployment config, and that no temperature is set.
- **Why:** a comparison is only valid if you know what stayed the same. Temperature isn't set, so each model uses its own default. That's a variable, not a constant.
- **Still open:** `system_prompt.txt` and `config.py` have uncommitted changes, so the baseline answers may come from an older prompt. Record the git commit and diff.

## C. Rebuilding the KB locally
1. **Downloaded the 38 documents (`download.py`).**
   - You fixed a broken venv and EWS credentials that caused 401s.
   - *Why:* a reseed means rebuilding from the originals, not copying the old database.
2. **Extracted them to markdown (`extract.py`).**
   - Four files were PDFs saved as `.docx`, and renaming them fixed it.
   - *Why:* it lets you read what will be chunked. The seed does its own conversion, so this was a check, not a requirement.
3. **Checked the local DB was empty.**
   - *Why:* the seed script skips anything already in the catalogue, so it only seeds an empty database. Empty also meant no backup was needed.
4. **Ran the seed (`bulk_seed_kb.py --env local`).**
   - For each file the script uploads it to the backend, which saves the original and a markdown copy and adds a `pending` catalogue row.
   - The script then approves it. In one transaction the backend chunks the text, embeds the chunks, stores the vectors in Postgres (pgvector) and marks the row `active`.
   - Your fixes were all about letting this pipeline run: `PYTHONPATH=.`, starting the backend, S3 credentials (which needed a restart), and switching to the key that works for embeddings.
   - *Why through the backend:* the new KB is built exactly as production builds it.
5. **Enabled local test login.**
   - SSO and local login were both off, so the UI looped.
   - *Why:* script 06 needs to log in to call the chat endpoint.

## D. The key problem (the main blocker)
- **What happened:** chat failed with a 401 on the embeddings call, and it showed up as a normal "lacks confidence, escalate" message. One key works for embeddings but isn't onboarded for Haiku. The other is rejected for embeddings.
- **What it means:** the question is embedded before any model sees it. A key that can't embed breaks every answer, whatever the model.
- **How to spot it:** ~0 ms latency and no sources.
- **Fix options:**
  - Use separate embedding and chat keys in config.
  - Add a local-only second key variable for the embedding client.
  - Ask the gateway team for one key covering everything.
  - Record whichever you used.

## E. The retrieval check (Report scripts 00–04)
This tests the KB only, with no model involved.
- **00 `create_db`:** creates the `report` database, the pgvector extension and the schema. *Why:* a separate workspace so analysis never touches the app database.
- **`_copy_documents_once`:** copied the 349 baseline chunks into `report.documents`. You changed the chunk ID type from BIGINT to TEXT so the IDs fit. *Why:* these old chunks are the reference. Delete the file afterwards, since it holds access to the source database.
- **01 `embed_questions`:** embedded the **43** golden questions with ADA-002. *Why:* questions and chunks must be in the same vector space to compare.
- **02 `compare_embeddings`:** cosine similarity of every question against every chunk (43 × 349). Scores ranged from 0.6483 to 0.9183. Cosine of 1 means identical direction, and ADA-002 squeezes scores into a high band, so these values are normal.
- **03 `plots`:** histograms and band counts at 0.70, 0.80 and 0.90 per question. *Why:* to see how many chunks each question would retrieve at each threshold.
- **04 `write_word_report`:** produced `Threshold_0.70_Rationale.docx`, the written reasoning for the 0.70 cutoff.
- **Limit:** this measures how close chunks are, not whether they're the *right* chunks. There are no relevance labels.

## F. The model test (script 06, run with Haiku)
- **What it does:** for each golden question it opens a fresh session, sends the question to the real backend, and saves the answer, sources, escalation flag and latency to `tables/run_<label>.csv`.
- **Why through the backend:** the backend applies the real retrieval, stitching, system prompt, model and escalation logic, so the result reflects production.
- **Limit:** it only sees the response. It can't confirm which database, model or prompt version was used, so record those before each run.
- **CHECK rows:** a fast escalation with no sources is probably a backend error (like a key failure), not a real escalation. Resolve these before counting escalations.

## What's not done yet
1. Compare the reseeded KB's chunk and document counts against the 349 baseline, and reconcile 38 vs 34 documents.
2. Confirm the Haiku run's CHECK rows and fix their cause.
3. Run Sonnet, and GPT-5 on the new KB as the true baseline.
4. Score all runs (correctness, grounded, citations, escalation, prompt rules) against the test plan's pass rules.
5. Decide, then archive the evidence in one dated folder.

The same text is saved in the project as "EIM Migration Walkthrough - What We Did and Why", with a results table for you to fill in.
