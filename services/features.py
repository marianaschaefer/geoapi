# services/features.py — v7.9.2 (NDSI sim, EBBI não)
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize

BANDAS_TODAS = ['B01','B02','B03','B04','B05','B06','B07','B08','B8A','B09','B11','B12']


def _load_segments_gdf(output_dir: str) -> gpd.GeoDataFrame:
    seg_geojson = os.path.join(output_dir, "segments.geojson")
    legacy_geojson = os.path.join(output_dir, "segments_slic_compactness05_step200.geojson")
    seg_shp = os.path.join(output_dir, "segments.shp")
    legacy_shp = os.path.join(output_dir, "segments_slic_compactness05_step200.shp")

    for p in (seg_geojson, legacy_geojson, seg_shp, legacy_shp):
        if os.path.exists(p):
            gdf = gpd.read_file(p)
            if "segment_id" not in gdf.columns:
                raise RuntimeError("Camada de segmentos não possui coluna 'segment_id'.")
            return gdf

    raise FileNotFoundError("Camada de segmentos não encontrada (rode /segmentar primeiro).")


def _read_multiband(output_dir: str) -> Tuple[np.ndarray, rasterio.Affine, str, Dict[str, int]]:
    multibanda = os.path.join(output_dir, "sentinel_multibanda.tif")
    if not os.path.exists(multibanda):
        raise FileNotFoundError("Multibanda não encontrado. Rode /segmentar primeiro.")

    with rasterio.open(multibanda) as src:
        arr = src.read().astype(np.float32)  # (C,H,W) escala *10000
        transform = src.transform
        crs = src.crs.to_string() if src.crs else "EPSG:4326"

    band_to_idx = {b: i for i, b in enumerate(BANDAS_TODAS)}
    return arr, transform, crs, band_to_idx


def _compute_indices(ref: np.ndarray, band_to_idx: Dict[str, int]) -> Dict[str, np.ndarray]:
    """
    ref: (C,H,W) em reflectância aproximada (arr/10000.0)
    Retorna dict de índices (H,W) float32.
    """
    eps = 1e-6

    R = ref[band_to_idx['B04']]
    G = ref[band_to_idx['B03']]
    B = ref[band_to_idx['B02']]
    NIR = ref[band_to_idx['B08']]
    SWIR1 = ref[band_to_idx['B11']]
    SWIR2 = ref[band_to_idx['B12']]

    # NDVI
    ndvi = (NIR - R) / (NIR + R + eps)

    # NDSI (Snow/soil/bright surfaces vs SWIR) — padrão: (Green - SWIR1)/(Green + SWIR1)
    ndsi = (G - SWIR1) / (G + SWIR1 + eps)

    # Mantidos (se quiser remover depois, ok)
    ndbi = (SWIR1 - NIR) / (SWIR1 + NIR + eps)
    mndwi = (G - SWIR1) / (G + SWIR1 + eps)  # igual ao NDSI (mesma fórmula); se quiser, removemos um dos dois.
    evi = 2.5 * (NIR - R) / (NIR + 6 * R - 7.5 * B + 1.0)
    nbr = (NIR - SWIR2) / (NIR + SWIR2 + eps)

    return {
        "NDVI": ndvi.astype(np.float32),
        "NDSI": ndsi.astype(np.float32),
        "NDBI": ndbi.astype(np.float32),
        "MNDWI": mndwi.astype(np.float32),
        "EVI": evi.astype(np.float32),
        "NBR": nbr.astype(np.float32),
    }


def _rasterize_segments(seg_gdf: gpd.GeoDataFrame, out_shape: Tuple[int, int], transform) -> np.ndarray:
    shapes_list = [(geom, int(seg_id)) for geom, seg_id in zip(seg_gdf.geometry, seg_gdf["segment_id"])]
    labels = rasterize(
        shapes_list,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False
    )
    return labels


def _stats_for_label(values: np.ndarray, labels: np.ndarray, label: int) -> Dict[str, float]:
    mask = labels == label
    if not np.any(mask):
        return {"mean": np.nan, "std": np.nan, "p10": np.nan, "p50": np.nan, "p90": np.nan}

    v = values[mask].astype(np.float32)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"mean": np.nan, "std": np.nan, "p10": np.nan, "p50": np.nan, "p90": np.nan}

    return {
        "mean": float(np.nanmean(v)),
        "std": float(np.nanstd(v)),
        "p10": float(np.nanpercentile(v, 10)),
        "p50": float(np.nanpercentile(v, 50)),
        "p90": float(np.nanpercentile(v, 90)),
    }


def build_features(output_dir: str, save_csv: bool = False) -> str:
    seg_gdf = _load_segments_gdf(output_dir)
    arr, transform, crs, band_to_idx = _read_multiband(output_dir)

    H, W = arr.shape[1], arr.shape[2]
    labels = _rasterize_segments(seg_gdf, out_shape=(H, W), transform=transform)

    unique_labels = np.array(sorted([int(x) for x in np.unique(labels) if x > 0]), dtype=int)
    if unique_labels.size == 0:
        raise RuntimeError("Rasterização não gerou labels > 0. Verifique CRS e bbox.")

    # ref uma vez
    ref = arr / 10000.0  # (C,H,W)
    bands_ref = {b: ref[band_to_idx[b]] for b in BANDAS_TODAS}
    indices = _compute_indices(ref, band_to_idx)

    rows: List[Dict[str, float]] = []
    for seg_id in unique_labels:
        rec: Dict[str, float] = {"segment_id": int(seg_id)}

        for b in BANDAS_TODAS:
            st = _stats_for_label(bands_ref[b], labels, seg_id)
            for k, v in st.items():
                rec[f"{b}_{k}"] = v

        for name, idx_arr in indices.items():
            st = _stats_for_label(idx_arr, labels, seg_id)
            for k, v in st.items():
                rec[f"{name}_{k}"] = v

        rows.append(rec)

    df = pd.DataFrame(rows)

    os.makedirs(output_dir, exist_ok=True)
    feats_parquet = os.path.join(output_dir, "features.parquet")
    df.to_parquet(feats_parquet, index=False)

    if save_csv:
        feats_csv = os.path.join(output_dir, "features_by_segment.csv")
        df.to_csv(feats_csv, index=False)

    return feats_parquet


def read_features(output_dir: str, limit: Optional[int] = None, segment_id: Optional[int] = None) -> pd.DataFrame:
    feats_parquet = os.path.join(output_dir, "features_by_segment.parquet")
    if not os.path.exists(feats_parquet):
        raise FileNotFoundError("Features ainda não foram geradas. Rode /segmentar primeiro.")

    df = pd.read_parquet(feats_parquet)
    if segment_id is not None:
        df = df[df["segment_id"] == int(segment_id)]
    if limit is not None and limit > 0:
        df = df.head(limit)
    return df
