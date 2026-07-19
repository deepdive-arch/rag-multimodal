# Deploy no free tier

Este runbook publica a arquitetura completa:

```text
Render Static Site (Next.js) → Render Web Service (FastAPI/Docker)
                                  ├─ Supabase Postgres
                                  ├─ Cloudflare R2 privado
                                  ├─ Pinecone
                                  └─ Gemini
```

O filesystem do Render não é persistente. O catálogo, os objetos e os vetores ficam nos serviços gerenciados; o container usa somente `TEMP_PROCESSING_DIR` para arquivos intermediários.

## 1. Pré-requisitos

- Repositório conectado ao Render.
- Projeto Supabase, bucket privado R2, índice Pinecone e chave Gemini.
- Domínio final ou URLs `onrender.com` anotadas para configurar CORS e `FRONTEND_ORIGIN`.
- Docker local para validar a imagem.
- Não adicione `.env`, arquivos de credenciais ou dumps ao Git.

## 2. Supabase Postgres

1. Crie um projeto no [Supabase Dashboard](https://supabase.com/dashboard) no plano Free e escolha uma região próxima ao serviço Render.
2. Abra **Connect** no projeto e copie a connection string **Shared Pooler / Session mode**, com host `aws-<region>.pooler.supabase.com` e porta `5432`. Para um backend persistente no Render IPv4, session mode é a opção documentada para tráfego de aplicação; não use a URL de transaction mode `6543` neste projeto.
3. Substitua `<password>` pela senha do banco, fazendo URL-encode de caracteres reservados, e configure no backend:

   ```text
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```

   A senha não deve aparecer em README, logs, screenshots ou commits. A referência de conexão é a [documentação de conexão do Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).

4. Em ambiente local com `DATABASE_URL` definido, execute:

   ```powershell
   python -m alembic upgrade head
   python -m alembic current
   ```

   O head esperado é `0007_response_ownership_constraints`. As revisões `0005` a `0007` adicionam ownership por visitante, histórico persistido e chaves estrangeiras compostas para conversa/resposta/feedback, mantendo os grants do catálogo revogados; o backend continua acessando o banco diretamente pela `DATABASE_URL`.

5. No SQL Editor, confirme que as nove tabelas de catálogo têm RLS, não possuem policies públicas e que a consulta abaixo retorna zero:

   ```sql
   SELECT count(*)
   FROM information_schema.role_table_grants
   WHERE table_schema = 'public'
     AND grantee IN ('anon', 'authenticated')
     AND table_name IN ('alembic_version', 'audit_events', 'chunks', 'conversations', 'document_objects', 'documents', 'feedback', 'ingestion_events', 'messages', 'usage_counters');
   ```

6. Verifique a conexão com `SELECT 1` por um cliente PostgreSQL e depois confira `GET /api/health`. O campo `services.database` deve ser `ok`. A migration é executada novamente pelo entrypoint do container em cada startup; migrations já aplicadas são idempotentes.
7. Não crie tabelas de catálogo no SQLite. O schema do projeto está em `alembic/` e a aplicação rejeita URLs que não sejam PostgreSQL/asyncpg.

## 3. Cloudflare R2

1. Em **Cloudflare → R2**, crie um bucket dedicado, por exemplo `<private-r2-bucket>`, mantendo o acesso público desativado. Não habilite public bucket nem coloque credenciais no frontend.
2. Em **Manage R2 API Tokens**, crie um token com **Object Read & Write** aplicado somente ao bucket escolhido. Esse é o menor escopo compatível com upload, leitura, listagem e exclusão usados pelo backend. A [documentação de tokens R2](https://developers.cloudflare.com/r2/api/tokens/) confirma o escopo por bucket e a incompatibilidade de tokens de objeto com a API REST do Cloudflare; o código usa a API S3 compatível.
3. Copie uma única vez o **Access Key ID** e o **Secret Access Key**. Configure no Render somente:

   ```text
   R2_ACCOUNT_ID=<cloudflare-account-id>
   R2_ACCESS_KEY_ID=<r2-access-key-id>
   R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
   R2_BUCKET_NAME=<private-r2-bucket>
   R2_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com
   R2_REGION=auto
   ```

4. Edite `infra/r2-cors.json` antes de aplicar: substitua `https://<frontend-service>.onrender.com` pela origem final e, se houver domínio próprio, adicione-o como uma origem exata. Não mantenha localhost na política de produção. Preserve `PUT`, `GET` e `HEAD`, os headers `Content-Type` e `x-amz-meta-*`, e `ExposeHeaders: ["ETag"]`.
5. Aplique no bucket pelo dashboard ou pelo Wrangler:

   ```powershell
   npx wrangler r2 bucket cors set <private-r2-bucket> --file infra/r2-cors.json
   ```

   CORS é necessário mesmo com URL pré-assinada; a [documentação de CORS do R2](https://developers.cloudflare.com/r2/buckets/cors/) explica o motivo e a configuração. O endpoint S3 e o fluxo de presigned URL estão na [documentação S3 do R2](https://developers.cloudflare.com/r2/get-started/s3/).

6. Valide sem publicar o bucket:

   - abra o frontend pela origem final;
   - faça um upload pequeno e confirme que o Browser faz `PUT` diretamente para a URL R2;
   - confirme `POST /api/uploads/<doc_id>/complete` e o estado `ready` em `GET /api/files/<doc_id>`;
   - faça uma consulta e abra uma fonte, confirmando que o backend gera URL pré-assinada de leitura sob demanda;
   - verifique que nenhum request do Browser contém `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` ou `ADMIN_TOKEN`;
   - confirme que uma URL pública direta para um object key não funciona.

## 4. Pinecone

1. Crie ou selecione o índice `rag-multimodal` no console Pinecone.
2. Confirme exatamente: `vector_type=dense`, `dimension=1536`, `metric=cosine`, `cloud=aws`, `region=us-east-1`.
3. Reserve `production` como prefixo deste deploy. O backend deriva um namespace por visitante (`production--visitor_<uuidhex>`) e ainda grava/valida `visitor_id` em metadata como defesa em profundidade. Nenhum namespace vem do cliente e não há fallback de consulta para `production` ou para o namespace default. Não reutilize o prefixo local ou de staging.
4. Configure:

   ```text
   PINECONE_API_KEY=<pinecone-api-key>
   PINECONE_INDEX_NAME=rag-multimodal
   PINECONE_NAMESPACE=production
   PINECONE_CLOUD=aws
   PINECONE_REGION=us-east-1
   EMBEDDING_DIMENSION=1536
   ```

5. Se precisar criar/validar o índice uma única vez, execute localmente `python tools/setup_pinecone.py`. O backend não recria um índice incompatível. A [documentação de criação de índice](https://docs.pinecone.io/guides/index-data/create-an-index) confirma que dimensão e métrica devem corresponder aos vetores; a [documentação de namespaces](https://docs.pinecone.io/guides/manage-data/manage-namespaces) descreve o isolamento por namespace.
6. Antes de promover esta versão, reindexe explicitamente os documentos que ainda estejam no namespace base compartilhado. O retrieval não consulta esse namespace legado como fallback; portanto, esses vetores ficam intencionalmente invisíveis até serem recriados no namespace derivado do respectivo visitante.
7. Após o deploy, `GET /api/health` deve retornar `services.pinecone=ready`.

## 5. Gemini

1. Crie uma chave no [Google AI Studio](https://aistudio.google.com/apikey) para o projeto que será usado pelo deploy.
2. Configure `GOOGLE_API_KEY=<gemini-api-key>` apenas no backend. Confirme no AI Studio que os modelos configurados em `GEMINI_EMBEDDING_MODEL` e `GEMINI_GENERATION_MODEL` estão disponíveis para a chave.
3. Verifique a quota do projeto no AI Studio. As cotas dependem do modelo e do tier, e são expressas em RPM, TPM e RPD; consulte [rate limits do Gemini](https://ai.google.dev/gemini-api/docs/rate-limits) antes de abrir o endpoint publicamente.

## 6. Variáveis do backend

O bloco abaixo é a lista completa das variáveis lidas pelo backend e pelo fluxo de testes. Os valores são placeholders; `TEST_DATABASE_URL` é somente para testes de integração locais e não deve ser configurada no Render. `PORT` é fornecida pelo Render e não é uma variável da aplicação no `.env.example`.

```text
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=10000
PORT=10000
FRONTEND_ORIGIN=https://<frontend-service>.onrender.com
VISITOR_COOKIE_SAMESITE=none
VISITOR_COOKIE_MAX_AGE_SECONDS=31536000
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres?sslmode=require
DATABASE_POOL_SIZE=2
DATABASE_MAX_OVERFLOW=0
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_CONNECT_TIMEOUT_SECONDS=10
DATABASE_HEALTH_TIMEOUT_SECONDS=2
GOOGLE_API_KEY=<gemini-api-key>
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_GENERATION_MODEL=gemini-3.5-flash
PINECONE_API_KEY=<pinecone-api-key>
PINECONE_INDEX_NAME=rag-multimodal
PINECONE_NAMESPACE=production
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1
EMBEDDING_DIMENSION=1536
CHUNK_SIZE=1500
CHUNK_OVERLAP=250
MIN_CHUNK_SIZE=100
TEXT_PREVIEW_SIZE=400
DEFAULT_TOP_K=5
MAX_TOP_K=20
MIN_RELEVANCE_SCORE=0.35
MAX_MATCHES_PER_DOCUMENT=3
MAX_MEDIA_PARTS_PER_QUERY=3
MAX_MEDIA_CONTEXT_SIZE_MB=4
MAX_CHAT_HISTORY_MESSAGES=6
MAX_UPLOAD_SIZE_MB=10
MAX_PDF_PAGES=20
MAX_PDF_PAGE_PIXELS=20000000
MAX_IMAGE_PIXELS=40000000
MAX_AUDIO_DURATION_SECONDS=60
MAX_VIDEO_DURATION_SECONDS=60
PUBLIC_DEMO_MODE=true
PUBLIC_RETENTION_DAYS=3
CLEANUP_BATCH_SIZE=2
CLEANUP_TIMEOUT_SECONDS=10
DELETION_LEASE_SECONDS=120
MAX_DAILY_UPLOADS_PER_CLIENT=3
MAX_DAILY_QUERIES_PER_CLIENT=30
MAX_TOTAL_STORED_BYTES=1073741824
MAX_ACTIVE_DOCUMENTS=50
RATE_LIMIT_SECRET=<long-random-backend-secret>
VISITOR_SESSION_SECRET=<at-least-32-random-backend-only-characters>
TEMP_PROCESSING_DIR=/tmp/rag-processing
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<r2-access-key-id>
R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
R2_BUCKET_NAME=<private-r2-bucket>
R2_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com
R2_REGION=auto
R2_OBJECT_PREFIX=rag
R2_PRESIGNED_URL_TTL_SECONDS=600
R2_PRESIGNED_UPLOAD_TTL_SECONDS=300
R2_UPLOAD_VERSION=1
R2_CONNECT_TIMEOUT_SECONDS=5
R2_READ_TIMEOUT_SECONDS=30
R2_MAX_ATTEMPTS=3
R2_HEALTH_CACHE_SECONDS=15
ADMIN_TOKEN=<long-random-admin-token>
LOG_LEVEL=INFO
TEST_DATABASE_URL=postgresql://postgres.<test-project-ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres?sslmode=require
TEST_DATABASE_SCHEMA=codex_test_<unique-suffix>
```

## Isolamento por visitante

O backend cria um UUIDv4 e só reutiliza o cookie persistente `rag_visitor_id` quando a assinatura HMAC-SHA256 confere com `VISITOR_SESSION_SECRET`. UUID cru ou cookie adulterado recebe uma identidade nova. Em produção separada entre Static Site e API, configure `VISITOR_COOKIE_SAMESITE=none`, mantenha `Secure` ativo via HTTPS e preserve `allow_credentials` no CORS; o frontend usa `credentials: include`.

Documentos novos são gravados com `visitor_id`. Chunks e objetos são resolvidos pelo documento pai; chaves e metadados R2 incluem o escopo do visitante. O Pinecone usa um namespace derivado por visitante e toda leitura também exige filtro de igualdade do mesmo owner. Não existe caminho de retrieval global ou fallback para o namespace base.

Uploads, listagem, polling, complete, consultas, fontes, histórico, feedback e exclusão verificam o mesmo escopo. Um `doc_id`, `conversation_id` ou `message_id` de outro visitante responde como inexistente. Dados antigos sem owner ficam com `visitor_id IS NULL`, fora das rotas públicas, até uma migração explícita e segura.

## 7. Render: backend FastAPI/Docker

Crie um **Web Service** conectado ao repositório:

| Campo | Valor |
|---|---|
| Root Directory | vazio, raiz do repositório |
| Runtime | Docker |
| Dockerfile Path | `./Dockerfile` |
| Plan | Free |
| Health Check Path | `/api/health` |
| Persistent Disk | não adicionar |

1. Não defina um Start Command que substitua o entrypoint. O `docker/entrypoint.sh` valida configuração, roda `alembic upgrade head` e inicia um único worker Uvicorn.
2. Adicione todas as variáveis do bloco acima, exceto `TEST_DATABASE_URL`, usando o painel **Environment → Secret Files/Environment Variables** do Render. Use valores reais somente nos campos protegidos.
3. O Render injeta `PORT`; o container usa esse valor e mantém fallback `10000`. Não fixe outra porta no Start Command.
4. Defina `FRONTEND_ORIGIN` para a URL final do Static Site. Depois de conhecer a URL, atualize também o CORS do R2.
5. Faça o deploy e aguarde a migration. Teste:

   ```powershell
   curl.exe https://<backend-service>.onrender.com/api/health
   ```

   O aceite operacional exige `status=ok`, `database=ok`, `r2=ready`, `pinecone=ready` e Gemini `configured`. `degraded` indica configuração/provedor incompleto; `offline` indica que Postgres não está acessível.

As configurações de Docker, health check e variáveis seguem o fluxo de [Web Services do Render](https://render.com/docs/web-services). O plano Free pode suspender o serviço ocioso e o filesystem é efêmero, portanto não adicione disco para tentar persistir uploads.

## 8. Render: frontend estático

Crie um **Static Site** apontando para o mesmo repositório:

| Campo | Valor |
|---|---|
| Root Directory | `frontend` |
| Build Command | `npm ci && npm run build` |
| Publish Directory | `out` |
| Plan | Free |
| Environment Variable | `NEXT_PUBLIC_API_BASE_URL=https://<backend-service>.onrender.com` |

O `frontend/next.config.ts` já usa `output: "export"` e imagens não otimizadas, compatíveis com publicação estática. `NEXT_PUBLIC_API_BASE_URL` é embutida no build; altere-a e faça novo deploy quando a URL do backend mudar. A configuração de root, build, publish e `NEXT_PUBLIC_*` segue a [documentação de Static Sites do Render](https://render.com/docs/static-sites) e o guia de deploy.

## 9. Limitações do free tier e operação

### Filesystem efêmero e cold start

O Render perde alterações locais em redeploy, restart ou spin down. O Free Web Service pode ser suspenso após 15 minutos sem tráfego e leva aproximadamente um minuto para voltar. Isso produz cold start no primeiro request; o health check deve tolerar a inicialização. O container usa um worker para caber no plano gratuito e não possui fila persistente.

### Supabase Free

Projetos Free podem ser pausados após baixa atividade por cerca de 7 dias; há uma janela de até 90 dias para restaurar um projeto pausado. O plano Free não deve ser tratado como alta disponibilidade. Consulte [Project Pausing](https://supabase.com/docs/guides/platform/free-project-pausing) antes de publicar e monitore o e-mail da conta.

### Limites gratuitos e retenção

Referência consultada em 19/07/2026; os painéis do projeto e as páginas oficiais prevalecem porque quotas podem mudar:

| Provedor | Limite gratuito relevante | Consequência operacional |
|---|---|---|
| Render Web Service | 750 horas/mês por workspace; spin down após 15 minutos ocioso; filesystem efêmero; sem disco, SSH ou escala horizontal | cold start próximo de um minuto e perda garantida de qualquer arquivo local em restart/redeploy/spin down |
| Render Static Site | deploy gratuito; consome a franquia compartilhada de bandwidth e pipeline minutes | um novo build pode ser bloqueado ao esgotar pipeline; acompanhe Billing |
| Supabase Free | 500 MB de banco, 5 GB de egress, pausa após uma semana inativo, sem backup automático/PITR/SLA | ao exceder 500 MB o banco pode entrar em read-only; faça dumps externos |
| Cloudflare R2 Standard | 10 GB-mês, 1 milhão de operações Class A, 10 milhões Class B e egress gratuito por mês | PUT/LIST/HEAD/GET consomem operações; a franquia não vale para Infrequent Access |
| Pinecone Starter | 5 índices, 2 GB por organização, 100 namespaces/índice, 2 milhões WU, 1 milhão RU e 1 GB de egress/mês; somente AWS `us-east-1`; sem backups | escrita/consulta é bloqueada ao atingir a quota; preserve originais no R2 para reindexação |
| Gemini Free | acesso limitado por modelo; quotas ativas variam por projeto/modelo em RPM, TPM e RPD | `429 RESOURCE_EXHAUSTED` é esperado ao esgotar quota; confirme o painel AI Studio antes de abrir o demo |

Fontes: [Render Free](https://render.com/docs/free), [Supabase pricing](https://supabase.com/pricing), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [Pinecone limits](https://docs.pinecone.io/reference/api/database-limits), [Pinecone pricing](https://www.pinecone.io/pricing/), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) e [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

`PUBLIC_RETENTION_DAYS=3` remove documentos expirados pelo fluxo de limpeza e limita o crescimento de Postgres, R2 e Pinecone. Aumentar retenção sem recalibrar `MAX_TOTAL_STORED_BYTES`, quotas e custos pode quebrar o objetivo de free tier.

### Proteção contra abuso

- as quotas de upload e consulta são aplicadas às rotas públicas e persistem no Postgres;
- mantenha `RATE_LIMIT_SECRET` longo, aleatório e somente no backend;
- preserve `MAX_DAILY_UPLOADS_PER_CLIENT`, `MAX_DAILY_QUERIES_PER_CLIENT`, `MAX_UPLOAD_SIZE_MB` e `MAX_ACTIVE_DOCUMENTS` conservadores;
- mantenha presigned upload/download TTL curto;
- bucket R2 privado, CORS com origens exatas e token limitado ao bucket;
- não exponha `ADMIN_TOKEN`, keys de provedor ou connection string no frontend;
- acompanhe logs, quotas, uso R2/Pinecone/Gemini e remova acessos comprometidos;
- lembre que quotas do app não substituem rate limits dos provedores ou proteção de rede.

## Migração opcional do legado local

Execute a migração somente em um checkout controlado, com backup dos serviços de destino e permissão de escrita no R2/Postgres. O comando padrão é dry-run e lê `.tmp/rag.db` com `mode=ro`; `--apply` é necessário para escrever. O relatório JSON não inclui valores de exceção, URLs ou segredos:

```powershell
python tools/migrate_local_persistence.py --dry-run --report-path .tmp/migration-report.json
python tools/migrate_local_persistence.py --apply --limit 100 --report-path .tmp/migration-report.json
```

Use `--document-id` para uma migração canária. Use `--reindex-missing` somente depois de confirmar que R2/Postgres estão consistentes; essa opção gera embeddings novamente e pode consumir quota Gemini/Pinecone. A ferramenta não remove nem altera o SQLite ou os arquivos locais e não deve ser executada dentro do container de produção.

Antes do lote completo, repita o canário e execute `python tools/reconcile_persistence.py`; o relatório deve ficar sem inconsistências. Um conflito `doc_id_conflict` ou `sha256_conflict` exige investigação manual e nunca deve ser contornado sobrescrevendo objetos.

### Backup manual e retenção

O plano Free não deve ser considerado backup suficiente. Antes de alterações de schema e em uma cadência definida:

1. Faça um dump do Postgres para fora do workspace público, com a connection string permitida pelo seu ambiente:

   ```powershell
   pg_dump "postgresql://postgres.<project-ref>:<password>@<postgres-host>:5432/postgres?sslmode=require" --format=custom --file "backup-<yyyy-mm-dd>.dump"
   ```

2. Exporte os objetos R2 para armazenamento local protegido ou outro bucket controlado:

   ```powershell
   aws s3 sync s3://<private-r2-bucket> .\backup-r2\<yyyy-mm-dd> `
     --endpoint-url https://<cloudflare-account-id>.r2.cloudflarestorage.com
   ```

3. Registre o índice Pinecone, dimensão, métrica, namespace e contagem. O Starter não deve ser tratado como serviço de backup; para recuperação, mantenha os originais no backup R2 e reindexe o namespace a partir deles.
4. Proteja os dumps com criptografia e retenção fora do Git. Teste periodicamente a restauração em projeto/bucket/namespace de staging.

## 10. Teste Docker local

Crie um arquivo não versionado `.env.docker-test` com valores reais de teste (Postgres acessível pelo container, R2, Pinecone e Gemini). O entrypoint exige `DATABASE_URL` para aplicar migrations e o health completo exige os três provedores; placeholders não resultam em um health `ok`.

Na raiz:

```powershell
docker build -t rag-multimodal-backend .
docker run --rm --name rag-multimodal-backend-test --env-file .env.docker-test -p 10000:10000 rag-multimodal-backend
```

Em outro terminal:

```powershell
curl.exe http://127.0.0.1:10000/api/health
```

Confirme também no container/host de teste:

- logs mostram configuração sem valores de secrets;
- logs mostram `alembic upgrade head` concluído;
- `status=ok`, Postgres `ok`, R2 `ready`, Pinecone `ready` e Gemini `configured`;
- não existe `*.db`, `*.sqlite` ou `*.sqlite3` no filesystem do container;
- não há uploads persistentes fora de `TEMP_PROCESSING_DIR`;
- reiniciar o container não remove documentos, porque eles vivem no Postgres/R2/Pinecone.

## 11. E2E externo isolado

O teste live cobre TXT, PDF, imagem, duplicado, URL expirada, reinício do backend, rate limit persistente, consulta com fonte, documento expirado e exclusão idempotente nos três stores. Ele chama provedores reais e pode consumir créditos. Execute somente com autorização, backup e alvos descartáveis — nunca com o schema `public`, namespace `production` ou prefixo R2 de produção:

```powershell
$env:RUN_LIVE_E2E="1"
$env:TEST_DATABASE_URL="postgresql://<disposable-test-database>?sslmode=require"
$env:TEST_DATABASE_SCHEMA="e2e_<unique-suffix>"
$env:PINECONE_NAMESPACE="e2e-<unique-suffix>"
$env:R2_OBJECT_PREFIX="e2e-<unique-suffix>"
$env:TEMP_PROCESSING_DIR=".tmp/e2e-<unique-suffix>"
$env:MAX_DAILY_QUERIES_PER_CLIENT="2"
$env:R2_PRESIGNED_UPLOAD_TTL_SECONDS="30"
pytest -q tests/test_live_persistence_e2e.py
```

Depois, confirme que o schema, o prefixo R2, o namespace Pinecone e o diretório temporário foram removidos. Falha de cleanup não pode ocultar a falha funcional original.

## Troubleshooting

- **Docker não conecta a `dockerDesktopLinuxEngine`**: inicie o Docker Desktop, aguarde `docker info` responder e só então repita o build. Não marque a imagem como validada enquanto o daemon estiver indisponível.
- **Migration não inicia**: valide a URL Supavisor session mode/porta `5432`, `sslmode=require`, senha com URL-encode e `python -m alembic current`. Não faça `stamp head` para esconder schema incompleto.
- **R2 CORS falha no Browser**: confira origem exata, método `PUT` e todos os headers assinados. Um token restrito a objetos pode retornar `AccessDenied` ao ler CORS; valide a policy pelo dashboard e por um preflight `OPTIONS` real.
- **Presigned URL expirou**: solicite novo presign; nunca persista ou reutilize a URL antiga. O documento `pending_upload` expirado será limpo de forma idempotente.
- **Documento em `failed` após indisponibilidade temporária**: corrija o provedor e use `POST /api/files/<doc_id>/retry` com `X-Admin-Token`. O frontend público não recebe esse token.
- **Gemini encerra com `MAX_TOKENS` ou retorna 429/503**: a API não entrega resposta parcial como sucesso; reduza contexto/saída, espere a quota ou troque para um modelo/tier compatível.
- **Documento preso em `processing`/`deleting`**: execute primeiro `python tools/reconcile_persistence.py`; use `--apply` somente após revisar o JSON e preservar os identificadores externos necessários ao retry.
- **Namespace Pinecone inexistente durante delete/clear**: isso já representa estado vazio e a operação é tratada como sucesso idempotente.
- **Rate limit some após restart**: confirme `DATABASE_URL`, `RATE_LIMIT_SECRET` estável e migrations no mesmo Postgres; as contagens não ficam em memória.

## Rollback recomendado

Rollback de aplicação e rollback de dados são operações distintas:

1. Antes do deploy, gere dump Postgres e cópia do prefixo R2; registre head Alembic, namespace/índice Pinecone e commit da aplicação.
2. Se o novo container falhar antes de processar dados, use o rollback do Render para a imagem anterior e mantenha o schema no head mais novo quando ele for retrocompatível.
3. Não faça downgrade de `0007_response_ownership_constraints`/`0005_visitor_isolation` em produção para desfazer ownership; restaure backup em staging e mantenha o head novo quando possível. Se for indispensável reverter `0004_revoke_data_api`, execute o downgrade apenas em janela controlada, pois isso restaura grants amplos de `anon`/`authenticated` e reduz a segurança.
4. Não faça downgrade destrutivo de `0003`/`0002` em produção sem restaurar um backup testado. Não apague o namespace Pinecone ou o prefixo R2 durante rollback de código.
5. Para inconsistência de dados, suspenda novos uploads, preserve originais, restaure Postgres/R2 em staging, reconcilie e só então promova a recuperação. Vetores podem ser reconstruídos explicitamente a partir dos originais; não reindexe automaticamente.

## Checklist manual de deploy, em ordem exata

1. Criar o projeto Supabase Free e anotar o project ref, região e senha.
2. Copiar a connection string Supavisor **session mode**, porta `5432`, e preparar `DATABASE_URL` com `sslmode=require`.
3. Executar `python -m alembic upgrade head` em um ambiente controlado e confirmar `python -m alembic current`.
4. Criar o bucket R2 privado; confirmar que public access está desativado.
5. Criar token R2 **Object Read & Write** restrito somente ao bucket; guardar Access Key ID e Secret Access Key no gerenciador de segredos.
6. Definir o endpoint S3 R2 e aplicar `infra/r2-cors.json` com a origem final do frontend.
7. Criar/validar o índice Pinecone dense `1536`/`cosine` em `aws/us-east-1` e reservar o prefixo de namespaces `production`.
8. Criar a chave Gemini, confirmar modelos e consultar quotas no AI Studio.
9. Criar o Render Web Service com root vazio, Docker, `./Dockerfile`, plano Free, sem disco persistente e health `/api/health`.
10. Adicionar ao backend todas as variáveis desta documentação, exceto `TEST_DATABASE_URL`; gerar `RATE_LIMIT_SECRET`, `VISITOR_SESSION_SECRET` e `ADMIN_TOKEN` novos e independentes.
11. Fazer o deploy do backend e aguardar o entrypoint concluir as migrations.
12. Testar `https://<backend-service>.onrender.com/api/health` até obter `status=ok` e todos os serviços essenciais prontos.
13. Criar o Render Static Site com root `frontend`, build `npm ci && npm run build`, publish `out` e `NEXT_PUBLIC_API_BASE_URL` apontando para o backend.
14. Atualizar `FRONTEND_ORIGIN` no backend para a URL final do frontend e redeployar o backend.
15. Atualizar CORS R2 com a URL final, mantendo o bucket privado, e reaplicar a política.
16. Fazer upload pequeno pelo Browser e validar presign → PUT direto R2 → complete → processamento → `ready`.
17. Fazer uma pergunta e validar retrieval Pinecone, resposta Gemini e fontes; confirmar que nenhum segredo aparece no Browser.
18. Executar o teste Docker local com `.env.docker-test`, verificar migrations, health e ausência de SQLite/uploads persistentes.
19. Criar o primeiro backup manual de Postgres e R2; registrar a configuração do índice Pinecone.
20. Configurar monitoramento de health, quotas, cold starts, pausa Supabase, consumo dos provedores e rotina de limpeza/reconciliação.

## Checklist de validação pós-deploy

1. Confirmar `alembic current = 0007_response_ownership_constraints (head)`, nove tabelas com RLS, zero policies públicas e zero grants Data API no catálogo.
2. Confirmar `/api/health` com database/R2/Pinecone prontos e Gemini configurado, sem segredos nos logs.
3. Validar CORS do FastAPI com credenciais e um preflight R2 pela origem de produção; rejeitar localhost/origem não autorizada.
4. Fazer upload TXT direto ao R2, `complete`, aguardar `ready`, consultar e abrir uma fonte pré-assinada.
5. Repetir com PDF e imagem; confirmar duplicado por SHA sem novo objeto/vetor.
6. Reiniciar o backend; confirmar que o cookie mantém o visitante, listagem, nova consulta, histórico e quota diária preservados.
7. Abrir dois navegadores/containers isolados; confirmar que A não lista, consulta, avalia ou exclui arquivos, fontes, conversas e mensagens de B.
8. Simular falhas temporárias em staging para R2/Pinecone e falha/limite Gemini; confirmar `failed`, retry administrativo e ausência de resposta parcial.
9. Validar URL expirada, documento `pending_upload` expirado, capacidade global e respostas 429/507 com `Retry-After` quando aplicável.
10. Excluir o documento duas vezes; confirmar estado `deleted`, referências Postgres removidas, prefixo R2 vazio e vetores ausentes.
11. Executar `python tools/reconcile_persistence.py` e aceitar somente relatório sem `ready` órfão, chunks sem vetor, objetos sem registro, vetores removidos pendentes ou estados presos.
12. Conferir uso/alertas dos cinco provedores e registrar o primeiro backup/restore testado.
