"""RAG (Retrieval-Augmented Generation) package.

Pluggable embedder + vector store backends behind a common interface.
Default in v1: OpenAI embeddings + pgvector. Swap to Azure via env vars.
See docs/superpowers/specs/2026-05-20-rag-pipeline-design.md.
"""
