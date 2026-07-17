# Visão geral

O MVP separa instruções em `workflows/`, wrappers determinísticos em `tools/` e lógica em `core/`, `db/` e `services/`.

Fluxo principal: upload seguro → extração multimodal → embeddings Gemini → upsert Pinecone → recuperação filtrada → resposta fundamentada com fontes.
