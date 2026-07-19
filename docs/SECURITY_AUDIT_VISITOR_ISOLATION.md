# Auditoria de segurança e consistência — isolamento por visitante

Data: 19 de julho de 2026

## Resultado executivo

O isolamento foi aprovado nos gates locais depois das correções descritas neste relatório. As rotas públicas agora usam exclusivamente um `visitor_id` resolvido pelo middleware a partir de cookie HttpOnly assinado por HMAC-SHA256. Vetores usam namespace Pinecone derivado no backend por visitante; R2 usa prefixo derivado por visitante/documento; Postgres filtra ownership; respostas e feedback têm vínculos compostos de ownership.

Nenhuma rota pública aceita `visitor_id`, namespace Pinecone ou object key R2 como autoridade. IDs estrangeiros e inexistentes retornam a mesma semântica de ausência.

## Fluxo de segurança

```text
Visitor
  ↓
Visitor Identity
  ↓
FastAPI
  ↓
Visitor-scoped persistence
  ↓
Visitor-scoped R2 object
  ↓
Visitor-scoped Pinecone namespace/filter
  ↓
RAG retrieval
  ↓
Generated Response
  ↓
Persisted response_id
  ↓
Feedback validation
```

O identificador persistente é um UUIDv4 em cookie HttpOnly assinado; não é baseado em IP. O middleware resolve a identidade antes dos handlers, e cada operação posterior valida ownership no catálogo e nos serviços externos antes de prosseguir.

O aceite é local/offline: 14 testes de integração com Postgres real foram pulados porque `TEST_DATABASE_URL` não foi fornecida. Não houve chamadas live a Supabase, Pinecone, R2 ou Gemini.

## Vulnerabilidades encontradas e correções

| Severidade | Achado | Impacto | Correção |
|---|---|---|---|
| Alta | Cookie aceitava qualquer UUIDv4 sem assinatura | Quem obtivesse um `visitor_id` poderia selecionar esse owner e acessar os recursos dele | Cookie HMAC-SHA256 com `VISITOR_SESSION_SECRET`; UUID cru ou cookie adulterado gera uma identidade nova; handlers confiam somente em `request.state` |
| Média | Pinecone usava namespace compartilhado e dependia apenas de metadata filter | Uma futura omissão/regressão de filtro poderia ampliar a busca para outros visitantes | Namespace físico derivado no backend por visitante, metadata `visitor_id` sobrescrita no upsert e filtro exato obrigatório na query |
| Média | Respostas privadas não definiam política explícita contra cache intermediário | Um proxy/CDN mal configurado poderia compartilhar GETs privados por URL | Todas as respostas `/api/*` recebem `Cache-Control: private, no-store, max-age=0` e `Pragma: no-cache` |
| Média | `/api/stats` filtrava totais principais, mas expunha contagens globais por status e deleções pendentes | Enumeração indireta de atividade de outros visitantes | Todas as agregações passaram a reutilizar o predicado de ownership |
| Média | Relações conversa → mensagem → feedback dependiam principalmente da aplicação | Corrupção ou um futuro bug interno poderia associar resposta/feedback ao owner errado; feedback órfão retinha snapshot privado | Migration `0007`: FKs compostas com `visitor_id`, colunas de feedback obrigatórias e `ON DELETE CASCADE` |
| Baixa | `complete` validava ownership na leitura, mas transições `mark_uploaded`/`claim_processing` eram feitas apenas por `doc_id` | Defesa em profundidade incompleta diante de regressão/race interno | Transições e renovação de upload aceitam e aplicam o predicado do owner |
| Baixa | Geração de URL R2 e deleção aceitavam qualquer chave dentro do prefixo global gerenciado | Corrupção do catálogo poderia assinar ou excluir chave de outro visitante | Verificação adicional do prefixo exato visitante/documento antes de URL ou delete |
| Baixa | Query com `conversation_id` estrangeiro só falhava ao persistir, depois de retrieval/geração | Desperdício de quota e diferença de timing | Ownership da conversa é validado antes de reservar quota e executar o pipeline |

## Auditoria das rotas

