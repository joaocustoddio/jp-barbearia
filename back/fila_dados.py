"""
fila_dados.py
-------------
Leitura e escrita da fila online no banco. As regras (estimativa, posição,
transições) ficam em fila.py, sem banco; aqui só entra e sai dado.
"""

import secrets
from dataclasses import dataclass, field

import agendamentos  # módulo (não o nome): agendamentos também importa este arquivo
from config import INTERVALO_MINUTOS
from fila import BarbeiroDia, Lugar, proximo_status
from horarios import hhmm_para_min
from validacoes import horario_efetivo_de


@dataclass
class DiaFila:
    agora_min: int
    barbeiros: list          # BarbeiroDia de quem trabalha hoje
    lugares: list            # Lugar ativos (aguardando/chamado)
    indisponiveis: set       # barbeiros com gente esperando mas fora do dia
    nomes: dict              # barbeiro_id -> nome (todos os ativos)
    fotos: dict = field(default_factory=dict)
    abertos_agora: set = field(default_factory=set)


# -------------------------------------------------------
# PERÍODOS DE FILA
# -------------------------------------------------------

def dia_de_fila(conn, data):
    """True se a data cai dentro de algum período de fila."""
    linha = conn.execute(
        "SELECT 1 FROM periodos_fila WHERE %s BETWEEN inicio AND fim LIMIT 1", (data,)
    ).fetchone()
    return bool(linha)


def dias_de_fila(conn, inicio, fim):
    """As datas entre inicio e fim (inclusivas) que caem em algum período."""
    from datetime import date, timedelta
    periodos = conn.execute(
        "SELECT inicio, fim FROM periodos_fila WHERE inicio <= %s AND fim >= %s",
        (fim, inicio)
    ).fetchall()
    dias = set()
    for p in periodos:
        dia = max(date.fromisoformat(p["inicio"]), date.fromisoformat(inicio))
        ultimo = min(date.fromisoformat(p["fim"]), date.fromisoformat(fim))
        while dia <= ultimo:
            dias.add(dia.isoformat())
            dia += timedelta(days=1)
    return dias


def listar_periodos(conn):
    linhas = conn.execute(
        "SELECT id, inicio, fim FROM periodos_fila ORDER BY inicio"
    ).fetchall()
    return [{"id": l["id"], "inicio": l["inicio"], "fim": l["fim"]} for l in linhas]


def criar_periodo(conn, inicio, fim, admin_id):
    linha = conn.execute(
        "INSERT INTO periodos_fila (inicio, fim, criado_por) VALUES (%s, %s, %s) RETURNING id",
        (inicio, fim, admin_id)
    ).fetchone()
    conn.commit()
    return linha["id"]


def apagar_periodo(conn, periodo_id):
    linha = conn.execute(
        "DELETE FROM periodos_fila WHERE id = %s RETURNING id", (periodo_id,)
    ).fetchone()
    conn.commit()
    return bool(linha)


# -------------------------------------------------------
# O DIA DA FILA
# -------------------------------------------------------

def carregar_dia(conn, data, agora_min):
    """
    Tudo que a estimativa precisa pra `data`: quem trabalha (com as janelas
    ocupadas e quando fica livre), quem está na fila e quem está aberto agora.
    """
    barbeiros = conn.execute(
        "SELECT id, nome, foto, almoco_fixo FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    expedientes = {l["barbeiro_id"]: l for l in conn.execute(
        "SELECT barbeiro_id, inicio, fim FROM expedientes WHERE data = %s", (data,)
    ).fetchall()}
    fila_rows = conn.execute(
        """SELECT id, barbeiro_id, atendido_por, status, duracao_min, agendamento_id
           FROM fila WHERE data = %s
             AND status IN ('aguardando', 'chamado', 'atendendo', 'concluido')""",
        (data,)
    ).fetchall()
    ligados = {l["agendamento_id"] for l in fila_rows if l["agendamento_id"]}
    agendados = conn.execute(
        """SELECT agendamentos.id, agendamentos.barbeiro_id, agendamentos.hora,
                  COALESCE(agendamentos.duracao_min, servicos.duracao_min) AS duracao_min
           FROM agendamentos JOIN servicos ON agendamentos.servico_id = servicos.id
           WHERE agendamentos.data = %s AND agendamentos.status != 'cancelado'""",
        (data,)
    ).fetchall()
    bloqueios = conn.execute(
        "SELECT barbeiro_id, hora, duracao_min, motivo FROM bloqueios WHERE data = %s", (data,)
    ).fetchall()

    # Atendimento em curso que veio da fila: o barbeiro só fica livre quando termina.
    fim_em_curso = {}
    por_agendamento = {a["id"]: a for a in agendados}
    for l in fila_rows:
        ag = por_agendamento.get(l["agendamento_id"])
        if l["status"] == "atendendo" and ag:
            fim = hhmm_para_min(ag["hora"]) + (ag["duracao_min"] or l["duracao_min"])
            fim_em_curso[l["atendido_por"]] = max(fim_em_curso.get(l["atendido_por"], 0), fim)

    trabalhando, abertos = [], set()
    for b in barbeiros:
        abertura, fechamento = horario_efetivo_de(data, b["id"], expedientes.get(b["id"]))
        ab, fe = hhmm_para_min(abertura), hhmm_para_min(fechamento)
        if fe <= ab:
            continue  # folga
        ocupadas, dia_bloqueado = agendamentos.montar_janelas(
            [a for a in agendados if a["barbeiro_id"] == b["id"] and a["id"] not in ligados],
            [x for x in bloqueios if x["barbeiro_id"] in (None, b["id"])],
            b["almoco_fixo"], INTERVALO_MINUTOS,
        )
        if dia_bloqueado:
            continue
        trabalhando.append(BarbeiroDia(
            id=b["id"], abertura=ab, fechamento=fe,
            livre_a_partir=max(agora_min, fim_em_curso.get(b["id"], 0)),
            ocupadas=tuple(ocupadas),
        ))
        if ab <= agora_min < fe:
            abertos.add(b["id"])

    ids_trabalhando = {b.id for b in trabalhando}
    lugares = [Lugar(id=l["id"], barbeiro_id=l["barbeiro_id"], atendido_por=l["atendido_por"],
                     status=l["status"], duracao_min=l["duracao_min"])
               for l in fila_rows if l["status"] in ("aguardando", "chamado")]
    indisponiveis = set()
    for l in lugares:
        dono = l.atendido_por if l.status == "chamado" else l.barbeiro_id
        if dono is not None and dono not in ids_trabalhando:
            indisponiveis.add(dono)

    return DiaFila(
        agora_min=agora_min, barbeiros=trabalhando, lugares=lugares,
        indisponiveis=indisponiveis,
        nomes={b["id"]: b["nome"] for b in barbeiros},
        fotos={b["id"]: b["foto"] for b in barbeiros},
        abertos_agora=abertos,
    )


