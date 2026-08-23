"""
Testes da rota GET /api/admin/contagem — o fechamento do dia.

É a conta que precisa bater com o caixa, então o que se testa aqui é a
aritmética: comissão do barbeiro, o que sobra pra barbearia, e produtos, que
entram no total mas NÃO comissionam.

Também cobre quem enxerga o quê: o tablet do salão vê quantidade de cortes mas
não vê valor, e barbeiro comum só vê a linha dele.

Conexão falsa, como nos outros testes — não precisa de banco.
"""
import json
from datetime import datetime, timedelta, timezone

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


class ConexaoContagem:
    """Responde às três consultas da contagem, reconhecidas pelo apelido da coluna."""

    def __init__(self, linhas=(), detalhe=(), produtos=()):
        self.linhas = linhas
        self.detalhe = detalhe
        self.produtos = produtos

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        if "as produtos_qtd" in consulta:
            return _Cursor(self.produtos)
        if "as servico_nome" in consulta:
            return _Cursor(self.detalhe)
        if "as clientes" in consulta:
            return _Cursor(self.linhas)
        raise AssertionError("consulta inesperada: %s" % consulta)

    def close(self):
        pass


def token(papel="master", barbeiro_id=None):
    """Gera um JWT válido pro papel pedido (mesma forma do /api/admin/login)."""
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


def linha(barbeiro_id=1, nome="JP", pct=60, clientes=0, total=0):
    return {"barbeiro_id": barbeiro_id, "barbeiro_nome": nome,
            "comissao_pct": pct, "clientes": clientes, "total": total}


@pytest.fixture
def cliente():
    return app.app.test_client()


def contar(cliente, monkeypatch, papel="master", barbeiro_id=None,
           data="2026-08-20", **conexao):
    monkeypatch.setattr(relatorios, "get_connection", lambda: ConexaoContagem(**conexao))
    resposta = cliente.get(
        "/api/admin/contagem?data=%s" % data,
        headers={"Authorization": "Bearer %s" % token(papel, barbeiro_id)},
    )
    return resposta.status_code, json.loads(resposta.data)


# ------------------------------------------------------------------ a conta

def test_comissao_divide_entre_barbeiro_e_barbearia(cliente, monkeypatch):
    status, corpo = contar(cliente, monkeypatch,
                           linhas=[linha(pct=60, clientes=3, total=100)])
    assert status == 200
    b = corpo["barbeiros"][0]
    assert b["total"] == 100
    assert b["barbeiro_recebe"] == 60
    assert b["barbearia_recebe"] == 40


def test_dono_com_comissao_zero_deixa_tudo_pra_barbearia(cliente, monkeypatch):
    """Regra de negócio do README: o corte do dono não vira repasse."""
    _, corpo = contar(cliente, monkeypatch,
                      linhas=[linha(nome="JP", pct=0, clientes=2, total=80)])
    b = corpo["barbeiros"][0]
    assert b["barbeiro_recebe"] == 0
    assert b["barbearia_recebe"] == 80


def test_produto_entra_no_total_mas_nao_comissiona(cliente, monkeypatch):
    """
    Produto é dinheiro que entrou (total_geral), mas não muda o repasse —
    o barbeiro continua recebendo só o percentual sobre os serviços.
    """
    _, corpo = contar(
        cliente, monkeypatch,
        linhas=[linha(pct=50, clientes=1, total=100)],
        produtos=[{"barbeiro_id": 1, "produtos_qtd": 2, "produtos_centavos": 3000}],
    )
    b = corpo["barbeiros"][0]
    assert b["produtos"] == 30.0          # 3000 centavos
    assert b["produtos_qtd"] == 2
    assert b["barbeiro_recebe"] == 50     # 50% de 100, o produto ficou de fora
    assert b["barbearia_recebe"] == 50
    assert b["total_geral"] == 130.0      # serviços + produtos


def test_totais_somam_todos_os_barbeiros(cliente, monkeypatch):
    _, corpo = contar(
        cliente, monkeypatch,
        linhas=[linha(1, "JP", pct=0, clientes=2, total=100),
                linha(2, "Rian", pct=60, clientes=1, total=50)],
        produtos=[{"barbeiro_id": 2, "produtos_qtd": 1, "produtos_centavos": 1000}],
    )
    t = corpo["totais"]
    assert t["clientes"] == 3
    assert t["total"] == 150
    assert t["barbeiro_recebe"] == 30     # 0 do JP + 60% de 50
    assert t["barbearia_recebe"] == 120
    assert t["produtos"] == 10.0
    assert t["total_geral"] == 160.0


def test_dia_sem_movimento_nao_quebra(cliente, monkeypatch):
    _, corpo = contar(cliente, monkeypatch,
                      linhas=[linha(clientes=0, total=0)])
    b = corpo["barbeiros"][0]
    assert (b["clientes"], b["total"], b["total_geral"]) == (0, 0, 0)
    assert corpo["totais"]["clientes"] == 0


