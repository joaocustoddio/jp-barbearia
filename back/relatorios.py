"""
relatorios.py
-------------
Números: o relatório público (total geral), o relatório do painel por período
e a contagem do dia (fechamento por barbeiro, com comissão e produtos).
"""

from datetime import datetime, timedelta

from flask import request, jsonify

from auth import barbeiro_do_escopo, pode_ver_valores, token_requerido
from config import data_hoje
from database import get_connection
from extensoes import app


@app.route("/api/relatorio", methods=["GET"])
def relatorio():
    conn = get_connection()

    total = conn.execute(
        "SELECT COUNT(*) as total FROM agendamentos WHERE status != 'cancelado'"
    ).fetchone()["total"]

    faturamento = conn.execute("""
        SELECT COALESCE(SUM(servicos.preco), 0) as total
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
    """).fetchone()["total"]

    mais_pedido = conn.execute("""
        SELECT servicos.nome, COUNT(*) as quantidade
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
        GROUP BY servicos.id
        ORDER BY quantidade DESC
        LIMIT 1
    """).fetchone()

    conn.close()

    return jsonify({
        "total_agendamentos": total,
        "faturamento_estimado": faturamento,
        "servico_mais_pedido": dict(mais_pedido) if mais_pedido else None
    })


@app.route("/api/admin/relatorio", methods=["GET"])
@token_requerido
def relatorio_admin():
    """
    Relatório financeiro com filtro por período.
    Parâmetro: ?periodo=dia | semana | mes
    Padrão: dia (hoje)
    """
    # Salão (tablet compartilhado) não vê dinheiro.
    if not pode_ver_valores():
        return jsonify({"erro": "Sem acesso a valores financeiros."}), 403

    periodo = request.args.get("periodo", "dia")

    hoje = data_hoje()
    if periodo == "semana":
        # Segunda-feira da semana atual até hoje
        inicio = hoje - timedelta(days=hoje.weekday())
        fim = hoje
    elif periodo == "mes":
        inicio = hoje.replace(day=1)
        fim = hoje
    else:  # dia
        inicio = hoje
        fim = hoje

    conn = get_connection()

    # Escopo: barbeiro vê só os números dele; master vê de todos.
    # (filtro_barb é um fragmento fixo — o valor vai por parâmetro, sem injeção)
    escopo = barbeiro_do_escopo()
    filtro_barb = " AND agendamentos.barbeiro_id = %s" if escopo is not None else ""
    per = [inicio.isoformat(), fim.isoformat()] + ([escopo] if escopo is not None else [])

    # Faturamento do período
    faturamento = conn.execute(f"""
        SELECT COALESCE(SUM(servicos.preco), 0) as total
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
    """, per).fetchone()["total"]

    # Total de agendamentos no período
    total = conn.execute(f"""
        SELECT COUNT(*) as total FROM agendamentos
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
    """, per).fetchone()["total"]

    # Agendamentos por dia (pra montar gráfico no futuro)
    por_dia = conn.execute(f"""
        SELECT agendamentos.data, COUNT(*) as quantidade,
               SUM(servicos.preco) as faturamento
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
        GROUP BY agendamentos.data
        ORDER BY agendamentos.data
    """, per).fetchall()

    # Produtos vendidos no período (adicionais). Entram no faturamento total —
    # é dinheiro que entrou no caixa — mas ficam também discriminados à parte,
    # porque não são serviço e não contam pra comissão do barbeiro.
    produtos = conn.execute(f"""
        SELECT COALESCE(SUM(consumos.valor_centavos * consumos.quantidade), 0) AS centavos,
               COALESCE(SUM(consumos.quantidade), 0) AS quantidade
        FROM agendamentos
        JOIN consumos ON consumos.agendamento_id = agendamentos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
    """, per).fetchone()
    produtos_valor = round(float(produtos["centavos"] or 0) / 100, 2)

    # Ranking dos serviços mais realizados no período (pro dashboard)
    servicos_mais_realizados = conn.execute(f"""
        SELECT servicos.nome,
               COUNT(*) as quantidade,
               SUM(servicos.preco) as faturamento
        FROM agendamentos
        JOIN servicos ON agendamentos.servico_id = servicos.id
        WHERE agendamentos.status != 'cancelado'
          AND agendamentos.data BETWEEN %s AND %s{filtro_barb}
        GROUP BY servicos.id
        ORDER BY quantidade DESC, faturamento DESC
    """, per).fetchall()

    conn.close()

    return jsonify({
        "periodo": periodo,
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "total_agendamentos": total,
        # faturamento_total = tudo que entrou (serviços + produtos). Os dois
        # pedaços vêm separados abaixo pra dar pra ver de onde veio.
        "faturamento_total": round(float(faturamento or 0) + produtos_valor, 2),
        "faturamento_servicos": round(float(faturamento or 0), 2),
        "faturamento_produtos": produtos_valor,
        "produtos_qtd": int(produtos["quantidade"] or 0),
        "por_dia": [dict(d) for d in por_dia],
        "servicos_mais_realizados": [dict(s) for s in servicos_mais_realizados]
    })


