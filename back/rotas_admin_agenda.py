"""
rotas_admin_agenda.py
---------------------
Painel — o dia a dia da agenda: agendamentos do painel (caderninho),
cancelamento, pagamento e adicionais, bloqueios, almoço e expediente.
"""

from flask import request, jsonify, g

from agendamentos import _data_br, _processar_novo_agendamento
from auth import (
    barbeiro_do_escopo, eh_master, pode_ver_valores, somente_master,
    token_requerido,
)
from config import DURACAO_ALMOCO_MIN, HORA_FOLGA, expediente_e_folga
from database import get_connection
from extensoes import app
from horarios import hhmm_para_min
from rotas_admin_gestao import _produtos_ativos
from validacoes import dia_fechado, horario_do_dia, validar_data, validar_hora
import notificacoes


@app.route("/api/admin/agendamentos", methods=["GET"])
@token_requerido
def painel_agendamentos():
    """
    Lista agendamentos com filtros opcionais.
    Parâmetros: ?data=YYYY-MM-DD  e/ou  ?status=confirmado
    Sem filtros: retorna todos os agendamentos.
    """
    data_filtro   = request.args.get("data")
    status_filtro = request.args.get("status")

    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            agendamentos.barbeiro_id,
            agendamentos.servico_id,
            agendamentos.encaixe,
            agendamentos.forma_pagamento,
            clientes.nome       AS cliente_nome,
            clientes.telefone   AS cliente_telefone,
            servicos.nome       AS servico_nome,
            servicos.preco      AS servico_preco,
            servicos.duracao_min AS servico_duracao,
            barbeiros.nome      AS barbeiro_nome
        FROM agendamentos
        JOIN clientes  ON agendamentos.cliente_id  = clientes.id
        JOIN servicos  ON agendamentos.servico_id  = servicos.id
        JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
        WHERE 1=1
    """
    parametros = []

    if data_filtro:
        query += " AND agendamentos.data = %s"
        parametros.append(data_filtro)

    if status_filtro:
        query += " AND agendamentos.status = %s"
        parametros.append(status_filtro)

    # Escopo: barbeiro vê só os agendamentos dele; master vê todos.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        query += " AND agendamentos.barbeiro_id = %s"
        parametros.append(escopo)

    query += " ORDER BY agendamentos.data, agendamentos.hora"

    conn = get_connection()
    resultados = conn.execute(query, parametros).fetchall()
    lista = [dict(r) for r in resultados]

    # Consumos (produtos) de cada agendamento: itens + total, pra mostrar no card.
    ids = [r["id"] for r in lista]
    consumos_por_ag = {}
    if ids:
        marcadores = ",".join(["%s"] * len(ids))
        cons = conn.execute(
            "SELECT id, agendamento_id, produto_id, descricao, valor_centavos, quantidade "
            "FROM consumos WHERE agendamento_id IN (%s) ORDER BY id" % marcadores, ids
        ).fetchall()
        for c in cons:
            consumos_por_ag.setdefault(c["agendamento_id"], []).append(dict(c))
    conn.close()

    # Salão (tablet compartilhado) não vê valores — remove preço e valores de consumo.
    ver_valores = pode_ver_valores()
    for r in lista:
        itens = consumos_por_ag.get(r["id"], [])
        r["consumos"] = itens
        r["consumos_total_centavos"] = sum(i["valor_centavos"] * i["quantidade"] for i in itens)
        if not ver_valores:
            r.pop("servico_preco", None)
            r.pop("consumos_total_centavos", None)
            for i in itens:
                i.pop("valor_centavos", None)

    return jsonify(lista)


@app.route("/api/admin/agendamentos", methods=["POST"])
@token_requerido
def criar_agendamento_admin():
    """
    Cria um agendamento pelo PAINEL (ex: barbeiro registrando um walk-in —
    cliente que chegou sem horário marcado). Reusa a mesma lógica da rota
    pública, mas SEM exigir antecedência mínima (o corte é agora/hoje) e
    PERMITINDO sobrescrever/encaixar em cima de outro horário (o barbeiro
    pode cortar mais rápido e encaixar outro corte no mesmo horário).
    """
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400

    # Escopo: barbeiro só pode marcar pra si mesmo — força o barbeiro_id dele,
    # ignorando o que vier no corpo. Master pode escolher qualquer barbeiro.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        dados["barbeiro_id"] = escopo

    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=False, permitir_conflito=True
    )
    return jsonify(corpo), status


@app.route("/api/admin/agendamentos/<int:agendamento_id>/cancelar", methods=["PATCH"])
@token_requerido
def cancelar_agendamento(agendamento_id):
    """
    Cancela um agendamento pelo ID.
    Usa PATCH porque estamos atualizando só o status,
    não o agendamento inteiro.

    Registra QUEM cancelou (login), quando e por onde, e avisa a equipe no
    Telegram dizendo o nome de quem fez. São duas fontes independentes: se um
    dia alguém perguntar "quem desmarcou o cliente?", dá pra responder na hora
    em vez de comparar backups.
    """
    conn = get_connection()
    existe = conn.execute(
        "SELECT id, status, barbeiro_id FROM agendamentos WHERE id = %s", (agendamento_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    # Escopo: barbeiro só cancela agendamento dele (trata como "não encontrado"
    # pra não revelar dados de outros barbeiros). Master cancela qualquer um.
    escopo = barbeiro_do_escopo()
    if escopo is not None and existe["barbeiro_id"] != escopo:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    if existe["status"] == "cancelado":
        conn.close()
        return jsonify({"erro": "Agendamento já está cancelado"}), 400

    # Quem está logado. O token guarda o admin_id; o nome do login vem do banco
    # pra a mensagem ficar legível ("rian" em vez de "3").
    quem = conn.execute(
        "SELECT usuario FROM admin WHERE id = %s",
        (getattr(g, "usuario", {}).get("admin_id"),)
    ).fetchone()
    login = quem["usuario"] if quem else "desconhecido"

    # Dados pro aviso — lidos ANTES do update, com os joins que a mensagem usa.
    detalhes = conn.execute(
        """SELECT agendamentos.data, agendamentos.hora,
                  clientes.nome  AS cliente,
                  servicos.nome  AS servico,
                  barbeiros.nome AS barbeiro
           FROM agendamentos
           JOIN clientes  ON agendamentos.cliente_id  = clientes.id
           JOIN servicos  ON agendamentos.servico_id  = servicos.id
           JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
           WHERE agendamentos.id = %s""",
        (agendamento_id,)
    ).fetchone()

    conn.execute(
        """UPDATE agendamentos
           SET status = 'cancelado', cancelado_em = now(),
               cancelado_por = %s, cancelado_via = 'painel'
           WHERE id = %s""",
        (login, agendamento_id)
    )
    conn.commit()
    conn.close()

    if detalhes:
        notificacoes.avisar_cancelamento(
            cliente=detalhes["cliente"], servico=detalhes["servico"],
            barbeiro=detalhes["barbeiro"], data_br=_data_br(detalhes["data"]),
            hora=detalhes["hora"], por=login,
        )

    return jsonify({"mensagem": "Agendamento cancelado com sucesso"})


# -------------------------------------------------------
# PAGAMENTO + CONSUMOS (produtos) de um agendamento
# -------------------------------------------------------
FORMAS_PAGAMENTO = {"cartao", "pix", "dinheiro"}


def _agendamento_no_escopo(conn, agendamento_id):
    """Row do agendamento se existir E estiver no escopo do usuário (barbeiro só
    mexe no próprio; master mexe em qualquer). Senão None."""
    row = conn.execute(
        "SELECT id, barbeiro_id FROM agendamentos WHERE id = %s", (agendamento_id,)
    ).fetchone()
    if not row:
        return None
    escopo = barbeiro_do_escopo()
    if escopo is not None and row["barbeiro_id"] != escopo:
        return None
    return row


@app.route("/api/admin/agendamentos/<int:agendamento_id>/pagamento", methods=["PATCH"])
@token_requerido
def registrar_pagamento(agendamento_id):
    """Registra a forma de pagamento do atendimento. { forma: cartao|pix|dinheiro }
    (forma vazia limpa o registro)."""
    dados = request.get_json(silent=True) or {}
    forma = (dados.get("forma") or "").strip().lower()
    if forma and forma not in FORMAS_PAGAMENTO:
        return jsonify({"erro": "Forma de pagamento inválida"}), 400
    conn = get_connection()
    if not _agendamento_no_escopo(conn, agendamento_id):
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404
    conn.execute("UPDATE agendamentos SET forma_pagamento = %s WHERE id = %s",
                 (forma or None, agendamento_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Forma de pagamento registrada.", "forma_pagamento": forma or None})


@app.route("/api/admin/agendamentos/<int:agendamento_id>/consumos", methods=["GET", "PUT"])
@token_requerido
def consumos_agendamento(agendamento_id):
    """Adicionais do atendimento. GET lista o que já tem; PUT substitui tudo pelo
    conjunto enviado: { itens: [{produto_id, quantidade}, ...] }."""
    conn = get_connection()
    if not _agendamento_no_escopo(conn, agendamento_id):
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    if request.method == "PUT":
        dados = request.get_json(silent=True) or {}
        itens = dados.get("itens") or []
        cur = conn.cursor()
        cur.execute("DELETE FROM consumos WHERE agendamento_id = %s", (agendamento_id,))
        # Preço vem do banco no momento do lançamento e é gravado no consumo
        # (snapshot): mudar o preço depois não altera o fechamento já feito.
        catalogo = {p["id"]: p for p in _produtos_ativos(conn)}
        for it in itens:
            prod = catalogo.get(it.get("produto_id"))
            try:
                qtd = int(it.get("quantidade") or 0)
            except (ValueError, TypeError):
                qtd = 0
            if not prod or qtd <= 0:
                continue
            cur.execute(
                "INSERT INTO consumos (agendamento_id, produto_id, descricao, valor_centavos, quantidade) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agendamento_id, prod["id"], prod["nome"], prod["preco_centavos"], qtd)
            )
        conn.commit()
        conn.close()
        return jsonify({"mensagem": "Adicionais salvos."})

    itens = conn.execute(
        "SELECT id, produto_id, descricao, valor_centavos, quantidade "
        "FROM consumos WHERE agendamento_id = %s ORDER BY id",
        (agendamento_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(i) for i in itens])


@app.route("/api/admin/bloqueios", methods=["GET"])
@token_requerido
def listar_bloqueios():
    """Bloqueios cadastrados. Barbeiro vê só os dele; master vê todos.

    O almoço fica de fora de propósito: ele é gravado nesta mesma tabela, mas
    quem manda nele é a aba Almoço — mostrar nos dois lugares dá remoção pela
    tela errada."""
    escopo = barbeiro_do_escopo()
    if escopo is None and not eh_master():
        return jsonify({"erro": "Acesso restrito ao administrador."}), 403

    consulta = ("SELECT b.*, bb.nome AS barbeiro_nome FROM bloqueios b "
                "LEFT JOIN barbeiros bb ON b.barbeiro_id = bb.id "
                "WHERE b.motivo IS DISTINCT FROM 'Almoço'")
    parametros = []
    if escopo is not None:
        consulta += " AND b.barbeiro_id = %s"
        parametros.append(escopo)
    consulta += " ORDER BY b.data, b.hora"

    conn = get_connection()
    bloqueios = conn.execute(consulta, parametros).fetchall()
    conn.close()
    return jsonify([dict(b) for b in bloqueios])


@app.route("/api/admin/bloqueios", methods=["POST"])
@token_requerido
def criar_bloqueio():
    """
    Cria um bloqueio de dia inteiro, de um horário ou de um INTERVALO.

    Dia inteiro:  { "data": "2026-07-10", "motivo": "Feriado" }
    Um horário:   { "data": "2026-07-10", "hora": "14:00" }
    Saída e volta:{ "data": "2026-07-10", "hora": "14:00", "volta": "15:30" }

    O intervalo vira UMA linha com duracao_min (mesmo mecanismo do almoço), não
    várias linhas de 30 em 30 — remover depois é um clique só.

    Barbeiro comum bloqueia só a própria agenda e precisa informar a hora:
    fechar o dia inteiro da barbearia é decisão do dono.
    """
    dados = request.get_json(silent=True)
    if not dados or not dados.get("data"):
        return jsonify({"erro": "Data é obrigatória"}), 400

    valido, erro = validar_data(dados["data"])
    if not valido:
        return jsonify({"erro": erro}), 400

    hora = dados.get("hora")
    if hora:
        valido, erro = validar_hora(hora)
        if not valido:
            return jsonify({"erro": erro}), 400

    # Volta: o painel pergunta "saio às / volto às", que é como a pessoa pensa.
    # Aqui isso vira duração em minutos. Fica ANTES da checagem de permissão pra
    # quem preencheu só a volta ouvir "faltou a saída", e não "isso é com o
    # administrador" — que manda procurar no lugar errado.
    duracao_min = None
    volta = dados.get("volta")
    if volta:
        if not hora:
            return jsonify({"erro": "Informe a hora de saída"}), 400
        valido, erro = validar_hora(volta)
        if not valido:
            return jsonify({"erro": erro}), 400
        duracao_min = hhmm_para_min(volta) - hhmm_para_min(hora)
        if duracao_min <= 0:
            return jsonify({"erro": "A volta precisa ser depois da saída"}), 400

    escopo = barbeiro_do_escopo()      # barbeiro → seu id; master/salão → None
    if escopo is None:
        if not eh_master():
            return jsonify({"erro": "Acesso restrito ao administrador."}), 403
        # Master sem barbeiro escolhido = bloqueio da barbearia inteira.
        barbeiro_id = dados.get("barbeiro_id") or None
    else:
        barbeiro_id = escopo
        if not hora:
            return jsonify({"erro": "Informe a hora. Fechar o dia inteiro é com o administrador."}), 403

    conn = get_connection()

    # Verifica se já existe bloqueio igual.
    # 'IS NOT DISTINCT FROM' é a comparação null-safe do Postgres (equivalente
    # ao 'hora IS ?' do SQLite): trata NULL = NULL como verdadeiro.
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND hora IS NOT DISTINCT FROM %s "
        "AND barbeiro_id IS NOT DISTINCT FROM %s",
        (dados["data"], hora, barbeiro_id)
    ).fetchone()

    if existe:
        conn.close()
        return jsonify({"erro": "Já existe um bloqueio para essa data/hora"}), 409

    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo, barbeiro_id, duracao_min) "
        "VALUES (%s, %s, %s, %s, %s)",
        (dados["data"], hora, dados.get("motivo"), barbeiro_id, duracao_min)
    )
    conn.commit()
    conn.close()

    if not hora:
        mensagem = "Dia bloqueado com sucesso!"
    elif volta:
        mensagem = f"Agenda bloqueada das {hora} às {volta}."
    else:
        mensagem = "Horário bloqueado com sucesso!"
    return jsonify({"mensagem": mensagem}), 201


@app.route("/api/admin/bloqueios/<int:bloqueio_id>", methods=["DELETE"])
@token_requerido
def remover_bloqueio(bloqueio_id):
    """Remove um bloqueio (desbloqueia o dia ou horário).
    Barbeiro só desbloqueia o que é dele."""
    escopo = barbeiro_do_escopo()
    if escopo is None and not eh_master():
        return jsonify({"erro": "Acesso restrito ao administrador."}), 403

    conn = get_connection()
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE id = %s", (bloqueio_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Bloqueio não encontrado"}), 404

    if escopo is not None:
        # Mesmo 404 de quando não existe: pro barbeiro, bloqueio de outro é
        # como se não estivesse lá.
        meu = conn.execute(
            "SELECT id FROM bloqueios WHERE id = %s AND barbeiro_id = %s",
            (bloqueio_id, escopo)
        ).fetchone()
        if not meu:
            conn.close()
            return jsonify({"erro": "Bloqueio não encontrado"}), 404

    conn.execute("DELETE FROM bloqueios WHERE id = %s", (bloqueio_id,))
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Bloqueio removido com sucesso"})


# -------------------------------------------------------
# ALMOÇO — o barbeiro bloqueia 60min do próprio dia (e pode liberar).
# Reaproveita a tabela bloqueios (barbeiro_id + duracao_min + motivo 'Almoço').
# Não é @somente_master: o próprio barbeiro gerencia o almoço dele.
# -------------------------------------------------------
def _barbeiro_alvo_almoco(fonte):
    """Descobre o barbeiro do almoço. Retorna (id, erro).
    - Barbeiro logado: SEMPRE o próprio (não mexe no de ninguém);
    - Master/salão: o barbeiro_id informado (pode escolher qualquer um) e,
      se não vier, cai no barbeiro do token (master = JP)."""
    escopo = barbeiro_do_escopo()          # barbeiro → seu id; master/salão → None
    if escopo is not None:
        return escopo, None
    barbeiro_id = fonte.get("barbeiro_id") or g.usuario.get("barbeiro_id")
    if not barbeiro_id:
        return None, "barbeiro_id é obrigatório"
    return barbeiro_id, None


@app.route("/api/admin/almoco", methods=["GET"])
@token_requerido
def obter_almoco():
    """Almoço do barbeiro (logado ou informado) numa data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    row = conn.execute(
        """SELECT id, hora, duracao_min FROM bloqueios
           WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'
           ORDER BY hora LIMIT 1""",
        (data, barbeiro_id)
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else None)


