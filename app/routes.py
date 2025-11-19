from app import app
import os
import glob
import geopandas as gpd

from flask import (
    jsonify, render_template, send_from_directory,
    request, Response
)

# ---- serviços ----
from services.segmentation import processar_segmentacao_completa
from services.features import build_features, read_features
from services.samples import (
    upsert_samples_from_features,
    upsert_samples_from_ids,
    list_samples,
    delete_sample,
)
from services.propagation import propagate_labels


# ============== PÁGINAS ==============
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/classification")
def resultado():
    return render_template("classification.html")


# ============== SEGMENTAÇÃO + FEATURES ==============
@app.route("/segmentar", methods=["POST"])          # legado
@app.route("/api/segmentar", methods=["POST"])      # novo
def api_segmentar():
    try:
        data = request.get_json(silent=True) or {}

        bbox           = data.get("bbox")
        dias           = int(data.get("dias", 180))
        resolucao      = int(data.get("resolucao", 10))
        max_cloud      = int(data.get("cloud_cover_max", data.get("max_cloud", 30)))

        data_inicio    = data.get("data_inicio") or None
        data_fim       = data.get("data_fim") or None
        composicao     = data.get("composicao", "TODAS")

        usar_ndvi       = bool(data.get("usar_ndvi", data.get("usar_ndvi_no_slic", False)))
        usar_n_segments = bool(data.get("usar_n_segments", False))
        n_segments      = int(data.get("n_segments", 2000))

        region_px      = int(data.get("region_px", 30))
        compactness    = float(data.get("compactness", 1.0))
        sigma          = float(data.get("sigma", 1.0))

        if not bbox or len(bbox) != 4:
            return jsonify({"status": "erro", "mensagem": "BBox inválido ou ausente."}), 400

        out_dir = "./SENTINEL2_BANDAS"

        try:
            seg_msg = processar_segmentacao_completa(
                output_dir=out_dir,
                bbox=bbox,
                dias=dias,
                resolucao=resolucao,
                max_cloud=max_cloud,
                data_inicio=data_inicio,
                data_fim=data_fim,
                composicao=composicao,
                usar_ndvi=usar_ndvi,
                usar_n_segments=usar_n_segments,
                n_segments=n_segments,
                region_px=region_px,
                compactness=compactness,
                sigma=sigma,
            )
        except TypeError:
            seg_msg = processar_segmentacao_completa(output_dir=out_dir, bbox=bbox)

        feats_path = build_features(save_csv=False)

        return jsonify({
            "status": "sucesso",
            "mensagem": "Segmentação concluída e features geradas.",
            "segmentation": seg_msg,
            "features_path": feats_path,
            "params": {
                "bbox": bbox,
                "dias": dias,
                "resolucao": resolucao,
                "max_cloud": max_cloud,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "usar_ndvi": usar_ndvi,
                "usar_n_segments": usar_n_segments,
                "n_segments": n_segments,
                "region_px": region_px,
                "compactness": compactness,
                "sigma": sigma,
            }
        }), 200

    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500


# ============== RESULTADO GEOJSON (segmentos) ==============
@app.route("/resultado_geojson")
def resultado_geojson():
    base = "./SENTINEL2_BANDAS/segments_slic_compactness05_step200"
    candidatos = [base + ".geojson", base + ".shp"]
    for path in candidatos:
        if os.path.exists(path):
            gdf = gpd.read_file(path)
            return Response(gdf.to_json(), mimetype="application/json")
    return jsonify({"erro": "Camada não encontrada"}), 404


# ============== BANDAS (serve arquivos) ==============
@app.route("/bandas/<nome>")
def servir_banda(nome: str):
    if ".." in nome or nome.startswith("/"):
        return "Nome inválido", 400
    pasta_absoluta = os.path.abspath("./SENTINEL2_BANDAS")
    alvo = os.path.join(pasta_absoluta, nome)
    if os.path.exists(alvo):
        return send_from_directory(pasta_absoluta, nome)
    return "Arquivo não encontrado", 404


# ============== SALVAR CLASSIFICAÇÃO (rotulação no front) ==============
@app.route("/salvar_classificacao", methods=["POST"])
def salvar_classificacao():
    data = request.get_json(silent=True) or {}
    feats = data.get("features")
    if not feats or not isinstance(feats, list):
        return jsonify({"status": "erro", "mensagem": "Envie 'features' (GeoJSON) no corpo da requisição."}), 400

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


# ============== AMOSTRAS (CRUD) ==============
@app.route("/amostras", methods=["GET"])
def amostras_listar():
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


# ============== FEATURES (build/list) ==============
@app.route("/api/features/build", methods=["POST"])
def api_features_build():
    try:
        path = build_features(save_csv=False)
        return jsonify({"status": "sucesso", "path": path}), 200
    except Exception as e:
        return jsonify({"status":"erro", "mensagem": str(e)}), 500


@app.route("/features", methods=["GET"])
def features_list():
    try:
        segment_id = request.args.get("segment_id", type=int)
        limit = request.args.get("limit", default=50, type=int)
        df = read_features(limit=limit, segment_id=segment_id)
        return Response(df.to_json(orient="records"), mimetype="application/json")
    except Exception as e:
        return jsonify({"status":"erro", "mensagem": str(e)}), 500


# ============== PROPAGAÇÃO ==============
@app.route("/api/propagate", methods=["POST"])
def api_propagate():
    try:
        data = request.get_json(silent=True) or {}
        method = (data.get("method") or "label_spreading").strip().lower()
        params = data.get("params") or {}
        result = propagate_labels(method=method, params=params)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500


# ============== RESULTADO PROPAGADO (NOVO) ==============
def _latest_propagado():
    files = sorted(glob.glob("./SENTINEL2_BANDAS/propagado_*.geojson"))
    return files[-1] if files else None

@app.route("/resultado_propagado")
def resultado_propagado():
    """Retorna o último GeoJSON propagado (200) ou 404 se não existir."""
    path = _latest_propagado()
    if not path or not os.path.exists(path):
        return jsonify({"erro": "Nenhum resultado propagado encontrado"}), 404
    gdf = gpd.read_file(path)
    return Response(gdf.to_json(), mimetype="application/json")

@app.route("/resultado_propagado/info")
def resultado_propagado_info():
    """Retorna metadados simples do arquivo propagado mais recente."""
    path = _latest_propagado()
    if not path or not os.path.exists(path):
        return jsonify({"existe": False}), 200
    return jsonify({"existe": True, "path": path}), 200
