"""
Teste DIFERENCIAL do GET /api/horarios-disponiveis-periodo.

A regra de ouro deste endpoint: ele existe só pra economizar requisição, NUNCA
pra mudar resultado. Então o que se testa aqui não é "o que ele devolve" — é
que ele devolve EXATAMENTE o mesmo que o endpoint de um dia devolveria, dia a
dia, no mesmo cenário.

Se algum dia alguém otimizar o cálculo e as duas respostas divergirem, é aqui
que estoura — antes de um cliente ver horário na tela e não conseguir marcar,
que é o bug histórico deste projeto.

Conexão falsa: um único conjunto de dados responde tanto às consultas do
endpoint de um dia quanto às do período.
"""
import json
from datetime import date, datetime

import pytest

import app


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class ConexaoAgenda:
    """
    Mesma base de dados servindo as duas formas de consulta: por dia
    (`data = %s`) e por período (`data BETWEEN %s AND %s`).
    """

    def __init__(self, agendamentos=(), bloqueios=(), expedientes=(),
                 almoco_fixo=None, duracao=30):
        self.agendamentos = list(agendamentos)
        self.bloqueios = list(bloqueios)
        self.expedientes = list(expedientes)
        self.almoco_fixo = almoco_fixo
        self.duracao = duracao
        self.consultas = 0

    def execute(self, sql, params=None):
        self.consultas += 1
        q = " ".join(sql.split()).lower()
        periodo = "between" in q

        if "duracao_min from servicos" in q:
            return _Cursor([{"duracao_min": self.duracao}])

        if "from agendamentos" in q:
            return _Cursor(self._filtrar(self.agendamentos, params, periodo))

        if "from bloqueios" in q:
            return _Cursor(self._filtrar(self.bloqueios, params, periodo))

        if "from expedientes" in q:
            # por dia: (barbeiro_id, data) | período: (barbeiro_id, ini, fim)
            if periodo:
                ini, fim = params[1], params[2]
                return _Cursor([e for e in self.expedientes if ini <= e["data"] <= fim])
            return _Cursor([e for e in self.expedientes if e["data"] == params[1]])

        if "almoco_fixo from barbeiros" in q:
            return _Cursor([{"almoco_fixo": self.almoco_fixo}])

        raise AssertionError("consulta inesperada: %s" % q)

    @staticmethod
    def _filtrar(linhas, params, periodo):
        if periodo:                      # (ini, fim, barbeiro_id)
            ini, fim = params[0], params[1]
            return [l for l in linhas if ini <= l["data"] <= fim]
        return [l for l in linhas if l["data"] == params[0]]   # (data, barbeiro_id)

    def close(self):
        pass


HOJE = date(2026, 9, 7)          # segunda — pra pegar a folga do dono também
INICIO = "2026-09-07"
DIAS = 8


