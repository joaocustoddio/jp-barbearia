# Fila online de fim de ano — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nos dias de fila (21 a 31/12) o site deixa de marcar horário e o cliente entra numa fila por barbeiro, ou "qualquer um", acompanhando a vez numa página ao vivo, enquanto o barbeiro chama, começa e conclui pelo painel.

**Architecture:** As regras ficam numa função pura (`back/fila.py`), sem banco e testada isoladamente. O acesso ao banco fica em `back/fila_dados.py` e as rotas em `back/rotas_fila.py`. Começar um atendimento cria um agendamento normal pela função do Caderninho, então caixa, comissão e relatórios não mudam. No front entram uma página nova (`fila.html`), um arquivo de tokens com o visual do protótipo (`tema.css`) e a aba "Fila" do painel num arquivo próprio (`admin-fila.js`).

**Tech Stack:** Python 3, Flask, psycopg2/PostgreSQL, pytest (conexão falsa via monkeypatch), JavaScript sem framework, Playwright com `node:test` e API interceptada.

**Spec:** `docs/superpowers/specs/2026-11-27-fila-online-design.md`

## Global Constraints

- Período de fila: datas `inicio` e `fim` inclusivas, formato `'YYYY-MM-DD'`. Só o papel `master` cria e apaga.
- Tolerância depois de chamado: `TOLERANCIA_FILA_MIN = int(os.getenv("TOLERANCIA_FILA_MINUTOS", "10"))`, em `config.py`.
- A fila fecha pela mesma regra do agendamento: `cabe_no_expediente(inicio, dur, abertura, fechamento, tolerancia_para(dur), MARGEM_ULTIMA_ENTRADA_MIN)`.
- Status de `fila.status`: `aguardando`, `chamado`, `atendendo`, `concluido`, `nao_veio`, `saiu`, `removido`. Ativos são só `aguardando` e `chamado`.
- Um lugar ativo por telefone (só dígitos) por dia. Telefone da tabela `telefones_genericos` não conta para essa regra e é gravado como `NULL`.
- Começar cria o agendamento com `_processar_novo_agendamento(dados, exigir_antecedencia=False, permitir_conflito=True)` e `dados["origem"] = "fila"`.
- Espera mostrada: arredondada para cima de 5 em 5 min, mínimo 0, sempre como "uns X min".
- Mensagens exatas, copiadas do spec:
  - telefone repetido: `"Esse telefone já está na fila. Abra o link que você recebeu ou saia da fila antes de entrar de novo."`
  - fila fechada para um barbeiro: `"A fila de hoje já fechou para esse barbeiro."`
  - fila fechada para "qualquer um": `"A fila de hoje já fechou."`
  - agendamento num dia de fila: `"Nesse dia o atendimento é por ordem de chegada. Entre na fila pelo site no dia."`
  - WhatsApp ao chamar: `"Fala, {primeiro nome}! Chegou sua vez na JP Barbearia com o {barbeiro}. Você tem 10 min pra chegar."`
- Visual: fundo `#0C0C0E`, dourado `#F5B21B`, títulos em "Big Shoulders Display" e texto em "Inter", vindos do protótipo.
- Os arquivos de front são carregados com `?v=N`. Todo arquivo alterado tem a versão aumentada no `<script>` ou `<link>` que o carrega.
- Não acessar o banco de produção nem publicar durante a implementação. Trabalhar no branch `fila-online`.
- Commits em português, no estilo do repositório, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Dois toques em "Entrar na fila" ou duas abas com o mesmo telefone ao mesmo tempo.** A segunda tentativa recebe 409 com a mensagem de telefone repetido, nunca 500. O índice único é a última defesa. Teste na Task 4.
2. **Telefone com e sem máscara** ("(11) 98888-7777" e "11988887777") é o mesmo telefone. Teste na Task 4.
3. **Barbeiro tira folga, ou o dia dele é bloqueado, com gente esperando.** A estimativa não quebra, os lugares dele aparecem como indisponíveis e ninguém é jogado para outro barbeiro sozinho. Testes nas Tasks 3 e 4.
4. **Entrar perto de fechar** (ex.: 18:45 de sábado, que fecha às 19:00, pedindo um Degradê de 40 min) recebe a mensagem de fila fechada e não fica esperando um corte que não vai acontecer. Teste na Task 4.
5. **Link velho ou inventado** (`/api/fila/abc`) devolve 404, e a página mostra "Esse link não vale mais" com o botão de entrar de novo, sem ficar presa em "Reconectando…". Testes nas Tasks 4 e 6.

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `back/config.py` (alterar) | `TOLERANCIA_FILA_MIN`. |
| `back/database.py` (alterar) | Tabelas `periodos_fila` e `fila`, com índices. |
| `back/fila.py` (novo) | Regras puras: estimativa, posição, espera, próximo da vez, transições. |
| `back/fila_dados.py` (novo) | Leitura e escrita da fila e dos períodos no banco. |
| `back/rotas_fila.py` (novo) | Rotas públicas, do painel e dos períodos. |
| `back/app.py` (alterar) | Importar `rotas_fila`; horários vazios num dia de fila. |
| `back/rotas_publicas.py` (alterar) | Recusar agendamento público num dia de fila. |
| `back/tests/test_fila_regras.py` (novo) | Testes de `fila.py`. |
| `back/tests/test_periodos_fila.py` (novo) | Períodos e agenda desligada. |
| `back/tests/test_rotas_fila.py` (novo) | Rotas públicas e do painel. |
| `front/tema.css` (novo) | Tokens do visual novo. |
| `front/fila.html`, `front/fila.js` (novos) | Página da fila do cliente. |
| `front/api.js` (alterar) | Chamadas `API.fila.*` e `API.admin.fila.*`. |
| `front/script.js`, `front/index.html` (alterar) | Cartão "Hoje é por ordem de chegada" e mensagem no lugar dos horários. |
| `front/admin-fila.js` (novo) | Aba "Fila" e cartão "Dias de fila". |
| `front/admin.js`, `front/admin.html` (alterar) | Registrar a aba e carregar `admin-fila.js`. |
| `front/tests/fila.test.cjs` (novo) | Testes de interface da fila. |

