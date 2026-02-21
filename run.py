from app import create_app

# Chama a função factory para instanciar o app
app = create_app()

if __name__ == "__main__":
    # Mantendo suas configurações de host e port
    app.run(host="0.0.0.0", port=5000, debug=True)