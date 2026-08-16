"""
app.py (versão produção)
------------------------
Mudanças em relação à versão anterior:
- debug e secret_key vêm do .env, nunca do código
- CORS restrito ao domínio configurado no .env
- Validações mais robustas em todas as rotas
- Tratamento de erro global (500 não vaza detalhes internos)
- Logs mais informativos
"""

import os
import re
import hmac
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
import bcrypt
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from database import (
    get_connection, init_db, criar_admin_padrao, criar_salao_padrao,
    criar_barbeiros_padrao, ajustar_servicos, fechar_conexoes_pendentes,
)
from horarios import (
    Janela, hhmm_para_min, gerar_slots, cabe_no_expediente,
    primeiro_conflito, filtrar_por_antecedencia, mensagem_de_conflito,
)
import notificacoes
import emails

# Carrega as variáveis do arquivo .env
# Se não existir o .env, usa os valores padrão definidos abaixo
load_dotenv()

app = Flask(__name__)

# -------------------------------------------------------
# CONFIGURAÇÕES VIA VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------
SECRET_KEY_PADRAO = "dev-inseguro-troque-em-producao"
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", SECRET_KEY_PADRAO)

FLASK_ENV      = os.getenv("FLASK_ENV", "development")
CORS_ORIGIN    = os.getenv("CORS_ORIGIN", "*")
EM_PRODUCAO    = FLASK_ENV == "production"

# Em produção, não deixa subir com a SECRET_KEY insegura padrão.
if EM_PRODUCAO and app.config["SECRET_KEY"] == SECRET_KEY_PADRAO:
    raise RuntimeError(
        "SECRET_KEY insegura em produção. Defina SECRET_KEY (string longa e "
        "aleatória) no .env antes de rodar com FLASK_ENV=production."
    )

# Em produção, CORS liberado pra qualquer origem é inseguro. Aqui a gente só
# AVISA (não derruba o boot, pra não quebrar deploy) — o ideal é setar o domínio
# real do front em CORS_ORIGIN. O aviso aparece nos logs até ser corrigido.
_avisar_cors = EM_PRODUCAO and CORS_ORIGIN == "*"

# Fuso de Brasília fixo (UTC-3). O Brasil não tem horário de verão desde 2019,
# então o offset é constante — assim a antecedência e os horários não dependem
# do TZ do servidor (se a env TZ sumir, nada desloca). Reveja se o DST voltar.
FUSO_BR = timezone(timedelta(hours=-3))


def agora_br():
    """Agora no fuso de Brasília (datetime ciente de fuso)."""
    return datetime.now(FUSO_BR)


def data_hoje():
    """Data de hoje no fuso de Brasília."""
    return agora_br().date()

HORARIO_ABERTURA      = os.getenv("HORARIO_ABERTURA", "09:00")
HORARIO_FECHAMENTO    = os.getenv("HORARIO_FECHAMENTO", "20:00")
INTERVALO_MINUTOS     = int(os.getenv("INTERVALO_MINUTOS", "30"))

# Horário de funcionamento ESPECIAL por dia da semana (weekday(): 0=seg..6=dom).
# (abertura, fechamento). Dias não listados usam HORARIO_ABERTURA/FECHAMENTO.
# Sexta (4): 08:20–19:30 | Sábado (5): 08:00–19:00 | domingo (6) é fechado.
HORARIOS_ESPECIAIS = {
    4: ("08:20", "20:00"),
    5: ("08:00", "19:00"),
}

# Antecedência mínima para agendamento (em minutos) = o "cooldown".
# Exemplo: 30 = o cliente precisa agendar com pelo menos 30min de antecedência.
# Mude o valor no .env (ANTECEDENCIA_MINIMA_MINUTOS) quando quiser ajustar —
# esse "30" aqui só é usado se a variável não existir no .env.
ANTECEDENCIA_MINIMA   = int(os.getenv("ANTECEDENCIA_MINIMA_MINUTOS", "15"))

# Duração do almoço (fixo ou manual), em minutos.
DURACAO_ALMOCO_MIN    = int(os.getenv("DURACAO_ALMOCO_MINUTOS", "60"))

# Endereço da barbearia — aparece no email e no evento do calendário do cliente.
ENDERECO_BARBEARIA    = os.getenv("ENDERECO_BARBEARIA", "").strip()

# "Última entrada": quantos minutos o serviço pode passar do fechamento, pra não
# perder o último corte do dia por 10 minutinhos. Ex: 10 = um Degradê (40min)
# pode começar 19:30 e terminar 20:10. Vale só no fim do dia — nunca invade o
# almoço nem o próximo cliente. Coloque 0 pra ninguém passar do horário.
TOLERANCIA_FECHAMENTO_MIN = int(os.getenv("TOLERANCIA_FECHAMENTO_MINUTOS", "10"))

# Última entrada do dia = fechamento menos esta margem. Impede alguém de entrar
# em cima da hora de fechar. Ex: 10 → fecha 20:00, última entrada 19:50
# (no sábado, que fecha 19:00, vira 18:50).
MARGEM_ULTIMA_ENTRADA_MIN = int(os.getenv("MARGEM_ULTIMA_ENTRADA_MINUTOS", "10"))

# A tolerância acima só vale pra serviços a partir desta duração. Faz sentido
# esticar o fim do dia por um Degradê (senão o horário se perde inteiro), mas
# não por uma Barba de 20min — essa termina até a hora de fechar.
TOLERANCIA_DURACAO_MINIMA_MIN = int(os.getenv("TOLERANCIA_DURACAO_MINIMA_MINUTOS", "30"))


def tolerancia_para(duracao):
    """Quantos minutos ESTE serviço pode passar do fechamento (0 pros curtos)."""
    return TOLERANCIA_FECHAMENTO_MIN if (duracao or 0) >= TOLERANCIA_DURACAO_MINIMA_MIN else 0

# Janela máxima de agendamento pelo site (em dias). O cliente só pode marcar
# de hoje até hoje + esse limite. Padrão 7 (1 semana). Vale só pro fluxo público;
# o barbeiro pelo painel (walk-in / repetir) não fica preso a esse limite.
LIMITE_DIAS_AGENDAMENTO = int(os.getenv("LIMITE_DIAS_AGENDAMENTO", "7"))

# Dias da semana em que a barbearia NÃO atende.
# Numeração do Python (date.weekday()): 0=segunda, 1=terça, ..., 5=sábado, 6=domingo.
# Padrão: "6" (domingo). Configure no .env (DIAS_FECHADOS) como lista separada
# por vírgula — ex: "5,6" fecha sábado e domingo.
_dias_fechados_raw = os.getenv("DIAS_FECHADOS", "6")
DIAS_FECHADOS = {int(d.strip()) for d in _dias_fechados_raw.split(",") if d.strip() != ""}

# Nomes dos dias na ordem do weekday() do Python (pra montar mensagens de erro).
NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

# Barbeiro "dono" (JP). Tem tratamento especial: é o único que entra antes das
# 9h nos dias de abertura antecipada (sex/sáb) e tem folga semanal. Os demais
# barbeiros só começam a partir de PISO_ABERTURA_OUTROS.
BARBEIRO_DONO_ID       = int(os.getenv("BARBEIRO_DONO_ID", "1"))
PISO_ABERTURA_OUTROS   = os.getenv("PISO_ABERTURA_OUTROS", "09:00")
# Folga semanal do dono (weekday do Python: 0=seg..6=dom). Vazio = sem folga.
_folga_dono_raw        = os.getenv("FOLGA_DONO_WEEKDAY", "0")
FOLGA_DONO_WEEKDAY     = int(_folga_dono_raw) if _folga_dono_raw.strip() != "" else None

# CORS: em produção trava no domínio configurado (CORS_ORIGIN); em
# desenvolvimento libera geral, pra rodar o front local (localhost) sem
# esbarrar em CORS. Assim o valor de CORS_ORIGIN no .env local é irrelevante.
CORS(app, origins=[CORS_ORIGIN] if EM_PRODUCAO else "*")

# Rate limiting: protege o login de força bruta. Sem limite global (só onde a
# gente marcar com @limiter.limit). Storage em memória — suficiente pra 1 serviço;
# se um dia escalar pra vários processos/instâncias, apontar pra um Redis.
limiter = Limiter(key_func=get_remote_address, app=app)

# -------------------------------------------------------
# TRATAMENTO DE ERRO GLOBAL
# Em produção, erros internos não revelam detalhes do servidor
# -------------------------------------------------------
@app.errorhandler(404)
def nao_encontrado(e):
    return jsonify({"erro": "Rota não encontrada"}), 404

@app.errorhandler(405)
def metodo_nao_permitido(e):
    return jsonify({"erro": "Método não permitido"}), 405

@app.errorhandler(429)
def limite_excedido(e):
    return jsonify({"erro": "Muitas tentativas. Aguarde um minuto e tente de novo."}), 429

@app.errorhandler(500)
def erro_interno(e):
    # Em desenvolvimento mostra o erro; em produção esconde
    if EM_PRODUCAO:
        return jsonify({"erro": "Erro interno no servidor"}), 500
    return jsonify({"erro": str(e)}), 500


# -------------------------------------------------------
# OBSERVABILIDADE — logging + captura de erros + saúde
# -------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jpbarbearia")

if _avisar_cors:
    logger.warning("CORS_ORIGIN=* em produção — defina o domínio do front pra travar o acesso.")

# Sentry é OPCIONAL: só liga se SENTRY_DSN estiver no .env e o pacote instalado.
# (pra ativar: pip install "sentry-sdk[flask]" e definir SENTRY_DSN)
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_sentry_dsn, environment=FLASK_ENV)
        logger.info("Sentry ativado.")
    except Exception as exc:  # pacote ausente ou DSN inválido não derruba o app
        logger.warning("SENTRY_DSN definido mas o Sentry não iniciou: %s", exc)


@app.teardown_request
def _fechar_conexoes(_exc):
    # Rede de segurança: garante que nenhuma conexão de banco fique pendurada,
    # mesmo se o endpoint estourou exceção antes do conn.close().
    fechar_conexoes_pendentes()


@app.errorhandler(Exception)
def _erro_nao_tratado(e):
    # Loga exceções não previstas (com stack) antes de esconder o detalhe em prod.
    # Erros HTTP (404/405/403…) passam direto pros handlers específicos.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    logger.exception("Erro não tratado")
    if EM_PRODUCAO:
        return jsonify({"erro": "Erro interno no servidor"}), 500
    return jsonify({"erro": str(e)}), 500