---

### Task 0: Branch

- [ ] **Step 1:** `git switch -c fila-online` a partir de `main`.
- [ ] **Step 2:** Confirmar a base verde. Rodar `cd back && pytest -q`, esperado: tudo passa. Rodar `cd front && npm test`, esperado: tudo passa.

### Task 1: Períodos de fila e agenda desligada (backend)

Esta é a entrega que precisa estar em produção até 13/12.

**Files:**
- Modify: `back/database.py` (tabela `periodos_fila` em `init_db`, depois de `expedientes`)
- Create: `back/fila_dados.py`
- Create: `back/rotas_fila.py` (só as rotas de período nesta task)
- Modify: `back/app.py` (import de `rotas_fila`; `horarios_disponiveis` e `horarios_disponiveis_periodo`)
- Modify: `back/rotas_publicas.py` (`criar_agendamento`)
- Test: `back/tests/test_periodos_fila.py`

**Interfaces:**
- Produces:
  - `fila_dados.dia_de_fila(conn, data: str) -> bool`
  - `fila_dados.dias_de_fila(conn, inicio: str, fim: str) -> set[str]`: as datas entre `inicio` e `fim` que caem em algum período.
  - `fila_dados.listar_periodos(conn) -> list[dict]`: chaves `id`, `inicio`, `fim`, ordenadas por `inicio`.
  - `fila_dados.criar_periodo(conn, inicio: str, fim: str, admin_id: int) -> int`
  - `fila_dados.apagar_periodo(conn, periodo_id: int) -> bool`
  - Rotas `GET/POST /api/admin/periodos-fila` e `DELETE /api/admin/periodos-fila/<id>`, com `@token_requerido` e `@somente_master`. O POST recebe `{inicio, fim}` e devolve 201 com `{id}`. Datas inválidas ou `fim < inicio` devolvem 400.
  - `horarios_disponiveis` num dia de fila: `{"data", "barbeiro_id", "horarios_disponiveis": [], "modo_fila": true}`.
  - `horarios_disponiveis_periodo`: cada dia de fila sai com a lista vazia e `"modo_fila": true` no objeto do dia.

- [ ] **Step 1: Escrever os testes que falham** em `test_periodos_fila.py`, com a conexão falsa e o `cabecalho()` copiados de `test_bloqueios_dia.py`:
  - `test_horarios_vazio_em_dia_de_fila`: `monkeypatch.setattr(app.fila_dados, "dia_de_fila", lambda c, d: True)`. `GET /api/horarios-disponiveis?data=2099-12-21&barbeiro_id=1` devolve `horarios_disponiveis == []` e `modo_fila is True`.
  - `test_horarios_normal_fora_do_periodo`: com `dia_de_fila` devolvendo False, a resposta não tem a chave `modo_fila`.
  - `test_periodo_marca_so_os_dias_de_fila`: com `dias_de_fila` devolvendo `{"2099-12-21"}` e `dias=2` a partir de 2099-12-20, só o dia 21 vem com `modo_fila`.
  - `test_agendamento_publico_recusado_em_dia_de_fila`: o POST `/api/agendamentos` devolve 400 com a mensagem exata dos Global Constraints, e `_processar_novo_agendamento` não é chamado (verificar com um monkeypatch que falha o teste se for chamado).
  - `test_periodo_so_master`: o POST em `/api/admin/periodos-fila` com papel `barbeiro` devolve 403. Com `master` e `{"inicio":"2099-12-21","fim":"2099-12-31"}` devolve 201.
  - `test_periodo_fim_antes_do_inicio`: `{"inicio":"2099-12-31","fim":"2099-12-21"}` devolve 400.
- [ ] **Step 2: Rodar** `cd back && pytest tests/test_periodos_fila.py -q`. Esperado: FAIL (módulo `fila_dados` inexistente).
- [ ] **Step 3: Implementar.**
  - Criar a tabela `periodos_fila` exatamente como no spec, seção 6.
  - Criar `fila_dados.py` com as funções acima (`dia_de_fila` usa `WHERE %s BETWEEN inicio AND fim`).
  - Criar `rotas_fila.py` com as rotas de período e importá-lo em `app.py` junto dos outros (`import rotas_fila  # noqa: F401`), mais `import fila_dados` no topo de `app.py`, para os testes poderem trocar `app.fila_dados`.
  - Em `horarios_disponiveis`, chamar `fila_dados.dia_de_fila` logo depois de abrir a conexão. Em `horarios_disponiveis_periodo`, chamar `fila_dados.dias_de_fila` uma vez só para o intervalo todo.
  - Em `rotas_publicas.criar_agendamento`, a checagem vem antes de `_processar_novo_agendamento`, abrindo e fechando a própria conexão. Só vale quando `dados.get("data")` é uma string.
