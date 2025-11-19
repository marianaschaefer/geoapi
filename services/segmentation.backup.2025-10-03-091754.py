import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.features import shapes

from shapely.geometry import shape as shp_shape
import geopandas as gpd

from pystac_client import Client
from rio_tiler.io import COGReader

# usado apenas para estimar (width, height) a partir do bbox+resolução
from sentinelhub import BBox, CRS, bbox_to_dimensions

from skimage.segmentation import slic

PRINT_VERSION_TAG = "seg-v5-stac-explicit"
print("[SEG] carregado:", PRINT_VERSION_TAG)

# ==============================
# CONFIG
# ==============================
BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']
BANDAS_RGB   = ['B04','B03','B02']  # R,G,B
OUTPUT_DIR   = './SENTINEL2_BANDAS'

# Aliases que aparecem em coleções STAC do EarthSearch
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

# Mapa explícito de nomes de asset no EarthSearch (prioritário)
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
# HELPERS: localizar assets / escolher item
# ==============================
def _match_asset_key(item, band_id: str) -> Optional[str]:
    """
    Retorna o HREF do asset da banda considerando várias chaves possíveis:
    1) 'Bxx' e 'Bxx_10m/_20m/_60m'
    2) nomes explícitos do EarthSearch (ASSET_BY_BAND) e suas variantes '-jp2'
    3) aliases exatos e 'alias-jp2'
    4) qualquer chave que contenha banda/alias
    5) metadados (eo:bands / raster:bands) por name/common_name
    """
    aliases = BAND_ALIASES.get(band_id, [band_id])

    # 1) tentativas diretas: 'Bxx', 'Bxx_10m'...
    for k in [band_id] + [band_id + s for s in PREFERRED_RES_SUFFIX]:
        if k in item.assets:
            return item.assets[k].href

    # 2) mapa explícito (prioritário)
    for key in ASSET_BY_BAND.get(band_id, []):
        if key in item.assets:
            return item.assets[key].href
        if f"{key}-jp2" in item.assets:
            return item.assets[f"{key}-jp2"].href

    # 3) aliases exatos
    for a in aliases:
        if a in item.assets:
            return item.assets[a].href
        if f"{a}-jp2" in item.assets:
            return item.assets[f"{a}-jp2"].href

    # 4) contains
    for k, asset in item.assets.items():
        if band_id in k:
            return asset.href
        for a in aliases:
            if a in k:
                return asset.href

    # 5) metadados (eo:bands / raster:bands)
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
    """Procura em coleções alternativas e devolve o primeiro item que possua uma B04 válida."""
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
        # ordena por menor cobertura de nuvens
        items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 999))
        # escolhe o primeiro que tenha asset para B04
        for it in items:
            if _match_asset_key(it, 'B04'):
                print(f"[INFO] Item escolhido da coleção '{coll}', cloud_cover=", it.properties.get("eo:cloud_cover"))
                print("[INFO] Assets disponíveis:", list(it.assets.keys()))
                return it
    return None


# ==============================
# DOWNLOAD via Earth Search (AWS COG) – sem credenciais
# ==============================
def baixar_bandas_earthsearch(output_dir=OUTPUT_DIR, dias=180, resolucao=10, bbox=None, max_cloud=80):
    """
    Baixa Sentinel-2 (COG) via STAC público, recortando ao bbox (WGS84).
    Salva um GeoTIFF UINT16 por banda.
    RETORNA: (lista_bandas_salvas, mensagem)
    """
    aoi = bbox if bbox else [-44.0, -21.5, -43.4, -20.9]  # [minx,miny,maxx,maxy] (EPSG:4326)
    aoi_sh = BBox(bbox=aoi, crs=CRS.WGS84)
    width, height = bbox_to_dimensions(aoi_sh, resolution=resolucao)
    width, height = int(max(1, width)), int(max(1, height))
    # usar um único max_size inteiro (evita bug de comparação int < tuple)
    max_size = int(max(width, height))

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
                # versões do rio-tiler variam: algumas retornam (array, mask), outras um ImageData
                img = cog.part(aoi, bounds_crs="EPSG:4326", max_size=max_size)
                try:
                    arr, mask = img  # padrão (array, mask)
                except Exception:
                    arr, mask = img.data, img.mask  # ImageData

            # arr: (bands, H, W) → pega a 1ª banda
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

    msg = f"✅ Bandas (EarthSearch) salvas: {','.join(salvas)} | bbox={bbox} | res≈{resolucao}m"
    return salvas, msg


