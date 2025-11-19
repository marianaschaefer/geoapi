# services/segmentation.py  — v7.2-params
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.features import shapes
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.warp import reproject

from shapely.geometry import shape as shp_shape
import geopandas as gpd

from pystac_client import Client
from rio_tiler.io import COGReader

from sentinelhub import BBox, CRS, bbox_to_dimensions
from skimage.segmentation import slic

PRINT_VERSION_TAG = "seg-v7.2-params"
print("[SEG] carregado:", PRINT_VERSION_TAG)

# ==============================
# CONFIG
# ==============================
BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']
BANDAS_RGB   = ['B04','B03','B02']  # R,G,B
OUTPUT_DIR   = './SENTINEL2_BANDAS'

BAND_ALIASES: Dict[str, List[str]] = {
    'B01': ['B01', 'coastal', 'aerosol', 'aot'],
    'B02': ['B02', 'blue'],
    'B03': ['B03', 'green'],
    'B04': ['B04', 'red'],
    'B05': ['B05', 'rededge1', 'red-edge-1', 're1'],
    'B06': ['B06', 'rededge2', 'red-edge-2', 're2'],
    'B07': ['B07', 'rededge3', 'red-edge-3', 're3'],
    'B08': ['B08', 'nir', 'nir08'],
    'B8A': ['B8A', 'nir9', 'nir09', 'nir8a', 'nir08'],
    'B09': ['B09', 'water_vapor', 'wv', 'wvp'],
    'B11': ['B11', 'swir16', 'swir1'],
    'B12': ['B12', 'swir22', 'swir2'],
}
PREFERRED_RES_SUFFIX = ['_10m', '_20m', '_60m']

ASSET_BY_BAND: Dict[str, List[str]] = {
    'B01': ['coastal', 'aot'],
    'B02': ['blue'],
    'B03': ['green'],
    'B04': ['red'],
    'B05': ['rededge1'],
    'B06': ['rededge2'],
    'B07': ['rededge3'],
    'B08': ['nir', 'nir08'],
    'B8A': ['nir09', 'nir9', 'nir8a', 'nir08'],
    'B09': ['wvp', 'water_vapor', 'wv'],
    'B11': ['swir16'],
    'B12': ['swir22'],
}

# ==============================
# HELPERS
# ==============================
def _match_asset_key(item, band_id: str) -> Optional[str]:
    aliases = BAND_ALIASES.get(band_id, [band_id])

    for k in [band_id] + [band_id + s for s in PREFERRED_RES_SUFFIX]:
        if k in item.assets:
            return item.assets[k].href

    for key in ASSET_BY_BAND.get(band_id, []):
        if key in item.assets:
            return item.assets[key].href
        if f"{key}-jp2" in item.assets:
            return item.assets[f"{key}-jp2"].href

    for a in aliases:
        if a in item.assets:
            return item.assets[a].href
        if f"{a}-jp2" in item.assets:
            return item.assets[f"{a}-jp2"].href

    for k, asset in item.assets.items():
        if band_id in k:
            return asset.href
        for a in aliases:
            if a in k:
                return asset.href

    for _, asset in item.assets.items():
        d = asset.to_dict()
        for field in ('eo:bands', 'raster:bands'):
            bands_meta = d.get(field)
            if isinstance(bands_meta, list):
                for bm in bands_meta:
                    name  = str(bm.get('name', ''))
                    cname = str(bm.get('common_name', ''))
                    if name.upper() == band_id.upper():
                        return asset.href
                    if cname and cname.lower() in [x.lower() for x in aliases]:
                        return asset.href
    return None


def _search_best_item(client: Client, bbox: List[float], dt_from: datetime, dt_to: datetime, max_cloud: int):
    collections_order = ["sentinel-2-l2a-cogs", "sentinel-2-l2a", "sentinel-2-l1c"]
    for coll in collections_order:
        search = client.search(
            collections=[coll],
            bbox=bbox,
            datetime=f"{dt_from.isoformat()}/{dt_to.isoformat()}",
            query={"eo:cloud_cover": {"lt": max_cloud}},
            limit=50,
        )
        items = list(search.items())
        if not items:
            continue
        items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 999))
        for it in items:
            if _match_asset_key(it, 'B04'):
                print(f"[INFO] Item escolhido da coleção '{coll}', cloud_cover=", it.properties.get("eo:cloud_cover"))
                print("[INFO] Assets disponíveis:", list(it.assets.keys()))
                return it
    return None

