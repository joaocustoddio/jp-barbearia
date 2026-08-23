"""
Testes da rota GET /api/admin/relatorio — o financeiro por período.

Dois pontos que valem teste:

- o faturamento total é serviços MAIS produtos, e os dois vêm discriminados;
- o período (dia/semana/mês) vira uma faixa de datas que entra na consulta —
  se essa faixa sair errada, o número do painel fica errado sem ninguém notar.

Também cobre o salão, que não pode ver valor nenhum aqui (403).
"""
import json
from datetime import date, datetime, timedelta, timezone

import jwt
import pytest

import app
import relatorios


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class ConexaoRelatorio:
    """Responde às cinco consultas do relatório e anota os parâmetros recebidos."""

    def __init__(self, faturamento=0, total=0, por_dia=(),
                 produtos_centavos=0, produtos_qtd=0, servicos=()):
        self.faturamento = faturamento
        self.total = total
        self.por_dia = por_dia
        self.produtos_centavos = produtos_centavos
        self.produtos_qtd = produtos_qtd
        self.servicos = servicos
        self.executados = []

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if "as centavos" in consulta:
            return _Cursor([{"centavos": self.produtos_centavos,
                             "quantidade": self.produtos_qtd}])
        if "group by agendamentos.data" in consulta:
            return _Cursor(self.por_dia)
        if "group by servicos.id" in consulta:
            return _Cursor(self.servicos)
        if "count(*) as total" in consulta:
            return _Cursor([{"total": self.total}])
        if "sum(servicos.preco)" in consulta:
            return _Cursor([{"total": self.faturamento}])
        raise AssertionError("consulta inesperada: %s" % consulta)

    def close(self):
        pass


def token(papel="master", barbeiro_id=None):
    return jwt.encode(
        {
            "admin_id": 1,
            "papel": papel,
            "barbeiro_id": barbeiro_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        app.app.config["SECRET_KEY"],
        algorithm="HS256",
    )


@pytest.fixture
def cliente():
    return app.app.test_client()


def pedir(cliente, monkeypatch, periodo=None, papel="master", barbeiro_id=None,
          hoje=date(2026, 8, 20), **conexao):
    conn = ConexaoRelatorio(**conexao)
    monkeypatch.setattr(relatorios, "get_connection", lambda: conn)
    monkeypatch.setattr(relatorios, "data_hoje", lambda: hoje)
    url = "/api/admin/relatorio" + ("?periodo=%s" % periodo if periodo else "")
    resposta = cliente.get(url, headers={"Authorization": "Bearer %s" % token(papel, barbeiro_id)})
    corpo = json.loads(resposta.data) if resposta.data else {}
    return resposta.status_code, corpo, conn


# ------------------------------------------------------------------- somatório

def test_faturamento_soma_servicos_e_produtos(cliente, monkeypatch):
    status, corpo, _ = pedir(cliente, monkeypatch, faturamento=200,
                             produtos_centavos=4500, produtos_qtd=3, total=5)
    assert status == 200
    assert corpo["faturamento_servicos"] == 200.0
    assert corpo["faturamento_produtos"] == 45.0
    assert corpo["faturamento_total"] == 245.0
    assert corpo["produtos_qtd"] == 3
    assert corpo["total_agendamentos"] == 5


def test_periodo_sem_movimento_zera_sem_quebrar(cliente, monkeypatch):
    status, corpo, _ = pedir(cliente, monkeypatch)
    assert status == 200
    assert corpo["faturamento_total"] == 0
    assert corpo["faturamento_produtos"] == 0
    assert corpo["por_dia"] == []
    assert corpo["servicos_mais_realizados"] == []


def test_ranking_de_servicos_vem_na_resposta(cliente, monkeypatch):
    _, corpo, _ = pedir(
        cliente, monkeypatch,
        servicos=[{"nome": "Degradê", "quantidade": 8, "faturamento": 320},
                  {"nome": "Barba", "quantidade": 2, "faturamento": 40}],
    )
    assert corpo["servicos_mais_realizados"][0]["nome"] == "Degradê"
    assert corpo["servicos_mais_realizados"][0]["quantidade"] == 8


# --------------------------------------------------------------------- período

def test_periodo_dia_usa_hoje_nas_duas_pontas(cliente, monkeypatch):
    _, corpo, conn = pedir(cliente, monkeypatch, periodo="dia")
    assert corpo["data_inicio"] == "2026-08-20"
    assert corpo["data_fim"] == "2026-08-20"
    assert all(p[:2] == ["2026-08-20", "2026-08-20"] for _, p in conn.executados)


def test_periodo_semana_comeca_na_segunda(cliente, monkeypatch):
    """20/08/2026 é quinta; a semana tem que começar na segunda, dia 17."""
    _, corpo, _ = pedir(cliente, monkeypatch, periodo="semana",
                        hoje=date(2026, 8, 20))
    assert corpo["data_inicio"] == "2026-08-17"
    assert corpo["data_fim"] == "2026-08-20"


def test_periodo_semana_quando_hoje_e_segunda(cliente, monkeypatch):
    """Segunda-feira: início e fim são o mesmo dia, não a semana anterior."""
    _, corpo, _ = pedir(cliente, monkeypatch, periodo="semana",
                        hoje=date(2026, 8, 17))
    assert corpo["data_inicio"] == "2026-08-17"


def test_periodo_mes_comeca_no_dia_um(cliente, monkeypatch):
    _, corpo, _ = pedir(cliente, monkeypatch, periodo="mes")
    assert corpo["data_inicio"] == "2026-08-01"
    assert corpo["data_fim"] == "2026-08-20"


def test_periodo_desconhecido_cai_no_dia(cliente, monkeypatch):
    _, corpo, _ = pedir(cliente, monkeypatch, periodo="decada")
    assert corpo["data_inicio"] == corpo["data_fim"] == "2026-08-20"


# ------------------------------------------------------------- quem vê o quê

def test_salao_nao_ve_o_financeiro(cliente, monkeypatch):
    status, corpo, conn = pedir(cliente, monkeypatch, papel="salao")
    assert status == 403
    assert conn.executados == []          # nem consultou o banco


def test_barbeiro_so_ve_os_numeros_dele(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch, papel="barbeiro", barbeiro_id=3)
    assert all("agendamentos.barbeiro_id = %s" in sql for sql, _ in conn.executados)
    assert all(p[-1] == 3 for _, p in conn.executados)


def test_master_ve_de_todos(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch, papel="master")
    assert all("agendamentos.barbeiro_id = %s" not in sql for sql, _ in conn.executados)
    assert all(len(p) == 2 for _, p in conn.executados)


def test_relatorio_sem_token_bloqueia(cliente):
    assert cliente.get("/api/admin/relatorio").status_code == 401
