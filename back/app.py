"""
app.py (versão produção)
------------------------
Mudanças em relação à versão anterior:
- debug e secret_key vêm do .env, nunca do código
- CORS restrito ao domínio configurado no .env
- Validações mais robustas em todas as rotas
- Tratamento de erro global (500 não vaza detalhes internos)
- Logs mais informativos
"""

import os
import re
from datetime import datetime, date, timedelta, timezone
from functools import wraps

import jwt
import bcrypt
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv
from database import get_connection, init_db, criar_admin_padrao, criar_salao_padrao, criar_barbeiros_padrao, ajustar_servicos

# Carrega as variáveis do arquivo .env
# Se não existir o .env, usa os valores padrão definidos abaixo
load_dotenv()

app = Flask(__name__)

# -------------------------------------------------------
# CONFIGURAÇÕES VIA VARIÁVEIS DE AMBIENTE
# -------------------------------------------------------
SECRET_KEY_PADRAO = "dev-inseguro-troque-em-producao"
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", SECRET_KEY_PADRAO)

FLASK_ENV      = os.getenv("FLASK_ENV", "development")
CORS_ORIGIN    = os.getenv("CORS_ORIGIN", "*")
EM_PRODUCAO    = FLASK_ENV == "production"

# Em produção, não deixa subir com a SECRET_KEY insegura padrão.
if EM_PRODUCAO and app.config["SECRET_KEY"] == SECRET_KEY_PADRAO:
    raise RuntimeError(
        "SECRET_KEY insegura em produção. Defina SECRET_KEY (string longa e "
        "aleatória) no .env antes de rodar com FLASK_ENV=production."
    )

HORARIO_ABERTURA      = os.getenv("HORARIO_ABERTURA", "09:00")
HORARIO_FECHAMENTO    = os.getenv("HORARIO_FECHAMENTO", "19:00")
INTERVALO_MINUTOS     = int(os.getenv("INTERVALO_MINUTOS", "30"))

# Antecedência mínima para agendamento (em minutos) = o "cooldown".
# Exemplo: 30 = o cliente precisa agendar com pelo menos 30min de antecedência.
# Mude o valor no .env (ANTECEDENCIA_MINIMA_MINUTOS) quando quiser ajustar —
# esse "30" aqui só é usado se a variável não existir no .env.
ANTECEDENCIA_MINIMA   = int(os.getenv("ANTECEDENCIA_MINIMA_MINUTOS", "30"))

# Janela máxima de agendamento pelo site (em dias). O cliente só pode marcar
# de hoje até hoje + esse limite. Padrão 7 (1 semana). Vale só pro fluxo público;
# o barbeiro pelo painel (walk-in / repetir) não fica preso a esse limite.
LIMITE_DIAS_AGENDAMENTO = int(os.getenv("LIMITE_DIAS_AGENDAMENTO", "7"))

# Dias da semana em que a barbearia NÃO atende.
# Numeração do Python (date.weekday()): 0=segunda, 1=terça, ..., 5=sábado, 6=domingo.
# Padrão: "6" (domingo). Configure no .env (DIAS_FECHADOS) como lista separada
# por vírgula — ex: "5,6" fecha sábado e domingo.
_dias_fechados_raw = os.getenv("DIAS_FECHADOS", "6")
DIAS_FECHADOS = {int(d.strip()) for d in _dias_fechados_raw.split(",") if d.strip() != ""}

# Nomes dos dias na ordem do weekday() do Python (pra montar mensagens de erro).
NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

# CORS: em produção trava no domínio configurado (CORS_ORIGIN); em
# desenvolvimento libera geral, pra rodar o front local (localhost) sem
# esbarrar em CORS. Assim o valor de CORS_ORIGIN no .env local é irrelevante.
CORS(app, origins=[CORS_ORIGIN] if EM_PRODUCAO else "*")

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

@app.errorhandler(500)
def erro_interno(e):
    # Em desenvolvimento mostra o erro; em produção esconde
    if EM_PRODUCAO:
        return jsonify({"erro": "Erro interno no servidor"}), 500
    return jsonify({"erro": str(e)}), 500


# -------------------------------------------------------
# FUNÇÕES DE VALIDAÇÃO
# Separadas das rotas para manter o código organizado.
# Cada uma retorna (True, None) se válido,
# ou (False, "mensagem de erro") se inválido.
# -------------------------------------------------------

