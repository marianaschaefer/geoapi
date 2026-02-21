import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv

# Inicializa as extensões fora da factory
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login' # Define para onde mandar o usuário não logado
login_manager.login_message = "Por favor, faça login para acessar esta página."

def create_app():
    # Carrega variáveis do .env (como você já fazia)
    load_dotenv(override=True)

    app = Flask(__name__)
    
    # Configurações básicas (podem ser movidas para o config.py depois)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'chave-secreta-para-testes')
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'geoapi.db')

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Inicializa as extensões no app
    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        # Importa rotas e modelos dentro do contexto
        from app import routes, models
        
        # Cria as tabelas do banco de dados automaticamente se não existirem
        db.create_all()

    return app