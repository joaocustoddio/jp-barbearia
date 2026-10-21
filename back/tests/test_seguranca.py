"""Regressões de acesso público, sem banco ou notificações reais."""
from datetime import datetime

import pytest

import app
import rotas_publicas
from config import FUSO_BR
from extensoes import limiter


@pytest.fixture(autouse=True)
def isolamento(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(rotas_publicas, "agora_br", lambda: datetime(2026, 8, 26, 10, 0, tzinfo=FUSO_BR), raising=False)



class Banco:
    def __init__(self, **trocas):
        self.linha = {"id": 555, "data": "2026-08-27", "hora": "10:00", "status": "confirmado", "cliente": "João", "servico": "Corte", "barbeiro": "Rian"}
        self.linha.update(trocas)
        self.sql = []
        self.commits = 0
        self.atualizado = True

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.lower().split()), params))
        return self

    def fetchone(self):
        if self.sql[-1][0].startswith("update") and not self.atualizado:
            return None
        return self.linha

    def fetchall(self):
        return [self.linha]

    def close(self):
        pass

    def commit(self):
        self.commits += 1


def test_consulta_lista_pelo_telefone(monkeypatch):
    """O cliente consulta só com o telefone — foi a decisão do João em
    21/10/2026, depois de o código assinado aparecer como um calhamaço na
    tela de confirmação. A consulta filtra pelo número e nunca por id solto,
    senão daria pra ler o agendamento de qualquer um chutando número."""
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    resposta = app.app.test_client().get(
        "/api/agendamentos/consultar", query_string={"telefone": "(11) 98888-7777"})
    assert resposta.status_code == 200
    sql, params = banco.sql[0]
    assert "clientes.telefone" in sql
    assert "agendamentos.id = %s" not in sql
    assert params[0] == "11988887777"


def test_consulta_exige_telefone_valido(monkeypatch):
    """Sem telefone não vai consulta nenhuma pro banco."""
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    for query in ({}, {"telefone": "123"}):
        resposta = app.app.test_client().get("/api/agendamentos/consultar", query_string=query)
        assert resposta.status_code == 400
    assert banco.sql == []


@pytest.mark.parametrize("trocas", [{"status": "concluido"}, {"status": "cancelado"}, {"data": "2026-08-25"}, {"data": "2026-08-26", "hora": "09:59"}, {"data": "2026-08-26", "hora": "10:00"}])
def test_cancelamento_recusa_finalizados_e_passados(monkeypatch, trocas):
    banco = Banco(**trocas)
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    avisos = []
    monkeypatch.setattr(rotas_publicas.notificacoes, "avisar_cancelamento", lambda **kw: avisos.append(kw))
    resposta = app.app.test_client().post("/api/agendamentos/cancelar", json={"agendamento_id": 555, "telefone": "11988887777"})
    assert resposta.status_code == 409
    assert banco.commits == 0
    assert not any(sql.startswith("update") for sql, _ in banco.sql)
    assert avisos == []


def test_cancelamento_trava_linha_e_confere_update(monkeypatch):
    banco = Banco()
    banco.atualizado = False
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    avisos = []
    monkeypatch.setattr(rotas_publicas.notificacoes, "avisar_cancelamento", lambda **kw: avisos.append(kw))
    resposta = app.app.test_client().post("/api/agendamentos/cancelar", json={"agendamento_id": 555, "telefone": "11988887777"})
    assert resposta.status_code == 409
    assert "for update of agendamentos" in banco.sql[0][0]
    assert "returning id" in banco.sql[1][0]
    assert banco.commits == 0
    assert avisos == []


def test_listagem_publica_removida(monkeypatch):
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    assert app.app.test_client().get("/api/agendamentos").status_code == 405
    assert banco.sql == []


@pytest.mark.parametrize("rota,metodo", [("/api/agendamentos/consultar", "get"), ("/api/agendamentos/cancelar", "post")])
def test_limita_tentativas_publicas(monkeypatch, rota, metodo):
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    requisitar = getattr(app.app.test_client(), metodo)
    respostas = [requisitar(rota).status_code for _ in range(11)]
    assert respostas[:10] == [400] * 10
    assert respostas[10] == 429
    limiter.reset()
