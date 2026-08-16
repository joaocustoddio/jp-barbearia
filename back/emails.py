"""
emails.py
---------
Emails automáticos para o CLIENTE: confirmação do agendamento e lembrete
1 hora antes.

Usa SMTP da biblioteca padrão do Python — nenhuma dependência nova e nenhum
provedor amarrado. Funciona com qualquer serviço que dê SMTP (Brevo, Resend,
Mailersend, Zoho...). O plano gratuito do Brevo (300 emails/dia) cobre bem uma
barbearia.

Mesmas garantias do notificacoes.py:
- SEM CONFIGURAÇÃO = NO-OP. Sem as variáveis SMTP no .env, nada é enviado e o
  sistema roda igual.
- NUNCA DERRUBA O FLUXO. Envio em thread separada; erro vira log, jamais faz um
  agendamento falhar.

Configuração (.env / Render):
  EMAIL_SMTP_HOST=smtp-relay.brevo.com
  EMAIL_SMTP_PORTA=587
  EMAIL_SMTP_USUARIO=...        (login que o provedor te dá)
  EMAIL_SMTP_SENHA=...          (chave SMTP)
  EMAIL_REMETENTE=contato@seudominio.com.br
  EMAIL_REMETENTE_NOME=JP Barbearia

Dica de entrega: use um domínio próprio com SPF/DKIM configurados no provedor.
Enviando de um endereço genérico, boa parte dos emails cai em spam.
"""

import json
import logging
import os
import smtplib
import threading
import time
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("jpbarbearia.emails")

# Caminho preferido: API HTTP (porta 443). Hospedagens como o Render BLOQUEIAM
# as portas de SMTP (25/465/587) pra evitar spam saindo da plataforma, então
# SMTP simplesmente dá timeout lá. A API resolve isso e ainda é mais rápida.
API_KEY = os.getenv("EMAIL_API_KEY", "").strip()
API_URL = os.getenv("EMAIL_API_URL", "https://api.brevo.com/v3/smtp/email").strip()

# Caminho alternativo: SMTP (útil em hospedagem que não bloqueia a porta).
SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "").strip()
SMTP_PORTA = int(os.getenv("EMAIL_SMTP_PORTA", "587"))
SMTP_USUARIO = os.getenv("EMAIL_SMTP_USUARIO", "").strip()
SMTP_SENHA = os.getenv("EMAIL_SMTP_SENHA", "").strip()

REMETENTE = os.getenv("EMAIL_REMETENTE", "").strip()
REMETENTE_NOME = os.getenv("EMAIL_REMETENTE_NOME", "JP Barbearia").strip()
TIMEOUT_SEGUNDOS = 15


def metodo():
    """Como os emails serão enviados: 'api', 'smtp' ou None (desligado)."""
    if not REMETENTE:
        return None
    if API_KEY:
        return "api"
    if SMTP_HOST:
        return "smtp"
    return None


def configurado():
    """True se dá pra enviar email."""
    return metodo() is not None


def _enviar_por_api(destino, assunto, corpo_html, corpo_texto):
    corpo = json.dumps({
        "sender": {"name": REMETENTE_NOME, "email": REMETENTE},
        "to": [{"email": destino}],
        "subject": assunto,
        "htmlContent": corpo_html,
        "textContent": corpo_texto,
    }).encode("utf-8")
    requisicao = urllib.request.Request(API_URL, data=corpo, headers={
        "api-key": API_KEY,
        "Content-Type": "application/json",
        "accept": "application/json",
    })
    with urllib.request.urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
        return resposta.status


def _enviar_por_smtp(destino, assunto, corpo_html, corpo_texto):
    mensagem = EmailMessage()
    mensagem["From"] = formataddr((REMETENTE_NOME, REMETENTE))
    mensagem["To"] = destino
    mensagem["Subject"] = assunto
    mensagem.set_content(corpo_texto)                      # versão simples
    mensagem.add_alternative(corpo_html, subtype="html")   # versão bonita

    with smtplib.SMTP(SMTP_HOST, SMTP_PORTA, timeout=TIMEOUT_SEGUNDOS) as servidor:
        servidor.starttls()
        if SMTP_USUARIO:
            servidor.login(SMTP_USUARIO, SMTP_SENHA)
        servidor.send_message(mensagem)