@app.route("/", methods=["GET"])
def health():
    """Health check simples (o Render usa pra saber que o serviço está de pé)."""
    return jsonify({"status": "ok", "servico": "jp-barbearia"})


# -------------------------------------------------------
# FUNÇÕES DE VALIDAÇÃO
# Separadas das rotas para manter o código organizado.
# Cada uma retorna (True, None) se válido,
# ou (False, "mensagem de erro") se inválido.
# -------------------------------------------------------

def validar_nome(nome):
    if not nome or not isinstance(nome, str):
        return False, "Nome é obrigatório"
    nome = nome.strip()
    if len(nome) < 2:
        return False, "Nome muito curto"
    if len(nome) > 100:
        return False, "Nome muito longo (máximo 100 caracteres)"
    return True, None

def validar_telefone(telefone):
    # Telefone é opcional — só valida se vier preenchido
    if not telefone:
        return True, None
    telefone = re.sub(r"\D", "", telefone)  # remove tudo que não é número
    if len(telefone) < 10 or len(telefone) > 11:
        return False, "Telefone inválido (use DDD + número)"
    return True, None

def validar_email(email):
    # Email é opcional aqui — só valida o FORMATO quando vier preenchido.
    # A obrigatoriedade (site do cliente sim, caderninho não) fica na rota.
    if not email:
        return True, None
    email = email.strip()
    if len(email) > 120:
        return False, "E-mail muito longo"
    if not re.match(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$", email):
        return False, "E-mail inválido"
    return True, None

def validar_data(data_str):
    if not data_str:
        return False, "Data é obrigatória"
    try:
        data = datetime.strptime(data_str, "%Y-%m-%d").date()
    except ValueError:
        return False, "Formato de data inválido (use YYYY-MM-DD)"
    if data < data_hoje():
        return False, "Não é possível agendar em datas passadas"
    return True, None

def dia_fechado(data_str):
    """
    Diz se a data cai num dia sem atendimento (ex: domingo).
    Retorna (True, "domingo") se for fechado, ou (False, None) se atende.
    Assume que data_str já passou por validar_data (formato válido).
    """
    try:
        data = datetime.strptime(data_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, None
    if data.weekday() in DIAS_FECHADOS:
        return True, NOMES_DIAS[data.weekday()]
    return False, None

def horario_do_dia(data_str):
    """(abertura, fechamento) da data conforme o dia da semana (ver HORARIOS_ESPECIAIS)."""
    try:
        wd = datetime.strptime(data_str, "%Y-%m-%d").date().weekday()
    except (ValueError, TypeError):
        wd = None
    return HORARIOS_ESPECIAIS.get(wd, (HORARIO_ABERTURA, HORARIO_FECHAMENTO))

def horario_efetivo(data_str, barbeiro_id, conn):
    """(abertura, fechamento) que valem pra ESTE barbeiro nesta data.

    Ordem de prioridade:
    1. Expediente manual do master pro dia (sobrepõe tudo);
    2. Folga semanal do dono → devolve abertura==fechamento (nenhum horário);
    3. Piso de abertura dos demais barbeiros (só o dono entra antes das 9h);
    4. Horário padrão do dia.
    """
    row = conn.execute(
        "SELECT inicio, fim FROM expedientes WHERE barbeiro_id = %s AND data = %s",
        (barbeiro_id, data_str)
    ).fetchone()
    if row:
        return row["inicio"], row["fim"]

    abertura, fechamento = horario_do_dia(data_str)
    try:
        wd = datetime.strptime(data_str, "%Y-%m-%d").date().weekday()
    except (ValueError, TypeError):
        wd = None
    eh_dono = int(barbeiro_id) == BARBEIRO_DONO_ID

    # Folga semanal do dono: fecha o dia pra ele (abertura == fechamento → 0 slots).
    if eh_dono and FOLGA_DONO_WEEKDAY is not None and wd == FOLGA_DONO_WEEKDAY:
        return abertura, abertura

    # Só o dono entra antes do piso (ex: 09:00). Os demais começam no piso.
    if not eh_dono and hhmm_para_min(abertura) < hhmm_para_min(PISO_ABERTURA_OUTROS):
        abertura = PISO_ABERTURA_OUTROS

    return abertura, fechamento

def validar_hora(hora_str, data_str=None):
    if not hora_str:
        return False, "Hora é obrigatória"
    try:
        datetime.strptime(hora_str, "%H:%M")
    except ValueError:
        return False, "Formato de hora inválido (use HH:MM)"

    # Se for hoje, valida antecedência mínima
    if data_str and data_str == data_hoje().isoformat():
        agora = agora_br()
        agora_min = agora.hour * 60 + agora.minute
        hora_min  = int(hora_str.split(":")[0]) * 60 + int(hora_str.split(":")[1])

        if hora_min <= agora_min + ANTECEDENCIA_MINIMA:
            horas = ANTECEDENCIA_MINIMA // 60
            minutos = ANTECEDENCIA_MINIMA % 60
            return False, f"Agendamentos devem ser feitos com pelo menos {horas}h{minutos:02d}min de antecedência"

    return True, None

def validar_servico_id(servico_id, conn):
    if not servico_id:
        return False, "Serviço é obrigatório"
    try:
        servico_id = int(servico_id)
    except (TypeError, ValueError):
        return False, "ID de serviço inválido"
    existe = conn.execute(
        "SELECT id FROM servicos WHERE id = %s", (servico_id,)
    ).fetchone()
    if not existe:
        return False, "Serviço não encontrado"
    return True, None


# -------------------------------------------------------
# ROTAS DA API
# -------------------------------------------------------

@app.route("/api/servicos", methods=["GET"])
def listar_servicos():
    conn = get_connection()
    servicos = conn.execute("SELECT * FROM servicos").fetchall()
    conn.close()
    return jsonify([dict(s) for s in servicos])


@app.route("/api/barbeiros", methods=["GET"])
def listar_barbeiros():
    """Retorna os barbeiros ativos. Usado pro cliente escolher no chat."""
    conn = get_connection()
    barbeiros = conn.execute(
        "SELECT id, nome, foto FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in barbeiros])


@app.route("/api/horarios-disponiveis", methods=["GET"])
def horarios_disponiveis():
    """
    Agora filtra por barbeiro_id também.
    Cada barbeiro tem sua agenda independente — um horário
    ocupado com o Barbeiro 1 continua livre pro Barbeiro 2.

    Parâmetros: ?data=YYYY-MM-DD&barbeiro_id=1
    """
    data_str     = request.args.get("data")
    barbeiro_id  = request.args.get("barbeiro_id")
    servico_id   = request.args.get("servico_id")

    valido, erro = validar_data(data_str)
    if not valido:
        return jsonify({"erro": erro}), 400

    if not barbeiro_id:
        return jsonify({"erro": "barbeiro_id é obrigatório"}), 400

    # Dia sem atendimento (ex: domingo): devolve lista vazia com status 200.
    fechado, _ = dia_fechado(data_str)
    if fechado:
        return jsonify({"data": data_str, "barbeiro_id": barbeiro_id,
                        "horarios_disponiveis": [], "fechado": True})

    conn = get_connection()

    # Duração do serviço escolhido = PASSO dos slots. Sem servico_id, usa o padrão.
    dur_serv = INTERVALO_MINUTOS
    if servico_id:
        srow = conn.execute("SELECT duracao_min FROM servicos WHERE id = %s", (servico_id,)).fetchone()
        if srow:
            dur_serv = srow["duracao_min"]

    # Horário efetivo do barbeiro no dia (expediente/piso/folga).
    abertura, fechamento = horario_efetivo(data_str, barbeiro_id, conn)
    ocupadas, dia_bloqueado = janelas_ocupadas(conn, data_str, barbeiro_id, dur_serv)
    conn.close()

    if dia_bloqueado:
        return jsonify({"data": data_str, "barbeiro_id": barbeiro_id,
                        "horarios_disponiveis": [], "bloqueio_dia": True})

    # Horários gerados de forma DINÂMICA: encaixam nas brechas livres começando
    # onde o corte anterior terminou (packing), sem desperdiçar espaço.
    disponiveis = gerar_slots(abertura, fechamento, ocupadas, dur_serv,
                              tolerancia_para(dur_serv), MARGEM_ULTIMA_ENTRADA_MIN)

    # Se for hoje, tira o que já passou ou está dentro da antecedência mínima.
    if data_str == data_hoje().isoformat():
        agora = agora_br()
        disponiveis = filtrar_por_antecedencia(
            disponiveis, agora.hour * 60 + agora.minute, ANTECEDENCIA_MINIMA
        )

    return jsonify({"data": data_str, "barbeiro_id": barbeiro_id, "horarios_disponiveis": disponiveis})


def janelas_ocupadas(conn, data_str, barbeiro_id, duracao_padrao):
    """
    TUDO que impede um atendimento neste dia/barbeiro, como janelas de minutos:
    agendamentos ativos + bloqueios (globais e do barbeiro, incluindo o almoço
    manual) + almoço fixo.

    Esta é a FONTE ÚNICA da verdade: tanto a lista de horários livres quanto a
    validação na hora de agendar chamam esta função. É o que garante que o que
    aparece na tela é exatamente o que o sistema aceita — sem "vejo mas não
    marco", e sem marcar em cima do almoço.

    Retorna (lista de Janela, dia_bloqueado) — dia_bloqueado quando existe um
    bloqueio de dia inteiro (hora NULL), que derruba a agenda toda.
    """
    janelas = []

    agendados = conn.execute(
        """SELECT agendamentos.hora, servicos.duracao_min
           FROM agendamentos
           JOIN servicos ON agendamentos.servico_id = servicos.id
           WHERE agendamentos.data = %s AND agendamentos.barbeiro_id = %s
             AND agendamentos.status != 'cancelado'""",
        (data_str, barbeiro_id)
    ).fetchall()
    for linha in agendados:
        inicio = hhmm_para_min(linha["hora"])
        janelas.append(Janela(inicio, inicio + (linha["duracao_min"] or duracao_padrao), "agendamento"))

    dia_bloqueado = False
    bloqueios = conn.execute(
        """SELECT hora, duracao_min, motivo FROM bloqueios
           WHERE data = %s AND (barbeiro_id IS NULL OR barbeiro_id = %s)""",
        (data_str, barbeiro_id)
    ).fetchall()
    for linha in bloqueios:
        if linha["hora"] is None:            # bloqueio de dia inteiro (feriado/folga)
            dia_bloqueado = True
            continue
        inicio = hhmm_para_min(linha["hora"])
        # O almoço manual é gravado como bloqueio com motivo 'Almoço' — separar
        # o motivo deixa a mensagem de erro específica pro barbeiro.
        motivo = "almoco" if (linha["motivo"] or "").strip().lower().startswith("almo") else "bloqueio"
        janelas.append(Janela(inicio, inicio + (linha["duracao_min"] or INTERVALO_MINUTOS), motivo))

    linha_barbeiro = conn.execute(
        "SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    almoco_fixo = linha_barbeiro["almoco_fixo"] if linha_barbeiro else None
    if almoco_fixo:
        inicio = hhmm_para_min(almoco_fixo)
        janelas.append(Janela(inicio, inicio + DURACAO_ALMOCO_MIN, "almoco"))

    return janelas, dia_bloqueado


def _data_br(data_iso):
    """'2026-08-25' -> '25/08/2026' (pras mensagens)."""
    try:
        ano, mes, dia = data_iso.split("-")
        return "%s/%s/%s" % (dia, mes, ano)
    except (ValueError, AttributeError):
        return data_iso


def _link_agenda(servico, barbeiro, data_iso, hora, duracao_min):
    """
    Link 'Adicionar à minha agenda' (Google Agenda). O Google espera os horários
    em UTC, então convertemos do fuso de Brasília.
    """
    try:
        inicio_local = datetime.strptime("%s %s" % (data_iso, hora[:5]), "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""
    inicio_local = inicio_local.replace(tzinfo=FUSO_BR)
    fim_local = inicio_local + timedelta(minutes=int(duracao_min or INTERVALO_MINUTOS))
    return emails.link_google_agenda(
        titulo="%s — JP Barbearia" % servico,
        inicio_utc=inicio_local.astimezone(timezone.utc),
        fim_utc=fim_local.astimezone(timezone.utc),
        detalhes="Profissional: %s" % barbeiro,
        local=ENDERECO_BARBEARIA,
    )


def _processar_novo_agendamento(dados, exigir_antecedencia, exigir_telefone=False,
                                permitir_conflito=False, avisar_equipe=False,
                                exigir_email=False):
    """
    Valida e insere um novo agendamento. Compartilhado entre duas rotas:
    - PÚBLICA (cliente pelo site): exigir_antecedencia=True, exigir_telefone=True
      (não dá pra agendar "em cima da hora" e o telefone é obrigatório)
    - ADMIN (barbeiro registrando walk-in): ambos False
      (registra corte de agora e o telefone é opcional)

    permitir_conflito=True libera sobrescrever/encaixar em cima de outro horário
    (o barbeiro pelo painel pode; o cliente pelo site NÃO).
    dados["encaixe"] marca o agendamento como encaixe (caderninho).

    Retorna (corpo_json, status_http).
    """
    conn = get_connection()

    # Valida barbeiro_id (precisa existir e estar ativo)
    barbeiro_id = dados.get("barbeiro_id")
    if not barbeiro_id:
        conn.close()
        return {"erro": "Barbeiro é obrigatório"}, 400
    barbeiro = conn.execute(
        "SELECT id FROM barbeiros WHERE id = %s AND ativo = 1", (barbeiro_id,)
    ).fetchone()
    if not barbeiro:
        conn.close()
        return {"erro": "Barbeiro não encontrado"}, 400

    # Telefone e e-mail são obrigatórios só no fluxo público (cliente pelo site).
    # No caderninho do barbeiro os dois são opcionais — o cliente está na cadeira,
    # não faz sentido travar o registro por causa de contato.
    if exigir_telefone and not (dados.get("telefone") or "").strip():
        conn.close()
        return {"erro": "Telefone é obrigatório"}, 400
    if exigir_email and not (dados.get("email") or "").strip():
        conn.close()
        return {"erro": "E-mail é obrigatório (é nele que enviamos a confirmação e o lembrete)"}, 400

    # Valida cada campo. A antecedência só é checada quando exigir_antecedencia
    # for True (passando a data pro validar_hora ativa a regra). O validar_telefone
    # só confere o FORMATO quando vier preenchido (a obrigatoriedade é acima).
    data_para_hora = dados.get("data") if exigir_antecedencia else None
    checks = [
        validar_nome(dados.get("nome_cliente")),
        validar_telefone(dados.get("telefone")),
        validar_email(dados.get("email")),
        validar_data(dados.get("data")),
        validar_hora(dados.get("hora"), data_para_hora),
        validar_servico_id(dados.get("servico_id"), conn),
    ]
    for valido, erro in checks:
        if not valido:
            conn.close()
            return {"erro": erro}, 400

    # Bloqueia dias sem atendimento (ex: domingo)
    fechado, nome_dia = dia_fechado(dados.get("data"))
    if fechado:
        conn.close()
        return {"erro": f"Não realizamos agendamentos aos {nome_dia}s."}, 400

    # Janela máxima (só no fluxo público): não deixa marcar além de X dias.
    if exigir_antecedencia:
        data_ag = datetime.strptime(dados["data"], "%Y-%m-%d").date()
        if data_ag > data_hoje() + timedelta(days=LIMITE_DIAS_AGENDAMENTO):
            conn.close()
            return {"erro": f"Só é possível agendar até {LIMITE_DIAS_AGENDAMENTO} dias à frente."}, 400

    # Verifica se o horário está mesmo livre PARA ESTE barbeiro (só quando não é
    # permitido sobrescrever — o barbeiro pelo painel/caderninho pode encaixar em
    # cima). Usa a MESMA base da lista de horários livres (janelas_ocupadas), então
    # o que o cliente vê na tela é exatamente o que o sistema aceita.
    if not permitir_conflito:
        srow = conn.execute(
            "SELECT duracao_min FROM servicos WHERE id = %s", (dados["servico_id"],)
        ).fetchone()
        dur_novo = srow["duracao_min"] if srow else INTERVALO_MINUTOS
        inicio_novo = hhmm_para_min(dados["hora"])

        # Precisa caber no expediente do barbeiro nesse dia (horário do dia + ajuste).
        ab, fe = horario_efetivo(dados["data"], barbeiro_id, conn)
        if not cabe_no_expediente(inicio_novo, dur_novo, ab, fe,
                                  tolerancia_para(dur_novo), MARGEM_ULTIMA_ENTRADA_MIN):
            conn.close()
            return {"erro": "Horário fora do expediente do barbeiro nesse dia."}, 400

        # Trava a linha do barbeiro até o commit: se dois clientes tentarem o
        # mesmo horário no mesmo instante, um espera o outro terminar em vez de
        # os dois passarem na checagem e gerarem agendamento duplicado.
        conn.execute("SELECT id FROM barbeiros WHERE id = %s FOR UPDATE", (barbeiro_id,))

        ocupadas, dia_bloqueado = janelas_ocupadas(conn, dados["data"], barbeiro_id, dur_novo)
        if dia_bloqueado:
            conn.close()
            return {"erro": "A agenda está bloqueada nesse dia. Escolha outra data."}, 400

        conflito = primeiro_conflito(inicio_novo, dur_novo, ocupadas)
        if conflito is not None:
            conn.close()
            return {"erro": mensagem_de_conflito(conflito)}, 409

    # Salva cliente + agendamento (RETURNING id — Postgres não tem lastrowid)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO clientes (nome, telefone, email) VALUES (%s, %s, %s) RETURNING id",
        (dados["nome_cliente"].strip(), dados.get("telefone") or None,
         (dados.get("email") or "").strip() or None)
    )
    cliente_id = cursor.fetchone()["id"]
    cursor.execute(
        """INSERT INTO agendamentos (cliente_id, servico_id, barbeiro_id, data, hora, status, encaixe)
           VALUES (%s, %s, %s, %s, %s, 'confirmado', %s) RETURNING id""",
        (cliente_id, int(dados["servico_id"]), int(barbeiro_id), dados["data"], dados["hora"],
         bool(dados.get("encaixe")))
    )
    agendamento_id = cursor.fetchone()["id"]
    conn.commit()

    # Avisa a equipe no Telegram e manda a confirmação pro cliente. Só no fluxo
    # público — quando é o próprio barbeiro anotando no caderninho não faz sentido.
    resposta = {"mensagem": "Agendamento criado com sucesso!", "agendamento_id": agendamento_id}
    if avisar_equipe:
        detalhes = conn.execute(
            """SELECT servicos.nome AS servico, servicos.preco, servicos.duracao_min,
                      barbeiros.nome AS barbeiro
               FROM servicos, barbeiros WHERE servicos.id = %s AND barbeiros.id = %s""",
            (int(dados["servico_id"]), int(barbeiro_id))
        ).fetchone()
        if detalhes:
            data_br = _data_br(dados["data"])
            notificacoes.avisar_novo_agendamento(
                cliente=dados["nome_cliente"].strip(), servico=detalhes["servico"],
                barbeiro=detalhes["barbeiro"], data_br=data_br, hora=dados["hora"],
                telefone=dados.get("telefone"),
            )
            # Link do Google Agenda: o lembrete passa a ser o alarme do próprio
            # celular do cliente. Devolvido pro front mostrar o botão também.
            link_agenda = _link_agenda(
                detalhes["servico"], detalhes["barbeiro"], dados["data"],
                dados["hora"], detalhes["duracao_min"]
            )
            resposta["link_agenda"] = link_agenda
            email_cliente = (dados.get("email") or "").strip()
            if email_cliente:
                assunto, html, texto = emails.email_confirmacao(
                    cliente=dados["nome_cliente"].strip(), servico=detalhes["servico"],
                    barbeiro=detalhes["barbeiro"], data_br=data_br, hora=dados["hora"],
                    preco=detalhes["preco"], link_agenda=link_agenda,
                    endereco=ENDERECO_BARBEARIA,
                )
                emails.enviar(email_cliente, assunto, html, texto)
    conn.close()

    return resposta, 201


@app.route("/api/agendamentos", methods=["POST"])
def criar_agendamento():
    """Agendamento pelo cliente (site público) — exige antecedência e telefone."""
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400
    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=True, exigir_telefone=True, avisar_equipe=True,
        exigir_email=True
    )
    return jsonify(corpo), status


@app.route("/api/agendamentos/consultar", methods=["GET"])
def consultar_agendamentos_cliente():
    """Público: lista os agendamentos FUTUROS (não cancelados) de um telefone,
    pro cliente poder cancelar pelo site. ?telefone=..."""
    telefone = (request.args.get("telefone") or "").strip()
    if not telefone:
        return jsonify({"erro": "Informe o telefone"}), 400
    valido, erro = validar_telefone(telefone)
    if not valido:
        return jsonify({"erro": erro}), 400
    tel_num = re.sub(r"\D", "", telefone)
    hoje = data_hoje().isoformat()
    conn = get_connection()
    rows = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                   servicos.nome  AS servico_nome,
                   barbeiros.nome AS barbeiro_nome
            FROM agendamentos
            JOIN clientes  ON agendamentos.cliente_id  = clientes.id
            JOIN servicos  ON agendamentos.servico_id  = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
              AND agendamentos.status != 'cancelado'
              AND agendamentos.data >= %s
            ORDER BY agendamentos.data, agendamentos.hora""",
        (tel_num, hoje)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/agendamentos/cancelar", methods=["POST"])
def cancelar_agendamento_cliente():
    """Público: cliente cancela o próprio agendamento (confere o telefone).
    Corpo: { agendamento_id, telefone }."""
    dados = request.get_json(silent=True) or {}
    agendamento_id = dados.get("agendamento_id")
    telefone = (dados.get("telefone") or "").strip()
    if not agendamento_id or not telefone:
        return jsonify({"erro": "Agendamento e telefone são obrigatórios"}), 400
    tel_num = re.sub(r"\D", "", telefone)
    conn = get_connection()
    row = conn.execute(
        r"""SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                   clientes.nome AS cliente, servicos.nome AS servico,
                   barbeiros.nome AS barbeiro
            FROM agendamentos
            JOIN clientes ON agendamentos.cliente_id = clientes.id
            JOIN servicos ON agendamentos.servico_id = servicos.id
            JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE agendamentos.id = %s
              AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') = %s
              AND agendamentos.status != 'cancelado'""",
        (agendamento_id, tel_num)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado para esse telefone"}), 404
    conn.execute("UPDATE agendamentos SET status = 'cancelado' WHERE id = %s", (agendamento_id,))
    conn.commit()
    conn.close()

    notificacoes.avisar_cancelamento(
        cliente=row["cliente"], servico=row["servico"], barbeiro=row["barbeiro"],
        data_br=_data_br(row["data"]), hora=row["hora"],
    )
    return jsonify({"mensagem": "Agendamento cancelado com sucesso."})


@app.route("/api/agendamentos", methods=["GET"])
def listar_agendamentos():
    data_filtro = request.args.get("data")

    # Valida data se vier como filtro
    if data_filtro:
        valido, erro = validar_data(data_filtro)
        if not valido:
            return jsonify({"erro": erro}), 400

    conn = get_connection()
    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            clientes.nome  AS cliente_nome,
            clientes.telefone AS cliente_telefone,
            servicos.nome  AS servico_nome,
            servicos.preco AS servico_preco
        FROM agendamentos
        JOIN clientes ON agendamentos.cliente_id = clientes.id
        JOIN servicos ON agendamentos.servico_id = servicos.id
    """
    parametros = ()
    if data_filtro:
        query += " WHERE agendamentos.data = %s"
        parametros = (data_filtro,)

    query += " ORDER BY agendamentos.data, agendamentos.hora"
    resultados = conn.execute(query, parametros).fetchall()
    conn.close()

    return jsonify([dict(r) for r in resultados])


@app.route("/api/relatorio", methods=["GET"])
def relatorio():
    conn = get_connection()

    total = conn.execute(
        "SELECT COUNT(*) as total FROM agendamentos WHERE status != 'cancelado'"
    ).fetchone()["total"]

    faturamento = conn.execute("""
        SELECT COALESCE(SUM(servicos.preco), 0) as total
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
    """).fetchone()["total"]

    mais_pedido = conn.execute("""
        SELECT servicos.nome, COUNT(*) as quantidade
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
        GROUP BY servicos.id
        ORDER BY quantidade DESC
        LIMIT 1
    """).fetchone()

    conn.close()

    return jsonify({
        "total_agendamentos": total,
        "faturamento_estimado": faturamento,
        "servico_mais_pedido": dict(mais_pedido) if mais_pedido else None
    })


# -------------------------------------------------------
# AUTENTICAÇÃO — TOKEN JWT
#
# O que é JWT? É um "crachá digital" que o servidor gera
# quando o admin faz login. Nas próximas requisições, o
# frontend envia esse crachá no cabeçalho. O servidor
# verifica se é válido sem precisar consultar o banco
# a cada chamada.
#
# Fluxo:
# 1. Admin envia usuário + senha → POST /api/admin/login
# 2. Servidor confere no banco → gera token com validade
# 3. Frontend guarda o token (localStorage)
# 4. Toda rota protegida exige o token no header:
#    Authorization: Bearer <token>
# -------------------------------------------------------

def token_requerido(f):
    """
    Decorator: envolve qualquer rota que precise de login.
    Se o token não vier, for inválido ou expirado, bloqueia.
    Uso: @token_requerido acima da função da rota.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")

        # O token vem no formato: "Bearer eyJ0eXAiOiJ..."
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"erro": "Token de autenticação não fornecido"}), 401

        try:
            # Decodifica e valida o token usando a SECRET_KEY
            dados_token = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "Sessão expirada. Faça login novamente."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido"}), 401

        # Guarda o usuário logado (papel + barbeiro) pra rota usar no escopo.
        g.usuario = dados_token
        return f(*args, **kwargs)
    return decorated


def papel_atual():
    """Papel do usuário logado: 'master', 'barbeiro' ou 'salao'."""
    return getattr(g, "usuario", {}).get("papel")


def eh_master():
    """True se o usuário logado é o dono (papel master = vê tudo)."""
    return papel_atual() == "master"


def barbeiro_do_escopo():
    """
    Retorna None se o usuário enxerga TODOS os barbeiros (master ou salão),
    ou o barbeiro_id do usuário se ele for um barbeiro (enxerga só o dele).
    As rotas usam isso pra filtrar QUAIS agendamentos aparecem.
    """
    if papel_atual() in ("master", "salao"):
        return None
    return getattr(g, "usuario", {}).get("barbeiro_id")


def pode_ver_valores():
    """
    Quem pode ver DINHEIRO (faturamento, comissão, repasse):
    - master (tudo) e barbeiro (só o dele) → sim
    - salão (tablet compartilhado) → NÃO (só quantidade de cortes)
    """
    return papel_atual() != "salao"


def somente_master(f):
    """
    Decorator pra rotas que só o master pode acessar (gerenciar barbeiros,
    logins, bloqueios). Use SEMPRE abaixo de @token_requerido.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not eh_master():
            return jsonify({"erro": "Acesso restrito ao administrador."}), 403
        return f(*args, **kwargs)
    return decorated


# -------------------------------------------------------
# ROTAS DO PAINEL (protegidas)
# -------------------------------------------------------

@app.route("/api/admin/login", methods=["POST"])
@limiter.limit("10 per minute")
def admin_login():
    """
    Recebe { usuario, senha } e devolve um token JWT se válido.
    A senha é verificada com bcrypt (hash salgado, resistente a força bruta).
    Validade do token configurável via JWT_EXPIRACAO_HORAS no .env (padrão 8h).
    """
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Dados inválidos"}), 400

    usuario = dados.get("usuario", "").strip()
    senha   = dados.get("senha", "")

    if not usuario or not senha:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

    conn = get_connection()
    admin = conn.execute(
        """SELECT admin.id, admin.senha_hash, admin.papel, admin.barbeiro_id,
                  barbeiros.nome AS barbeiro_nome
           FROM admin
           LEFT JOIN barbeiros ON admin.barbeiro_id = barbeiros.id
           WHERE admin.usuario = %s""",
        (usuario,)
    ).fetchone()
    conn.close()

    # Verifica a senha com bcrypt. A mensagem de erro é a mesma pra usuário
    # inexistente e senha errada (não revela qual dos dois falhou).
    senha_confere = (
        admin is not None
        and bcrypt.checkpw(senha.encode(), admin["senha_hash"].encode())
    )
    if not senha_confere:
        return jsonify({"erro": "Usuário ou senha incorretos"}), 401

    horas = int(os.getenv("JWT_EXPIRACAO_HORAS", "8"))
    token = jwt.encode(
        {
            "admin_id": admin["id"],
            "papel": admin["papel"],
            "barbeiro_id": admin["barbeiro_id"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=horas)
        },
        app.config["SECRET_KEY"],
        algorithm="HS256"
    )

    # Devolve papel/barbeiro pro front adaptar a interface (master vs barbeiro).
    return jsonify({
        "token": token,
        "mensagem": "Login realizado com sucesso!",
        "papel": admin["papel"],
        "barbeiro_id": admin["barbeiro_id"],
        "barbeiro_nome": admin["barbeiro_nome"]
    })


@app.route("/api/admin/agendamentos", methods=["GET"])
@token_requerido
def painel_agendamentos():
    """
    Lista agendamentos com filtros opcionais.
    Parâmetros: ?data=YYYY-MM-DD  e/ou  ?status=confirmado
    Sem filtros: retorna todos os agendamentos.
    """
    data_filtro   = request.args.get("data")
    status_filtro = request.args.get("status")

    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            agendamentos.barbeiro_id,
            agendamentos.servico_id,
            agendamentos.encaixe,
            agendamentos.forma_pagamento,
            clientes.nome       AS cliente_nome,
            clientes.telefone   AS cliente_telefone,
            servicos.nome       AS servico_nome,
            servicos.preco      AS servico_preco,
            servicos.duracao_min AS servico_duracao,
            barbeiros.nome      AS barbeiro_nome
        FROM agendamentos
        JOIN clientes  ON agendamentos.cliente_id  = clientes.id
        JOIN servicos  ON agendamentos.servico_id  = servicos.id
        JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
        WHERE 1=1
    """
    parametros = []

    if data_filtro:
        query += " AND agendamentos.data = %s"
        parametros.append(data_filtro)

    if status_filtro:
        query += " AND agendamentos.status = %s"
        parametros.append(status_filtro)

    # Escopo: barbeiro vê só os agendamentos dele; master vê todos.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        query += " AND agendamentos.barbeiro_id = %s"
        parametros.append(escopo)

    query += " ORDER BY agendamentos.data, agendamentos.hora"

    conn = get_connection()
    resultados = conn.execute(query, parametros).fetchall()
    lista = [dict(r) for r in resultados]

    # Consumos (produtos) de cada agendamento: itens + total, pra mostrar no card.
    ids = [r["id"] for r in lista]
    consumos_por_ag = {}
    if ids:
        marcadores = ",".join(["%s"] * len(ids))
        cons = conn.execute(
            "SELECT id, agendamento_id, produto_id, descricao, valor_centavos, quantidade "
            "FROM consumos WHERE agendamento_id IN (%s) ORDER BY id" % marcadores, ids
        ).fetchall()
        for c in cons:
            consumos_por_ag.setdefault(c["agendamento_id"], []).append(dict(c))
    conn.close()

    # Salão (tablet compartilhado) não vê valores — remove preço e valores de consumo.
    ver_valores = pode_ver_valores()
    for r in lista:
        itens = consumos_por_ag.get(r["id"], [])
        r["consumos"] = itens
        r["consumos_total_centavos"] = sum(i["valor_centavos"] * i["quantidade"] for i in itens)
        if not ver_valores:
            r.pop("servico_preco", None)
            r.pop("consumos_total_centavos", None)
            for i in itens:
                i.pop("valor_centavos", None)

    return jsonify(lista)


@app.route("/api/admin/agendamentos", methods=["POST"])
@token_requerido
def criar_agendamento_admin():
    """
    Cria um agendamento pelo PAINEL (ex: barbeiro registrando um walk-in —
    cliente que chegou sem horário marcado). Reusa a mesma lógica da rota
    pública, mas SEM exigir antecedência mínima (o corte é agora/hoje) e
    PERMITINDO sobrescrever/encaixar em cima de outro horário (o barbeiro
    pode cortar mais rápido e encaixar outro corte no mesmo horário).
    """
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400

    # Escopo: barbeiro só pode marcar pra si mesmo — força o barbeiro_id dele,
    # ignorando o que vier no corpo. Master pode escolher qualquer barbeiro.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        dados["barbeiro_id"] = escopo

    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=False, permitir_conflito=True
    )
    return jsonify(corpo), status


@app.route("/api/admin/agendamentos/<int:agendamento_id>/cancelar", methods=["PATCH"])
@token_requerido
def cancelar_agendamento(agendamento_id):
    """
    Cancela um agendamento pelo ID.
    Usa PATCH porque estamos atualizando só o status,
    não o agendamento inteiro.
    """
    conn = get_connection()
    existe = conn.execute(
        "SELECT id, status, barbeiro_id FROM agendamentos WHERE id = %s", (agendamento_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    # Escopo: barbeiro só cancela agendamento dele (trata como "não encontrado"
    # pra não revelar dados de outros barbeiros). Master cancela qualquer um.
    escopo = barbeiro_do_escopo()
    if escopo is not None and existe["barbeiro_id"] != escopo:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    if existe["status"] == "cancelado":
        conn.close()
        return jsonify({"erro": "Agendamento já está cancelado"}), 400

    conn.execute(
        "UPDATE agendamentos SET status = 'cancelado' WHERE id = %s",
        (agendamento_id,)
    )
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Agendamento cancelado com sucesso"})


# -------------------------------------------------------
# PAGAMENTO + CONSUMOS (produtos) de um agendamento
# -------------------------------------------------------
FORMAS_PAGAMENTO = {"cartao", "pix", "dinheiro"}

# Catálogo de adicionais (produtos vendidos no balcão). O barbeiro escolhe a
# quantidade de cada num atendimento. Preço em centavos. Pra mudar preço/itens,
# é só editar aqui — cada consumo guarda um snapshot (nome + preço unitário).
PRODUTOS_ADICIONAIS = [
    {"id": 1, "nome": "Salgadinho",        "preco_centavos": 500},
    {"id": 2, "nome": "Long neck",         "preco_centavos": 800},
    {"id": 3, "nome": "Corona",            "preco_centavos": 1000},
    {"id": 4, "nome": "Refrigerante",      "preco_centavos": 600},
    {"id": 5, "nome": "Cerveja 269 ml",    "preco_centavos": 400},
    {"id": 6, "nome": "Óleo de barba",     "preco_centavos": 2500},
    {"id": 7, "nome": "Pomada em pó",      "preco_centavos": 2000},
    {"id": 8, "nome": "Pomada modeladora", "preco_centavos": 2000},
    {"id": 9, "nome": "Gel cola",          "preco_centavos": 1500},
]
PRODUTOS_POR_ID = {p["id"]: p for p in PRODUTOS_ADICIONAIS}


def _agendamento_no_escopo(conn, agendamento_id):
    """Row do agendamento se existir E estiver no escopo do usuário (barbeiro só
    mexe no próprio; master mexe em qualquer). Senão None."""
    row = conn.execute(
        "SELECT id, barbeiro_id FROM agendamentos WHERE id = %s", (agendamento_id,)
    ).fetchone()
    if not row:
        return None
    escopo = barbeiro_do_escopo()
    if escopo is not None and row["barbeiro_id"] != escopo:
        return None
    return row


@app.route("/api/admin/agendamentos/<int:agendamento_id>/pagamento", methods=["PATCH"])
@token_requerido
def registrar_pagamento(agendamento_id):
    """Registra a forma de pagamento do atendimento. { forma: cartao|pix|dinheiro }
    (forma vazia limpa o registro)."""
    dados = request.get_json(silent=True) or {}
    forma = (dados.get("forma") or "").strip().lower()
    if forma and forma not in FORMAS_PAGAMENTO:
        return jsonify({"erro": "Forma de pagamento inválida"}), 400
    conn = get_connection()
    if not _agendamento_no_escopo(conn, agendamento_id):
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404
    conn.execute("UPDATE agendamentos SET forma_pagamento = %s WHERE id = %s",
                 (forma or None, agendamento_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Forma de pagamento registrada.", "forma_pagamento": forma or None})


@app.route("/api/admin/produtos", methods=["GET"])
@token_requerido
def listar_produtos():
    """Catálogo de adicionais (nome + preço) pro barbeiro escolher a quantidade."""
    return jsonify(PRODUTOS_ADICIONAIS)


@app.route("/api/admin/agendamentos/<int:agendamento_id>/consumos", methods=["GET", "PUT"])
@token_requerido
def consumos_agendamento(agendamento_id):
    """Adicionais do atendimento. GET lista o que já tem; PUT substitui tudo pelo
    conjunto enviado: { itens: [{produto_id, quantidade}, ...] }."""
    conn = get_connection()
    if not _agendamento_no_escopo(conn, agendamento_id):
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    if request.method == "PUT":
        dados = request.get_json(silent=True) or {}
        itens = dados.get("itens") or []
        cur = conn.cursor()
        cur.execute("DELETE FROM consumos WHERE agendamento_id = %s", (agendamento_id,))
        for it in itens:
            prod = PRODUTOS_POR_ID.get(it.get("produto_id"))
            try:
                qtd = int(it.get("quantidade") or 0)
            except (ValueError, TypeError):
                qtd = 0
            if not prod or qtd <= 0:
                continue
            cur.execute(
                "INSERT INTO consumos (agendamento_id, produto_id, descricao, valor_centavos, quantidade) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agendamento_id, prod["id"], prod["nome"], prod["preco_centavos"], qtd)
            )
        conn.commit()
        conn.close()
        return jsonify({"mensagem": "Adicionais salvos."})

    itens = conn.execute(
        "SELECT id, produto_id, descricao, valor_centavos, quantidade "
        "FROM consumos WHERE agendamento_id = %s ORDER BY id",
        (agendamento_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(i) for i in itens])


@app.route("/api/admin/barbeiros", methods=["GET"])
@token_requerido
@somente_master
def painel_barbeiros():
    """
    Lista TODOS os barbeiros (ativos e inativos), com agendamentos futuros,
    comissão e o login de cada um (se já tiver). Só o master acessa.
    """
    hoje = data_hoje().isoformat()
    conn = get_connection()
    barbeiros = conn.execute(
        """
        SELECT
            barbeiros.id,
            barbeiros.nome,
            barbeiros.ativo,
            barbeiros.comissao_pct,
            COUNT(agendamentos.id) AS agendamentos_futuros,
            (SELECT usuario FROM admin
             WHERE admin.barbeiro_id = barbeiros.id AND admin.papel = 'barbeiro'
             LIMIT 1) AS login_usuario
        FROM barbeiros
        LEFT JOIN agendamentos
            ON agendamentos.barbeiro_id = barbeiros.id
            AND agendamentos.status = 'confirmado'
            AND agendamentos.data >= %s
        GROUP BY barbeiros.id
        ORDER BY barbeiros.id
        """,
        (hoje,)
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in barbeiros])


@app.route("/api/admin/barbeiros/<int:barbeiro_id>/login", methods=["PUT"])
@token_requerido
@somente_master
def definir_login_barbeiro(barbeiro_id):
    """
    Master cria/atualiza o login (usuário + senha) de um barbeiro.
    Corpo: { "usuario": "...", "senha": "..." } (senha opcional na atualização).
    """
    dados = request.get_json(silent=True) or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""

    if not usuario:
        return jsonify({"erro": "Usuário é obrigatório"}), 400

    conn = get_connection()
    # barbeiro existe?
    if not conn.execute("SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404

    # usuário já usado por OUTRA conta?
    conflito = conn.execute(
        "SELECT id FROM admin WHERE usuario = %s AND barbeiro_id IS DISTINCT FROM %s",
        (usuario, barbeiro_id)
    ).fetchone()
    if conflito:
        conn.close()
        return jsonify({"erro": "Esse nome de usuário já está em uso"}), 409

    existente = conn.execute(
        "SELECT id FROM admin WHERE barbeiro_id = %s AND papel = 'barbeiro'", (barbeiro_id,)
    ).fetchone()

    if existente:
        # atualiza usuário (e senha, se veio)
        conn.execute("UPDATE admin SET usuario = %s WHERE id = %s", (usuario, existente["id"]))
        if senha:
            h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
            conn.execute("UPDATE admin SET senha_hash = %s WHERE id = %s", (h, existente["id"]))
        msg = "Login do barbeiro atualizado"
    else:
        if not senha:
            conn.close()
            return jsonify({"erro": "Senha é obrigatória ao criar o login"}), 400
        h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (usuario, senha_hash, papel, barbeiro_id) VALUES (%s, %s, 'barbeiro', %s)",
            (usuario, h, barbeiro_id)
        )
        msg = "Login do barbeiro criado"

    conn.commit()
    conn.close()
    return jsonify({"mensagem": msg, "usuario": usuario})


@app.route("/api/admin/acessos", methods=["GET"])
@token_requerido
@somente_master
def listar_acessos():
    """Lista todos os logins do painel (master, salão, barbeiros) — pro master
    ver e trocar senhas. Ordena: master, salão, depois barbeiros."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT admin.id, admin.usuario, admin.papel, barbeiros.nome AS barbeiro_nome
           FROM admin
           LEFT JOIN barbeiros ON admin.barbeiro_id = barbeiros.id
           ORDER BY CASE admin.papel WHEN 'master' THEN 0 WHEN 'salao' THEN 1 ELSE 2 END,
                    admin.usuario"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/senha", methods=["PUT"])
@token_requerido
@somente_master
def trocar_senha_login():
    """Master troca a senha de qualquer login. Corpo: { usuario, senha }."""
    dados = request.get_json(silent=True) or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""
    if not usuario or not senha:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
    if len(senha) < 4:
        return jsonify({"erro": "A senha deve ter pelo menos 4 caracteres"}), 400

    conn = get_connection()
    existe = conn.execute("SELECT id FROM admin WHERE usuario = %s", (usuario,)).fetchone()
    if not existe:
        conn.close()
        return jsonify({"erro": "Login não encontrado"}), 404
    h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE admin SET senha_hash = %s WHERE usuario = %s", (h, usuario))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Senha de '{usuario}' atualizada."})


