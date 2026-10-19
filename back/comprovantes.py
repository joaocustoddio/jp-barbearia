"""Comprovantes assinados que autorizam gerenciar um único agendamento."""
from itsdangerous import BadData, URLSafeSerializer

from extensoes import app


def _assinador():
    return URLSafeSerializer(app.config["SECRET_KEY"], salt="comprovante-agendamento-v1")


def criar_comprovante(agendamento_id):
    return _assinador().dumps({"agendamento_id": agendamento_id,
                              "finalidade": "gerenciar_agendamento"})


def validar_comprovante(token):
    if not isinstance(token, str) or not token or len(token) > 2048:
        return None
    try:
        dados = _assinador().loads(token)
    except BadData:
        return None
    if not isinstance(dados, dict) or dados.get("finalidade") != "gerenciar_agendamento":
        return None
    identificador = dados.get("agendamento_id")
    return identificador if type(identificador) is int and identificador > 0 else None