def _enviar_agora(destino, assunto, corpo_html, corpo_texto):
    if metodo() == "api":
        return _enviar_por_api(destino, assunto, corpo_html, corpo_texto)
    return _enviar_por_smtp(destino, assunto, corpo_html, corpo_texto)


def testar(destino):
    """
    Tenta enviar um email de teste AGORA (sem thread) e devolve
    (sucesso, mensagem_de_erro). Serve pra diagnosticar configuração: em vez de
    o erro sumir num log, ele volta na resposta.
    """
    try:
        _enviar_agora(
            destino,
            "Teste de configuração — JP Barbearia",
            "<p style='font-family:Arial'>Deu certo! 👊<br>"
            "Se você recebeu isso, o envio de e-mails está funcionando.</p>",
            "Deu certo! Se você recebeu isso, o envio de e-mails está funcionando.",
        )
        return True, None
    except Exception as erro:
        return False, _descrever_erro(erro)


def _descrever_erro(erro):
    """
    Mensagem de erro útil. Em falha da API o motivo real vem no CORPO da
    resposta (ex: remetente não verificado) — sem isso ficaria só
    'HTTP Error 400: Bad Request', que não ajuda ninguém.
    """
    detalhe = ""
    corpo = getattr(erro, "read", None)
    if callable(corpo):
        try:
            detalhe = " | resposta: %s" % corpo().decode("utf-8", "replace")[:300]
        except Exception:
            pass
    return "%s: %s%s" % (type(erro).__name__, erro, detalhe)


def diagnostico():
    """Como o servidor está configurado (sem expor chave nem senha)."""
    return {
        "configurado": configurado(),
        "metodo": metodo(),
        "api_key_definida": bool(API_KEY),
        "api_url": API_URL if API_KEY else None,
        "host": SMTP_HOST or None,
        "porta": SMTP_PORTA,
        "usuario": SMTP_USUARIO or None,
        "remetente": REMETENTE or None,
        "remetente_nome": REMETENTE_NOME or None,
        "senha_definida": bool(SMTP_SENHA),
    }


# Só um alerta a cada 30 min: se 20 emails falharem seguidos, o grupo recebe
# UM aviso, não vinte.
INTERVALO_ALERTA_SEGUNDOS = 30 * 60
_ultimo_alerta = 0.0


def _alertar_equipe(destino, erro):
    """
    Avisa a equipe no Telegram quando um email falha. Sem isso, o lembrete do
    cliente pararia em silêncio (chave expirada, cota estourada...) e a
    barbearia só descobriria pelo cliente reclamando.
    """
    global _ultimo_alerta
    agora = time.time()
    if agora - _ultimo_alerta < INTERVALO_ALERTA_SEGUNDOS:
        return
    _ultimo_alerta = agora
    try:
        import notificacoes
        notificacoes.enviar(
            "⚠️ <b>Falha ao enviar e-mail</b>\n\n"
            "Não consegui mandar o e-mail para <code>%s</code>.\n\n"
            "<b>Motivo:</b> %s\n\n"
            "Os lembretes automáticos podem estar parados — vale conferir a "
            "configuração de e-mail." % (destino, _descrever_erro(erro)[:300])
        )
    except Exception:
        pass                                               # alerta nunca quebra nada


def enviar(destino, assunto, corpo_html, corpo_texto, esperar=False):
    """
    Manda um email. Por padrão não bloqueia a requisição.
    Devolve False se nem tentou (não configurado ou sem destinatário).
    """
    if not configurado() or not destino:
        return False

    def tarefa():
        try:
            _enviar_agora(destino, assunto, corpo_html, corpo_texto)
        except Exception as erro:                          # nunca propaga
            logger.warning("Falha ao enviar email para %s: %s", destino, erro)
            _alertar_equipe(destino, erro)

    if esperar:
        tarefa()
    else:
        threading.Thread(target=tarefa, daemon=True).start()
    return True


# -------------------------------------------------------
# Conteúdo dos emails
# -------------------------------------------------------

_ACENTO = "#f5a623"
_FUNDO = "#0a0c11"


