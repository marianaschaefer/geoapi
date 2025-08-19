import os
from datetime import datetime, timedelta
import numpy as np
import matplotlib.pyplot as plt
import rasterio
from rasterio.transform import from_origin
from sentinelhub import (
    SHConfig, BBox, CRS, SentinelHubRequest,
    DataCollection, MimeType, bbox_to_dimensions
)
from skimage.segmentation import slic, mark_boundaries
from skimage.util import img_as_float
from rasterio.transform import from_bounds
from rasterio.features import shapes
from shapely.geometry import shape
import geopandas as gpd
from skimage.segmentation import slic
from skimage.util import img_as_float

# === CONFIGURAÇÕES GERAIS ===
BANDAS_TODAS = ['B01', 'B02', 'B03', 'B04', 'B05', 'B06',
                'B07', 'B08', 'B8A', 'B09', 'B11', 'B12']
BANDAS_RGB = ['B04', 'B03', 'B02'] 
OUTPUT_DIR = './SENTINEL2_BANDAS'


# === BAIXAR BANDAS ===
def baixar_bandas_sentinel(output_dir=OUTPUT_DIR, dias=60, resolucao=30, bbox=None):
    config = SHConfig()
    config.sh_client_id = '47a99045-2354-4408-aaa3-d8066ef4650d'
    config.sh_client_secret = 'AY9DOuGxFcwaH969RFCqIdKPAZZZqGm5'

    hoje = datetime.utcnow().date()

    if bbox:
        aoi = BBox(bbox=bbox, crs=CRS.WGS84)
    else:
        aoi = BBox(bbox=[-44.0, -21.5, -43.4, -20.9], crs=CRS.WGS84)

    width, height = bbox_to_dimensions(aoi, resolution=resolucao)

    os.makedirs(output_dir, exist_ok=True)

    for banda in BANDAS_TODAS:
        request = SentinelHubRequest(
            evalscript=f"""
            //VERSION=3
            function setup() {{
                return {{
                    input: ["{banda}"],
                    output: {{
                        bands: 1,
                        sampleType: "UINT16"
                    }}
                }};
            }}
            function evaluatePixel(sample) {{
                return [sample.{banda} * 10000];
            }}
            """,
            input_data=[{
                'type': DataCollection.SENTINEL2_L1C.api_id,
                'dataFilter': {
                    'timeRange': {
                        'from': f'{hoje - timedelta(days=dias)}T00:00:00Z',
                        'to': f'{hoje}T23:59:59Z'
                    },
                    'mosaickingOrder': 'leastCC',
                    'maxCloudCoverage': 30
                }
            }],
            responses=[{
                'identifier': 'default',
                'format': {'type': MimeType.TIFF.get_string()}
            }],
            bbox=aoi,
            size=(width, height),
            config=config
        )

        image = request.get_data(save_data=False)[0]
        if image.ndim == 2:
            image = np.expand_dims(image, axis=0)

        _, h, w = image.shape

        # Aqui o transform correto usando from_bounds
        transform = from_bounds(
            aoi.min_x, aoi.min_y, aoi.max_x, aoi.max_y,
            w, h
        )

        path_out = os.path.join(output_dir, f'{banda}.tif')

        with rasterio.open(
            path_out, 'w',
            driver='GTiff',
            height=h,
            width=w,
            count=1,
            dtype='uint16',
            crs=aoi.crs.pyproj_crs(),
            transform=transform
        ) as dst:
            dst.write(image[0], 1)

    return f"✅ Bandas salvas com bbox {bbox}"


# === CRIAR MULTIBANDA ===
def criar_multibanda(bandas, output_dir, output_path):
    arrays = []
    for banda in bandas:
        path = os.path.join(output_dir, f'{banda}.tif')
        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float32)
            arrays.append(data)
            if 'profile' not in locals():
                profile = src.profile
                profile.update({
                    'count': len(bandas),
                    'dtype': 'float32',
                    'compress': 'lzw'
                })

    stack = np.stack(arrays)
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack)

    print(f"📦 Raster multibanda salvo em: {output_path}")