- [ ] **Step 4: Rodar** `pytest -q`. Esperado: tudo passa, inclusive o teste antigo que garante que todo horário mostrado é aceito.
- [ ] **Step 5: Commit** com a mensagem "Fecha a agenda do site nos dias de fila".

### Task 2: Períodos no painel e aviso no site (front)

**Files:**
- Create: `front/admin-fila.js` (nesta task, só `renderDiasDeFila`)
- Modify: `front/admin.js` (`renderExpediente` chama `renderDiasDeFila` quando o papel é master), `front/admin.html` (carregar `admin-fila.js?v=1` antes de `admin.js` e aumentar a versão de `admin.js`)
- Modify: `front/api.js` (`API.admin.periodosFila.listar()`, `.criar(inicio, fim)`, `.apagar(id)`)
- Modify: `front/script.js` (passo da data: um dia com `modo_fila` aparece no calendário como "Fila" e, ao ser escolhido, mostra a explicação com o link para `fila.html`), `front/index.html` (aumentar versões)
- Test: `front/tests/fila.test.cjs`

**Interfaces:**
- Consumes: as rotas de período e o `modo_fila` da Task 1.
- Produces: `window.renderDiasDeFila(container: HTMLElement): Promise<void>` em `admin-fila.js`.

- [ ] **Step 1: Escrever os testes que falham** em `fila.test.cjs`, com o `pagina()` copiado de `agenda.test.cjs` e com respostas de período interceptadas:
  - `dias de fila: master cria período`: o papel master abre Expediente, preenche início 2099-12-21 e fim 2099-12-31 e clica em "Salvar dias de fila". A requisição POST para `/api/admin/periodos-fila` leva o corpo `{inicio:'2099-12-21',fim:'2099-12-31'}`.
  - `dias de fila: barbeiro não vê o cartão`: com o papel barbeiro, o texto "Dias de fila" não aparece.
  - `site: dia de fila mostra o caminho da fila`: com `/api/horarios-disponiveis-periodo` devolvendo o dia 2099-12-21 com `modo_fila:true`, escolher esse dia mostra o texto "Nesse dia o atendimento é por ordem de chegada" e um link para `fila.html`.
- [ ] **Step 2: Rodar** `cd front && npm test`. Esperado: os 3 testes novos falham.
- [ ] **Step 3: Implementar.** O cartão lista os períodos ("21/12 a 31/12" com o botão Apagar) e tem o formulário de início e fim. Apagar pede confirmação na própria página, porque o painel não usa `confirm()`. No site, reusar o estilo do aviso de dia sem vaga que o `script.js` já tem.
- [ ] **Step 4: Rodar** `npm test`. Esperado: tudo passa.
- [ ] **Step 5: Commit** com a mensagem "Dono cadastra os dias de fila e o site avisa".

> **Marco 1:** as Tasks 1 e 2 já podem ir para produção sozinhas. Publicar seguindo a **Publicação 1** (no fim deste plano), por volta de 5/12 e no máximo até 13/12.

### Task 3: Regras puras da fila

**Files:**
- Modify: `back/config.py` (`TOLERANCIA_FILA_MIN`)
- Create: `back/fila.py`
- Test: `back/tests/test_fila_regras.py`

**Interfaces:**
- Consumes: `horarios.primeiro_conflito`, `horarios.cabe_no_expediente`, `horarios.Janela`, `config.tolerancia_para`, `config.MARGEM_ULTIMA_ENTRADA_MIN`.
- Produces:
```python
@dataclass(frozen=True)
class BarbeiroDia:
    id: int
    abertura: int          # minutos desde 00:00
    fechamento: int
    livre_a_partir: int    # minutos; já é max(agora, fim do atendimento em curso)
    ocupadas: tuple        # tuple[Janela]

@dataclass(frozen=True)
class Lugar:
    id: int                # ordem de entrada: id menor entrou antes
    barbeiro_id: int | None   # escolhido; None = qualquer um
    atendido_por: int | None  # preenchido quando chamado
    status: str               # 'aguardando' | 'chamado'
    duracao_min: int

class TransicaoInvalida(Exception): ...

def estimar_fila(agora_min: int, barbeiros: list[BarbeiroDia], lugares: list[Lugar]) -> dict[int, tuple[int, int]]
    # lugar.id -> (barbeiro_id, inicio_min). Lugar cujo barbeiro não está na lista fica fora do dict.
def cabe_mais_um(agora_min: int, barbeiros: list[BarbeiroDia], lugares: list[Lugar], barbeiro_id: int | None, duracao_min: int) -> bool
def posicao(lugar_id: int, lugares: list[Lugar]) -> int
def arredondar_espera(minutos: int) -> int
def escolher_proximo(lugares: list[Lugar], barbeiro_id: int) -> Lugar | None
def proximo_status(atual: str, acao: str) -> str   # acao em: chamar, comecar, concluir, nao_veio, remover, sair
```

