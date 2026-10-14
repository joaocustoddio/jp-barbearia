"""
Testes de GET /api/admin/bloqueios-dia — as travas que a agenda desenha.

O ponto delicado é a trava da barbearia inteira (barbeiro_id nulo): ela fecha a
agenda de todo mundo, então precisa aparecer pro barbeiro também. Se não
aparecesse, o horário sumiria do site e continuaria com cara de livre no
painel — o bug clássico de "vejo mas não consigo marcar", ao contrário.

Conexão falsa: nada de banco.
"""
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

import app
import rotas_admin_agenda


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class Conexao:
    def __init__(self, linhas=()):
        self.linhas = linhas
        self.executados = []

    def execute(self, sql, params=None):
        self.executados.append((" ".join(sql.split()).lower(), params))
        return _Cursor(self.linhas)

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


def pedir(cliente, monkeypatch, linhas=(), papel="master", barbeiro_id=None,
          data="2026-10-14"):
    conn = Conexao(linhas)
    monkeypatch.setattr(rotas_admin_agenda, "get_connection", lambda: conn)
    url = "/api/admin/bloqueios-dia" + (f"?data={data}" if data else "")
    r = cliente.get(url, headers=cabecalho(papel, barbeiro_id))
    return r.status_code, (json.loads(r.data) if r.data else None), conn


def trava(hora="14:00", duracao=90, motivo="Médico", barbeiro_id=2, id=1):
    return {"id": id, "barbeiro_id": barbeiro_id, "hora": hora,
            "duracao_min": duracao, "motivo": motivo}


def test_devolve_a_trava_do_dia(cliente, monkeypatch):
    _, corpo, _ = pedir(cliente, monkeypatch, [trava()])
    assert corpo == [{"id": 1, "barbeiro_id": 2, "hora": "14:00",
                      "duracao_min": 90, "motivo": "Médico"}]


def test_hora_vem_sem_os_segundos(cliente, monkeypatch):
    """O Postgres devolve 14:00:00 e o card desenharia '14:00:00–15:30'."""
    _, corpo, _ = pedir(cliente, monkeypatch, [trava(hora="14:00:00")])
    assert corpo[0]["hora"] == "14:00"


def test_sem_duracao_usa_o_intervalo_padrao(cliente, monkeypatch):
    """Mesmo padrão que a regra de disponibilidade aplica — se divergir, o
    bloco desenhado não bate com o horário que o site recusa."""
    _, corpo, _ = pedir(cliente, monkeypatch, [trava(duracao=None)])
    assert corpo[0]["duracao_min"] == rotas_admin_agenda.INTERVALO_MINUTOS


def test_motivo_vazio_vira_nulo(cliente, monkeypatch):
    """A tela usa o motivo quando existe e cai no texto padrão quando não."""
    _, corpo, _ = pedir(cliente, monkeypatch, [trava(motivo="   ")])
    assert corpo[0]["motivo"] is None


def test_almoco_nao_entra(cliente, monkeypatch):
    """Já vem pela rota de almoços; entrando aqui apareceria duas vezes."""
    _, _, conn = pedir(cliente, monkeypatch)
    assert "motivo is distinct from 'almoço'" in conn.executados[0][0]


def test_dia_inteiro_nao_entra(cliente, monkeypatch):
    """Trava sem hora não tem onde ser desenhada numa linha do tempo."""
    _, _, conn = pedir(cliente, monkeypatch)
    assert "hora is not null" in conn.executados[0][0]


def test_barbeiro_ve_a_dele_e_a_da_barbearia(cliente, monkeypatch):
    """A trava geral fecha a agenda dele também — precisa aparecer."""
    _, _, conn = pedir(cliente, monkeypatch, papel="barbeiro", barbeiro_id=3)
    sql, params = conn.executados[0]
    assert "(barbeiro_id is null or barbeiro_id = %s)" in sql
    assert params == ["2026-10-14", 3]


def test_master_ve_de_todos(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch)
    sql, params = conn.executados[0]
    assert "barbeiro_id = %s" not in sql
    assert params == ["2026-10-14"]


def test_data_e_obrigatoria(cliente, monkeypatch):
    status, _, conn = pedir(cliente, monkeypatch, data=None)
    assert status == 400
    assert conn.executados == []


def test_sem_token_bloqueia(cliente):
    assert cliente.get("/api/admin/bloqueios-dia?data=2026-10-14").status_code == 401
