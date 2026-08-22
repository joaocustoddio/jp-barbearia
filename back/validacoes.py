"""
validacoes.py
-------------
Funções de validação dos campos que chegam nas rotas e o cálculo do horário
que vale pra cada barbeiro no dia. Cada validador retorna (True, None) se
válido ou (False, "mensagem de erro") se inválido.
"""

import re
from datetime import datetime

from config import (
    ANTECEDENCIA_MINIMA, BARBEIRO_DONO_ID, DIAS_FECHADOS, FOLGA_DONO_WEEKDAY,
    HORARIO_ABERTURA, HORARIO_FECHAMENTO, HORARIOS_ESPECIAIS, NOMES_DIAS,
    PISO_ABERTURA_OUTROS, agora_br, data_hoje,
)
from horarios import hhmm_para_min


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