def _read_as_ref_grid(src_path: str, ref_crs, ref_transform, ref_width: int, ref_height: int,
                      dtype=np.float32, resampling=Resampling.bilinear):
    with rasterio.open(src_path) as src:
        needs_warp = (src.crs != ref_crs or src.transform != ref_transform or
                      src.width != ref_width or src.height != ref_height)
        if not needs_warp:
            return src.read(1).astype(dtype)
        with WarpedVRT(src, crs=ref_crs, transform=ref_transform, width=ref_width,
                       height=ref_height, resampling=resampling) as vrt:
            return vrt.read(1).astype(dtype)

def _percentile_scale_stack(img_hw_c: np.ndarray, p_low=2, p_high=98):
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

def _stretch8(arr, p_low=2, p_high=98):
    lo, hi = np.nanpercentile(arr, p_low), np.nanpercentile(arr, p_high)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
        if hi <= lo:
            return np.zeros_like(arr, dtype=np.uint8)
    x = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1)
    return (x * 255).astype(np.uint8)

# ==============================
# DOWNLOAD via Earth Search (AWS COG)
# ==============================
def baixar_bandas_earthsearch(output_dir=OUTPUT_DIR, dias=180, resolucao=10, bbox=None, max_cloud=80):
    """
    Baixa Sentinel-2 (COG) via STAC público, recortando ao bbox (WGS84).
    Usa 'dias', 'resolucao' e 'max_cloud' conforme painel.
    """
    aoi = bbox if bbox else [-44.0, -21.5, -43.4, -20.9]
    aoi_sh = BBox(bbox=aoi, crs=CRS.WGS84)
    width, height = bbox_to_dimensions(aoi_sh, resolution=resolucao)
    width, height = int(max(1, width)), int(max(1, height))

    dt_to   = datetime.utcnow().date()
    dt_from = dt_to - timedelta(days=dias)

    client = Client.open("https://earth-search.aws.element84.com/v1")
    item = _search_best_item(client, aoi, dt_from, dt_to, max_cloud)
    if item is None:
        raise RuntimeError("Nenhuma cena com bandas válidas (tente aumentar 'dias' e/ou 'max_cloud').")

    os.makedirs(output_dir, exist_ok=True)
    salvas: List[str] = []

    for banda in BANDAS_TODAS:
        href = _match_asset_key(item, banda)
        if not href:
            print(f"[WARN] Asset de {banda} não encontrado; pulando.")
            continue

        try:
            with COGReader(href) as cog:
                img = cog.part(aoi, bounds_crs="EPSG:4326", width=width, height=height)
                try:
                    arr, mask = img
                except Exception:
                    arr, mask = img.data, img.mask

            data = arr[0].astype("uint16")
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

            salvas.append(banda)
        except Exception as e:
            print(f"[WARN] Falha ao salvar {banda}: {e}")

    if not salvas:
        raise RuntimeError("Falha ao baixar qualquer banda.")

    msg = f"✅ Bandas (EarthSearch) salvas: {','.join(salvas)} | bbox={bbox} | res≈{resolucao}m | dias={dias} | nuvens<{max_cloud}%"
    return salvas, msg

