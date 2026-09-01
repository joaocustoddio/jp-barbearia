"""
Testes dos bloqueios de agenda:

  GET/POST  /api/admin/bloqueios
  DELETE    /api/admin/bloqueios/<id>

O que importa aqui:

- "saio às 14:00, volto às 15:30" precisa virar UMA linha com duracao_min=90.
  Se virasse várias linhas de 30 em 30, remover depois seria um clique por
  linha — e é exatamente isso que a tela promete não fazer;
- barbeiro bloqueia só a agenda DELE (nunca a de outro, nunca o dia inteiro
  da barbearia);
- o almoço mora na mesma tabela, mas não aparece nesta lista: quem manda nele
  é a aba Almoço.

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
    """Finge o banco e anota tudo que foi executado."""

    def __init__(self, existente=None, bloqueios=()):
        self.existente = existente      # linha devolvida nos SELECTs de conferência
        self.bloqueios = bloqueios
        self.executados = []
        self.commits = 0

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if consulta.startswith("select b.*"):
            return _Cursor(self.bloqueios)
        if consulta.startswith("select id from bloqueios"):
            return _Cursor([self.existente] if self.existente else [])
        return _Cursor([])

    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def inserts(self):
        return [p for sql, p in self.executados if sql.startswith("insert into bloqueios")]

    def deletes(self):
        return [p for sql, p in self.executados if sql.startswith("delete from bloqueios")]


def cabecalho(papel="master", barbeiro_id=None):
    token = jwt.encode(
        {
            "admin_id": 1,
            "papel": papel,
            "barbeiro_id": barbeiro_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        app.app.config["SECRET_KEY"],
        algorithm="HS256",
    )
    return {"Authorization": "Bearer %s" % token}


@pytest.fixture
def cliente():
    return app.app.test_client()


def ligar(monkeypatch, conexao):
    monkeypatch.setattr(rotas_admin_agenda, "get_connection", lambda: conexao)
    return conexao


BARBEIRO = {"papel": "barbeiro", "barbeiro_id": 3}


# ------------------------------------------------------------------ intervalo

def test_saida_e_volta_viram_uma_linha_com_duracao(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00",
                                  "volta": "15:30", "motivo": "Médico"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 201
    # data, hora, motivo, barbeiro_id, duracao_min — 90 min, não 3 linhas
    assert conn.inserts() == [("2026-09-10", "14:00", "Médico", 3, 90)]
    assert conn.commits == 1
    assert "15:30" in json.loads(resposta.data)["mensagem"]


def test_sem_volta_bloqueia_so_aquele_horario(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 201
    assert conn.inserts()[0][4] is None      # sem duracao_min


def test_volta_antes_da_saida_e_recusada(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "15:30", "volta": "14:00"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 400
    assert conn.inserts() == []
    assert conn.commits == 0


def test_volta_igual_a_saida_e_recusada(cliente, monkeypatch):
    """Duração zero não bloqueia nada, mas deixaria uma linha inútil na lista."""
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00", "volta": "14:00"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 400
    assert conn.inserts() == []


def test_volta_sem_saida_e_recusada(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "volta": "15:30"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 400
    assert conn.inserts() == []


# --------------------------------------------------------------------- escopo

def test_barbeiro_bloqueia_a_propria_agenda_mesmo_pedindo_outra(cliente, monkeypatch):
    """barbeiro_id do corpo é ignorado: o do token manda."""
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00", "barbeiro_id": 99},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 201
    assert conn.inserts()[0][3] == 3


def test_barbeiro_nao_fecha_o_dia_inteiro(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 403
    assert conn.inserts() == []


def test_master_sem_barbeiro_fecha_a_barbearia_toda(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-12-25", "motivo": "Natal"},
                            headers=cabecalho())
    assert resposta.status_code == 201
    assert conn.inserts() == [("2026-12-25", None, "Natal", None, None)]


def test_master_bloqueia_a_agenda_de_um_barbeiro(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "09:00", "barbeiro_id": 5},
                            headers=cabecalho())
    assert resposta.status_code == 201
    assert conn.inserts()[0][3] == 5


def test_salao_nao_cria_bloqueio(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao())
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00"},
                            headers=cabecalho(papel="salao"))
    assert resposta.status_code == 403
    assert conn.inserts() == []


def test_bloqueio_repetido_da_409(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao(existente={"id": 1}))
    resposta = cliente.post("/api/admin/bloqueios",
                            json={"data": "2026-09-10", "hora": "14:00"},
                            headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 409
    assert conn.inserts() == []


# ---------------------------------------------------------------------- lista

def test_lista_do_barbeiro_filtra_pelo_dele_e_esconde_almoco(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao(bloqueios=[]))
    resposta = cliente.get("/api/admin/bloqueios", headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 200
    consulta, params = conn.executados[0]
    assert "b.barbeiro_id = %s" in consulta
    assert params == [3]
    assert "motivo is distinct from 'almoço'" in consulta


def test_lista_do_master_nao_filtra_barbeiro(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao(bloqueios=[]))
    resposta = cliente.get("/api/admin/bloqueios", headers=cabecalho())
    assert resposta.status_code == 200
    consulta, params = conn.executados[0]
    assert "b.barbeiro_id = %s" not in consulta
    assert params == []


# -------------------------------------------------------------------- remover

def test_barbeiro_remove_o_proprio_bloqueio(cliente, monkeypatch):
    conn = ligar(monkeypatch, Conexao(existente={"id": 8}))
    resposta = cliente.delete("/api/admin/bloqueios/8", headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 200
    assert conn.deletes() == [(8,)]
    # a conferência de dono precisa ter acontecido de fato
    assert any("barbeiro_id = %s" in sql for sql, _ in conn.executados)


def test_barbeiro_nao_remove_bloqueio_de_outro(cliente, monkeypatch):
    """Existe, mas não é dele: 404 (não confirma nem que existe) e nada apagado."""
    class SoOPrimeiro(Conexao):
        def execute(self, sql, params=None):
            consulta = " ".join(sql.split()).lower()
            self.executados.append((consulta, params))
            if consulta.startswith("select id from bloqueios where id = %s and barbeiro_id"):
                return _Cursor([])            # não é dele
            if consulta.startswith("select id from bloqueios"):
                return _Cursor([{"id": 8}])   # existe
            return _Cursor([])

    conn = ligar(monkeypatch, SoOPrimeiro())
    resposta = cliente.delete("/api/admin/bloqueios/8", headers=cabecalho(**BARBEIRO))
    assert resposta.status_code == 404
    assert conn.deletes() == []
    assert conn.commits == 0