- [ ] **Step 1: Escrever os testes que falham.** Todos com `agora_min = 600` (10:00), barbeiros com abertura 540, fechamento 1200 e sem janelas, salvo quando o teste diz outra coisa:
  - `test_um_barbeiro_em_sequencia`: 3 lugares de 30 min para o barbeiro 1 começam em 600, 630 e 660.
  - `test_qualquer_um_vai_com_quem_libera_primeiro`: o barbeiro 1 está livre a partir de 640 e o 2 a partir de 610. Um "qualquer um" fica com `(2, 610)`.
  - `test_empate_vai_para_o_menor_id`: os dois livres em 600, o "qualquer um" fica com `(1, 600)`.
  - `test_pula_o_almoco`: o barbeiro 1 tem `Janela(660, 720, "almoco")`. Lugares de 40 min ficam em 600 e 720 (o de 640 colidiria com o almoço).
  - `test_chamado_vem_antes_de_quem_aguarda`: um `chamado` com `atendido_por=1` e id maior que um `aguardando` do barbeiro 1 começa primeiro.
  - `test_barbeiro_indisponivel_fica_fora`: um lugar do barbeiro 3, que não está na lista, não aparece no resultado e não derruba os outros.
  - `test_cabe_mais_um_recusa_perto_de_fechar`: barbeiro com fechamento 1140 (19:00) e livre a partir de 1125 (18:45). `cabe_mais_um(..., 1, 40)` é False e `cabe_mais_um(..., 1, 10)` é True.
  - `test_cabe_mais_um_qualquer_um_basta_um`: o barbeiro 1 está cheio até o fechamento e o 2 está livre. `cabe_mais_um(..., None, 30)` é True.
  - `test_posicao_conta_so_concorrentes`: lugares em ordem `[A(barbeiro 1), B(barbeiro 2), C(qualquer um), D(barbeiro 1)]`. `posicao(D) == 3` (A e C), `posicao(B) == 1`, `posicao(C) == 3` (A e B).
  - `test_arredondar_espera`: 0→0, 1→5, 35→35, 36→40, -3→0.
  - `test_escolher_proximo_pega_o_mais_antigo_entre_meu_e_qualquer`: `[A(barbeiro 2), B(qualquer um), C(barbeiro 1)]` com o barbeiro 1 escolhe B. Com o barbeiro 2 escolhe A. Lugares `chamado` são ignorados.
  - `test_transicoes`: `aguardando` com chamar vira `chamado`, com comecar vira `atendendo`, com sair vira `saiu`. `chamado` com nao_veio vira `nao_veio`. `atendendo` com concluir vira `concluido`. `aguardando` com concluir levanta `TransicaoInvalida`, e `concluido` com qualquer ação também.
- [ ] **Step 2: Rodar** `pytest tests/test_fila_regras.py -q`. Esperado: FAIL (import).
- [ ] **Step 3: Implementar `fila.py`.** O algoritmo de `estimar_fila`:
```
livre = {b.id: b.livre_a_partir for b in barbeiros}
ordem = chamados (por id) + aguardando (por id)
para cada lugar:
    candidatos = [lugar.atendido_por] se chamado
                 senão [lugar.barbeiro_id] se escolhido
                 senão todos os barbeiros
    (ignorar candidatos que não estão em `livre`; sem candidato -> pular o lugar)
    para cada candidato: inicio = livre[c]
        enquanto primeiro_conflito(inicio, dur, ocupadas_c) devolver janela j: inicio = j.fim
    vencedor = menor (inicio, id)
    resultado[lugar.id] = (vencedor, inicio); livre[vencedor] = inicio + dur
```
`cabe_mais_um` simula com um `Lugar(id=10**9, ...)` no fim. Para `None`, testa cada barbeiro como escolhido e devolve True se algum couber, conferindo com `cabe_no_expediente` do barbeiro que ficou com o lugar.
- [ ] **Step 4: Rodar** `pytest tests/test_fila_regras.py -q`. Esperado: PASS.
- [ ] **Step 5: Commit** com a mensagem "Regras da fila: estimativa, posição e transições".

### Task 4: Tabela da fila e rotas públicas

**Files:**
- Modify: `back/database.py` (tabela `fila` e os dois índices, como no spec, seção 6)
- Modify: `back/fila_dados.py`
- Modify: `back/rotas_fila.py`
- Test: `back/tests/test_rotas_fila.py`

