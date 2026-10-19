from datetime import date, datetime, timezone
import pytest
import app
import rotas_admin_gestao as gestao
from test_alterar_agendamento import Banco
from test_criar_cancelar import cabecalho_admin


@pytest.fixture
def banco(monkeypatch):
    b = Banco()
    b.db.executescript('''
      ALTER TABLE servicos ADD COLUMN nome TEXT DEFAULT 'Corte';
      ALTER TABLE servicos ADD COLUMN ativo INTEGER DEFAULT 1;
      ALTER TABLE servicos ADD COLUMN imagem TEXT;
      CREATE TABLE servico_vigencias(id INTEGER PRIMARY KEY, servico_id INTEGER, inicio TEXT,
        valor_centavos INTEGER, duracao_min INTEGER, admin_id INTEGER, UNIQUE(servico_id,inicio));
      UPDATE agendamentos SET data='2026-11-03';
      INSERT INTO agendamentos VALUES(3,2,1,'2026-10-01','09:00','confirmado',4000,50,0);
      INSERT INTO agendamentos VALUES(4,2,1,'2026-11-03','11:00','confirmado',2000,50,1);
    ''')
    monkeypatch.setattr(gestao, 'get_connection', lambda: b)
    monkeypatch.setattr(gestao, 'data_hoje', lambda: date(2026,10,19))
    monkeypatch.setattr(gestao, 'agora_br', lambda: datetime(2026,10,19,12,tzinfo=timezone.utc), raising=False)
    yield b
    b.db.close()


def reajustar(preco=60, inicio='2026-11-01', duracao=40):
    return app.app.test_client().put('/api/admin/servicos/1',
        json={'preco':preco,'duracao_min':duracao,'inicio_vigencia':inicio},
        headers=cabecalho_admin())


def test_reajuste_futuro_preserva_passado_desconto_e_duracao_reservada(banco):
    r = reajustar()
    assert r.status_code == 200, r.json
    valores = [tuple(r) for r in banco.execute('SELECT valor_centavos,duracao_min FROM agendamentos ORDER BY id').fetchall()]
    assert valores == [(6000,50),(6000,50),(4000,50),(2000,50)]
    assert banco.execute('SELECT preco FROM servicos').fetchone()[0] == 40
    assert banco.execute('SELECT COUNT(*) FROM agendamento_alteracoes').fetchone()[0] == 2


def test_catalogo_resolve_preco_e_duracao_na_data(banco):
    assert reajustar().status_code == 200
    from precos import servico_na_data
    antes = servico_na_data(banco,1,'2026-10-31')
    depois = servico_na_data(banco,1,'2026-11-01')
    assert (antes['valor_centavos'],antes['duracao_min']) == (4000,50)
    assert (depois['valor_centavos'],depois['duracao_min']) == (6000,40)


def test_reajuste_intermediario_preserva_vigencia_posterior(banco):
    assert reajustar(70,'2026-11-02').status_code == 200
    assert reajustar(60,'2026-11-01').status_code == 200
    assert banco.execute('SELECT valor_centavos FROM agendamentos WHERE id=1').fetchone()[0] == 7000


@pytest.mark.parametrize('preco,inicio', [('NaN','2026-11-01'),('Infinity','2026-11-01'),(60,'2026-10-18'),(60,''),(60,'invalid')])
def test_reajuste_invalido_nao_muda_dados(banco,preco,inicio):
    assert reajustar(preco,inicio).status_code == 400
    assert banco.execute('SELECT COUNT(*) FROM servico_vigencias').fetchone()[0] == 0


def test_so_master_pode_reajustar_catalogo(banco):
    r = app.app.test_client().put('/api/admin/servicos/1',json={'preco':60,'duracao_min':40,'inicio_vigencia':'2026-11-01'},headers=cabecalho_admin('barbeiro',2))
    assert r.status_code == 403


def test_disponibilidade_periodo_considera_mudanca_de_duracao(banco, monkeypatch):
    import validacoes
    banco.execute('CREATE TABLE expedientes(barbeiro_id INTEGER, data TEXT, inicio TEXT, fim TEXT)')
    banco.execute('DELETE FROM agendamentos')
    assert reajustar(60,'2026-10-21',20).status_code == 200
    monkeypatch.setattr(app, 'get_connection', lambda: banco)
    monkeypatch.setattr(app, 'horario_efetivo', lambda *a: ('09:00','20:00'))
    monkeypatch.setattr(app, 'data_hoje', lambda: date(2026,10,19))
    monkeypatch.setattr(validacoes, 'data_hoje', lambda: date(2026,10,19))
    c = app.app.test_client()
    periodo = c.get('/api/horarios-disponiveis-periodo?inicio=2026-10-20&dias=2&barbeiro_id=2&servico_id=1')
    assert periodo.status_code == 200, periodo.json
    for data, segundo in [('2026-10-20','09:50'),('2026-10-21','09:20')]:
        dia = c.get(f'/api/horarios-disponiveis?data={data}&barbeiro_id=2&servico_id=1')
        assert dia.status_code == 200, dia.json
        assert dia.json['horarios_disponiveis'][1] == segundo
        assert dia.json['horarios_disponiveis'] == periodo.json['dias'][data]['horarios_disponiveis']


def test_catalogo_publico_mostra_preco_da_data(banco,monkeypatch):
    import rotas_publicas
    monkeypatch.setattr(rotas_publicas,'get_connection',lambda:banco)
    assert reajustar().status_code == 200
    c = app.app.test_client()
    assert c.get('/api/servicos?data=2026-10-31').json[0]['preco'] == 40
    assert c.get('/api/servicos?data=2026-11-01').json[0]['preco'] == 60


def test_validacao_recusa_servico_desativado_existente(banco):
    from validacoes import validar_servico_id
    banco.execute('UPDATE servicos SET ativo=0 WHERE id=1')
    assert validar_servico_id(1,banco)[0] is False