def telefone_na_fila(conn, data, telefone):
    """True se o telefone (só dígitos) já tem lugar ativo nesse dia."""
    return bool(conn.execute(
        """SELECT 1 FROM fila WHERE data = %s AND telefone = %s
             AND status IN ('aguardando', 'chamado') LIMIT 1""",
        (data, telefone)
    ).fetchone())


def eh_telefone_generico(conn, telefone):
    """Número que não identifica ninguém (ex.: o da própria barbearia)."""
    return bool(conn.execute(
        "SELECT 1 FROM telefones_genericos WHERE telefone = %s", (telefone,)
    ).fetchone())


def inserir_lugar(conn, *, data, nome, telefone, servico_id, duracao_min, barbeiro_id,
                  origem, cliente_id):
    """Grava o lugar (sem commit) e devolve (id, token)."""
    token = secrets.token_urlsafe(24)
    linha = conn.execute(
        """INSERT INTO fila (data, token, cliente_id, nome, telefone, servico_id,
                             duracao_min, barbeiro_id, origem)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (data, token, cliente_id, nome, telefone, servico_id, duracao_min, barbeiro_id, origem)
    ).fetchone()
    return linha["id"], token


_SELECT_LUGAR = """
    SELECT fila.id, fila.data, fila.status, fila.barbeiro_id, fila.atendido_por,
           fila.chamado_em, fila.duracao_min, fila.nome, fila.telefone, fila.servico_id,
           fila.entrou_em, fila.agendamento_id, fila.origem,
           servicos.nome AS servico, b1.nome AS barbeiro_nome, b2.nome AS atendido_por_nome
    FROM fila
    JOIN servicos ON servicos.id = fila.servico_id
    LEFT JOIN barbeiros b1 ON b1.id = fila.barbeiro_id
    LEFT JOIN barbeiros b2 ON b2.id = fila.atendido_por
"""


def buscar_por_token(conn, token):
    linha = conn.execute(_SELECT_LUGAR + " WHERE fila.token = %s", (token,)).fetchone()
    return dict(linha) if linha else None


def buscar_por_id(conn, lugar_id):
    linha = conn.execute(_SELECT_LUGAR + " WHERE fila.id = %s", (lugar_id,)).fetchone()
    return dict(linha) if linha else None


def lugares_do_dia(conn, data):
    """Todos os lugares do dia (inclusive finalizados), na ordem de entrada."""
    return [dict(l) for l in conn.execute(
        _SELECT_LUGAR + " WHERE fila.data = %s ORDER BY fila.id", (data,)
    ).fetchall()]


def mudar_status(conn, lugar_id, acao, barbeiro_id=None, agendamento_id=None):
    """
    Aplica `acao` ao lugar com a linha travada e faz o commit. Levanta
    fila.TransicaoInvalida se não couber; devolve None se o lugar não existe.
    """
    atual = conn.execute(
        "SELECT id, status, atendido_por FROM fila WHERE id = %s FOR UPDATE", (lugar_id,)
    ).fetchone()
    if not atual:
        return None
    novo = proximo_status(atual["status"], acao)
    if acao == "chamar":
        conn.execute("UPDATE fila SET status = %s, chamado_em = now(), atendido_por = %s WHERE id = %s",
                     (novo, barbeiro_id, lugar_id))
    elif acao == "comecar":
        conn.execute("""UPDATE fila SET status = %s, agendamento_id = %s,
                               atendido_por = COALESCE(atendido_por, %s) WHERE id = %s""",
                     (novo, agendamento_id, barbeiro_id, lugar_id))
    else:
        conn.execute("UPDATE fila SET status = %s, finalizado_em = now() WHERE id = %s",
                     (novo, lugar_id))
    conn.commit()
    return buscar_por_id(conn, lugar_id)