def validar_nome(nome):
    if not nome or not isinstance(nome, str):
        return False, "Nome é obrigatório"
    nome = nome.strip()
    if len(nome) < 2:
        return False, "Nome muito curto"
    if len(nome) > 100:
        return False, "Nome muito longo (máximo 100 caracteres)"
    return True, None

def validar_telefone(telefone):
    # Telefone é opcional — só valida se vier preenchido
    if not telefone:
        return True, None
    telefone = re.sub(r"\D", "", telefone)  # remove tudo que não é número
    if len(telefone) < 10 or len(telefone) > 11:
        return False, "Telefone inválido (use DDD + número)"
    return True, None

def validar_data(data_str):
    if not data_str:
        return False, "Data é obrigatória"
    try:
        data = datetime.strptime(data_str, "%Y-%m-%d").date()
    except ValueError:
        return False, "Formato de data inválido (use YYYY-MM-DD)"
    if data < date.today():
        return False, "Não é possível agendar em datas passadas"
    return True, None

def dia_fechado(data_str):
    """
    Diz se a data cai num dia sem atendimento (ex: domingo).
    Retorna (True, "domingo") se for fechado, ou (False, None) se atende.
    Assume que data_str já passou por validar_data (formato válido).
    """
    try:
        data = datetime.strptime(data_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, None
    if data.weekday() in DIAS_FECHADOS:
        return True, NOMES_DIAS[data.weekday()]
    return False, None

def validar_hora(hora_str, data_str=None):
    if not hora_str:
        return False, "Hora é obrigatória"
    try:
        datetime.strptime(hora_str, "%H:%M")
    except ValueError:
        return False, "Formato de hora inválido (use HH:MM)"

    # Se for hoje, valida antecedência mínima
    if data_str and data_str == date.today().isoformat():
        agora = datetime.now()
        agora_min = agora.hour * 60 + agora.minute
        hora_min  = int(hora_str.split(":")[0]) * 60 + int(hora_str.split(":")[1])

        if hora_min <= agora_min + ANTECEDENCIA_MINIMA:
            horas = ANTECEDENCIA_MINIMA // 60
            minutos = ANTECEDENCIA_MINIMA % 60
            return False, f"Agendamentos devem ser feitos com pelo menos {horas}h{minutos:02d}min de antecedência"

    return True, None

def validar_servico_id(servico_id, conn):
    if not servico_id:
        return False, "Serviço é obrigatório"
    try:
        servico_id = int(servico_id)
    except (TypeError, ValueError):
        return False, "ID de serviço inválido"
    existe = conn.execute(
        "SELECT id FROM servicos WHERE id = %s", (servico_id,)
    ).fetchone()
    if not existe:
        return False, "Serviço não encontrado"
    return True, None


# -------------------------------------------------------
# ROTAS DA API
# -------------------------------------------------------

@app.route("/api/servicos", methods=["GET"])
def listar_servicos():
    conn = get_connection()
    servicos = conn.execute("SELECT * FROM servicos").fetchall()
    conn.close()
    return jsonify([dict(s) for s in servicos])


@app.route("/api/barbeiros", methods=["GET"])
def listar_barbeiros():
    """Retorna os barbeiros ativos. Usado pro cliente escolher no chat."""
    conn = get_connection()
    barbeiros = conn.execute(
        "SELECT id, nome, foto FROM barbeiros WHERE ativo = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in barbeiros])


@app.route("/api/horarios-disponiveis", methods=["GET"])
def horarios_disponiveis():
    """
    Agora filtra por barbeiro_id também.
    Cada barbeiro tem sua agenda independente — um horário
    ocupado com o Barbeiro 1 continua livre pro Barbeiro 2.

    Parâmetros: ?data=YYYY-MM-DD&barbeiro_id=1
    """
    data_str     = request.args.get("data")
    barbeiro_id  = request.args.get("barbeiro_id")

    valido, erro = validar_data(data_str)
    if not valido:
        return jsonify({"erro": erro}), 400

    if not barbeiro_id:
        return jsonify({"erro": "barbeiro_id é obrigatório"}), 400

    # Dia sem atendimento (ex: domingo): devolve lista vazia com status 200.
    # Importante NÃO retornar erro aqui — o front, ao receber erro, cai no mock
    # e mostraria horários falsos. Vazio faz o fluxo pular pro próximo dia.
    fechado, _ = dia_fechado(data_str)
    if fechado:
        return jsonify({"data": data_str, "barbeiro_id": barbeiro_id,
                        "horarios_disponiveis": [], "fechado": True})

    todos = _gerar_horarios_do_dia(data_str)

    conn = get_connection()

    # Horários já ocupados DESTE barbeiro nesta data
    ocupados = conn.execute(
        """SELECT hora FROM agendamentos
           WHERE data = %s AND barbeiro_id = %s AND status != 'cancelado'""",
        (data_str, barbeiro_id)
    ).fetchall()

    horarios_ocupados = {row["hora"] for row in ocupados}

    # Bloqueios: os globais (barbeiro_id NULL) valem pra todos; os com
    # barbeiro_id valem só pra esse barbeiro (ex: almoço). Cada bloqueio tem
    # uma janela (duracao_min); sem duração, vale por 1 slot.
    bloqueios = conn.execute(
        """SELECT hora, duracao_min FROM bloqueios
           WHERE data = %s AND (barbeiro_id IS NULL OR barbeiro_id = %s)""",
        (data_str, barbeiro_id)
    ).fetchall()
    # Almoço fixo do barbeiro (se tiver) — repete todo dia, bloqueia 60min.
    row_barb = conn.execute(
        "SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    almoco_fixo = row_barb["almoco_fixo"] if row_barb else None
    conn.close()

    def _min(hhmm):
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])

    def _bloquear(ini, dur):
        fim_b = ini + dur
        # Marca ocupado todo slot cuja janela [S, S+intervalo) encoste no bloqueio.
        for h in todos:
            hm = _min(h)
            if hm < fim_b and hm + INTERVALO_MINUTOS > ini:
                horarios_ocupados.add(h)

    for b in bloqueios:
        if b["hora"] is None:
            return jsonify({"data": data_str, "horarios_disponiveis": [], "bloqueio_dia": True})
        _bloquear(_min(b["hora"]), b["duracao_min"] or INTERVALO_MINUTOS)

    if almoco_fixo:
        _bloquear(_min(almoco_fixo), 60)

    disponiveis = [h for h in todos if h not in horarios_ocupados]

    return jsonify({"data": data_str, "barbeiro_id": barbeiro_id, "horarios_disponiveis": disponiveis})


def _gerar_horarios_do_dia(data_str=None):
    """
    Gera todos os slots do dia dentro do horário de funcionamento.

    Se a data for HOJE, filtra automaticamente:
    - Horários que já passaram
    - Horários que estão dentro da janela de antecedência mínima
      (ex: se agora são 20:00 e a antecedência é 75min,
       o próximo horário possível seria 21:15 — mas a barbearia
       já está fechada, então não aparece nada hoje)

    Se for um dia futuro, devolve todos os slots normalmente.
    """
    inicio = datetime.strptime(HORARIO_ABERTURA, "%H:%M")
    fim    = datetime.strptime(HORARIO_FECHAMENTO, "%H:%M")

    # Gera lista completa de slots
    horarios = []
    atual = inicio
    while atual < fim:
        horarios.append(atual.strftime("%H:%M"))
        total_min = atual.hour * 60 + atual.minute + INTERVALO_MINUTOS
        atual = atual.replace(hour=total_min // 60, minute=total_min % 60)

    # Se for hoje, remove horários que já passaram ou estão muito próximos
    if data_str and data_str == date.today().isoformat():
        agora = datetime.now()

        # Limite mínimo = agora + antecedência mínima configurada
        limite_minimo = agora.hour * 60 + agora.minute + ANTECEDENCIA_MINIMA

        horarios = [
            h for h in horarios
            # Converte "HH:MM" em minutos e compara com o limite
            if (int(h.split(":")[0]) * 60 + int(h.split(":")[1])) > limite_minimo
        ]

    return horarios


def _processar_novo_agendamento(dados, exigir_antecedencia, exigir_telefone=False,
                                permitir_conflito=False):
    """
    Valida e insere um novo agendamento. Compartilhado entre duas rotas:
    - PÚBLICA (cliente pelo site): exigir_antecedencia=True, exigir_telefone=True
      (não dá pra agendar "em cima da hora" e o telefone é obrigatório)
    - ADMIN (barbeiro registrando walk-in): ambos False
      (registra corte de agora e o telefone é opcional)

    permitir_conflito=True libera sobrescrever/encaixar em cima de outro horário
    (o barbeiro pelo painel pode; o cliente pelo site NÃO).
    dados["encaixe"] marca o agendamento como encaixe (caderninho).

    Retorna (corpo_json, status_http).
    """
    conn = get_connection()

    # Valida barbeiro_id (precisa existir e estar ativo)
    barbeiro_id = dados.get("barbeiro_id")
    if not barbeiro_id:
        conn.close()
        return {"erro": "Barbeiro é obrigatório"}, 400
    barbeiro = conn.execute(
        "SELECT id FROM barbeiros WHERE id = %s AND ativo = 1", (barbeiro_id,)
    ).fetchone()
    if not barbeiro:
        conn.close()
        return {"erro": "Barbeiro não encontrado"}, 400

    # Telefone obrigatório só no fluxo público (cliente).
    if exigir_telefone and not (dados.get("telefone") or "").strip():
        conn.close()
        return {"erro": "Telefone é obrigatório"}, 400

    # Valida cada campo. A antecedência só é checada quando exigir_antecedencia
    # for True (passando a data pro validar_hora ativa a regra). O validar_telefone
    # só confere o FORMATO quando vier preenchido (a obrigatoriedade é acima).
    data_para_hora = dados.get("data") if exigir_antecedencia else None
    checks = [
        validar_nome(dados.get("nome_cliente")),
        validar_telefone(dados.get("telefone")),
        validar_data(dados.get("data")),
        validar_hora(dados.get("hora"), data_para_hora),
        validar_servico_id(dados.get("servico_id"), conn),
    ]
    for valido, erro in checks:
        if not valido:
            conn.close()
            return {"erro": erro}, 400

    # Bloqueia dias sem atendimento (ex: domingo)
    fechado, nome_dia = dia_fechado(dados.get("data"))
    if fechado:
        conn.close()
        return {"erro": f"Não realizamos agendamentos aos {nome_dia}s."}, 400

    # Janela máxima (só no fluxo público): não deixa marcar além de X dias.
    if exigir_antecedencia:
        data_ag = datetime.strptime(dados["data"], "%Y-%m-%d").date()
        if data_ag > date.today() + timedelta(days=LIMITE_DIAS_AGENDAMENTO):
            conn.close()
            return {"erro": f"Só é possível agendar até {LIMITE_DIAS_AGENDAMENTO} dias à frente."}, 400

    # Verifica conflito de horário PARA ESTE barbeiro (só quando não é permitido
    # sobrescrever — o barbeiro pelo painel/caderninho pode encaixar em cima).
    if not permitir_conflito:
        ocupado = conn.execute(
            """SELECT id FROM agendamentos
               WHERE data = %s AND hora = %s AND barbeiro_id = %s AND status != 'cancelado'""",
            (dados["data"], dados["hora"], barbeiro_id)
        ).fetchone()
        if ocupado:
            conn.close()
            return {"erro": "Esse horário já foi reservado com este barbeiro. Escolha outro."}, 409

    # Salva cliente + agendamento (RETURNING id — Postgres não tem lastrowid)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO clientes (nome, telefone) VALUES (%s, %s) RETURNING id",
        (dados["nome_cliente"].strip(), dados.get("telefone") or None)
    )
    cliente_id = cursor.fetchone()["id"]
    cursor.execute(
        """INSERT INTO agendamentos (cliente_id, servico_id, barbeiro_id, data, hora, status, encaixe)
           VALUES (%s, %s, %s, %s, %s, 'confirmado', %s) RETURNING id""",
        (cliente_id, int(dados["servico_id"]), int(barbeiro_id), dados["data"], dados["hora"],
         bool(dados.get("encaixe")))
    )
    agendamento_id = cursor.fetchone()["id"]
    conn.commit()
    conn.close()

    return {"mensagem": "Agendamento criado com sucesso!", "agendamento_id": agendamento_id}, 201


@app.route("/api/agendamentos", methods=["POST"])
def criar_agendamento():
    """Agendamento pelo cliente (site público) — exige antecedência e telefone."""
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400
    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=True, exigir_telefone=True
    )
    return jsonify(corpo), status


