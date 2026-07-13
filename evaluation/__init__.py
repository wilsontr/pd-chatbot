# evaluation — production-LLM evaluation harness for pd-chatbot
#
# Measures:
#   Retrieval: recall@k, MRR, nDCG with ablation against vector-only / BM25-only
#   Generation: faithfulness, answer relevance, citation correctness (LLM-as-judge)
#   Model comparison: Sonnet vs Haiku vs any model configurable via CLI
#
# Usage:
#   python -m evaluation.evaluate --all          # run everything
#   python -m evaluation.evaluate --retrieval    # retrieval only
#   python -m evaluation.evaluate --generation   # generation only (LLM-as-judge)
#   python -m evaluation.evaluate --model claude-haiku-4-5  # override generation model