@app.route("/api/admin/minha-senha", methods=["PUT"])
@token_requerido
def trocar_minha_senha():
    """Qualquer usuário logado troca a PRÓPRIA senha (exige a senha atual).
    Corpo: { senha_atual, nova_senha }."""
    dados = request.get_json(silent=True) or {}
    atual = dados.get("senha_atual") or ""
    nova = dados.get("nova_senha") or ""
    if not atual or not nova:
        return jsonify({"erro": "Informe a senha atual e a nova"}), 400
    if len(nova) < 4:
        return jsonify({"erro": "A nova senha deve ter pelo menos 4 caracteres"}), 400

    admin_id = g.usuario.get("admin_id")
    conn = get_connection()
    row = conn.execute("SELECT senha_hash FROM admin WHERE id = %s", (admin_id,)).fetchone()
    if not row or not bcrypt.checkpw(atual.encode(), row["senha_hash"].encode()):
        conn.close()
        return jsonify({"erro": "Senha atual incorreta"}), 400
    h = bcrypt.hashpw(nova.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE admin SET senha_hash = %s WHERE id = %s", (h, admin_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Senha alterada com sucesso."})


@app.route("/api/admin/barbeiros/<int:barbeiro_id>", methods=["PATCH"])
@token_requerido
@somente_master
def atualizar_status_barbeiro(barbeiro_id):
    """
    Inativa ou reativa um barbeiro (item: folga/imprevisto temporário).
    Corpo esperado: { "ativo": true }  ou  { "ativo": false }

    Inativar NÃO apaga o barbeiro nem seus agendamentos históricos —
    apenas o esconde da tela pública de agendamento (/api/barbeiros).
    """
    dados = request.get_json(silent=True)
    if not dados or "ativo" not in dados:
        return jsonify({"erro": "Campo 'ativo' é obrigatório (true ou false)"}), 400

    novo_ativo = 1 if dados["ativo"] else 0

    conn = get_connection()
    existe = conn.execute(
        "SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    if not existe:
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404

    conn.execute(
        "UPDATE barbeiros SET ativo = %s WHERE id = %s", (novo_ativo, barbeiro_id)
    )
    conn.commit()
    conn.close()

    estado = "reativado" if novo_ativo else "inativado"
    return jsonify({"mensagem": f"Barbeiro {estado} com sucesso", "ativo": bool(novo_ativo)})


@app.route("/api/admin/bloqueios", methods=["GET"])
@token_requerido
@somente_master
def listar_bloqueios():
    """Lista todos os bloqueios cadastrados."""
    conn = get_connection()
    bloqueios = conn.execute(
        "SELECT * FROM bloqueios ORDER BY data, hora"
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in bloqueios])


