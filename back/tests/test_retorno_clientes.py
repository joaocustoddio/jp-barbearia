"""
Testes de GET /api/admin/clientes/retorno — quem está atrasado para voltar.

A regra não é um prazo fixo: compara os dias parados com o intervalo médio
DAQUELA pessoa. Quem corta a cada 15 dias e sumiu há 30 é um caso; quem corta
a cada 45, não. Medido no dado real, o ciclo mediano da barbearia é 14 dias.

Conexão falsa: nada de banco.
"""
import json
from datetime import date, datetime, timedelta, timezone

import jwt
import pytest

import app
import rotas_admin_gestao

HOJE = date(2026, 10, 7)


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


def pedir(cliente, monkeypatch, linhas, papel="master", barbeiro_id=None):
    conn = Conexao(linhas)
    monkeypatch.setattr(rotas_admin_gestao, "get_connection", lambda: conn)
    monkeypatch.setattr(rotas_admin_gestao, "data_hoje", lambda: HOJE)
    r = cliente.get("/api/admin/clientes/retorno",
                    headers=cabecalho(papel, barbeiro_id))
    return r.status_code, json.loads(r.data), conn


def pessoa(nome, visitas, primeira, ultima, id=1, telefone="11988887766"):
    return {"id": id, "nome": nome, "telefone": telefone, "visitas": visitas,
            "primeira": primeira, "ultima": ultima}


def test_quem_corta_toda_quinzena_e_sumiu_entra(cliente, monkeypatch):
    # 5 visitas em 56 dias = ciclo de 14; parado há 35
    _, corpo, _ = pedir(cliente, monkeypatch,
                        [pessoa("Rafael", 5, "2026-07-08", "2026-09-02")])
    assert len(corpo) == 1
    assert corpo[0]["ciclo_dias"] == 14
    assert corpo[0]["dias_parado"] == 35


def test_quem_acabou_de_vir_nao_entra(cliente, monkeypatch):
    _, corpo, _ = pedir(cliente, monkeypatch,
                        [pessoa("Rafael", 5, "2026-07-30", "2026-10-05")])
    assert corpo == []


def test_ciclo_longo_nao_vira_atraso_cedo_demais(cliente, monkeypatch):
    """Quem corta a cada 40 dias e está há 30 parado NÃO está atrasado —
    com prazo fixo de 21 ou 30 dias ele apareceria na lista errado."""
    # 3 visitas em 80 dias = ciclo de 40; parado há 30
    _, corpo, _ = pedir(cliente, monkeypatch,
                        [pessoa("Jorge", 3, "2026-06-19", "2026-09-07")])
    assert corpo == []


def test_piso_de_21_dias(cliente, monkeypatch):
    """Quem corta toda semana não pode aparecer por ter faltado 11 dias:
    1,5x de um ciclo de 7 daria 11, e a lista viraria ruído."""
    # 5 visitas em 28 dias = ciclo 7; parado há 14
    _, corpo, _ = pedir(cliente, monkeypatch,
                        [pessoa("Ana", 5, "2026-08-25", "2026-09-23")])
    assert corpo == []


def test_mais_atrasado_vem_primeiro(cliente, monkeypatch):
    linhas = [
        # os dois cortam a cada 14 dias; um parou ha 27 dias, o outro ha 55
        pessoa("Pouco atrasado", 3, "2026-08-13", "2026-09-10", id=1),
        pessoa("Muito atrasado", 3, "2026-07-16", "2026-08-13", id=2),
    ]
    _, corpo, _ = pedir(cliente, monkeypatch, linhas)
    assert [c["nome"] for c in corpo] == ["Muito atrasado", "Pouco atrasado"]


def test_so_quem_ja_voltou_entra_na_conta(cliente, monkeypatch):
    """Uma visita só não diz se a pessoa é cliente ou passou ali uma vez.
    O corte é feito na consulta, não depois."""
    _, _, conn = pedir(cliente, monkeypatch, [])
    sql = conn.executados[0][0]
    assert "having count(distinct agendamentos.data) >= 2" in sql


def test_telefone_generico_fica_de_fora(cliente, monkeypatch):
    """No número da barbearia moram várias pessoas — o 'hábito' dali não é
    de ninguém, e chamar de volta não faria sentido."""
    _, _, conn = pedir(cliente, monkeypatch, [])
    sql = conn.executados[0][0]
    assert "not in (select telefone from telefones_genericos)" in sql


def test_cancelado_nao_conta_como_visita(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch, [])
    assert "status != 'cancelado'" in conn.executados[0][0]


def test_barbeiro_so_ve_os_proprios_clientes(cliente, monkeypatch):
    """A lista é de quem chamar de volta. Não pode virar a carteira de
    clientes do colega."""
    _, _, conn = pedir(cliente, monkeypatch, [], papel="barbeiro", barbeiro_id=3)
    sql, params = conn.executados[0]
    assert "agendamentos.barbeiro_id = %s" in sql
    assert params == [3]


def test_master_ve_de_todos(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch, [])
    sql, params = conn.executados[0]
    assert "agendamentos.barbeiro_id = %s" not in sql
    assert params == []


def test_sem_token_bloqueia(cliente):
    assert cliente.get("/api/admin/clientes/retorno").status_code == 401
