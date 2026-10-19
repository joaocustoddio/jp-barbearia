"""Exercita a rota e as consultas reais com um banco SQLite isolado em memória."""
import sqlite3
from datetime import date

import pytest

import app
import rotas_admin_agenda as agenda
from test_criar_cancelar import cabecalho_admin


class Banco:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE barbeiros(id INTEGER PRIMARY KEY, almoco_fixo TEXT);
          CREATE TABLE servicos(id INTEGER PRIMARY KEY, preco REAL, duracao_min INTEGER);
          CREATE TABLE agendamentos(id INTEGER PRIMARY KEY, barbeiro_id INTEGER,
            servico_id INTEGER, data TEXT, hora TEXT, status TEXT,
            valor_centavos INTEGER, duracao_min INTEGER, preco_personalizado BOOLEAN DEFAULT 0);
          CREATE TABLE bloqueios(data TEXT, barbeiro_id INTEGER, hora TEXT, duracao_min INTEGER, motivo TEXT);
          CREATE TABLE agendamento_alteracoes(id INTEGER PRIMARY KEY, agendamento_id INTEGER,
            admin_id INTEGER, valor_anterior_centavos INTEGER, valor_novo_centavos INTEGER,
            duracao_anterior_min INTEGER, duracao_nova_min INTEGER, criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
          INSERT INTO barbeiros VALUES(2, NULL);
          INSERT INTO servicos VALUES(1, 40, 50);
          INSERT INTO agendamentos VALUES(1,2,1,'2026-10-21','09:00','confirmado',4000,50,0);
          INSERT INTO agendamentos VALUES(2,2,1,'2026-10-21','10:00','confirmado',4000,50,0);
        ''')

    def execute(self, sql, params=()):
        sql = sql.replace('%s', '?').replace(' FOR UPDATE', '').replace('now()', 'CURRENT_TIMESTAMP')
        return self.db.execute(sql, params or ())

    def commit(self): self.db.commit()
    def close(self): pass


@pytest.fixture
def banco(monkeypatch):
    b = Banco()
    monkeypatch.setattr(agenda, 'get_connection', lambda: b)
    # Os nomes ainda não existem antes da implementação (a rota é testada primeiro).
    monkeypatch.setattr(agenda, 'horario_efetivo', lambda *args: ('09:00', '20:00'), raising=False)
    monkeypatch.setattr(agenda, 'data_hoje', lambda: date(2026,10,19), raising=False)
    yield b
    b.db.close()


def alterar(dados, papel='barbeiro', barbeiro_id=2, id=1):
    return app.app.test_client().patch(f'/api/admin/agendamentos/{id}', json=dados,
        headers=cabecalho_admin(papel, barbeiro_id))


def test_reduzir_libera_trinta_minutos_e_registra_auditoria(banco):
    r = alterar({'preco': '20,00', 'duracao_min': 20})
    assert r.status_code == 200, r.json
    row = banco.execute('SELECT * FROM agendamentos WHERE id=1').fetchone()
    assert (row['valor_centavos'], row['duracao_min'], row['preco_personalizado']) == (2000,20,1)
    from agendamentos import janelas_ocupadas
    from horarios import gerar_slots
    ocupadas, _ = janelas_ocupadas(banco, '2026-10-21', 2, 30)
    assert '09:20' in gerar_slots('09:00', '20:00', ocupadas, 30)
    audit = banco.execute('SELECT * FROM agendamento_alteracoes').fetchone()
    assert (audit['valor_anterior_centavos'], audit['valor_novo_centavos'],
            audit['duracao_anterior_min'], audit['duracao_nova_min']) == (4000,2000,50,20)
    assert banco.execute('SELECT valor_centavos FROM agendamentos WHERE id=2').fetchone()[0] == 4000


@pytest.mark.parametrize('papel,barbeiro', [('barbeiro',3), ('salao',None)])
def test_nao_edita_agendamento_fora_do_escopo(banco, papel, barbeiro):
    assert alterar({'preco':20,'duracao_min':20}, papel, barbeiro).status_code in (403,404)
    assert banco.execute('SELECT valor_centavos FROM agendamentos WHERE id=1').fetchone()[0] == 4000


def test_master_pode_editar(banco):
    assert alterar({'preco':25,'duracao_min':20}, 'master', None).status_code == 200


def test_aumento_nao_invade_proximo_cliente(banco):
    assert alterar({'preco':20,'duracao_min':80}).status_code == 409
    assert banco.execute('SELECT duracao_min FROM agendamentos WHERE id=1').fetchone()[0] == 50


def test_aumento_nao_invade_bloqueio(banco):
    banco.execute("INSERT INTO bloqueios VALUES('2026-10-21',2,'09:50',10,'Pausa')")
    assert alterar({'preco':40,'duracao_min':60}).status_code == 409


@pytest.mark.parametrize('preco,duracao', [('NaN',20),('Infinity',20),(-1,20),('20.001',20),(20,0),(20,481),(20,20.5),(20,True)])
def test_rejeita_valores_invalidos(banco, preco, duracao):
    assert alterar({'preco':preco,'duracao_min':duracao}).status_code == 400


def test_cancelado_nao_pode_ser_editado(banco):
    banco.execute("UPDATE agendamentos SET status='cancelado' WHERE id=1")
    assert alterar({'preco':20,'duracao_min':20}).status_code == 409


def test_passado_nao_pode_ser_editado(banco):
    banco.execute("UPDATE agendamentos SET data='2026-10-18' WHERE id=1")
    assert alterar({'preco':20,'duracao_min':20}).status_code == 409


def test_editar_so_duracao_nao_cria_desconto_personalizado(banco):
    assert alterar({'duracao_min':20}).status_code == 200
    row = banco.execute('SELECT * FROM agendamentos WHERE id=1').fetchone()
    assert row['valor_centavos'] == 4000 and not row['preco_personalizado']


def test_fechamento_usa_valor_editado_e_comissao_do_atendimento(banco, monkeypatch):
    import relatorios
    banco.db.executescript('''
        ALTER TABLE barbeiros ADD COLUMN nome TEXT DEFAULT 'Rian';
        ALTER TABLE barbeiros ADD COLUMN comissao_pct INTEGER DEFAULT 80;
        ALTER TABLE servicos ADD COLUMN nome TEXT DEFAULT 'Corte';
        ALTER TABLE agendamentos ADD COLUMN comissao_pct INTEGER DEFAULT 60;
        CREATE TABLE consumos(agendamento_id INTEGER, quantidade INTEGER, valor_centavos INTEGER);
    ''')
    assert alterar({'preco':20,'duracao_min':20}).status_code == 200
    monkeypatch.setattr(relatorios, 'get_connection', lambda: banco)
    r = app.app.test_client().get('/api/admin/contagem?data=2026-10-21',headers=cabecalho_admin())
    assert r.status_code == 200, r.json
    assert r.json['totais']['total'] == 60
    assert r.json['totais']['barbeiro_recebe'] == 36
    assert r.json['totais']['barbearia_recebe'] == 24


def test_relatorio_publico_nao_expoe_faturamento():
    assert app.app.test_client().get('/api/relatorio').status_code == 404
