"""
Rotas da fila online. O banco fica de fora: fila_dados e os helpers que as
rotas usam são trocados por monkeypatch, e o "dia" é montado no próprio teste.
"""
import json
from datetime import date, datetime, timedelta, timezone

import jwt
import psycopg2.errors
import pytest

import app
import fila_dados
import rotas_fila
from fila import BarbeiroDia, Lugar
from fila_dados import DiaFila
from extensoes import limiter

HOJE = date(2026, 12, 21)                 # segunda, dia de fila
TEL = "11988887777"
MSG_REPETIDO = ("Esse telefone já está na fila. Abra o link que você recebeu "
                "ou saia da fila antes de entrar de novo.")


class Conexao:
    def __init__(self):
        self.executados = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.executados.append(" ".join(sql.split()).lower())

        class _C:
            def fetchall(self_inner):
                return []

            def fetchone(self_inner):
                return None
        return _C()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def barbeiro(id=1, livre=600, abertura=540, fechamento=1200, ocupadas=()):
    return BarbeiroDia(id=id, abertura=abertura, fechamento=fechamento,
                       livre_a_partir=livre, ocupadas=tuple(ocupadas))


def lugar(id, barbeiro_id=1, dur=30, status="aguardando", atendido_por=None):
    return Lugar(id=id, barbeiro_id=barbeiro_id, atendido_por=atendido_por,
                 status=status, duracao_min=dur)


def dia(barbeiros=None, lugares=(), indisponiveis=(), abertos=None, agora=600):
    barbeiros = [barbeiro()] if barbeiros is None else barbeiros
    return DiaFila(
        agora_min=agora, barbeiros=barbeiros, lugares=list(lugares),
        indisponiveis=set(indisponiveis),
        nomes={1: "JP", 2: "Rian", 3: "Gabriel"},
        fotos={1: None, 2: None, 3: None},
        abertos_agora=set(b.id for b in barbeiros) if abertos is None else set(abertos),
    )


@pytest.fixture
def cliente():
    return app.app.test_client()


@pytest.fixture
def ambiente(monkeypatch):
    """Hoje 10:00 de um dia de fila, serviço de 30 min, ninguém bloqueado."""
    conn = Conexao()
    estado = {"dia": dia(), "inseridos": [], "repetido": False}
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(rotas_fila, "get_connection", lambda: conn)
    monkeypatch.setattr(rotas_fila, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(rotas_fila, "agora_br",
                        lambda: datetime(2026, 12, 21, 10, 0, tzinfo=timezone(timedelta(hours=-3))))
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: True)
    monkeypatch.setattr(fila_dados, "carregar_dia", lambda c, d, a: estado["dia"])
    monkeypatch.setattr(fila_dados, "telefone_na_fila", lambda c, d, t: estado["repetido"])
    monkeypatch.setattr(fila_dados, "eh_telefone_generico", lambda c, t: t == "11972570084")
    monkeypatch.setattr(rotas_fila, "servico_na_data",
                        lambda c, sid, d: {"duracao_min": 30, "valor_centavos": 4000} if sid == 1 else None)
    monkeypatch.setattr(rotas_fila, "cliente_esta_bloqueado", lambda c, t: False)
    monkeypatch.setattr(rotas_fila, "buscar_ou_criar_cliente", lambda c, n, t, e: 77)

    def inserir(c, **kw):
        estado["inseridos"].append(kw)
        return 99, "tok-novo"
    monkeypatch.setattr(fila_dados, "inserir_lugar", inserir)
    estado["conn"] = conn
    return estado


def entrar(cliente, **troca):
    corpo = {"nome": "Ana", "telefone": TEL, "servico_id": 1, "barbeiro_id": 1}
    corpo.update(troca)
    r = cliente.post("/api/fila", json=corpo)
    return r.status_code, json.loads(r.data)


# ---------------------------------------------------------------- GET /api/fila/hoje

def test_hoje_fora_do_periodo(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: False)
    corpo = json.loads(cliente.get("/api/fila/hoje").data)
    assert corpo["dia_de_fila"] is False
    assert corpo["barbeiros"] == []


