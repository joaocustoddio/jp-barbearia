"""
rotas_admin_gestao.py
---------------------
Painel — cadastro e configuração: barbeiros (status, comissão, login),
produtos, preço/duração dos serviços e troca de senhas.
"""

import bcrypt
from flask import request, jsonify, g

from auth import somente_master, token_requerido
from config import data_hoje
from database import get_connection
from extensoes import app


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
