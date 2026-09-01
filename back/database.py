"""
database.py
------------
Agora usando PostgreSQL (Supabase) em vez de SQLite.

A conexão vem da variável DATABASE_URL no .env (connection string do
Supabase — Session pooler). Não existe mais arquivo local: o banco é
gerenciado na nuvem.

Para o app.py continuar simples, get_connection() devolve um pequeno
wrapper que imita o atalho `conn.execute(...)` do sqlite3 e entrega cada
linha como um dicionário (row["coluna"]).
"""

import os
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
import bcrypt
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# POOL DE CONEXÕES — o motivo de existir:
# abrir conexão nova com o Supabase custava ~1 SEGUNDO por requisição (handshake
# TLS + autenticação, com o banco em outra região). Medido em produção: um
# endpoint de UMA query levava 1,2s contra 0,2s de um que não toca no banco.
# Reaproveitando a conexão, esse segundo some de toda requisição do sistema.
#
# Tamanho: precisa ser >= ao número de threads do gunicorn (ver Procfile), senão
# uma thread fica esperando conexão livre. Ajustável por DB_POOL_MAX.
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "6"))
_pool = None
_pool_lock = threading.Lock()


def _obter_pool():
    """Cria o pool na primeira necessidade (não no import, pra não travar o boot)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:   # confere de novo já com o lock
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1, _POOL_MAX, DATABASE_URL, connect_timeout=15
                )
    return _pool

# Rede de segurança contra vazamento de conexão: cada conexão aberta numa
# requisição fica registrada nesta thread. Se um endpoint estourar exceção antes
# do conn.close(), o app.py chama fechar_conexoes_pendentes() no teardown e a
# conexão é liberada (com rollback). Endpoints que já fecham normalmente não são
# afetados — o fechamento é idempotente.
_conexoes_thread = threading.local()


def _registrar_conexao(conexao):
    lista = getattr(_conexoes_thread, "lista", None)
    if lista is None:
        lista = []
        _conexoes_thread.lista = lista
    lista.append(conexao)


def fechar_conexoes_pendentes():
    """Fecha (com rollback) conexões desta thread que não foram fechadas."""
    lista = getattr(_conexoes_thread, "lista", None)
    if not lista:
        return
    for conexao in lista:
        try:
            if not conexao.fechada:
                conexao.rollback()
                conexao.close()
        except Exception:
            pass
    _conexoes_thread.lista = []


class _Conexao:
    """
    Wrapper leve sobre a conexão psycopg2 que lembra a API do sqlite3:
    - conn.execute(sql, params) -> cursor (com fetchone/fetchall)
    - conn.cursor(), conn.commit(), conn.close()
    As linhas voltam como dicionário (RealDictRow), então row["nome"] e
    dict(row) funcionam igual ao sqlite3.Row.
    """
    def __init__(self, pgconn, pool=None):
        self._conn = pgconn
        self._pool = pool
        self._devolvida = False

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Sem params, chama sem o 2º argumento pra o psycopg2 não tentar
        # interpretar '%' como placeholder.
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return cur

    def cursor(self):
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        if not self._conn.closed:
            self._conn.rollback()

    @property
    def fechada(self):
        # Já devolvida ao pool conta como fechada: a rede de segurança do
        # teardown não pode mexer numa conexão que outra thread já pegou.
        return self._devolvida or self._conn.closed != 0

    def close(self):
        # Idempotente: fechar duas vezes (endpoint + rede de segurança) não quebra.
        if self._devolvida:
            return
        self._devolvida = True

        if self._pool is None:            # sem pool: comportamento antigo
            if not self._conn.closed:
                self._conn.close()
            return

        try:
            if self._conn.closed:
                self._pool.putconn(self._conn, close=True)
                return
            # ROLLBACK antes de devolver: as leituras deixam uma transação
            # aberta, e devolver assim faria a próxima requisição herdar um
            # snapshot velho (e prenderia a conexão como "idle in transaction").
            self._conn.rollback()
            self._pool.putconn(self._conn)
        except Exception:
            # Conexão quebrada (banco reiniciou, rede caiu): descarta em vez de
            # devolver defeituosa pro pool.
            try:
                self._pool.putconn(self._conn, close=True)
            except Exception:
                pass


def get_connection():
    """Abre uma conexão com o Postgres (Supabase) usando a DATABASE_URL."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL não definida. Configure a connection string do "
            "Supabase no arquivo .env (veja o .env.example)."
        )
    pool = _obter_pool()
    pgconn = pool.getconn()
    # Conexão que ficou parada pode ter caído do outro lado (Supabase derruba
    # ociosas). Se vier morta, troca por uma nova em vez de estourar no endpoint.
    if pgconn.closed:
        pool.putconn(pgconn, close=True)
        pgconn = pool.getconn()
    conexao = _Conexao(pgconn, pool)
    _registrar_conexao(conexao)
    return conexao


