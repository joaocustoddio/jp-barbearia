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
