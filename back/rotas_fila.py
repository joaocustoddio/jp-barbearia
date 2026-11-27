"""
rotas_fila.py
-------------
Rotas da fila online de fim de ano:
- públicas: ver a fila de hoje, entrar, acompanhar a vez e sair (pelo token);
- painel: períodos de fila (só o dono).
Regras em fila.py; banco em fila_dados.py.
"""

import re
from datetime import datetime, timedelta

import psycopg2.errors
from flask import g, jsonify, request

from agendamentos import (
    MENSAGEM_CLIENTE_BLOQUEADO, _processar_novo_agendamento, buscar_ou_criar_cliente,
    cliente_esta_bloqueado,
)
from auth import barbeiro_do_escopo, somente_master, token_requerido
from horarios import min_para_hhmm
import notificacoes
from config import INTERVALO_MINUTOS, TOLERANCIA_FILA_MIN, agora_br, data_hoje
from database import get_connection
from extensoes import app, limiter
from precos import servico_na_data
from validacoes import dia_fechado, validar_nome, validar_telefone
import fila
import fila_dados

MSG_REPETIDO = ("Esse telefone já está na fila. Abra o link que você recebeu "
                "ou saia da fila antes de entrar de novo.")
MSG_FECHOU_BARBEIRO = "A fila de hoje já fechou para esse barbeiro."
MSG_FECHOU = "A fila de hoje já fechou."
MSG_NAO_ABRIU = "A fila de hoje ainda não abriu."
MSG_SEM_FILA = "Hoje o atendimento é com horário marcado. Agende pelo site."
MSG_LINK = "Esse link não vale mais."


# Limites de tentativa. Atrás do proxy do Render todos os clientes chegam com
# o mesmo IP, então contar por IP faria 10 pessoas esperando se bloquearem.
# A consulta conta por lugar na fila; a entrada, por telefone.
def _chave_token():
    return "fila-token:" + str((request.view_args or {}).get("token", ""))


def _chave_telefone():
    dados = request.get_json(silent=True)
    tel = dados.get("telefone") if isinstance(dados, dict) else None
    numero = re.sub(r"\D", "", tel) if isinstance(tel, str) else ""
    return "fila-tel:" + (numero or request.remote_addr or "")


def _agora_min():
    agora = agora_br()
    return agora.hour * 60 + agora.minute


def _abertos(dia):
    """Barbeiros que trabalham hoje e estão dentro do expediente agora."""
    return [b for b in dia.barbeiros if b.id in dia.abertos_agora]


def _espera_para_novo(dia, barbeiro_id, duracao):
    """Minutos de espera de quem entrar agora (None se ninguém atende)."""
    novo = fila.Lugar(id=10 ** 9, barbeiro_id=barbeiro_id, atendido_por=None,
                      status="aguardando", duracao_min=duracao)
    est = fila.estimar_fila(dia.agora_min, _abertos(dia), dia.lugares + [novo])
    if novo.id not in est:
        return None
    return fila.arredondar_espera(est[novo.id][1] - dia.agora_min)


def _motivo_fechada(dia, barbeiro_id, duracao):
    """None se dá pra entrar na fila de `barbeiro_id` (None = qualquer um); senão a mensagem."""
    trabalhando = {b.id: b for b in dia.barbeiros}
    if barbeiro_id is not None:
        if barbeiro_id not in trabalhando:
            return MSG_FECHOU_BARBEIRO
        if barbeiro_id not in dia.abertos_agora:
            return MSG_NAO_ABRIU if dia.agora_min < trabalhando[barbeiro_id].abertura else MSG_FECHOU_BARBEIRO
    elif not dia.abertos_agora:
        ainda_vai_abrir = any(dia.agora_min < b.abertura for b in dia.barbeiros)
        return MSG_NAO_ABRIU if ainda_vai_abrir else MSG_FECHOU
    if not fila.cabe_mais_um(dia.agora_min, _abertos(dia), dia.lugares, barbeiro_id, duracao):
        return MSG_FECHOU_BARBEIRO if barbeiro_id is not None else MSG_FECHOU
    return None