@app.route("/api/admin/bloqueios", methods=["POST"])
@token_requerido
@somente_master
def criar_bloqueio():
    """
    Cria um bloqueio de dia inteiro ou horário específico.

    Dia inteiro:      { "data": "2026-07-10", "motivo": "Feriado" }
    Horário específico: { "data": "2026-07-10", "hora": "14:00", "motivo": "Compromisso" }
    """
    dados = request.get_json(silent=True)
    if not dados or not dados.get("data"):
        return jsonify({"erro": "Data é obrigatória"}), 400

    valido, erro = validar_data(dados["data"])
    if not valido:
        return jsonify({"erro": erro}), 400

    if dados.get("hora"):
        valido, erro = validar_hora(dados["hora"])
        if not valido:
            return jsonify({"erro": erro}), 400

    conn = get_connection()

    # Verifica se já existe bloqueio igual.
    # 'IS NOT DISTINCT FROM' é a comparação null-safe do Postgres (equivalente
    # ao 'hora IS ?' do SQLite): trata NULL = NULL como verdadeiro.
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND hora IS NOT DISTINCT FROM %s",
        (dados["data"], dados.get("hora"))
    ).fetchone()

    if existe:
        conn.close()
        return jsonify({"erro": "Já existe um bloqueio para essa data/hora"}), 409

    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo) VALUES (%s, %s, %s)",
        (dados["data"], dados.get("hora"), dados.get("motivo"))
    )
    conn.commit()
    conn.close()

    tipo = "Horário bloqueado" if dados.get("hora") else "Dia bloqueado"
    return jsonify({"mensagem": f"{tipo} com sucesso!"}), 201


