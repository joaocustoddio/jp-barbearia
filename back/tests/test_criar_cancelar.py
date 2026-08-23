"""
Testes das duas rotas públicas que escrevem no banco:

  POST /api/agendamentos            cliente marca pelo site
  POST /api/agendamentos/cancelar   cliente cancela o próprio

O que mais importa aqui é o 409 de conflito. O bug mais recorrente do projeto
foi horário que aparecia na tela mas não era aceito no agendamento; a defesa é
as duas coisas passarem por `janelas_ocupadas`. Aqui se garante o outro lado:
horário ocupado tem que ser RECUSADO, e a recusa tem que ser 409, não 500.

Conexão falsa: nada de banco, nada de Telegram, nada de e-mail.
"""
import json
from datetime import date, datetime, timedelta

import pytest

import agendamentos
import app
import emails
import notificacoes
import rotas_publicas
import validacoes


HOJE = date(2026, 8, 24)          # segunda
DATA = "2026-08-26"               # quarta, dentro do limite de 7 dias
HORA = "10:00"


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class ConexaoCriacao:
    """
    Finge o banco do fluxo de criação e anota o que foi executado.

    Cada consulta é reconhecida por um pedaço característico do SQL; a ordem
    dos testes abaixo importa porque vários SQLs citam as mesmas tabelas.
    """

    def __init__(self, barbeiro_ativo=True, servico_existe=True, duracao=30,
                 agendamentos_do_dia=(), bloqueios=(), almoco_fixo=None,
                 detalhes=None):
        self.barbeiro_ativo = barbeiro_ativo
        self.servico_existe = servico_existe
        self.duracao = duracao
        self.agendamentos_do_dia = agendamentos_do_dia
        self.bloqueios = bloqueios
        self.almoco_fixo = almoco_fixo
        self.detalhes = detalhes or {"servico": "Degradê", "preco": 40,
                                     "duracao_min": 40, "barbeiro": "Rian"}
        self.executados = []
        self.commits = 0
        self._ultimo = _Cursor([])

    def execute(self, sql, params=None):
        # Imita o psycopg2: guarda o resultado no próprio objeto, porque o
        # código faz `cur.execute(...)` e depois `cur.fetchone()`.
        self._ultimo = self._resolver(sql, params)
        return self._ultimo

    # o cursor falso é a própria conexão
    def fetchone(self):
        return self._ultimo.fetchone()

    def fetchall(self):
        return self._ultimo.fetchall()

    def _resolver(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))

        if consulta.startswith("insert into clientes"):
            return _Cursor([{"id": 100}])
        if consulta.startswith("insert into agendamentos"):
            return _Cursor([{"id": 555}])
        if "for update" in consulta:
            return _Cursor([{"id": 1}])
        if "ativo = 1" in consulta:
            return _Cursor([{"id": 2}] if self.barbeiro_ativo else [])
        if "duracao_min from servicos" in consulta:
            return _Cursor([{"duracao_min": self.duracao}])
        if "from servicos, barbeiros" in consulta:
            return _Cursor([self.detalhes])
        if "from servicos where id" in consulta:
            return _Cursor([{"id": 1}] if self.servico_existe else [])
        if "from agendamentos" in consulta:
            return _Cursor(self.agendamentos_do_dia)
        if "from bloqueios" in consulta:
            return _Cursor(self.bloqueios)
        if "almoco_fixo from barbeiros" in consulta:
            return _Cursor([{"almoco_fixo": self.almoco_fixo}])
        raise AssertionError("consulta inesperada: %s" % consulta)

    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def inseriu_agendamento(self):
        return any(s.startswith("insert into agendamentos") for s, _ in self.executados)


@pytest.fixture
def cliente():
    return app.app.test_client()


