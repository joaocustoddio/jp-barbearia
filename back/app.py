"""
app.py (versão produção)
------------------------
Ponto de entrada da API (é este `app` que o gunicorn sobe: `gunicorn app:app`).

O código está dividido por assunto em módulos:
- config.py               constantes e variáveis de ambiente
- extensoes.py            objeto Flask, CORS, rate limit, logging, erros
- validacoes.py           validação dos campos e horário efetivo do barbeiro
- auth.py                 login do painel, JWT e escopo (master/barbeiro/salão)
- agendamentos.py         núcleo do agendamento (janelas ocupadas, criação)
- rotas_publicas.py       rotas do site do cliente
- rotas_admin_agenda.py   painel: agenda, bloqueios, almoço, expediente
- rotas_admin_gestao.py   painel: barbeiros, produtos, preços, senhas
- relatorios.py           relatórios e contagem do dia

Ficam AQUI: a rota de horários disponíveis e as tarefas agendadas — são os
trechos que os testes exercitam injetando dependências no módulo `app`
(get_connection, horario_efetivo, agora_br, TAREFAS_TOKEN…).
"""

import os
import hmac
from datetime import datetime, timedelta

from flask import request, jsonify

# Alguns nomes abaixo não são usados neste arquivo: são reexportados de
# propósito, porque `app.X` continua sendo a porta de entrada deles
# (é assim que os testes leem/injetam configuração e helpers).
from config import (
    ANTECEDENCIA_MINIMA, CORS_ORIGIN, DURACAO_ALMOCO_MIN, EM_PRODUCAO,
    ENDERECO_BARBEARIA, FLASK_ENV, FUSO_BR, HORA_FOLGA, HORARIO_ABERTURA,
    HORARIO_FECHAMENTO, INTERVALO_MINUTOS, MARGEM_ULTIMA_ENTRADA_MIN,
    TOLERANCIA_FECHAMENTO_MIN, agora_br, data_hoje, expediente_e_folga,
    tolerancia_para,
)
from database import (
    get_connection, init_db, criar_admin_padrao, criar_salao_padrao,
    criar_barbeiros_padrao, ajustar_servicos, criar_produtos_padrao,
)
from extensoes import app, limiter, logger
from horarios import hhmm_para_min, gerar_slots, filtrar_por_antecedencia
from validacoes import dia_fechado, horario_efetivo, horario_efetivo_de, validar_data
from agendamentos import janelas_ocupadas, montar_janelas, _data_br, _link_agenda
import notificacoes
import emails

# Importa os módulos de rota pelo efeito colateral: cada um registra as próprias
# rotas no mesmo objeto `app` (mesmos caminhos e mesmos nomes de endpoint).
import auth                  # noqa: F401
import rotas_publicas        # noqa: F401
import rotas_admin_agenda    # noqa: F401
import rotas_admin_gestao    # noqa: F401
import relatorios            # noqa: F401


@app.route("/", methods=["GET"])
def health():
    """Health check simples (o Render usa pra saber que o serviço está de pé)."""
    return jsonify({"status": "ok", "servico": "jp-barbearia"})


