"""Valores monetários exatos e catálogo com vigência por data do atendimento."""
from decimal import Decimal, InvalidOperation


CATALOGO_SQL = '''SELECT s.id, s.nome, s.ativo, s.imagem,
        COALESCE((SELECT v.valor_centavos FROM servico_vigencias v
                  WHERE v.servico_id = s.id AND v.inicio <= %s
                  ORDER BY v.inicio DESC LIMIT 1), CAST(ROUND(CAST(s.preco AS NUMERIC) * 100) AS INTEGER)) AS valor_centavos,
        COALESCE((SELECT v.duracao_min FROM servico_vigencias v
                  WHERE v.servico_id = s.id AND v.inicio <= %s
                  ORDER BY v.inicio DESC LIMIT 1), s.duracao_min) AS duracao_min
        FROM servicos s'''


def servico_na_data(conn, servico_id, data):
    """Usa a última vigência <= data, ou o catálogo base antes do 1º reajuste."""
    return conn.execute(CATALOGO_SQL + ' WHERE s.id = %s AND s.ativo = 1',
                        (data, data, servico_id)).fetchone()


def catalogo_na_data(conn, data):
    rows = conn.execute(CATALOGO_SQL + ' WHERE s.ativo = 1 ORDER BY s.id', (data, data)).fetchall()
    return [dict(r, preco=r['valor_centavos'] / 100) for r in rows]


def duracoes_no_periodo(conn, servico_id, datas):
    rows = conn.execute('''SELECT s.duracao_min, v.inicio, v.duracao_min AS duracao_vigente
        FROM servicos s LEFT JOIN servico_vigencias v ON v.servico_id = s.id AND v.inicio <= %s
        WHERE s.id = %s AND s.ativo = 1 ORDER BY v.inicio''', (datas[-1],servico_id)).fetchall()
    if not rows:
        return None
    resultado = {}
    for data in datas:
        duracao = rows[0]['duracao_min']
        for row in rows:
            if row['inicio'] and row['inicio'] <= data:
                duracao = row['duracao_vigente']
        resultado[data] = duracao
    return resultado


def em_centavos(valor):
    try:
        numero = Decimal(str(valor).replace(',', '.'))
        if not numero.is_finite() or numero < 0 or numero > Decimal('999999.99'):
            raise ValueError
        centavos = numero * 100
        if centavos != centavos.to_integral_value():
            raise ValueError
        return int(centavos)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Informe um valor entre R$ 0,00 e R$ 999.999,99, com até duas casas decimais.')


def duracao_valida(valor):
    try:
        if isinstance(valor, bool) or Decimal(str(valor)) != int(valor):
            raise ValueError
        minutos = int(valor)
        if not 5 <= minutos <= 480:
            raise ValueError
        return minutos
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise ValueError('Duração deve ser um número inteiro entre 5 e 480 minutos.')
