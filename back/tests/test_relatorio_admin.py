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
                 produtos_centavos=0, produtos_qtd=0, servicos=(),
                 comissoes=0, cancelados_qtd=0, cancelados_valor=0):
        self.faturamento = faturamento
        self.total = total
        self.por_dia = por_dia
        self.produtos_centavos = produtos_centavos
        self.produtos_qtd = produtos_qtd
        self.servicos = servicos
        self.comissoes = comissoes
        self.cancelados_qtd = cancelados_qtd
        self.cancelados_valor = cancelados_valor
        self.executados = []

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if "as centavos" in consulta:
            return _Cursor([{"centavos": self.produtos_centavos,
                             "quantidade": self.produtos_qtd}])
        if "comissao_pct" in consulta:
            return _Cursor([{"total": self.comissoes}])
        if "status = 'cancelado'" in consulta:
            return _Cursor([{"quantidade": self.cancelados_qtd,
                             "valor": self.cancelados_valor}])
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
          hoje=date(2026, 8, 20), inicio=None, fim=None, **conexao):
    conn = ConexaoRelatorio(**conexao)
    monkeypatch.setattr(relatorios, "get_connection", lambda: conn)
    monkeypatch.setattr(relatorios, "data_hoje", lambda: hoje)
    filtros = []
    if periodo:
        filtros.append("periodo=%s" % periodo)
    if inicio is not None:
        filtros.append("inicio=%s" % inicio)
    if fim is not None:
        filtros.append("fim=%s" % fim)
    url = "/api/admin/relatorio" + ("?" + "&".join(filtros) if filtros else "")
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


# ------------------------------------------------- lucro real e cancelamentos

def test_lucro_e_faturamento_menos_comissao(cliente, monkeypatch):
    """Serviços 1000 + produtos 200, comissão 600 -> sobra 600 pra casa.
    Produto NÃO comissiona, então entra inteiro no lucro."""
    _, corpo, _ = pedir(cliente, monkeypatch, faturamento=1000,
                        produtos_centavos=20000, comissoes=600)
    assert corpo["faturamento_total"] == 1200.0
    assert corpo["comissoes"] == 600.0
    assert corpo["lucro_real"] == 600.0


def test_lucro_com_tudo_do_dono_e_o_faturamento_inteiro(cliente, monkeypatch):
    """O dono entra com comissão 0: o que ele corta fica todo pra casa."""
    _, corpo, _ = pedir(cliente, monkeypatch, faturamento=500, comissoes=0)
    assert corpo["lucro_real"] == 500.0


def test_comissao_usa_o_pct_do_barbeiro_no_banco(cliente, monkeypatch):
    """A conta não pode ser refeita no Python com um percentual chutado: tem
    que sair do comissao_pct de cada barbeiro, igual à Contagem."""
    _, _, conn = pedir(cliente, monkeypatch, comissoes=10)
    sql = [s for s, _ in conn.executados if "comissao_pct" in s]
    assert len(sql) == 1
    assert "servicos.preco * barbeiros.comissao_pct / 100.0" in sql[0]
    assert "status != 'cancelado'" in sql[0]


def test_barbeiro_nao_recebe_lucro_nem_comissoes(cliente, monkeypatch):
    """É o número da casa. Some da RESPOSTA, não só da tela."""
    _, corpo, _ = pedir(cliente, monkeypatch, papel="barbeiro", barbeiro_id=3,
                        faturamento=1000, comissoes=600)
    assert corpo["lucro_real"] is None
    assert corpo["comissoes"] is None


def test_cancelados_vem_com_quantidade_e_valor(cliente, monkeypatch):
    _, corpo, _ = pedir(cliente, monkeypatch, cancelados_qtd=4,
                        cancelados_valor=160)
    assert corpo["cancelados_qtd"] == 4
    assert corpo["cancelados_valor"] == 160.0


def test_cancelado_nao_entra_no_faturamento(cliente, monkeypatch):
    """O resto do relatório ignora cancelado; a contagem deles é a única
    consulta que olha pra esse status — e nenhuma outra pode olhar."""
    _, _, conn = pedir(cliente, monkeypatch, cancelados_qtd=4)
    olham_cancelado = [s for s, _ in conn.executados if "status = 'cancelado'" in s]
    assert len(olham_cancelado) == 1
    ignoram = [s for s, _ in conn.executados if "status != 'cancelado'" in s]
    assert len(ignoram) == len(conn.executados) - 1


