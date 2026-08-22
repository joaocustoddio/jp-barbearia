"""
extensoes.py
------------
Cria o objeto Flask e liga o que é transversal: CORS, rate limit, logging,
Sentry, tratamento de erro global e o fechamento de conexões pendentes.

Todos os módulos de rota importam o `app` daqui — assim cada rota continua
registrada no MESMO objeto Flask, com o mesmo nome de endpoint de sempre.
"""

import logging
import os

from flask import Flask, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import CORS_ORIGIN, EM_PRODUCAO, FLASK_ENV, SECRET_KEY_PADRAO
from database import fechar_conexoes_pendentes

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", SECRET_KEY_PADRAO)

# Em produção, não deixa subir com a SECRET_KEY insegura padrão.
if EM_PRODUCAO and app.config["SECRET_KEY"] == SECRET_KEY_PADRAO:
    raise RuntimeError(
        "SECRET_KEY insegura em produção. Defina SECRET_KEY (string longa e "
        "aleatória) no .env antes de rodar com FLASK_ENV=production."
    )

# Em produção, CORS liberado pra qualquer origem é inseguro. Aqui a gente só
# AVISA (não derruba o boot, pra não quebrar deploy) — o ideal é setar o domínio
# real do front em CORS_ORIGIN. O aviso aparece nos logs até ser corrigido.
_avisar_cors = EM_PRODUCAO and CORS_ORIGIN == "*"

# CORS: em produção trava no domínio configurado (CORS_ORIGIN); em
# desenvolvimento libera geral, pra rodar o front local (localhost) sem
# esbarrar em CORS. Assim o valor de CORS_ORIGIN no .env local é irrelevante.
CORS(app, origins=[CORS_ORIGIN] if EM_PRODUCAO else "*")

# Rate limiting: protege o login de força bruta. Sem limite global (só onde a
# gente marcar com @limiter.limit). Storage em memória — suficiente pra 1 serviço;
# se um dia escalar pra vários processos/instâncias, apontar pra um Redis.
limiter = Limiter(key_func=get_remote_address, app=app)

# -------------------------------------------------------
# TRATAMENTO DE ERRO GLOBAL
# Em produção, erros internos não revelam detalhes do servidor
# -------------------------------------------------------
@app.errorhandler(404)
def nao_encontrado(e):
    return jsonify({"erro": "Rota não encontrada"}), 404

@app.errorhandler(405)
def metodo_nao_permitido(e):
    return jsonify({"erro": "Método não permitido"}), 405

@app.errorhandler(429)
def limite_excedido(e):
    return jsonify({"erro": "Muitas tentativas. Aguarde um minuto e tente de novo."}), 429

@app.errorhandler(500)
def erro_interno(e):
    # Em desenvolvimento mostra o erro; em produção esconde
    if EM_PRODUCAO:
        return jsonify({"erro": "Erro interno no servidor"}), 500
    return jsonify({"erro": str(e)}), 500


# -------------------------------------------------------
# OBSERVABILIDADE — logging + captura de erros + saúde
# -------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jpbarbearia")

if _avisar_cors:
    logger.warning("CORS_ORIGIN=* em produção — defina o domínio do front pra travar o acesso.")

# Sentry é OPCIONAL: só liga se SENTRY_DSN estiver no .env e o pacote instalado.
# (pra ativar: pip install "sentry-sdk[flask]" e definir SENTRY_DSN)
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_sentry_dsn, environment=FLASK_ENV)
        logger.info("Sentry ativado.")
    except Exception as exc:  # pacote ausente ou DSN inválido não derruba o app
        logger.warning("SENTRY_DSN definido mas o Sentry não iniciou: %s", exc)


@app.teardown_request
def _fechar_conexoes(_exc):
    # Rede de segurança: garante que nenhuma conexão de banco fique pendurada,
    # mesmo se o endpoint estourou exceção antes do conn.close().
    fechar_conexoes_pendentes()


@app.errorhandler(Exception)
def _erro_nao_tratado(e):
    # Loga exceções não previstas (com stack) antes de esconder o detalhe em prod.
    # Erros HTTP (404/405/403…) passam direto pros handlers específicos.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    logger.exception("Erro não tratado")
    if EM_PRODUCAO:
        return jsonify({"erro": "Erro interno no servidor"}), 500
    return jsonify({"erro": str(e)}), 500
