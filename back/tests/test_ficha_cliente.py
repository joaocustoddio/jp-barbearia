"""
Testes da identificação do cliente pelo telefone.

O sistema criava uma ficha nova a cada agendamento: em 05/10/2026 eram 846
fichas para 284 telefones. Sem uma ficha por pessoa não existe histórico, e
sem histórico não dá pra saber quem voltou, quem sumiu nem quem é bom cliente.

O que precisa continuar valendo:

- mesmo telefone, mesma ficha — mesmo digitado com máscara diferente;
- telefone da barbearia NÃO junta ninguém: lá cabem dezenas de pessoas
  diferentes, e uma delas chegou a acumular 22 nomes;
- sem telefone também não junta, porque não dá pra afirmar que é a mesma pessoa.

Conexão falsa: nada de banco.
"""
import pytest

import agendamentos


class _Cursor:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchall(self):
        return self._linhas


class Conexao:
    """Finge o banco: diz se o telefone é genérico e se já existe ficha."""

    def __init__(self, ficha_existente=None, generico=False):
        self.ficha_existente = ficha_existente
        self.generico = generico
        self.executados = []

    def execute(self, sql, params=None):
        # Imita o psycopg2: o resultado fica guardado, porque o código faz
        # `cur.execute(...)` e depois `cur.fetchone()`.
        consulta = " ".join(sql.split()).lower()
        self.executados.append((consulta, params))
        if "from telefones_genericos" in consulta:
            self._ultimo = _Cursor([{"?column?": 1}] if self.generico else [])
        elif "from clientes where regexp_replace" in consulta:
            self._ultimo = _Cursor([{"id": self.ficha_existente}] if self.ficha_existente else [])
        elif consulta.startswith("insert into clientes"):
            self._ultimo = _Cursor([{"id": 900}])
        else:
            self._ultimo = _Cursor([])
        return self._ultimo

    def cursor(self):
        return self

    def fetchone(self):
        return self._ultimo.fetchone()

    def commit(self):
        pass

    def close(self):
        pass

    # ------- leitura pros testes
    def inserts(self):
        return [p for sql, p in self.executados if sql.startswith("insert into clientes")]

    def updates(self):
        return [p for sql, p in self.executados if sql.startswith("update clientes")]

    def buscas(self):
        return [p for sql, p in self.executados if "from clientes where regexp_replace" in sql]


def test_cliente_novo_ganha_ficha(cliente_novo=None):
    conn = Conexao(ficha_existente=None)
    assert agendamentos.buscar_ou_criar_cliente(conn, "Rafael", "11988887766", None) == 900
    assert len(conn.inserts()) == 1


def test_mesmo_telefone_reaproveita_a_ficha():
    conn = Conexao(ficha_existente=42)
    assert agendamentos.buscar_ou_criar_cliente(conn, "Rafael", "11988887766", None) == 42
    assert conn.inserts() == []          # nada de ficha nova


@pytest.mark.parametrize("digitado", [
    "11988887766",
    "(11) 98888-7766",
    "11 98888 7766",
    "+55 11 98888-7766 ",
])
def test_mascara_nao_cria_pessoa_nova(digitado):
    """O mesmo cliente digita de um jeito diferente a cada vez. Se a busca
    comparasse o texto cru, cada formato viraria uma pessoa."""
    conn = Conexao(ficha_existente=42)
    agendamentos.buscar_ou_criar_cliente(conn, "Rafael", digitado, None)
    numero = conn.buscas()[0][0]
    assert numero.endswith("11988887766")
    assert numero.isdigit()


def test_telefone_da_barbearia_nunca_junta():
    """É o número usado quando o cliente não informa o dele. Juntar por ali
    empilharia dezenas de pessoas numa ficha só."""
    conn = Conexao(ficha_existente=42, generico=True)
    assert agendamentos.buscar_ou_criar_cliente(conn, "Pedro", "11972570084", None) == 900
    assert len(conn.inserts()) == 1
    assert conn.buscas() == []           # nem chegou a procurar ficha


def test_sem_telefone_nao_junta():
    conn = Conexao(ficha_existente=42)
    assert agendamentos.buscar_ou_criar_cliente(conn, "Visitante", "", None) == 900
    assert len(conn.inserts()) == 1
    assert conn.buscas() == []


def test_reencontro_atualiza_o_nome():
    """Quem casou e mudou o sobrenome, ou digitou o nome errado da primeira
    vez, não fica preso ao cadastro velho."""
    conn = Conexao(ficha_existente=42)
    agendamentos.buscar_ou_criar_cliente(conn, "Rafael Souza", "11988887766", None)
    assert conn.updates()[0][0] == "Rafael Souza"


def test_reencontro_sem_email_nao_apaga_o_que_ja_tinha():
    """O site pede e-mail; o caderninho não. Um corte anotado no balcão não
    pode apagar o e-mail que manda a confirmação."""
    conn = Conexao(ficha_existente=42)
    agendamentos.buscar_ou_criar_cliente(conn, "Rafael", "11988887766", "")
    sql = [s for s, _ in conn.executados if s.startswith("update clientes")][0]
    assert "coalesce(%s, email)" in sql
    assert conn.updates()[0][1] is None


def test_email_novo_entra_no_cadastro():
    conn = Conexao(ficha_existente=42)
    agendamentos.buscar_ou_criar_cliente(conn, "Rafael", "11988887766", " raf@ex.com ")
    assert conn.updates()[0][1] == "raf@ex.com"
