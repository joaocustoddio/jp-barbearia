"""
auth.py
-------
Login do painel e as regras de quem enxerga o quê (master, barbeiro, salão),
incluindo os decorators usados por todas as rotas protegidas.
"""

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
import bcrypt
from flask import request, jsonify, g

from database import get_connection
from extensoes import app, limiter


# -------------------------------------------------------
# AUTENTICAÇÃO — TOKEN JWT
#
# O que é JWT? É um "crachá digital" que o servidor gera
# quando o admin faz login. Nas próximas requisições, o
# frontend envia esse crachá no cabeçalho. O servidor
# verifica se é válido sem precisar consultar o banco
# a cada chamada.
#
# Fluxo:
# 1. Admin envia usuário + senha → POST /api/admin/login
# 2. Servidor confere no banco → gera token com validade
# 3. Frontend guarda o token (localStorage)
# 4. Toda rota protegida exige o token no header:
#    Authorization: Bearer <token>
# -------------------------------------------------------

def token_requerido(f):
    """
    Decorator: envolve qualquer rota que precise de login.
    Se o token não vier, for inválido ou expirado, bloqueia.
    Uso: @token_requerido acima da função da rota.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")

        # O token vem no formato: "Bearer eyJ0eXAiOiJ..."
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"erro": "Token de autenticação não fornecido"}), 401

        try:
            # Decodifica e valida o token usando a SECRET_KEY
            dados_token = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "Sessão expirada. Faça login novamente."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido"}), 401

        # Guarda o usuário logado (papel + barbeiro) pra rota usar no escopo.
        g.usuario = dados_token
        return f(*args, **kwargs)
    return decorated


def papel_atual():
    """Papel do usuário logado: 'master', 'barbeiro' ou 'salao'."""
    return getattr(g, "usuario", {}).get("papel")


def eh_master():
    """True se o usuário logado é o dono (papel master = vê tudo)."""
    return papel_atual() == "master"


def barbeiro_do_escopo():
    """
    Retorna None se o usuário enxerga TODOS os barbeiros (master ou salão),
    ou o barbeiro_id do usuário se ele for um barbeiro (enxerga só o dele).
    As rotas usam isso pra filtrar QUAIS agendamentos aparecem.
    """
    if papel_atual() in ("master", "salao"):
        return None
    return getattr(g, "usuario", {}).get("barbeiro_id")


def pode_ver_valores():
    """
    Quem pode ver DINHEIRO (faturamento, comissão, repasse):
    - master (tudo) e barbeiro (só o dele) → sim
    - salão (tablet compartilhado) → NÃO (só quantidade de cortes)
    """
    return papel_atual() != "salao"


def somente_master(f):
    """
    Decorator pra rotas que só o master pode acessar (gerenciar barbeiros,
    logins, bloqueios). Use SEMPRE abaixo de @token_requerido.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not eh_master():
            return jsonify({"erro": "Acesso restrito ao administrador."}), 403
        return f(*args, **kwargs)
    return decorated


# -------------------------------------------------------
# ROTAS DO PAINEL (protegidas)
# -------------------------------------------------------

@app.route("/api/admin/login", methods=["POST"])
@limiter.limit("10 per minute")
def admin_login():
    """
    Recebe { usuario, senha } e devolve um token JWT se válido.
    A senha é verificada com bcrypt (hash salgado, resistente a força bruta).
    Validade do token configurável via JWT_EXPIRACAO_HORAS no .env (padrão 8h).
    """
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Dados inválidos"}), 400

    usuario = dados.get("usuario", "").strip()
    senha   = dados.get("senha", "")

    if not usuario or not senha:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

    conn = get_connection()
    admin = conn.execute(
        """SELECT admin.id, admin.senha_hash, admin.papel, admin.barbeiro_id,
                  barbeiros.nome AS barbeiro_nome
           FROM admin
           LEFT JOIN barbeiros ON admin.barbeiro_id = barbeiros.id
           WHERE admin.usuario = %s""",
        (usuario,)
    ).fetchone()
    conn.close()

    # Verifica a senha com bcrypt. A mensagem de erro é a mesma pra usuário
    # inexistente e senha errada (não revela qual dos dois falhou).
    senha_confere = (
        admin is not None
        and bcrypt.checkpw(senha.encode(), admin["senha_hash"].encode())
    )
    if not senha_confere:
        return jsonify({"erro": "Usuário ou senha incorretos"}), 401

    horas = int(os.getenv("JWT_EXPIRACAO_HORAS", "8"))
    token = jwt.encode(
        {
            "admin_id": admin["id"],
            "papel": admin["papel"],
            "barbeiro_id": admin["barbeiro_id"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=horas)
        },
        app.config["SECRET_KEY"],
        algorithm="HS256"
    )

    # Devolve papel/barbeiro pro front adaptar a interface (master vs barbeiro).
    return jsonify({
        "token": token,
        "mensagem": "Login realizado com sucesso!",
        "papel": admin["papel"],
        "barbeiro_id": admin["barbeiro_id"],
        "barbeiro_nome": admin["barbeiro_nome"]
    })