def init_db():
    """
    Cria as tabelas (se não existirem) e insere os dados de exemplo
    (serviços e barbeiros) apenas na primeira vez.
    """
    conn = get_connection()
    cur = conn.cursor()

    # ---------------------------------------------------------
    # CLIENTES
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            telefone TEXT,
            criado_em TIMESTAMPTZ DEFAULT now()
        )
    """)
    # email: opcional, usado pra mandar a confirmação e o lembrete automático.
    cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS email TEXT")

    # ---------------------------------------------------------
    # SERVICOS
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS servicos (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            duracao_min INTEGER NOT NULL,
            preco REAL NOT NULL
        )
    """)
    # imagem: nome do arquivo em front/img (ex: 'degrade.jpg'); NULL = sem foto.
    cur.execute("ALTER TABLE servicos ADD COLUMN IF NOT EXISTS imagem TEXT")

    # ---------------------------------------------------------
    # BARBEIROS
    # ativo (1/0) permite desativar um barbeiro sem apagar o histórico.
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS barbeiros (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1
        )
    """)
    # comissao_pct = % do valor do corte que fica com o BARBEIRO (resto = barbearia).
    # Padrão 60 (60% barbeiro / 40% barbearia). Coluna aditiva (não quebra o que existe).
    cur.execute("ALTER TABLE barbeiros ADD COLUMN IF NOT EXISTS comissao_pct INTEGER NOT NULL DEFAULT 60")
    # foto: nome do arquivo em front/img (ex: 'rian.jpg'); NULL = sem foto.
    cur.execute("ALTER TABLE barbeiros ADD COLUMN IF NOT EXISTS foto TEXT")
    # almoco_fixo: hora 'HH:MM' de almoço que se repete TODO dia (bloqueia 60min);
    # NULL = sem almoço fixo (o barbeiro marca manualmente dia a dia).
    cur.execute("ALTER TABLE barbeiros ADD COLUMN IF NOT EXISTS almoco_fixo TEXT")
    # Canal do Telegram por barbeiro. HOJE NÃO É USADO — todos os avisos vão pro
    # grupo da equipe. A coluna fica aqui porque o recurso pode voltar (ver commit
    # b0c6d40, onde ele está implementado por inteiro).
    cur.execute("ALTER TABLE barbeiros ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT")

    # ---------------------------------------------------------
    # AGENDAMENTOS
    # FOREIGN KEY garante integridade com clientes/servicos/barbeiros.
    # status: 'confirmado', 'cancelado' ou 'concluido'
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agendamentos (
            id SERIAL PRIMARY KEY,
            cliente_id  INTEGER NOT NULL REFERENCES clientes (id),
            servico_id  INTEGER NOT NULL REFERENCES servicos (id),
            barbeiro_id INTEGER NOT NULL REFERENCES barbeiros (id),
            data TEXT NOT NULL,       -- 'YYYY-MM-DD'
            hora TEXT NOT NULL,       -- 'HH:MM'
            status TEXT NOT NULL DEFAULT 'confirmado',
            criado_em TIMESTAMPTZ DEFAULT now()
        )
    """)
    # encaixe: marca os cortes lançados pelo caderninho virtual (encaixe do barbeiro).
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS encaixe BOOLEAN NOT NULL DEFAULT false")
    # forma_pagamento: 'cartao' | 'pix' | 'dinheiro' | NULL (ainda não registrado).
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT")
    # Quando o lembrete automático (1h antes) foi enviado — evita mandar duas vezes.
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS lembrete_enviado_em TIMESTAMPTZ")

    # QUEM cancelou, QUANDO e POR ONDE.
    #
    # Em 01/09/2026 três clientes foram desmarcados e não houve como descobrir
    # quem fez: o sistema só trocava o status. A resposta só apareceu porque
    # existia backup do dia anterior pra comparar — arqueologia, não registro.
    # cancelado_via: 'painel' (alguém logado) ou 'site' (o próprio cliente).
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS cancelado_em TIMESTAMPTZ")
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS cancelado_por TEXT")
    cur.execute("ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS cancelado_via TEXT")

    # ---------------------------------------------------------
    # ADMIN (usuários do painel — senha em hash bcrypt)
    # Agora com PAPEL: 'master' (dono, vê tudo) ou 'barbeiro' (vê só o dele).
    # barbeiro_id liga o login a um barbeiro. Colunas aditivas — a tabela e o
    # login que já existiam continuam funcionando igual.
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id SERIAL PRIMARY KEY,
            usuario TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL
        )
    """)
    cur.execute("ALTER TABLE admin ADD COLUMN IF NOT EXISTS papel TEXT NOT NULL DEFAULT 'master'")
    cur.execute("ALTER TABLE admin ADD COLUMN IF NOT EXISTS barbeiro_id INTEGER REFERENCES barbeiros (id)")

    # ---------------------------------------------------------
    # BLOQUEIOS (dia inteiro = hora NULL; horário específico = hora preenchida)
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bloqueios (
            id SERIAL PRIMARY KEY,
            data TEXT NOT NULL,
            hora TEXT,
            motivo TEXT,
            criado_em TIMESTAMPTZ DEFAULT now()
        )
    """)
    # Colunas aditivas: barbeiro_id (NULL = vale pra todos; preenchido = só
    # daquele barbeiro, ex: almoço) e duracao_min (janela do bloqueio em minutos,
    # ex: 60 pro almoço; NULL = um slot só, como os bloqueios antigos).
    cur.execute("ALTER TABLE bloqueios ADD COLUMN IF NOT EXISTS barbeiro_id INTEGER REFERENCES barbeiros (id)")
    cur.execute("ALTER TABLE bloqueios ADD COLUMN IF NOT EXISTS duracao_min INTEGER")

    # ---------------------------------------------------------
    # CLIENTES BLOQUEADOS — quem não pode marcar SOZINHO pelo site.
    #
    # Não confundir com a tabela `bloqueios` acima, que é de agenda (feriado,
    # compromisso). Esta é de pessoa.
    #
    # A chave é o telefone só com dígitos: não existe cadastro único de cliente
    # (a tabela `clientes` ganha uma linha nova a cada agendamento), então o
    # telefone é o que identifica alguém na prática — é assim que as rotas de
    # consultar e cancelar já acham o agendamento da pessoa.
    #
    # O barbeiro CONTINUA podendo encaixar essa pessoa pelo caderninho: o
    # bloqueio impede o auto-agendamento, não o atendimento.
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes_bloqueados (
            id SERIAL PRIMARY KEY,
            telefone TEXT NOT NULL UNIQUE,
            nome TEXT,
            motivo TEXT,
            criado_em TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ---------------------------------------------------------
    # EXPEDIENTE — jornada de um barbeiro num DIA específico (o master define
    # que o fulano hoje trabalha das 'inicio' às 'fim'). Sobrepõe o horário
    # padrão do dia SÓ pra aquele barbeiro naquela data. Um por barbeiro/dia.
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expedientes (
            id SERIAL PRIMARY KEY,
            barbeiro_id INTEGER NOT NULL REFERENCES barbeiros (id),
            data   TEXT NOT NULL,   -- 'YYYY-MM-DD'
            inicio TEXT NOT NULL,   -- 'HH:MM'
            fim    TEXT NOT NULL,   -- 'HH:MM'
            UNIQUE (barbeiro_id, data)
        )
    """)

    # ---------------------------------------------------------
    # CONSUMOS — produtos consumidos no atendimento (ex: refrigerante, salgado).
    # Não é serviço: entra no faturamento numa linha à parte e NÃO comissiona.
    # valor_centavos guarda o preço em centavos (evita float). Free-form por
    # enquanto (sem catálogo fixo de produtos).
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS consumos (
            id SERIAL PRIMARY KEY,
            agendamento_id INTEGER NOT NULL REFERENCES agendamentos (id) ON DELETE CASCADE,
            descricao TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL DEFAULT 0,
            criado_em TIMESTAMPTZ DEFAULT now()
        )
    """)
    # produto_id: qual adicional do catálogo (ver PRODUTOS_ADICIONAIS no app.py).
    # quantidade: quantos daquele item. valor_centavos passa a ser o preço UNITÁRIO
    # (snapshot na hora do lançamento). Total do item = valor_centavos * quantidade.
    cur.execute("ALTER TABLE consumos ADD COLUMN IF NOT EXISTS produto_id INTEGER")
    cur.execute("ALTER TABLE consumos ADD COLUMN IF NOT EXISTS quantidade INTEGER NOT NULL DEFAULT 1")

    # ---------------------------------------------------------
    # PRODUTOS — o catálogo de adicionais vendidos no balcão (bebida, pomada...).
    # Fica no banco, e não no código, pra o dono poder mudar preço pelo painel.
    # ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            preco_centavos INTEGER NOT NULL DEFAULT 0,
            ativo SMALLINT NOT NULL DEFAULT 1
        )
    """)

    # ---------------------------------------------------------
    # ÍNDICES — aceleram as consultas mais frequentes (agenda do dia, horários
    # disponíveis, contagem). Sem eles o Postgres varre a tabela inteira. Só
    # criação (idempotente): não muda dado nenhum.
    # ---------------------------------------------------------
    cur.execute("CREATE INDEX IF NOT EXISTS ix_agendamentos_barbeiro_data ON agendamentos (barbeiro_id, data, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_agendamentos_data ON agendamentos (data)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_consumos_agendamento ON consumos (agendamento_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_bloqueios_data ON bloqueios (data, barbeiro_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_admin_barbeiro ON admin (barbeiro_id)")

    conn.commit()

    # ---------------------------------------------------------
    # SEED — só insere se a tabela estiver vazia
    # ---------------------------------------------------------
    cur.execute("SELECT COUNT(*) AS n FROM barbeiros")
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            "INSERT INTO barbeiros (nome) VALUES (%s)",
            [("Barbeiro 1",), ("Barbeiro 2",), ("Barbeiro 3",)]
        )
        conn.commit()
        print("[seed] 3 barbeiros inseridos. Renomeie no banco quando quiser.")

    cur.execute("SELECT COUNT(*) AS n FROM servicos")
    if cur.fetchone()["n"] == 0:
        servicos_exemplo = [
            ("Degradê", 40, 35.00),
            ("Social", 30, 25.00),
            ("Navalhado", 45, 40.00),
            ("Barba", 20, 20.00),
            ("Corte + Barba", 60, 50.00),
            ("Sobrancelha", 10, 10.00),
        ]
        cur.executemany(
            "INSERT INTO servicos (nome, duracao_min, preco) VALUES (%s, %s, %s)",
            servicos_exemplo
        )
        conn.commit()
        print(f"[seed] {len(servicos_exemplo)} serviços inseridos.")

    conn.close()


def _e_instalacao_nova(conn, barbeiro_id):
    """
    True se este barbeiro ainda está com o nome genérico do seed ("Barbeiro 1").
    É como sabemos que ninguém configurou nada ainda: só nesse caso os valores
    do .env são aplicados. Depois disso, quem manda é o banco (e o painel).
    """
    linha = conn.execute(
        "SELECT nome FROM barbeiros WHERE id = %s", (barbeiro_id,)
    ).fetchone()
    return bool(linha) and (linha["nome"] or "").startswith("Barbeiro ")


def criar_admin_padrao(usuario=None, senha=None):
    """
    Cria/garante o usuário MASTER (o dono) se ainda não existir. Usuário e
    senha vêm do .env (ADMIN_USUARIO / ADMIN_SENHA). Papel 'master' = vê tudo;
    fica ligado ao barbeiro 1 (o dono também corta). A senha é hash bcrypt.

    Idempotente: se o admin já existia (de antes das colunas de papel), garante
    que ele vire master ligado a um barbeiro.

    IMPORTANTE: defina uma ADMIN_SENHA forte no .env antes de ir pra produção.
    """
    usuario = usuario or os.getenv("ADMIN_USUARIO", "admin")
    senha   = senha   or os.getenv("ADMIN_SENHA", "admin123")

    conn = get_connection()
    existe = conn.execute("SELECT id FROM admin WHERE usuario = %s", (usuario,)).fetchone()
    if not existe:
        senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (usuario, senha_hash, papel, barbeiro_id) VALUES (%s, %s, 'master', 1)",
            (usuario, senha_hash)
        )
        print(f"[seed] Master '{usuario}' criado (hash bcrypt). Troque a senha antes da produção!")
    else:
        # garante papel master + ligação a um barbeiro (migração de admin antigo)
        conn.execute(
            "UPDATE admin SET papel = 'master', barbeiro_id = COALESCE(barbeiro_id, 1) WHERE usuario = %s",
            (usuario,)
        )
    # Dados do dono (barbeiro 1): nome, foto e comissão.
    #
    # Só preenche numa instalação NOVA (quando o nome ainda é o "Barbeiro 1" do
    # seed). Depois disso o BANCO manda: o que for alterado pelo painel fica.
    # Antes isso rodava a cada deploy e desfazia mudanças feitas à mão — foi
    # assim que a comissão do dono "voltou sozinha" uma vez.
    if _e_instalacao_nova(conn, 1):
        conn.execute(
            "UPDATE barbeiros SET nome = %s, foto = %s, comissao_pct = %s WHERE id = 1",
            (os.getenv("BARBEIRO1_NOME", "JP"),
             os.getenv("BARBEIRO1_FOTO", "jp.jpg"),
             int(os.getenv("BARBEIRO1_COMISSAO", "0")))
        )
    conn.commit()
    conn.close()


def criar_salao_padrao(usuario=None, senha=None):
    """
    Cria o login do SALÃO (o do tablet compartilhado) se não existir.
    Papel 'salao': vê a agenda de TODOS e marca encaixe, mas NÃO vê valores
    (só a quantidade de cortes). Não é ligado a nenhum barbeiro específico.
    Configurável via SALAO_USUARIO / SALAO_SENHA no .env.
    """
    usuario = usuario or os.getenv("SALAO_USUARIO", "salao")
    senha   = senha   or os.getenv("SALAO_SENHA", "salao123")

    conn = get_connection()
    existe = conn.execute("SELECT id FROM admin WHERE usuario = %s", (usuario,)).fetchone()
    if not existe:
        senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (usuario, senha_hash, papel, barbeiro_id) VALUES (%s, %s, 'salao', NULL)",
            (usuario, senha_hash)
        )
        conn.commit()
        print(f"[seed] Login do salão '{usuario}' criado (papel salao). Troque a senha!")
    conn.close()


def _sync_login_barbeiro(barbeiro_id, nome, usuario, senha, foto=None):
    """
    Sincroniza um barbeiro no boot (dirigido por config, sem botão):
    - NOME de exibição, FOTO e USUÁRIO de login: atualizados sempre (não são segredo).
    - SENHA: definida só na CRIAÇÃO. Depois é self-service (cada um troca a sua)
      ou o master reseta — nunca sobrescrevemos senha aqui.
    """
    conn = get_connection()
    # Nome e foto: só na instalação nova (ver _e_instalacao_nova). Depois o
    # banco é a fonte da verdade e nada aqui sobrescreve o que foi ajustado.
    if _e_instalacao_nova(conn, barbeiro_id):
        if nome:
            conn.execute("UPDATE barbeiros SET nome = %s WHERE id = %s", (nome, barbeiro_id))
        if foto:
            conn.execute("UPDATE barbeiros SET foto = %s WHERE id = %s", (foto, barbeiro_id))
    login = conn.execute(
        "SELECT id FROM admin WHERE barbeiro_id = %s AND papel = 'barbeiro'", (barbeiro_id,)
    ).fetchone()
    if not login:
        senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (usuario, senha_hash, papel, barbeiro_id) VALUES (%s, %s, 'barbeiro', %s)",
            (usuario, senha_hash, barbeiro_id)
        )
        print(f"[seed] Login '{usuario}' criado (barbeiro id={barbeiro_id}). Senha inicial temporária — troque no 1º acesso.")
    # Se o login já existe, não mexe: o usuário pode ter sido ajustado pelo
    # painel, e sobrescrever aqui desfaria a mudança no próximo deploy.
    conn.commit()
    conn.close()


def criar_barbeiros_padrao():
    """
    Sincroniza os barbeiros 2 e 3 (nome + usuário) a partir da config, e cria o
    login se ainda não existir. Nome/usuário via .env (com defaults JP/Rian/Gabriel);
    a senha é temporária na criação e depois é trocada pela pessoa (self-service).
    """
    _sync_login_barbeiro(
        2,
        os.getenv("BARBEIRO2_NOME", "Rian"),
        os.getenv("BARBEIRO2_USUARIO", "rian"),
        os.getenv("BARBEIRO2_SENHA", "mudar@123"),
        os.getenv("BARBEIRO2_FOTO", "rian.jpg"),
    )
    _sync_login_barbeiro(
        3,
        os.getenv("BARBEIRO3_NOME", "Gabriel Xeybão"),
        os.getenv("BARBEIRO3_USUARIO", "gabriel"),
        os.getenv("BARBEIRO3_SENHA", "mudar@123"),
        os.getenv("BARBEIRO3_FOTO", "xeybao.jpg"),
    )


def ajustar_servicos():
    """
    Garante que o cardápio de serviços EXISTA. Roda no boot e é idempotente.

    Esta lista é só o ponto de partida: cria o que ainda não existe e migra
    nomes antigos. Serviço que já está no banco não é alterado — preço e
    duração passaram a ser editáveis no painel, e o banco é a fonte da verdade.
    """
    conn = get_connection()
    # Renomeações + preços dos serviços que vieram do seed antigo (casa pelo nome antigo).
    # (nome_antigo, nome_novo, duracao_min, preco, imagem)
    renomear = [
        ("Degradê",       "Degradê",       40, 40.00, "degrade.jpg"),
        ("Social",        "Corte Social",  30, 35.00, "social.jpg"),
        ("Barba",         "Barba",         20, 25.00, "barba.jpg"),
        ("Corte + Barba", "Corte + Barba", 50, 60.00, "corte-barba.jpg"),
        ("Navalhado",     "Navalhado",     45, 40.00, None),
        ("Sobrancelha",   "Sobrancelha",   10, 10.00, None),
    ]
    for antigo, novo, dur, preco, img in renomear:
        if antigo == novo:
            continue                      # nada a renomear
        # Só migra quem ainda está com o nome antigo. Se já foi renomeado, o
        # preço e a duração atuais são os do banco — não mexemos.
        conn.execute(
            "UPDATE servicos SET nome=%s, duracao_min=%s, preco=%s, imagem=%s WHERE nome=%s",
            (novo, dur, preco, img, antigo)
        )
    # Serviços que não vêm do seed antigo — UPSERT por nome (cria ou atualiza
    # preço/duração). Durações dos químicos são estimadas — ajuste aqui se precisar.
    extras = [
        ("Corte e penteado",           50, 50.00, None),
        ("Limpeza de pele",            20, 25.00, None),
        ("Corte + Progressiva",        60, 75.00, None),
        ("Corte + Alisamento",         45, 55.00, None),
        ("Corte + Botox",              60, 65.00, None),
        ("Corte + Hidratação Capilar", 60, 60.00, None),
        ("Acabamento",                 10, 10.00, None),
        ("Corte + Luzes",              60, 95.00, None)
    ]
    for nome, dur, preco, img in extras:
        # Serviço que já existe NÃO é tocado: preço e duração são do banco,
        # editáveis pelo painel. Aqui a lista serve só pra CRIAR o que falta.
        if not conn.execute("SELECT id FROM servicos WHERE nome=%s", (nome,)).fetchone():
            conn.execute(
                "INSERT INTO servicos (nome, duracao_min, preco, imagem) VALUES (%s,%s,%s,%s)",
                (nome, dur, preco, img)
            )
    conn.commit()
    conn.close()


def criar_produtos_padrao():
    """
    Garante que o catálogo de adicionais EXISTA (bebidas, pomadas...).

    Igual aos serviços: só cria o que falta. Preço de produto que já está no
    banco não é tocado — quem manda nele é o painel.
    """
    padrao = [
        (1, "Salgadinho",        500),
        (2, "Long neck",         800),
        (3, "Corona",           1000),
        (4, "Refrigerante",      600),
        (5, "Cerveja 269 ml",    400),
        (6, "Óleo de barba",    2500),
        (7, "Pomada em pó",     2000),
        (8, "Pomada modeladora", 2000),
        (9, "Gel cola",         1500),
    ]
    conn = get_connection()
    cur = conn.cursor()
    # IDs fixos de propósito: os consumos já lançados apontam pra eles.
    cur.executemany(
        "INSERT INTO produtos (id, nome, preco_centavos) VALUES (%s, %s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        padrao
    )
    # Deixa a sequência do SERIAL à frente dos IDs fixos, senão um produto novo
    # criado depois tentaria reaproveitar um id já usado.
    cur.execute(
        "SELECT setval(pg_get_serial_sequence('produtos', 'id'), "
        "COALESCE((SELECT MAX(id) FROM produtos), 1))"
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    # Permite rodar "python database.py" pra criar/verificar o schema.
    init_db()
    criar_admin_padrao()
    criar_salao_padrao()
    criar_barbeiros_padrao()
    ajustar_servicos()
    criar_produtos_padrao()
    print("Banco de dados (Postgres/Supabase) criado/verificado.")