**Interfaces:**
- Consumes: Task 3 inteira; `agendamentos.buscar_ou_criar_cliente`, `agendamentos.cliente_esta_bloqueado`, `agendamentos.so_digitos`, `agendamentos.montar_janelas`, `agendamentos.MENSAGEM_CLIENTE_BLOQUEADO`, `validacoes.horario_efetivo`, `validacoes.dia_fechado`, `validacoes.validar_nome`, `validacoes.validar_telefone`, `precos.servico_na_data`.
- Produces:
```python
@dataclass
class DiaFila:
    agora_min: int
    barbeiros: list[BarbeiroDia]            # só os que trabalham hoje
    lugares: list[Lugar]                    # só os ativos (aguardando/chamado)
    indisponiveis: set[int]                 # barbeiros com lugares mas fora do dia
    nomes: dict[int, str]                   # barbeiro_id -> nome
    abertos_agora: set[int]                 # dentro do expediente neste minuto

fila_dados.carregar_dia(conn, data: str, agora_min: int) -> DiaFila
fila_dados.inserir_lugar(conn, *, data, nome, telefone, servico_id, duracao_min, barbeiro_id, origem, cliente_id) -> tuple[int, str]   # (id, token)
fila_dados.buscar_por_token(conn, token: str) -> dict | None
fila_dados.mudar_status(conn, lugar_id: int, acao: str, barbeiro_id: int | None = None, agendamento_id: int | None = None) -> dict
    # trava a linha, aplica fila.proximo_status, preenche chamado_em/finalizado_em/atendido_por; levanta fila.TransicaoInvalida
```
  - `carregar_dia` monta `ocupadas` de cada barbeiro com `montar_janelas`, a partir dos agendamentos do dia **exceto os ligados à fila** (`id NOT IN (SELECT agendamento_id FROM fila WHERE agendamento_id IS NOT NULL)`), mais os bloqueios e o almoço fixo. O `livre_a_partir` sai do agendamento do lugar em `atendendo` daquele barbeiro (`hora + duracao_min`), comparado com `agora_min`. Barbeiro de folga (`abertura >= fechamento`) ou com bloqueio de dia inteiro fica fora de `barbeiros`.
  - Rotas, com os formatos do spec (seção 7):
    - `GET /api/fila/hoje` devolve `{"dia_de_fila": bool, "barbeiros": [{"id","nome","foto","aberta","motivo","esperando","espera_min"}], "qualquer_um": {"aberta","esperando","espera_min"}}`. O `motivo` vale `"De folga hoje"`, `"Fila fechada"` ou `"Ainda não abriu"`.
    - `POST /api/fila` com `@limiter.limit("5 per minute")` devolve 201 com `{"token","posicao","espera_min"}`.
    - `GET /api/fila/<token>` com `@limiter.limit("30 per minute")` devolve `{"status","posicao","a_frente","espera_min","barbeiro","servico","chamado_em","tolerancia_min"}`, e 404 se o token não existe.
    - `POST /api/fila/<token>/sair` devolve 200, 404 se o token não existe ou 409 se o lugar já não está ativo.

- [ ] **Step 1: Escrever os testes que falham.** Usar `monkeypatch` em `rotas_fila.get_connection` (conexão falsa) e em `rotas_fila.fila_dados.carregar_dia` / `inserir_lugar` / `buscar_por_token` / `mudar_status`, devolvendo um `DiaFila` montado no teste:
  - `test_hoje_fora_do_periodo`: com `dia_de_fila` False, `dia_de_fila` vem False e `barbeiros` vem vazio.
  - `test_hoje_lista_espera_por_barbeiro`: dois aguardando com o barbeiro 1, de 30 min, às 10:00 dão `esperando == 2` e `espera_min == 60` para quem entrar agora.
  - `test_entrar_ok`: devolve 201 com `token` e `posicao == 1`. `inserir_lugar` recebe o telefone só com dígitos.
  - `test_entrar_mascara_e_sem_mascara_e_o_mesmo_telefone`: com um lugar ativo para `11988887777`, entrar com `"(11) 98888-7777"` devolve 409 com a mensagem exata de telefone repetido.
  - `test_entrar_corrida_vira_409`: quando `inserir_lugar` levanta `psycopg2.errors.UniqueViolation`, a resposta é 409 com a mesma mensagem, e não 500.
  - `test_entrar_cliente_bloqueado`: devolve 403 com `MENSAGEM_CLIENTE_BLOQUEADO`.
  - `test_entrar_perto_de_fechar`: no sábado às 18:45 (barbeiro livre, fechamento 19:00), pedindo um serviço de 40 min, devolve 409 com `"A fila de hoje já fechou para esse barbeiro."`.
  - `test_entrar_barbeiro_de_folga`: barbeiro fora de `barbeiros` devolve 409 com a mesma mensagem.
  - `test_entrar_antes_de_abrir`: barbeiro fora de `abertos_agora` devolve 409 com `"A fila de hoje ainda não abriu."` (texto novo, que o spec não fixava).
  - `test_telefone_generico_nao_conta_como_repetido`: com um lugar ativo gravado sem telefone, entrar pelo site com o número da barbearia (que está em `telefones_genericos`) passa, e `inserir_lugar` recebe `telefone=None`.
  - `test_situacao_token_inexistente`: devolve 404.
  - `test_situacao_posicao_e_espera`: devolve `posicao == 2`, `a_frente == 1` e a espera arredondada.
  - `test_situacao_barbeiro_indisponivel`: com o barbeiro em `indisponiveis`, devolve `status == "aguardando"`, `espera_min is None` e 200.
  - `test_sair_duas_vezes`: a primeira devolve 200 e a segunda 409.
- [ ] **Step 2: Rodar** `pytest tests/test_rotas_fila.py -q`. Esperado: FAIL.
- [ ] **Step 3: Implementar** as funções de `fila_dados.py` e as rotas públicas. A entrada segue esta ordem:
  1. Validar nome e telefone.
  2. Checar cliente bloqueado.
  3. `dia_de_fila` e `dia_fechado`.
  4. Serviço (`servico_na_data`, que dá a duração).
  5. Travar a fila do dia com `SELECT ... FOR UPDATE` nos barbeiros ativos.
  6. Checar telefone repetido, quando o telefone não é genérico.
  7. `cabe_mais_um`.
  8. `buscar_ou_criar_cliente` e `inserir_lugar`.
  9. Commit.