@app.route("/api/admin/contagem", methods=["GET"])
@token_requerido
def contagem_dia():
    """
    Fechamento do dia por barbeiro: nº de clientes, valor total e o repasse
    (comissão do barbeiro conforme comissao_pct; o resto fica com a barbearia).
    Master vê todos os barbeiros; barbeiro vê só o dele.
    Parâmetro: ?data=YYYY-MM-DD (padrão: hoje). Aceita datas passadas (fechamento).
    """
    data_str = request.args.get("data") or data_hoje().isoformat()
    try:
        datetime.strptime(data_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"erro": "Formato de data inválido (use YYYY-MM-DD)"}), 400

    # Escopo: barbeiro vê só a linha dele; master vê todos.
    escopo = barbeiro_do_escopo()
    filtro = " WHERE barbeiros.id = %s" if escopo is not None else ""
    params = [data_str] + ([escopo] if escopo is not None else [])

    conn = get_connection()
    linhas = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id, barbeiros.nome AS barbeiro_nome,
               barbeiros.comissao_pct,
               COUNT(agendamentos.id) AS clientes,
               COALESCE(SUM(servicos.preco), 0) AS total
        FROM barbeiros
        LEFT JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        LEFT JOIN servicos ON agendamentos.servico_id = servicos.id
        {filtro}
        GROUP BY barbeiros.id
        ORDER BY barbeiros.id
    """, params).fetchall()

    # Detalhe: quantos de cada serviço + o valor somado, por barbeiro
    # (ex: 3 Degradê = R$120, 1 Corte Social = R$35).
    detalhe = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id, servicos.nome AS servico_nome,
               COUNT(agendamentos.id) AS quantidade,
               COALESCE(SUM(servicos.preco), 0) AS valor
        FROM barbeiros
        JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        JOIN servicos ON agendamentos.servico_id = servicos.id
        {filtro}
        GROUP BY barbeiros.id, servicos.nome
        ORDER BY quantidade DESC, servicos.nome
    """, params).fetchall()

    # Produtos (consumos) do dia por barbeiro — linha à parte, NÃO comissiona.
    produtos = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id,
               COALESCE(SUM(consumos.quantidade), 0) AS produtos_qtd,
               COALESCE(SUM(consumos.valor_centavos * consumos.quantidade), 0) AS produtos_centavos
        FROM barbeiros
        JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        JOIN consumos ON consumos.agendamento_id = agendamentos.id
        {filtro}
        GROUP BY barbeiros.id
    """, params).fetchall()
    conn.close()

    por_barbeiro = {}
    for d in detalhe:
        por_barbeiro.setdefault(d["barbeiro_id"], []).append(
            {"nome": d["servico_nome"], "quantidade": d["quantidade"],
             "valor": round(float(d["valor"] or 0), 2)}
        )
    produtos_por_barbeiro = {
        p["barbeiro_id"]: {"qtd": p["produtos_qtd"],
                           "valor": round(float(p["produtos_centavos"] or 0) / 100, 2)}
        for p in produtos
    }

    # Salão (tablet) NÃO vê valores — só a quantidade de cortes.
    ver_valores = pode_ver_valores()

    barbeiros = []
    tot_clientes = tot_valor = tot_barbeiro = tot_barbearia = tot_produtos = 0
    for r in linhas:
        total = float(r["total"] or 0)
        pct = r["comissao_pct"]
        recebe_barbeiro = round(total * pct / 100, 2)
        recebe_barbearia = round(total - recebe_barbeiro, 2)
        prod = produtos_por_barbeiro.get(r["barbeiro_id"], {"qtd": 0, "valor": 0})

        servicos_b = por_barbeiro.get(r["barbeiro_id"], [])
        if not ver_valores:   # salão vê só a quantidade, sem valor
            servicos_b = [{"nome": s["nome"], "quantidade": s["quantidade"]} for s in servicos_b]
        item = {
            "barbeiro_id": r["barbeiro_id"],
            "barbeiro_nome": r["barbeiro_nome"],
            "clientes": r["clientes"],
            "servicos": servicos_b
        }
        if ver_valores:
            item.update({
                "total": round(total, 2),
                "comissao_pct": pct,
                "barbeiro_recebe": recebe_barbeiro,
                "barbearia_recebe": recebe_barbearia,
                "produtos": prod["valor"],
                "produtos_qtd": prod["qtd"],
                # O que realmente entrou: serviços + produtos. É este número que
                # tem que bater com o caixa no fim do dia.
                "total_geral": round(total + prod["valor"], 2)
            })
        barbeiros.append(item)

        tot_clientes += r["clientes"]
        tot_valor += total
        tot_barbeiro += recebe_barbeiro
        tot_barbearia += recebe_barbearia
        tot_produtos += prod["valor"]

    totais = {"clientes": tot_clientes}
    if ver_valores:
        totais.update({
            "total": round(tot_valor, 2),
            "barbeiro_recebe": round(tot_barbeiro, 2),
            "barbearia_recebe": round(tot_barbearia, 2),
            "produtos": round(tot_produtos, 2),
            "total_geral": round(tot_valor + tot_produtos, 2)
        })

    return jsonify({
        "data": data_str,
        "ver_valores": ver_valores,
        "barbeiros": barbeiros,
        "totais": totais
    })
