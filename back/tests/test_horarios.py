"""
Testes do motor de horários (geração dinâmica de slots).

Cobrem a lógica pura de `_intervalos_livres` e `_slots_dinamicos` — o núcleo que
decide quais horários o cliente vê. Usam data futura ("2099-01-01") pra não cair
no filtro de antecedência (que só age quando a data é hoje).

Minutos: 09:00 = 540, 12:30 = 750, etc.
"""
import app


# ---------- _intervalos_livres ----------

def test_dia_todo_livre():
    assert app._intervalos_livres(540, 600, []) == [(540, 600)]

def test_buraco_no_meio():
    # ocupa 09:20–09:40 → livre antes e depois
    assert app._intervalos_livres(540, 700, [(560, 580)]) == [(540, 560), (580, 700)]

def test_ocupados_sobrepostos_sao_mesclados():
    assert app._intervalos_livres(540, 600, [(540, 570), (560, 590)]) == [(590, 600)]

def test_ocupado_fora_da_janela_e_ignorado():
    # bloqueio antes da abertura não corta nada
    assert app._intervalos_livres(540, 600, [(500, 520)]) == [(540, 600)]


# ---------- _slots_dinamicos ----------

def slots(ab, fe, ocup, dur):
    return app._slots_dinamicos(ab, fe, ocup, dur, "2099-01-01")

def test_dia_vazio_passo_da_duracao():
    s = slots("09:00", "20:00", [], 40)  # Degradê 40min
    assert s[0] == "09:00"
    assert s[1] == "09:40"
    # grid de 40min a partir das 09:00: ...18:20, 19:00 (19:00+40=19:40 cabe;
    # o próximo, 19:40, passaria das 20:00). Então o último é 19:00.
    assert s[-1] == "19:00"

def test_encaixa_logo_apos_corte_anterior():
    # Social 30 ocupa 09:00–09:30; cliente Degradê 40 → 1º slot é 09:30, não 09:40
    assert slots("09:00", "20:00", [(540, 570)], 40)[0] == "09:30"

def test_packing_com_duracao_diferente():
    # Degradê 40 ocupa 09:00–09:40; cliente Social 30 → 1º slot 09:40
    assert slots("09:00", "20:00", [(540, 580)], 30)[0] == "09:40"

def test_almoco_bloqueia_a_janela():
    # almoço 12:30–13:30; Degradê 40
    s = slots("09:00", "20:00", [(750, 810)], 40)
    assert "11:40" in s                  # último antes do almoço (11:40–12:20)
    assert "13:30" in s                  # primeiro depois do almoço
    assert "12:20" not in s              # 12:20–13:00 encostaria no almoço
    assert "13:00" not in s

def test_folga_sem_slots():
    # abertura == fechamento (folga do dono) → nenhum horário
    assert slots("09:00", "09:00", [], 40) == []

def test_servico_nao_cabe_ate_o_fechamento():
    # janela 19:30–20:00 (30min) e serviço de 40min → não cabe
    assert slots("19:30", "20:00", [], 40) == []