| Rota | Identidade e ownership | IDs / stores | Resultado |
|---|---|---|---|
| `GET /api/health` | Não usa dados de visitante | Não lê catálogo privado; expõe somente estados/modelos/limites | Seguro; sem dados privados |
| `GET /api/session` | Inicializa cookie assinado sem expor UUID no body | Sem acesso por ID | Seguro |
| `GET /api/stats` | `request.state.visitor_id`; todas as agregações filtradas | Sem Pinecone/R2 | Corrigido |
| `GET /api/files` | Lista somente `Document.visitor_id == owner` | Não expõe object key | Seguro |
| `GET /api/files/{doc_id}` | Busca `doc_id` + owner; foreign e inexistente retornam 404 | Sem URL R2 | Seguro contra IDOR |
| `POST /api/uploads/presign` | Owner do middleware; dedupe por `(visitor_id, sha256)` | `doc_id` e object key gerados no servidor; schema rejeita campos extras | Seguro contra visitor/R2 key injection |
| `POST /api/uploads/{doc_id}/complete` | Lê e transiciona somente documento do owner | HEAD usa somente key persistida e valida tamanho, MIME e metadata incluindo visitante | Corrigido |
| `POST /api/ingest` | Owner do middleware repassado ao catálogo, R2 e Pinecone | Nenhum namespace/key fornecido pelo cliente | Seguro |
| `POST /api/query` | Owner do middleware; conversa opcional pré-validada | Namespace derivado; metadata filter exato; fontes revalidadas no Postgres | Corrigido; sem cross-visitor retrieval/citation |
| `DELETE /api/files/{doc_id}` | Público: owner obrigatório; admin: token backend-only | Deleta IDs do plano owned, namespace do owner e prefixo R2 exato | Seguro contra delete IDOR |
| `POST /api/files/{doc_id}/retry` | Somente admin | Acesso global é intencional e protegido por comparação constante do token | Superfície administrativa controlada |
| `DELETE /api/index` | Somente admin + confirmação `DELETE_ALL` | Limpa apenas namespace base/visitor prefix gerenciado e prefixo R2 configurado | Superfície administrativa controlada |
| `GET /api/conversations/{id}` | Conversa e mensagens exigem o mesmo owner | Foreign/inexistente retornam 404 | Seguro contra history/response IDOR |
| `DELETE /api/conversations/{id}` | Delete com `conversation_id` + owner | FK cascade remove mensagens e feedback relacionado | Seguro |
| `POST /api/feedback` | `response_id` UUID; lookup e upsert repetem owner | Pergunta/resposta/fontes vêm da mensagem persistida, nunca do payload | Seguro contra fabricação, enumeração e cross-feedback |

Não há endpoints customizados de debug. O FastAPI ainda publica `/docs`, `/redoc` e `/openapi.json`; eles enumeram contratos, mas não retornam dados privados ou segredos.

## Postgres / Supabase

- Todas as consultas públicas de documentos, chunks, conversas, mensagens, feedback e estatísticas aplicam o owner resolvido na sessão.
- Linhas legadas com `visitor_id` nulo não pertencem a visitante público e ficam invisíveis pelas consultas owned.
- As migrations habilitam RLS nas tabelas do catálogo, conversas e mensagens sem criar policies para `anon` ou `authenticated`; outra migration revoga os grants desses papéis. O catálogo permanece backend-only por conexão Postgres direta.
- A migration `0007` adiciona FKs compostas para exigir o mesmo `visitor_id` em conversa → mensagem → feedback, além de tornar o vínculo do feedback obrigatório.
- RLS, grants e constraints foram validados pelo SQL Alembic offline, mas ainda precisam ser inspecionados em um projeto Supabase descartável antes do deploy.

## Pinecone

- Namespace efetivo: `<PINECONE_NAMESPACE>--visitor_<uuidhex>`, derivado somente no backend.
- Upsert exige UUIDv4, sobrescreve qualquer `metadata.visitor_id` e usa o namespace do mesmo owner.
- Query exige o mesmo owner no namespace e em um predicado `visitor_id == owner`.
- Não existe query sem namespace, namespace vindo do cliente ou fallback de retrieval para base/default.
- Delete de documento usa o `visitor_id` capturado no plano owned do Postgres.
- Cleanup administrativo enumera estatísticas e remove somente o namespace base legado e namespaces com o prefixo visitor deste deploy; namespaces de outros apps são ignorados.
- Fontes retornadas pelo Pinecone ainda são revalidadas contra chunk, documento, object key, status `ready` e owner no Postgres.

A mudança segue a recomendação oficial de usar namespaces para isolamento multitenant estrito, mantendo metadata como defesa em profundidade: <https://docs.pinecone.io/guides/index-data/implement-multitenancy>.

## Cloudflare R2

- Chaves novas: `<prefix>/<env>/<base>/documents/visitor_<uuidhex>/<doc_id>/...`.
- Nome de arquivo e segmentos são sanitizados; traversal, segmentos vazios e chaves fora do prefixo configurado são rejeitados.
- Presign PUT não aceita object key no payload e assina Content-Type + metadata controlada.
- Complete valida HEAD, tamanho, MIME, SHA metadata, versão, doc e visitante.
- URLs GET são curtas e só são emitidas depois da validação Postgres e do prefixo exato owner/documento.
- Delete rejeita qualquer key registrada fora do prefixo exato antes de chamar R2.
- Bucket permanece privado; não há URL pública permanente.

## Feedback e respostas

