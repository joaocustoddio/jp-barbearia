"""
config.py
---------
Configurações que vêm do .env e as constantes de negócio (horários, tolerâncias,
dias fechados, dono). Sem Flask e sem banco aqui: é a camada mais baixa, que
todos os outros módulos importam.
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from horarios import hhmm_para_min

# Carrega as variáveis do arquivo .env
# Se não existir o .env, usa os valores padrão definidos abaixo
load_dotenv()

# -------------------------------------------------------
# CONFIGURAÇÕES VIA VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------
SECRET_KEY_PADRAO = "dev-inseguro-troque-em-producao"

FLASK_ENV      = os.getenv("FLASK_ENV", "development")
CORS_ORIGIN    = os.getenv("CORS_ORIGIN", "*")
EM_PRODUCAO    = FLASK_ENV == "production"

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

# Como uma FOLGA fica gravada na tabela de expedientes: início igual ao fim,
# ou seja, jornada de duração zero — nenhum horário é gerado.
HORA_FOLGA            = "00:00"


def expediente_e_folga(inicio, fim):
    """
    True se este expediente representa folga. Aceita também o formato antigo
    (00:00–00:01), usado antes de existir o botão de folga.
    """
    try:
        return hhmm_para_min(fim) - hhmm_para_min(inicio) <= 1
    except (ValueError, TypeError):
        return False

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