def test_detalhe_lista_servicos_do_barbeiro(cliente, monkeypatch):
    _, corpo = contar(
        cliente, monkeypatch,
        linhas=[linha(clientes=4, total=155)],
        detalhe=[{"barbeiro_id": 1, "servico_nome": "Degradê", "quantidade": 3, "valor": 120},
                 {"barbeiro_id": 1, "servico_nome": "Corte Social", "quantidade": 1, "valor": 35}],
    )
    servicos = corpo["barbeiros"][0]["servicos"]
    assert servicos[0] == {"nome": "Degradê", "quantidade": 3, "valor": 120.0}
    assert servicos[1]["nome"] == "Corte Social"


# ------------------------------------------------------------- quem vê o quê

def test_salao_ve_quantidade_mas_nao_ve_valor(cliente, monkeypatch):
    _, corpo = contar(
        cliente, monkeypatch, papel="salao",
        linhas=[linha(clientes=3, total=100)],
        detalhe=[{"barbeiro_id": 1, "servico_nome": "Degradê", "quantidade": 3, "valor": 120}],
    )
    assert corpo["ver_valores"] is False
    b = corpo["barbeiros"][0]
    assert b["clientes"] == 3
    for campo in ("total", "barbeiro_recebe", "barbearia_recebe", "total_geral"):
        assert campo not in b
    # no detalhe também some o valor, sobra só o que foi feito
    assert b["servicos"][0] == {"nome": "Degradê", "quantidade": 3}
    assert "total" not in corpo["totais"]


def test_master_ve_valores(cliente, monkeypatch):
    _, corpo = contar(cliente, monkeypatch, papel="master",
                      linhas=[linha(clientes=1, total=40)])
    assert corpo["ver_valores"] is True
    assert corpo["barbeiros"][0]["total"] == 40


def test_barbeiro_ve_valores_da_linha_dele(cliente, monkeypatch):
    _, corpo = contar(cliente, monkeypatch, papel="barbeiro", barbeiro_id=2,
                      linhas=[linha(2, "Rian", pct=60, clientes=1, total=50)])
    assert corpo["ver_valores"] is True
    assert corpo["barbeiros"][0]["barbeiro_recebe"] == 30


def test_escopo_do_barbeiro_filtra_no_sql(cliente, monkeypatch):
    """
    Barbeiro comum não pode ver a contagem dos outros: a consulta tem que sair
    com o WHERE por barbeiro e receber o id dele como parâmetro.
    """
    vistas = []

    class Espia(ConexaoContagem):
        def execute(self, sql, params=None):
            vistas.append((" ".join(sql.split()).lower(), params))
            return super().execute(sql, params)

    monkeypatch.setattr(relatorios, "get_connection",
                        lambda: Espia(linhas=[linha(2, "Rian", clientes=1, total=50)]))
    resposta = cliente.get(
        "/api/admin/contagem?data=2026-08-20",
        headers={"Authorization": "Bearer %s" % token("barbeiro", barbeiro_id=2)},
    )
    assert resposta.status_code == 200
    assert all("where barbeiros.id = %s" in sql for sql, _ in vistas)
    assert all(p == ["2026-08-20", 2] for _, p in vistas)


def test_master_nao_filtra_por_barbeiro(cliente, monkeypatch):
    vistas = []

    class Espia(ConexaoContagem):
        def execute(self, sql, params=None):
            vistas.append((" ".join(sql.split()).lower(), params))
            return super().execute(sql, params)

    monkeypatch.setattr(relatorios, "get_connection",
                        lambda: Espia(linhas=[linha(clientes=1, total=40)]))
    cliente.get("/api/admin/contagem?data=2026-08-20",
                headers={"Authorization": "Bearer %s" % token("master")})
    assert all("where barbeiros.id" not in sql for sql, _ in vistas)
    assert all(p == ["2026-08-20"] for _, p in vistas)


# ------------------------------------------------------------------ entradas

def test_sem_token_bloqueia(cliente):
    assert cliente.get("/api/admin/contagem").status_code == 401


def test_token_invalido_bloqueia(cliente):
    resposta = cliente.get("/api/admin/contagem",
                           headers={"Authorization": "Bearer nao-e-um-token"})
    assert resposta.status_code == 401


def test_data_invalida_da_400(cliente, monkeypatch):
    status, corpo = contar(cliente, monkeypatch, data="20-08-2026",
                           linhas=[linha()])
    assert status == 400
    assert "data" in corpo["erro"].lower()


def test_sem_data_usa_hoje(cliente, monkeypatch):
    """Sem ?data, o fechamento é o de hoje — não pode cair em erro."""
    monkeypatch.setattr(relatorios, "data_hoje",
                        lambda: datetime(2026, 8, 20).date())
    monkeypatch.setattr(relatorios, "get_connection",
                        lambda: ConexaoContagem(linhas=[linha(clientes=1, total=40)]))
    resposta = cliente.get("/api/admin/contagem",
                           headers={"Authorization": "Bearer %s" % token()})
    assert resposta.status_code == 200
    assert json.loads(resposta.data)["data"] == "2026-08-20"
