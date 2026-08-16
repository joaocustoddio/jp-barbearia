"""
Testes do motor de horários (horarios.py).

Como o módulo é puro (sem Flask e sem banco), estes testes rodam em
milissegundos e cobrem a regra de negócio de ponta a ponta.

Referência rápida de minutos: 08:00=480, 09:00=540, 12:00=720, 18:00=1080.
"""
import pytest

from horarios import (
    Janela, hhmm_para_min, min_para_hhmm, normalizar_janelas, sobrepoe,
    intervalos_livres, gerar_slots, cabe_no_expediente, primeiro_conflito,
    filtrar_por_antecedencia, mensagem_de_conflito, MIN_DURACAO_MIN,
)


# ---------------------------------------------------------------- conversões

def test_hhmm_para_min_basico():
    assert hhmm_para_min("00:00") == 0
    assert hhmm_para_min("09:00") == 540
    assert hhmm_para_min("23:59") == 1439

def test_hhmm_para_min_tolera_formatos_do_banco():
    assert hhmm_para_min("9:00") == 540         # sem zero à esquerda
    assert hhmm_para_min("09:00:00") == 540     # com segundos
    assert hhmm_para_min(" 09:30 ") == 570      # com espaços

def test_hhmm_para_min_rejeita_lixo():
    for ruim in (None, "", "banana", "0900"):
        with pytest.raises(ValueError):
            hhmm_para_min(ruim)

def test_min_para_hhmm_ida_e_volta():
    for texto in ("00:00", "08:20", "12:45", "19:30", "23:59"):
        assert min_para_hhmm(hhmm_para_min(texto)) == texto


# ------------------------------------------------------------------ janelas

def test_normalizar_ordena_e_descarta_vazias():
    janelas = [Janela(600, 640), Janela(540, 580), Janela(700, 700), Janela(800, 790)]
    assert [(j.inicio, j.fim) for j in normalizar_janelas(janelas)] == [(540, 580), (600, 640)]

def test_sobrepoe_encostar_nao_e_sobrepor():
    assert sobrepoe(540, 580, 560, 600) is True     # cruza
    assert sobrepoe(540, 580, 580, 620) is False    # encosta no fim
    assert sobrepoe(580, 620, 540, 580) is False    # encosta no início

def test_janela_guarda_motivo():
    assert Janela(540, 600, "almoco").motivo == "almoco"
    assert Janela(540, 600).motivo == "ocupado"     # padrão


# ------------------------------------------------------- intervalos_livres

def test_dia_inteiro_livre():
    assert intervalos_livres(540, 1200, []) == [(540, 1200)]

def test_buraco_no_meio():
    assert intervalos_livres(540, 700, [Janela(560, 580)]) == [(540, 560), (580, 700)]

def test_janelas_sobrepostas_sao_mescladas():
    assert intervalos_livres(540, 600, [Janela(540, 570), Janela(560, 590)]) == [(590, 600)]

def test_janelas_fora_de_ordem():
    ocupadas = [Janela(600, 620), Janela(550, 560)]
    assert intervalos_livres(540, 700, ocupadas) == [(540, 550), (560, 600), (620, 700)]

def test_janela_fora_do_expediente_e_ignorada():
    assert intervalos_livres(540, 600, [Janela(400, 420)]) == [(540, 600)]

def test_ocupacao_cobrindo_o_dia_todo():
    assert intervalos_livres(540, 600, [Janela(500, 700)]) == []

def test_ocupacao_ultrapassa_o_fechamento():
    assert intervalos_livres(540, 600, [Janela(580, 900)]) == [(540, 580)]

def test_expediente_invertido_ou_vazio():
    assert intervalos_livres(600, 600, []) == []    # folga (abertura == fechamento)
    assert intervalos_livres(700, 600, []) == []    # invertido não quebra


# ------------------------------------------------------------- gerar_slots

def test_dia_vazio_usa_a_duracao_como_passo():
    slots = gerar_slots("09:00", "20:00", [], 40)   # Degradê 40min
    assert slots[0] == "09:00"
    assert slots[1] == "09:40"
    assert "19:00" in slots                         # último da grade (termina 19:40)
    # ...mas ainda é oferecido o encaixe que fecha exato às 20:00.
    assert slots[-1] == "19:20"

def test_encaixa_logo_apos_o_corte_anterior():
    # Social 30min ocupa 09:00–09:30 → Degradê 40min começa 09:30, não 09:40
    ocupadas = [Janela(540, 570, "agendamento")]
    assert gerar_slots("09:00", "20:00", ocupadas, 40)[0] == "09:30"