@app.route("/api/admin/almoco", methods=["POST"])
@token_requerido
def marcar_almoco():
    """Barbeiro bloqueia 60min de almoço no dia. { data, hora, barbeiro_id? }"""
    dados = request.get_json(silent=True) or {}
    data = dados.get("data")
    hora = dados.get("hora")
    if not data or not hora:
        return jsonify({"erro": "Data e hora são obrigatórias"}), 400
    valido, erro = validar_data(data)
    if not valido:
        return jsonify({"erro": erro}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(dados)
    if erro:
        return jsonify({"erro": erro}), 400

    conn = get_connection()
    ja = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    ).fetchone()
    if ja:
        conn.close()
        return jsonify({"erro": "Você já tem um almoço marcado nesse dia. Libere o atual primeiro."}), 409
    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo, barbeiro_id, duracao_min) VALUES (%s, %s, 'Almoço', %s, %s)",
        (data, hora, barbeiro_id, DURACAO_ALMOCO_MIN)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço bloqueado às {hora} (60 min)."}), 201


@app.route("/api/admin/almoco", methods=["DELETE"])
@token_requerido
def liberar_almoco():
    """Libera (remove) o almoço do barbeiro na data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute(
        "DELETE FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço liberado."})


@app.route("/api/admin/almoco-fixo", methods=["GET"])
@token_requerido
def obter_almoco_fixo():
    """Almoço fixo (todo dia). Barbeiro vê o próprio; master/salão pode consultar
    outro passando ?barbeiro_id=."""
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    row = conn.execute("SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone()
    conn.close()
    return jsonify({"almoco_fixo": row["almoco_fixo"] if row else None})


@app.route("/api/admin/almoco-fixo", methods=["PUT"])
@token_requerido
def definir_almoco_fixo():
    """Define o almoço fixo (repete todo dia, 60min). Corpo: { hora }."""
    dados = request.get_json(silent=True) or {}
    hora = dados.get("hora")
    if not hora:
        return jsonify({"erro": "Hora é obrigatória"}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(dados)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = %s WHERE id = %s", (hora, barbeiro_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço fixo definido às {hora} (todo dia)."})


@app.route("/api/admin/almoco-fixo", methods=["DELETE"])
@token_requerido
def remover_almoco_fixo():
    """Remove o almoço fixo (volta a marcar manualmente por dia). Master/salão
    pode remover o de outro passando ?barbeiro_id=."""
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = NULL WHERE id = %s", (barbeiro_id,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço fixo removido."})


@app.route("/api/admin/almocos", methods=["GET"])
@token_requerido
def listar_almocos():
    """Almoços do dia (fixo + manual) por barbeiro, pra desenhar na agenda como
    um bloco. Barbeiro vê o próprio; master/salão vê todos. ?data=YYYY-MM-DD"""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    escopo = barbeiro_do_escopo()   # barbeiro → seu id; master/salão → None
    conn = get_connection()

    q_manual = """SELECT b.barbeiro_id, b.hora, b.duracao_min, bb.nome AS barbeiro_nome
                  FROM bloqueios b JOIN barbeiros bb ON b.barbeiro_id = bb.id
                  WHERE b.data = %s AND b.motivo = 'Almoço'"""
    pm = [data]
    if escopo is not None:
        q_manual += " AND b.barbeiro_id = %s"
        pm.append(escopo)
    manuais = conn.execute(q_manual, pm).fetchall()

    q_fixo = ("SELECT id AS barbeiro_id, nome AS barbeiro_nome, almoco_fixo "
              "FROM barbeiros WHERE ativo = 1 AND almoco_fixo IS NOT NULL")
    pf = []
    if escopo is not None:
        q_fixo += " AND id = %s"
        pf.append(escopo)
    fixos = conn.execute(q_fixo, pf).fetchall()
    conn.close()

    saida, vistos = [], set()
    for m in manuais:
        hora = m["hora"][:5]
        saida.append({"barbeiro_id": m["barbeiro_id"], "barbeiro_nome": m["barbeiro_nome"],
                      "hora": hora, "duracao_min": m["duracao_min"] or 60, "tipo": "manual"})
        vistos.add((m["barbeiro_id"], hora))
    for f in fixos:
        hora = f["almoco_fixo"][:5]
        if (f["barbeiro_id"], hora) in vistos:   # manual do dia já cobre esse horário
            continue
        saida.append({"barbeiro_id": f["barbeiro_id"], "barbeiro_nome": f["barbeiro_nome"],
                      "hora": hora, "duracao_min": 60, "tipo": "fixo"})
    return jsonify(saida)


# -------------------------------------------------------
# EXPEDIENTE — o master ajusta a jornada (início/fim) de cada barbeiro por dia.
# Sobrepõe o horário padrão do dia SÓ pra aquele barbeiro naquela data.
# -------------------------------------------------------
@app.route("/api/admin/expedientes", methods=["GET"])
@token_requerido
@somente_master
def listar_expedientes():
    """Barbeiros ativos com o horário que vale pra eles numa data (expediente
    especial, se definido, ou o padrão do dia). ?data=YYYY-MM-DD"""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    padrao_ini, padrao_fim = horario_do_dia(data)
    fechado, _ = dia_fechado(data)
    conn = get_connection()
    barbeiros = conn.execute(
        "SELECT id, nome FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    exps = conn.execute(
        "SELECT barbeiro_id, inicio, fim FROM expedientes WHERE data = %s", (data,)
    ).fetchall()
    conn.close()
    por_barb = {e["barbeiro_id"]: (e["inicio"], e["fim"]) for e in exps}
    lista = []
    for b in barbeiros:
        ov = por_barb.get(b["id"])
        folga = ov is not None and expediente_e_folga(ov[0], ov[1])
        lista.append({
            "barbeiro_id": b["id"],
            "barbeiro_nome": b["nome"],
            # Numa folga devolvemos o horário padrão nos campos, pra o painel já
            # abrir com valores usáveis se o master quiser desfazer.
            "inicio": padrao_ini if folga else (ov[0] if ov else padrao_ini),
            "fim": padrao_fim if folga else (ov[1] if ov else padrao_fim),
            "personalizado": ov is not None,
            "folga": folga,
        })
    return jsonify({
        "data": data,
        "fechado": fechado,
        "padrao": {"inicio": padrao_ini, "fim": padrao_fim},
        "barbeiros": lista,
    })


@app.route("/api/admin/expediente", methods=["PUT"])
@token_requerido
@somente_master
def definir_expediente():
    """Define/atualiza o expediente de um barbeiro num dia.
    Corpo: { barbeiro_id, data, inicio, fim }."""
    dados = request.get_json(silent=True) or {}
    barbeiro_id = dados.get("barbeiro_id")
    data = dados.get("data")
    inicio = dados.get("inicio")
    fim = dados.get("fim")
    folga = bool(dados.get("folga"))

    if folga:
        # Folga = expediente vazio. Fica gravado assim em vez de um horário
        # esquisito de 1 minuto, e o painel mostra "FOLGA" pra quem olhar.
        inicio = fim = HORA_FOLGA
    else:
        if not (barbeiro_id and data and inicio and fim):
            return jsonify({"erro": "barbeiro_id, data, início e fim são obrigatórios"}), 400
        for h in (inicio, fim):
            ok, e = validar_hora(h)
            if not ok:
                return jsonify({"erro": e}), 400
        if hhmm_para_min(inicio) >= hhmm_para_min(fim):
            return jsonify({"erro": "A hora de início tem que ser antes do fim"}), 400

    if not (barbeiro_id and data):
        return jsonify({"erro": "barbeiro_id e data são obrigatórios"}), 400
    valido, erro = validar_data(data)
    if not valido:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    if not conn.execute("SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404
    conn.execute(
        """INSERT INTO expedientes (barbeiro_id, data, inicio, fim)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (barbeiro_id, data)
           DO UPDATE SET inicio = EXCLUDED.inicio, fim = EXCLUDED.fim""",
        (barbeiro_id, data, inicio, fim)
    )
    conn.commit()
    conn.close()
    if folga:
        return jsonify({"mensagem": "Folga marcada — o dia fica sem horários.", "folga": True})
    return jsonify({"mensagem": f"Expediente definido ({inicio}–{fim})."})


@app.route("/api/admin/expediente", methods=["DELETE"])
@token_requerido
@somente_master
def remover_expediente():
    """Remove o expediente especial (volta pro horário padrão do dia).
    ?barbeiro_id=&data="""
    barbeiro_id = request.args.get("barbeiro_id")
    data = request.args.get("data")
    if not (barbeiro_id and data):
        return jsonify({"erro": "barbeiro_id e data são obrigatórios"}), 400
    conn = get_connection()
    conn.execute("DELETE FROM expedientes WHERE barbeiro_id = %s AND data = %s", (barbeiro_id, data))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Expediente removido (voltou ao normal)."})