def _moldura(titulo, saudacao, blocos, rodape, botao=None):
    """Molde HTML simples (email não aceita CSS moderno — tudo inline)."""
    linhas_html = "".join(
        '<tr><td style="padding:4px 0;color:#9aa1b1;font-size:14px;">%s</td>'
        '<td style="padding:4px 0;color:#f3f4f7;font-size:14px;font-weight:600;">%s</td></tr>'
        % (rotulo, valor) for rotulo, valor in blocos
    )
    html_botao = ""
    if botao:
        texto_botao, link_botao = botao
        html_botao = (
            '<div style="margin:22px 0 4px;">'
            '<a href="%s" style="background:%s;color:#14100a;text-decoration:none;'
            'font-weight:700;font-size:15px;padding:13px 22px;border-radius:8px;'
            'display:inline-block;">%s</a></div>' % (link_botao, _ACENTO, texto_botao)
        )
    return """<!doctype html>
<html><body style="margin:0;padding:24px;background:%s;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#171a24;border-radius:14px;padding:28px;">
    <h1 style="margin:0 0 6px;color:%s;font-size:20px;">JP BARBEARIA</h1>
    <h2 style="margin:0 0 18px;color:#f3f4f7;font-size:17px;">%s</h2>
    <p style="color:#f3f4f7;font-size:15px;margin:0 0 16px;">%s</p>
    <table style="width:100%%;border-collapse:collapse;">%s</table>
    %s
    <p style="color:#626a7a;font-size:12px;margin:22px 0 0;line-height:1.5;">%s</p>
  </div>
</body></html>""" % (_FUNDO, _ACENTO, titulo, saudacao, linhas_html, html_botao, rodape)


def _texto_simples(saudacao, blocos, rodape):
    linhas = [saudacao, ""]
    linhas += ["%s %s" % (rotulo, valor) for rotulo, valor in blocos]
    linhas += ["", rodape]
    return "\n".join(linhas)


def link_google_agenda(titulo, inicio_utc, fim_utc, detalhes="", local=""):
    """
    Link que abre o Google Agenda já preenchido. É o lembrete mais eficaz e
    gratuito que existe: o alarme passa a ser do próprio celular do cliente.
    inicio_utc/fim_utc: datetime em UTC.
    """
    from urllib.parse import urlencode
    formato = "%Y%m%dT%H%M%SZ"
    parametros = {
        "action": "TEMPLATE",
        "text": titulo,
        "dates": "%s/%s" % (inicio_utc.strftime(formato), fim_utc.strftime(formato)),
        "details": detalhes,
        "location": local,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(parametros)


def email_confirmacao(cliente, servico, barbeiro, data_br, hora, preco=None,
                      link_agenda=None, endereco=""):
    """Confirmação enviada na hora em que o cliente agenda."""
    primeiro_nome = (cliente or "").split()[0] if cliente else "tudo bem"
    blocos = [("Serviço", servico), ("Profissional", barbeiro),
              ("Data", data_br), ("Horário", hora)]
    if preco is not None:
        blocos.append(("Valor", "R$ %s" % ("%.2f" % float(preco)).replace(".", ",")))
    saudacao = "Fala, %s! Seu horário está confirmado. 👊" % primeiro_nome
    rodape = ("Se não puder vir, avise a gente com antecedência — assim liberamos "
              "o horário pra outra pessoa." + (("<br>%s" % endereco) if endereco else ""))
    botao = ("Adicionar à minha agenda", link_agenda) if link_agenda else None
    html = _moldura("Agendamento confirmado", saudacao, blocos, rodape, botao)
    texto = _texto_simples(saudacao, blocos,
                           "Se não puder vir, avise a gente com antecedência."
                           + (("\n\nAdicionar à agenda: %s" % link_agenda) if link_agenda else ""))
    return ("Agendamento confirmado — %s às %s" % (data_br, hora), html, texto)


def email_lembrete(cliente, servico, barbeiro, data_br, hora, endereco=""):
    """Lembrete disparado automaticamente cerca de 1 hora antes."""
    primeiro_nome = (cliente or "").split()[0] if cliente else "tudo bem"
    blocos = [("Serviço", servico), ("Profissional", barbeiro), ("Horário", hora)]
    saudacao = "Fala, %s! Seu horário é daqui a pouco, às %s. Te esperamos! ✂️" % (
        primeiro_nome, hora)
    rodape = ("Não vai conseguir vir? Avise a gente o quanto antes."
              + (("<br>%s" % endereco) if endereco else ""))
    html = _moldura("Seu horário é logo mais", saudacao, blocos, rodape)
    texto = _texto_simples(saudacao, blocos, "Não vai conseguir vir? Avise a gente.")
    return ("Lembrete: seu horário hoje às %s" % hora, html, texto)
