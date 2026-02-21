# services/segmentation.py — v7.19.2 (CONSOLIDADA + COMPOSIÇÕES CIENTÍFICAS + FIX PARQUET)
import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.features import shapes
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling

from shapely.geometry import shape as shp_shape
import geopandas as gpd

from pystac_client import Client
from rio_tiler.io import COGReader

from sentinelhub import BBox, CRS, bbox_to_dimensions
from skimage.segmentation import slic

PRINT_VERSION_TAG = "seg-v7.19.2-cientifico-completo"
print(f"[SEG] carregado: {PRINT_VERSION_TAG}")

# Configurações de Bandas
BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']
BANDAS_RGB   = ['B04','B03','B02']

BAND_ALIASES: Dict[str, List[str]] = {
    'B01': ['B01', 'coastal', 'aerosol', 'aot'],
    'B02': ['B02', 'blue'],
    'B03': ['B03', 'green'],
    'B04': ['B04', 'red'],
    'B05': ['B05', 'rededge1', 're1'],
    'B06': ['B06', 'rededge2', 're2'],
    'B07': ['B07', 'rededge3', 're3'],
    'B08': ['B08', 'nir'],
    'B8A': ['B8A', 'nir8a', 'nir08', 'nir_narrow', 'nir08a'],
    'B09': ['B09', 'water_vapor', 'wvp', 'nir09'],
    'B11': ['B11', 'swir16'],
    'B12': ['B12', 'swir22'],
}

# --- AUXILIARES ---

def _match_asset_key(item, band_id: str) -> Optional[str]:
    aliases = BAND_ALIASES.get(band_id, [band_id])
    for a in aliases:
        if a in item.assets: return item.assets[a].href
        if f"{a}-jp2" in item.assets: return item.assets[f"{a}-jp2"].href
    return None

def _search_items_sorted(client: Client, bbox: List[float], dt_from: datetime, dt_to: datetime, max_cloud: int) -> List:
    search = client.search(collections=["sentinel-2-l2a-cogs", "sentinel-2-l2a"],
                           bbox=bbox, datetime=f"{dt_from.isoformat()}/{dt_to.isoformat()}",
                           query={"eo:cloud_cover": {"lt": max_cloud}}, limit=50)
    items = list(search.items())
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 999))
    return items

def _percentile_scale(band, p_low=2, p_high=98):
    lo, hi = np.nanpercentile(band, p_low), np.nanpercentile(band, p_high)
    if hi <= lo: lo, hi = np.nanmin(band), np.nanmax(band)
    return np.clip((band - lo) / (hi - lo + 1e-6), 0, 1)

def _compute_ndvi(img_chw):
    idx_red, idx_nir = BANDAS_TODAS.index("B04"), BANDAS_TODAS.index("B08")
    red = img_chw[idx_red].astype(np.float32) / 10000.0
    nir = img_chw[idx_nir].astype(np.float32) / 10000.0
    return np.clip((nir - red) / (nir + red + 1e-6), -1.0, 1.0)

# --- DOWNLOAD E COMPOSIÇÕES ---

