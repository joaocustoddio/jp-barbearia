"""
Testes das rotas de dinheiro por atendimento:

  PATCH /api/admin/agendamentos/<id>/pagamento   forma de pagamento
  GET   /api/admin/agendamentos/<id>/consumos    adicionais lançados
  PUT   /api/admin/agendamentos/<id>/consumos    substitui os adicionais

O que mais importa aqui:

- o PUT de consumos grava o preço COMO ESTÁ HOJE (snapshot). Mudar o preço do
  produto depois não pode alterar um fechamento já feito — é regra de negócio
  documentada no README;
- barbeiro comum não mexe no atendimento de outro barbeiro (escopo).

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


class ConexaoAgendamento:
    """
    Finge o banco e ANOTA todo comando executado, pra dar pra afirmar o que foi
    gravado (o teste não tem banco pra reler depois).
    """

    def __init__(self, agendamento=None, consumos=(), produtos=()):
        # agendamento=None simula "não existe"
        self.agendamento = agendamento
        self.consumos = consumos
        self.produtos = produtos
        self.executados = []      # [(sql_normalizado, params)]
        self.commits = 0

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if "from agendamentos where id" in consulta:
            return _Cursor([self.agendamento] if self.agendamento else [])
        if "from produtos" in consulta:
            return _Cursor(self.produtos)
        if "from consumos" in consulta:
            return _Cursor(self.consumos)
        return _Cursor([])

    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    # ------- ajudantes de leitura pros testes
    def inserts_de_consumo(self):
        return [p for sql, p in self.executados if sql.startswith("insert into consumos")]

    def houve_delete_de_consumo(self):
        return any(sql.startswith("delete from consumos") for sql, _ in self.executados)


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


def cabecalho(papel="master", barbeiro_id=None):
    return {"Authorization": "Bearer %s" % token(papel, barbeiro_id)}


@pytest.fixture
def cliente():
    return app.app.test_client()


def ligar(monkeypatch, conexao):
    monkeypatch.setattr(rotas_admin_agenda, "get_connection", lambda: conexao)
    return conexao


AGENDAMENTO = {"id": 7, "barbeiro_id": 2}


# ------------------------------------------------------------------ pagamento

@pytest.mark.parametrize("forma", ["cartao", "pix", "dinheiro"])
def test_registra_cada_forma_valida(cliente, monkeypatch, forma):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": forma}, headers=cabecalho())
    assert resposta.status_code == 200
    assert json.loads(resposta.data)["forma_pagamento"] == forma
    updates = [p for sql, p in conn.executados if sql.startswith("update agendamentos")]
    assert updates == [(forma, 7)]
    assert conn.commits == 1


def test_forma_invalida_da_400_e_nao_grava(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": "bitcoin"}, headers=cabecalho())
    assert resposta.status_code == 400
    assert conn.executados == []      # nem chegou a abrir consulta
    assert conn.commits == 0


def test_forma_vazia_limpa_o_registro(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": ""}, headers=cabecalho())
    assert resposta.status_code == 200
    assert json.loads(resposta.data)["forma_pagamento"] is None
    updates = [p for sql, p in conn.executados if sql.startswith("update agendamentos")]
    assert updates == [(None, 7)]


def test_maiuscula_e_espaco_sao_normalizados(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": "  PIX  "}, headers=cabecalho())
    assert resposta.status_code == 200
    updates = [p for sql, p in conn.executados if sql.startswith("update agendamentos")]
    assert updates == [("pix", 7)]


def test_pagamento_de_agendamento_inexistente_da_404(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=None))
    resposta = cliente.patch("/api/admin/agendamentos/999/pagamento",
                             json={"forma": "pix"}, headers=cabecalho())
    assert resposta.status_code == 404
    assert conn.commits == 0


def test_pagamento_sem_token_bloqueia(cliente):
    assert cliente.patch("/api/admin/agendamentos/7/pagamento",
                         json={"forma": "pix"}).status_code == 401


# --------------------------------------------------------------------- escopo

def test_barbeiro_nao_mexe_no_atendimento_de_outro(cliente, monkeypatch):
    """Atendimento é do barbeiro 2; quem tenta é o barbeiro 3 → 404, sem gravar."""
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": "pix"},
                             headers=cabecalho("barbeiro", barbeiro_id=3))
    assert resposta.status_code == 404
    assert conn.commits == 0
    assert not [p for sql, p in conn.executados if sql.startswith("update")]


def test_barbeiro_mexe_no_proprio_atendimento(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": "pix"},
                             headers=cabecalho("barbeiro", barbeiro_id=2))
    assert resposta.status_code == 200
    assert conn.commits == 1


def test_master_mexe_em_qualquer_atendimento(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO))
    resposta = cliente.patch("/api/admin/agendamentos/7/pagamento",
                             json={"forma": "pix"}, headers=cabecalho("master"))
    assert resposta.status_code == 200
    assert conn.commits == 1


# ------------------------------------------------------------------- consumos

POMADA = {"id": 3, "nome": "Pomada", "preco_centavos": 2500}
CERA = {"id": 4, "nome": "Cera", "preco_centavos": 1800}


def test_get_lista_os_adicionais(cliente, monkeypatch):
    ligar(monkeypatch, ConexaoAgendamento(
        agendamento=AGENDAMENTO,
        consumos=[{"id": 1, "produto_id": 3, "descricao": "Pomada",
                   "valor_centavos": 2500, "quantidade": 2}],
    ))
    resposta = cliente.get("/api/admin/agendamentos/7/consumos", headers=cabecalho())
    assert resposta.status_code == 200
    corpo = json.loads(resposta.data)
    assert corpo[0]["descricao"] == "Pomada"
    assert corpo[0]["valor_centavos"] == 2500


def test_put_grava_o_preco_do_catalogo_e_nao_o_enviado(cliente, monkeypatch):
    """
    O SNAPSHOT: o preço vem do banco no momento do lançamento. Mesmo que o
    cliente mande um preço na requisição, ele é ignorado.
    """
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO,
                                                 produtos=[POMADA]))
    resposta = cliente.put(
        "/api/admin/agendamentos/7/consumos",
        json={"itens": [{"produto_id": 3, "quantidade": 2,
                         "valor_centavos": 1, "descricao": "de graca"}]},
        headers=cabecalho(),
    )
    assert resposta.status_code == 200
    assert conn.inserts_de_consumo() == [(7, 3, "Pomada", 2500, 2)]


def test_put_substitui_tudo_apagando_antes(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO,
                                                 produtos=[POMADA, CERA]))
    cliente.put("/api/admin/agendamentos/7/consumos",
                json={"itens": [{"produto_id": 4, "quantidade": 1}]},
                headers=cabecalho())
    assert conn.houve_delete_de_consumo()
    assert conn.inserts_de_consumo() == [(7, 4, "Cera", 1800, 1)]


def test_put_com_lista_vazia_so_limpa(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO,
                                                 produtos=[POMADA]))
    resposta = cliente.put("/api/admin/agendamentos/7/consumos",
                           json={"itens": []}, headers=cabecalho())
    assert resposta.status_code == 200
    assert conn.houve_delete_de_consumo()
    assert conn.inserts_de_consumo() == []


def test_put_ignora_produto_fora_do_catalogo(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO,
                                                 produtos=[POMADA]))
    cliente.put("/api/admin/agendamentos/7/consumos",
                json={"itens": [{"produto_id": 999, "quantidade": 1}]},
                headers=cabecalho())
    assert conn.inserts_de_consumo() == []


@pytest.mark.parametrize("quantidade", [0, -3, None, "abc"])
def test_put_ignora_quantidade_invalida(cliente, monkeypatch, quantidade):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=AGENDAMENTO,
                                                 produtos=[POMADA]))
    cliente.put("/api/admin/agendamentos/7/consumos",
                json={"itens": [{"produto_id": 3, "quantidade": quantidade}]},
                headers=cabecalho())
    assert conn.inserts_de_consumo() == []


def test_consumos_de_agendamento_inexistente_da_404(cliente, monkeypatch):
    conn = ligar(monkeypatch, ConexaoAgendamento(agendamento=None))
    assert cliente.get("/api/admin/agendamentos/999/consumos",
                       headers=cabecalho()).status_code == 404
    assert cliente.put("/api/admin/agendamentos/999/consumos",
                       json={"itens": []}, headers=cabecalho()).status_code == 404
    assert conn.commits == 0


def test_consumos_sem_token_bloqueia(cliente):
    assert cliente.get("/api/admin/agendamentos/7/consumos").status_code == 401
    assert cliente.put("/api/admin/agendamentos/7/consumos",
                       json={"itens": []}).status_code == 401