def test_cancelados_respeitam_o_periodo(cliente, monkeypatch):
    _, _, conn = pedir(cliente, monkeypatch, inicio="2026-09-01", fim="2026-09-30",
                       cancelados_qtd=2)
    sql, params = next((s, p) for s, p in conn.executados if "status = 'cancelado'" in s)
    assert params[:2] == ["2026-09-01", "2026-09-30"]


# ----------------------------------------------------- período escolhido a dedo

def test_faixa_livre_entra_na_consulta(cliente, monkeypatch):
    _, corpo, conn = pedir(cliente, monkeypatch,
                           inicio="2026-10-01", fim="2026-10-31")
    assert corpo["data_inicio"] == "2026-10-01"
    assert corpo["data_fim"] == "2026-10-31"
    assert corpo["periodo"] == "personalizado"
    # não basta devolver na resposta: tem que ter ido pras cinco consultas
    assert all(p[:2] == ["2026-10-01", "2026-10-31"] for _, p in conn.executados)


def test_mes_ja_fechado_e_aceito(cliente, monkeypatch):
    """O relatório é sobre o PASSADO. O validar_data do agendamento recusa data
    passada — se um dia alguém o reaproveitar aqui, este teste estoura."""
    status, corpo, _ = pedir(cliente, monkeypatch, hoje=date(2026, 10, 5),
                             inicio="2026-01-01", fim="2026-01-31")
    assert status == 200
    assert corpo["data_inicio"] == "2026-01-01"


def test_faixa_livre_ganha_do_atalho(cliente, monkeypatch):
    """Mandando os dois, vale a faixa — é a escolha explícita de quem clicou."""
    _, corpo, _ = pedir(cliente, monkeypatch, periodo="mes",
                        inicio="2026-10-01", fim="2026-10-10")
    assert corpo["data_inicio"] == "2026-10-01"
    assert corpo["data_fim"] == "2026-10-10"


@pytest.mark.parametrize("inicio,fim", [
    ("2026-10-01", None),        # só um lado da faixa
    (None, "2026-10-31"),
])
def test_faixa_pela_metade_da_400(cliente, monkeypatch, inicio, fim):
    """Aceitar meia faixa daria um relatório que parece filtrado e não é."""
    status, _, conn = pedir(cliente, monkeypatch, inicio=inicio, fim=fim)
    assert status == 400
    assert conn.executados == []


def test_fim_antes_do_inicio_da_400(cliente, monkeypatch):
    status, _, conn = pedir(cliente, monkeypatch,
                            inicio="2026-10-31", fim="2026-10-01")
    assert status == 400
    assert conn.executados == []


@pytest.mark.parametrize("data_ruim", ["31/10/2026", "2026-13-01", "ontem"])
def test_data_invalida_da_400(cliente, monkeypatch, data_ruim):
    status, _, conn = pedir(cliente, monkeypatch, inicio=data_ruim, fim="2026-10-31")
    assert status == 400
    assert conn.executados == []


def test_um_dia_so_e_faixa_valida(cliente, monkeypatch):
    status, corpo, _ = pedir(cliente, monkeypatch,
                             inicio="2026-10-05", fim="2026-10-05")
    assert status == 200
    assert corpo["data_inicio"] == corpo["data_fim"] == "2026-10-05"


def test_barbeiro_na_faixa_livre_continua_restrito(cliente, monkeypatch):
    """A faixa não pode ser uma porta pra enxergar o faturamento dos outros."""
    _, _, conn = pedir(cliente, monkeypatch, papel="barbeiro", barbeiro_id=3,
                       inicio="2026-10-01", fim="2026-10-31")
    assert all("agendamentos.barbeiro_id = %s" in sql for sql, _ in conn.executados)
    assert all(p[-1] == 3 for _, p in conn.executados)


def test_salao_nao_escapa_pela_faixa_livre(cliente, monkeypatch):
    status, _, conn = pedir(cliente, monkeypatch, papel="salao",
                            inicio="2026-10-01", fim="2026-10-31")
    assert status == 403
    assert conn.executados == []


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
