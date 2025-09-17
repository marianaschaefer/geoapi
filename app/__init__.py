from dotenv import load_dotenv
load_dotenv()  # carrega .env da raiz do projeto

from flask import Flask
app = Flask(__name__)

from app import routes  # importa as rotas depois de carregar o .env