# ==============================
# MULTIBANDA & RGB
# ==============================
def criar_multibanda(bandas: List[str], output_dir: str, output_path: str):
    ref_profile = None
    ref_transform = None
    ref_crs = None
    H_ref = W_ref = None

    for b in bandas:
        p = os.path.join(output_dir, f"{b}.tif")
        if os.path.exists(p):
            with rasterio.open(p) as src:
                ref_profile  = src.profile.copy()
                ref_transform = src.transform
                ref_crs       = src.crs
                H_ref, W_ref  = src.height, src.width
            break
    if ref_profile is None:
        raise FileNotFoundError("Nenhuma banda *.tif encontrada em SENTINEL2_BANDAS.")

    print(f"[MB] grade ref: {W_ref}x{H_ref} | CRS={ref_crs}")

    arrays, used = [], []
    for b in bandas:
        p = os.path.join(output_dir, f"{b}.tif")
        if not os.path.exists(p):
            print(f"[MB] {b}: ausente")
            continue

        with rasterio.open(p) as src:
            needs_warp = (src.crs != ref_crs or src.transform != ref_transform or
                          src.width != W_ref or src.height != H_ref)
            if needs_warp:
                with WarpedVRT(src, crs=ref_crs, transform=ref_transform, width=W_ref,
                               height=H_ref, resampling=Resampling.bilinear) as vrt:
                    band = vrt.read(1).astype(np.float32)
                print(f"[MB] {b}: VRT -> {band.shape}")
            else:
                band = src.read(1).astype(np.float32)
                print(f"[MB] {b}: native -> {band.shape}")

        if band.shape != (H_ref, W_ref):
            print(f"[MB][WARN] {b}: shape {band.shape} != ({H_ref},{W_ref}). Fallback reproject.")
            with rasterio.open(p) as src:
                dst = np.empty((H_ref, W_ref), dtype=np.float32)
                reproject(
                    source=src.read(1).astype(np.float32),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    resampling=Resampling.bilinear,
                )
                band = dst
            print(f"[MB] {b}: fallback -> {band.shape}")

        arrays.append(band)
        used.append(b)

    shapes = {a.shape for a in arrays}
    if len(shapes) != 1:
        raise RuntimeError(f"[MB] Shapes divergentes após normalização: {shapes}")

    stack = np.stack(arrays)  # (C,H,W)
    ref_profile.update({"count": stack.shape[0], "dtype": "float32", "compress": "lzw"})
    with rasterio.open(output_path, "w", **ref_profile) as dst:
        dst.write(stack)
    return f"📦 Raster multibanda salvo em: {output_path} (bandas usadas: {','.join(used)})"

def criar_rgb_8bit(bandas_rgb: List[str], output_dir: str, output_path: str):
    ref_profile = None
    ref_transform = None
    ref_crs = None
    H_ref = W_ref = None

    for b in bandas_rgb:
        p = os.path.join(output_dir, f"{b}.tif")
        if os.path.exists(p):
            with rasterio.open(p) as src:
                ref_profile = src.profile.copy()
                ref_transform = src.transform
                ref_crs = src.crs
                H_ref, W_ref = src.height, src.width
            break

    if ref_profile is None:
        return "⚠️ RGB não gerado (nenhuma das bandas RGB disponíveis)."

    arrays, used = [], []
    for b in bandas_rgb:
        p = os.path.join(output_dir, f"{b}.tif")
        if not os.path.exists(p):
            continue
        band_f32 = _read_as_ref_grid(p, ref_crs, ref_transform, W_ref, H_ref,
                                     dtype=np.float32, resampling=Resampling.bilinear)
        lo, hi = np.nanpercentile(band_f32, 2), np.nanpercentile(band_f32, 98)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.nanmin(band_f32)), float(np.nanmax(band_f32))
            if hi <= lo:
                band_u8 = np.zeros_like(band_f32, dtype=np.uint8)
            else:
                band_u8 = (np.clip((band_f32 - lo) / (hi - lo + 1e-6), 0, 1) * 255).astype(np.uint8)
        else:
            band_u8 = (np.clip((band_f32 - lo) / (hi - lo + 1e-6), 0, 1) * 255).astype(np.uint8)

        arrays.append(band_u8)
        used.append(b)

    stack = np.stack(arrays)
    ref_profile.update({"count": stack.shape[0], "dtype": "uint8", "compress": "lzw"})
    with rasterio.open(output_path, "w", **ref_profile) as dst:
        dst.write(stack)
    return f"📸 RGB 8 bits salvo em: {output_path} (bandas usadas: {','.join(used)})"

