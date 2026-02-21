# services/ibge.py
import re
import requests
import unicodedata
from shapely.geometry import shape
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def norm_txt(txt: str) -> str:
    if not txt:
        return ""
    txt = str(txt).strip().lower()
    txt = ''.join(
        c for c in unicodedata.normalize('NFD', txt)
        if unicodedata.category(c) != 'Mn'
    )
    # remove pontuação e normaliza espaços
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def _as_list(json_data):
    # Alguns endpoints retornam lista direta; outros podem encapsular em chave
    if isinstance(json_data, list):
        return json_data
    if isinstance(json_data, dict):
        # tentativas comuns
        for k in ("items", "result", "results", "data"):
            v = json_data.get(k)
            if isinstance(v, list):
                return v
    return None

def buscar_geometria_ibge(tipo, identificador):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    }

    # importante: não mascarar SSL sem motivo
    # se seu WSL tiver problema de CA, corrija no sistema (ca-certificates)
    verify_ssl = True

    try:
        search_url = f"https://servicodados.ibge.gov.br/api/v1/localidades/{tipo}"
        res = requests.get(search_url, headers=headers, timeout=20, verify=verify_ssl)

        print(f"[IBGE] GET {search_url} -> {res.status_code}")

        if res.status_code != 200:
            print(f"[ERRO IBGE] HTTP {res.status_code} | body[:200]={res.text[:200]!r}")
            return None

        raw = res.json()
        data = _as_list(raw)

        if data is None:
            print(f"[ERRO IBGE] JSON não é lista. type={type(raw)} keys={list(raw.keys()) if isinstance(raw, dict) else '—'}")
            return None

        id_busca = norm_txt(identificador)
        print(f"[IBGE] procurando: '{identificador}' -> norm='{id_busca}' | total_itens={len(data)}")

        # match exato
        item = next((x for x in data if norm_txt(x.get("nome")) == id_busca), None)

        # fallback: contains (ex.: “sao joao d alianca” vs “sao joao d’alianca”)
        if not item:
            candidatos = [x for x in data if id_busca and id_busca in norm_txt(x.get("nome"))]
            if len(candidatos) == 1:
                item = candidatos[0]
            elif len(candidatos) > 1:
                # pega o mais curto (heurística simples) e loga ambiguidade
                candidatos.sort(key=lambda x: len(norm_txt(x.get("nome"))))
                print(f"[IBGE] ambíguo: {len(candidatos)} candidatos. Ex.: {[c.get('nome') for c in candidatos[:5]]}")
                item = candidatos[0]

        if not item:
            # log de amostra para entender o que está vindo
            sample = [x.get("nome") for x in data[:10]]
            print(f"[IBGE] NÃO ACHOU '{identificador}'. Amostra dos 10 primeiros nomes: {sample}")
            return None

        ibge_id = item.get("id")
        if ibge_id is None:
            print(f"[ERRO IBGE] item sem 'id': {item}")
            return None

        geojson_url = (
            f"https://servicodados.ibge.gov.br/api/v3/malhas/{tipo}/{ibge_id}"
            f"?formato=application/vnd.geo+json&qualidade=intermediaria"
        )
        res_geo = requests.get(geojson_url, headers=headers, timeout=20, verify=verify_ssl)
        print(f"[IBGE] GET {geojson_url} -> {res_geo.status_code}")

        if res_geo.status_code != 200:
            print(f"[ERRO IBGE] malha HTTP {res_geo.status_code} | body[:200]={res_geo.text[:200]!r}")
            return None

        geojson = res_geo.json()
        features = geojson.get("features", [])
        geom_data = features[0]["geometry"] if features else geojson.get("geometry")

        if not geom_data:
            print(f"[AVISO] IBGE não retornou geometria válida para {item.get('nome')}")
            return None

        geom = shape(geom_data)
        print(f"--- [SUCESSO] Geometria encontrada: {item.get('nome')} (id={ibge_id}) ---")

        return {
            "nome": item.get("nome"),
            "id": ibge_id,
            "geojson": geojson,
            "bbox": list(geom.bounds),
        }

    except Exception as e:
        print(f"[ERRO CRÍTICO NO SERVIÇO IBGE] {type(e).__name__}: {e}")
        return None