@app.route("/api/horarios-disponiveis", methods=["GET"])
def horarios_disponiveis():
    """
    Agora filtra por barbeiro_id também.
    Cada barbeiro tem sua agenda independente — um horário
    ocupado com o Barbeiro 1 continua livre pro Barbeiro 2.

    Parâmetros: ?data=YYYY-MM-DD&barbeiro_id=1
    """
    data_str     = request.args.get("data")
    barbeiro_id  = request.args.get("barbeiro_id")
    servico_id   = request.args.get("servico_id")

    valido, erro = validar_data(data_str)
    if not valido:
        return jsonify({"erro": erro}), 400

    if not barbeiro_id:
        return jsonify({"erro": "barbeiro_id é obrigatório"}), 400

    # Dia sem atendimento (ex: domingo): devolve lista vazia com status 200.
    fechado, _ = dia_fechado(data_str)
    if fechado:
        return jsonify({"data": data_str, "barbeiro_id": barbeiro_id,
                        "horarios_disponiveis": [], "fechado": True})

    conn = get_connection()

    # Duração do serviço escolhido = PASSO dos slots. Sem servico_id, usa o padrão.
    dur_serv = INTERVALO_MINUTOS
    if servico_id:
        srow = conn.execute("SELECT duracao_min FROM servicos WHERE id = %s", (servico_id,)).fetchone()
        if srow:
            dur_serv = srow["duracao_min"]

    # Horário efetivo do barbeiro no dia (expediente/piso/folga).
    abertura, fechamento = horario_efetivo(data_str, barbeiro_id, conn)
    ocupadas, dia_bloqueado = janelas_ocupadas(conn, data_str, barbeiro_id, dur_serv)
    conn.close()

    if dia_bloqueado:
        return jsonify({"data": data_str, "barbeiro_id": barbeiro_id,
                        "horarios_disponiveis": [], "bloqueio_dia": True})

    # Horários gerados de forma DINÂMICA: encaixam nas brechas livres começando
    # onde o corte anterior terminou (packing), sem desperdiçar espaço.
    disponiveis = gerar_slots(abertura, fechamento, ocupadas, dur_serv,
                              tolerancia_para(dur_serv), MARGEM_ULTIMA_ENTRADA_MIN)

    # Se for hoje, tira o que já passou ou está dentro da antecedência mínima.
    if data_str == data_hoje().isoformat():
        agora = agora_br()
        disponiveis = filtrar_por_antecedencia(
            disponiveis, agora.hour * 60 + agora.minute, ANTECEDENCIA_MINIMA
        )

    return jsonify({"data": data_str, "barbeiro_id": barbeiro_id, "horarios_disponiveis": disponiveis})


# Teto de dias por chamada. 14 cobre a janela de agendamento do site com folga;
# o limite existe pra ninguém pedir um ano de uma vez.
MAX_DIAS_PERIODO = 14


