import os
from datetime import datetime, timedelta
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.features import shapes

from shapely.geometry import shape as shp_shape
import geopandas as gpd

# STAC/COG (gratuito)
from pystac_client import Client
from rio_tiler.io import COGReader

# só para calcular width/height a partir da resolução (m) e bbox WGS84
from sentinelhub import BBox, CRS, bbox_to_dimensions

# ===== CONFIG =====
BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']
BANDAS_RGB   = ['B04','B03','B02']  # R,G,B
OUTPUT_DIR   = './SENTINEL2_BANDAS'

# ------------------------------------------------------------------------------
# DOWNLOAD via Earth Search (AWS COG) – NÃO precisa de credenciais
# ------------------------------------------------------------------------------
def baixar_bandas_earthsearch(output_dir=OUTPUT_DIR, dias=60, resolucao=10, bbox=None, max_cloud=30):
    """
    Baixa Sentinel-2 L2A (COG) via STAC público, recortando ao bbox (WGS84).
    Salva um GeoTIFF UINT16 por banda.
    """
    aoi = bbox if bbox else [-44.0, -21.5, -43.4, -20.9]  # [minx,miny,maxx,maxy] (WGS84)
    aoi_sh = BBox(bbox=aoi, crs=CRS.WGS84)
    width, height = bbox_to_dimensions(aoi_sh, resolution=resolucao)  # estima tamanho do raster

    dt_to = datetime.utcnow().date()
    dt_from = dt_to - timedelta(days=dias)

    client = Client.open("https://earth-search.aws.element84.com/v1")
    search = client.search(
        collections=["sentinel-2-l2a"],
        bbox=aoi,
        datetime=f"{dt_from.isoformat()}/{dt_to.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        limit=20,
    )
    items = list(search.get_items())
    if not items:
        raise RuntimeError("Nenhuma cena Sentinel-2 L2A encontrada para este bbox/período.")

    # pega o item com menor cobertura de nuvens
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 999))
    item = items[0]

    os.makedirs(output_dir, exist_ok=True)

    for banda in BANDAS_TODAS:
        asset = item.assets.get(banda)
        if not asset:
            continue
        href = asset.href

        with COGReader(href) as cog:
            # recorte ao bbox; max_size respeita a resolução aproximada desejada
            arr, mask = cog.part(
                aoi, bounds_crs="EPSG:4326",
                max_size=(width, height)
            )  # arr: (count,H,W); mask: (H,W) 0/255

        data = arr[0].astype("uint16")  # (H,W) – Sentinel-2 L2A já vem em UINT16 (escala *10000)
        if mask is not None:
            data = np.where(mask == 0, 0, data)

        H, W = data.shape
        transform = from_bounds(aoi[0], aoi[1], aoi[2], aoi[3], W, H)
        path_out = os.path.join(output_dir, f"{banda}.tif")

        with rasterio.open(
            path_out, "w",
            driver="GTiff", height=H, width=W, count=1, dtype="uint16",
            crs="EPSG:4326", transform=transform, compress="lzw"
        ) as dst:
            dst.write(data, 1)

    return f"✅ Bandas (EarthSearch) salvas com bbox {bbox} em {output_dir} (res≈{resolucao}m)"

# ------------------------------------------------------------------------------
# MULTIBANDA & RGB
# ------------------------------------------------------------------------------
def criar_multibanda(bandas, output_dir, output_path):
    arrays, profile = [], None
    for b in bandas:
        p = os.path.join(output_dir, f'{b}.tif')
        with rasterio.open(p) as src:
            a = src.read(1).astype(np.float32)  # mantém escala *10000
            arrays.append(a)
            if profile is None:
                profile = src.profile
                profile.update({'count': len(bandas), 'dtype': 'float32', 'compress':'lzw'})
    stack = np.stack(arrays)  # (C,H,W)
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack)
    return f"📦 Raster multibanda salvo em: {output_path}"

def _stretch8(arr, p_low=2, p_high=98):
    lo, hi = np.nanpercentile(arr, p_low), np.nanpercentile(arr, p_high)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
        if hi <= lo:
            return np.zeros_like(arr, dtype=np.uint8)
    x = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1)
    return (x * 255).astype(np.uint8)

