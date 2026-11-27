# Fila online de fim de ano — Design

**Data:** 27/11/2026
**Status:** aprovado em conversa; aguardando revisão deste documento
**Prazo:** fechamento da agenda no ar até **13/12** (o site deixa marcar só 7 dias à frente, então em 14/12 já seria possível marcar para o dia 21); fila completa em produção antes de **21/12**.

## 1. Contexto

Dezembro é o mês mais cheio da JP Barbearia e o de mais furo: quem marca horário e não aparece deixa a cadeira vazia. A primeira ideia foi cobrar sinal via Pix. Na reunião de 26/11 o JP recusou o sinal e pediu uma **fila por ordem de chegada, no estilo do Outback**, para os dias de Natal e Ano Novo.

Junto veio um protótipo de interface nova, que fica em `C:\Users\joaoe\Downloads\JP Barbearia.html` (fora do repositório). Ficou combinado:
1. **Agora:** a fila completa, já com o visual do protótipo.
2. **Depois, em outro spec:** o resto do site (Início, Agendar, Meus horários) migrado para o visual novo.

## 2. Decisões tomadas

| Tema | Decisão |
|---|---|
| Agenda nos dias de fila | De **21/12 em diante não existe horário marcado** pelo site. Só fila. |
| Organização | **Uma fila por barbeiro**, mais a opção **"qualquer um"**, que vai com o primeiro barbeiro que liberar. |
| De onde entra | **De qualquer lugar**, mas **só no dia** e com a barbearia aberta. |
| Quem não aparece | Depois de chamado tem **10 min de tolerância**. Se não chegar, o barbeiro marca "Não veio" e o cliente **sai da fila**. Pode entrar de novo, no fim. |
| Aviso ao cliente | **Versão grátis:** página ao vivo, com som e vibração, mais o botão "Chamar" do barbeiro, que abre o WhatsApp com a mensagem pronta. O aviso automático pelo WhatsApp (Z-API) fica como upgrade pago, fora deste spec. |
| Sinal | Não existe neste projeto. |

## 3. Fora do escopo

- Aviso automático pelo WhatsApp, robô e IA (decisões de 26/11 guardadas para quando o upgrade for vendido).
- Tela da fila na TV da barbearia.
- Redesign de Início, Agendar e Meus horários.
- Notificação push do navegador.
- Qualquer mudança no motor de horários (`horarios.py`) além de reusar as funções que já existem.

## 4. Regras de negócio

### 4.1 Período de fila
- O dono cadastra no painel um ou mais **períodos de fila** (data de início e de fim, inclusivas). Para 2026: 21/12 a 31/12.
- **Dia de fila** é qualquer data dentro de um período. O agendamento pelo site fica desligado nesses dias. A fila só existe nesses dias.
- O Caderninho do painel continua funcionando normalmente, porque o barbeiro pode lançar o que quiser.

### 4.2 Quando a fila de hoje está aberta
A fila de um barbeiro está aberta hoje quando **todas** estas condições valem:
- hoje é dia de fila e a barbearia não está fechada (`dia_fechado`);
- o barbeiro está ativo, trabalha hoje e não está de folga (`horario_efetivo`);
- não existe bloqueio de dia inteiro para ele;
- o horário atual está entre a abertura dele e o fechamento.

A opção "qualquer um" está aberta quando pelo menos um barbeiro está aberto.

### 4.3 Entrar na fila
- Campos: **nome** e **telefone** (obrigatórios pelo site), **serviço** e **barbeiro ou "qualquer um"**.
- **Um lugar ativo por telefone por dia.** Se o telefone já está aguardando ou foi chamado, o sistema recusa e explica: "Esse telefone já está na fila. Abra o link que você recebeu ou saia da fila antes de entrar de novo."
- Cliente da lista de bloqueados não entra. A mensagem é a mesma do agendamento (`MENSAGEM_CLIENTE_BLOQUEADO`).
- **A fila fecha sozinha:** a entrada é recusada quando o início estimado mais a duração do serviço não cabe no expediente do barbeiro, usando a mesma regra do agendamento (`cabe_no_expediente` com a tolerância de fim de dia e a margem de última entrada). Para "qualquer um", basta caber em um barbeiro. Mensagem: "A fila de hoje já fechou para esse barbeiro." ou "A fila de hoje já fechou."
- O cliente recebe um **link próprio** (com um código secreto) para acompanhar a vez. O código também fica salvo no aparelho.

