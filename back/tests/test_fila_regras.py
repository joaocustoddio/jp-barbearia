"""
Regras puras da fila (fila.py): estimativa, posição, próximo da vez e
transições. Nada de banco nem Flask.
"""
import pytest

import fila
from fila import BarbeiroDia, Lugar, TransicaoInvalida
from horarios import Janela

AGORA = 600  # 10:00


def barbeiro(id=1, livre=AGORA, ocupadas=(), abertura=540, fechamento=1200):
    return BarbeiroDia(id=id, abertura=abertura, fechamento=fechamento,
                       livre_a_partir=livre, ocupadas=tuple(ocupadas))


def lugar(id, barbeiro_id=1, dur=30, status="aguardando", atendido_por=None):
    return Lugar(id=id, barbeiro_id=barbeiro_id, atendido_por=atendido_por,
                 status=status, duracao_min=dur)


# ---------------------------------------------------------------- estimar_fila

def test_um_barbeiro_em_sequencia():
    r = fila.estimar_fila(AGORA, [barbeiro()], [lugar(1), lugar(2), lugar(3)])
    assert r == {1: (1, 600), 2: (1, 630), 3: (1, 660)}


def test_qualquer_um_vai_com_quem_libera_primeiro():
    r = fila.estimar_fila(AGORA, [barbeiro(1, livre=640), barbeiro(2, livre=610)],
                          [lugar(1, barbeiro_id=None)])
    assert r[1] == (2, 610)


def test_empate_vai_para_o_menor_id():
    r = fila.estimar_fila(AGORA, [barbeiro(2), barbeiro(1)], [lugar(1, barbeiro_id=None)])
    assert r[1] == (1, 600)


def test_pula_o_almoco():
    b = barbeiro(ocupadas=[Janela(660, 720, "almoco")])
    r = fila.estimar_fila(AGORA, [b], [lugar(1, dur=40), lugar(2, dur=40)])
    assert r == {1: (1, 600), 2: (1, 720)}


def test_chamado_vem_antes_de_quem_aguarda():
    r = fila.estimar_fila(AGORA, [barbeiro()],
                          [lugar(1), lugar(5, barbeiro_id=None, status="chamado", atendido_por=1)])
    assert r[5] == (1, 600)
    assert r[1] == (1, 630)


def test_barbeiro_indisponivel_fica_fora():
    r = fila.estimar_fila(AGORA, [barbeiro(1)], [lugar(1, barbeiro_id=3), lugar(2)])
    assert 1 not in r
    assert r[2] == (1, 600)


# ---------------------------------------------------------------- cabe_mais_um

def test_cabe_mais_um_recusa_perto_de_fechar():
    b = barbeiro(livre=1125, fechamento=1140)
    assert fila.cabe_mais_um(1125, [b], [], 1, 40) is False
    assert fila.cabe_mais_um(1125, [b], [], 1, 10) is True


def test_cabe_mais_um_qualquer_um_basta_um():
    cheio = barbeiro(1, livre=1195, fechamento=1200)
    livre = barbeiro(2)
    assert fila.cabe_mais_um(AGORA, [cheio, livre], [], None, 30) is True
    assert fila.cabe_mais_um(AGORA, [cheio], [], 1, 30) is False


def test_cabe_mais_um_barbeiro_fora_do_dia():
    assert fila.cabe_mais_um(AGORA, [barbeiro(1)], [], 3, 30) is False


# ---------------------------------------------------------------- posicao e espera

def test_posicao_conta_so_concorrentes():
    lugares = [lugar(1, barbeiro_id=1), lugar(2, barbeiro_id=2),
               lugar(3, barbeiro_id=None), lugar(4, barbeiro_id=1)]
    assert fila.posicao(4, lugares) == 3
    assert fila.posicao(2, lugares) == 1
    assert fila.posicao(3, lugares) == 3


def test_posicao_conta_chamado_do_mesmo_barbeiro():
    lugares = [lugar(1, barbeiro_id=None, status="chamado", atendido_por=1),
               lugar(2, barbeiro_id=1)]
    assert fila.posicao(2, lugares) == 2


def test_arredondar_espera():
    assert [fila.arredondar_espera(m) for m in (0, 1, 35, 36, -3)] == [0, 5, 35, 40, 0]


# ---------------------------------------------------------------- escolher_proximo

def test_escolher_proximo_pega_o_mais_antigo_entre_meu_e_qualquer():
    lugares = [lugar(1, barbeiro_id=2), lugar(2, barbeiro_id=None), lugar(3, barbeiro_id=1)]
    assert fila.escolher_proximo(lugares, 1).id == 2
    assert fila.escolher_proximo(lugares, 2).id == 1


def test_escolher_proximo_ignora_chamados():
    lugares = [lugar(1, barbeiro_id=None, status="chamado", atendido_por=2), lugar(2)]
    assert fila.escolher_proximo(lugares, 1).id == 2
    assert fila.escolher_proximo([lugar(1, status="chamado", atendido_por=1)], 1) is None


# ---------------------------------------------------------------- transições

def test_transicoes_validas():
    assert fila.proximo_status("aguardando", "chamar") == "chamado"
    assert fila.proximo_status("aguardando", "comecar") == "atendendo"
    assert fila.proximo_status("aguardando", "sair") == "saiu"
    assert fila.proximo_status("aguardando", "remover") == "removido"
    assert fila.proximo_status("chamado", "comecar") == "atendendo"
    assert fila.proximo_status("chamado", "nao_veio") == "nao_veio"
    assert fila.proximo_status("atendendo", "concluir") == "concluido"


@pytest.mark.parametrize("atual,acao", [
    ("aguardando", "concluir"), ("aguardando", "nao_veio"),
    ("concluido", "chamar"), ("saiu", "comecar"), ("atendendo", "sair"),
])
def test_transicoes_invalidas(atual, acao):
    with pytest.raises(TransicaoInvalida):
        fila.proximo_status(atual, acao)
