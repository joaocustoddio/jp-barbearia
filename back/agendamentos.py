"""
agendamentos.py
---------------
O núcleo do agendamento, sem rota nenhuma: o que ocupa a agenda
(`janelas_ocupadas`), a criação de um agendamento (compartilhada entre o site
público e o painel) e os helpers de formatação/link de calendário.
"""

import re
from datetime import datetime, timedelta, timezone

from config import (
    DURACAO_ALMOCO_MIN, ENDERECO_BARBEARIA, FUSO_BR, INTERVALO_MINUTOS,
    LIMITE_DIAS_AGENDAMENTO, MARGEM_ULTIMA_ENTRADA_MIN, data_hoje, tolerancia_para,
)
from database import get_connection
from horarios import (
    Janela, hhmm_para_min, cabe_no_expediente, primeiro_conflito,
    mensagem_de_conflito,
)
from validacoes import (
    dia_fechado, horario_efetivo, validar_data, validar_email, validar_hora,
    validar_nome, validar_servico_id, validar_telefone,
)
import notificacoes
import emails


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
    agendados = conn.execute(
        """SELECT agendamentos.hora, servicos.duracao_min
           FROM agendamentos
           JOIN servicos ON agendamentos.servico_id = servicos.id
           WHERE agendamentos.data = %s AND agendamentos.barbeiro_id = %s
             AND agendamentos.status != 'cancelado'""",
        (data_str, barbeiro_id)
    ).fetchall()

    bloqueios = conn.execute(
        """SELECT hora, duracao_min, motivo FROM bloqueios
           WHERE data = %s AND (barbeiro_id IS NULL OR barbeiro_id = %s)""",
        (data_str, barbeiro_id)
    ).fetchall()

    linha_barbeiro = conn.execute(
        "SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    almoco_fixo = linha_barbeiro["almoco_fixo"] if linha_barbeiro else None

    return montar_janelas(agendados, bloqueios, almoco_fixo, duracao_padrao)


def montar_janelas(agendados, bloqueios, almoco_fixo, duracao_padrao):
    """
    Transforma as linhas já lidas do banco em janelas ocupadas. SEM tocar no
    banco: existe pra quem buscou vários dias de uma vez montar as janelas com
    exatamente esta regra, em vez de reescrever e arriscar divergir.
    """
    janelas = []

    for linha in agendados:
        inicio = hhmm_para_min(linha["hora"])
        janelas.append(Janela(inicio, inicio + (linha["duracao_min"] or duracao_padrao), "agendamento"))

    dia_bloqueado = False
    for linha in bloqueios:
        if linha["hora"] is None:            # bloqueio de dia inteiro (feriado/folga)
            dia_bloqueado = True
            continue
        inicio = hhmm_para_min(linha["hora"])
        # O almoço manual é gravado como bloqueio com motivo 'Almoço' — separar
        # o motivo deixa a mensagem de erro específica pro barbeiro.
        motivo = "almoco" if (linha["motivo"] or "").strip().lower().startswith("almo") else "bloqueio"
        janelas.append(Janela(inicio, inicio + (linha["duracao_min"] or INTERVALO_MINUTOS), motivo))

    if almoco_fixo:
        inicio = hhmm_para_min(almoco_fixo)
        janelas.append(Janela(inicio, inicio + DURACAO_ALMOCO_MIN, "almoco"))

    return janelas, dia_bloqueado


# Mensagem que o cliente bloqueado vê no site. De propósito ela NÃO diz
# "você foi bloqueado": o objetivo é que a conversa aconteça entre a pessoa e a
# barbearia, não que o sistema anuncie a decisão e gere discussão no balcão.
# Também não é mentira — pelo site realmente não dá.
MENSAGEM_CLIENTE_BLOQUEADO = (
    "Não foi possível concluir o agendamento pelo site. "
    "Entre em contato com a barbearia para marcar seu horário."
)


def so_digitos(telefone):
    """'(11) 98888-7777' -> '11988887777'. Como o telefone é comparado no banco."""
    return re.sub(r"\D", "", telefone or "")


def cliente_esta_bloqueado(conn, telefone):
    """True se ESTE telefone está na lista de bloqueados."""
    numero = so_digitos(telefone)
    if not numero:
        return False
    achou = conn.execute(
        "SELECT 1 FROM clientes_bloqueados WHERE telefone = %s", (numero,)
    ).fetchone()
    return achou is not None


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
                                checar_bloqueio=False):
    """
    Valida e insere um novo agendamento. Compartilhado entre duas rotas:
    - PÚBLICA (cliente pelo site): exigir_antecedencia=True, exigir_telefone=True
      (não dá pra agendar "em cima da hora" e o telefone é obrigatório)
    - ADMIN (barbeiro registrando walk-in): ambos False
      (registra corte de agora e o telefone é opcional)

    permitir_conflito=True libera sobrescrever/encaixar em cima de outro horário
    (o barbeiro pelo painel pode; o cliente pelo site NÃO).
    dados["encaixe"] marca o agendamento como encaixe (encaixe do caderninho).

    checar_bloqueio=True recusa cliente da lista de bloqueados. Vale SÓ no fluxo
    público: se o barbeiro está anotando pelo caderninho, é porque decidiu
    atender — o sistema não manda nele.

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

    # Telefone: obrigatório no site (é como a barbearia fala com o cliente),
    # opcional no caderninho do barbeiro — ali o cliente já está na cadeira.
    # E-mail: sempre opcional. Tem cliente que não usa, e travar o agendamento
    # por causa disso custaria mais do que ganhar. Quem informar recebe a
    # confirmação e o lembrete; quem não informar, não recebe.
    if exigir_telefone and not (dados.get("telefone") or "").strip():
        conn.close()
        return {"erro": "Telefone é obrigatório"}, 400

    # Cliente bloqueado (só no fluxo público). Fica ANTES das outras validações
    # pra não gastar consulta à toa, e devolve 403 — não é dado inválido, é
    # permissão negada.
    if checar_bloqueio and cliente_esta_bloqueado(conn, dados.get("telefone")):
        conn.close()
        return {"erro": MENSAGEM_CLIENTE_BLOQUEADO}, 403

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