@pytest.fixture
def avisos(monkeypatch):
    """Captura Telegram e e-mail em vez de deixar sair."""
    enviados = {"telegram": [], "email": []}
    monkeypatch.setattr(notificacoes, "avisar_novo_agendamento",
                        lambda **kw: enviados["telegram"].append(kw) or True)
    monkeypatch.setattr(notificacoes, "avisar_cancelamento",
                        lambda **kw: enviados["telegram"].append(kw) or True)
    monkeypatch.setattr(emails, "enviar",
                        lambda *a, **kw: enviados["email"].append(a) or True)
    return enviados


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    """Congela 'hoje' e o expediente, pra data e horário não dependerem do dia real."""
    monkeypatch.setattr(agendamentos, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(validacoes, "data_hoje", lambda: HOJE)
    monkeypatch.setattr(agendamentos, "horario_efetivo",
                        lambda *a, **k: ("09:00", "20:00"))


def corpo(**troca):
    base = {"nome_cliente": "João Silva", "telefone": "11988887777",
            "servico_id": 1, "barbeiro_id": 2, "data": DATA, "hora": HORA}
    base.update(troca)
    return base


def marcar(cliente, monkeypatch, dados=None, **conexao):
    conn = ConexaoCriacao(**conexao)
    monkeypatch.setattr(agendamentos, "get_connection", lambda: conn)
    resposta = cliente.post("/api/agendamentos",
                            json=corpo(**(dados or {})) if dados is not None else corpo())
    return resposta.status_code, json.loads(resposta.data), conn


# ------------------------------------------------------------- caminho feliz

def test_agendamento_valido_cria_e_devolve_201(cliente, monkeypatch, avisos):
    status, resposta, conn = marcar(cliente, monkeypatch)
    assert status == 201
    assert resposta["agendamento_id"] == 555
    assert conn.commits == 1
    assert conn.inseriu_agendamento()


def test_agendamento_avisa_a_equipe_no_telegram(cliente, monkeypatch, avisos):
    marcar(cliente, monkeypatch)
    assert len(avisos["telegram"]) == 1
    assert avisos["telegram"][0]["cliente"] == "João Silva"
    assert avisos["telegram"][0]["barbeiro"] == "Rian"


def test_email_so_sai_quando_o_cliente_informa(cliente, monkeypatch, avisos):
    marcar(cliente, monkeypatch)
    assert avisos["email"] == []          # sem e-mail no corpo, nada é enviado

    marcar(cliente, monkeypatch, dados={"email": "joao@exemplo.com"})
    assert len(avisos["email"]) == 1
    assert avisos["email"][0][0] == "joao@exemplo.com"


def test_resposta_traz_link_do_google_agenda(cliente, monkeypatch, avisos):
    _, resposta, _ = marcar(cliente, monkeypatch)
    assert resposta["link_agenda"].startswith("https://calendar.google.com/")


# ------------------------------------------------------- conflito (o 409)

def test_horario_ocupado_recusa_com_409(cliente, monkeypatch, avisos):
    """O bug histórico do projeto: tem que RECUSAR, e com 409 — não 500."""
    status, resposta, conn = marcar(
        cliente, monkeypatch,
        agendamentos_do_dia=[{"hora": HORA, "duracao_min": 30}],
    )
    assert status == 409
    assert "erro" in resposta
    assert not conn.inseriu_agendamento()
    assert conn.commits == 0


def test_encaixe_na_brecha_seguinte_e_aceito(cliente, monkeypatch, avisos):
    """10:00 ocupado por 30min: 10:30 tem que passar."""
    status, _, conn = marcar(
        cliente, monkeypatch, dados={"hora": "10:30"},
        agendamentos_do_dia=[{"hora": "10:00", "duracao_min": 30}],
    )
    assert status == 201
    assert conn.inseriu_agendamento()


def test_almoco_fixo_tambem_bloqueia_o_agendamento(cliente, monkeypatch, avisos):
    status, _, conn = marcar(cliente, monkeypatch, dados={"hora": "12:30"},
                             almoco_fixo="12:30")
    assert status == 409
    assert not conn.inseriu_agendamento()


def test_dia_bloqueado_recusa(cliente, monkeypatch, avisos):
    status, resposta, conn = marcar(
        cliente, monkeypatch,
        bloqueios=[{"hora": None, "duracao_min": None, "motivo": "Feriado"}],
    )
    assert status == 400
    assert "bloqueada" in resposta["erro"].lower()
    assert not conn.inseriu_agendamento()


def test_fora_do_expediente_recusa(cliente, monkeypatch, avisos):
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"hora": "21:00"})
    assert status == 400
    assert "expediente" in resposta["erro"].lower()
    assert not conn.inseriu_agendamento()


# ------------------------------------------------------------- validações

def test_corpo_ausente_da_400(cliente):
    assert cliente.post("/api/agendamentos").status_code == 400


def test_barbeiro_obrigatorio(cliente, monkeypatch, avisos):
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"barbeiro_id": None})
    assert status == 400
    assert "barbeiro" in resposta["erro"].lower()
    assert conn.commits == 0


def test_barbeiro_inativo_recusa(cliente, monkeypatch, avisos):
    status, resposta, _ = marcar(cliente, monkeypatch, barbeiro_ativo=False)
    assert status == 400
    assert "barbeiro" in resposta["erro"].lower()


def test_telefone_e_obrigatorio_no_site(cliente, monkeypatch, avisos):
    """
    Telefone VAZIO tem que bater na regra de obrigatoriedade, não na de formato.
    A diferença importa: `validar_telefone` aceita vazio (é opcional no
    caderninho do barbeiro), então se a obrigatoriedade do site sumir, um
    agendamento sem telefone passaria — e a barbearia fica sem contato.
    """
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"telefone": ""})
    assert status == 400
    assert "obrigatório" in resposta["erro"].lower()
    assert not conn.inseriu_agendamento()


def test_telefone_so_de_espacos_tambem_e_recusado(cliente, monkeypatch, avisos):
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"telefone": "   "})
    assert status == 400
    assert not conn.inseriu_agendamento()