- [ ] **Step 4: Rodar** `pytest -q`. Esperado: tudo passa.
- [ ] **Step 5: Commit** com a mensagem "Cliente entra na fila e acompanha a vez".

### Task 5: Rotas do painel

**Files:**
- Modify: `back/rotas_fila.py`
- Test: `back/tests/test_rotas_fila.py` (novos testes)

**Interfaces:**
- Consumes: Tasks 3 e 4; `auth.barbeiro_do_escopo`, `auth.token_requerido`, `agendamentos._processar_novo_agendamento`, `notificacoes.link_whatsapp`, `config.agora_br`.
- Produces (todas com `@token_requerido`; o papel barbeiro tem `barbeiro_id` forçado para o próprio):
  - `GET /api/admin/fila` devolve `{"lugares": [...], "indisponiveis": [...], "tolerancia_min": 10}`. Cada lugar traz `id`, `nome`, `telefone`, `servico`, `barbeiro_id`, `atendido_por`, `status`, `entrou_em`, `chamado_em`, `inicio_estimado` (HH:MM ou null) e `agendamento_id`. Vêm os lugares ativos, em atendimento e os finalizados de hoje. O barbeiro vê os dele, os de "qualquer um" e os que ele chamou.
  - `POST /api/admin/fila` (entrada pela porta, telefone opcional, `origem='painel'`, sem a regra de fila fechada) devolve 201.
  - `POST /api/admin/fila/chamar-proximo` recebe `{barbeiro_id}` e devolve `{"lugar": {...}, "whatsapp": str|null}`. Devolve 404 `"Ninguém esperando."` ou 409 `"Resolva o cliente chamado antes de chamar outro."`.
  - `POST /api/admin/fila/<id>/chamar`, `/comecar`, `/concluir`, `/nao-veio`, `/remover`. `comecar` devolve `{"agendamento_id"}`. Transição inválida devolve 409 com a mensagem de `TransicaoInvalida`. Lugar fora do escopo devolve 403.

- [ ] **Step 1: Escrever os testes que falham:**
  - `test_barbeiro_ve_so_o_dele_e_qualquer_um`: lugares dos barbeiros 1, 2 e "qualquer um". O barbeiro 1 recebe só os do 1 e os de "qualquer um".
  - `test_chamar_proximo_ordem`: chama o mais antigo entre o do barbeiro e "qualquer um" (reusa a regra de `escolher_proximo`), e `mudar_status` recebe `acao="chamar"` e `barbeiro_id=1`.
  - `test_chamar_proximo_com_chamado_pendente`: devolve 409.
  - `test_chamar_devolve_whatsapp`: o link contém `wa.me` e o texto `"Chegou sua vez na JP Barbearia"`. Sem telefone, `whatsapp` é null.
  - `test_barbeiro_nao_mexe_na_fila_de_outro`: o barbeiro 1 chamando um lugar escolhido para o barbeiro 2 recebe 403.
  - `test_comecar_cria_agendamento_da_fila`: `_processar_novo_agendamento` é chamado com `exigir_antecedencia=False`, `permitir_conflito=True`, `origem="fila"`, a data de hoje, a hora atual em HH:MM e o barbeiro que chamou. A resposta traz o `agendamento_id`.
  - `test_comecar_direto_de_aguardando`: começar sem chamar antes liga o lugar ao barbeiro que começou.
  - `test_concluir_sem_comecar`: devolve 409.
  - `test_salao_chama_por_qualquer_barbeiro`: o papel `salao` com `{barbeiro_id: 2}` é aceito.
- [ ] **Step 2: Rodar** `pytest tests/test_rotas_fila.py -q`. Esperado: os testes novos falham.
- [ ] **Step 3: Implementar.**
  - `chamar-proximo` trava as linhas dos barbeiros ativos (`SELECT id FROM barbeiros WHERE ativo = 1 FOR UPDATE`) antes de escolher, para dois barbeiros não chamarem a mesma pessoa.
  - Em `comecar`, se `_processar_novo_agendamento` devolver status diferente de 201, desfazer e repassar o erro sem mudar o lugar.
- [ ] **Step 4: Rodar** `pytest -q`. Esperado: tudo passa.
- [ ] **Step 5: Commit** com a mensagem "Painel chama, começa e conclui a fila".

### Task 6: Página da fila do cliente

**Files:**
- Create: `front/tema.css`, `front/fila.html`, `front/fila.js`
- Modify: `front/api.js` (`API.fila.hoje()`, `.entrar(dados)`, `.situacao(token)`, `.sair(token)`, com o `origem`/`campanha` guardado pelo `script.js`, na mesma chave do `localStorage`)
- Modify: `front/index.html` + `front/script.js` (cartão no topo, "Hoje é por ordem de chegada → Entrar na fila", quando `API.fila.hoje()` diz `dia_de_fila`)
- Test: `front/tests/fila.test.cjs`