@app.route("/api/agendamentos", methods=["GET"])
def listar_agendamentos():
    data_filtro = request.args.get("data")

    # Valida data se vier como filtro
    if data_filtro:
        valido, erro = validar_data(data_filtro)
        if not valido:
            return jsonify({"erro": erro}), 400

    conn = get_connection()
    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            clientes.nome  AS cliente_nome,
            clientes.telefone AS cliente_telefone,
            servicos.nome  AS servico_nome,
            servicos.preco AS servico_preco
        FROM agendamentos
        JOIN clientes ON agendamentos.cliente_id = clientes.id
        JOIN servicos ON agendamentos.servico_id = servicos.id
    """
    parametros = ()
    if data_filtro:
        query += " WHERE agendamentos.data = %s"
        parametros = (data_filtro,)

    query += " ORDER BY agendamentos.data, agendamentos.hora"
    resultados = conn.execute(query, parametros).fetchall()
    conn.close()

    return jsonify([dict(r) for r in resultados])


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


@app.route("/api/admin/agendamentos", methods=["GET"])
@token_requerido
def painel_agendamentos():
    """
    Lista agendamentos com filtros opcionais.
    Parâmetros: ?data=YYYY-MM-DD  e/ou  ?status=confirmado
    Sem filtros: retorna todos os agendamentos.
    """
    data_filtro   = request.args.get("data")
    status_filtro = request.args.get("status")

    query = """
        SELECT
            agendamentos.id,
            agendamentos.data,
            agendamentos.hora,
            agendamentos.status,
            agendamentos.barbeiro_id,
            agendamentos.servico_id,
            agendamentos.encaixe,
            clientes.nome       AS cliente_nome,
            clientes.telefone   AS cliente_telefone,
            servicos.nome       AS servico_nome,
            servicos.preco      AS servico_preco,
            servicos.duracao_min AS servico_duracao,
            barbeiros.nome      AS barbeiro_nome
        FROM agendamentos
        JOIN clientes  ON agendamentos.cliente_id  = clientes.id
        JOIN servicos  ON agendamentos.servico_id  = servicos.id
        JOIN barbeiros ON agendamentos.barbeiro_id = barbeiros.id
        WHERE 1=1
    """
    parametros = []

    if data_filtro:
        query += " AND agendamentos.data = %s"
        parametros.append(data_filtro)

    if status_filtro:
        query += " AND agendamentos.status = %s"
        parametros.append(status_filtro)

    # Escopo: barbeiro vê só os agendamentos dele; master vê todos.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        query += " AND agendamentos.barbeiro_id = %s"
        parametros.append(escopo)

    query += " ORDER BY agendamentos.data, agendamentos.hora"

    conn = get_connection()
    resultados = conn.execute(query, parametros).fetchall()
    conn.close()

    lista = [dict(r) for r in resultados]
    # Salão (tablet compartilhado) não vê valores — remove o preço dos cards.
    if not pode_ver_valores():
        for r in lista:
            r.pop("servico_preco", None)

    return jsonify(lista)


@app.route("/api/admin/agendamentos", methods=["POST"])
@token_requerido
def criar_agendamento_admin():
    """
    Cria um agendamento pelo PAINEL (ex: barbeiro registrando um walk-in —
    cliente que chegou sem horário marcado). Reusa a mesma lógica da rota
    pública, mas SEM exigir antecedência mínima (o corte é agora/hoje) e
    PERMITINDO sobrescrever/encaixar em cima de outro horário (o barbeiro
    pode cortar mais rápido e encaixar outro corte no mesmo horário).
    """
    dados = request.get_json(silent=True)
    if not dados:
        return jsonify({"erro": "Corpo da requisição inválido ou ausente"}), 400

    # Escopo: barbeiro só pode marcar pra si mesmo — força o barbeiro_id dele,
    # ignorando o que vier no corpo. Master pode escolher qualquer barbeiro.
    escopo = barbeiro_do_escopo()
    if escopo is not None:
        dados["barbeiro_id"] = escopo

    corpo, status = _processar_novo_agendamento(
        dados, exigir_antecedencia=False, permitir_conflito=True
    )
    return jsonify(corpo), status


@app.route("/api/admin/agendamentos/<int:agendamento_id>/cancelar", methods=["PATCH"])
@token_requerido
def cancelar_agendamento(agendamento_id):
    """
    Cancela um agendamento pelo ID.
    Usa PATCH porque estamos atualizando só o status,
    não o agendamento inteiro.
    """
    conn = get_connection()
    existe = conn.execute(
        "SELECT id, status, barbeiro_id FROM agendamentos WHERE id = %s", (agendamento_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    # Escopo: barbeiro só cancela agendamento dele (trata como "não encontrado"
    # pra não revelar dados de outros barbeiros). Master cancela qualquer um.
    escopo = barbeiro_do_escopo()
    if escopo is not None and existe["barbeiro_id"] != escopo:
        conn.close()
        return jsonify({"erro": "Agendamento não encontrado"}), 404

    if existe["status"] == "cancelado":
        conn.close()
        return jsonify({"erro": "Agendamento já está cancelado"}), 400

    conn.execute(
        "UPDATE agendamentos SET status = 'cancelado' WHERE id = %s",
        (agendamento_id,)
    )
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Agendamento cancelado com sucesso"})


@app.route("/api/admin/barbeiros", methods=["GET"])
@token_requerido
@somente_master
def painel_barbeiros():
    """
    Lista TODOS os barbeiros (ativos e inativos), com agendamentos futuros,
    comissão e o login de cada um (se já tiver). Só o master acessa.
    """
    hoje = date.today().isoformat()
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


@app.route("/api/admin/bloqueios", methods=["GET"])
@token_requerido
@somente_master
def listar_bloqueios():
    """Lista todos os bloqueios cadastrados."""
    conn = get_connection()
    bloqueios = conn.execute(
        "SELECT * FROM bloqueios ORDER BY data, hora"
    ).fetchall()
    conn.close()
    return jsonify([dict(b) for b in bloqueios])


@app.route("/api/admin/bloqueios", methods=["POST"])
@token_requerido
@somente_master
def criar_bloqueio():
    """
    Cria um bloqueio de dia inteiro ou horário específico.

    Dia inteiro:      { "data": "2026-07-10", "motivo": "Feriado" }
    Horário específico: { "data": "2026-07-10", "hora": "14:00", "motivo": "Compromisso" }
    """
    dados = request.get_json(silent=True)
    if not dados or not dados.get("data"):
        return jsonify({"erro": "Data é obrigatória"}), 400

    valido, erro = validar_data(dados["data"])
    if not valido:
        return jsonify({"erro": erro}), 400

    if dados.get("hora"):
        valido, erro = validar_hora(dados["hora"])
        if not valido:
            return jsonify({"erro": erro}), 400

    conn = get_connection()

    # Verifica se já existe bloqueio igual.
    # 'IS NOT DISTINCT FROM' é a comparação null-safe do Postgres (equivalente
    # ao 'hora IS ?' do SQLite): trata NULL = NULL como verdadeiro.
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND hora IS NOT DISTINCT FROM %s",
        (dados["data"], dados.get("hora"))
    ).fetchone()

    if existe:
        conn.close()
        return jsonify({"erro": "Já existe um bloqueio para essa data/hora"}), 409

    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo) VALUES (%s, %s, %s)",
        (dados["data"], dados.get("hora"), dados.get("motivo"))
    )
    conn.commit()
    conn.close()

    tipo = "Horário bloqueado" if dados.get("hora") else "Dia bloqueado"
    return jsonify({"mensagem": f"{tipo} com sucesso!"}), 201


@app.route("/api/admin/bloqueios/<int:bloqueio_id>", methods=["DELETE"])
@token_requerido
@somente_master
def remover_bloqueio(bloqueio_id):
    """Remove um bloqueio (desbloqueia o dia ou horário)."""
    conn = get_connection()
    existe = conn.execute(
        "SELECT id FROM bloqueios WHERE id = %s", (bloqueio_id,)
    ).fetchone()

    if not existe:
        conn.close()
        return jsonify({"erro": "Bloqueio não encontrado"}), 404

    conn.execute("DELETE FROM bloqueios WHERE id = %s", (bloqueio_id,))
    conn.commit()
    conn.close()

    return jsonify({"mensagem": "Bloqueio removido com sucesso"})


# -------------------------------------------------------
# ALMOÇO — o barbeiro bloqueia 60min do próprio dia (e pode liberar).
# Reaproveita a tabela bloqueios (barbeiro_id + duracao_min + motivo 'Almoço').
# Não é @somente_master: o próprio barbeiro gerencia o almoço dele.
# -------------------------------------------------------
def _barbeiro_alvo_almoco(fonte):
    """Descobre o barbeiro do almoço: o do escopo (barbeiro logado), o barbeiro_id
    do token (master = JP) ou o que vier na requisição (salão escolhendo).
    Retorna (id, erro)."""
    barbeiro_id = barbeiro_do_escopo()
    if barbeiro_id is None:
        barbeiro_id = g.usuario.get("barbeiro_id") or fonte.get("barbeiro_id")
    if not barbeiro_id:
        return None, "barbeiro_id é obrigatório"
    return barbeiro_id, None


@app.route("/api/admin/almoco", methods=["GET"])
@token_requerido
def obter_almoco():
    """Almoço do barbeiro (logado ou informado) numa data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    row = conn.execute(
        """SELECT id, hora, duracao_min FROM bloqueios
           WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'
           ORDER BY hora LIMIT 1""",
        (data, barbeiro_id)
    ).fetchone()
    conn.close()
    return jsonify(dict(row) if row else None)