def test_hoje_lista_espera_por_barbeiro(cliente, ambiente):
    ambiente["dia"] = dia(lugares=[lugar(1), lugar(2)])
    corpo = json.loads(cliente.get("/api/fila/hoje").data)
    jp = next(b for b in corpo["barbeiros"] if b["id"] == 1)
    assert jp["aberta"] is True
    assert jp["esperando"] == 2
    assert jp["espera_min"] == 60
    assert corpo["qualquer_um"]["aberta"] is True


def test_hoje_barbeiro_de_folga(cliente, ambiente):
    ambiente["dia"] = dia(barbeiros=[barbeiro(1)])
    corpo = json.loads(cliente.get("/api/fila/hoje").data)
    rian = next(b for b in corpo["barbeiros"] if b["id"] == 2)
    assert rian["aberta"] is False
    assert rian["motivo"] == "De folga hoje"


# ---------------------------------------------------------------- POST /api/fila

def test_entrar_ok(cliente, ambiente):
    status, corpo = entrar(cliente, telefone="(11) 98888-7777")
    assert status == 201
    assert corpo["token"] == "tok-novo"
    assert corpo["posicao"] == 1
    assert ambiente["inseridos"][0]["telefone"] == TEL
    assert ambiente["inseridos"][0]["barbeiro_id"] == 1
    assert ambiente["conn"].commits == 1


def test_entrar_qualquer_um(cliente, ambiente):
    status, _ = entrar(cliente, barbeiro_id=None)
    assert status == 201
    assert ambiente["inseridos"][0]["barbeiro_id"] is None


def test_entrar_mascara_e_sem_mascara_e_o_mesmo_telefone(cliente, ambiente, monkeypatch):
    vistos = []

    def na_fila(c, d, t):
        vistos.append(t)
        return True
    monkeypatch.setattr(fila_dados, "telefone_na_fila", na_fila)
    status, corpo = entrar(cliente, telefone="(11) 98888-7777")
    assert status == 409
    assert corpo["erro"] == MSG_REPETIDO
    assert vistos == [TEL]
    assert ambiente["inseridos"] == []


def test_entrar_corrida_vira_409(cliente, ambiente, monkeypatch):
    def duplicado(c, **kw):
        raise psycopg2.errors.UniqueViolation("ux_fila_telefone_ativo")
    monkeypatch.setattr(fila_dados, "inserir_lugar", duplicado)
    status, corpo = entrar(cliente)
    assert status == 409
    assert corpo["erro"] == MSG_REPETIDO
    assert ambiente["conn"].rollbacks == 1


def test_entrar_cliente_bloqueado(cliente, ambiente, monkeypatch):
    from agendamentos import MENSAGEM_CLIENTE_BLOQUEADO
    monkeypatch.setattr(rotas_fila, "cliente_esta_bloqueado", lambda c, t: True)
    status, corpo = entrar(cliente)
    assert status == 403
    assert corpo["erro"] == MENSAGEM_CLIENTE_BLOQUEADO


def test_entrar_perto_de_fechar(cliente, ambiente, monkeypatch):
    # sábado, fecha 19:00; barbeiro livre às 18:45; Degradê de 40 min
    monkeypatch.setattr(rotas_fila, "servico_na_data", lambda c, sid, d: {"duracao_min": 40})
    ambiente["dia"] = dia(barbeiros=[barbeiro(1, livre=1125, abertura=480, fechamento=1140)],
                          agora=1125)
    status, corpo = entrar(cliente)
    assert status == 409
    assert corpo["erro"] == "A fila de hoje já fechou para esse barbeiro."


