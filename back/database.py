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
import psycopg2
import psycopg2.extras
import bcrypt
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


class _Conexao:
    """
    Wrapper leve sobre a conexão psycopg2 que lembra a API do sqlite3:
    - conn.execute(sql, params) -> cursor (com fetchone/fetchall)
    - conn.cursor(), conn.commit(), conn.close()
    As linhas voltam como dicionário (RealDictRow), então row["nome"] e
    dict(row) funcionam igual ao sqlite3.Row.
    """
    def __init__(self, pgconn):
        self._conn = pgconn

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

    def close(self):
        self._conn.close()


def get_connection():
    """Abre uma conexão com o Postgres (Supabase) usando a DATABASE_URL."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL não definida. Configure a connection string do "
            "Supabase no arquivo .env (veja o .env.example)."
        )
    pgconn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    return _Conexao(pgconn)


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


if __name__ == "__main__":
    # Permite rodar "python database.py" pra criar/verificar o schema.
    init_db()
    criar_admin_padrao()
    criar_salao_padrao()
    print("Banco de dados (Postgres/Supabase) criado/verificado.")
