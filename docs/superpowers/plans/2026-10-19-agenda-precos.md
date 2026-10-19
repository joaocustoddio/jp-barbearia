# Agenda, preços por vigência e correções — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Editar valor/duração de um único atendimento, programar reajustes e corrigir exposição de dados, cancelamento inseguro e lembretes perdidos.

**Architecture:** Flask/PostgreSQL e JavaScript existentes. Guardar preço, duração e comissão no atendimento; versões do catálogo por data. Atualizações de agenda usam o mesmo lock de barbeiro da reserva pública. Cancelamento público exige comprovante assinado e telefone.

**Tech Stack:** Python, Flask, psycopg2, pytest, JavaScript sem framework.

**Spec:** Pedidos e aprovação do usuário nesta conversa (19/10/2026).

## Global Constraints
- Desconto manual vale apenas para o agendamento editado.
- Reajuste não altera atendimentos passados nem descontos manuais.
- Barbeiro edita apenas sua agenda; master pode editar todas; salão não edita valores.
- Redução de 50 para 20 libera 30 minutos; aumento não invade compromissos/bloqueios.
- Não acessar banco de produção nem publicar durante esta implementação.
- Migração preserva o histórico com os valores atualmente disponíveis; preços antigos perdidos não podem ser reconstruídos.

## Review Focus
- Reajuste futuro aplicado a reserva já existente e preservação do desconto individual.
- Corrida entre edição e nova reserva; edição concorrente de preço e duração.
- Valores não finitos, centavos e duração fracionária/inválida.
- Cancelamento sem comprovante, comprovante de outro atendimento e atendimento passado.
- Falha real de envio não impede nova tentativa do lembrete.

## Tasks
1. [x] Testar regressões de segurança/envio; remover GET público de agenda/relatório, exigir comprovante no cancelamento, recusar serviço inativo e só marcar lembrete após sucesso. Arquivos: rotas_publicas.py, relatorios.py, validacoes.py, emails.py, app.py, agendamentos.py; testes existentes e test_seguranca.py.
2. [x] Testar vigência e moeda; criar precos.py, migração aditiva em database.py e snapshots na criação. Consultar catálogo por data, validar aumento com vigência e preservar preços manuais. Testes: test_precos.py.
3. [x] Testar PATCH /api/admin/agendamentos/<id>: permissões, conflito, redução, auditoria e entrada inválida; implementar com lock e atualizar todas as leituras financeiras/disponibilidade. Testes: test_alterar_agendamento.py.
4. [x] Integrar formulário Alterar, vigência/confirmar reajuste, preço na data selecionada e comprovante de cancelamento no frontend. Validar com testes JS e navegador local com API simulada.
5. [x] Rodar suíte completa, checar sintaxe, revisar diff com revisor independente e documentar implantação/migração.

## Verification
- Baseline: `python -m pytest back/tests -q`: 278 passed, aviso preexistente de limiter em memória.
- Cada regressão deve falhar antes da correção e passar depois.
- Final: pytest completo; node --check dos scripts; testes de interface; revisão independente.

## Execution ledger
- Dependências instaladas em .venv local; nenhum acesso a banco real.
- Ruling: reutilizar checkout atual e deixar diff sem commit/publicação, conforme autorização de edição do projeto e preferência do ambiente.
- Vigência pela data do atendimento, inclusive reservas existentes, exceto preço personalizado; confirmado pelo usuário: quem já marcou para depois da vigência paga o novo preço.

## Final verification
- Backend: 334 testes passaram; aviso preexistente de rate limit em memória.
- Interface: 7 testes Playwright passaram com API interceptada, incluindo erro 409, salão, desconto individual, vigência, remarcação e comprovante.
- Migração: 48 instruções executadas 3 vezes em PostgreSQL embutido isolado (PGlite); snapshots preservados. Oito consultas financeiras executadas, total 60 e comissão 36 conferidos.
- Revisão independente: corrigido preço exibido na remarcação; teste reproduziu erro antes da correção.
- 50 rotas registradas; sintaxe Python/JavaScript e git diff --check conferidos.
- Sem commit, push, deploy ou acesso ao banco da barbearia. Histórico anterior já perdido não pode ser reconstruído pela migração.