@app.route("/api/horarios-disponiveis-periodo", methods=["GET"])
def horarios_disponiveis_periodo():
    """
    A MESMA disponibilidade do /api/horarios-disponiveis, só que de vários dias
    numa requisição só.

    Motivo de existir: a tela de escolher data precisa saber de 8 dias. Uma
    chamada por dia custava ~1s cada na hospedagem gratuita (0,1 de CPU), o que
    dava ~8s de espera. Aqui as consultas são feitas EM LOTE — 5 no total,
    independente do número de dias.

    O cálculo é o mesmo: usa `montar_janelas` e `horario_efetivo_de`, as mesmas
    funções que o endpoint de um dia usa. Não existe regra duplicada aqui.

    Parâmetros: ?inicio=YYYY-MM-DD&dias=8&barbeiro_id=1&servico_id=2
    Resposta:   {"barbeiro_id": 1, "dias": {"2026-08-27": {...}, ...}}
    """
    inicio_str  = request.args.get("inicio")
    barbeiro_id = request.args.get("barbeiro_id")
    servico_id  = request.args.get("servico_id")

    valido, erro = validar_data(inicio_str)
    if not valido:
        return jsonify({"erro": erro}), 400
    if not barbeiro_id:
        return jsonify({"erro": "barbeiro_id é obrigatório"}), 400

    try:
        qtd_dias = int(request.args.get("dias", 8))
    except (TypeError, ValueError):
        return jsonify({"erro": "dias inválido"}), 400
    if qtd_dias < 1 or qtd_dias > MAX_DIAS_PERIODO:
        return jsonify({"erro": f"dias deve ser entre 1 e {MAX_DIAS_PERIODO}"}), 400

    primeiro = datetime.strptime(inicio_str, "%Y-%m-%d").date()
    datas = [(primeiro + timedelta(days=i)).isoformat() for i in range(qtd_dias)]
    ultimo = datas[-1]

    conn = get_connection()

    dur_serv = INTERVALO_MINUTOS
    if servico_id:
        srow = conn.execute("SELECT duracao_min FROM servicos WHERE id = %s", (servico_id,)).fetchone()
        if srow:
            dur_serv = srow["duracao_min"]

    # --- as 4 consultas em lote (uma por assunto, cobrindo o período inteiro) ---
    agendados_por_dia = {}
    for linha in conn.execute(
        """SELECT agendamentos.data, agendamentos.hora, servicos.duracao_min
           FROM agendamentos
           JOIN servicos ON agendamentos.servico_id = servicos.id
           WHERE agendamentos.data BETWEEN %s AND %s
             AND agendamentos.barbeiro_id = %s
             AND agendamentos.status != 'cancelado'""",
        (datas[0], ultimo, barbeiro_id)
    ).fetchall():
        agendados_por_dia.setdefault(linha["data"], []).append(linha)

    bloqueios_por_dia = {}
    for linha in conn.execute(
        """SELECT data, hora, duracao_min, motivo FROM bloqueios
           WHERE data BETWEEN %s AND %s AND (barbeiro_id IS NULL OR barbeiro_id = %s)""",
        (datas[0], ultimo, barbeiro_id)
    ).fetchall():
        bloqueios_por_dia.setdefault(linha["data"], []).append(linha)

    expediente_por_dia = {
        linha["data"]: linha
        for linha in conn.execute(
            "SELECT data, inicio, fim FROM expedientes WHERE barbeiro_id = %s AND data BETWEEN %s AND %s",
            (barbeiro_id, datas[0], ultimo)
        ).fetchall()
    }

    linha_barbeiro = conn.execute(
        "SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    almoco_fixo = linha_barbeiro["almoco_fixo"] if linha_barbeiro else None
    conn.close()

    hoje = data_hoje().isoformat()
    agora = agora_br()
    agora_min = agora.hour * 60 + agora.minute

    resposta = {}
    for data_str in datas:
        fechado, _ = dia_fechado(data_str)
        if fechado:
            resposta[data_str] = {"horarios_disponiveis": [], "fechado": True}
            continue

        ocupadas, dia_bloqueado = montar_janelas(
            agendados_por_dia.get(data_str, []),
            bloqueios_por_dia.get(data_str, []),
            almoco_fixo, dur_serv
        )
        if dia_bloqueado:
            resposta[data_str] = {"horarios_disponiveis": [], "bloqueio_dia": True}
            continue

        abertura, fechamento = horario_efetivo_de(
            data_str, barbeiro_id, expediente_por_dia.get(data_str)
        )
        disponiveis = gerar_slots(abertura, fechamento, ocupadas, dur_serv,
                                  tolerancia_para(dur_serv), MARGEM_ULTIMA_ENTRADA_MIN)
        if data_str == hoje:
            disponiveis = filtrar_por_antecedencia(disponiveis, agora_min, ANTECEDENCIA_MINIMA)

        resposta[data_str] = {"horarios_disponiveis": disponiveis}

    return jsonify({"barbeiro_id": barbeiro_id, "dias": resposta})


# -------------------------------------------------------
# TAREFAS AGENDADAS (chamadas por um agendador externo)
# O Render free hiberna, então quem "acorda" o serviço no horário é uma chamada
# de fora (GitHub Actions, cron-job.org...). Protegido por token secreto.
# -------------------------------------------------------
TAREFAS_TOKEN = os.getenv("TAREFAS_TOKEN", "").strip()

# Quantos minutos antes do horário o lembrete automático sai (padrão 60 = 1h).
# A tarefa roda de tempos em tempos e pega quem está entrando nessa janela.
LEMBRETE_ANTECEDENCIA_MIN = int(os.getenv("LEMBRETE_ANTECEDENCIA_MINUTOS", "60"))


def _token_de_tarefa_confere():
    if not TAREFAS_TOKEN:
        return False
    enviado = (request.headers.get("X-Tarefa-Token")
               or request.args.get("token") or "")
    return hmac.compare_digest(enviado, TAREFAS_TOKEN)


def _agendamentos_do_dia(data_str):
    """Agendamentos ativos da data, já no formato das mensagens."""
    conn = get_connection()
    linhas = conn.execute(
        """SELECT agendamentos.hora, agendamentos.barbeiro_id, clientes.nome AS cliente,
                  clientes.telefone AS telefone, servicos.nome AS servico,
                  barbeiros.nome AS barbeiro
           FROM agendamentos
           JOIN clientes  ON agendamentos.cliente_id  = clientes.id
           JOIN servicos  ON agendamentos.servico_id  = servicos.id
           JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
           WHERE agendamentos.data = %s AND agendamentos.status != 'cancelado'
           ORDER BY agendamentos.hora""",
        (data_str,)
    ).fetchall()
    conn.close()
    return [{"hora": l["hora"][:5], "cliente": l["cliente"], "telefone": l["telefone"],
             "servico": l["servico"], "barbeiro": l["barbeiro"],
             "barbeiro_id": l["barbeiro_id"]} for l in linhas]




def _enviar_lembretes_de_clientes():
    """
    Manda o lembrete por email pra quem tem horário chegando (~1h). Roda de
    tempos em tempos; cada agendamento é marcado como avisado (lembrete_enviado_em),
    então ninguém recebe duas vezes mesmo se a tarefa rodar várias vezes.

    Pega quem começa entre AGORA e AGORA + antecedência: se a tarefa atrasar, o
    cliente ainda recebe (um pouco em cima da hora, melhor que não receber).
    """
    if not emails.configurado():
        return {"tipo": "proximos", "enviados": 0, "erro": "E-mail não configurado"}

    agora = agora_br()
    hoje = agora.date().isoformat()
    agora_min = agora.hour * 60 + agora.minute
    limite_min = agora_min + LEMBRETE_ANTECEDENCIA_MIN

    conn = get_connection()
    candidatos = conn.execute(
        """SELECT agendamentos.id, agendamentos.hora, clientes.nome AS cliente,
                  clientes.email, servicos.nome AS servico, barbeiros.nome AS barbeiro
           FROM agendamentos
           JOIN clientes  ON agendamentos.cliente_id  = clientes.id
           JOIN servicos  ON agendamentos.servico_id  = servicos.id
           JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
           WHERE agendamentos.data = %s
             AND agendamentos.status != 'cancelado'
             AND agendamentos.lembrete_enviado_em IS NULL
             AND clientes.email IS NOT NULL AND clientes.email <> ''
           ORDER BY agendamentos.hora""",
        (hoje,)
    ).fetchall()

    enviados = 0
    for linha in candidatos:
        try:
            inicio = hhmm_para_min(linha["hora"])
        except ValueError:
            continue
        if not (agora_min <= inicio <= limite_min):
            continue                                  # ainda não é a hora (ou já passou)
        assunto, html, texto = emails.email_lembrete(
            cliente=linha["cliente"], servico=linha["servico"],
            barbeiro=linha["barbeiro"], data_br=_data_br(hoje),
            hora=linha["hora"][:5], endereco=ENDERECO_BARBEARIA,
        )
        # esperar=True: só marca como enviado depois de realmente tentar mandar.
        emails.enviar(linha["email"], assunto, html, texto, esperar=True)
        conn.execute("UPDATE agendamentos SET lembrete_enviado_em = now() WHERE id = %s",
                     (linha["id"],))
        enviados += 1

    conn.commit()
    conn.close()
    logger.info("Lembretes de clientes: %d enviado(s) de %d candidato(s).",
                enviados, len(candidatos))
    return {"tipo": "proximos", "data": hoje, "enviados": enviados}


@app.route("/api/tarefas/testar-email", methods=["POST"])
@limiter.limit("10 per hour")
def tarefa_testar_email():
    """
    Diagnóstico de e-mail: tenta enviar UM email de teste e devolve o erro real
    do servidor SMTP, em vez de deixá-lo escondido no log.
    Uso: POST /api/tarefas/testar-email?para=alguem@email.com  (com o token)
    """
    if not _token_de_tarefa_confere():
        return jsonify({"erro": "Não autorizado"}), 401

    resultado = emails.diagnostico()
    destino = (request.args.get("para") or "").strip()
    if not resultado["configurado"]:
        resultado["dica"] = "Faltam variáveis SMTP no servidor (EMAIL_SMTP_HOST / EMAIL_REMETENTE)."
        return jsonify(resultado), 200
    if not destino:
        resultado["dica"] = "Informe ?para=seu@email.com pra fazer o teste de envio."
        return jsonify(resultado), 200

    enviado, erro = emails.testar(destino)
    resultado.update({"destino": destino, "enviado": enviado, "erro_smtp": erro})
    logger.info("Teste de email para %s: enviado=%s erro=%s", destino, enviado, erro)
    return jsonify(resultado), 200


@app.route("/api/tarefas/avisos", methods=["POST"])
@limiter.limit("20 per hour")
def tarefa_avisos():
    """
    Manda no Telegram da equipe:
    - ?tipo=lembretes (padrão): agenda de AMANHÃ com um link de WhatsApp pronto
      por cliente — o barbeiro só toca e envia.
    - ?tipo=resumo: agenda de HOJE, pra abrir o dia sabendo o movimento.
    Autenticação: header X-Tarefa-Token (ou ?token=) igual a TAREFAS_TOKEN.
    """
    if not _token_de_tarefa_confere():
        return jsonify({"erro": "Não autorizado"}), 401

    tipo = (request.args.get("tipo") or "lembretes").lower()

    # Lembrete automático pro CLIENTE, ~1h antes do horário dele (por email).
    # Esta é a única tarefa que não depende do Telegram.
    if tipo == "proximos":
        return jsonify(_enviar_lembretes_de_clientes())

    if not notificacoes.configurado():
        return jsonify({"erro": "Telegram não configurado"}), 503

    if tipo == "resumo":
        data = data_hoje()
        montar = lambda lista: notificacoes.texto_resumo_do_dia(
            _data_br(data.isoformat()), lista)
    else:
        data = data_hoje() + timedelta(days=1)
        montar = lambda lista: notificacoes.texto_lembretes(
            _data_br(data.isoformat()), lista)

    agendamentos = _agendamentos_do_dia(data.isoformat())
    enviado = notificacoes.enviar(montar(agendamentos), esperar=True)
    logger.info("Tarefa '%s' para %s: %d agendamento(s), enviado=%s",
                tipo, data.isoformat(), len(agendamentos), enviado)
    return jsonify({"tipo": tipo, "data": data.isoformat(),
                    "agendamentos": len(agendamentos), "enviado": enviado})


# -------------------------------------------------------
# INICIALIZAÇÃO
# -------------------------------------------------------
# Roda no carregamento do módulo para funcionar tanto com `python app.py`
# quanto sob gunicorn em produção (o bloco __main__ NÃO executa sob gunicorn,
# então init_db/seeds precisam ficar aqui fora). Tudo é idempotente
# (CREATE TABLE / ADD COLUMN IF NOT EXISTS e seeds que checam antes de inserir).
# APP_SKIP_BOOT=1 pula o boot (usado por testes que importam o módulo sem banco).
# Em produção a variável não existe, então roda tudo normalmente.
if os.getenv("APP_SKIP_BOOT") != "1":
    init_db()
    criar_admin_padrao()      # login master (dono) — do .env
    criar_salao_padrao()      # login do salão (tablet) — do .env
    criar_barbeiros_padrao()  # logins dos barbeiros comuns (2 e 3) — do .env
    ajustar_servicos()        # cria os serviços que faltarem (preço é do banco)
    criar_produtos_padrao()   # cria os adicionais que faltarem (preço é do banco)

if __name__ == "__main__":
    print(f"[config] Ambiente: {FLASK_ENV}")
    print(f"[config] CORS permitido para: {CORS_ORIGIN}")
    print(f"[config] Funcionamento: {HORARIO_ABERTURA} às {HORARIO_FECHAMENTO}")
    app.run(debug=not EM_PRODUCAO)
