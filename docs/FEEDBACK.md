# Feedback de respostas RAG

O feedback é aceito somente para respostas que passaram pelo pipeline RAG e foram persistidas pelo backend.

## Fluxo de resposta

`POST /api/query` executa retrieval, geração e validação. Depois, a API persiste a pergunta, a resposta e o snapshot das fontes em `messages` dentro da mesma transação que cria ou valida a conversa. O `message_id` dessa linha é exposto como `response_id` (o campo `message_id` permanece na resposta por compatibilidade). Se a persistência falhar, a consulta não retorna uma resposta bem-sucedida nem um `response_id` feedbackável.

O cookie HttpOnly `rag_visitor_id`, autenticado por HMAC-SHA256 com `VISITOR_SESSION_SECRET`, é a fonte de `visitor_id`. O cliente não fornece esse valor como autorização; UUID cru ou cookie adulterado não é aceito.

Esse é um isolamento lógico por visitante anônimo, não autenticação de usuário. O backend valida que o `response_id` pertence ao visitante resolvido antes de aceitar o feedback.

## Endpoint

```http
POST /api/feedback
Content-Type: application/json
```

Payload mínimo:

```json
{
  "response_id": "uuid-da-resposta-persistida",
  "useful": true
}
```

O schema rejeita campos extras, tipos inválidos, UUIDs inválidos e valores que não sejam booleanos estritos. `question`, `answer`, `source_ids`, `message_id` e `visitor_id` enviados pelo navegador não são aceitos.

Antes de persistir, o backend busca `response_id` no escopo do visitante atual. Uma resposta inexistente, inventada ou pertencente a outro visitante produz o mesmo `404` genérico e não revela se o ID existe.

## Modelo e duplicidade

`messages` é o modelo canônico de resposta gerada:

- `message_id` / `response_id`;
- `visitor_id`;
- `conversation_id`;
- `question` e `answer` persistidas;
- `source_ids` e `sources` persistidos;
- `insufficient_context` e `created_at`.

`feedback` mantém uma cópia operacional da pergunta, resposta e fontes, mas esses valores são sempre derivados de `messages` no backend. A migration `0006_feedback_response_ownership` cria unicidade em `(visitor_id, message_id)`; a `0007_response_owner_constraints` remove feedback legado sem resposta, torna as duas colunas obrigatórias e cria chaves estrangeiras compostas com `ON DELETE CASCADE`. Uma nova avaliação do mesmo visitante para a mesma resposta atualiza `useful` e retorna o mesmo `feedback_id`.

Depois de `0007`, não permanecem linhas de feedback sem `visitor_id` ou `message_id`.