# -------------------------------------------------------
# PÚBLICAS
# -------------------------------------------------------

@app.route("/api/fila/hoje", methods=["GET"])
def fila_de_hoje():
    """Como está a fila agora: por barbeiro e "qualquer um". Sem nomes de clientes."""
    hoje = data_hoje().isoformat()
    conn = get_connection()
    em_fila = fila_dados.dia_de_fila(conn, hoje)
    fechado = dia_fechado(hoje)[0]
    if not em_fila or fechado:
        # Fora do dia (ou loja fechada): diz quando a fila volta a funcionar.
        amanha = (data_hoje() + timedelta(days=1)).isoformat()
        proximo = fila_dados.proximo_dia_de_fila(conn, amanha)
        conn.close()
        return jsonify({"dia_de_fila": False, "fechado_hoje": bool(em_fila and fechado),
                        "proximo_dia_de_fila": proximo, "barbeiros": [],
                        "qualquer_um": {"aberta": False, "motivo": "Fila fechada",
                                        "esperando": 0, "espera_min": None}})
    dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
    conn.close()

    trabalhando = {b.id for b in dia.barbeiros}
    barbeiros = []
    for bid, nome in sorted(dia.nomes.items()):
        motivo = None
        if bid not in trabalhando:
            motivo = "De folga hoje"
        else:
            fechada = _motivo_fechada(dia, bid, INTERVALO_MINUTOS)
            if fechada == MSG_NAO_ABRIU:
                motivo = "Ainda não abriu"
            elif fechada:
                motivo = "Fila fechada"
        aberta = motivo is None
        barbeiros.append({
            "id": bid, "nome": nome, "foto": dia.fotos.get(bid),
            "aberta": aberta, "motivo": motivo,
            "esperando": sum(1 for l in dia.lugares
                             if l.status == "aguardando" and l.barbeiro_id == bid),
            "espera_min": _espera_para_novo(dia, bid, INTERVALO_MINUTOS) if aberta else None,
        })
    fechada_qualquer = _motivo_fechada(dia, None, INTERVALO_MINUTOS)
    qualquer_aberta = fechada_qualquer is None
    ainda_vai_abrir = [b.abertura for b in dia.barbeiros if dia.agora_min < b.abertura]
    return jsonify({
        "dia_de_fila": True,
        "barbeiros": barbeiros,
        "abre_as": (min_para_hhmm(min(ainda_vai_abrir))
                    if ainda_vai_abrir and not dia.abertos_agora else None),
        "qualquer_um": {
            "aberta": qualquer_aberta,
            "motivo": (None if qualquer_aberta else
                       "Ainda não abriu" if fechada_qualquer == MSG_NAO_ABRIU else "Fila fechada"),
            "esperando": sum(1 for l in dia.lugares
                             if l.status == "aguardando" and l.barbeiro_id is None),
            "espera_min": _espera_para_novo(dia, None, INTERVALO_MINUTOS) if qualquer_aberta else None,
        },
    })


