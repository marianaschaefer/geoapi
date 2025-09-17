from app import app
import geopandas as gpd
import os
from flask import jsonify, render_template, send_from_directory, request, Response

from services.segmentation import processar_segmentacao_completa
from services.samples import (
    upsert_samples_from_features,
    upsert_samples_from_ids,
    list_samples,
    delete_sample,
)

# ---------- PÁGINAS ----------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/classification")
def resultado():
    return render_template("classification.html")


# ---------- SEGMENTAÇÃO ----------
@app.route("/segmentar", methods=["POST"])
def segmentar():
    try:
        data = request.get_json()
        bbox = data.get("bbox")
        if not bbox or len(bbox) != 4:
            return jsonify({"status": "erro", "mensagem": "BBox inválido ou não enviado"}), 400

        output_dir = "./SENTINEL2_BANDAS"
        resultado = processar_segmentacao_completa(output_dir=output_dir, bbox=bbox)
        return jsonify({"status": "sucesso", "mensagem": resultado})
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500


@app.route("/resultado_geojson")
def resultado_geojson():
    base = "./SENTINEL2_BANDAS/segments_slic_compactness05_step200"
    candidatos = [base + ".geojson", base + ".shp"]
    for path in candidatos:
        if os.path.exists(path):
            gdf = gpd.read_file(path)
            return Response(gdf.to_json(), mimetype="application/json")
    return jsonify({"erro": "Camada não encontrada"}), 404


@app.route("/bandas/<nome>")
def servir_banda(nome):
    # proteção simples contra path traversal
    if ".." in nome or nome.startswith("/"):
        return "Nome inválido", 400

    pasta_absoluta = os.path.abspath("./SENTINEL2_BANDAS")
    alvo = os.path.join(pasta_absoluta, nome)
    if os.path.exists(alvo):
        return send_from_directory(pasta_absoluta, nome)
    return "Arquivo não encontrado", 404


# ---------- SALVAR CLASSIFICAÇÃO (do frontend) ----------
@app.route("/salvar_classificacao", methods=["POST"])
def salvar_classificacao():
    """
    Salva o GeoJSON que o usuário rotulou no frontend.
    Define CRS usando a camada de segmentos para evitar o warning.
    """
    data = request.get_json(silent=True) or {}
    feats = data.get("features")
    if not feats or not isinstance(feats, list):
        return jsonify({"status": "erro", "mensagem": "Envie 'features' (GeoJSON) no corpo da requisição."}), 400

    # tenta herdar CRS da camada de segmentos
    seg_base = "./SENTINEL2_BANDAS/segments_slic_compactness05_step200"
    seg_path = seg_base + ".geojson" if os.path.exists(seg_base + ".geojson") else seg_base + ".shp"
    crs = None
    if os.path.exists(seg_path):
        try:
            seg = gpd.read_file(seg_path)
            crs = seg.crs
        except Exception:
            pass
    if crs is None:
        crs = "EPSG:4326"

    gdf = gpd.GeoDataFrame.from_features(feats, crs=crs)

    path_out = "./SENTINEL2_BANDAS/classificado.geojson"
    os.makedirs(os.path.dirname(path_out), exist_ok=True)
    gdf.to_file(path_out, driver="GeoJSON")

    return jsonify({"status": "ok", "path": path_out})


# ---------- AMOSTRAS (CRUD mínimo) ----------
@app.route("/amostras", methods=["GET"])
def amostras_listar():
    """
    Lista amostras.
    Query params opcionais:
      - classe=NomeDaClasse
      - bbox=minx,miny,maxx,maxy
    """
    classe = request.args.get("classe")
    bbox_str = request.args.get("bbox")
    bbox = None
    if bbox_str:
        try:
            bbox = [float(x) for x in bbox_str.split(",")]
        except Exception:
            return jsonify({"status": "erro", "mensagem": "bbox inválido (use minx,miny,maxx,maxy)"}), 400

    gdf = list_samples(classe=classe, bbox=bbox)
    return Response(gdf.to_json(), mimetype="application/json")


@app.route("/amostras/add", methods=["POST"])
def amostras_add():
    """
    Adiciona/atualiza amostras (UPSERT).
    Formatos aceitos:

    1) GeoJSON:
      { "features": [ { "type":"Feature", "properties":{ "segment_id":123, "classe":"Urbanizado", "usuario":"maria" }, "geometry": {...} }, ... ] }

    2) Lista simples:
      { "amostras": [ { "segment_id":123, "classe":"Urbanizado", "usuario":"maria" }, ... ] }
    """
    data = request.get_json(silent=True) or {}
    try:
        if "features" in data:
            created, updated = upsert_samples_from_features(data["features"])
        elif "amostras" in data:
            created, updated = upsert_samples_from_ids(data["amostras"])
        else:
            return jsonify({"status": "erro", "mensagem": "Envie 'features' (GeoJSON) ou 'amostras' (lista)."}), 400

        return jsonify({"status": "sucesso", "criadas": created, "atualizadas": updated}), 200
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500


@app.route("/amostras/<int:segment_id>", methods=["DELETE"])
def amostras_delete(segment_id: int):
    ok = delete_sample(segment_id)
    if ok:
        return jsonify({"status": "sucesso", "removidas": 1}), 200
    return jsonify({"status": "sucesso", "removidas": 0}), 200
