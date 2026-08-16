"""
horarios.py
-----------
Motor de horários da barbearia. Toda a matemática de agenda mora aqui.

É código PURO: não conhece Flask, banco de dados nem .env. Recebe números e
strings, devolve números e strings. Isso traz três ganhos:

1. Dá pra testar tudo sem subir servidor nem banco (ver tests/test_horarios.py).
2. A regra fica num lugar só, fácil de ler e de mudar.
3. E o mais importante: a LISTA de horários livres e a VALIDAÇÃO na hora de
   agendar passam a usar exatamente a mesma conta. Quando essas duas contas
   moram em lugares diferentes, elas divergem com o tempo — e aparece o
   clássico "vejo o horário na tela mas não consigo marcar" (ou o contrário,
   que é pior: marcar em cima do almoço).

Convenções:
- "minuto do dia": inteiro contado desde 00:00 — 0 = 00:00, 540 = 09:00.
- "janela": intervalo MEIO-ABERTO [inicio, fim). Um corte das 09:00 às 09:40
  ocupa (540, 580), então quem começa exatamente às 09:40 NÃO conflita com ele.
  É isso que permite encaixar um corte logo na sequência do outro.
"""

from collections import namedtuple

# Menor passo aceitável pra um slot. Protege contra duração 0/None/negativa
# gerar loop infinito ou uma lista absurda de horários.
MIN_DURACAO_MIN = 5


class Janela(namedtuple("Janela", "inicio fim motivo")):
    """
    Um trecho ocupado do dia, em minutos: [inicio, fim).
    `motivo` diz de onde veio ("agendamento", "almoco", "bloqueio") e serve pra
    explicar ao barbeiro POR QUE aquele horário não está livre.
    """
    __slots__ = ()

    def __new__(cls, inicio, fim, motivo="ocupado"):
        return super().__new__(cls, int(inicio), int(fim), motivo)


MENSAGEM_POR_MOTIVO = {
    "agendamento": "Esse horário já foi reservado com este barbeiro. Escolha outro.",
    "almoco": "Esse horário cai no almoço do barbeiro. Escolha outro.",
    "bloqueio": "Esse horário está bloqueado na agenda do barbeiro. Escolha outro.",
}
MENSAGEM_PADRAO = "Esse horário não está disponível. Escolha outro."


def mensagem_de_conflito(janela):
    """Mensagem amigável explicando por que o horário não serve."""
    if janela is None:
        return MENSAGEM_PADRAO
    return MENSAGEM_POR_MOTIVO.get(janela.motivo, MENSAGEM_PADRAO)


def hhmm_para_min(hhmm):
    """
    'HH:MM' -> minutos desde 00:00.
    Tolerante com o que vem do banco: aceita '9:00' e '09:00:00' também.
    """
    if hhmm is None:
        raise ValueError("horário vazio")
    partes = str(hhmm).strip().split(":")
    if len(partes) < 2:
        raise ValueError("horário inválido: %r" % (hhmm,))
    return int(partes[0]) * 60 + int(partes[1])