@app.route("/api/admin/almoco", methods=["POST"])
@token_requerido
def marcar_almoco():
    """Barbeiro bloqueia 60min de almoço no dia. { data, hora, barbeiro_id? }"""
    dados = request.get_json(silent=True) or {}
    data = dados.get("data")
    hora = dados.get("hora")
    if not data or not hora:
        return jsonify({"erro": "Data e hora são obrigatórias"}), 400
    valido, erro = validar_data(data)
    if not valido:
        return jsonify({"erro": erro}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(dados)
    if erro:
        return jsonify({"erro": erro}), 400

    conn = get_connection()
    ja = conn.execute(
        "SELECT id FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    ).fetchone()
    if ja:
        conn.close()
        return jsonify({"erro": "Você já tem um almoço marcado nesse dia. Libere o atual primeiro."}), 409
    conn.execute(
        "INSERT INTO bloqueios (data, hora, motivo, barbeiro_id, duracao_min) VALUES (%s, %s, 'Almoço', %s, 60)",
        (data, hora, barbeiro_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço bloqueado às {hora} (60 min)."}), 201


@app.route("/api/admin/almoco", methods=["DELETE"])
@token_requerido
def liberar_almoco():
    """Libera (remove) o almoço do barbeiro na data. ?data=&barbeiro_id?="""
    data = request.args.get("data")
    if not data:
        return jsonify({"erro": "Data é obrigatória"}), 400
    barbeiro_id, erro = _barbeiro_alvo_almoco(request.args)
    if erro:
        return jsonify({"erro": erro}), 400
    conn = get_connection()
    conn.execute(
        "DELETE FROM bloqueios WHERE data = %s AND barbeiro_id = %s AND motivo = 'Almoço'",
        (data, barbeiro_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço liberado."})


def _barbeiro_do_usuario():
    """Barbeiro do usuário logado: escopo (barbeiro) ou o barbeiro_id do token
    (master = JP). Retorna None se não tiver (ex: salão)."""
    return barbeiro_do_escopo() or g.usuario.get("barbeiro_id")


@app.route("/api/admin/almoco-fixo", methods=["GET"])
@token_requerido
def obter_almoco_fixo():
    """Almoço fixo (todo dia) do barbeiro logado."""
    barbeiro_id = _barbeiro_do_usuario()
    if not barbeiro_id:
        return jsonify({"erro": "Sem barbeiro associado"}), 400
    conn = get_connection()
    row = conn.execute("SELECT almoco_fixo FROM barbeiros WHERE id = %s", (barbeiro_id,)).fetchone()
    conn.close()
    return jsonify({"almoco_fixo": row["almoco_fixo"] if row else None})


@app.route("/api/admin/almoco-fixo", methods=["PUT"])
@token_requerido
def definir_almoco_fixo():
    """Define o almoço fixo (repete todo dia, 60min). Corpo: { hora }."""
    dados = request.get_json(silent=True) or {}
    hora = dados.get("hora")
    if not hora:
        return jsonify({"erro": "Hora é obrigatória"}), 400
    valido, erro = validar_hora(hora)
    if not valido:
        return jsonify({"erro": erro}), 400
    barbeiro_id = _barbeiro_do_usuario()
    if not barbeiro_id:
        return jsonify({"erro": "Sem barbeiro associado"}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = %s WHERE id = %s", (hora, barbeiro_id))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": f"Almoço fixo definido às {hora} (todo dia)."})


@app.route("/api/admin/almoco-fixo", methods=["DELETE"])
@token_requerido
def remover_almoco_fixo():
    """Remove o almoço fixo do barbeiro (volta a marcar manualmente por dia)."""
    barbeiro_id = _barbeiro_do_usuario()
    if not barbeiro_id:
        return jsonify({"erro": "Sem barbeiro associado"}), 400
    conn = get_connection()
    conn.execute("UPDATE barbeiros SET almoco_fixo = NULL WHERE id = %s", (barbeiro_id,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Almoço fixo removido."})


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

    hoje = date.today()
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
        "faturamento_total": faturamento,
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
    data_str = request.args.get("data") or date.today().isoformat()
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

    # Detalhe: quantos de cada serviço, por barbeiro (ex: 3 Degradê, 1 Corte Social).
    detalhe = conn.execute(f"""
        SELECT barbeiros.id AS barbeiro_id, servicos.nome AS servico_nome,
               COUNT(agendamentos.id) AS quantidade
        FROM barbeiros
        JOIN agendamentos ON agendamentos.barbeiro_id = barbeiros.id
             AND agendamentos.data = %s AND agendamentos.status != 'cancelado'
        JOIN servicos ON agendamentos.servico_id = servicos.id
        {filtro}
        GROUP BY barbeiros.id, servicos.nome
        ORDER BY quantidade DESC, servicos.nome
    """, params).fetchall()
    conn.close()

    por_barbeiro = {}
    for d in detalhe:
        por_barbeiro.setdefault(d["barbeiro_id"], []).append(
            {"nome": d["servico_nome"], "quantidade": d["quantidade"]}
        )

    # Salão (tablet) NÃO vê valores — só a quantidade de cortes.
    ver_valores = pode_ver_valores()

    barbeiros = []
    tot_clientes = tot_valor = tot_barbeiro = tot_barbearia = 0
    for r in linhas:
        total = float(r["total"] or 0)
        pct = r["comissao_pct"]
        recebe_barbeiro = round(total * pct / 100, 2)
        recebe_barbearia = round(total - recebe_barbeiro, 2)

        item = {
            "barbeiro_id": r["barbeiro_id"],
            "barbeiro_nome": r["barbeiro_nome"],
            "clientes": r["clientes"],
            "servicos": por_barbeiro.get(r["barbeiro_id"], [])
        }
        if ver_valores:
            item.update({
                "total": round(total, 2),
                "comissao_pct": pct,
                "barbeiro_recebe": recebe_barbeiro,
                "barbearia_recebe": recebe_barbearia
            })
        barbeiros.append(item)

        tot_clientes += r["clientes"]
        tot_valor += total
        tot_barbeiro += recebe_barbeiro
        tot_barbearia += recebe_barbearia

    totais = {"clientes": tot_clientes}
    if ver_valores:
        totais.update({
            "total": round(tot_valor, 2),
            "barbeiro_recebe": round(tot_barbeiro, 2),
            "barbearia_recebe": round(tot_barbearia, 2)
        })

    return jsonify({
        "data": data_str,
        "ver_valores": ver_valores,
        "barbeiros": barbeiros,
        "totais": totais
    })


# -------------------------------------------------------
# INICIALIZAÇÃO
# -------------------------------------------------------
# Roda no carregamento do módulo para funcionar tanto com `python app.py`
# quanto sob gunicorn em produção (o bloco __main__ NÃO executa sob gunicorn,
# então init_db/seeds precisam ficar aqui fora). Tudo é idempotente
# (CREATE TABLE / ADD COLUMN IF NOT EXISTS e seeds que checam antes de inserir).
init_db()
criar_admin_padrao()      # login master (dono) — do .env
criar_salao_padrao()      # login do salão (tablet) — do .env
criar_barbeiros_padrao()  # logins dos barbeiros comuns (2 e 3) — do .env
ajustar_servicos()        # cardápio oficial (nome/preço/duração/imagem)

if __name__ == "__main__":
    print(f"[config] Ambiente: {FLASK_ENV}")
    print(f"[config] CORS permitido para: {CORS_ORIGIN}")
    print(f"[config] Funcionamento: {HORARIO_ABERTURA} às {HORARIO_FECHAMENTO}")
    app.run(debug=not EM_PRODUCAO)
