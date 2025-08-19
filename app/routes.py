from app import app
import geopandas as gpd
import os
from flask import jsonify, render_template, send_from_directory, request

from services.segmentation import processar_segmentacao_completa


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/segmentar', methods=['POST'])
def segmentar():
    try:
        # Recebe o JSON enviado pelo frontend
        data = request.get_json()

        # Verifica e extrai o bbox
        bbox = data.get('bbox')  # Esperado: [minLng, minLat, maxLng, maxLat]

        if not bbox or len(bbox) != 4:
            return jsonify({'status': 'erro', 'mensagem': 'BBox inválido ou não enviado'}), 400

        # Chama a função de segmentação passando o bbox
        output_dir = './SENTINEL2_BANDAS'
        resultado = processar_segmentacao_completa(output_dir=output_dir, bbox=bbox)

        return jsonify({'status': 'sucesso', 'mensagem': resultado})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
    
    
@app.route('/classification')
def resultado():
    return render_template('classification.html')


@app.route('/resultado_geojson')
def resultado_geojson():
    shapefile_path = './SENTINEL2_BANDAS/segments_slic_compactness05_step200.shp'
    
    if not os.path.exists(shapefile_path):
        return jsonify({'erro': 'Shapefile não encontrado'}), 404

    gdf = gpd.read_file(shapefile_path)
    return gdf.to_json()

@app.route('/bandas/<nome>')
def servir_banda(nome):
    pasta_absoluta = os.path.abspath('./SENTINEL2_BANDAS')
    if os.path.exists(os.path.join(pasta_absoluta, nome)):
        return send_from_directory(pasta_absoluta, nome)
    return 'Arquivo não encontrado', 404

@app.route("/salvar_classificacao", methods=["POST"])
def salvar_classificacao():
    data = request.get_json()
    path_out = "./SENTINEL2_BANDAS/classificado.geojson"

    gdf = gpd.GeoDataFrame.from_features(data["features"])
    gdf.to_file(path_out, driver="GeoJSON")

    return jsonify({"status": "ok", "path": path_out})

