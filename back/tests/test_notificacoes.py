"""
Testes dos avisos da equipe (Telegram) e da tarefa agendada.

Nada aqui toca a internet: sem TELEGRAM_BOT_TOKEN no ambiente o módulo já é
no-op, e no teste do endpoint o envio é substituído por uma função que só
guarda o texto.
"""
import json

import pytest

import app
import notificacoes


# ------------------------------------------------------- comportamento seguro

def test_sem_configuracao_e_no_op():
    # Ambiente de teste não tem token: nada é enviado e nada explode.
    assert notificacoes.configurado() is False
    assert notificacoes.enviar("oi") is False
    assert notificacoes.avisar_novo_agendamento("Zé", "Barba", "JP", "25/08/2026", "15:00") is False
    assert notificacoes.avisar_cancelamento("Zé", "Barba", "JP", "25/08/2026", "15:00") is False


# ----------------------------------------------------------- link do WhatsApp

def test_link_whatsapp_adiciona_ddi_e_limpa_o_numero():
    link = notificacoes.link_whatsapp("(11) 98888-7777", "oi")
    assert link.startswith("https://wa.me/5511988887777?text=")

def test_link_whatsapp_nao_duplica_ddi():
    assert notificacoes.link_whatsapp("5511988887777", "oi").startswith("https://wa.me/5511988887777")

def test_link_whatsapp_vazio_sem_telefone():
    assert notificacoes.link_whatsapp("", "oi") == ""
    assert notificacoes.link_whatsapp(None, "oi") == ""

def test_link_whatsapp_escapa_a_mensagem():
    assert " " not in notificacoes.link_whatsapp("11988887777", "bom dia")


# --------------------------------------------------------------- textos

def test_texto_de_novo_agendamento_tem_o_essencial():
    texto = notificacoes.texto_novo_agendamento(
        "João Silva", "Degradê", "Rian", "25/08/2026", "15:00", "11988887777")
    for pedaco in ("Novo agendamento", "João Silva", "Degradê", "Rian", "25/08/2026", "15:00"):
        assert pedaco in texto

def test_texto_de_cancelamento_avisa_que_liberou():
    texto = notificacoes.texto_cancelamento("João", "Barba", "JP", "25/08/2026", "10:00")
    assert "cancelado" in texto.lower()
    assert "livre" in texto.lower()

def test_lembretes_sem_ninguem():
    assert "Nenhum agendamento" in notificacoes.texto_lembretes("25/08/2026", [])

def test_lembretes_trazem_link_pronto_e_ordenam_por_hora():
    agendamentos = [
        {"hora": "16:00", "cliente": "Bruno Souza", "telefone": "11977776666",
         "servico": "Barba", "barbeiro": "Rian"},
        {"hora": "09:30", "cliente": "Ana Lima", "telefone": "11988887777",
         "servico": "Degradê", "barbeiro": "JP"},
    ]
    texto = notificacoes.texto_lembretes("25/08/2026", agendamentos)
    assert texto.index("Ana Lima") < texto.index("Bruno Souza")     # ordenado por hora
    assert "https://wa.me/5511988887777" in texto                   # link pronto
    assert "Total: 2" in texto

def test_lembrete_sem_telefone_nao_vira_link():
    texto = notificacoes.texto_lembretes("25/08/2026", [
        {"hora": "10:00", "cliente": "Sem Fone", "telefone": None,
         "servico": "Barba", "barbeiro": "JP"}])
    assert "sem telefone" in texto
    assert "wa.me" not in texto

def test_resumo_do_dia_agrupa_por_barbeiro():
    agendamentos = [
        {"hora": "09:00", "cliente": "A", "telefone": None, "servico": "Barba", "barbeiro": "JP"},
        {"hora": "10:00", "cliente": "B", "telefone": None, "servico": "Barba", "barbeiro": "JP"},
        {"hora": "11:00", "cliente": "C", "telefone": None, "servico": "Barba", "barbeiro": "Rian"},
    ]
    texto = notificacoes.texto_resumo_do_dia("25/08/2026", agendamentos)
    assert "JP</b> — 2 corte(s)" in texto
    assert "Rian</b> — 1 corte(s)" in texto
    assert "Total: 3" in texto


# ------------------------------------------------- endpoint da tarefa agendada