def test_nao_desperdica_brecha_em_hora_quebrada():
    # corte termina 18:10 (1090) → próximo slot é exatamente 18:10
    ocupadas = [Janela(1050, 1090, "agendamento")]
    slots = gerar_slots("09:00", "20:00", ocupadas, 40)
    assert "18:10" in slots

def test_almoco_bloqueia_a_janela_inteira():
    ocupadas = [Janela(750, 810, "almoco")]         # 12:30–13:30
    slots = gerar_slots("09:00", "20:00", ocupadas, 40)
    assert "11:40" in slots      # último que cabe antes (11:40–12:20)
    assert "13:30" in slots      # primeiro depois do almoço
    assert "12:20" not in slots  # 12:20–13:00 invadiria o almoço
    assert "13:00" not in slots

def test_folga_nao_gera_slot():
    assert gerar_slots("09:00", "09:00", [], 40) == []

def test_servico_que_nao_cabe_ate_o_fechamento():
    assert gerar_slots("19:30", "20:00", [], 40) == []   # só 30min de janela

def test_servico_cabendo_exatamente_ate_o_fechamento():
    assert gerar_slots("19:20", "20:00", [], 40) == ["19:20"]

def test_duracao_invalida_nao_trava():
    # duração 0/None/negativa cai no mínimo seguro, sem loop infinito
    for ruim in (0, None, -30):
        slots = gerar_slots("09:00", "09:30", [], ruim)
        assert slots and slots[0] == "09:00"
        assert len(slots) == 30 // MIN_DURACAO_MIN

def test_slots_saem_em_ordem_crescente():
    ocupadas = [Janela(600, 660, "agendamento"), Janela(750, 810, "almoco")]
    slots = gerar_slots("08:00", "20:00", ocupadas, 30)
    assert slots == sorted(slots)


# ------------------------------------------------------ conflito/expediente

def test_sem_conflito_quando_apenas_encosta():
    ocupadas = [Janela(540, 580, "agendamento")]    # 09:00–09:40
    assert primeiro_conflito(580, 30, ocupadas) is None      # começa 09:40
    assert primeiro_conflito(510, 30, ocupadas) is None      # termina 09:00

def test_conflito_em_todas_as_formas_de_sobreposicao():
    ocupadas = [Janela(540, 580, "agendamento")]
    assert primeiro_conflito(550, 10, ocupadas) is not None   # contido
    assert primeiro_conflito(530, 20, ocupadas) is not None   # invade o início
    assert primeiro_conflito(570, 30, ocupadas) is not None   # invade o fim
    assert primeiro_conflito(500, 120, ocupadas) is not None  # engloba

def test_conflito_devolve_a_janela_com_o_motivo():
    ocupadas = [Janela(750, 810, "almoco"), Janela(540, 580, "agendamento")]
    assert primeiro_conflito(760, 30, ocupadas).motivo == "almoco"
    assert primeiro_conflito(545, 30, ocupadas).motivo == "agendamento"

def test_mensagem_por_motivo():
    assert "almoço" in mensagem_de_conflito(Janela(0, 60, "almoco")).lower()
    assert "reservado" in mensagem_de_conflito(Janela(0, 60, "agendamento")).lower()
    assert "bloqueado" in mensagem_de_conflito(Janela(0, 60, "bloqueio")).lower()
    assert mensagem_de_conflito(None)            # sem conflito ainda dá mensagem

def test_cabe_no_expediente():
    assert cabe_no_expediente(540, 40, "09:00", "20:00") is True
    assert cabe_no_expediente(530, 40, "09:00", "20:00") is False   # antes de abrir
    assert cabe_no_expediente(1170, 40, "09:00", "20:00") is False  # 19:30+40 > 20:00
    assert cabe_no_expediente(1160, 40, "09:00", "20:00") is True   # 19:20+40 = 20:00


# ------------------------------------------- tolerância de fechamento (última entrada)

ALMOCO = [Janela(750, 810, "almoco")]      # 12:30–13:30, usado nos cenários abaixo

def test_tolerancia_salva_o_ultimo_corte_do_dia():
    # Degradê 40min, dia com almoço: sem tolerância o último é o encaixe que
    # fecha exato às 20:00 (19:20).
    sem = gerar_slots("09:00", "20:00", ALMOCO, 40)
    assert sem[-1] == "19:20"
    # Com 10min de tolerância entra mais um: 19:30 (termina 20:10).
    com = gerar_slots("09:00", "20:00", ALMOCO, 40, 10)
    assert com[-1] == "19:30"
    assert len(com) == len(sem) + 1

