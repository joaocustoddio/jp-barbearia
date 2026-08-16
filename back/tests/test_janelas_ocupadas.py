"""
Testes de `app.janelas_ocupadas` — a ponte entre o banco e o motor de horários.

É aqui que mora o risco que gerou o bug antigo: o almoço e os bloqueios eram
considerados na LISTA de horários livres, mas não na hora de agendar. Como
agora as duas coisas passam por esta função, basta garantir que ela transforma
tudo (agendamento, almoço fixo, almoço manual, bloqueio) em janela.

Usa uma conexão FALSA, então não precisa de banco pra rodar.
"""
import app


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchall(self):
        return self._linhas

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class ConexaoFalsa:
    """Responde às três consultas que janelas_ocupadas faz, olhando o SQL."""

    def __init__(self, agendamentos=(), bloqueios=(), almoco_fixo=None):
        self.agendamentos = agendamentos
        self.bloqueios = bloqueios
        self.almoco_fixo = almoco_fixo

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        if "from agendamentos" in consulta:
            return _Cursor(self.agendamentos)
        if "from bloqueios" in consulta:
            return _Cursor(self.bloqueios)
        if "almoco_fixo from barbeiros" in consulta:
            return _Cursor([{"almoco_fixo": self.almoco_fixo}])
        raise AssertionError("consulta inesperada: %s" % consulta)


def janelas(**kwargs):
    return app.janelas_ocupadas(ConexaoFalsa(**kwargs), "2099-01-01", 1, 30)


def test_agendamento_vira_janela_com_a_duracao_do_servico():
    ocupadas, dia_bloqueado = janelas(
        agendamentos=[{"hora": "09:00", "duracao_min": 40}]
    )
    assert dia_bloqueado is False
    assert (ocupadas[0].inicio, ocupadas[0].fim, ocupadas[0].motivo) == (540, 580, "agendamento")


def test_agendamento_sem_duracao_usa_o_padrao():
    ocupadas, _ = janelas(agendamentos=[{"hora": "09:00", "duracao_min": None}])
    assert ocupadas[0].fim == 570        # 540 + duracao_padrao (30)


def test_almoco_fixo_vira_janela_de_almoco():
    ocupadas, _ = janelas(almoco_fixo="12:30")
    assert (ocupadas[0].inicio, ocupadas[0].motivo) == (750, "almoco")
    assert ocupadas[0].fim == 750 + app.DURACAO_ALMOCO_MIN


def test_almoco_manual_e_reconhecido_como_almoco():
    # o almoço manual é gravado na tabela de bloqueios com motivo 'Almoço'
    ocupadas, _ = janelas(
        bloqueios=[{"hora": "13:00", "duracao_min": 60, "motivo": "Almoço"}]
    )
    assert ocupadas[0].motivo == "almoco"


def test_bloqueio_comum_e_marcado_como_bloqueio():
    ocupadas, _ = janelas(
        bloqueios=[{"hora": "15:00", "duracao_min": None, "motivo": "Dentista"}]
    )
    assert ocupadas[0].motivo == "bloqueio"
    assert ocupadas[0].fim == 900 + app.INTERVALO_MINUTOS   # sem duração = 1 slot


def test_bloqueio_de_dia_inteiro_derruba_a_agenda():
    ocupadas, dia_bloqueado = janelas(
        bloqueios=[{"hora": None, "duracao_min": None, "motivo": "Feriado"}]
    )
    assert dia_bloqueado is True
    assert ocupadas == []


def test_tudo_junto_no_mesmo_dia():
    ocupadas, dia_bloqueado = janelas(
        agendamentos=[{"hora": "09:00", "duracao_min": 40}],
        bloqueios=[{"hora": "16:00", "duracao_min": 30, "motivo": "Dentista"}],
        almoco_fixo="12:30",
    )
    assert dia_bloqueado is False
    assert sorted(j.motivo for j in ocupadas) == ["agendamento", "almoco", "bloqueio"]