@app.route("/api/admin/bloqueios/<int:bloqueio_id>", methods=["DELETE"])
@token_requerido
@somente_master
def remover_bloqueio(bloqueio_id):
    """Remove um bloqueio (desbloqueia o dia ou horário)."""
    conn = get_connection()
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE id = %s", (bloqueio_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Bloqueio não encontrado"}), 404

    conn.execute("DELETE FROM bloqueios WHERE id = %s", (bloqueio_id,))
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Bloqueio removido com sucesso"})


# -------------------------------------------------------
# ALMOÇO — o barbeiro bloqueia 60min do próprio dia (e pode liberar).
# Reaproveita a tabela bloqueios (barbeiro_id + duracao_min + motivo 'Almoço').
# Não é @somente_master: o próprio barbeiro gerencia o almoço dele.
# -------------------------------------------------------
def _barbeiro_alvo_almoco(fonte):
    """Descobre o barbeiro do almoço. Retorna (id, erro).
    - Barbeiro logado: SEMPRE o próprio (não mexe no de ninguém);
    - Master/salão: o barbeiro_id informado (pode escolher qualquer um) e,
      se não vier, cai no barbeiro do token (master = JP)."""
    escopo = barbeiro_do_escopo()          # barbeiro → seu id; master/salão → None
    if escopo is not None:
        return escopo, None
    barbeiro_id = fonte.get("barbeiro_id") or g.usuario.get("barbeiro_id")
    if not barbeiro_id:
        return None, "barbeiro_id é obrigatório"
    return barbeiro_id, None


