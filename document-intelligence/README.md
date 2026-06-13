# Finance Document Intelligence (RAG)

Semantic search, retrieval-augmented generation (RAG), and structured
extraction over finance documents (vendor contracts and an expense policy),
on synthetic data modeled on the Lumen company used across this repo.

## Files

- `embedder.py` — embedding backend. Primary: `sentence-transformers`
  (PyTorch). Fallback: `model2vec` (no PyTorch). The search code is
  backend-agnostic, so it also works with hosted embeddings (Voyage, OpenAI).
- `search.py` — chunks the documents, embeds them, and returns the closest
  passages to a query by cosine similarity.
- `rag.py` — RAG question answering with source citations, and structured
  extraction of key contract terms into a table.
- `docs/` — the source documents.

## Run it

```bash
pip install -r requirements.txt
python search.py        # semantic search (no API key needed)
python rag.py           # RAG + extraction (needs ANTHROPIC_API_KEY in repo-root .env)
```

## Design decisions (the "why")

- **When RAG, when not.** For a specific question over a large corpus, RAG
  wins: retrieve only the relevant passages. For extracting fields from a
  single short document, pass the whole document; retrieval there is
  unnecessary overhead. Choosing well is the skill, not using RAG by default.
- **Grounded answers.** The model answers using only retrieved context and
  cites the source document. If the context is insufficient, it says so
  instead of inventing.
- **Swappable embeddings.** Retrieval depends on an `embed()` function, not on
  a specific model, so the backend can change (sentence-transformers, a
  multilingual model, or a hosted embedder) without touching the search.

## Debugging note: cross-lingual retrieval and an environment limit

Two real findings from building this, worth more than a demo that always works.

First, a Spanish question over English documents failed to retrieve the right
passage. For "plazo de pago de la agencia de marketing", the model correctly
refused to answer rather than invent a figure, because the relevant passage was
never retrieved, while the full-document extraction returned the same field
correctly. The failure was in retrieval, not generation, specifically
cross-lingual matching (Spanish query, English corpus).

Second, the proper fix, a multilingual embedding model, would not load on this
machine: the Hugging Face multilingual model segfaulted under Python 3.14 with
torch 2.12 (a bleeding-edge environment issue, and a segfault cannot be caught
in code). Pragmatic resolution: ship the English model, which is stable here,
and keep queries in English. Paths that avoid the torch/3.14 instability include
a static multilingual model (model2vec, no torch) or a hosted multilingual
embedding API.

The broader lesson: a correct number can still produce a wrong answer if
retrieval misses, and you only catch this reliably with an evaluation set, not
by spot-checking. That is the motivation for the evals and guardrails work.

## Stack
Python, sentence-transformers / PyTorch, embeddings & cosine similarity,
RAG, structured extraction, Anthropic API.
