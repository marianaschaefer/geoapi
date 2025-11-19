from dotenv import load_dotenv
from flask import Flask

# Carrega variáveis do .env (opcional)
load_dotenv(override=True)

# Cria o app
app = Flask(__name__)

# Importa as rotas (depois de criar o app)
from app import routes