@app.route("/api/admin/almoco", methods=["GET"])
@token_requerido
def obter_almoco():
    """Almoço do barbeiro (logado ou informado) numa data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    row = conn.execute(
        """SELECT id, hora, duracao_min FROM bloqueios
           WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'
           ORDER BY hora LIMIT 1""",
        (data, barbeiro_id)
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else None)


@app.route("/api/admin/almoco", methods=["POST"])
@token_requerido
def marcar_almoco():
    """Barbeiro bloqueia 60min de almoço no dia. { data, hora, barbeiro_id? }"""
    dados = request.get_json(silent=True) or {}
    data = dados.get("data")
    hora = dados.get("hora")
    if not data or not hora:
        return jsonify({"erro": "Data e hora são obrigatórias"}), 400
    valido, erro = validar_data(data)
    if not valido:
        return jsonify({"erro": erro}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(dados)
    if erro:
        return jsonify({"erro": erro}), 400

    conn = get_connection()
    ja = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    ).fetchone()
    if ja:
        conn.close()
        return jsonify({"erro": "Você já tem um almoço marcado nesse dia. Libere o atual primeiro."}), 409
    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo, barbeiro_id, duracao_min) VALUES (%s, %s, 'Almoço', %s, %s)",
        (data, hora, barbeiro_id, DURACAO_ALMOCO_MIN)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço bloqueado às {hora} (60 min)."}), 201


@app.route("/api/admin/almoco", methods=["DELETE"])
@token_requerido
def liberar_almoco():
    """Libera (remove) o almoço do barbeiro na data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute(
        "DELETE FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço liberado."})


@app.route("/api/admin/almoco-fixo", methods=["GET"])
@token_requerido
def obter_almoco_fixo():
    """Almoço fixo (todo dia). Barbeiro vê o próprio; master/salão pode consultar
    outro passando ?barbeiro_id=."""
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    row = conn.execute("SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone()
    conn.close()
    return jsonify({"almoco_fixo": row["almoco_fixo"] if row else None})


@app.route("/api/admin/almoco-fixo", methods=["PUT"])
@token_requerido
def definir_almoco_fixo():
    """Define o almoço fixo (repete todo dia, 60min). Corpo: { hora }."""
    dados = request.get_json(silent=True) or {}
    hora = dados.get("hora")
    if not hora:
        return jsonify({"erro": "Hora é obrigatória"}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(dados)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = %s WHERE id = %s", (hora, barbeiro_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço fixo definido às {hora} (todo dia)."})


@app.route("/api/admin/almoco-fixo", methods=["DELETE"])
@token_requerido
def remover_almoco_fixo():
    """Remove o almoço fixo (volta a marcar manualmente por dia). Master/salão
    pode remover o de outro passando ?barbeiro_id=."""
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = NULL WHERE id = %s", (barbeiro_id,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço fixo removido."})