def min_para_hhmm(minutos):
    """Minutos desde 00:00 -> 'HH:MM'."""
    minutos = int(minutos)
    return "%02d:%02d" % (minutos // 60, minutos % 60)


def _duracao_segura(duracao):
    """Duração utilizável em minutos (nunca 0/None/negativa)."""
    try:
        valor = int(duracao)
    except (TypeError, ValueError):
        valor = 0
    return max(valor, MIN_DURACAO_MIN)


def normalizar_janelas(janelas):
    """Ordena por início e descarta janelas vazias ou invertidas."""
    return sorted((j for j in janelas if j.fim > j.inicio),
                  key=lambda j: (j.inicio, j.fim))


def sobrepoe(a_inicio, a_fim, b_inicio, b_fim):
    """
    True se [a_inicio, a_fim) cruza [b_inicio, b_fim).
    Encostar não é cruzar: 09:00–09:40 e 09:40–10:00 conviven numa boa.
    """
    return a_inicio < b_fim and b_inicio < a_fim


def intervalos_livres(abertura, fechamento, ocupadas):
    """
    Brechas livres dentro de [abertura, fechamento), descontando as janelas
    ocupadas. Aceita janelas fora de ordem e sobrepostas entre si.
    Devolve lista de (inicio, fim) em minutos, ordenada e sem sobreposição.
    """
    livres = []
    cursor = abertura
    for janela in normalizar_janelas(ocupadas):
        if janela.fim <= cursor:            # já ficou pra trás
            continue
        if janela.inicio > cursor:          # sobrou espaço livre antes dela
            livres.append((cursor, min(janela.inicio, fechamento)))
        cursor = max(cursor, janela.fim)
        if cursor >= fechamento:
            break
    if cursor < fechamento:
        livres.append((cursor, fechamento))
    return [(a, b) for (a, b) in livres if b > a]


def gerar_slots(abertura, fechamento, ocupadas, duracao, tolerancia_fim=0,
                margem_ultima_entrada=0):
    """
    Os horários oferecidos ao cliente, de forma DINÂMICA: em cada brecha livre
    começa no início da brecha (logo após o corte anterior, o almoço ou a
    abertura) e vai somando a duração enquanto o serviço couber INTEIRO.

    Assim nenhuma brecha é desperdiçada: um corte que termina 18:10 libera o
    próximo às 18:10 — e não às 18:40, como faria uma grade fixa ancorada na
    abertura.

    Duas regras cuidam do fim do dia:

    - `tolerancia_fim`: quantos minutos o serviço pode passar do fechamento, pra
      não perder o último corte por 10 minutinhos. Vale SÓ na brecha que encosta
      no horário de fechar — nunca invade o almoço nem o próximo cliente.
    - `margem_ultima_entrada`: a última entrada é `fechamento - margem`. Impede
      alguém de entrar em cima da hora de fechar (ex: margem 10 e fechamento
      20:00 → ninguém começa depois das 19:50).

    abertura/fechamento: 'HH:MM'. ocupadas: lista de Janela. duracao: minutos.
    """
    inicio_dia = hhmm_para_min(abertura)
    fim_dia = hhmm_para_min(fechamento)
    passo = _duracao_segura(duracao)
    ultima_entrada = fim_dia - margem_ultima_entrada

    slots = []
    for (livre_inicio, livre_fim) in intervalos_livres(inicio_dia, fim_dia, ocupadas):
        # Só a última brecha do dia (a que termina na hora de fechar) ganha a
        # tolerância. As outras terminam onde começa o próximo compromisso.
        limite = (livre_fim + tolerancia_fim) if livre_fim == fim_dia else livre_fim
        momento = livre_inicio
        while momento + passo <= limite and momento <= ultima_entrada:
            slots.append(min_para_hhmm(momento))
            momento += passo
    return slots


def cabe_no_expediente(inicio, duracao, abertura, fechamento, tolerancia_fim=0,
                       margem_ultima_entrada=0):
    """
    True se o serviço cabe no expediente do barbeiro. Aplica as MESMAS regras de
    fim de dia usadas pra gerar os horários (tolerância e última entrada) — as
    duas contas precisam bater, senão volta o "vejo o horário mas não marco".
    """
    passo = _duracao_segura(duracao)
    fim_dia = hhmm_para_min(fechamento)
    return (inicio >= hhmm_para_min(abertura)
            and inicio <= fim_dia - margem_ultima_entrada
            and inicio + passo <= fim_dia + tolerancia_fim)


def primeiro_conflito(inicio, duracao, ocupadas):
    """
    A primeira janela ocupada que colide com [inicio, inicio + duracao),
    ou None se o horário está livre. É esta função que a criação de
    agendamento usa — a mesma base que gera a lista de horários livres.
    """
    passo = _duracao_segura(duracao)
    fim = inicio + passo
    for janela in normalizar_janelas(ocupadas):
        if sobrepoe(inicio, fim, janela.inicio, janela.fim):
            return janela
    return None


def filtrar_por_antecedencia(slots, agora_min, antecedencia_min):
    """
    Tira os horários que já passaram ou estão dentro da antecedência mínima.
    Só faz sentido quando a data consultada é HOJE.
    """
    limite = agora_min + antecedencia_min
    return [h for h in slots if hhmm_para_min(h) > limite]
