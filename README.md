# JP Barbearia — sistema de agendamento

Site onde o cliente marca o horário e painel onde os barbeiros tocam o dia:
agenda, caderninho de encaixes, almoço, produtos e fechamento do caixa.

**Este documento serve pra duas pessoas:**

- quem **não programa** e precisa resolver um problema ou pedir uma mudança → comece pelas duas primeiras seções;
- quem **vai mexer no código** (ou o Claude numa sessão nova) → o resto do documento.

---

## 🚨 Deu problema? Comece aqui

### Passo 1 — descubra ONDE está o problema

Abra este endereço no navegador (celular serve):

**https://jp-barbearia-r9lq.onrender.com/**

| O que aparece | O que significa | O que fazer |
|---|---|---|
| `{"status":"ok"}` | O servidor está de pé | Vá para o Passo 2 |
| Demora e depois abre | Estava "dormindo" e acordou | **Normal.** Espere 1 minuto e use o site |
| Erro / não abre | O servidor está fora | Vá para o Passo 3 |

### Passo 2 — o servidor está ok, mas o site reclama

Peça pra pessoa **recarregar segurando Shift** (ou fechar e abrir o navegador).
O site guarda uma cópia dos arquivos e às vezes fica com a versão velha.

Se continuar, veja **Passo 3**.

### Passo 3 — servidor fora do ar

1. Entre em **render.com** → serviço **jp-barbearia** → aba **Logs**
2. Procure linhas em vermelho ou a palavra `Error`
3. Abra o **claude.ai/code** (veja a próxima seção), conecte neste repositório e escreva:

> *"O site está fora do ar. Confere o que está acontecendo."*

O Claude sabe testar o sistema sozinho e vai te dizer o que houve.

### O que provavelmente NÃO é

- **Não é o Supabase** só porque apareceu algum alerta lá. O sistema usa o banco direto, não usa a "API REST" do Supabase — alertas sobre `PostgREST` ou `schemas expostos` **não afetam** o site.
- **Não é preciso mexer em código** na maioria das quedas. Quase sempre é o servidor hibernando ou reiniciando.

---

## 💬 Precisa mudar alguma coisa e não sabe programar?

**Você não vai escrever código. Você vai pedir.**

1. Acesse **claude.ai/code**
2. Conecte no repositório **`joaocustoddio/jp-barbearia`**
3. Escreva o que precisa, em português normal. Exemplos:

> *"Muda o preço do Degradê para R$ 45."*
> *"O Rian vai tirar folga na segunda dia 25, marca isso pra ele."*
> *"Adiciona um serviço novo: Corte infantil, R$ 30, 30 minutos."*

O Claude altera os arquivos, testa e publica. **O site se atualiza sozinho em uns 2 minutos.**

### ⚠️ Antes de pedir: dá pra fazer sozinho pelo painel?

Muita coisa **não precisa de programador nenhum**. Entre no painel
(`/admin.html`) com o login de dono e veja as abas:

| O que você quer | Onde fazer |
|---|---|
| Mudar preço de serviço ou produto | aba **Preços** |
| Mudar a comissão de um barbeiro | aba **Barbeiros** → *editar* |
| Marcar folga de alguém | aba **Expediente** → botão **Folga** |
| Mudar horário de entrada/saída num dia | aba **Expediente** |
| Marcar almoço | aba **Almoço** |
| Anotar corte de encaixe | aba **Caderninho** |
| Ver quanto entrou no dia | aba **Contagem** |

### 🚫 Não mexa nisso sem falar com alguém

- Variáveis no **Render** (as senhas do sistema moram lá)
- Qualquer coisa dentro do **Supabase** (é o banco de dados — apagar algo ali é irreversível)
- Configurações de segurança do **Brevo** (o envio de e-mail para de funcionar)

---

## 🧩 Como o sistema é montado

São três serviços gratuitos conversando entre si:

```
Cliente/Barbeiro (navegador)
        │
        ▼
   VERCEL  ── o site e o painel (as telas)
        │
        ▼
   RENDER  ── o cérebro: horários, agendamentos, contas
        │
        ▼
  SUPABASE ── o banco: onde tudo fica guardado
```

E dois ajudantes:

- **Brevo** → envia os e-mails para o cliente
- **Telegram** → avisa os barbeiros de agendamento novo, resumo do dia e lembretes

