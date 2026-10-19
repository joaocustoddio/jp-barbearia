"""
rotas_publicas.py
-----------------
Rotas abertas, usadas pelo site do cliente: catálogo de serviços, barbeiros,
criação/consulta/cancelamento do próprio agendamento.

A rota /api/horarios-disponiveis fica em app.py — os testes injetam a conexão
de banco por lá (monkeypatch em app.get_connection / app.horario_efetivo).
"""

import re
from datetime import datetime

from flask import request, jsonify

from agendamentos import _data_br, _processar_novo_agendamento
from config import agora_br, FUSO_BR, data_hoje
from precos import catalogo_na_data
from comprovantes import validar_comprovante
from database import get_connection
from extensoes import app, limiter
from validacoes import validar_telefone
import notificacoes


@app.route("/api/servicos", methods=["GET"])
def listar_servicos():
    """Cardápio do site e do painel. Só os ativos: serviço desativado continua
    na tabela por causa do histórico, mas não pode mais ser marcado."""
    data = request.args.get('data') or data_hoje().isoformat()
    try:
        data = datetime.strptime(data, '%Y-%m-%d').date().isoformat()
    except (TypeError, ValueError):
        return jsonify({'erro':'Data inválida.'}), 400
    conn = get_connection()
    servicos = catalogo_na_data(conn, data)
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
@limiter.limit("10 per minute")
def consultar_agendamentos_cliente():
    """Consulta apenas o agendamento autorizado pelo comprovante e telefone."""
    telefone = (request.args.get("telefone") or "").strip()
    if not telefone:
        return jsonify({"erro": "Informe o telefone"}), 400
    valido, erro = validar_telefone(telefone)
    if not valido:
        return jsonify({"erro": erro}), 400
    token = request.args.get("comprovante")
    if not token:
        return jsonify({"erro": "Informe o comprovante. Sem ele, entre em contato com a barbearia."}), 400
    agendamento_id = validar_comprovante(token)
    if agendamento_id is None:
        return jsonify({"erro": "Comprovante inválido"}), 403
    tel_num = re.sub(r"\D", "", telefone)
    agora = agora_br()
    conn = get_connection()
    rows = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                   servicos.nome  AS servico_nome,
                   barbeiros.nome AS barbeiro_nome
            FROM agendamentos
            JOIN clientes  ON agendamentos.cliente_id  = clientes.id
            JOIN servicos  ON agendamentos.servico_id  = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE agendamentos.id = %s
              AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
              AND agendamentos.status NOT IN ('cancelado', 'concluido')
              AND (agendamentos.data::date + agendamentos.hora::time) > %s
            ORDER BY agendamentos.data, agendamentos.hora""",
        (agendamento_id, tel_num, agora.replace(tzinfo=None))
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/agendamentos/cancelar", methods=["POST"])
@limiter.limit("10 per minute")
def cancelar_agendamento_cliente():
    """Cancela com telefone e comprovante assinado do próprio agendamento."""
    dados = request.get_json(silent=True) or {}
    if not isinstance(dados, dict):
        return jsonify({"erro": "Corpo da requisição inválido"}), 400
    agendamento_id = dados.get("agendamento_id")
    telefone = dados.get("telefone")
    if type(agendamento_id) is not int or agendamento_id <= 0 or not isinstance(telefone, str) or not telefone.strip():
        return jsonify({"erro": "Agendamento e telefone são obrigatórios"}), 400
    valido, erro = validar_telefone(telefone)
    if not valido:
        return jsonify({"erro": erro}), 400
    token = dados.get("comprovante")
    if not token:
        return jsonify({"erro": "Informe o comprovante. Sem ele, entre em contato com a barbearia."}), 400
    if validar_comprovante(token) != agendamento_id:
        return jsonify({"erro": "Comprovante inválido"}), 403
    tel_num = re.sub(r"\D", "", telefone)
    conn = get_connection()
    row = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora, agendamentos.status,
                   clientes.nome AS cliente, servicos.nome AS servico,
                   barbeiros.nome AS barbeiro
            FROM agendamentos
            JOIN clientes ON agendamentos.cliente_id = clientes.id
            JOIN servicos ON agendamentos.servico_id = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE agendamentos.id = %s
              AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
            FOR UPDATE OF agendamentos""",
        (agendamento_id, tel_num)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado para esse telefone"}), 404
    inicio = datetime.fromisoformat(f"{row['data']}T{row['hora']}").replace(tzinfo=FUSO_BR)
    if row["status"] in ("cancelado", "concluido") or inicio <= agora_br():
        conn.close()
        return jsonify({"erro": "Este agendamento não pode mais ser cancelado pelo site."}), 409
    # cancelado_por fica com o telefone: é o que identifica quem cancelou pelo
    # site (a pessoa não tem login).
    atualizado = conn.execute(
        """UPDATE agendamentos
           SET status = 'cancelado', cancelado_em = now(),
               cancelado_por = %s, cancelado_via = 'site'
           WHERE id = %s AND status NOT IN ('cancelado', 'concluido')
           RETURNING id""",
        (tel_num, agendamento_id)
    ).fetchone()
    if not atualizado:
        conn.close()
        return jsonify({"erro": "Este agendamento não pode mais ser cancelado pelo site."}), 409
    conn.commit()
    conn.close()

    notificacoes.avisar_cancelamento(
        cliente=row["cliente"], servico=row["servico"], barbeiro=row["barbeiro"],
        data_br=_data_br(row["data"]), hora=row["hora"],
    )
    return jsonify({"mensagem": "Agendamento cancelado com sucesso."})