def test_servico_inexistente_recusa(cliente, monkeypatch, avisos):
    status, resposta, _ = marcar(cliente, monkeypatch, servico_existe=False)
    assert status == 400
    assert "serviço" in resposta["erro"].lower()


def test_domingo_e_recusado(cliente, monkeypatch, avisos):
    """2026-08-30 é domingo — DIAS_FECHADOS padrão."""
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"data": "2026-08-30"})
    assert status == 400
    assert "domingo" in resposta["erro"].lower()
    assert not conn.inseriu_agendamento()


def test_data_passada_recusada(cliente, monkeypatch, avisos):
    status, resposta, _ = marcar(cliente, monkeypatch, dados={"data": "2026-08-20"})
    assert status == 400
    assert "passada" in resposta["erro"].lower()


def test_alem_do_limite_de_dias_recusado(cliente, monkeypatch, avisos):
    """Site só deixa marcar até LIMITE_DIAS_AGENDAMENTO dias à frente."""
    longe = (HOJE + timedelta(days=agendamentos.LIMITE_DIAS_AGENDAMENTO + 1)).isoformat()
    status, resposta, conn = marcar(cliente, monkeypatch, dados={"data": longe})
    assert status == 400
    assert "dias" in resposta["erro"].lower()
    assert not conn.inseriu_agendamento()


@pytest.mark.parametrize("telefone", ["123", "119888877771234"])
def test_telefone_com_tamanho_invalido_recusado(cliente, monkeypatch, avisos, telefone):
    status, resposta, _ = marcar(cliente, monkeypatch, dados={"telefone": telefone})
    assert status == 400
    assert "telefone" in resposta["erro"].lower()


def test_email_mal_formatado_recusado(cliente, monkeypatch, avisos):
    status, resposta, _ = marcar(cliente, monkeypatch, dados={"email": "sem-arroba"})
    assert status == 400
    assert "mail" in resposta["erro"].lower()


# ------------------------------------------------------------- cancelamento

class ConexaoCancelamento:
    def __init__(self, achou=True):
        self.achou = achou
        self.executados = []
        self.commits = 0

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if consulta.startswith("update agendamentos"):
            return _Cursor([])
        linha = {"id": 555, "data": DATA, "hora": HORA, "cliente": "João Silva",
                 "servico": "Degradê", "barbeiro": "Rian"}
        return _Cursor([linha] if self.achou else [])

    def commit(self):
        self.commits += 1

    def close(self):
        pass

    def cancelou(self):
        return any(s.startswith("update agendamentos") for s, _ in self.executados)


def cancelar(cliente, monkeypatch, dados, achou=True):
    conn = ConexaoCancelamento(achou=achou)
    monkeypatch.setattr(rotas_publicas, "get_connection", lambda: conn)
    resposta = cliente.post("/api/agendamentos/cancelar", json=dados)
    return resposta.status_code, json.loads(resposta.data), conn


def test_cancelamento_valido(cliente, monkeypatch, avisos):
    status, resposta, conn = cancelar(
        cliente, monkeypatch, {"agendamento_id": 555, "telefone": "11988887777"})
    assert status == 200
    assert conn.cancelou()
    assert conn.commits == 1
    assert "cancelado" in resposta["mensagem"].lower()


def test_cancelamento_avisa_a_equipe(cliente, monkeypatch, avisos):
    cancelar(cliente, monkeypatch, {"agendamento_id": 555, "telefone": "11988887777"})
    assert len(avisos["telegram"]) == 1
    assert avisos["telegram"][0]["cliente"] == "João Silva"


def test_telefone_com_mascara_e_normalizado(cliente, monkeypatch, avisos):
    """O cliente digita com parênteses e traço; a busca usa só os dígitos."""
    _, _, conn = cancelar(cliente, monkeypatch,
                          {"agendamento_id": 555, "telefone": "(11) 98888-7777"})
    consulta = [p for s, p in conn.executados if "regexp_replace" in s][0]
    assert consulta[1] == "11988887777"


def test_telefone_que_nao_bate_da_404(cliente, monkeypatch, avisos):
    """Não pode cancelar agendamento de outra pessoa sabendo só o id."""
    status, _, conn = cancelar(cliente, monkeypatch,
                               {"agendamento_id": 555, "telefone": "11900000000"},
                               achou=False)
    assert status == 404
    assert not conn.cancelou()
    assert conn.commits == 0
    assert avisos["telegram"] == []


@pytest.mark.parametrize("dados", [
    {},
    {"agendamento_id": 555},
    {"telefone": "11988887777"},
    {"agendamento_id": 555, "telefone": "   "},
])
def test_cancelamento_sem_dados_da_400(cliente, monkeypatch, avisos, dados):
    status, _, conn = cancelar(cliente, monkeypatch, dados)
    assert status == 400
    assert conn.commits == 0