def test_entrar_qualquer_um_com_tudo_fechado(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(rotas_fila, "servico_na_data", lambda c, sid, d: {"duracao_min": 40})
    ambiente["dia"] = dia(barbeiros=[barbeiro(1, livre=1125, abertura=480, fechamento=1140)],
                          agora=1125)
    status, corpo = entrar(cliente, barbeiro_id=None)
    assert status == 409
    assert corpo["erro"] == "A fila de hoje já fechou."


def test_entrar_barbeiro_de_folga(cliente, ambiente):
    ambiente["dia"] = dia(barbeiros=[barbeiro(1)])
    status, corpo = entrar(cliente, barbeiro_id=2)
    assert status == 409
    assert corpo["erro"] == "A fila de hoje já fechou para esse barbeiro."


def test_entrar_antes_de_abrir(cliente, ambiente):
    ambiente["dia"] = dia(barbeiros=[barbeiro(1, abertura=660)], abertos=[])
    status, corpo = entrar(cliente)
    assert status == 409
    assert corpo["erro"] == "A fila de hoje ainda não abriu."


def test_telefone_generico_nao_conta_como_repetido(cliente, ambiente, monkeypatch):
    def nao_deveria(c, d, t):
        raise AssertionError("telefone genérico não passa pela regra de repetido")
    monkeypatch.setattr(fila_dados, "telefone_na_fila", nao_deveria)
    status, _ = entrar(cliente, telefone="11972570084")
    assert status == 201
    assert ambiente["inseridos"][0]["telefone"] is None


def test_entrar_fora_do_dia_de_fila(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda c, d: False)
    status, _ = entrar(cliente)
    assert status == 409


def test_entrar_sem_telefone(cliente, ambiente):
    status, _ = entrar(cliente, telefone="")
    assert status == 400


# ---------------------------------------------------------------- GET /api/fila/<token>

def registro(status="aguardando", id=2, barbeiro_id=1, atendido_por=None, data=HOJE.isoformat(),
             chamado_em=None):
    return {"id": id, "data": data, "status": status, "barbeiro_id": barbeiro_id,
            "atendido_por": atendido_por, "chamado_em": chamado_em, "duracao_min": 30,
            "nome": "Ana", "servico": "Corte", "barbeiro_nome": "JP" if barbeiro_id == 1 else None,
            "atendido_por_nome": None}


def test_situacao_token_inexistente(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: None)
    assert cliente.get("/api/fila/abc").status_code == 404


def test_situacao_de_outro_dia_e_404(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: registro(data="2026-12-20"))
    assert cliente.get("/api/fila/abc").status_code == 404


def test_situacao_posicao_e_espera(cliente, ambiente, monkeypatch):
    ambiente["dia"] = dia(lugares=[lugar(1), lugar(2)])
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: registro())
    corpo = json.loads(cliente.get("/api/fila/tok").data)
    assert corpo["status"] == "aguardando"
    assert corpo["posicao"] == 2
    assert corpo["a_frente"] == 1
    assert corpo["espera_min"] == 30
    assert corpo["barbeiro"] == "JP"
    assert corpo["servico"] == "Corte"
    assert corpo["tolerancia_min"] == 10


def test_situacao_barbeiro_indisponivel(cliente, ambiente, monkeypatch):
    ambiente["dia"] = dia(barbeiros=[barbeiro(1)], lugares=[lugar(2, barbeiro_id=3)],
                          indisponiveis=[3])
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: registro(barbeiro_id=3))
    r = cliente.get("/api/fila/tok")
    corpo = json.loads(r.data)
    assert r.status_code == 200
    assert corpo["status"] == "aguardando"
    assert corpo["espera_min"] is None


def test_situacao_finalizado_nao_calcula_fila(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: registro(status="nao_veio"))
    corpo = json.loads(cliente.get("/api/fila/tok").data)
    assert corpo["status"] == "nao_veio"
    assert corpo["posicao"] is None


# ---------------------------------------------------------------- POST /api/fila/<token>/sair

def test_sair_duas_vezes(cliente, ambiente, monkeypatch):
    atual = {"status": "aguardando"}
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: registro(status=atual["status"]))

    def mudar(c, lugar_id, acao, barbeiro_id=None, agendamento_id=None):
        from fila import proximo_status
        atual["status"] = proximo_status(atual["status"], acao)
        return {"id": lugar_id, "status": atual["status"]}
    monkeypatch.setattr(fila_dados, "mudar_status", mudar)
    assert cliente.post("/api/fila/tok/sair").status_code == 200
    assert cliente.post("/api/fila/tok/sair").status_code == 409


def test_sair_token_inexistente(cliente, ambiente, monkeypatch):
    monkeypatch.setattr(fila_dados, "buscar_por_token", lambda c, t: None)
    assert cliente.post("/api/fila/abc/sair").status_code == 404
