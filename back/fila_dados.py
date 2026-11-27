"""
fila_dados.py
-------------
Leitura e escrita da fila online no banco. As regras (estimativa, posição,
transições) ficam em fila.py, sem banco; aqui só entra e sai dado.
"""


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
