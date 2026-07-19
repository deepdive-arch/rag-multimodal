# Ferramentas operacionais

Scripts determinísticos usados pelos workflows. Eles leem configuração do `.env`, não devem imprimir segredos e não substituem backup.

- `setup_pinecone.py`: cria ou valida o índice de forma idempotente;
- `ingest.py`: ingere um arquivo ou diretório recursivo;
- `query_rag.py`: executa retrieval e geração fundamentada;
- `cleanup_expired.py`: limpa um lote limitado de documentos expirados; dry-run por padrão;
- `reconcile_persistence.py`: audita Postgres, Pinecone e R2; altera estado somente com `--apply`;
- `migrate_local_persistence.py`: migra opcionalmente o antigo SQLite e `.tmp/uploads`.

## Migração local legada

A ferramenta usa `sqlite3` da biblioteca padrão em modo read-only e as dependências normais do backend para Postgres/R2/Pinecone/Gemini. Ela é excluída da imagem Docker de runtime, não apaga arquivos locais e não altera o SQLite.

```powershell
python tools/migrate_local_persistence.py --dry-run --report-path .tmp/migration-report.json
python tools/migrate_local_persistence.py --apply --document-id <uuid> --report-path .tmp/migration-canary.json
python tools/migrate_local_persistence.py --apply --limit 100 --report-path .tmp/migration-report.json
```

Opções: `--dry-run` (padrão), `--apply`, `--limit`, `--document-id`, `--report-path` e `--reindex-missing`. As opções avançadas `--sqlite-path` e `--uploads-dir` existem somente para selecionar uma origem legada controlada. `--reindex-missing` exige `--apply`, regenera embeddings apenas quando faltam vetores e pode consumir quota paga; use somente após o canário e a reconciliação.