# ==============================
# MULTIBANDA & RGB (robustos a bandas ausentes)
# ==============================
def criar_multibanda(bandas: List[str], output_dir: str, output_path: str):
    arrays, used, profile = [], [], None
    for b in bandas:
        p = os.path.join(output_dir, f'{b}.tif')
        if not os.path.exists(p):
            continue
        with rasterio.open(p) as src:
            a = src.read(1).astype(np.float32)  # mantém escala *10000
            arrays.append(a)
            used.append(b)
            if profile is None:
                profile = src.profile
                profile.update({'count': 0, 'dtype': 'float32', 'compress':'lzw'})
    if not arrays:
        raise FileNotFoundError("Nenhuma banda *.tif encontrada em SENTINEL2_BANDAS.")
    stack = np.stack(arrays)  # (C,H,W)
    profile['count'] = stack.shape[0]
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack)
    return f"📦 Raster multibanda salvo em: {output_path} (bandas usadas: {','.join(used)})"


def _stretch8(arr, p_low=2, p_high=98):
    lo, hi = np.nanpercentile(arr, p_low), np.nanpercentile(arr, p_high)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
        if hi <= lo:
            return np.zeros_like(arr, dtype=np.uint8)
    x = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1)
    return (x * 255).astype(np.uint8)


def criar_rgb_8bit(bandas_rgb: List[str], output_dir: str, output_path: str):
    arrays, used, profile = [], [], None
    for b in bandas_rgb:
        p = os.path.join(output_dir, f'{b}.tif')
        if not os.path.exists(p):
            continue
        with rasterio.open(p) as src:
            a = src.read(1).astype(np.float32)
            arrays.append(_stretch8(a))
            used.append(b)
            if profile is None:
                profile = src.profile
                profile.update({'count': 0, 'dtype':'uint8', 'compress':'lzw'})
    if not arrays:
        return "⚠️ RGB não gerado (nenhuma das bandas RGB disponíveis)."
    stack = np.stack(arrays)  # (N,H,W) — N pode ser < 3
    profile['count'] = stack.shape[0]
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(stack)
    return f"📸 RGB 8 bits salvo em: {output_path} (bandas usadas: {','.join(used)})"


# ==============================
# SLIC (ajustado para evitar “quadradinhos”)
# ==============================
def _percentile_scale_stack(img_hw_c: np.ndarray, p_low=2, p_high=98):
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
    image_path: str, output_dir: str, *,
    region_px=30,        # ~30 px -> ~300 m em 10 m
    compactness=1.0,     # menor = segue mais as bordas
    sigma=1.0,           # suaviza ruído
    output_filename='segments_slic_compactness05_step200'
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


# ==============================
# PIPELINE
# ==============================
def processar_segmentacao_completa(output_dir=OUTPUT_DIR, bbox=None):
    msgs = [f"🔍 Iniciando com BBox: {bbox}"]
    bandas_salvas, msg = baixar_bandas_earthsearch(output_dir=output_dir, bbox=bbox)
    msgs.append(msg)

    # monta multibanda só com o que realmente existe no disco
    bandas_disponiveis = [b for b in BANDAS_TODAS if os.path.exists(os.path.join(output_dir, f"{b}.tif"))]
    if not bandas_disponiveis:
        raise RuntimeError("Nenhuma banda disponível no disco após download.")

    path_multibanda = os.path.join(output_dir, 'sentinel_multibanda.tif')
    msgs.append(criar_multibanda(bandas_disponiveis, output_dir, path_multibanda))

    path_rgb_8bit = os.path.join(output_dir, 'RGB_composicao_8bit.tif')
    msgs.append(criar_rgb_8bit(BANDAS_RGB, output_dir, path_rgb_8bit))

    msgs.append(aplicar_segmentacao_multibanda(
        image_path=path_multibanda,
        output_dir=output_dir,
        region_px=30, compactness=1.0, sigma=1.0,
        output_filename='segments_slic_compactness05_step200'  # mantém compatível com as rotas
    ))
    return " | ".join(msgs)


if __name__ == '__main__':
    print(processar_segmentacao_completa())
