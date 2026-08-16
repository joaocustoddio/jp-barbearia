"""
notificacoes.py
---------------
Avisos para a equipe da barbearia via Telegram (bot gratuito).

Duas garantias importantes:

1. SILENCIOSO SE NÃO CONFIGURADO — sem TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no
   .env, tudo vira no-op. O sistema roda igual, só não avisa.
2. NUNCA DERRUBA O FLUXO — o envio acontece numa thread separada e qualquer
   erro (Telegram fora do ar, token errado, internet lenta) só vira log. Um
   agendamento JAMAIS falha por causa de notificação.

Como configurar (leva ~2 minutos):
  1. No Telegram, fale com o @BotFather e mande /newbot. Ele devolve um TOKEN.
  2. Crie um grupo com os barbeiros e adicione o bot nele.
  3. Mande qualquer mensagem no grupo e abra no navegador:
     https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
     Procure "chat":{"id":-100...} — esse número é o CHAT_ID (grupo costuma
     ser negativo).
  4. Coloque TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nas variáveis do Render.
"""

import json
import logging
import os
import threading
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("jpbarbearia.notificacoes")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TIMEOUT_SEGUNDOS = 8


def configurado():
    """True se dá pra enviar (token e chat definidos no .env)."""
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _enviar_agora(mensagem, chat_id=None, botoes=None):
    url = "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_TOKEN
    dados = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if botoes:
        # Botões clicáveis embaixo da mensagem (ex: "Chamar no WhatsApp").
        dados["reply_markup"] = {"inline_keyboard": [
            [{"text": texto, "url": link}] for texto, link in botoes if link
        ]}
    requisicao = urllib.request.Request(
        url, data=json.dumps(dados).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(requisicao, timeout=TIMEOUT_SEGUNDOS) as resposta:
        return resposta.status


def enviar(mensagem, esperar=False, chat_id=None, botoes=None):
    """
    Manda um aviso no Telegram. Por padrão não bloqueia a requisição
    (o cliente não fica esperando o Telegram responder).

    chat_id: pra quem enviar. Sem isso, vai pro grupo geral (TELEGRAM_CHAT_ID).
             É assim que cada barbeiro recebe só o que é dele.
    botoes:  lista de (texto, link) que vira botão embaixo da mensagem.

    Devolve False se nem tentou (não configurado / sem destino).
    """
    if not TELEGRAM_TOKEN or not (chat_id or TELEGRAM_CHAT_ID):
        return False

    def tarefa():
        try:
            _enviar_agora(mensagem, chat_id, botoes)
        except Exception as erro:                      # nunca propaga
            logger.warning("Não consegui avisar no Telegram: %s", erro)

    if esperar:
        tarefa()
    else:
        threading.Thread(target=tarefa, daemon=True).start()
    return True


def listar_conversas():
    """
    Conversas recentes que o bot enxerga (getUpdates), pro master escolher o
    canal de cada barbeiro sem precisar mexer em URL. Devolve lista de
    {id, nome, tipo}. Só aparece quem já mandou mensagem pro bot.
    """
    if not TELEGRAM_TOKEN:
        return []
    url = "https://api.telegram.org/bot%s/getUpdates" % TELEGRAM_TOKEN
    with urllib.request.urlopen(url, timeout=TIMEOUT_SEGUNDOS) as resposta:
        dados = json.load(resposta)
    conversas = {}
    for item in dados.get("result", []):
        mensagem = item.get("message") or item.get("my_chat_member") or {}
        chat = mensagem.get("chat") or {}
        if not chat.get("id"):
            continue
        nome = chat.get("title") or " ".join(
            p for p in [chat.get("first_name"), chat.get("last_name")] if p
        ) or chat.get("username") or "(sem nome)"
        conversas[chat["id"]] = {"id": chat["id"], "nome": nome, "tipo": chat.get("type")}
    return list(conversas.values())


# -------------------------------------------------------
# Mensagens prontas (formatação num lugar só)
# -------------------------------------------------------

def link_whatsapp(telefone, mensagem):
    """Monta o link wa.me com a mensagem pronta. '' se não tiver telefone."""
    numero = "".join(c for c in (telefone or "") if c.isdigit())
    if not numero:
        return ""
    if len(numero) <= 11:                              # adiciona o DDI do Brasil
        numero = "55" + numero
    return "https://wa.me/%s?text=%s" % (numero, urllib.parse.quote(mensagem))


def texto_novo_agendamento(cliente, servico, barbeiro, data_br, hora, telefone=None):
    linhas = [
        "🗓️ <b>Novo agendamento</b>",
        "",
        "👤 %s" % cliente,
        "✂️ %s" % servico,
        "💈 %s" % barbeiro,
        "📅 %s às %s" % (data_br, hora),
    ]
    if telefone:
        linhas.append("📱 %s" % telefone)
    return "\n".join(linhas)


def texto_cancelamento(cliente, servico, barbeiro, data_br, hora):
    return "\n".join([
        "❌ <b>Agendamento cancelado</b>",
        "",
        "👤 %s" % cliente,
        "✂️ %s" % servico,
        "💈 %s" % barbeiro,
        "📅 %s às %s" % (data_br, hora),
        "",
        "O horário voltou a ficar livre.",
    ])


def texto_lembretes(data_br, agendamentos):
    """
    Lista de amanhã com um link de WhatsApp pronto por cliente: o barbeiro só
    toca no nome, o WhatsApp abre com a mensagem escrita e ele dá enviar.
    `agendamentos`: dicts com cliente, telefone, servico, barbeiro, hora.
    """
    if not agendamentos:
        return "😴 <b>Amanhã (%s)</b>\n\nNenhum agendamento até agora." % data_br

    linhas = ["📋 <b>Lembretes de amanhã (%s)</b>" % data_br,
              "Toque no nome pra abrir o WhatsApp já com a mensagem pronta.", ""]
    for item in sorted(agendamentos, key=lambda a: (a["hora"], a["barbeiro"])):
        mensagem = (
            "*JP BARBEARIA*\n"
            "Olá, %s! Passando pra lembrar do seu horário amanhã (%s) às %s "
            "com o %s. Se não puder vir, avise a gente com antecedência. 👊"
            % (item["cliente"].split()[0], data_br, item["hora"], item["barbeiro"])
        )
        link = link_whatsapp(item.get("telefone"), mensagem)
        nome = ('<a href="%s">%s</a>' % (link, item["cliente"])) if link else (
            "%s (sem telefone)" % item["cliente"])
        linhas.append("🕐 <b>%s</b> — %s · %s · %s" % (
            item["hora"], nome, item["servico"], item["barbeiro"]))
    linhas.append("")
    linhas.append("Total: %d agendamento(s)." % len(agendamentos))
    return "\n".join(linhas)


def texto_resumo_do_dia(data_br, agendamentos):
    """Bom-dia com a agenda de hoje."""
    if not agendamentos:
        return "☀️ <b>Hoje (%s)</b>\n\nNenhum agendamento marcado." % data_br
    por_barbeiro = {}
    for item in agendamentos:
        por_barbeiro.setdefault(item["barbeiro"], []).append(item)
    linhas = ["☀️ <b>Agenda de hoje (%s)</b>" % data_br, ""]
    for barbeiro in sorted(por_barbeiro):
        itens = sorted(por_barbeiro[barbeiro], key=lambda a: a["hora"])
        horas = ", ".join(i["hora"] for i in itens)
        linhas.append("💈 <b>%s</b> — %d corte(s)" % (barbeiro, len(itens)))
        linhas.append("   %s" % horas)
    linhas.append("")
    linhas.append("Total: %d agendamento(s)." % len(agendamentos))
    return "\n".join(linhas)


def avisar_novo_agendamento(cliente, servico, barbeiro, data_br, hora, telefone=None,
                            chat_id=None):
    """
    'Fulano acabou de agendar...' — disparado quando o cliente marca pelo site.
    Vai com um botão pra chamar o cliente no WhatsApp direto da mensagem.
    """
    mensagem_wpp = (
        "*JP BARBEARIA*\n"
        "Olá, %s! Aqui é da JP Barbearia, sobre seu horário de %s no dia %s às %s."
        % ((cliente or "").split()[0] if cliente else "tudo bem", servico, data_br, hora)
    )
    botoes = [("💬 Chamar no WhatsApp", link_whatsapp(telefone, mensagem_wpp))]
    return enviar(
        texto_novo_agendamento(cliente, servico, barbeiro, data_br, hora, telefone),
        chat_id=chat_id, botoes=botoes,
    )


def avisar_cancelamento(cliente, servico, barbeiro, data_br, hora, chat_id=None):
    """Cliente cancelou pelo site — o horário voltou a ficar livre."""
    return enviar(texto_cancelamento(cliente, servico, barbeiro, data_br, hora),
                  chat_id=chat_id)