def criar_rgb_8bit(bandas_rgb, output_dir, output_path):
    arrays, profile = [], None
    for b in bandas_rgb:
        p = os.path.join(output_dir, f'{b}.tif')
        with rasterio.open(p) as src:
            a = src.read(1).astype(np.float32)
            arrays.append(_stretch8(a))
            if profile is None:
                profile = src.profile
                profile.update({'count': len(bandas_rgb), 'dtype':'uint8', 'compress':'lzw'})
    stack = np.stack(arrays)  # (3,H,W)
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack)
    return f"📸 RGB 8 bits salvo em: {output_path}"

# ------------------------------------------------------------------------------
# SLIC ajustado (evita “quadradinhos”)
# ------------------------------------------------------------------------------
from skimage.segmentation import slic

def _percentile_scale_stack(img_hw_c, p_low=2, p_high=98):
    """Normaliza cada banda por percentil para [0,1]."""
    H, W, C = img_hw_c.shape
    out = np.empty_like(img_hw_c, dtype=np.float32)
    for c in range(C):
        band = img_hw_c[..., c].astype(np.float32)
        lo, hi = np.nanpercentile(band, p_low), np.nanpercentile(band, p_high)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.nanmin(band)), float(np.nanmax(band))
        if hi <= lo:
            out[..., c] = 0.0
        else:
            out[..., c] = np.clip((band - lo) / (hi - lo + 1e-6), 0, 1)
    return out

def aplicar_segmentacao_multibanda(
    image_path, output_dir, *,
    region_px=30,        # ~30 px -> ~300 m em 10 m
    compactness=1.0,     # menor = segue mais as bordas
    sigma=1.0,           # suaviza ruído
    output_filename='segments_slic_compactness05_step200'  # mantém o nome esperado pelas rotas
):
    with rasterio.open(image_path) as src:
        img = src.read().astype(np.float32)    # (C,H,W)
        transform, crs = src.transform, src.crs

    img = np.moveaxis(img, 0, -1)              # (H,W,C)
    img = _percentile_scale_stack(img, 2, 98)  # normaliza banda a banda

    H, W, _ = img.shape
    n_segments = max(50, int((H * W) / max(region_px**2, 1)))

    segments = slic(
        img,
        n_segments=n_segments,
        compactness=compactness,
        sigma=sigma,
        start_label=1,
        channel_axis=-1,
        convert2lab=False,
        enforce_connectivity=True,
    )

    # Vetoriza
    mask = segments > 0
    shapes_gen = shapes(segments.astype(np.uint16), mask=mask, transform=transform)
    geoms = [{"geometry": shp_shape(geom), "properties": {"segment_id": int(val)}} for geom, val in shapes_gen]

    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)
    gdf = gdf[gdf.geometry.is_valid & gdf.geometry.notna()]
    gdf = gdf[~gdf.geometry.is_empty]

    base = os.path.join(output_dir, output_filename)
    shp_path = base + '.shp'
    geojson_path = base + '.geojson'
    gdf.to_file(shp_path)
    gdf.to_file(geojson_path, driver='GeoJSON')

    return f"✅ Segmentação: {len(gdf)} polígonos | SHP: {shp_path} | GeoJSON: {geojson_path}"

# ------------------------------------------------------------------------------
# PIPELINE
# ------------------------------------------------------------------------------
def processar_segmentacao_completa(output_dir=OUTPUT_DIR, bbox=None):
    msgs = [f"🔍 Iniciando com BBox: {bbox}"]
    msgs.append(baixar_bandas_earthsearch(output_dir=output_dir, bbox=bbox))

    # SLIC com TODAS as bandas; RGB só para visualização
    path_multibanda = os.path.join(output_dir, 'sentinel_multibanda.tif')
    msgs.append(criar_multibanda(BANDAS_TODAS, output_dir, path_multibanda))

    path_rgb_8bit = os.path.join(output_dir, 'RGB_composicao_8bit.tif')
    msgs.append(criar_rgb_8bit(BANDAS_RGB, output_dir, path_rgb_8bit))

    msgs.append(aplicar_segmentacao_multibanda(
        image_path=path_multibanda,
        output_dir=output_dir,
        region_px=30, compactness=1.0, sigma=1.0,
        output_filename='segments_slic_compactness05_step200'  # não muda suas rotas
    ))
    return " | ".join(msgs)

if __name__ == '__main__':
    print(processar_segmentacao_completa())