### 4.4 Estados de um lugar na fila
```
aguardando → chamado → atendendo → concluido
     │           │
     │           └→ nao_veio   (barbeiro marcou depois da tolerância)
     ├→ saiu      (o próprio cliente saiu pelo link)
     └→ removido  (barbeiro ou dono tirou da fila)
```
Ativos são só `aguardando` e `chamado`. Um lugar em `atendendo` ocupa o barbeiro, mas não conta como "na fila".

### 4.5 Quem é chamado
- O barbeiro aperta **"Chamar próximo"**. O sistema pega, entre os `aguardando` da fila dele **e** da fila "qualquer um", **o que entrou primeiro**. Esse lugar fica ligado ao barbeiro que chamou.
- O dono e o login do salão podem chamar por qualquer barbeiro, escolhendo qual.
- Também dá para chamar um lugar específico, fora da ordem. Serve para o caso de "o cliente já está aqui na frente".
- Um barbeiro só tem **um chamado por vez**. Chamar outro exige resolver o atual antes (Começar, Não veio ou Remover).

### 4.6 Tolerância
- A partir do momento em que é chamado, o cliente tem **10 min** (constante `TOLERANCIA_FILA_MIN`, configurável por variável de ambiente).
- O painel mostra a contagem. Passado o prazo, o cartão fica vermelho com "Passou da tolerância".
- **A saída não é automática.** O barbeiro marca "Não veio", porque pode estar vendo o cliente chegar na porta.

### 4.7 Começar e concluir
- **Começar** cria um atendimento normal na tabela `agendamentos`, pela mesma função do Caderninho (`_processar_novo_agendamento`, com `permitir_conflito=True` e `exigir_antecedencia=False`), com a data de hoje, a hora atual (HH:MM), `origem = 'fila'` e o barbeiro que chamou. A partir daí, valor, comissão, forma de pagamento, adicionais, contagem, ficha do cliente e relatórios funcionam como hoje, sem nenhuma mudança.
- Começar vale para quem foi `chamado` e também direto de `aguardando`, para o caso de o cliente já estar na frente do barbeiro. Nesse caso o lugar fica ligado a quem começou.
- **Concluir** só marca o lugar como `concluido` e libera o barbeiro na estimativa. O agendamento continua `confirmado`, como acontece hoje com qualquer corte.

## 5. Estimativa de espera

Uma função pura em `back/fila.py`, sem banco, testável isoladamente:

```
estimar_fila(agora_min, barbeiros, lugares) -> {lugar_id: inicio_estimado_min}
```

- `barbeiros`: para cada barbeiro aberto hoje, `livre_a_partir` (fim do atendimento em curso: `hora + duracao` do agendamento de quem está `atendendo`, ou `agora`, o que for maior), as **janelas ocupadas do dia** e o expediente.
  - As janelas vêm de `janelas_ocupadas`: almoço fixo, almoço manual e bloqueios. Agendamentos já criados também entram, o que cobre qualquer horário marcado antes da virada para a fila.
  - Agendamentos ligados a lugares `concluido` ficam de fora, para um corte que terminou antes não segurar a estimativa.
