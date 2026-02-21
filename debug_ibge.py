import requests
import certifi

def testar():
    url = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
    print("--- TESTE DE CONEXÃO ---")
    try:
        # Tentativa 1: Com SSL
        r = requests.get(url, timeout=10, verify=certifi.where())
        print(f"Tentativa 1 (SSL): {r.status_code}")
    except Exception as e:
        print(f"Tentativa 1 falhou: {e}")
        
    try:
        # Tentativa 2: Sem SSL (O que deve resolver no seu Linux)
        r = requests.get(url, timeout=10, verify=False)
        print(f"Tentativa 2 (Sem SSL): {r.status_code}")
        if r.status_code == 200:
            print(f"Sucesso! Encontrados {len(r.json())} municípios.")
    except Exception as e:
        print(f"Tentativa 2 falhou: {e}")

testar()