@pytest.fixture
def tarefa(monkeypatch):
    """Endpoint pronto pra rodar: token válido, Telegram fingido, agenda falsa."""
    enviados = []   # [(chat_id, texto)]
    monkeypatch.setattr(app, "TAREFAS_TOKEN", "segredo-de-teste")
    monkeypatch.setattr(app.notificacoes, "configurado", lambda: True)
    monkeypatch.setattr(app.notificacoes, "TELEGRAM_CHAT_ID", "-100grupo")
    monkeypatch.setattr(app.notificacoes, "enviar",
                        lambda texto, esperar=False, chat_id=None, botoes=None:
                        enviados.append((chat_id, texto)) or True)
    monkeypatch.setattr(app, "_canais_dos_barbeiros", lambda: [])
    monkeypatch.setattr(app, "_agendamentos_do_dia", lambda data: [
        {"hora": "15:00", "cliente": "João Silva", "telefone": "11988887777",
         "servico": "Degradê", "barbeiro": "Rian", "barbeiro_id": 2}])
    return app.app.test_client(), enviados


def test_tarefa_exige_token(tarefa):
    cliente, _ = tarefa
    assert cliente.post("/api/tarefas/avisos").status_code == 401
    assert cliente.post("/api/tarefas/avisos?token=errado").status_code == 401


def test_tarefa_de_lembretes_manda_a_agenda_de_amanha(tarefa):
    cliente, enviados = tarefa
    resposta = cliente.post("/api/tarefas/avisos?token=segredo-de-teste&tipo=lembretes")
    corpo = json.loads(resposta.data)
    assert resposta.status_code == 200
    assert corpo["tipo"] == "lembretes" and corpo["agendamentos"] == 1 and corpo["enviado"]
    assert corpo["data"] == (app.data_hoje() + app.timedelta(days=1)).isoformat()
    assert "Lembretes de amanhã" in enviados[0][1]
    assert "wa.me" in enviados[0][1]


def test_tarefa_de_resumo_usa_o_dia_de_hoje(tarefa):
    cliente, enviados = tarefa
    corpo = json.loads(cliente.post("/api/tarefas/avisos?token=segredo-de-teste&tipo=resumo").data)
    assert corpo["data"] == app.data_hoje().isoformat()
    assert "Agenda de hoje" in enviados[0][1]


def test_cada_barbeiro_recebe_so_a_agenda_dele(tarefa, monkeypatch):
    """
    O ponto do canal por barbeiro: o Rian não pode receber a montoeira de
    avisos do Gabriel. O grupo geral continua vendo tudo (visão do dono).
    """
    cliente, enviados = tarefa
    monkeypatch.setattr(app, "_canais_dos_barbeiros",
                        lambda: [(2, "Rian", "111"), (3, "Gabriel", "222")])
    monkeypatch.setattr(app, "_agendamentos_do_dia", lambda data: [
        {"hora": "09:00", "cliente": "Ana", "telefone": None, "servico": "Barba",
         "barbeiro": "Rian", "barbeiro_id": 2},
        {"hora": "10:00", "cliente": "Bruno", "telefone": None, "servico": "Degradê",
         "barbeiro": "Gabriel", "barbeiro_id": 3},
    ])
    corpo = json.loads(cliente.post("/api/tarefas/avisos?token=segredo-de-teste&tipo=resumo").data)
    assert corpo["mensagens"] == 3           # grupo geral + 2 barbeiros
    por_chat = dict(enviados)

    # O grupo geral enxerga os dois barbeiros...
    assert "Rian" in por_chat["-100grupo"] and "Gabriel" in por_chat["-100grupo"]
    # ...mas cada barbeiro só vê a própria agenda (nome e horário dele, não do outro).
    assert "Rian" in por_chat["111"] and "09:00" in por_chat["111"]
    assert "Gabriel" not in por_chat["111"] and "10:00" not in por_chat["111"]
    assert "Gabriel" in por_chat["222"] and "10:00" in por_chat["222"]
    assert "Rian" not in por_chat["222"] and "09:00" not in por_chat["222"]


def test_sem_canal_proprio_tudo_vai_pro_grupo(tarefa):
    cliente, enviados = tarefa
    cliente.post("/api/tarefas/avisos?token=segredo-de-teste&tipo=resumo")
    assert len(enviados) == 1
    assert enviados[0][0] == "-100grupo"


def test_tarefa_aceita_token_por_header(tarefa):
    cliente, _ = tarefa
    resposta = cliente.post("/api/tarefas/avisos",
                            headers={"X-Tarefa-Token": "segredo-de-teste"})
    assert resposta.status_code == 200