def test_tolerancia_nao_invade_o_almoco():
    # A brecha da manhã termina no almoço, NÃO no fechamento: nada de tolerância.
    # 11:40+40 = 12:20 (cabe); o próximo (12:20) terminaria 13:00, dentro do almoço.
    for tolerancia in (0, 10, 30, 120):
        slots = gerar_slots("09:00", "20:00", ALMOCO, 40, tolerancia)
        assert "12:20" not in slots
        assert "11:40" in slots

def test_tolerancia_nao_invade_o_proximo_cliente():
    # Brecha livre até 16:00 porque tem cliente 16:00–16:40; tolerância não vale ali.
    ocupadas = [Janela(960, 1000, "agendamento")]
    slots = gerar_slots("15:00", "20:00", ocupadas, 40, 30)
    assert "15:40" not in slots            # 15:40+40 = 16:20 pegaria o cliente
    assert "15:00" in slots

def test_tolerancia_curta_deixa_servico_longo_de_fora():
    # Químico de 60min NÃO passa do fechamento com só 10min de tolerância: o
    # último é o encaixe que termina exato às 20:00 (19:00), não 19:30 (20:30).
    curto = gerar_slots("09:00", "20:00", ALMOCO, 60, 10)
    assert curto[-1] == "19:00"
    # Com 30min de tolerância aí sim ele pode passar — a regra escala com a duração.
    longo = gerar_slots("09:00", "20:00", ALMOCO, 60, 30)
    assert longo[-1] == "19:30"            # termina 20:30

def test_tolerancia_zero_e_o_comportamento_antigo():
    assert gerar_slots("09:00", "20:00", ALMOCO, 40, 0) == gerar_slots("09:00", "20:00", ALMOCO, 40)

def test_cabe_no_expediente_com_tolerancia():
    assert cabe_no_expediente(1170, 40, "09:00", "20:00", 10) is True    # 19:30 -> 20:10
    assert cabe_no_expediente(1170, 60, "09:00", "20:00", 10) is False   # 19:30 -> 20:30

# --------------------------------------------------- última entrada (margem)

def test_ninguem_entra_depois_da_ultima_entrada():
    # Fecha 20:00, margem 10 → última entrada 19:50. Serviço de 10min:
    # sem margem entraria às 20:00 (em cima da hora de fechar).
    sem = gerar_slots("09:00", "20:00", ALMOCO, 10, 10)
    assert sem[-1] == "20:00"
    com = gerar_slots("09:00", "20:00", ALMOCO, 10, 10, 10)
    assert com[-1] == "19:50"              # última entrada respeitada

def test_ultima_entrada_acompanha_o_fechamento_do_dia():
    # Sábado fecha 19:00 → última entrada 18:50 (mesma margem de 10min).
    slots = gerar_slots("08:00", "19:00", ALMOCO, 10, 10, 10)
    assert slots[-1] == "18:50"

def test_margem_nao_atrapalha_os_cortes_principais():
    # Degradê e Corte+Barba continuam ganhando o horário extra do fim do dia.
    assert gerar_slots("09:00", "20:00", ALMOCO, 40, 10, 10)[-1] == "19:30"   # termina 20:10
    assert gerar_slots("09:00", "20:00", ALMOCO, 50, 10, 10)[-1] == "19:20"   # termina 20:10

def test_cabe_no_expediente_respeita_a_ultima_entrada():
    assert cabe_no_expediente(1190, 10, "09:00", "20:00", 10, 10) is True    # 19:50 ok
    assert cabe_no_expediente(1200, 10, "09:00", "20:00", 10, 10) is False   # 20:00 barrado

def test_margem_zero_e_o_comportamento_anterior():
    assert (gerar_slots("09:00", "20:00", ALMOCO, 10, 10, 0)
            == gerar_slots("09:00", "20:00", ALMOCO, 10, 10))


# ------------------------------------------- encaixe que fecha exato no fim

def test_barba_ganha_o_horario_que_fecha_exato():
    # A grade da Barba (20min) ancorada no fim do almoço cai em 19:30 e 19:50,
    # pulando o 19:40 — que é justamente o que encerra certinho às 20:00.
    # Serviço curto não usa tolerância (o app passa 0), então 19:40 é o último.
    slots = gerar_slots("09:00", "20:00", ALMOCO, 20, 0, 10)
    assert "19:40" in slots
    assert slots[-1] == "19:40"

def test_encaixe_final_nao_passa_da_ultima_entrada():
    # Serviço de 5min: fecharia exato às 19:55, mas a última entrada é 19:50.
    slots = gerar_slots("09:00", "20:00", [], 5, 10, 10)
    assert "19:55" not in slots
    assert slots[-1] == "19:50"