- `lugares`: os `chamado` primeiro (já presos a um barbeiro, começam em `max(agora, livre_a_partir)`), depois os `aguardando` em ordem de entrada.
- Para cada lugar, os candidatos são o barbeiro escolhido ou, para "qualquer um", todos os abertos. Para cada candidato procura-se o primeiro início ≥ `livre_a_partir` que não colide com nenhuma janela (`primeiro_conflito`), avançando até o fim da janela que colidiu. Vence o menor início, e no empate o menor id de barbeiro. O barbeiro vencedor passa a estar livre em `inicio + duracao`.
- **Posição** mostrada ao cliente = 1 + quantos lugares ativos entraram antes dele e podem ir para o mesmo barbeiro (os da fila dele mais os de "qualquer um"; para quem escolheu "qualquer um", todos).
- **Espera** mostrada = `inicio_estimado - agora`, arredondada para cima de 5 em 5 min, com mínimo de 0. O texto é sempre "uns X min", nunca um horário prometido.

## 6. Dados

Migração aditiva em `database.py`, no mesmo estilo das outras (`CREATE TABLE IF NOT EXISTS` e `ADD COLUMN IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS periodos_fila (
    id SERIAL PRIMARY KEY,
    inicio TEXT NOT NULL,            -- 'YYYY-MM-DD'
    fim    TEXT NOT NULL,            -- 'YYYY-MM-DD', inclusivo
    criado_por INTEGER,              -- admin.id
    criado_em TIMESTAMPTZ DEFAULT now(),
    CHECK (fim >= inicio)
);

CREATE TABLE IF NOT EXISTS fila (
    id SERIAL PRIMARY KEY,
    data TEXT NOT NULL,                              -- dia da fila
    token TEXT NOT NULL UNIQUE,                      -- secrets.token_urlsafe(24)
    cliente_id INTEGER REFERENCES clientes (id),
    nome TEXT NOT NULL,
    telefone TEXT,                                   -- só dígitos; opcional quando entra pelo painel
    servico_id INTEGER NOT NULL REFERENCES servicos (id),
    duracao_min INTEGER NOT NULL,                    -- foto da duração na entrada
    barbeiro_id INTEGER REFERENCES barbeiros (id),   -- NULL = qualquer um
    atendido_por INTEGER REFERENCES barbeiros (id),  -- quem chamou
    status TEXT NOT NULL DEFAULT 'aguardando',
    origem TEXT NOT NULL DEFAULT 'site',             -- 'site' | 'painel'
    entrou_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    chamado_em TIMESTAMPTZ,
    finalizado_em TIMESTAMPTZ,
    agendamento_id INTEGER REFERENCES agendamentos (id)
);
CREATE INDEX IF NOT EXISTS ix_fila_data_status ON fila (data, status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fila_telefone_ativo
    ON fila (data, telefone) WHERE status IN ('aguardando', 'chamado') AND telefone IS NOT NULL;
```

- O cliente é registrado pela mesma `buscar_ou_criar_cliente`, para a ficha juntar o histórico pelo telefone.
- Telefones da lista de genéricos (o número da barbearia) **não** entram na regra de um lugar por telefone. Na entrada pelo painel eles são gravados como `NULL` em `fila.telefone`.
- Disputa: entrar, chamar e começar travam a linha do barbeiro (`SELECT ... FOR UPDATE`), como já faz o agendamento. Chamar próximo também trava a fila do dia, para dois barbeiros não chamarem a mesma pessoa.

## 7. API

### Pública (site do cliente)
| Rota | O que faz |
|---|---|
| `GET /api/fila/hoje` | Se hoje é dia de fila e, para cada barbeiro aberto e para "qualquer um": aberta ou fechada, quantos esperam e espera estimada para quem entrar agora. Sem nomes nem telefones. |
| `POST /api/fila` | Entra na fila. Corpo: `nome`, `telefone`, `servico_id`, `barbeiro_id` (ou `null`), `origem`/`campanha` (as mesmas UTMs do agendamento). Devolve `token`. Limite de 5 por minuto por IP. |
| `GET /api/fila/<token>` | Situação do próprio lugar: status, posição, espera, serviço, barbeiro (nome) e `chamado_em`. Limite de 30 por minuto. |
| `POST /api/fila/<token>/sair` | O cliente sai. Só vale para `aguardando` ou `chamado`. |

### Mudanças nas rotas que já existem
- `GET /api/horarios-disponiveis` e `/api/horarios-disponiveis-periodo`: num dia de fila não devolvem horários e trazem `modo_fila: true`, para o site mostrar o motivo.
- `POST /api/agendamentos` (público): num dia de fila recusa com 400 e a mensagem "Nesse dia o atendimento é por ordem de chegada. Entre na fila pelo site no dia." O teste de que "todo horário mostrado é aceito" continua passando, porque nenhum horário é mostrado nesses dias.

### Painel (com login)
| Rota | Quem | O que faz |
|---|---|---|
| `GET /api/admin/fila` | todos | Lugares de hoje (ativos, em atendimento e os finalizados do dia), com a estimativa. O barbeiro vê a própria fila e a de "qualquer um"; o dono e o salão veem tudo. |
| `POST /api/admin/fila` | todos | Põe na fila quem chegou na porta. Telefone opcional. |
| `POST /api/admin/fila/chamar-proximo` | todos | Corpo: `barbeiro_id` (forçado para o próprio no papel barbeiro). Devolve o lugar chamado e o link do WhatsApp quando há telefone. |
| `POST /api/admin/fila/<id>/chamar` | todos | Chama um lugar específico. |
| `POST /api/admin/fila/<id>/comecar` | todos | Cria o agendamento e passa para `atendendo`. |
| `POST /api/admin/fila/<id>/concluir` | todos | `atendendo` → `concluido`. |
| `POST /api/admin/fila/<id>/nao-veio` | todos | `chamado` → `nao_veio`. |
| `POST /api/admin/fila/<id>/remover` | todos | `aguardando`/`chamado` → `removido`. |
| `GET/POST/DELETE /api/admin/periodos-fila` | só dono | Lista, cria e apaga períodos. |

Ações fora do escopo do usuário (barbeiro mexendo na fila de outro barbeiro) devolvem 403. Transição inválida (concluir quem não começou, por exemplo) devolve 409 com a explicação.

A mensagem do WhatsApp ao chamar é: "Fala, {primeiro nome}! Chegou sua vez na JP Barbearia com o {barbeiro}. Você tem 10 min pra chegar." O link é montado por `notificacoes.link_whatsapp`, que já existe.

## 8. Telas

### 8.1 Visual
O visual vem do protótipo: fundo escuro, dourado `#F5B21B`, títulos em **Big Shoulders Display**, texto em **Inter**, cartões com borda fina e botões grandes. Os tokens ficam num arquivo novo, `front/tema.css`, que o redesign das outras telas vai reaproveitar.

### 8.2 Site do cliente: `front/fila.html` + `front/fila.js`
Uma página nova e independente do assistente de agendamento atual.
- **Sem lugar ativo neste aparelho:** cabeçalho "Fila agora" e, por barbeiro, um cartão com a foto, quantos esperam e "uns X min". Vem também o cartão "Qualquer um" com a menor espera. Abaixo, o formulário com nome, WhatsApp, serviço e o barbeiro escolhido no cartão. Barbeiro com a fila fechada aparece apagado, com o motivo ("Fila fechada", "De folga hoje").
- **Fora de dia de fila ou antes de abrir:** mensagem com o próximo dia e horário em que a fila abre.
- **Na fila:** a posição grande ("4º"), "3 na sua frente · uns 35 min", o barbeiro e o serviço, o botão "Sair da fila" (pede confirmação na própria página) e a orientação "Deixe esta página aberta para ouvir o aviso".
- **Perto (2 ou menos na frente):** uma faixa dourada "Já vem vindo" e uma vibração curta, uma vez só.
- **Chamado:** a tela inteira em destaque, "É sua vez! Você tem até {hora} para chegar", com som repetido (gerado no navegador, sem arquivo) e vibração. O botão "Estou indo" só para o som.
- **Finalizado** (saiu, não veio ou removido): explica o que aconteceu e oferece "Entrar de novo".
- A página consulta a situação a cada **15 s** com a página visível e na hora em que ela volta a ficar visível. Se a consulta falhar, mostra "Reconectando…" e tenta de novo, como o site já faz quando o servidor está acordando.
- O som só toca depois de um toque do cliente na página, que é regra dos navegadores. O toque em "Entrar na fila" já conta. Se a página for reaberta depois, aparece o botão "Ativar aviso sonoro".
- **No iPhone a vibração não funciona.** O aviso por som e pela tela continua valendo.

**`front/index.html`:** num dia de fila, o assistente atual mostra no topo um cartão "Hoje é por ordem de chegada → Entrar na fila" apontando para `fila.html`. Ao escolher uma data de fila no calendário, aparece a mesma explicação no lugar dos horários.

### 8.3 Painel: aba "Fila" em `admin.html`
Nova aba nos três papéis (dono, barbeiro, salão), em primeiro lugar nos dias de fila. Ela usa os tokens de `tema.css`, aplicados só dentro da aba.
- No topo, por barbeiro: **"Chamar próximo"** grande e, embaixo, quem está na cadeira e quem foi chamado, com a contagem da tolerância.
- Lista dos que aguardam: nome, serviço, há quanto tempo entrou, se escolheu o barbeiro ou "qualquer um", e espera estimada.
- Ações por cartão, conforme o estado: Chamar, WhatsApp (abre a mensagem pronta), Começar, Concluir, Não veio, Remover.
- **"+ Pôr na fila"** para quem chegou na porta.
- Atualiza a cada 10 s.
- **Dono, em Gerenciar → Expediente:** cartão "Dias de fila", com os períodos cadastrados, um formulário de início e fim e o botão de apagar.

## 9. Erros e casos de borda
- **Barbeiro some do dia** (folga lançada ou bloqueio de dia inteiro depois que já tinha gente esperando): os lugares dele continuam na lista com o aviso "Barbeiro indisponível hoje". O painel mostra isso ao dono para ele redistribuir ou remover. Não há redistribuição automática.
- **Almoço:** a estimativa pula a janela de almoço. Lugares presos àquele barbeiro esperam o fim do almoço.
- **Servidor dormindo:** a fila só funciona em horário comercial, que o keep-alive (06h às 24h) já cobre. A página tenta de novo sozinha se a primeira consulta demorar.
- **Link perdido:** o cliente não tem como recuperar o lugar sozinho. O barbeiro vê pelo nome no painel. Se o cliente tentar entrar de novo com o mesmo telefone, a mensagem explica que ele já está na fila e que pode pedir no balcão.
- **Virada do dia:** lugares que ainda estiverem ativos de um dia anterior não aparecem mais (tudo é filtrado pela data de hoje).

## 10. Testes
Backend (pytest, como os atuais):
- `estimar_fila`: fila de um barbeiro; "qualquer um" indo para quem libera primeiro; empate; almoço no meio; chamado em curso; atendimento terminando antes do previsto.
- Fila fechando por fim de expediente, com a tolerância e a margem de última entrada.
- Um lugar por telefone, telefone genérico liberado, cliente bloqueado recusado.
- Chamar próximo respeitando ordem e escopo; dois barbeiros chamando ao mesmo tempo; um chamado por barbeiro.
- Transições válidas e inválidas; Começar criando o agendamento com `origem = 'fila'`, preço, duração e comissão iguais aos do Caderninho.
- Dia de fila sem horários e com agendamento público recusado; dia normal sem mudança nenhuma.
- Rotas do período só para o dono.

Front (Playwright, interceptando a API como os testes atuais):
- `fila.html` em cada estado: escolher, na fila, perto, chamado, finalizado, fora do dia e reconectando.
- Aba Fila do painel: chamar próximo, começar e concluir, nos papéis barbeiro e salão.

## 11. Ordem de entrega
1. **Período de fila e agenda desligada nesses dias.** É o que precisa estar em produção até **13/12**.
2. Tabela `fila`, `estimar_fila` e rotas públicas e do painel, com testes.
3. `tema.css` e `fila.html`.
4. Aba Fila no painel e o cartão de períodos.
5. Ensaio com o JP antes do dia 21: simular uma manhã de fila com dois celulares e o painel.