@pytest.fixture
def cliente():
    return app.app.test_client()


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    """Congela o dia e a hora: senão 'hoje' muda o corte de antecedência."""
    monkeypatch.setattr(app, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(app, "agora_br", lambda: datetime(2026, 9, 7, 7, 0))


def datas_do_periodo():
    from datetime import timedelta
    return [(HOJE + timedelta(days=i)).isoformat() for i in range(DIAS)]


def comparar(cliente, monkeypatch, **dados):
    """
    Roda os dois endpoints no MESMO cenário e devolve (por_dia, periodo) já
    reduzidos à lista de horários de cada data.
    """
    # período: uma chamada só
    monkeypatch.setattr(app, "get_connection", lambda: ConexaoAgenda(**dados))
    r = cliente.get(f"/api/horarios-disponiveis-periodo?inicio={INICIO}&dias={DIAS}"
                    f"&barbeiro_id=1&servico_id=2")
    assert r.status_code == 200
    periodo = {d: v["horarios_disponiveis"] for d, v in json.loads(r.data)["dias"].items()}

    # dia a dia: uma chamada por data
    por_dia = {}
    for data_str in datas_do_periodo():
        monkeypatch.setattr(app, "get_connection", lambda: ConexaoAgenda(**dados))
        r = cliente.get(f"/api/horarios-disponiveis?data={data_str}"
                        f"&barbeiro_id=1&servico_id=2")
        assert r.status_code == 200
        por_dia[data_str] = json.loads(r.data)["horarios_disponiveis"]

    return por_dia, periodo


# ------------------------------------------------- os dois têm que concordar

def test_agenda_vazia(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch)
    assert periodo == por_dia
    assert any(len(v) > 0 for v in periodo.values())     # o teste não é vazio à toa


def test_com_agendamentos(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, agendamentos=[
        {"data": "2026-09-08", "hora": "09:00", "duracao_min": 40},
        {"data": "2026-09-08", "hora": "10:00", "duracao_min": 30},
        {"data": "2026-09-10", "hora": "14:00", "duracao_min": 60},
    ])
    assert periodo == por_dia
    assert "09:00" not in periodo["2026-09-08"]


def test_com_almoco_fixo(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, almoco_fixo="12:30")
    assert periodo == por_dia
    assert "12:30" not in periodo["2026-09-08"]


def test_com_bloqueio_de_horario(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, bloqueios=[
        {"data": "2026-09-09", "hora": "15:00", "duracao_min": 60, "motivo": "Dentista"},
    ])
    assert periodo == por_dia
    assert "15:00" not in periodo["2026-09-09"]


def test_com_bloqueio_de_dia_inteiro(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, bloqueios=[
        {"data": "2026-09-09", "hora": None, "duracao_min": None, "motivo": "Feriado"},
    ])
    assert periodo == por_dia
    assert periodo["2026-09-09"] == []


def test_com_expediente_manual(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, expedientes=[
        {"data": "2026-09-09", "inicio": "14:00", "fim": "17:00"},
    ])
    assert periodo == por_dia
    assert periodo["2026-09-09"][0] == "14:00"
    assert "09:00" not in periodo["2026-09-09"]


def test_com_folga_gravada_no_expediente(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch, expedientes=[
        {"data": "2026-09-09", "inicio": "00:00", "fim": "00:00"},
    ])
    assert periodo == por_dia
    assert periodo["2026-09-09"] == []


def test_domingo_fecha_nos_dois(cliente, monkeypatch):
    por_dia, periodo = comparar(cliente, monkeypatch)
    domingo = "2026-09-13"        # o 7º dia a partir de 07/09 é domingo
    assert periodo[domingo] == []
    assert por_dia[domingo] == []


def test_folga_semanal_do_dono(cliente, monkeypatch):
    """07/09/2026 é segunda — dia de folga do barbeiro 1 (dono)."""
    por_dia, periodo = comparar(cliente, monkeypatch)
    assert periodo["2026-09-07"] == por_dia["2026-09-07"]


def test_antecedencia_de_hoje_vale_nos_dois(cliente, monkeypatch):
    """
    O corte por antecedência só existe pro dia de HOJE. Precisa de um dia em que
    o barbeiro realmente atenda — na segunda (folga do dono) não sobra horário
    nenhum pra filtrar, e aí os dois lados concordariam à toa.

    Aqui hoje é TERÇA, meio da manhã: os horários da manhã têm que sumir dos
    dois endpoints, e sumir igual.
    """
    global HOJE, INICIO
    hoje_real, inicio_real = HOJE, INICIO
    HOJE, INICIO = date(2026, 9, 8), "2026-09-08"        # terça
    try:
        monkeypatch.setattr(app, "data_hoje", lambda: HOJE)
        monkeypatch.setattr(app, "agora_br", lambda: datetime(2026, 9, 8, 10, 30))
        por_dia, periodo = comparar(cliente, monkeypatch)

        assert periodo == por_dia
        hoje_str = "2026-09-08"
        # a manhã já passou: nada antes das 10:30 pode aparecer
        assert all(h > "10:30" for h in periodo[hoje_str]), periodo[hoje_str]
        # e o dia seguinte, que não sofre o corte, continua abrindo cedo
        assert periodo["2026-09-09"][0] < "10:30"
    finally:
        HOJE, INICIO = hoje_real, inicio_real


def test_tudo_junto(cliente, monkeypatch):
    por_dia, periodo = comparar(
        cliente, monkeypatch,
        agendamentos=[{"data": "2026-09-08", "hora": "09:00", "duracao_min": 40},
                      {"data": "2026-09-11", "hora": "16:00", "duracao_min": 30}],
        bloqueios=[{"data": "2026-09-08", "hora": "15:00", "duracao_min": 30, "motivo": "Almoço"},
                   {"data": "2026-09-12", "hora": None, "duracao_min": None, "motivo": "Feriado"}],
        expedientes=[{"data": "2026-09-09", "inicio": "10:00", "fim": "16:00"}],
        almoco_fixo="12:00",
    )
    assert periodo == por_dia


@pytest.mark.parametrize("duracao", [10, 20, 30, 40, 60])
def test_concorda_em_qualquer_duracao_de_servico(cliente, monkeypatch, duracao):
    """A duração muda o passo dos slots e a tolerância de fim de dia."""
    por_dia, periodo = comparar(cliente, monkeypatch, duracao=duracao,
                                agendamentos=[{"data": "2026-09-08", "hora": "11:00",
                                               "duracao_min": 30}])
    assert periodo == por_dia


# ------------------------------------------------------------ economia real

def test_periodo_faz_menos_consultas_que_dia_a_dia(cliente, monkeypatch):
    """O ganho tem que ser real: consultas fixas, não uma por dia."""
    conexoes = []

    def nova():
        c = ConexaoAgenda()
        conexoes.append(c)
        return c

    monkeypatch.setattr(app, "get_connection", nova)
    cliente.get(f"/api/horarios-disponiveis-periodo?inicio={INICIO}&dias={DIAS}"
                f"&barbeiro_id=1&servico_id=2")
    consultas_periodo = sum(c.consultas for c in conexoes)

    conexoes.clear()
    for data_str in datas_do_periodo():
        cliente.get(f"/api/horarios-disponiveis?data={data_str}&barbeiro_id=1&servico_id=2")
    consultas_dia_a_dia = sum(c.consultas for c in conexoes)

    assert consultas_periodo == 5                    # 4 em lote + a do serviço
    assert consultas_periodo < consultas_dia_a_dia


# ------------------------------------------------------------ entradas ruins

def test_data_invalida(cliente):
    assert cliente.get("/api/horarios-disponiveis-periodo?inicio=xx&barbeiro_id=1").status_code == 400


def test_barbeiro_obrigatorio(cliente):
    assert cliente.get(f"/api/horarios-disponiveis-periodo?inicio={INICIO}").status_code == 400


@pytest.mark.parametrize("dias", ["0", "99", "abc", "-3"])
def test_quantidade_de_dias_invalida(cliente, dias):
    r = cliente.get(f"/api/horarios-disponiveis-periodo?inicio={INICIO}&dias={dias}&barbeiro_id=1")
    assert r.status_code == 400
