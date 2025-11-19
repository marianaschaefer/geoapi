# services/features.py
import os
import json
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from shapely.geometry import box

# Mesma convenção usada na segmentação
BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']

BASE_DIR = "./SENTINEL2_BANDAS"
MULTIBANDA = os.path.join(BASE_DIR, "sentinel_multibanda.tif")

SEG_BASE = os.path.join(BASE_DIR, "segments_slic_compactness05_step200")
SEG_GEOJSON = SEG_BASE + ".geojson"
SEG_SHP = SEG_BASE + ".shp"

FEATS_PARQUET = os.path.join(BASE_DIR, "features_by_segment.parquet")
FEATS_CSV     = os.path.join(BASE_DIR, "features_by_segment.csv")  # opcional p/ debug


def _load_segments_gdf() -> gpd.GeoDataFrame:
    for p in (SEG_GEOJSON, SEG_SHP):
        if os.path.exists(p):
            gdf = gpd.read_file(p)
            if "segment_id" not in gdf.columns:
                raise RuntimeError("Camada de segmentos não possui coluna 'segment_id'.")
            return gdf
    raise FileNotFoundError("Camada de segmentos não encontrada (rode /segmentar primeiro).")


def _read_multiband() -> Tuple[np.ndarray, rasterio.Affine, str, Dict[str,int]]:
    """
    Lê o multibanda (C,H,W) e retorna array, transform, crs e mapa banda->índice.
    """
    if not os.path.exists(MULTIBANDA):
        raise FileNotFoundError("Multibanda não encontrado. Rode /segmentar primeiro.")
    with rasterio.open(MULTIBANDA) as src:
        arr = src.read().astype(np.float32)  # (C,H,W) escala *10000
        transform = src.transform
        crs = src.crs.to_string() if src.crs else "EPSG:4326"
    band_to_idx = {b: i for i, b in enumerate(BANDAS_TODAS)}
    return arr, transform, crs, band_to_idx


def _compute_indices(arr: np.ndarray, band_to_idx: Dict[str,int]) -> Dict[str, np.ndarray]:
    """
    Calcula alguns índices por pixel (em float32). Usa reflectância (0..1).
    NDVI, NDBI, MNDWI, EVI, NBR.
    """
    eps = 1e-6
    # converte p/ reflectância 0..1 (arr foi salvo como *10000)
    ref = arr / 10000.0

    R  = ref[band_to_idx['B04']]    # Red
    G  = ref[band_to_idx['B03']]    # Green
    B  = ref[band_to_idx['B02']]    # Blue
    NIR  = ref[band_to_idx['B08']]  # NIR
    SWIR1 = ref[band_to_idx['B11']] # SWIR1
    SWIR2 = ref[band_to_idx['B12']] # SWIR2

    ndvi  = (NIR - R) / (NIR + R + eps)
    ndbi  = (SWIR1 - NIR) / (SWIR1 + NIR + eps)          # proxy de áreas construídas
    mndwi = (G - SWIR1) / (G + SWIR1 + eps)              # água
    evi   = 2.5 * (NIR - R) / (NIR + 6*R - 7.5*B + 1.0)  # vegetação
    nbr   = (NIR - SWIR2) / (NIR + SWIR2 + eps)          # queimadas/estresse

    return {
        "NDVI": ndvi.astype(np.float32),
        "NDBI": ndbi.astype(np.float32),
        "MNDWI": mndwi.astype(np.float32),
        "EVI": evi.astype(np.float32),
        "NBR": nbr.astype(np.float32),
    }


def _rasterize_segments(seg_gdf: gpd.GeoDataFrame, out_shape: Tuple[int,int], transform) -> np.ndarray:
    """
    Rasteriza o GeoDataFrame de segmentos para uma imagem (H,W) de labels (segment_id).
    """
    shapes = [(geom, int(seg_id)) for geom, seg_id in zip(seg_gdf.geometry, seg_gdf["segment_id"])]
    labels = rasterize(
        shapes,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False
    )
    return labels


def _stats_for_label(values: np.ndarray, labels: np.ndarray, label: int) -> Dict[str, float]:
    """
    Calcula estatísticas para um label específico (mean, std, p10, p50, p90) ignorando NaN.
    """
    mask = labels == label
    if not np.any(mask):
        return {"mean": np.nan, "std": np.nan, "p10": np.nan, "p50": np.nan, "p90": np.nan}
    v = values[mask].astype(np.float32)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"mean": np.nan, "std": np.nan, "p10": np.nan, "p50": np.nan, "p90": np.nan}
    return {
        "mean": float(np.nanmean(v)),
        "std":  float(np.nanstd(v)),
        "p10":  float(np.nanpercentile(v, 10)),
        "p50":  float(np.nanpercentile(v, 50)),
        "p90":  float(np.nanpercentile(v, 90)),
    }


def build_features(save_csv: bool = False) -> str:
    """
    Gera o arquivo de features por segmento (Parquet). Retorna o caminho salvo.
    """
    # 1) carregamentos
    seg_gdf = _load_segments_gdf()
    arr, transform, crs, band_to_idx = _read_multiband()   # arr: (C,H,W)

    # 2) rasterizar segmentos para (H,W)
    H, W = arr.shape[1], arr.shape[2]
    labels = _rasterize_segments(seg_gdf, out_shape=(H, W), transform=transform)
    unique_labels = np.array(sorted(list(set(int(x) for x in np.unique(labels) if x > 0))), dtype=int)
    if unique_labels.size == 0:
        raise RuntimeError("Rasterização não gerou labels > 0. Verifique CRS e bbox.")

    # 3) preparar estrutura do resultado
    rows: List[Dict[str, float]] = []
    # índices por pixel (em float32)
    indices = _compute_indices(arr, band_to_idx)

    # 4) calcular estatísticas por segmento
    for seg_id in unique_labels:
        rec: Dict[str, float] = {"segment_id": int(seg_id)}

        # bandas (em reflectância 0..1 para médias comparáveis)
        for b in BANDAS_TODAS:
            band_vals = (arr[band_to_idx[b]] / 10000.0)  # 0..1
            st = _stats_for_label(band_vals, labels, seg_id)
            for k, v in st.items():
                rec[f"{b}_{k}"] = v

        # índices
        for name, idx_arr in indices.items():
            st = _stats_for_label(idx_arr, labels, seg_id)
            for k, v in st.items():
                rec[f"{name}_{k}"] = v

        rows.append(rec)

    df = pd.DataFrame(rows)
    # salva
    os.makedirs(BASE_DIR, exist_ok=True)
    df.to_parquet(FEATS_PARQUET, index=False)
    if save_csv:
        df.to_csv(FEATS_CSV, index=False)

    return FEATS_PARQUET


def read_features(limit: Optional[int] = None, segment_id: Optional[int] = None) -> pd.DataFrame:
    """
    Lê o parquet de features. Pode filtrar por segment_id e/ou limitar linhas.
    """
    if not os.path.exists(FEATS_PARQUET):
        raise FileNotFoundError("Features ainda não foram geradas. Chame /features/build primeiro.")
    df = pd.read_parquet(FEATS_PARQUET)
    if segment_id is not None:
        df = df[df["segment_id"] == int(segment_id)]
    if limit is not None and limit > 0:
        df = df.head(limit)
    return df