# === CRIAR COMPOSIÇÃO RGB 8 BITS PARA VISUALIZAÇÃO ===
def criar_rgb_8bit(bandas_rgb, output_dir, output_path):
    arrays = []
    for banda in bandas_rgb:
        path = os.path.join(output_dir, f'{banda}.tif')
        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float32)
            # Normaliza entre 0-1 para 8bit
            data_norm = (data - data.min()) / (data.max() - data.min())
            arrays.append(data_norm)
            if 'profile' not in locals():
                profile = src.profile
                profile.update({
                    'count': len(bandas_rgb),
                    'dtype': 'uint8',
                    'compress': 'lzw'
                })

    stack = np.stack(arrays)
    stack_8bit = (stack * 255).astype(np.uint8)

    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack_8bit)

    print(f"📸 RGB 8 bits salvo em: {output_path}")


# === SEGMENTAÇÃO COM SLIC (skimage) ===
def aplicar_segmentacao_multibanda(image_path, output_dir, compactness=5, step=200, output_filename='segments_slic.shp'):

    # === Ler raster multibanda
    with rasterio.open(image_path) as src:
        img = src.read().astype(np.float32)
        transform = src.transform
        crs = src.crs

    # Rearranjar e normalizar imagem
    img = np.moveaxis(img, 0, -1)
    img = img_as_float(img)

    # === Aplicar SLIC
    n_segments = max(100, int(img.shape[0] * img.shape[1] / step))
    segments = slic(img, n_segments=n_segments, compactness=compactness, start_label=1)

    # === Converter segmentação para vetor (shapefile)
    print("🔄 Convertendo segmentação para shapefile...")
    mask = segments > 0
    shapes_gen = shapes(segments.astype(np.uint16), mask=mask, transform=transform)
    geoms = [
        {"geometry": shape(geom), "properties": {"segment_id": int(value)}}
        for geom, value in shapes_gen
    ]

    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)

    # Filtrar geometrias válidas e não vazias (sem reprojetar)
    gdf = gdf[gdf.geometry.is_valid & gdf.geometry.notna()]
    gdf = gdf[~gdf.geometry.is_empty]

    # ⚠️ Não calcular área e nem reprojetar por enquanto
    # Apenas salvar e plotar
    shapefile_path = os.path.join(output_dir, output_filename)
    gdf.to_file(shapefile_path)
    print(f"✅ Shapefile salvo com {len(gdf)} polígonos: {shapefile_path}")

    # === Visualizar shapefile sobre RGB
    rgb_path = os.path.join(output_dir, 'RGB_composicao_8bit.tif')
    if os.path.exists(rgb_path):
        print("🖼️ Plotando shapefile sobre imagem RGB...")
        with rasterio.open(rgb_path) as rgb:
            fig, ax = plt.subplots(figsize=(12, 12))
            rgb_data = rgb.read()
            rgb_img = np.moveaxis(rgb_data, 0, -1).astype(np.float32) / 255.0

            ax.imshow(rgb_img)
            gdf.plot(ax=ax, facecolor='none', edgecolor='yellow', linewidth=0.5, aspect=None)

            plt.title("Segmentação SLIC - Vetorial sobre RGB", fontsize=14)
            plt.axis('off')
            # plt.show()
    else:
        print("⚠️ RGB não encontrado.")


# === EXECUÇÃO COMPLETA ===
def processar_segmentacao_completa(output_dir=OUTPUT_DIR, bbox=None):
    print("🔍 Iniciando com BBox:", bbox)

    print(baixar_bandas_sentinel(output_dir=output_dir, bbox=bbox))

    path_multibanda = os.path.join(output_dir, 'sentinel_multibanda.tif')
    criar_multibanda(BANDAS_RGB, output_dir, path_multibanda)

    path_rgb_8bit = os.path.join(output_dir, 'RGB_composicao_8bit.tif')
    criar_rgb_8bit(BANDAS_RGB, output_dir, path_rgb_8bit)

    aplicar_segmentacao_multibanda(
        image_path=path_multibanda,
        output_dir=output_dir,
        compactness=5,
        step=200,
        output_filename='segments_slic_compactness05_step200.shp'
    )

    return f"✅ Segmentação finalizada com bbox: {bbox}"


# Para rodar tudo:
if __name__ == '__main__':
    print(processar_segmentacao_completa())