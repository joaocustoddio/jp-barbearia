"""
rotas_publicas.py
-----------------
Rotas abertas, usadas pelo site do cliente: catálogo de serviços, barbeiros,
criação/consulta/cancelamento do próprio agendamento.

A rota /api/horarios-disponiveis fica em app.py — os testes injetam a conexão
de banco por lá (monkeypatch em app.get_connection / app.horario_efetivo).
"""

import re

from flask import request, jsonify

from agendamentos import _data_br, _processar_novo_agendamento
from config import data_hoje
from database import get_connection
from extensoes import app
from validacoes import validar_data, validar_telefone
import notificacoes


@app.route("/api/servicos", methods=["GET"])
def listar_servicos():
    conn = get_connection()
    servicos = conn.execute("SELECT * FROM servicos").fetchall()
    conn.close()
    return jsonify([dict(s) for s in servicos])


@app.route("/api/barbeiros", methods=["GET"])
def listar_barbeiros():
    """Retorna os barbeiros ativos. Usado pro cliente escolher no chat."""
    conn = get_connection()
    barbeiros = conn.execute(
        "SELECT id, nome, foto FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in barbeiros])


@app.route("/api/agendamentos", methods=["POST"])
def criar_agendamento():
    """Agendamento pelo cliente (site público) — exige antecedência e telefone."""
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400
    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=True, exigir_telefone=True, avisar_equipe=True,
        checar_bloqueio=True
    )
    return jsonify(corpo), status


@app.route("/api/agendamentos/consultar", methods=["GET"])
def consultar_agendamentos_cliente():
    """Público: lista os agendamentos FUTUROS (não cancelados) de um telefone,
    pro cliente poder cancelar pelo site. ?telefone=..."""
    telefone = (request.args.get("telefone") or "").strip()
    if not telefone:
        return jsonify({"erro": "Informe o telefone"}), 400
    valido, erro = validar_telefone(telefone)
    if not valido:
        return jsonify({"erro": erro}), 400
    tel_num = re.sub(r"\D", "", telefone)
    hoje = data_hoje().isoformat()
    conn = get_connection()
    rows = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                   servicos.nome  AS servico_nome,
                   barbeiros.nome AS barbeiro_nome
            FROM agendamentos
            JOIN clientes  ON agendamentos.cliente_id  = clientes.id
            JOIN servicos  ON agendamentos.servico_id  = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
              AND agendamentos.status != 'cancelado'
              AND agendamentos.data >= %s
            ORDER BY agendamentos.data, agendamentos.hora""",
        (tel_num, hoje)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/agendamentos/cancelar", methods=["POST"])
def cancelar_agendamento_cliente():
    """Público: cliente cancela o próprio agendamento (confere o telefone).
    Corpo: { agendamento_id, telefone }."""
    dados = request.get_json(silent=True) or {}
    agendamento_id = dados.get("agendamento_id")
    telefone = (dados.get("telefone") or "").strip()
    if not agendamento_id or not telefone:
        return jsonify({"erro": "Agendamento e telefone são obrigatórios"}), 400
    tel_num = re.sub(r"\D", "", telefone)
    conn = get_connection()
    row = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                   clientes.nome AS cliente, servicos.nome AS servico,
                   barbeiros.nome AS barbeiro
            FROM agendamentos
            JOIN clientes ON agendamentos.cliente_id = clientes.id
            JOIN servicos ON agendamentos.servico_id = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE agendamentos.id = %s
              AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
              AND agendamentos.status != 'cancelado'""",
        (agendamento_id, tel_num)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado para esse telefone"}), 404
    conn.execute("UPDATE agendamentos SET status = 'cancelado' WHERE id = %s", (agendamento_id,))
    conn.commit()
    conn.close()

    notificacoes.avisar_cancelamento(
        cliente=row["cliente"], servico=row["servico"], barbeiro=row["barbeiro"],
        data_br=_data_br(row["data"]), hora=row["hora"],
    )
    return jsonify({"mensagem": "Agendamento cancelado com sucesso."})


@app.route("/api/agendamentos", methods=["GET"])
def listar_agendamentos():
    data_filtro = request.args.get("data")

    # Valida data se vier como filtro
    if data_filtro:
        valido, erro = validar_data(data_filtro)
        if not valido:
            return jsonify({"erro": erro}), 400

    conn = get_connection()
    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            clientes.nome  AS cliente_nome,
            clientes.telefone AS cliente_telefone,
            servicos.nome  AS servico_nome,
            servicos.preco AS servico_preco
        FROM agendamentos
        JOIN clientes ON agendamentos.cliente_id = clientes.id
        JOIN servicos ON agendamentos.servico_id = servicos.id
    """
    parametros = ()
    if data_filtro:
        query += " WHERE agendamentos.data = %s"
        parametros = (data_filtro,)

    query += " ORDER BY agendamentos.data, agendamentos.hora"
    resultados = conn.execute(query, parametros).fetchall()
    conn.close()

    return jsonify([dict(r) for r in resultados])