**Interfaces:**
- Consumes: as rotas públicas da Task 4.
- Produces: o token salvo em `localStorage["fila_token"]`, junto com a data (`{"token","data"}`). Um token de outro dia é descartado.

- [ ] **Step 1: Escrever os testes que falham.** Interceptar `/api/fila/*` em cada cenário e usar a tela de 390×844:
  - `fila: escolher barbeiro e entrar`: os cartões mostram "Rian · 2 esperando · uns 60 min" e "Qualquer um". Escolher o Rian, preencher e clicar em "Entrar na fila" manda `barbeiro_id: 2` no POST, e a tela passa a mostrar a posição.
  - `fila: barbeiro de folga aparece apagado`: o cartão com `aberta:false, motivo:'De folga hoje'` fica desabilitado e mostra o motivo.
  - `fila: perto mostra "Já vem vindo"`: com `a_frente: 2`, a faixa aparece.
  - `fila: chamado mostra "É sua vez!"`: com `status:'chamado'` e `chamado_em` às 10:00, a tela mostra "É sua vez!" e "Você tem até 10:10".
  - `fila: link velho`: com 404 na situação, a tela mostra "Esse link não vale mais" e o botão "Entrar de novo", e o token some do `localStorage`.
  - `fila: sem conexão mostra reconectando`: com a situação devolvendo 503, a tela mostra "Reconectando…".
  - `fila: fora do dia`: com `dia_de_fila:false`, a tela explica e não mostra o formulário.
  - `site: cartão da fila no topo`: no `index.html`, com `dia_de_fila:true`, aparece o link "Entrar na fila".
- [ ] **Step 2: Rodar** `npm test`. Esperado: os testes novos falham.
- [ ] **Step 3: Implementar.**
  - **Tokens (`tema.css`):** copiar do bloco `:root` do protótipo as cores `--bg`, `--surface`, `--surface-2`, `--line`, `--ink`, `--muted`, `--gold`, `--gold-ink`, `--good`, `--bad` e as fontes `--f-display` e `--f-body`.
  - **Telas e textos:** seguir o spec, seção 8.2.
  - **Consulta da situação:** a cada 15 s com a página visível (`setInterval` combinado com `visibilitychange`).
  - **Som:** um bip curto gerado com `AudioContext` e `OscillatorNode`, repetido a cada 3 s enquanto o cliente estiver "chamado" e até ele tocar em "Estou indo".
  - **Vibração:** `navigator.vibrate?.(...)`, com 200 ms na faixa "Já vem vindo" e `[400,200,400]` quando chamado.
  - **Sair da fila:** a confirmação aparece na própria página.
  - **Som depois de reabrir:** o navegador só libera o som depois de um toque do cliente. Ao reabrir a página com um lugar ativo, mostrar o botão "Ativar aviso sonoro" até o primeiro toque.
- [ ] **Step 4: Rodar** `npm test`. Esperado: tudo passa.
- [ ] **Step 5: Commit** com a mensagem "Página da fila no visual novo".

### Task 7: Aba Fila no painel

**Files:**
- Modify: `front/admin-fila.js` (`renderFila`)
- Modify: `front/admin.js`:
  - `{ chave: "fila", rotulo: "Fila" }` como primeiro item de `ABAS_POR_PAPEL.master`, `.barbeiro` e `.salao`;
  - `fila: renderFila` em `SECOES`;
  - quando hoje não é dia de fila, a aba vai para o fim da barra em vez de abrir primeiro (usar `API.fila.hoje()` no carregamento do painel).
- Modify: `front/admin.html` (aumentar as versões)
- Modify: `front/api.js` (`API.admin.fila.listar()`, `.porNaFila(dados)`, `.chamarProximo(barbeiroId)`, `.acao(id, acao)`, onde `acao` é `chamar|comecar|concluir|nao-veio|remover`)
- Test: `front/tests/fila.test.cjs`

**Interfaces:**
- Consumes: as rotas da Task 5.
- Produces: `window.renderFila(): Promise<void>`, com o estilo limitado a `.painel-fila` e usando os tokens de `tema.css` (carregado no `admin.html`).

- [ ] **Step 1: Escrever os testes que falham:**
  - `painel fila: chamar próximo abre o WhatsApp`: clicar em "Chamar próximo" faz o POST `/api/admin/fila/chamar-proximo`, e o cartão chamado mostra o link do WhatsApp com o `href` vindo da resposta.
  - `painel fila: começar e concluir`: em sequência, os POSTs vão para `/comecar` e `/concluir` do mesmo id.
  - `painel fila: passou da tolerância`: com `chamado_em` 11 min antes de agora (relógio fixado pelo Playwright com `page.clock`), o cartão mostra "Passou da tolerância".
  - `painel fila: salão escolhe o barbeiro`: com o papel salao, existe um seletor de barbeiro antes de "Chamar próximo".
  - `painel fila: pôr na fila sem telefone`: o formulário "+ Pôr na fila" manda o POST sem `telefone`.
- [ ] **Step 2: Rodar** `npm test`. Esperado: os testes novos falham.
- [ ] **Step 3: Implementar** o layout do spec, seção 8.3:
  - um bloco por barbeiro com "Chamar próximo", quem está na cadeira e quem foi chamado (com a contagem da tolerância);
  - a lista de quem aguarda;
  - os botões conforme o estado;
  - a atualização a cada 10 s, parada quando o usuário troca de aba (o painel já desmonta a seção ao trocar).
  - Lugares de barbeiro indisponível mostram a faixa "Barbeiro indisponível hoje".
