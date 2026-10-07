"""
rotas_admin_gestao.py
---------------------
Painel — cadastro e configuração: barbeiros (status, comissão, login),
produtos, preço/duração dos serviços e troca de senhas.
"""

import bcrypt
from flask import request, jsonify, g

from collections import Counter
from datetime import date

from agendamentos import so_digitos
from auth import (
    barbeiro_do_escopo, pode_ver_valores, somente_master, token_requerido,
)
from config import data_hoje
from database import get_connection
from extensoes import app


# -------------------------------------------------------
# CLIENTES BLOQUEADOS — quem não pode marcar sozinho pelo site.
# Só o master mexe: é decisão de dono, como preço e comissão.
# -------------------------------------------------------

@app.route("/api/admin/clientes-bloqueados", methods=["GET"])
@token_requerido
@somente_master
def listar_clientes_bloqueados():
    conn = get_connection()
    linhas = conn.execute(
        "SELECT id, telefone, nome, motivo, criado_em FROM clientes_bloqueados "
        "ORDER BY criado_em DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(l) for l in linhas])


@app.route("/api/admin/clientes-bloqueados", methods=["POST"])
@token_requerido
@somente_master
def bloquear_cliente():
    """Bloqueia um telefone. Corpo: { telefone, nome?, motivo? }."""
    dados = request.get_json(silent=True) or {}
    telefone = so_digitos(dados.get("telefone"))
    if not telefone:
        return jsonify({"erro": "Telefone é obrigatório"}), 400
    # Mesma régua do agendamento: DDD + número.
    if len(telefone) < 10 or len(telefone) > 11:
        return jsonify({"erro": "Telefone inválido (use DDD + número)"}), 400

    conn = get_connection()
    # ON CONFLICT: bloquear duas vezes o mesmo número não é erro — atualiza o
    # motivo e segue. Evita mensagem de falha por algo que a pessoa quis fazer.
    conn.execute(
        """INSERT INTO clientes_bloqueados (telefone, nome, motivo)
           VALUES (%s, %s, %s)
           ON CONFLICT (telefone) DO UPDATE SET nome = EXCLUDED.nome,
                                                motivo = EXCLUDED.motivo""",
        (telefone, (dados.get("nome") or "").strip() or None,
         (dados.get("motivo") or "").strip() or None)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Cliente bloqueado.", "telefone": telefone}), 201


@app.route("/api/admin/clientes-bloqueados/<int:bloqueio_id>", methods=["DELETE"])
@token_requerido
@somente_master
def desbloquear_cliente(bloqueio_id):
    conn = get_connection()
    achou = conn.execute(
        "SELECT id FROM clientes_bloqueados WHERE id = %s", (bloqueio_id,)
    ).fetchone()
    if not achou:
        conn.close()
        return jsonify({"erro": "Bloqueio não encontrado"}), 404
    conn.execute("DELETE FROM clientes_bloqueados WHERE id = %s", (bloqueio_id,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Cliente desbloqueado."})


def _produtos_ativos(conn):
    """Catálogo de adicionais vindo do banco (o painel é quem edita)."""
    linhas = conn.execute(
        "SELECT id, nome, preco_centavos FROM produtos WHERE ativo = 1 ORDER BY nome"
    ).fetchall()
    return [dict(l) for l in linhas]


@app.route("/api/admin/produtos", methods=["GET"])
@token_requerido
def listar_produtos():
    """Catálogo de adicionais (nome + preço) pro barbeiro escolher a quantidade."""
    conn = get_connection()
    produtos = _produtos_ativos(conn)
    conn.close()
    return jsonify(produtos)


@app.route("/api/admin/produtos/<int:produto_id>", methods=["PUT"])
@token_requerido
@somente_master
def atualizar_produto(produto_id):
    """Edita nome e preço de um adicional. Corpo: { nome, preco }."""
    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    try:
        centavos = int(round(float(str(dados.get("preco")).replace(",", ".")) * 100))
    except (TypeError, ValueError):
        return jsonify({"erro": "Preço precisa ser um número"}), 400
    if not nome:
        return jsonify({"erro": "Nome é obrigatório"}), 400
    if centavos < 0:
        return jsonify({"erro": "Preço não pode ser negativo"}), 400

    conn = get_connection()
    if not conn.execute("SELECT id FROM produtos WHERE id = %s", (produto_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Produto não encontrado"}), 404
    conn.execute("UPDATE produtos SET nome = %s, preco_centavos = %s WHERE id = %s",
                 (nome[:80], centavos, produto_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Produto atualizado.", "preco_centavos": centavos})


@app.route("/api/admin/barbeiros", methods=["GET"])
@token_requerido
@somente_master
def painel_barbeiros():
    """
    Lista TODOS os barbeiros (ativos e inativos), com agendamentos futuros,
    comissão e o login de cada um (se já tiver). Só o master acessa.
    """
    hoje = data_hoje().isoformat()
    conn = get_connection()
    barbeiros = conn.execute(
        """
        SELECT
            barbeiros.id,
            barbeiros.nome,
            barbeiros.ativo,
            barbeiros.comissao_pct,
            COUNT(agendamentos.id) AS agendamentos_futuros,
            (SELECT usuario FROM admin
             WHERE admin.barbeiro_id = barbeiros.id AND admin.papel = 'barbeiro'
             LIMIT 1) AS login_usuario
        FROM barbeiros
        LEFT JOIN agendamentos
            ON agendamentos.barbeiro_id = barbeiros.id
            AND agendamentos.status = 'confirmado'
            AND agendamentos.data >= %s
        GROUP BY barbeiros.id
        ORDER BY barbeiros.id
        """,
        (hoje,)
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in barbeiros])


@app.route("/api/admin/servicos/<int:servico_id>", methods=["PUT"])
@token_requerido
@somente_master
def atualizar_servico(servico_id):
    """
    Edita preço e duração de um serviço. É o dono quem manda no cardápio —
    antes isso exigia mexer no código e fazer deploy.
    Corpo: { preco, duracao_min }.
    """
    dados = request.get_json(silent=True) or {}
    try:
        preco = round(float(str(dados.get("preco")).replace(",", ".")), 2)
        duracao = int(dados.get("duracao_min"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Preço e duração precisam ser números"}), 400
    if preco < 0:
        return jsonify({"erro": "Preço não pode ser negativo"}), 400
    if not (5 <= duracao <= 480):
        return jsonify({"erro": "Duração deve ficar entre 5 e 480 minutos"}), 400

    conn = get_connection()
    if not conn.execute("SELECT id FROM servicos WHERE id = %s", (servico_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Serviço não encontrado"}), 404
    conn.execute("UPDATE servicos SET preco = %s, duracao_min = %s WHERE id = %s",
                 (preco, duracao, servico_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Serviço atualizado.", "preco": preco, "duracao_min": duracao})


@app.route("/api/admin/barbeiros/<int:barbeiro_id>/comissao", methods=["PUT"])
@token_requerido
@somente_master
def atualizar_comissao(barbeiro_id):
    """
    Define a comissão do barbeiro (% que fica com ELE; o resto é da barbearia).
    Corpo: { comissao_pct }.
    """
    dados = request.get_json(silent=True) or {}
    try:
        pct = int(dados.get("comissao_pct"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Comissão precisa ser um número"}), 400
    if not (0 <= pct <= 100):
        return jsonify({"erro": "Comissão deve ficar entre 0 e 100"}), 400

    conn = get_connection()
    if not conn.execute("SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404
    conn.execute("UPDATE barbeiros SET comissao_pct = %s WHERE id = %s", (pct, barbeiro_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Comissão atualizada.", "comissao_pct": pct})


@app.route("/api/admin/barbeiros/<int:barbeiro_id>/login", methods=["PUT"])
@token_requerido
@somente_master
def definir_login_barbeiro(barbeiro_id):
    """
    Master cria/atualiza o login (usuário + senha) de um barbeiro.
    Corpo: { "usuario": "...", "senha": "..." } (senha opcional na atualização).
    """
    dados = request.get_json(silent=True) or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""

    if not usuario:
        return jsonify({"erro": "Usuário é obrigatório"}), 400

    conn = get_connection()
    # barbeiro existe?
    if not conn.execute("SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone():
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404

    # usuário já usado por OUTRA conta?
    conflito = conn.execute(
        "SELECT id FROM admin WHERE usuario = %s AND barbeiro_id IS DISTINCT FROM %s",
        (usuario, barbeiro_id)
    ).fetchone()
    if conflito:
        conn.close()
        return jsonify({"erro": "Esse nome de usuário já está em uso"}), 409

    existente = conn.execute(
        "SELECT id FROM admin WHERE barbeiro_id = %s AND papel = 'barbeiro'", (barbeiro_id,)
    ).fetchone()

    if existente:
        # atualiza usuário (e senha, se veio)
        conn.execute("UPDATE admin SET usuario = %s WHERE id = %s", (usuario, existente["id"]))
        if senha:
            h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
            conn.execute("UPDATE admin SET senha_hash = %s WHERE id = %s", (h, existente["id"]))
        msg = "Login do barbeiro atualizado"
    else:
        if not senha:
            conn.close()
            return jsonify({"erro": "Senha é obrigatória ao criar o login"}), 400
        h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (usuario, senha_hash, papel, barbeiro_id) VALUES (%s, %s, 'barbeiro', %s)",
            (usuario, h, barbeiro_id)
        )
        msg = "Login do barbeiro criado"

    conn.commit()
    conn.close()
    return jsonify({"mensagem": msg, "usuario": usuario})


@app.route("/api/admin/acessos", methods=["GET"])
@token_requerido
@somente_master
def listar_acessos():
    """Lista todos os logins do painel (master, salão, barbeiros) — pro master
    ver e trocar senhas. Ordena: master, salão, depois barbeiros."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT admin.id, admin.usuario, admin.papel, barbeiros.nome AS barbeiro_nome
           FROM admin
           LEFT JOIN barbeiros ON admin.barbeiro_id = barbeiros.id
           ORDER BY CASE admin.papel WHEN 'master' THEN 0 WHEN 'salao' THEN 1 ELSE 2 END,
                    admin.usuario"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/senha", methods=["PUT"])
@token_requerido
@somente_master
def trocar_senha_login():
    """Master troca a senha de qualquer login. Corpo: { usuario, senha }."""
    dados = request.get_json(silent=True) or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""
    if not usuario or not senha:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
    if len(senha) < 4:
        return jsonify({"erro": "A senha deve ter pelo menos 4 caracteres"}), 400

    conn = get_connection()
    existe = conn.execute("SELECT id FROM admin WHERE usuario = %s", (usuario,)).fetchone()
    if not existe:
        conn.close()
        return jsonify({"erro": "Login não encontrado"}), 404
    h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE admin SET senha_hash = %s WHERE usuario = %s", (h, usuario))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Senha de '{usuario}' atualizada."})


@app.route("/api/admin/minha-senha", methods=["PUT"])
@token_requerido
def trocar_minha_senha():
    """Qualquer usuário logado troca a PRÓPRIA senha (exige a senha atual).
    Corpo: { senha_atual, nova_senha }."""
    dados = request.get_json(silent=True) or {}
    atual = dados.get("senha_atual") or ""
    nova = dados.get("nova_senha") or ""
    if not atual or not nova:
        return jsonify({"erro": "Informe a senha atual e a nova"}), 400
    if len(nova) < 4:
        return jsonify({"erro": "A nova senha deve ter pelo menos 4 caracteres"}), 400

    admin_id = g.usuario.get("admin_id")
    conn = get_connection()
    row = conn.execute("SELECT senha_hash FROM admin WHERE id = %s", (admin_id,)).fetchone()
    if not row or not bcrypt.checkpw(atual.encode(), row["senha_hash"].encode()):
        conn.close()
        return jsonify({"erro": "Senha atual incorreta"}), 400
    h = bcrypt.hashpw(nova.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE admin SET senha_hash = %s WHERE id = %s", (h, admin_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Senha alterada com sucesso."})


@app.route("/api/admin/barbeiros/<int:barbeiro_id>", methods=["PATCH"])
@token_requerido
@somente_master
def atualizar_status_barbeiro(barbeiro_id):
    """
    Inativa ou reativa um barbeiro (item: folga/imprevisto temporário).
    Corpo esperado: { "ativo": true }  ou  { "ativo": false }

    Inativar NÃO apaga o barbeiro nem seus agendamentos históricos —
    apenas o esconde da tela pública de agendamento (/api/barbeiros).
    """
    dados = request.get_json(silent=True)
    if not dados or "ativo" not in dados:
        return jsonify({"erro": "Campo 'ativo' é obrigatório (true ou false)"}), 400

    novo_ativo = 1 if dados["ativo"] else 0

    conn = get_connection()
    existe = conn.execute(
        "SELECT id FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    if not existe:
        conn.close()
        return jsonify({"erro": "Barbeiro não encontrado"}), 404

    conn.execute(
        "UPDATE barbeiros SET ativo = %s WHERE id = %s", (novo_ativo, barbeiro_id)
    )
    conn.commit()
    conn.close()

    estado = "reativado" if novo_ativo else "inativado"
    return jsonify({"mensagem": f"Barbeiro {estado} com sucesso", "ativo": bool(novo_ativo)})


# -------------------------------------------------------
# FICHA DO CLIENTE
#
# Só existe porque cada pessoa passou a ter UMA ficha. Antes, cada corte criava
# um cadastro novo e "histórico do cliente" era literalmente impossível de
# montar — eram 920 fichas para 385 pessoas.
# -------------------------------------------------------
@app.route("/api/admin/clientes/<int:cliente_id>", methods=["GET"])
@token_requerido
def ficha_cliente(cliente_id):
    """Histórico da pessoa: quantas vezes veio, o que costuma fazer, quando foi
    a última vez. Valores só pra quem pode ver dinheiro (salão não vê)."""
    conn = get_connection()
    cliente = conn.execute(
        "SELECT id, nome, telefone, email, criado_em FROM clientes WHERE id = %s",
        (cliente_id,)
    ).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"erro": "Cliente não encontrado"}), 404

    # Um barbeiro comum só abre a ficha de quem ele já atendeu — não é uma
    # lista da carteira de clientes dos colegas.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        atendeu = conn.execute(
            "SELECT 1 FROM agendamentos WHERE cliente_id = %s AND barbeiro_id = %s "
            "AND status != 'cancelado' LIMIT 1",
            (cliente_id, escopo)
        ).fetchone()
        if not atendeu:
            conn.close()
            return jsonify({"erro": "Cliente não encontrado"}), 404

    historico = conn.execute(
        """SELECT agendamentos.id, agendamentos.data, agendamentos.hora,
                  agendamentos.status, agendamentos.forma_pagamento,
                  servicos.nome AS servico, servicos.preco,
                  barbeiros.nome AS barbeiro
             FROM agendamentos
             JOIN servicos  ON agendamentos.servico_id  = servicos.id
             JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
            WHERE agendamentos.cliente_id = %s
            ORDER BY agendamentos.data DESC, agendamentos.hora DESC""",
        (cliente_id,)
    ).fetchall()
    conn.close()

    feitos = [h for h in historico if h["status"] != "cancelado"]
    cancelados = [h for h in historico if h["status"] == "cancelado"]
    ver_valores = pode_ver_valores()

    # Ciclo: média de dias entre uma visita e a próxima. Serve pra dizer se a
    # pessoa está atrasada SEGUNDO O HÁBITO DELA — quem corta a cada 15 dias e
    # sumiu há 30 é um caso; quem corta a cada 45, não.
    datas = sorted({h["data"] for h in feitos})
    ciclo = None
    if len(datas) >= 2:
        d0 = date.fromisoformat(datas[0])
        d1 = date.fromisoformat(datas[-1])
        ciclo = round((d1 - d0).days / (len(datas) - 1))

    def preferido(campo):
        if not feitos:
            return None
        return Counter(h[campo] for h in feitos).most_common(1)[0][0]

    ficha = {
        "id": cliente["id"],
        "nome": cliente["nome"],
        "telefone": cliente["telefone"],
        "email": cliente["email"],
        "total_atendimentos": len(feitos),
        "total_cancelados": len(cancelados),
        "primeira_visita": datas[0] if datas else None,
        "ultima_visita": datas[-1] if datas else None,
        "dias_desde_ultima": (data_hoje() - date.fromisoformat(datas[-1])).days if datas else None,
        "ciclo_dias": ciclo,
        "servico_preferido": preferido("servico"),
        "barbeiro_preferido": preferido("barbeiro"),
        "historico": [
            {k: h[k] for k in ("id", "data", "hora", "status", "servico", "barbeiro")}
            for h in historico
        ],
    }
    if ver_valores:
        ficha["total_gasto"] = round(sum(float(h["preco"] or 0) for h in feitos), 2)
        for item, linha in zip(ficha["historico"], historico):
            item["preco"] = round(float(linha["preco"] or 0), 2)
            item["forma_pagamento"] = linha["forma_pagamento"]
    return jsonify(ficha)


@app.route("/api/admin/clientes/retorno", methods=["GET"])
@token_requerido
def clientes_para_chamar():
    """
    Quem já é cliente de casa e está atrasado para voltar.

    "Atrasado" é pelo hábito de cada um, não por um prazo fixo: compara os dias
    desde a última visita com o intervalo médio daquela pessoa. Quem corta a
    cada 15 dias entra na lista antes de quem corta a cada 45.

    Só entra quem já voltou pelo menos duas vezes — uma visita só não diz se a
    pessoa é cliente ou passou ali uma vez. Telefone genérico fica de fora: lá
    moram várias pessoas e o "hábito" não é de ninguém.
    """
    escopo = barbeiro_do_escopo()
    filtro = " AND agendamentos.barbeiro_id = %s" if escopo is not None else ""
    params = [escopo] if escopo is not None else []

    conn = get_connection()
    linhas = conn.execute(rf"""
        SELECT clientes.id, clientes.nome, clientes.telefone,
               COUNT(DISTINCT agendamentos.data) AS visitas,
               MIN(agendamentos.data) AS primeira,
               MAX(agendamentos.data) AS ultima
          FROM agendamentos
          JOIN clientes ON agendamentos.cliente_id = clientes.id
         WHERE agendamentos.status != 'cancelado'
           AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g') <> ''
           AND regexp_replace(COALESCE(clientes.telefone, ''), '\D', '', 'g')
               NOT IN (SELECT telefone FROM telefones_genericos){filtro}
         GROUP BY clientes.id
        HAVING COUNT(DISTINCT agendamentos.data) >= 2
    """, params).fetchall()
    conn.close()

    hoje = data_hoje()
    atrasados = []
    for linha in linhas:
        primeira = date.fromisoformat(linha["primeira"])
        ultima = date.fromisoformat(linha["ultima"])
        ciclo = max(1, round((ultima - primeira).days / (linha["visitas"] - 1)))
        parado = (hoje - ultima).days
        # 1,5x o próprio ciclo, com piso de 21 dias: sem o piso, quem corta
        # toda semana apareceria na lista por ter faltado 11 dias.
        if parado >= max(21, round(ciclo * 1.5)):
            atrasados.append({
                "id": linha["id"],
                "nome": linha["nome"],
                "telefone": linha["telefone"],
                "visitas": linha["visitas"],
                "ciclo_dias": ciclo,
                "ultima_visita": linha["ultima"],
                "dias_parado": parado,
                "atraso": parado - ciclo,
            })

    atrasados.sort(key=lambda c: c["atraso"], reverse=True)
    return jsonify(atrasados)
