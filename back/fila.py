"""
fila.py
-------
Regras da fila online, sem banco e sem Flask: quem começa quando (estimativa),
em que posição cada um está, quem é o próximo da vez e quais mudanças de
estado valem. O acesso ao banco fica em fila_dados.py.

Tempo sempre em minutos desde 00:00 do dia.
"""

from dataclasses import dataclass

from config import MARGEM_ULTIMA_ENTRADA_MIN, tolerancia_para
from horarios import cabe_no_expediente, min_para_hhmm, primeiro_conflito

ATIVOS = ("aguardando", "chamado")


@dataclass(frozen=True)
class BarbeiroDia:
    id: int
    abertura: int          # minutos desde 00:00
    fechamento: int
    livre_a_partir: int    # já é max(agora, fim do atendimento em curso)
    ocupadas: tuple        # tuple[Janela]: almoço, bloqueios, agendamentos


@dataclass(frozen=True)
class Lugar:
    id: int                   # ordem de entrada: id menor entrou antes
    barbeiro_id: object       # escolhido; None = qualquer um
    atendido_por: object      # barbeiro que chamou (quando chamado)
    status: str               # 'aguardando' | 'chamado'
    duracao_min: int


class TransicaoInvalida(Exception):
    """Mudança de estado que não faz sentido (ex.: concluir quem não começou)."""


# -------------------------------------------------------
# ESTIMATIVA
# -------------------------------------------------------

def _primeiro_inicio(livre, duracao, ocupadas):
    """Primeiro minuto >= livre em que o serviço não colide com nada."""
    inicio = livre
    while True:
        janela = primeiro_conflito(inicio, duracao, ocupadas)
        if janela is None:
            return inicio
        inicio = max(inicio + 1, janela.fim)


def _ordem(lugares):
    """Chamados primeiro (já estão indo pra cadeira), depois quem aguarda."""
    chamados = sorted((l for l in lugares if l.status == "chamado"), key=lambda l: l.id)
    aguardando = sorted((l for l in lugares if l.status == "aguardando"), key=lambda l: l.id)
    return chamados + aguardando


def estimar_fila(agora_min, barbeiros, lugares):
    """
    {lugar.id: (barbeiro_id, inicio_min)} simulando a fila em ordem. "Qualquer
    um" vai com quem libera primeiro (empate: menor id). Lugar cujo barbeiro não
    está trabalhando hoje fica fora do resultado.
    """
    por_id = {b.id: b for b in barbeiros}
    # Ninguém começa antes de o barbeiro chegar; e quem já foi embora não recebe ninguém.
    livre = {b.id: max(agora_min, b.livre_a_partir, b.abertura) for b in barbeiros}
    resultado = {}
    for l in _ordem(lugares):
        if l.status == "chamado":
            candidatos = [l.atendido_por]
        elif l.barbeiro_id is not None:
            candidatos = [l.barbeiro_id]
        else:
            candidatos = sorted(livre)
        opcoes = []
        for c in candidatos:
            if c not in livre:
                continue
            inicio = _primeiro_inicio(livre[c], l.duracao_min, por_id[c].ocupadas)
            if inicio >= por_id[c].fechamento and l.status != "chamado":
                continue
            opcoes.append((inicio, c))
        if not opcoes:
            continue
        inicio, vencedor = min(opcoes)
        resultado[l.id] = (vencedor, inicio)
        livre[vencedor] = inicio + l.duracao_min
    return resultado


def cabe_mais_um(agora_min, barbeiros, lugares, barbeiro_id, duracao_min):
    """
    True se um novo lugar (de `barbeiro_id`, ou qualquer um se None) ainda é
    atendido dentro do expediente, com as mesmas regras de fim de dia do
    agendamento. Pra "qualquer um" basta caber em um barbeiro.
    """
    por_id = {b.id: b for b in barbeiros}
    alvos = sorted(por_id) if barbeiro_id is None else [barbeiro_id]
    for alvo in alvos:
        if alvo not in por_id:
            continue
        novo = Lugar(id=10 ** 9, barbeiro_id=alvo, atendido_por=None,
                     status="aguardando", duracao_min=duracao_min)
        estimativa = estimar_fila(agora_min, barbeiros, list(lugares) + [novo])
        if novo.id not in estimativa:
            continue
        _, inicio = estimativa[novo.id]
        b = por_id[alvo]
        if cabe_no_expediente(inicio, duracao_min, min_para_hhmm(b.abertura),
                              min_para_hhmm(b.fechamento), tolerancia_para(duracao_min),
                              MARGEM_ULTIMA_ENTRADA_MIN):
            return True
    return False


# -------------------------------------------------------
# POSIÇÃO, ESPERA E PRÓXIMO DA VEZ
# -------------------------------------------------------

def _concorre(outro, meu_barbeiro):
    """`outro` está na frente de quem escolheu `meu_barbeiro` (None = qualquer um)?"""
    if meu_barbeiro is None:
        return True
    if outro.status == "chamado":
        return outro.atendido_por == meu_barbeiro
    return outro.barbeiro_id in (None, meu_barbeiro)


def posicao(lugar_id, lugares):
    """1 + quantos lugares ativos estão na frente e disputam o mesmo barbeiro."""
    ordem = _ordem(lugares)
    meu = next(l for l in ordem if l.id == lugar_id)
    if meu.status == "chamado":
        return 1
    frente = ordem[:ordem.index(meu)]
    return 1 + sum(1 for o in frente if _concorre(o, meu.barbeiro_id))


def arredondar_espera(minutos):
    """Para cima, de 5 em 5 minutos, nunca negativo."""
    if minutos <= 0:
        return 0
    return -(-minutos // 5) * 5


def escolher_proximo(lugares, barbeiro_id):
    """O mais antigo aguardando entre a fila de `barbeiro_id` e a de qualquer um."""
    candidatos = [l for l in lugares if l.status == "aguardando"
                  and l.barbeiro_id in (None, barbeiro_id)]
    return min(candidatos, key=lambda l: l.id) if candidatos else None


# -------------------------------------------------------
# TRANSIÇÕES
# -------------------------------------------------------

TRANSICOES = {
    "chamar":   ({"aguardando"}, "chamado"),
    "comecar":  ({"aguardando", "chamado"}, "atendendo"),
    "concluir": ({"atendendo"}, "concluido"),
    "nao_veio": ({"chamado"}, "nao_veio"),
    "remover":  ({"aguardando", "chamado"}, "removido"),
    "sair":     ({"aguardando", "chamado"}, "saiu"),
}

MENSAGENS_TRANSICAO = {
    "chamar": "Só dá pra chamar quem está aguardando.",
    "comecar": "Esse cliente não está mais na fila.",
    "concluir": "Só dá pra concluir um atendimento que começou.",
    "nao_veio": "Só dá pra marcar \"Não veio\" em quem foi chamado.",
    "remover": "Esse cliente não está mais na fila.",
    "sair": "Esse lugar não está mais na fila.",
}


def proximo_status(atual, acao):
    """Novo status depois de `acao`, ou TransicaoInvalida com a explicação."""
    if acao not in TRANSICOES:
        raise TransicaoInvalida("Ação desconhecida.")
    origens, destino = TRANSICOES[acao]
    if atual not in origens:
        raise TransicaoInvalida(MENSAGENS_TRANSICAO[acao])
    return destino
