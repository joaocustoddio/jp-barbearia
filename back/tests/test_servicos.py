"""
Teste do cardápio público: GET /api/servicos.

Serviço tirado do cardápio (ativo = 0) NÃO pode aparecer pra marcar, e ao mesmo
tempo NÃO pode ser apagado do banco: agendamentos.servico_id aponta pra ele, e
sem a linha o corte que já foi feito sumiria do histórico e do faturamento.
Por isso o filtro mora na consulta — é ele que segura essa regra.

Conexão falsa: nada de banco.
"""
import json

import pytest

import app
import rotas_publicas


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class Conexao:
    """Devolve só o que a consulta pediu, como o banco faria: se o SELECT
    filtrar por ativo, o desativado não volta."""

    def __init__(self, servicos):
        self.servicos = servicos
        self.consultas = []

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.consultas.append(consulta)
        linhas = self.servicos
        if "ativo = 1" in consulta:
            linhas = [s for s in linhas if s.get("ativo", 1) == 1]
        return _Cursor(linhas)

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        pass


CARDAPIO = [
    {"id": 1, "nome": "Degradê", "preco": 40.0, "ativo": 1},
    {"id": 3, "nome": "Navalhado", "preco": 40.0, "ativo": 0},
    {"id": 4, "nome": "Barba", "preco": 25.0, "ativo": 1},
]


@pytest.fixture
def cliente():
    return app.app.test_client()


def test_servico_desativado_nao_aparece(cliente, monkeypatch):
    conn = Conexao(CARDAPIO)
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: conn)
    nomes = [s["nome"] for s in json.loads(cliente.get("/api/servicos").data)]
    assert nomes == ["Degradê", "Barba"]


def test_a_consulta_filtra_no_banco(cliente, monkeypatch):
    """Não basta o site esconder: tem que sair filtrado da consulta, senão
    qualquer outro consumidor da rota volta a oferecer o serviço."""
    conn = Conexao(CARDAPIO)
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: conn)
    cliente.get("/api/servicos")
    assert any("from servicos where ativo = 1" in c for c in conn.consultas)
