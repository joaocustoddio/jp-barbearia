"""Regressões de acesso público, sem banco ou notificações reais."""
from datetime import datetime

import pytest
from itsdangerous import URLSafeSerializer

import app
import rotas_publicas
from config import FUSO_BR
from extensoes import limiter


@pytest.fixture(autouse=True)
def isolamento(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(rotas_publicas, "agora_br", lambda: datetime(2026, 8, 26, 10, 0, tzinfo=FUSO_BR), raising=False)


def comprovante(id=555, **trocas):
    dados = {"agendamento_id": id, "finalidade": "gerenciar_agendamento"}
    dados.update(trocas)
    return URLSafeSerializer(app.app.config["SECRET_KEY"], salt="comprovante-agendamento-v1").dumps(dados)


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


@pytest.mark.parametrize("token", [None, "adulterado", comprovante(556), comprovante(finalidade="outra")])
def test_cancelamento_exige_comprovante_do_agendamento(monkeypatch, token):
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    monkeypatch.setattr(rotas_publicas.notificacoes, "avisar_cancelamento", lambda **kw: None)
    resposta = app.app.test_client().post("/api/agendamentos/cancelar", json={"agendamento_id": 555, "telefone": "11988887777", "comprovante": token})
    assert resposta.status_code in (400, 403)
    assert banco.sql == []


def test_consulta_sem_comprovante_nao_lista_telefone(monkeypatch):
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    resposta = app.app.test_client().get("/api/agendamentos/consultar?telefone=11988887777")
    assert resposta.status_code == 400
    assert "barbearia" in resposta.json["erro"]
    assert banco.sql == []


def test_consulta_restringe_ao_id_assinado_e_telefone(monkeypatch):
    banco = Banco()
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    resposta = app.app.test_client().get("/api/agendamentos/consultar", query_string={"telefone": "(11) 98888-7777", "comprovante": comprovante()})
    assert resposta.status_code == 200
    sql, params = banco.sql[0]
    assert "agendamentos.id = %s" in sql
    assert params[:2] == (555, "11988887777")


@pytest.mark.parametrize("trocas", [{"status": "concluido"}, {"status": "cancelado"}, {"data": "2026-08-25"}, {"data": "2026-08-26", "hora": "09:59"}, {"data": "2026-08-26", "hora": "10:00"}])
def test_cancelamento_recusa_finalizados_e_passados(monkeypatch, trocas):
    banco = Banco(**trocas)
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: banco)
    avisos = []
    monkeypatch.setattr(rotas_publicas.notificacoes, "avisar_cancelamento", lambda **kw: avisos.append(kw))
    resposta = app.app.test_client().post("/api/agendamentos/cancelar", json={"agendamento_id": 555, "telefone": "11988887777", "comprovante": comprovante()})
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
    resposta = app.app.test_client().post("/api/agendamentos/cancelar", json={"agendamento_id": 555, "telefone": "11988887777", "comprovante": comprovante()})
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


def test_comprovante_assinado_recusa_alteracao_e_chave_diferente():
    from comprovantes import criar_comprovante, validar_comprovante
    token = criar_comprovante(555)
    assert validar_comprovante(token) == 555
    assert validar_comprovante(token + "adulterado") is None
    falso = URLSafeSerializer("outra-chave", salt="comprovante-agendamento-v1").dumps(
        {"agendamento_id": 555, "finalidade": "gerenciar_agendamento"})
    assert validar_comprovante(falso) is None


@pytest.mark.parametrize("rota,metodo", [("/api/agendamentos/consultar", "get"), ("/api/agendamentos/cancelar", "post")])
def test_limita_tentativas_publicas(monkeypatch, rota, metodo):
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    requisitar = getattr(app.app.test_client(), metodo)
    respostas = [requisitar(rota).status_code for _ in range(11)]
    assert respostas[:10] == [400] * 10
    assert respostas[10] == 429
    limiter.reset()
