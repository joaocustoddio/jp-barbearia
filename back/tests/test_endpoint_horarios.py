"""
Teste de fiação do endpoint GET /api/horarios-disponiveis.

Sobe o Flask de verdade (test client) e troca só a conexão de banco por uma
falsa. Assim confere o caminho completo — parâmetros, expediente, janelas
ocupadas, geração dos slots e o JSON de resposta — sem precisar de Postgres.
"""
import json

import pytest

import app
from test_janelas_ocupadas import ConexaoFalsa, _Cursor


class ConexaoEndpoint(ConexaoFalsa):
    """ConexaoFalsa + as consultas extras que o endpoint faz (serviço/expediente)."""

    def __init__(self, duracao_servico=30, expediente=None, **kwargs):
        super().__init__(**kwargs)
        self.duracao_servico = duracao_servico
        self.expediente = expediente          # None = usa o horário padrão do dia

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        if "from servicos" in consulta:
            return _Cursor([{"duracao_min": self.duracao_servico}])
        if "from expedientes" in consulta:
            return _Cursor([self.expediente] if self.expediente else [])
        return super().execute(sql, params)

    def close(self):
        pass


@pytest.fixture
def cliente(monkeypatch):
    """Test client com expediente fixo 09:00–20:00 (independe do dia/config)."""
    monkeypatch.setattr(app, "horario_efetivo", lambda *a, **k: ("09:00", "20:00"))
    return app.app.test_client()


def responder_com(monkeypatch, **kwargs):
    monkeypatch.setattr(app, "get_connection", lambda: ConexaoEndpoint(**kwargs))


def buscar(cliente, servico_id=1, data="2099-06-10", barbeiro_id=1):
    resposta = cliente.get("/api/horarios-disponiveis"
                           f"?data={data}&barbeiro_id={barbeiro_id}&servico_id={servico_id}")
    return resposta.status_code, json.loads(resposta.data)


def test_dia_livre_lista_do_inicio_ao_fim(cliente, monkeypatch):
    responder_com(monkeypatch, duracao_servico=30)
    status, corpo = buscar(cliente)
    assert status == 200
    assert corpo["horarios_disponiveis"][0] == "09:00"
    assert corpo["horarios_disponiveis"][-1] == "19:30"


def test_almoco_fixo_some_da_lista(cliente, monkeypatch):
    responder_com(monkeypatch, duracao_servico=30, almoco_fixo="12:30")
    _, corpo = buscar(cliente)
    horarios = corpo["horarios_disponiveis"]
    assert "12:00" in horarios       # 12:00–12:30 encosta no almoço, tudo bem
    assert "12:30" not in horarios
    assert "13:00" not in horarios   # ainda dentro do almoço
    assert "13:30" in horarios       # liberou


def test_agendamento_existente_some_da_lista(cliente, monkeypatch):
    responder_com(monkeypatch, duracao_servico=30,
                  agendamentos=[{"hora": "10:00", "duracao_min": 40}])
    _, corpo = buscar(cliente)
    horarios = corpo["horarios_disponiveis"]
    assert "10:00" not in horarios
    assert "10:40" in horarios       # encaixa logo na sequência (packing)


def test_bloqueio_de_dia_inteiro_zera_a_agenda(cliente, monkeypatch):
    responder_com(monkeypatch,
                  bloqueios=[{"hora": None, "duracao_min": None, "motivo": "Feriado"}])
    status, corpo = buscar(cliente)
    assert status == 200
    assert corpo["horarios_disponiveis"] == []
    assert corpo["bloqueio_dia"] is True
    assert corpo["barbeiro_id"]      # a resposta continua identificando o barbeiro


def test_data_invalida_e_barbeiro_ausente(cliente, monkeypatch):
    responder_com(monkeypatch)
    assert cliente.get("/api/horarios-disponiveis?data=xx&barbeiro_id=1").status_code == 400
    assert cliente.get("/api/horarios-disponiveis?data=2099-06-10").status_code == 400