@app.route("/api/fila", methods=["POST"])
@limiter.limit("5 per minute", key_func=_chave_telefone)
def entrar_na_fila():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Dados inválidos."}), 400
    nome = dados.get("nome")
    telefone = dados.get("telefone") or ""
    for valido, erro in (validar_nome(nome), validar_telefone(telefone)):
        if not valido:
            return jsonify({"erro": erro}), 400
    if not telefone.strip():
        return jsonify({"erro": "Telefone é obrigatório"}), 400
    numero = re.sub(r"\D", "", telefone)
    barbeiro_id = dados.get("barbeiro_id")
    if barbeiro_id is not None and (type(barbeiro_id) is not int or barbeiro_id <= 0):
        return jsonify({"erro": "Barbeiro inválido."}), 400

    hoje = data_hoje().isoformat()
    conn = get_connection()
    try:
        if cliente_esta_bloqueado(conn, numero):
            return jsonify({"erro": MENSAGEM_CLIENTE_BLOQUEADO}), 403
        if not fila_dados.dia_de_fila(conn, hoje) or dia_fechado(hoje)[0]:
            return jsonify({"erro": MSG_SEM_FILA}), 409
        servico = servico_na_data(conn, dados.get("servico_id"), hoje)
        if not servico:
            return jsonify({"erro": "Serviço indisponível."}), 400

        # Trava a fila do dia: duas entradas ao mesmo tempo esperam uma a outra.
        conn.execute("SELECT id FROM barbeiros WHERE ativo = 1 FOR UPDATE")
        dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
        if barbeiro_id is not None and barbeiro_id not in dia.nomes:
            return jsonify({"erro": "Barbeiro não encontrado."}), 400

        generico = fila_dados.eh_telefone_generico(conn, numero)
        if not generico and fila_dados.telefone_na_fila(conn, hoje, numero):
            return jsonify({"erro": MSG_REPETIDO}), 409

        duracao = servico["duracao_min"]
        fechada = _motivo_fechada(dia, barbeiro_id, duracao)
        if fechada:
            return jsonify({"erro": fechada}), 409

        cliente_id = buscar_ou_criar_cliente(conn, nome.strip(), numero, None)
        try:
            lugar_id, token = fila_dados.inserir_lugar(
                conn, data=hoje, nome=nome.strip(), telefone=None if generico else numero,
                servico_id=dados.get("servico_id"), duracao_min=duracao,
                barbeiro_id=barbeiro_id, origem="site", cliente_id=cliente_id,
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return jsonify({"erro": MSG_REPETIDO}), 409
        conn.commit()
    finally:
        conn.close()

    novo = fila.Lugar(id=lugar_id, barbeiro_id=barbeiro_id, atendido_por=None,
                      status="aguardando", duracao_min=duracao)
    lugares = dia.lugares + [novo]
    est = fila.estimar_fila(dia.agora_min, _abertos(dia), lugares)
    espera = fila.arredondar_espera(est[lugar_id][1] - dia.agora_min) if lugar_id in est else None
    return jsonify({"token": token, "posicao": fila.posicao(lugar_id, lugares),
                    "espera_min": espera}), 201


def _iso(valor):
    return valor.isoformat() if hasattr(valor, "isoformat") else valor


@app.route("/api/fila/<token>", methods=["GET"])
@limiter.limit("30 per minute", key_func=_chave_token)
def situacao_na_fila(token):
    hoje = data_hoje().isoformat()
    conn = get_connection()
    reg = fila_dados.buscar_por_token(conn, token)
    if not reg or reg["data"] != hoje:
        conn.close()
        return jsonify({"erro": MSG_LINK}), 404

    barbeiro = reg.get("atendido_por_nome") or reg.get("barbeiro_nome") or "Qualquer um"
    corpo = {"status": reg["status"], "posicao": None, "a_frente": None, "espera_min": None,
             "barbeiro": barbeiro, "servico": reg["servico"],
             "chamado_em": _iso(reg.get("chamado_em")), "tolerancia_min": TOLERANCIA_FILA_MIN}
    if reg["status"] not in fila.ATIVOS:
        conn.close()
        return jsonify(corpo)

    dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
    conn.close()
    if reg["id"] in {l.id for l in dia.lugares}:
        corpo["posicao"] = fila.posicao(reg["id"], dia.lugares)
        corpo["a_frente"] = corpo["posicao"] - 1
        est = fila.estimar_fila(dia.agora_min, dia.barbeiros, dia.lugares)
        if reg["id"] in est:
            corpo["espera_min"] = fila.arredondar_espera(est[reg["id"]][1] - dia.agora_min)
    return jsonify(corpo)


@app.route("/api/fila/<token>/sair", methods=["POST"])
@limiter.limit("10 per minute", key_func=_chave_token)
def sair_da_fila(token):
    conn = get_connection()
    try:
        reg = fila_dados.buscar_por_token(conn, token)
        if not reg:
            return jsonify({"erro": MSG_LINK}), 404
        try:
            fila_dados.mudar_status(conn, reg["id"], "sair")
        except fila.TransicaoInvalida as e:
            conn.rollback()
            return jsonify({"erro": str(e)}), 409
    finally:
        conn.close()
    return jsonify({"mensagem": "Você saiu da fila."})


# -------------------------------------------------------
# PAINEL — A FILA DO DIA
# -------------------------------------------------------

MSG_CHAMADO_PENDENTE = "Resolva o cliente chamado antes de chamar outro."
MSG_NINGUEM = "Ninguém esperando."
MSG_ESCOPO = "Esse cliente está na fila de outro barbeiro."


def _pode_mexer(reg, escopo):
    """Barbeiro mexe só na própria fila e na de qualquer um; dono e salão em todas."""
    if escopo is None:
        return True
    return reg["barbeiro_id"] in (None, escopo) or reg["atendido_por"] == escopo


def _barbeiro_da_acao(dados, escopo):
    """Barbeiro que está agindo: o próprio (papel barbeiro) ou o escolhido no corpo."""
    if escopo is not None:
        return escopo
    bid = dados.get("barbeiro_id")
    return bid if type(bid) is int and bid > 0 else None


def _whatsapp(reg, nome_barbeiro):
    telefone = reg.get("telefone")
    if not telefone:
        return None
    primeiro = (reg.get("nome") or "").strip().split(" ")[0] or "tudo bem"
    mensagem = (f"Fala, {primeiro}! Chegou sua vez na JP Barbearia com o {nome_barbeiro}. "
                f"Você tem {TOLERANCIA_FILA_MIN} min pra chegar.")
    return notificacoes.link_whatsapp(telefone, mensagem)


def _serializar(reg, estimativa=None):
    inicio = estimativa.get(reg["id"]) if estimativa else None
    return {
        "id": reg["id"], "nome": reg["nome"], "telefone": reg.get("telefone"),
        "servico": reg.get("servico"), "barbeiro_id": reg["barbeiro_id"],
        "atendido_por": reg["atendido_por"], "status": reg["status"],
        "entrou_em": _iso(reg.get("entrou_em")), "chamado_em": _iso(reg.get("chamado_em")),
        "inicio_estimado": min_para_hhmm(inicio[1]) if inicio else None,
        "agendamento_id": reg.get("agendamento_id"), "origem": reg.get("origem"),
    }


def _chamado_pendente(dia, barbeiro_id):
    return any(l.status == "chamado" and l.atendido_por == barbeiro_id for l in dia.lugares)


@app.route("/api/admin/fila", methods=["GET"])
@token_requerido
def painel_fila():
    hoje = data_hoje().isoformat()
    escopo = barbeiro_do_escopo()
    conn = get_connection()
    regs = fila_dados.lugares_do_dia(conn, hoje)
    dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
    dia_de_fila = fila_dados.dia_de_fila(conn, hoje)
    conn.close()
    est = fila.estimar_fila(dia.agora_min, dia.barbeiros, dia.lugares)
    lugares = []
    for r in regs:
        if not _pode_mexer(r, escopo):
            continue
        item = _serializar(r, est)
        item["whatsapp"] = (_whatsapp(r, dia.nomes.get(r["atendido_por"], ""))
                            if r["status"] == "chamado" else None)
        lugares.append(item)
    return jsonify({
        "dia_de_fila": dia_de_fila,
        "lugares": lugares,
        "barbeiros": [{"id": b.id, "nome": dia.nomes.get(b.id)} for b in dia.barbeiros
                      if escopo is None or b.id == escopo],
        "indisponiveis": sorted(dia.indisponiveis),
        "tolerancia_min": TOLERANCIA_FILA_MIN,
    })


@app.route("/api/admin/fila", methods=["POST"])
@token_requerido
def por_na_fila():
    """Quem chegou na porta. Telefone opcional; vale mesmo com a fila "fechada"."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Dados inválidos."}), 400
    nome = dados.get("nome")
    telefone = dados.get("telefone") or ""
    for valido, erro in (validar_nome(nome), validar_telefone(telefone)):
        if not valido:
            return jsonify({"erro": erro}), 400
    numero = re.sub(r"\D", "", telefone) or None
    escopo = barbeiro_do_escopo()
    barbeiro_id = dados.get("barbeiro_id")
    if barbeiro_id is not None and (type(barbeiro_id) is not int or barbeiro_id <= 0):
        return jsonify({"erro": "Barbeiro inválido."}), 400
    if escopo is not None and barbeiro_id not in (None, escopo):
        return jsonify({"erro": MSG_ESCOPO}), 403

    hoje = data_hoje().isoformat()
    conn = get_connection()
    try:
        servico = servico_na_data(conn, dados.get("servico_id"), hoje)
        if not servico:
            return jsonify({"erro": "Serviço indisponível."}), 400
        if numero and fila_dados.eh_telefone_generico(conn, numero):
            numero = None
        if numero and fila_dados.telefone_na_fila(conn, hoje, numero):
            return jsonify({"erro": MSG_REPETIDO}), 409
        cliente_id = buscar_ou_criar_cliente(conn, nome.strip(), numero, None)
        try:
            lugar_id, _ = fila_dados.inserir_lugar(
                conn, data=hoje, nome=nome.strip(), telefone=numero,
                servico_id=dados.get("servico_id"), duracao_min=servico["duracao_min"],
                barbeiro_id=barbeiro_id, origem="painel", cliente_id=cliente_id,
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return jsonify({"erro": MSG_REPETIDO}), 409
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": lugar_id}), 201


@app.route("/api/admin/fila/chamar-proximo", methods=["POST"])
@token_requerido
def chamar_proximo():
    dados = request.get_json(silent=True) or {}
    barbeiro_id = _barbeiro_da_acao(dados, barbeiro_do_escopo())
    if barbeiro_id is None:
        return jsonify({"erro": "Escolha o barbeiro."}), 400
    hoje = data_hoje().isoformat()
    conn = get_connection()
    try:
        # Trava a fila: dois barbeiros apertando juntos não chamam a mesma pessoa.
        conn.execute("SELECT id FROM barbeiros WHERE ativo = 1 FOR UPDATE")
        dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
        if _chamado_pendente(dia, barbeiro_id):
            conn.rollback()
            return jsonify({"erro": MSG_CHAMADO_PENDENTE}), 409
        proximo = fila.escolher_proximo(dia.lugares, barbeiro_id)
        if not proximo:
            conn.rollback()
            return jsonify({"erro": MSG_NINGUEM}), 404
        reg = fila_dados.mudar_status(conn, proximo.id, "chamar", barbeiro_id=barbeiro_id)
    finally:
        conn.close()
    return jsonify({"lugar": _serializar(reg),
                    "whatsapp": _whatsapp(reg, dia.nomes.get(barbeiro_id, ""))})


@app.route("/api/admin/fila/<int:lugar_id>/<acao>", methods=["POST"])
@token_requerido
def acao_na_fila(lugar_id, acao):
    acoes = {"chamar": "chamar", "comecar": "comecar", "concluir": "concluir",
             "nao-veio": "nao_veio", "remover": "remover"}
    if acao not in acoes:
        return jsonify({"erro": "Ação desconhecida."}), 404
    acao = acoes[acao]
    dados = request.get_json(silent=True) or {}
    escopo = barbeiro_do_escopo()
    hoje = data_hoje().isoformat()
    conn = get_connection()
    try:
        # Trava o lugar até o fim: dois toques em "Começar" não criam dois
        # atendimentos — o segundo espera e já encontra o lugar em atendimento.
        conn.execute("SELECT id FROM fila WHERE id = %s FOR UPDATE", (lugar_id,))
        reg = fila_dados.buscar_por_id(conn, lugar_id)
        if not reg or reg["data"] != hoje:
            return jsonify({"erro": "Cliente não encontrado na fila de hoje."}), 404
        if not _pode_mexer(reg, escopo):
            return jsonify({"erro": MSG_ESCOPO}), 403
        barbeiro_id = (_barbeiro_da_acao(dados, escopo) or reg["atendido_por"]
                       or reg["barbeiro_id"])
        try:
            fila.proximo_status(reg["status"], acao)   # recusa antes de qualquer efeito
            if acao == "chamar":
                if barbeiro_id is None:
                    return jsonify({"erro": "Escolha o barbeiro."}), 400
                dia = fila_dados.carregar_dia(conn, hoje, _agora_min())
                if _chamado_pendente(dia, barbeiro_id):
                    return jsonify({"erro": MSG_CHAMADO_PENDENTE}), 409
                novo = fila_dados.mudar_status(conn, lugar_id, "chamar", barbeiro_id=barbeiro_id)
                nome_barbeiro = dia.nomes.get(barbeiro_id, "")
                return jsonify({"lugar": _serializar(novo),
                                "whatsapp": _whatsapp(novo, nome_barbeiro)})
            if acao == "comecar":
                if barbeiro_id is None:
                    return jsonify({"erro": "Escolha o barbeiro."}), 400
                agora = agora_br()
                corpo, status = _processar_novo_agendamento({
                    "nome_cliente": reg["nome"], "telefone": reg.get("telefone") or "",
                    "servico_id": reg["servico_id"], "barbeiro_id": barbeiro_id,
                    "data": hoje, "hora": agora.strftime("%H:%M"), "origem": "fila",
                }, exigir_antecedencia=False, permitir_conflito=True)
                if status != 201:
                    return jsonify(corpo), status
                novo = fila_dados.mudar_status(conn, lugar_id, "comecar", barbeiro_id=barbeiro_id,
                                               agendamento_id=corpo["agendamento_id"])
                return jsonify({"lugar": _serializar(novo),
                                "agendamento_id": corpo["agendamento_id"]})
            novo = fila_dados.mudar_status(conn, lugar_id, acao)
        except fila.TransicaoInvalida as e:
            conn.rollback()
            return jsonify({"erro": str(e)}), 409
    finally:
        conn.close()
    return jsonify({"lugar": _serializar(novo)})


# -------------------------------------------------------
# PAINEL — PERÍODOS DE FILA (só o dono)
# -------------------------------------------------------


def _data_valida(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date().isoformat() == valor
    except (TypeError, ValueError):
        return False


@app.route("/api/admin/periodos-fila", methods=["GET"])
@token_requerido
@somente_master
def listar_periodos_fila():
    conn = get_connection()
    periodos = fila_dados.listar_periodos(conn)
    conn.close()
    return jsonify(periodos)


@app.route("/api/admin/periodos-fila", methods=["POST"])
@token_requerido
@somente_master
def criar_periodo_fila():
    dados = request.get_json(silent=True) or {}
    inicio, fim = dados.get("inicio"), dados.get("fim")
    if not (_data_valida(inicio) and _data_valida(fim)):
        return jsonify({"erro": "Informe as datas de início e fim."}), 400
    if fim < inicio:
        return jsonify({"erro": "A data de fim vem antes da de início."}), 400
    conn = get_connection()
    periodo_id = fila_dados.criar_periodo(conn, inicio, fim, g.usuario.get("admin_id"))
    conn.close()
    return jsonify({"id": periodo_id}), 201


@app.route("/api/admin/periodos-fila/<int:periodo_id>", methods=["DELETE"])
@token_requerido
@somente_master
def apagar_periodo_fila(periodo_id):
    conn = get_connection()
    apagou = fila_dados.apagar_periodo(conn, periodo_id)
    conn.close()
    if not apagou:
        return jsonify({"erro": "Período não encontrado."}), 404
    return jsonify({"mensagem": "Período apagado."})