- [ ] **Step 4: Rodar** `npm test`. Esperado: tudo passa.
- [ ] **Step 5: Commit** com a mensagem "Aba Fila no painel".

### Task 8: Ensaio local de ponta a ponta

- [ ] **Step 1:** Subir a API e o front locais (`preview_start` com `api` e `front`) apontando para um Postgres local de testes, nunca o de produção. Cadastrar um período que inclua hoje.
- [ ] **Step 2:** Pelo celular (tela 390×844), colocar três clientes na fila: dois para barbeiros diferentes e um "qualquer um". Pelo painel do barbeiro, chamar, começar e concluir. Confirmar que a posição e a espera atualizam nas páginas e que o atendimento aparece na Contagem com o valor do serviço.
- [ ] **Step 3:** Marcar "Não veio" num chamado e confirmar que a página do cliente mostra o resultado e "Entrar de novo".
- [ ] **Step 4:** Rodar `cd back && pytest -q` e `cd front && npm test`. Esperado: tudo passa.
- [ ] **Step 5:** Atualizar a seção "Regras de negócio que não são óbvias" do `README.md` com a fila (dias de fila, tolerância, um lugar por telefone) e fazer o commit com a mensagem "Documenta a fila de fim de ano".

---

## Publicação e volta atrás

Publicar é sempre decisão do João: o executor prepara tudo e **pede autorização antes de cada publicação**. Nunca publicar direto do branch sem o merge aprovado.

### Por que os agendamentos atuais não correm risco
- As mudanças no banco são **só aditivas**: as tabelas `periodos_fila` e `fila`, criadas por `init_db()` com `CREATE TABLE IF NOT EXISTS`. Nenhuma coluna ou linha existente é alterada ou apagada.
- A agenda só muda nas datas dentro de um período de fila. Antes de 21/12, site, painel, Caderninho e lembretes ficam iguais.
- `horarios.py` não é alterado.
- O site só deixa marcar 7 dias à frente (`LIMITE_DIAS_AGENDAMENTO`). Com o período cadastrado antes de 14/12, não existe agendamento de cliente em dia de fila.
- Agendamento lançado pelo Caderninho num dia de fila continua valendo, aparece na agenda e entra na estimativa como janela ocupada (Task 4, `carregar_dia`).

### Publicação 1 — agenda desligada nos dias de fila (Tasks 1 e 2), ~5/12, no máximo 13/12
- [ ] **Step 1:** Rodar `pytest -q` e `npm test` no branch. Esperado: tudo passa.
- [ ] **Step 2:** Pedir ao João um **backup do banco** no Supabase (Database → Backups) e confirmar que ele foi feito.
- [ ] **Step 3:** Com autorização, fazer o merge das Tasks 0 a 2 no `main` e o push. O Render publica o backend sozinho em ~2 min.
- [ ] **Step 4:** Conferir a publicação: `https://jp-barbearia-r9lq.onrender.com/` responde `{"status":"ok"}` e os logs do Render não mostram erro no `init_db`.
- [ ] **Step 5:** Conferir que a Vercel publicou o front e que o painel carrega com a versão nova dos arquivos (Shift+recarregar).
- [ ] **Step 6:** O dono cadastra **21/12 a 31/12** em Gerenciar → Expediente → Dias de fila.
- [ ] **Step 7:** Conferir no site: um dia antes de 21/12 mostra horários normalmente, e 21/12 aparece como "Fila" (a partir de 14/12, quando ele entra na janela de 7 dias). No painel, a Agenda e o Caderninho de hoje funcionam igual.

### Publicação 2 — fila completa (Tasks 3 a 8), ~15 a 18/12
- [ ] **Step 1:** O ensaio da Task 8 feito com uma **cópia** do banco (restauração do backup num Postgres local), nunca com o de produção. Testes passando.
- [ ] **Step 2:** Novo backup do banco, com a confirmação do João.
- [ ] **Step 3:** Com autorização, fazer o merge e o push. Conferir o health e os logs como na Publicação 1.
- [ ] **Step 4:** Conferir que `GET /api/fila/hoje` devolve `dia_de_fila: false` antes de 21/12 e que `fila.html` explica que a fila abre no dia 21. Nada muda para os clientes até lá.
- [ ] **Step 5:** Mostrar a aba Fila ao JP e aos barbeiros, com um ensaio rápido no painel.

### Volta atrás
| Situação | O que fazer | Quem faz | Tempo |
|---|---|---|---|
| A fila dá problema num dia de fila | Apagar o período em Dias de fila. A agenda normal volta na hora, e o Caderninho segue funcionando. | o dono, pelo painel | segundos |
| Erro no sistema depois de uma publicação | Render → serviço → Deploys → voltar para o deploy anterior. As tabelas novas ficam paradas, porque a versão antiga não as usa. | João | ~2 min |
| Erro nas telas | Vercel → Deployments → promover o deploy anterior. | João | ~1 min |
| Dado gravado errado | Restaurar o backup feito antes da publicação, só com o João decidindo. | João | conforme o Supabase |
