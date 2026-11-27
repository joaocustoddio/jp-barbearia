"""
Configuração dos testes.

Seta APP_SKIP_BOOT=1 ANTES de importar o app, pra o módulo carregar sem tentar
conectar no banco (o boot init_db/seeds fica pulado). Também põe o diretório
`back/` no path pra o `import app` funcionar rodando pytest de qualquer lugar.
"""
import os
import sys

os.environ.setdefault("APP_SKIP_BOOT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usada-em-producao-000000")
os.environ.setdefault("FLASK_ENV", "development")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def sem_dias_de_fila(monkeypatch):
    """As conexões falsas dos testes devolvem linhas pra qualquer consulta, o que
    faria todo dia parecer dia de fila. Fora dos testes da fila, nenhum dia é."""
    import fila_dados
    monkeypatch.setattr(fila_dados, "dia_de_fila", lambda conn, data: False)
    monkeypatch.setattr(fila_dados, "dias_de_fila", lambda conn, inicio, fim: set())