### Como uma alteração chega no ar

```
alterou o código → git push → Render publica sozinho (~2 min)
```

Ninguém precisa "subir" nada manualmente.

### Onde fica cada coisa no repositório

```
back/          o cérebro (Python)
  app.py           rotas da API
  horarios.py      toda a matemática da agenda
  database.py      banco de dados e criação das tabelas
  emails.py        e-mails do cliente
  notificacoes.py  avisos no Telegram
  tests/           91 testes automáticos

front/         as telas (HTML/CSS/JS)
  index.html     site do cliente
  admin.html     painel do barbeiro

.github/       tarefas automáticas (avisos e lembretes)
```

---

## 🔑 Onde ficam as senhas

**Nenhuma senha está neste repositório** — e não pode estar, porque ele é público.

| Serviço | O que guarda |
|---|---|
| **Render** → *Environment* | senha do banco, chave de e-mail, token do Telegram |
| **GitHub** → *Settings → Secrets* | token usado pelas tarefas automáticas |

O arquivo `back/.env.example` lista **quais** variáveis existem e o que cada uma faz — mas sem valores reais. É a referência quando precisar configurar algo.

---

## 📋 Regras de negócio que não são óbvias

Coisas que o sistema faz de propósito e podem parecer bug:

- **Domingo é fechado.** Nenhum horário aparece.
- **O dono folga na segunda**, automaticamente. Os outros barbeiros trabalham.
- **Só o dono entra antes das 9h** (sexta 08:20, sábado 08:00). Os demais começam 09:00.
- **Última entrada é 10 min antes de fechar** (19:50 nos dias que fecham 20:00).
- **Cortes longos podem passar 10 min do fechamento** — pra não perder o último corte do dia. Serviços de até 20 min não passam.
- **Os horários se encaixam nas brechas:** se um corte termina 18:10, o próximo é oferecido às 18:10 (não às 18:40).
- **O barbeiro pode encaixar em cima de qualquer horário** pelo Caderninho, sem restrição. O cliente pelo site, não.
- **Telefone é obrigatório** pro cliente; e-mail é opcional (quem não informa, não recebe confirmação nem lembrete).
- **Comissão do dono é 0%** — o corte dele não vira repasse, o valor fica todo com a barbearia.
- **Produtos não entram na comissão** de ninguém; vão direto pro caixa.
- **Preço gravado no consumo é uma "foto"**: mudar o preço hoje não altera o fechamento de ontem.

Quase tudo isso é ajustável por variável de ambiente — veja `back/.env.example`.

---

## ⚠️ Armadilhas já resolvidas (não repita)

Cada uma dessas custou horas pra descobrir:

- **O servidor hiberna.** No plano gratuito ele dorme após 15 min parado. Uma tarefa automática o mantém acordado das **06h à meia-noite**. Fora disso, o primeiro acesso demora ~40s — e o site já tenta reconectar sozinho.
- **SMTP não funciona no Render.** As portas de e-mail (25/465/587) são bloqueadas. Por isso o envio usa a **API HTTP** do Brevo. Se alguém trocar de volta pra SMTP, vai dar *timeout* eterno.
- **O Brevo bloqueia por IP.** Se ligarem a trava de IPs autorizados, o e-mail para de sair — o Render não tem IP fixo.
- **Alertas de PostgREST no Supabase são irrelevantes.** O sistema não usa essa API.
- **Mudar dado direto no banco não adianta** para preço, comissão e nomes: use o painel.

Se o e-mail parar de sair, existe um diagnóstico pronto — peça ao Claude:
*"Roda o teste de e-mail e me diz o erro."*

---

## 🧪 Para quem for mexer no código

```bash
cd back
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # 91 testes, rodam em ~1 segundo
```

Os testes cobrem o motor de horários (a parte mais delicada), o cálculo do
fechamento e os avisos. **Rode antes e depois de qualquer mudança.**

Há uma proteção específica contra o bug mais recorrente do projeto: um teste
percorre centenas de combinações garantindo que **todo horário exibido ao
cliente é aceito no agendamento** — nada de "vejo na tela mas não consigo
marcar".

O arquivo `CLAUDE.md` traz as diretrizes de como escrever código aqui.
