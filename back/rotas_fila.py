"""
rotas_fila.py
-------------
Rotas da fila online de fim de ano: períodos de fila (só o dono).
"""

from datetime import datetime

from flask import g, jsonify, request

from auth import somente_master, token_requerido
from database import get_connection
from extensoes import app
import fila_dados


def _data_valida(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date().isoformat() == valor
    except (TypeError, ValueError):
        return False


@app.route("/api/admin/periodos-fila", methods=["GET"])
@token_requerido
@somente_master
def listar_periodos_fila():
    conn = get_connection()
    periodos = fila_dados.listar_periodos(conn)
    conn.close()
    return jsonify(periodos)


@app.route("/api/admin/periodos-fila", methods=["POST"])
@token_requerido
@somente_master
def criar_periodo_fila():
    dados = request.get_json(silent=True) or {}
    inicio, fim = dados.get("inicio"), dados.get("fim")
    if not (_data_valida(inicio) and _data_valida(fim)):
        return jsonify({"erro": "Informe as datas de início e fim."}), 400
    if fim < inicio:
        return jsonify({"erro": "A data de fim vem antes da de início."}), 400
    conn = get_connection()
    periodo_id = fila_dados.criar_periodo(conn, inicio, fim, g.usuario.get("admin_id"))
    conn.close()
    return jsonify({"id": periodo_id}), 201


@app.route("/api/admin/periodos-fila/<int:periodo_id>", methods=["DELETE"])
@token_requerido
@somente_master
def apagar_periodo_fila(periodo_id):
    conn = get_connection()
    apagou = fila_dados.apagar_periodo(conn, periodo_id)
    conn.close()
    if not apagou:
        return jsonify({"erro": "Período não encontrado."}), 404
    return jsonify({"mensagem": "Período apagado."})