def test_encaixe_final_nao_aparece_se_nao_couber_na_brecha():
    # Brecha de 15min no fim do dia e serviço de 40min: nada é oferecido.
    ocupadas = [Janela(540, 1185, "agendamento")]     # ocupa até 19:45
    assert gerar_slots("09:00", "20:00", ocupadas, 40, 10, 10) == []

def test_encaixe_final_so_vale_no_fim_do_dia():
    # A brecha da manhã termina no almoço: não ganha encaixe que "fecha exato"
    # nele (12:30 - 40min = 11:50 não é oferecido; a grade para em 11:40).
    slots = gerar_slots("09:00", "20:00", ALMOCO, 40, 10, 10)
    assert "11:50" not in slots
    assert "11:40" in slots


def test_lista_e_validacao_nunca_divergem():
    """
    Blindagem contra o bug clássico "vejo o horário mas não consigo marcar":
    TODO horário oferecido tem que passar na validação de agendamento, em
    vários cenários, durações e combinações de regra de fim de dia.
    """
    cenarios = [
        ("09:00", "20:00", []),
        ("09:00", "20:00", ALMOCO),
        ("08:20", "20:00", ALMOCO + [Janela(540, 580, "agendamento")]),
        ("08:00", "19:00", [Janela(600, 660, "agendamento"), Janela(900, 930, "bloqueio")]),
    ]
    for (abertura, fechamento, ocupadas) in cenarios:
        for duracao in (10, 20, 30, 40, 45, 50, 60):
            for tolerancia in (0, 10, 30):
                for margem in (0, 10, 30):
                    oferecidos = gerar_slots(abertura, fechamento, ocupadas,
                                             duracao, tolerancia, margem)
                    for horario in oferecidos:
                        inicio = hhmm_para_min(horario)
                        contexto = f"{horario} ({duracao}min, tol {tolerancia}, margem {margem})"
                        assert cabe_no_expediente(inicio, duracao, abertura, fechamento,
                                                  tolerancia, margem), \
                            f"{contexto} foi oferecido mas não cabe no expediente"
                        assert primeiro_conflito(inicio, duracao, ocupadas) is None, \
                            f"{contexto} foi oferecido mas conflita com algo ocupado"


# ---------------------------------------------------------- antecedência

def test_filtra_por_antecedencia():
    slots = ["09:00", "09:30", "10:00", "10:30"]
    # agora 09:00 (540) + 15min → só depois de 09:15
    assert filtrar_por_antecedencia(slots, 540, 15) == ["09:30", "10:00", "10:30"]

def test_antecedencia_exclui_o_limite_exato():
    # 09:30 não vale quando o limite é exatamente 09:30 (precisa ser MAIOR)
    assert filtrar_por_antecedencia(["09:30", "09:31"], 540, 30) == ["09:31"]

def test_antecedencia_zero_mantem_futuro():
    assert filtrar_por_antecedencia(["09:00", "09:30"], 540, 0) == ["09:30"]


# ------------------------------------------------- cenário real de ponta a ponta

def test_dia_realista_do_jp():
    """
    Sexta do JP: expediente 08:20–20:00, almoço fixo 12:30–13:30,
    já tem um Degradê 09:00–09:40 e um Corte+Barba 14:00–14:50.
    Cliente quer um Corte Social (30min).
    """
    ocupadas = [
        Janela(540, 580, "agendamento"),   # 09:00–09:40
        Janela(750, 810, "almoco"),        # 12:30–13:30
        Janela(840, 890, "agendamento"),   # 14:00–14:50
    ]
    slots = gerar_slots("08:20", "20:00", ocupadas, 30)

    assert slots[0] == "08:20"        # abre cedo (só o dono)
    assert "09:40" in slots           # encaixa logo após o Degradê
    assert "12:30" not in slots       # almoço
    assert "14:00" not in slots       # ocupado
    assert "14:50" in slots           # logo após o Corte+Barba
    # Depois das 14:50 a grade fica ancorada NELE (14:50, 15:20, 15:50...), o que
    # levaria a 19:20; mas o encaixe final garante o 19:30, que fecha exato 20:00.
    assert "19:20" in slots
    assert slots[-1] == "19:30"

    # E a validação de agendamento concorda com a lista em todos os pontos:
    for horario in slots:
        inicio = hhmm_para_min(horario)
        assert primeiro_conflito(inicio, 30, ocupadas) is None
        assert cabe_no_expediente(inicio, 30, "08:20", "20:00")