@app.route("/api/admin/almocos", methods=["GET"])
@token_requerido
def listar_almocos():
    """Almoços do dia (fixo + manual) por barbeiro, pra desenhar na agenda como
    um bloco. Barbeiro vê o próprio; master/salão vê todos. ?data=YYYY-MM-DD"""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    escopo = barbeiro_do_escopo()   # barbeiro → seu id; master/salão → None
    conn = get_connection()

    q_manual = """SELECT b.barbeiro_id, b.hora, b.duracao_min, bb.nome AS barbeiro_nome
                  FROM bloqueios b JOIN barbeiros bb ON b.barbeiro_id = bb.id
                  WHERE b.data = %s AND b.motivo = 'Almoço'"""
    pm = [data]
    if escopo is not None:
        q_manual += " AND b.barbeiro_id = %s"
        pm.append(escopo)
    manuais = conn.execute(q_manual, pm).fetchall()

    q_fixo = ("SELECT id AS barbeiro_id, nome AS barbeiro_nome, almoco_fixo "
              "FROM barbeiros WHERE ativo = 1 AND almoco_fixo IS NOT NULL")
    pf = []
    if escopo is not None:
        q_fixo += " AND id = %s"
        pf.append(escopo)
    fixos = conn.execute(q_fixo, pf).fetchall()
    conn.close()

    saida, vistos = [], set()
    for m in manuais:
        hora = m["hora"][:5]
        saida.append({"barbeiro_id": m["barbeiro_id"], "barbeiro_nome": m["barbeiro_nome"],
                      "hora": hora, "duracao_min": m["duracao_min"] or 60, "tipo": "manual"})
        vistos.add((m["barbeiro_id"], hora))
    for f in fixos:
        hora = f["almoco_fixo"][:5]
        if (f["barbeiro_id"], hora) in vistos:   # manual do dia já cobre esse horário
            continue
        saida.append({"barbeiro_id": f["barbeiro_id"], "barbeiro_nome": f["barbeiro_nome"],
                      "hora": hora, "duracao_min": 60, "tipo": "fixo"})
    return jsonify(saida)


# -------------------------------------------------------
# EXPEDIENTE — o master ajusta a jornada (início/fim) de cada barbeiro por dia.
# Sobrepõe o horário padrão do dia SÓ pra aquele barbeiro naquela data.
# -------------------------------------------------------
@app.route("/api/admin/expedientes", methods=["GET"])
@token_requerido
@somente_master
def listar_expedientes():
    """Barbeiros ativos com o horário que vale pra eles numa data (expediente
    especial, se definido, ou o padrão do dia). ?data=YYYY-MM-DD"""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    padrao_ini, padrao_fim = horario_do_dia(data)
    fechado, _ = dia_fechado(data)
    conn = get_connection()
    barbeiros = conn.execute(
        "SELECT id, nome FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    exps = conn.execute(
        "SELECT barbeiro_id, inicio, fim FROM expedientes WHERE data = %s", (data,)
    ).fetchall()
    conn.close()
    por_barb = {e["barbeiro_id"]: (e["inicio"], e["fim"]) for e in exps}
    lista = []
    for b in barbeiros:
        ov = por_barb.get(b["id"])
        lista.append({
            "barbeiro_id": b["id"],
            "barbeiro_nome": b["nome"],
            "inicio": ov[0] if ov else padrao_ini,
            "fim": ov[1] if ov else padrao_fim,
            "personalizado": ov is not None,
        })
    return jsonify({
        "data": data,
        "fechado": fechado,
        "padrao": {"inicio": padrao_ini, "fim": padrao_fim},
        "barbeiros": lista,
    })


