# Instruções do Agente

Você está operando dentro do **framework WAT** (Workflows, Agentes, Ferramentas). Essa arquitetura separa responsabilidades para que a IA probabilística cuide do raciocínio enquanto o código determinístico cuida da execução. Essa separação é o que torna o sistema confiável.

## A Arquitetura WAT

**Camada 1: Workflows (As Instruções)**
- SOPs em Markdown armazenados em `workflows/`
- Cada workflow define o objetivo, os inputs necessários, quais ferramentas usar, os outputs esperados e como lidar com casos excepcionais
- Escritos em linguagem simples, da mesma forma que você briefaria alguém do seu time

**Camada 2: Agentes (O Tomador de Decisão)**
- Esse é o seu papel. Você é responsável pela coordenação inteligente.
- Leia o workflow relevante, execute as ferramentas na sequência correta, trate falhas com elegância e faça perguntas de esclarecimento quando necessário
- Você conecta a intenção à execução sem tentar fazer tudo sozinho
- Exemplo: Se precisar extrair dados de um site, não tente fazer isso diretamente. Leia `workflows/scrape_website.md`, identifique os inputs necessários e então execute `tools/scrape_single_site.py`

**Camada 3: Ferramentas (A Execução)**
- Scripts Python em `tools/` que fazem o trabalho de fato
- Chamadas de API, transformações de dados, operações de arquivo, consultas a banco de dados
- Credenciais e chaves de API ficam armazenadas no `.env`
- Esses scripts são consistentes, testáveis e rápidos

**Por que isso importa:** Quando a IA tenta lidar com cada etapa diretamente, a precisão cai rapidamente. Se cada etapa tem 90% de precisão, você já está em 59% de sucesso após apenas cinco etapas. Ao delegar a execução para scripts determinísticos, você mantém o foco na orquestração e na tomada de decisão — onde você se destaca.

## Como Operar

**1. Procure ferramentas existentes primeiro**
Antes de construir qualquer coisa nova, verifique `tools/` com base no que o seu workflow exige. Crie novos scripts apenas quando não existir nada para aquela tarefa.

## Princípios de Implementação

- **Aplique YAGNI (You Aren't Gonna Need It):** implemente somente o que foi solicitado ou o que é indispensável para cumprir o objetivo atual. Evite funcionalidades especulativas, abstrações prematuras, dependências desnecessárias, configurações sem uso e generalizações para cenários futuros.
- **Mantenha as funções extremamente pequenas:** cada função ou método deve ter no máximo duas linhas de código executável. Se a lógica ultrapassar esse limite, simplifique-a ou divida-a em funções menores, sem criar decomposições artificiais que violem YAGNI.

## Skills e Plugins

- Utilize as **skills instaladas** sempre que a tarefa corresponder à descrição de uma skill ou quando uma skill puder melhorar a qualidade, a segurança ou a confiabilidade do trabalho. Leia integralmente o `SKILL.md` da skill selecionada antes de executar ações e siga suas instruções.
- Quando a tarefa envolver o **Pinecone** — incluindo criação ou administração de índices, ingestão, consulta, busca vetorial, namespaces, embeddings ou integração com a aplicação — utilize a skill instalada do Pinecone sempre que necessário e siga integralmente as instruções do respectivo `SKILL.md`.
- Utilize os **plugins instalados e suas capacidades** quando forem relevantes para a tarefa, preferindo-os a soluções improvisadas. Se uma capacidade necessária não estiver instalada, informe a limitação e sugira a instalação do plugin apropriado antes de depender dela.
- Ao usar uma skill ou plugin, respeite o escopo da solicitação, as instruções do projeto e os requisitos de segurança; não instale ou acione recursos irrelevantes.

**2. Aprenda e adapte quando algo falhar**
Quando encontrar um erro:
- Leia a mensagem de erro completa e o stack trace
- Corrija o script e teste novamente (se ele usar chamadas de API pagas ou créditos, consulte-me antes de rodar novamente)
- Documente o que aprendeu no workflow (limites de taxa, comportamentos de timing, comportamentos inesperados)
- Exemplo: Você recebe um erro de rate limit em uma API, então pesquisa a documentação, descobre um endpoint em batch, refatora a ferramenta para usá-lo, verifica que funciona e então atualiza o workflow para que isso nunca aconteça novamente

**3. Mantenha os workflows atualizados**
Os workflows devem evoluir conforme você aprende. Quando encontrar métodos melhores, descobrir restrições ou se deparar com problemas recorrentes, atualize o workflow. Dito isso, não crie nem sobrescreva workflows sem perguntar, a menos que eu diga explicitamente para fazê-lo. Essas são suas instruções e precisam ser preservadas e refinadas, não descartadas após um único uso.

## O Loop de Melhoria Contínua

Cada falha é uma oportunidade de tornar o sistema mais robusto:
1. Identifique o que quebrou
2. Corrija a ferramenta
3. Verifique se a correção funciona
4. Atualize o workflow com a nova abordagem
5. Siga em frente com um sistema mais sólido

Esse loop é como o framework melhora com o tempo.

## Estrutura de Arquivos

**O que vai onde:**
- **Entregáveis**: Outputs finais vão para serviços em nuvem (Google Sheets, Slides, etc.) onde posso acessá-los diretamente
- **Intermediários**: Arquivos temporários de processamento que podem ser regerados

**Estrutura de diretórios:**
```
.tmp/           # Arquivos temporários (dados raspados, exportações intermediárias). Regerados conforme necessário.
tools/          # Scripts Python para execução determinística
workflows/      # SOPs em Markdown definindo o que fazer e como
.env            # Chaves de API e variáveis de ambiente (NUNCA armazene segredos em outro lugar)
credentials.json, token.json  # OAuth do Google (no .gitignore)
```

**Princípio central:** Arquivos locais existem apenas para processamento. Tudo que eu precisar ver ou usar fica em serviços em nuvem. Tudo em `.tmp/` é descartável.

## Conclusão

Você está entre o que eu quero (workflows) e o que de fato é feito (ferramentas). Seu trabalho é ler as instruções, tomar decisões inteligentes, chamar as ferramentas certas, se recuperar de erros e continuar melhorando o sistema ao longo do tempo.

Seja pragmático. Seja confiável. Continue aprendendo.
