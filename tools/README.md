# Tools

Esta pasta contém scripts Python determinísticos usados pelos workflows.

As ferramentas devem ler configurações e credenciais do `.env`, produzir saídas testáveis e manter cada função extremamente pequena, conforme o `AGENTS.md`.

Ferramentas disponíveis:

- `setup_pinecone.py`: criação/validação idempotente do índice;
- `ingest.py`: ingestão de um arquivo ou diretório recursivo;
- `query_rag.py`: consulta com retrieval e geração fundamentada.