@app.route("/api/admin/expediente", methods=["PUT"])
@token_requerido
@somente_master
def definir_expediente():
    """Define/atualiza o expediente de um barbeiro num dia.
    Corpo: { barbeiro_id, data, inicio, fim }."""
    dados = request.get_json(silent=True) or {}
    barbeiro_id = dados.get("barbeiro_id")
    data = dados.get("data")
    inicio = dados.get("inicio")
    fim = dados.get("fim")
    if not (barbeiro_id and data and inicio and fim):
        return jsonify({"erro": "barbeiro_id, data, início e fim são obrigatórios"}), 400
    valido, erro = validar_data(data)
    if not valido:
        return jsonify({"erro": erro}), 400
    for h in (inicio, fim):
        ok, e = validar_hora(h)
        if not ok:
            return jsonify({"erro": e}), 400
    if hhmm_para_min(inicio) >= hhmm_para_min(fim):
        return jsonify({"erro": "A hora de início tem que ser antes do fim"}), 400
    conn = get_connection()
    if not conn.execute("SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404
    conn.execute(
        """INSERT INTO expedientes (barbeiro_id, data, inicio, fim)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (barbeiro_id, data)
           DO UPDATE SET inicio = EXCLUDED.inicio, fim = EXCLUDED.fim""",
        (barbeiro_id, data, inicio, fim)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Expediente definido ({inicio}–{fim})."})


@app.route("/api/admin/expediente", methods=["DELETE"])
@token_requerido
@somente_master
def remover_expediente():
    """Remove o expediente especial (volta pro horário padrão do dia).
    ?barbeiro_id=&data="""
    barbeiro_id = request.args.get("barbeiro_id")
    data = request.args.get("data")
    if not (barbeiro_id and data):
        return jsonify({"erro": "barbeiro_id e data são obrigatórios"}), 400
    conn = get_connection()
    conn.execute("DELETE FROM expedientes WHERE barbeiro_id = %s AND data = %s", (barbeiro_id, data))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Expediente removido (voltou ao normal)."})


@app.route("/api/admin/relatorio", methods=["GET"])
@token_requerido
def relatorio_admin():
    """
    Relatório financeiro com filtro por período.
    Parâmetro: ?periodo=dia | semana | mes
    Padrão: dia (hoje)
    """
    # Salão (tablet compartilhado) não vê dinheiro.
    if not pode_ver_valores():
        return jsonify({"erro": "Sem acesso a valores financeiros."}), 403

    periodo = request.args.get("periodo", "dia")

    hoje = data_hoje()
    if periodo == "semana":
        # Segunda-feira da semana atual até hoje
        inicio = hoje - timedelta(days=hoje.weekday())
        fim = hoje
    elif periodo == "mes":
        inicio = hoje.replace(day=1)
        fim = hoje
    else:  # dia
        inicio = hoje
        fim = hoje

    conn = get_connection()

    # Escopo: barbeiro vê só os números dele; master vê de todos.
    # (filtro_barb é um fragmento fixo — o valor vai por parâmetro, sem injeção)
    escopo = barbeiro_do_escopo()
    filtro_barb = " AND agendamentos.barbeiro_id = %s" if escopo is not None else ""
    per = [inicio.isoformat(), fim.isoformat()] + ([escopo] if escopo is not None else [])

    # Faturamento do período
    faturamento = conn.execute(f"""
        SELECT COALESCE(SUM(servicos.preco), 0) as total
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
    """, per).fetchone()["total"]

    # Total de agendamentos no período
    total = conn.execute(f"""
        SELECT COUNT(*) as total FROM agendamentos
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
    """, per).fetchone()["total"]

    # Agendamentos por dia (pra montar gráfico no futuro)
    por_dia = conn.execute(f"""
        SELECT agendamentos.data, COUNT(*) as quantidade,
               SUM(servicos.preco) as faturamento
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
        GROUP BY agendamentos.data
        ORDER BY agendamentos.data
    """, per).fetchall()

    # Ranking dos serviços mais realizados no período (pro dashboard)
    servicos_mais_realizados = conn.execute(f"""
        SELECT servicos.nome,
               COUNT(*) as quantidade,
               SUM(servicos.preco) as faturamento
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
        GROUP BY servicos.id
        ORDER BY quantidade DESC, faturamento DESC
    """, per).fetchall()

    conn.close()

    return jsonify({
        "periodo": periodo,
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "total_agendamentos": total,
        "faturamento_total": faturamento,
        "por_dia": [dict(d) for d in por_dia],
        "servicos_mais_realizados": [dict(s) for s in servicos_mais_realizados]
    })


@app.route("/api/admin/contagem", methods=["GET"])
@token_requerido
def contagem_dia():
    """
    Fechamento do dia por barbeiro: nº de clientes, valor total e o repasse
    (comissão do barbeiro conforme comissao_pct; o resto fica com a barbearia).
    Master vê todos os barbeiros; barbeiro vê só o dele.
    Parâmetro: ?data=YYYY-MM-DD (padrão: hoje). Aceita datas passadas (fechamento).
    """
    data_str = request.args.get("data") or data_hoje().isoformat()
    try:
        datetime.strptime(data_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"erro": "Formato de data inválido (use YYYY-MM-DD)"}), 400

    # Escopo: barbeiro vê só a linha dele; master vê todos.
    escopo = barbeiro_do_escopo()
    filtro = " WHERE barbeiros.id = %s" if escopo is not None else ""
    params = [data_str] + ([escopo] if escopo is not None else [])

    conn = get_connection()
    linhas = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id, barbeiros.nome AS barbeiro_nome,
               barbeiros.comissao_pct,
               COUNT(agendamentos.id) AS clientes,
               COALESCE(SUM(servicos.preco), 0) AS total
        FROM barbeiros
        LEFT JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        LEFT JOIN servicos ON agendamentos.servico_id = servicos.id
        {filtro}
        GROUP BY barbeiros.id
        ORDER BY barbeiros.id
    """, params).fetchall()

    # Detalhe: quantos de cada serviço + o valor somado, por barbeiro
    # (ex: 3 Degradê = R$120, 1 Corte Social = R$35).
    detalhe = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id, servicos.nome AS servico_nome,
               COUNT(agendamentos.id) AS quantidade,
               COALESCE(SUM(servicos.preco), 0) AS valor
        FROM barbeiros
        JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        JOIN servicos ON agendamentos.servico_id = servicos.id
        {filtro}
        GROUP BY barbeiros.id, servicos.nome
        ORDER BY quantidade DESC, servicos.nome
    """, params).fetchall()

    # Produtos (consumos) do dia por barbeiro — linha à parte, NÃO comissiona.
    produtos = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id,
               COALESCE(SUM(consumos.quantidade), 0) AS produtos_qtd,
               COALESCE(SUM(consumos.valor_centavos * consumos.quantidade), 0) AS produtos_centavos
        FROM barbeiros
        JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        JOIN consumos ON consumos.agendamento_id = agendamentos.id
        {filtro}
        GROUP BY barbeiros.id
    """, params).fetchall()
    conn.close()

    por_barbeiro = {}
    for d in detalhe:
        por_barbeiro.setdefault(d["barbeiro_id"], []).append(
            {"nome": d["servico_nome"], "quantidade": d["quantidade"],
             "valor": round(float(d["valor"] or 0), 2)}
        )
    produtos_por_barbeiro = {
        p["barbeiro_id"]: {"qtd": p["produtos_qtd"],
                           "valor": round(float(p["produtos_centavos"] or 0) / 100, 2)}
        for p in produtos
    }

    # Salão (tablet) NÃO vê valores — só a quantidade de cortes.
    ver_valores = pode_ver_valores()

    barbeiros = []
    tot_clientes = tot_valor = tot_barbeiro = tot_barbearia = tot_produtos = 0
    for r in linhas:
        total = float(r["total"] or 0)
        pct = r["comissao_pct"]
        recebe_barbeiro = round(total * pct / 100, 2)
        recebe_barbearia = round(total - recebe_barbeiro, 2)
        prod = produtos_por_barbeiro.get(r["barbeiro_id"], {"qtd": 0, "valor": 0})

        servicos_b = por_barbeiro.get(r["barbeiro_id"], [])
        if not ver_valores:   # salão vê só a quantidade, sem valor
            servicos_b = [{"nome": s["nome"], "quantidade": s["quantidade"]} for s in servicos_b]
        item = {
            "barbeiro_id": r["barbeiro_id"],
            "barbeiro_nome": r["barbeiro_nome"],
            "clientes": r["clientes"],
            "servicos": servicos_b
        }
        if ver_valores:
            item.update({
                "total": round(total, 2),
                "comissao_pct": pct,
                "barbeiro_recebe": recebe_barbeiro,
                "barbearia_recebe": recebe_barbearia,
                "produtos": prod["valor"],
                "produtos_qtd": prod["qtd"]
            })
        barbeiros.append(item)

        tot_clientes += r["clientes"]
        tot_valor += total
        tot_barbeiro += recebe_barbeiro
        tot_barbearia += recebe_barbearia
        tot_produtos += prod["valor"]

    totais = {"clientes": tot_clientes}
    if ver_valores:
        totais.update({
            "total": round(tot_valor, 2),
            "barbeiro_recebe": round(tot_barbeiro, 2),
            "barbearia_recebe": round(tot_barbearia, 2),
            "produtos": round(tot_produtos, 2)
        })

    return jsonify({
        "data": data_str,
        "ver_valores": ver_valores,
        "barbeiros": barbeiros,
        "totais": totais
    })


# -------------------------------------------------------
# TAREFAS AGENDADAS (chamadas por um agendador externo)
# O Render free hiberna, então quem "acorda" o serviço no horário é uma chamada
# de fora (GitHub Actions, cron-job.org...). Protegido por token secreto.
# -------------------------------------------------------
TAREFAS_TOKEN = os.getenv("TAREFAS_TOKEN", "").strip()

# Quantos minutos antes do horário o lembrete automático sai (padrão 60 = 1h).
# A tarefa roda de tempos em tempos e pega quem está entrando nessa janela.
LEMBRETE_ANTECEDENCIA_MIN = int(os.getenv("LEMBRETE_ANTECEDENCIA_MINUTOS", "60"))


def _token_de_tarefa_confere():
    if not TAREFAS_TOKEN:
        return False
    enviado = (request.headers.get("X-Tarefa-Token")
               or request.args.get("token") or "")
    return hmac.compare_digest(enviado, TAREFAS_TOKEN)


def _agendamentos_do_dia(data_str):
    """Agendamentos ativos da data, já no formato das mensagens."""
    conn = get_connection()
    linhas = conn.execute(
        """SELECT agendamentos.hora, clientes.nome AS cliente,
                  clientes.telefone AS telefone, servicos.nome AS servico,
                  barbeiros.nome AS barbeiro
           FROM agendamentos
           JOIN clientes  ON agendamentos.cliente_id  = clientes.id
           JOIN servicos  ON agendamentos.servico_id  = servicos.id
           JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
           WHERE agendamentos.data = %s AND agendamentos.status != 'cancelado'
           ORDER BY agendamentos.hora""",
        (data_str,)
    ).fetchall()
    conn.close()
    return [{"hora": l["hora"][:5], "cliente": l["cliente"], "telefone": l["telefone"],
             "servico": l["servico"], "barbeiro": l["barbeiro"]} for l in linhas]


def _enviar_lembretes_de_clientes():
    """
    Manda o lembrete por email pra quem tem horário chegando (~1h). Roda de
    tempos em tempos; cada agendamento é marcado como avisado (lembrete_enviado_em),
    então ninguém recebe duas vezes mesmo se a tarefa rodar várias vezes.

    Pega quem começa entre AGORA e AGORA + antecedência: se a tarefa atrasar, o
    cliente ainda recebe (um pouco em cima da hora, melhor que não receber).
    """
    if not emails.configurado():
        return {"tipo": "proximos", "enviados": 0, "erro": "E-mail não configurado"}

    agora = agora_br()
    hoje = agora.date().isoformat()
    agora_min = agora.hour * 60 + agora.minute
    limite_min = agora_min + LEMBRETE_ANTECEDENCIA_MIN

    conn = get_connection()
    candidatos = conn.execute(
        """SELECT agendamentos.id, agendamentos.hora, clientes.nome AS cliente,
                  clientes.email, servicos.nome AS servico, barbeiros.nome AS barbeiro
           FROM agendamentos
           JOIN clientes  ON agendamentos.cliente_id  = clientes.id
           JOIN servicos  ON agendamentos.servico_id  = servicos.id
           JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
           WHERE agendamentos.data = %s
             AND agendamentos.status != 'cancelado'
             AND agendamentos.lembrete_enviado_em IS NULL
             AND clientes.email IS NOT NULL AND clientes.email <> ''
           ORDER BY agendamentos.hora""",
        (hoje,)
    ).fetchall()

    enviados = 0
    for linha in candidatos:
        try:
            inicio = hhmm_para_min(linha["hora"])
        except ValueError:
            continue
        if not (agora_min <= inicio <= limite_min):
            continue                                  # ainda não é a hora (ou já passou)
        assunto, html, texto = emails.email_lembrete(
            cliente=linha["cliente"], servico=linha["servico"],
            barbeiro=linha["barbeiro"], data_br=_data_br(hoje),
            hora=linha["hora"][:5], endereco=ENDERECO_BARBEARIA,
        )
        # esperar=True: só marca como enviado depois de realmente tentar mandar.
        emails.enviar(linha["email"], assunto, html, texto, esperar=True)
        conn.execute("UPDATE agendamentos SET lembrete_enviado_em = now() WHERE id = %s",
                     (linha["id"],))
        enviados += 1

    conn.commit()
    conn.close()
    logger.info("Lembretes de clientes: %d enviado(s) de %d candidato(s).",
                enviados, len(candidatos))
    return {"tipo": "proximos", "data": hoje, "enviados": enviados}


@app.route("/api/tarefas/testar-email", methods=["POST"])
@limiter.limit("10 per hour")
def tarefa_testar_email():
    """
    Diagnóstico de e-mail: tenta enviar UM email de teste e devolve o erro real
    do servidor SMTP, em vez de deixá-lo escondido no log.
    Uso: POST /api/tarefas/testar-email?para=alguem@email.com  (com o token)
    """
    if not _token_de_tarefa_confere():
        return jsonify({"erro": "Não autorizado"}), 401

    resultado = emails.diagnostico()
    destino = (request.args.get("para") or "").strip()
    if not resultado["configurado"]:
        resultado["dica"] = "Faltam variáveis SMTP no servidor (EMAIL_SMTP_HOST / EMAIL_REMETENTE)."
        return jsonify(resultado), 200
    if not destino:
        resultado["dica"] = "Informe ?para=seu@email.com pra fazer o teste de envio."
        return jsonify(resultado), 200

    enviado, erro = emails.testar(destino)
    resultado.update({"destino": destino, "enviado": enviado, "erro_smtp": erro})
    logger.info("Teste de email para %s: enviado=%s erro=%s", destino, enviado, erro)
    return jsonify(resultado), 200


@app.route("/api/tarefas/avisos", methods=["POST"])
@limiter.limit("20 per hour")
def tarefa_avisos():
    """
    Manda no Telegram da equipe:
    - ?tipo=lembretes (padrão): agenda de AMANHÃ com um link de WhatsApp pronto
      por cliente — o barbeiro só toca e envia.
    - ?tipo=resumo: agenda de HOJE, pra abrir o dia sabendo o movimento.
    Autenticação: header X-Tarefa-Token (ou ?token=) igual a TAREFAS_TOKEN.
    """
    if not _token_de_tarefa_confere():
        return jsonify({"erro": "Não autorizado"}), 401

    tipo = (request.args.get("tipo") or "lembretes").lower()

    # Lembrete automático pro CLIENTE, ~1h antes do horário dele (por email).
    # Esta é a única tarefa que não depende do Telegram.
    if tipo == "proximos":
        return jsonify(_enviar_lembretes_de_clientes())

    if not notificacoes.configurado():
        return jsonify({"erro": "Telegram não configurado"}), 503

    if tipo == "resumo":
        data = data_hoje()
        agendamentos = _agendamentos_do_dia(data.isoformat())
        texto = notificacoes.texto_resumo_do_dia(_data_br(data.isoformat()), agendamentos)
    else:
        data = data_hoje() + timedelta(days=1)
        agendamentos = _agendamentos_do_dia(data.isoformat())
        texto = notificacoes.texto_lembretes(_data_br(data.isoformat()), agendamentos)

    enviado = notificacoes.enviar(texto, esperar=True)
    logger.info("Tarefa '%s' para %s: %d agendamento(s), enviado=%s",
                tipo, data.isoformat(), len(agendamentos), enviado)
    return jsonify({"tipo": tipo, "data": data.isoformat(),
                    "agendamentos": len(agendamentos), "enviado": enviado})


# -------------------------------------------------------
# INICIALIZAÇÃO
# -------------------------------------------------------
# Roda no carregamento do módulo para funcionar tanto com `python app.py`
# quanto sob gunicorn em produção (o bloco __main__ NÃO executa sob gunicorn,
# então init_db/seeds precisam ficar aqui fora). Tudo é idempotente
# (CREATE TABLE / ADD COLUMN IF NOT EXISTS e seeds que checam antes de inserir).
# APP_SKIP_BOOT=1 pula o boot (usado por testes que importam o módulo sem banco).
# Em produção a variável não existe, então roda tudo normalmente.
if os.getenv("APP_SKIP_BOOT") != "1":
    init_db()
    criar_admin_padrao()      # login master (dono) — do .env
    criar_salao_padrao()      # login do salão (tablet) — do .env
    criar_barbeiros_padrao()  # logins dos barbeiros comuns (2 e 3) — do .env
    ajustar_servicos()        # cardápio oficial (nome/preço/duração/imagem)

if __name__ == "__main__":
    print(f"[config] Ambiente: {FLASK_ENV}")
    print(f"[config] CORS permitido para: {CORS_ORIGIN}")
    print(f"[config] Funcionamento: {HORARIO_ABERTURA} às {HORARIO_FECHAMENTO}")
    app.run(debug=not EM_PRODUCAO)
