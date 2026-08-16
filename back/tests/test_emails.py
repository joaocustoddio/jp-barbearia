"""
Testes do email do cliente (confirmação + lembrete 1h antes) e do link de agenda.

Nada aqui abre conexão SMTP: sem as variáveis de ambiente o módulo já é no-op,
e no teste da tarefa o envio é trocado por uma função que só anota o que sairia.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import app
import emails


# ------------------------------------------------------- comportamento seguro

def test_sem_configuracao_nao_envia():
    assert emails.configurado() is False
    assert emails.enviar("a@b.com", "oi", "<p>oi</p>", "oi") is False


def test_nao_envia_sem_destinatario(monkeypatch):
    monkeypatch.setattr(emails, "SMTP_HOST", "smtp.teste")
    monkeypatch.setattr(emails, "REMETENTE", "contato@teste.com")
    assert emails.configurado() is True
    assert emails.enviar("", "oi", "<p>oi</p>", "oi") is False       # sem e-mail, nem tenta


# --------------------------------------------------------- link do calendário

def test_link_de_agenda_usa_utc_e_duracao():
    inicio = datetime(2026, 8, 25, 15, 0, tzinfo=app.FUSO_BR)        # 15:00 em Brasília
    link = emails.link_google_agenda("Degradê", inicio.astimezone(timezone.utc),
                                     (inicio + timedelta(minutes=40)).astimezone(timezone.utc))
    datas = parse_qs(urlparse(link).query)["dates"][0]
    # 15:00 BRT = 18:00 UTC; fim 40min depois
    assert datas == "20260825T180000Z/20260825T184000Z"


def test_helper_do_app_monta_o_link_completo():
    link = app._link_agenda("Degradê", "Rian", "2026-08-25", "15:00", 40)
    parametros = parse_qs(urlparse(link).query)
    assert link.startswith("https://calendar.google.com/calendar/render")
    assert "Degradê" in parametros["text"][0]
    assert "Rian" in parametros["details"][0]
    assert parametros["dates"][0] == "20260825T180000Z/20260825T184000Z"


def test_link_de_agenda_com_hora_invalida_nao_quebra():
    assert app._link_agenda("Degradê", "Rian", "2026-08-25", "abc", 40) == ""


# ----------------------------------------------------------- conteúdo do email

def test_email_de_confirmacao_tem_dados_e_botao():
    assunto, html, texto = emails.email_confirmacao(
        "João Silva", "Degradê", "Rian", "25/08/2026", "15:00",
        preco=40.0, link_agenda="https://calendar.google.com/x")
    assert "25/08/2026" in assunto and "15:00" in assunto
    for pedaco in ("João", "Degradê", "Rian", "15:00", "R$ 40,00"):
        assert pedaco in html
    assert "Adicionar à minha agenda" in html
    assert "https://calendar.google.com/x" in html
    assert "Degradê" in texto                       # versão sem HTML também tem o essencial


def test_email_de_lembrete_fala_do_horario():
    assunto, html, texto = emails.email_lembrete("Ana Lima", "Barba", "JP", "25/08/2026", "09:30")
    assert "09:30" in assunto
    assert "Ana" in html and "09:30" in html
    assert "09:30" in texto


# ------------------------------------------- tarefa de lembrete (1h antes)

class _CursorFalso:
    def __init__(self, linhas): self._linhas = list(linhas)
    def fetchall(self): return self._linhas
    def fetchone(self): return self._linhas[0] if self._linhas else None


class _ConexaoFalsa:
    """Devolve os candidatos e anota quais foram marcados como avisados."""
    def __init__(self, candidatos):
        self.candidatos = candidatos
        self.marcados = []
        self.commitou = False

    def execute(self, sql, params=None):
        consulta = " ".join(sql.split()).lower()
        if consulta.startswith("update agendamentos set lembrete_enviado_em"):
            self.marcados.append(params[0])
            return _CursorFalso([])
        return _CursorFalso(self.candidatos)

    def commit(self): self.commitou = True
    def close(self): pass


def _preparar(monkeypatch, candidatos, agora_hhmm="14:00"):
    enviados = []
    conexao = _ConexaoFalsa(candidatos)
    monkeypatch.setattr(app.emails, "configurado", lambda: True)
    monkeypatch.setattr(app.emails, "enviar",
                        lambda destino, assunto, html, texto, esperar=False:
                        enviados.append(destino) or True)
    monkeypatch.setattr(app, "get_connection", lambda: conexao)
    hora, minuto = (int(p) for p in agora_hhmm.split(":"))
    agora = app.agora_br().replace(hour=hora, minute=minuto)
    monkeypatch.setattr(app, "agora_br", lambda: agora)
    return enviados, conexao


def _candidato(id_, hora, email="cliente@teste.com"):
    return {"id": id_, "hora": hora, "cliente": "João Silva", "email": email,
            "servico": "Degradê", "barbeiro": "Rian"}


def test_avisa_quem_esta_dentro_da_janela(monkeypatch):
    # Agora 14:00, antecedência 60min → avisa quem começa entre 14:00 e 15:00.
    enviados, conexao = _preparar(monkeypatch, [
        _candidato(1, "14:30"),      # dentro
        _candidato(2, "15:00"),      # limite exato, dentro
        _candidato(3, "16:00"),      # ainda longe
        _candidato(4, "13:00"),      # já passou
    ])
    resultado = app._enviar_lembretes_de_clientes()
    assert resultado["enviados"] == 2
    assert conexao.marcados == [1, 2]        # só os avisados são marcados
    assert len(enviados) == 2
    assert conexao.commitou


def test_nao_reenvia_para_quem_ja_foi_marcado(monkeypatch):
    # A consulta já filtra lembrete_enviado_em IS NULL; aqui garantimos que
    # sem candidatos nada é enviado.
    enviados, conexao = _preparar(monkeypatch, [])
    assert app._enviar_lembretes_de_clientes()["enviados"] == 0
    assert enviados == [] and conexao.marcados == []


def test_hora_invalida_nao_derruba_a_tarefa(monkeypatch):
    enviados, _ = _preparar(monkeypatch, [_candidato(1, "sem-hora"), _candidato(2, "14:30")])
    assert app._enviar_lembretes_de_clientes()["enviados"] == 1
    assert len(enviados) == 1


def test_sem_email_configurado_a_tarefa_avisa_e_nao_quebra(monkeypatch):
    monkeypatch.setattr(app.emails, "configurado", lambda: False)
    resultado = app._enviar_lembretes_de_clientes()
    assert resultado["enviados"] == 0 and "erro" in resultado