- `response_id` é o UUIDv4 de uma linha `messages` persistida após o pipeline gerar a resposta.
- Se a persistência falhar, a API não devolve sucesso nem um ID feedbackável.
- O payload de feedback aceita apenas `response_id` e booleano estrito `useful`.
- O backend carrega pergunta, resposta e fontes da mensagem persistida; o cliente não consegue substituí-las.
- Lookup inicial e upsert transacional validam `visitor_id` novamente.
- FK composta `(message_id, visitor_id)` impede associação inconsistente no banco.
- UUID fabricado e UUID estrangeiro produzem o mesmo 404.

## Testes de ataque adicionados

Os testes em `tests/test_visitor_isolation.py`, `tests/test_pinecone_service.py`, `tests/test_r2_storage.py`, `tests/test_deletion.py` e `tests/test_catalog.py` cobrem explicitamente:

1. A → B cross-visitor query.
2. B → A cross-visitor query.
3. IDOR de arquivo.
4. IDOR de conversa.
5. Ausência de rota pública de response-by-ID e ownership de `get_message`.
6. IDOR de feedback.
7. Namespace injection no payload e filtro Pinecone de owner divergente.
8. `visitor_id` injection no JSON e cookie UUID cru/adulterado.
9. R2 key injection no presign e no plano de deleção.
10. `response_id` fabricado.
11. Enumeração de `response_id` com respostas indistinguíveis.
12. Query sem documentos próprios.
13. Query filtrada por documento de outro visitante.
14. Delete cross-visitor.
15. Feedback cross-visitor.

## Validações executadas

- Backend: `164 passed, 14 skipped, 1 warning`.
- Ruff: aprovado.
- `compileall`: aprovado.
- Alembic offline `upgrade head --sql`: aprovado até `0007_response_ownership_constraints`.
- Alembic offline `downgrade head:base --sql`: aprovado.
- `git diff --check`: aprovado.
- Frontend ESLint: aprovado.
- Frontend TypeScript `tsc --noEmit`: aprovado.
- Frontend Next.js production build: aprovado.
- Docker build: `rag-multimodal-backend:audit` criado com sucesso.
- Docker smoke: usuário `app`, UID/GID `10001`, `/tmp/rag-processing`, 19 rotas carregadas e nenhum `.db`, `.sqlite` ou `.sqlite3` em `/app`.

## Arquivos alterados pela auditoria

- Identidade/configuração: `core/visitor.py`, `core/config.py`, `.env.example`.
- HTTP: `api/dependencies.py`, `api/schemas.py`, `api/server.py`.
- Persistência: `db/catalog.py`, `db/interfaces.py`, `db/models.py`, `alembic/versions/0007_response_ownership_constraints.py`.
- Pinecone/R2/pipeline: `services/pinecone_service.py`, `services/retrieval.py`, `services/storage.py`, `services/deletion.py`, `services/ingestion.py`.
- Testes: `tests/test_visitor_isolation.py`, `tests/test_pinecone_service.py`, `tests/test_r2_storage.py`, `tests/test_deletion.py`, `tests/test_catalog.py`, `tests/test_api.py`, `tests/test_config.py`, `tests/test_ingestion.py`, `tests/test_pipeline_contract.py`, `tests/test_public_controls.py`, `tests/test_retrieval.py`.
- Documentação: `README.md`, `docs/DEPLOYMENT_FREE_TIER.md`, `docs/FEEDBACK.md`, este relatório.

## Riscos residuais e ações antes do deploy

1. Configurar um `VISITOR_SESSION_SECRET` novo, aleatório, com pelo menos 32 caracteres e somente no backend. Rotacioná-lo invalida cookies existentes e cria novas identidades anônimas.
2. Fazer backup antes da migration `0007`: ela remove feedback histórico sem `visitor_id` ou `message_id` e troca FKs por vínculos compostos com cascade.
3. Vetores já existentes no namespace base compartilhado ficam intencionalmente fora do retrieval novo. Reindexar documentos owned para os namespaces derivados antes de considerar a migração operacional concluída; não habilitar fallback para o namespace compartilhado.
4. Validar em staging com `TEST_DATABASE_URL` descartável e credenciais próprias de teste. Os 14 skips significam que concorrência/FKs/RLS em Postgres real e comportamento live de Supabase/R2/Pinecone não foram provados nesta execução.
5. Monitorar quantidade de namespaces e quotas do plano Pinecone. Um namespace só nasce quando o visitante indexa vetores, mas retenção/reindexação deve ser acompanhada.
6. Cookies anônimos continuam sendo bearer credentials: HttpOnly/Secure/HMAC reduzem adulteração e XSS trivial, mas roubo do cookie ainda equivale a roubo da sessão.
7. Considerar desabilitar `/docs`, `/redoc` e `/openapi.json` em produção se a enumeração de contratos não for desejada.