# ==============================
# SLIC com NDVI opcional
# ==============================
def aplicar_segmentacao_multibanda(
    image_path: str, output_dir: str, *,
    region_px=30,
    compactness=1.0,
    sigma=1.0,
    usar_ndvi=False,
    output_filename='segments_slic_compactness05_step200'
):
    with rasterio.open(image_path) as src:
        img = src.read().astype(np.float32)    # (C,H,W)
        transform, crs = src.transform, src.crs

    # Construir o cubo para o SLIC
    stack_for_slic = np.moveaxis(img, 0, -1)  # (H,W,C)

    if usar_ndvi:
        # NDVI = (NIR - RED) / (NIR + RED)
        # Procurar índices das bandas no multibanda
        # (assumimos que o multibanda foi salvo na ordem das 'bandas_disponiveis')
        with rasterio.open(image_path) as src:
            tags = [src.descriptions[i] if src.descriptions and i < len(src.descriptions) else f"b{i+1}"
                    for i in range(src.count)]
        # fallback se descriptions não existem: tentar achar por nomes esperados
        # localiza RED(B04) e NIR(B08) pelo caminho mais simples:
        try:
            idx_red = next(i for i, d in enumerate(tags) if "B04" in (d or ""))
            idx_nir = next(i for i, d in enumerate(tags) if "B08" in (d or ""))
        except StopIteration:
            # assume posições padrão (se não houver descriptions)
            # aqui consideramos ordem original da pilha usada por criar_multibanda
            # faremos uma tentativa segura:
            band_names_guess = BANDAS_TODAS
            def guess_index(name):
                try:
                    return band_names_guess.index(name)
                except ValueError:
                    return None
            idx_red = guess_index("B04")
            idx_nir = guess_index("B08")

        if idx_red is not None and idx_nir is not None \
           and 0 <= idx_red < stack_for_slic.shape[-1] \
           and 0 <= idx_nir < stack_for_slic.shape[-1]:
            red = stack_for_slic[..., idx_red]
            nir = stack_for_slic[..., idx_nir]
            ndvi = (nir - red) / (np.maximum(nir + red, 1e-6))
            ndvi = np.clip(ndvi, -1, 1).astype(np.float32)
            stack_for_slic = np.concatenate([stack_for_slic, ndvi[..., None]], axis=-1)

    # Normaliza para [0,1]
    stack_for_slic = _percentile_scale_stack(stack_for_slic, 2, 98)

    H, W, _ = stack_for_slic.shape
    n_segments = max(50, int((H * W) / max(region_px**2, 1)))

    segments = slic(
        stack_for_slic,
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

# ==============================
# PIPELINE
# ==============================
def processar_segmentacao_completa(
    output_dir=OUTPUT_DIR,
    bbox=None,
    *,
    dias=180,
    resolucao=10,
    max_cloud=80,
    region_px=30,
    compactness=1.0,
    sigma=1.0,
    usar_ndvi=False
):
    msgs = [f"🔍 Iniciando com BBox: {bbox}"]

    # 1) Download com os parâmetros do painel
    bandas_salvas, msg = baixar_bandas_earthsearch(
        output_dir=output_dir,
        dias=dias,
        resolucao=resolucao,
        bbox=bbox,
        max_cloud=max_cloud
    )
    msgs.append(msg)

    # 2) Multibanda & RGB
    bandas_disponiveis = [b for b in BANDAS_TODAS if os.path.exists(os.path.join(output_dir, f"{b}.tif"))]
    if not bandas_disponiveis:
        raise RuntimeError("Nenhuma banda disponível no disco após download.")

    path_multibanda = os.path.join(output_dir, 'sentinel_multibanda.tif')
    msgs.append(criar_multibanda(bandas_disponiveis, output_dir, path_multibanda))

    path_rgb_8bit = os.path.join(output_dir, 'RGB_composicao_8bit.tif')
    msgs.append(criar_rgb_8bit(BANDAS_RGB, output_dir, path_rgb_8bit))

    # 3) SLIC usando os parâmetros do painel
    msgs.append(aplicar_segmentacao_multibanda(
        image_path=path_multibanda,
        output_dir=output_dir,
        region_px=region_px,
        compactness=compactness,
        sigma=sigma,
        usar_ndvi=usar_ndvi,
        output_filename='segments_slic_compactness05_step200'  # mantém o nome esperado pelo front
    ))
    return " | ".join(msgs)
