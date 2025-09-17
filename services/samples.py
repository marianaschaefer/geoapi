# services/samples.py
import os
import json
from datetime import datetime
from typing import Iterable, Optional, Tuple, List, Dict, Any

import geopandas as gpd
from shapely.geometry import shape as shp_shape, box

BASE_DIR = "./SENTINEL2_BANDAS"
SAMPLES_PATH = os.path.join(BASE_DIR, "samples.geojson")
# caminho padrão da segmentação (ajuste se você trocar o nome do arquivo)
SEGMENTS_BASE = os.path.join(BASE_DIR, "segments_slic_compactness05_step200")
SEGMENTS_GEOJSON = SEGMENTS_BASE + ".geojson"
SEGMENTS_SHP = SEGMENTS_BASE + ".shp"


# ---------- utils ----------
def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_segments(path_geojson: str = SEGMENTS_GEOJSON, path_shp: str = SEGMENTS_SHP) -> gpd.GeoDataFrame:
    for p in (path_geojson, path_shp):
        if os.path.exists(p):
            gdf = gpd.read_file(p)
            if "segment_id" not in gdf.columns:
                raise RuntimeError("Camada de segmentos não possui coluna 'segment_id'.")
            return gdf
    raise FileNotFoundError("Camada de segmentos não encontrada (nem .geojson nem .shp).")


def _empty_samples(crs=None) -> gpd.GeoDataFrame:
    cols = ["segment_id", "classe", "usuario", "ts", "geometry"]
    return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=crs)


def _read_samples() -> gpd.GeoDataFrame:
    if os.path.exists(SAMPLES_PATH):
        return gpd.read_file(SAMPLES_PATH)
    # se ainda não existe, tentamos herdar o CRS da segmentação
    try:
        seg = _read_segments()
        return _empty_samples(seg.crs)
    except Exception:
        return _empty_samples()


def _atomic_write_geojson(gdf: gpd.GeoDataFrame, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    gdf.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, path)


# ---------- API de alto nível ----------
def upsert_samples_from_features(features: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Recebe uma lista de GeoJSON Features.
    Cada feature deve ter properties com ao menos: segment_id, classe.
    Se não vier geometry, buscamos a geometria na camada de segmentos.
    Faz UPSERT por segment_id (atualiza se existir, cria se não).
    Retorna (criadas, atualizadas).
    """
    if not isinstance(features, list):
        raise ValueError("Payload inválido: 'features' deve ser uma lista de GeoJSON features.")

    seg = _read_segments()
    seg_idx = {int(r["segment_id"]): i for i, r in seg[["segment_id"]].reset_index(drop=True).iterrows()}

    samples = _read_samples()
    # índice auxiliar por segment_id existente
    existing_idx = {int(r["segment_id"]): i for i, r in samples[["segment_id"]].reset_index(drop=True).iterrows()}

    created = 0
    updated = 0

    rows = []
    for feat in features:
        props = feat.get("properties", {}) or {}
        seg_id = props.get("segment_id")
        classe = props.get("classe")

        if seg_id is None or classe is None:
            raise ValueError("Cada feature deve conter 'properties.segment_id' e 'properties.classe'.")

        seg_id = int(seg_id)
        usuario = props.get("usuario")
        ts = props.get("ts") or _now_iso()

        # geometria: pega da feature ou busca na camada de segmentos
        geom = None
        if feat.get("geometry"):
            geom = shp_shape(feat["geometry"])
        else:
            pos = seg_idx.get(seg_id)
            if pos is None:
                raise ValueError(f"segment_id {seg_id} não encontrado na camada de segmentos.")
            geom = seg.iloc[pos].geometry

        rows.append(dict(segment_id=seg_id, classe=str(classe), usuario=(usuario or None), ts=ts, geometry=geom))

    # aplica upsert em memória
    if len(samples) == 0:
        samples = gpd.GeoDataFrame(rows, geometry="geometry", crs=seg.crs)
        created = len(rows)
    else:
        for row in rows:
            seg_id = int(row["segment_id"])
            if seg_id in existing_idx:
                # update
                idx = existing_idx[seg_id]
                for k, v in row.items():
                    samples.at[idx, k] = v
                updated += 1
            else:
                # append
                samples = gpd.GeoDataFrame(pd.concat([samples, gpd.GeoDataFrame([row], geometry="geometry", crs=samples.crs)], ignore_index=True))
                existing_idx[seg_id] = len(samples) - 1
                created += 1

    _atomic_write_geojson(samples, SAMPLES_PATH)
    return created, updated


def upsert_samples_from_ids(amostras: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Alternativa sem GeoJSON:
    Recebe [{"segment_id":123, "classe":"Urbanizado", "usuario":"maria"}...]
    Busca a geometria correspondente na camada de segmentos e faz UPSERT.
    """
    feats = []
    for a in amostras:
        if "segment_id" not in a or "classe" not in a:
            raise ValueError("Cada amostra deve ter 'segment_id' e 'classe'.")
        feats.append({
            "type": "Feature",
            "properties": {
                "segment_id": int(a["segment_id"]),
                "classe": a["classe"],
                "usuario": a.get("usuario"),
                "ts": a.get("ts"),
            },
            "geometry": None  # será resolvida pelo upsert
        })
    return upsert_samples_from_features(feats)


def list_samples(classe: Optional[str] = None, bbox: Optional[Iterable[float]] = None) -> gpd.GeoDataFrame:
    """
    Retorna as amostras como GeoDataFrame, com filtros opcionais:
    - classe: string exata
    - bbox: [minx,miny,maxx,maxy] no mesmo CRS da camada (normalmente WGS84)
    """
    gdf = _read_samples()
    if len(gdf) == 0:
        return gdf

    if classe:
        gdf = gdf[gdf["classe"] == classe]

    if bbox and len(bbox) == 4:
        minx, miny, maxx, maxy = map(float, bbox)
        gdf = gdf[gdf.intersects(box(minx, miny, maxx, maxy))]

    return gdf


def delete_sample(segment_id: int) -> bool:
    """Remove a amostra pelo segment_id. Retorna True se removeu algo."""
    gdf = _read_samples()
    if len(gdf) == 0:
        return False
    antes = len(gdf)
    gdf = gdf[gdf["segment_id"] != int(segment_id)]
    if len(gdf) < antes:
        _atomic_write_geojson(gdf, SAMPLES_PATH)
        return True
    return False