def baixar_bandas_earthsearch(output_dir, dias, resolucao, bbox, max_cloud):
    bbox = [float(x) for x in bbox]
    aoi_sh = BBox(bbox=bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(aoi_sh, resolution=(int(resolucao), int(resolucao)))
    dt_to = datetime.utcnow().date()
    dt_from = dt_to - timedelta(days=int(dias))
    
    client = Client.open("https://earth-search.aws.element84.com/v1")
    items = _search_items_sorted(client, bbox, dt_from, dt_to, int(max_cloud))
    if not items: raise RuntimeError("Cena não encontrada.")

    salvas = []
    total = len(BANDAS_TODAS)
    for i, banda in enumerate(BANDAS_TODAS, 1):
        sys.stdout.write(f"\r[DOWNLOAD] {i}/{total} - {banda}...")
        sys.stdout.flush()
        href = _match_asset_key(items[0], banda)
        if not href: continue
        with COGReader(href) as cog:
            part = cog.part(bbox, bounds_crs="EPSG:4326", width=w, height=h)
            data = part.data[0].astype(np.uint16)
        
        path_out = os.path.join(output_dir, f"{banda}.tif")
        with rasterio.open(path_out, "w", driver="GTiff", height=h, width=w, count=1, 
                          dtype="uint16", crs="EPSG:4326", transform=from_bounds(*bbox, w, h), compress="lzw") as dst:
            dst.write(data, 1)
        salvas.append(banda)
    return salvas

def criar_composicao_custom(bandas, output_dir, filename):
    arrays = []
    profile = None
    for b in bandas:
        p = os.path.join(output_dir, f"{b}.tif")
        if not os.path.exists(p): return
        with rasterio.open(p) as src:
            if profile is None: profile = src.profile.copy()
            arrays.append((_percentile_scale(src.read(1)) * 255).astype(np.uint8))
    if len(arrays) == 3:
        profile.update(count=3, dtype="uint8")
        with rasterio.open(os.path.join(output_dir, filename), "w", **profile) as dst:
            dst.write(np.stack(arrays), [1,2,3])

# --- CORE: SEGMENTAÇÃO E ATRIBUTOS (PARQUET) ---

def aplicar_segmentacao_e_extrair_features(image_path, output_dir, aoi_geojson, algoritmo, region_px, compactness, sigma):
    with rasterio.open(image_path) as src:
        img = src.read().astype(np.float32)
        transform, crs = src.transform, src.crs
        band_names = src.descriptions

    stack_base = np.stack([_percentile_scale(b) for b in img], axis=-1)
    ndvi = _compute_ndvi(img)
    stack = np.concatenate([stack_base, ((ndvi + 1.0)/2.0)[..., np.newaxis]], axis=-1)
    
    H, W, _ = stack.shape
    n_seg = max(2, int((H * W) / (int(region_px) ** 2)))
    segments = slic(stack, n_segments=n_seg, compactness=float(compactness), sigma=float(sigma), 
                    start_label=1, channel_axis=-1, slic_zero=(algoritmo.upper() == "ASA"))

    # Vetorização
    shapes_gen = shapes(segments.astype(np.uint16), mask=np.any(img > 0, axis=0), transform=transform)
    geoms = [{"geometry": shp_shape(g), "properties": {"segment_id": int(v)}} for g, v in shapes_gen]
    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)

    if aoi_geojson:
        mask_gdf = gpd.GeoDataFrame.from_features(aoi_geojson, crs="EPSG:4326").to_crs(crs)
        gdf = gpd.clip(gdf, mask_gdf)
    
    gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty]
    
    # --- FIX CRÍTICO: Sincronizar Parquet com Polígonos Reais ---
    ids_reais = gdf['segment_id'].unique()
    print(f"\n[ML] Extraindo atributos para {len(ids_reais)} polígonos válidos...")
    
    feature_list = []
    for sid in ids_reais:
        mask = (segments == sid)
        if not np.any(mask): continue
        feat = {"segment_id": int(sid)}
        for b_idx, b_name in enumerate(band_names):
            feat[b_name] = float(np.mean(img[b_idx][mask]))
        feat["NDVI_mean"] = float(np.mean(ndvi[mask]))
        feature_list.append(feat)
    
    df_features = pd.DataFrame(feature_list)
    # Salva sempre com o nome esperado pelo propagation.py
    df_features.to_parquet(os.path.join(output_dir, "features.parquet"))
    
    gdf.to_file(os.path.join(output_dir, 'segments.geojson'), driver='GeoJSON')
    return len(gdf)

def processar_segmentacao_completa(output_dir, bbox, aoi_geojson=None, algoritmo='SLIC', **kwargs):
    bandas = baixar_bandas_earthsearch(output_dir, kwargs.get('dias', 180), 10, bbox, kwargs.get('max_cloud', 80))
    
    # Criar Composições para o Usuário
    criar_composicao_custom(['B04','B03','B02'], output_dir, 'RGB_composicao_8bit.tif')
    criar_composicao_custom(['B08','B04','B03'], output_dir, 'Falsa_Cor_Veg.tif')
    criar_composicao_custom(['B11','B08','B02'], output_dir, 'Agricultura_Solo.tif')

    path_mb = os.path.join(output_dir, 'sentinel_multibanda.tif')
    # Gera o multibanda para segmentação
    arrays = []
    for b in BANDAS_TODAS:
        with rasterio.open(os.path.join(output_dir, f"{b}.tif")) as src: arrays.append(src.read(1))
    with rasterio.open(path_mb, "w", driver="GTiff", height=arrays[0].shape[0], width=arrays[0].shape[1], 
                      count=len(BANDAS_TODAS), dtype="float32", crs="EPSG:4326", 
                      transform=from_bounds(*bbox, arrays[0].shape[1], arrays[0].shape[0])) as dst:
        dst.write(np.stack(arrays).astype(np.float32))
        dst.descriptions = tuple(BANDAS_TODAS)

    count = aplicar_segmentacao_e_extrair_features(path_mb, output_dir, aoi_geojson, algoritmo, 
                                                   kwargs.get('region_px', 30), kwargs.get('compactness', 1.0), 
                                                   kwargs.get('sigma', 1.0))
    return f"✅ Download OK | ✅ {count} polígonos e atributos extraídos."