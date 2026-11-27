"""
Testes dos períodos de fila: nesses dias o site não oferece horário nem aceita
agendamento, e só o dono cadastra o período.

Conexão falsa e fila_dados trocado por monkeypatch: nada de banco.
"""
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

import app
import fila_dados
import rotas_publicas
import rotas_fila


MENSAGEM_DIA_DE_FILA = ("Nesse dia o atendimento é por ordem de chegada. "
                        "Entre na fila pelo site no dia.")


class ConexaoMuda:
    """Conexão que não deveria ser consultada além do necessário."""

    def __init__(self):
        self.executados = []

    def execute(self, sql, params=None):
        self.executados.append(" ".join(sql.split()).lower())

        class _C:
            def fetchall(self_inner):
                return []

            def fetchone(self_inner):
                return None
        return _C()

    def commit(self):
        pass

    def close(self):
        pass


def cabecalho(papel="master", barbeiro_id=None):
    token = jwt.encode(
        {"admin_id": 1, "papel": papel, "barbeiro_id": barbeiro_id,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        app.app.config["SECRET_KEY"], algorithm="HS256")
    return {"Authorization": "Bearer %s" % token}


@pytest.fixture
def cliente():
    return app.app.test_client()


# ---------------------------------------------------------------- horários

def test_horarios_vazio_em_dia_de_fila(cliente, monkeypatch):
    monkeypatch.setattr(app, "get_connection", lambda: ConexaoMuda())
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: True)
    r = cliente.get("/api/horarios-disponiveis?data=2099-12-21&barbeiro_id=1")
    corpo = json.loads(r.data)
    assert r.status_code == 200
    assert corpo["horarios_disponiveis"] == []
    assert corpo["modo_fila"] is True


def test_horarios_normal_fora_do_periodo(cliente, monkeypatch):
    from test_endpoint_horarios import ConexaoEndpoint
    monkeypatch.setattr(app, "horario_efetivo", lambda *a, **k: ("09:00", "20:00"))
    monkeypatch.setattr(app, "get_connection", lambda: ConexaoEndpoint(duracao_servico=30))
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: False)
    r = cliente.get("/api/horarios-disponiveis?data=2099-06-10&barbeiro_id=1&servico_id=1")
    corpo = json.loads(r.data)
    assert corpo["horarios_disponiveis"][0] == "09:00"
    assert "modo_fila" not in corpo


def test_periodo_marca_so_os_dias_de_fila(cliente, monkeypatch):
    from test_horarios_periodo import ConexaoAgenda, congelar
    congelar(monkeypatch, datetime(2099, 12, 20, 7, 0))
    monkeypatch.setattr(app, "get_connection", lambda: ConexaoAgenda())
    monkeypatch.setattr(fila_dados, "dias_de_fila", lambda c, i, f: {"2099-12-21"})
    r = cliente.get("/api/horarios-disponiveis-periodo"
                    "?inicio=2099-12-20&dias=2&barbeiro_id=1&servico_id=1")
    dias = json.loads(r.data)["dias"]
    assert dias["2099-12-21"]["modo_fila"] is True
    assert dias["2099-12-21"]["horarios_disponiveis"] == []
    assert "modo_fila" not in dias["2099-12-20"]


# ---------------------------------------------------------------- agendamento público

def _preparar_criacao(monkeypatch):
    import agendamentos
    import validacoes
    from test_criar_cancelar import ConexaoCriacao, HOJE
    monkeypatch.setattr(agendamentos, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(validacoes, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(agendamentos, "horario_efetivo", lambda *a, **k: ("09:00", "20:00"))
    conn = ConexaoCriacao()
    monkeypatch.setattr(agendamentos, "get_connection", lambda: conn)
    return conn


def test_agendamento_publico_recusado_em_dia_de_fila(cliente, monkeypatch):
    from test_criar_cancelar import DATA
    conn = _preparar_criacao(monkeypatch)
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: d == DATA)
    r = cliente.post("/api/agendamentos", json={
        "nome_cliente": "Ana", "telefone": "11988887777", "servico_id": 1,
        "barbeiro_id": 2, "data": DATA, "hora": "10:00"})
    assert r.status_code == 400
    assert json.loads(r.data)["erro"] == MENSAGEM_DIA_DE_FILA
    assert not conn.inseriu_agendamento()


def test_caderninho_continua_lancando_em_dia_de_fila(cliente, monkeypatch):
    from test_criar_cancelar import DATA
    conn = _preparar_criacao(monkeypatch)
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: True)
    r = cliente.post("/api/admin/agendamentos", headers=cabecalho(), json={
        "nome_cliente": "Ana", "servico_id": 1, "barbeiro_id": 2,
        "data": DATA, "hora": "10:00", "encaixe": True})
    assert r.status_code == 201
    assert conn.inseriu_agendamento()


# ---------------------------------------------------------------- períodos no painel

def test_periodo_so_master(cliente, monkeypatch):
    monkeypatch.setattr(rotas_fila, "get_connection", lambda: ConexaoMuda())
    monkeypatch.setattr(fila_dados, "criar_periodo", lambda c, i, f, a: 7)
    corpo = {"inicio": "2099-12-21", "fim": "2099-12-31"}

    r = cliente.post("/api/admin/periodos-fila", json=corpo,
                     headers=cabecalho("barbeiro", 2))
    assert r.status_code == 403

    r = cliente.post("/api/admin/periodos-fila", json=corpo, headers=cabecalho())
    assert r.status_code == 201
    assert json.loads(r.data) == {"id": 7}


def test_periodo_fim_antes_do_inicio(cliente, monkeypatch):
    monkeypatch.setattr(rotas_fila, "get_connection", lambda: ConexaoMuda())
    monkeypatch.setattr(fila_dados, "criar_periodo", lambda c, i, f, a: 7)
    r = cliente.post("/api/admin/periodos-fila",
                     json={"inicio": "2099-12-31", "fim": "2099-12-21"},
                     headers=cabecalho())
    assert r.status_code == 400


def test_periodo_lista_e_apaga(cliente, monkeypatch):
    monkeypatch.setattr(rotas_fila, "get_connection", lambda: ConexaoMuda())
    monkeypatch.setattr(fila_dados, "listar_periodos",
                        lambda c: [{"id": 7, "inicio": "2099-12-21", "fim": "2099-12-31"}])
    monkeypatch.setattr(fila_dados, "apagar_periodo", lambda c, pid: pid == 7)

    r = cliente.get("/api/admin/periodos-fila", headers=cabecalho())
    assert json.loads(r.data) == [{"id": 7, "inicio": "2099-12-21", "fim": "2099-12-31"}]
    assert cliente.delete("/api/admin/periodos-fila/7", headers=cabecalho()).status_code == 200
    assert cliente.delete("/api/admin/periodos-fila/8", headers=cabecalho()).status_code == 404
