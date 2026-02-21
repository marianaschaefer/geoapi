# services/samples.py — v7.10.1 (amostras por projeto)
from __future__ import annotations

import os
import pandas as pd

from datetime import datetime
from typing import Iterable, Optional, Tuple, List, Dict, Any

import geopandas as gpd
from shapely.geometry import shape as shp_shape, box


# ---------- utils ----------
def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _segments_candidates(output_dir: str) -> list[str]:
    return [
        os.path.join(output_dir, "segments.geojson"),
        os.path.join(output_dir, "segments.shp"),
        os.path.join(output_dir, "segments_slic_compactness05_step200.geojson"),
        os.path.join(output_dir, "segments_slic_compactness05_step200.shp"),
    ]


def _read_segments(output_dir: str) -> gpd.GeoDataFrame:
    for p in _segments_candidates(output_dir):
        if os.path.exists(p):
            gdf = gpd.read_file(p)
            if "segment_id" not in gdf.columns:
                raise RuntimeError("Camada de segmentos não possui coluna 'segment_id'.")
            return gdf
    raise FileNotFoundError("Camada de segmentos não encontrada no projeto (nem .geojson nem .shp).")


def _samples_path(output_dir: str) -> str:
    return os.path.join(output_dir, "samples.geojson")


def _empty_samples(crs=None) -> gpd.GeoDataFrame:
    cols = ["segment_id", "classe", "usuario", "ts", "geometry"]
    return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=crs)


def _read_samples(output_dir: str) -> gpd.GeoDataFrame:
    path = _samples_path(output_dir)
    if os.path.exists(path):
        gdf = gpd.read_file(path)
        return gdf.reset_index(drop=True)

    # se ainda não existe, tenta herdar CRS dos segmentos
    try:
        seg = _read_segments(output_dir)
        return _empty_samples(seg.crs)
    except Exception:
        return _empty_samples()


def _atomic_write_geojson(gdf: gpd.GeoDataFrame, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    gdf.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, path)


# ---------- API de alto nível ----------
def upsert_samples_from_features(output_dir: str, features: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Recebe uma lista de GeoJSON Features.
    Cada feature deve ter properties com ao menos: segment_id, classe.
    Se não vier geometry, busca a geometria na camada de segmentos do projeto.
    UPSERT por segment_id.
    Retorna (criadas, atualizadas).
    """
    if not isinstance(features, list):
        raise ValueError("Payload inválido: 'features' deve ser uma lista de GeoJSON features.")

    seg = _read_segments(output_dir).reset_index(drop=True)
    seg["segment_id"] = pd.to_numeric(seg["segment_id"], errors="coerce").astype("Int64")

    seg_idx = {}
    for i, sid in enumerate(seg["segment_id"].tolist()):
        if pd.notna(sid):
            seg_idx[int(sid)] = i

    samples = _read_samples(output_dir).reset_index(drop=True)
    if "segment_id" in samples.columns:
        samples["segment_id"] = pd.to_numeric(samples["segment_id"], errors="coerce").astype("Int64")

    existing_idx = {}
    if len(samples) > 0 and "segment_id" in samples.columns:
        for i, sid in enumerate(samples["segment_id"].tolist()):
            if pd.notna(sid):
                existing_idx[int(sid)] = i

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

        geom = None
        if feat.get("geometry"):
            geom = shp_shape(feat["geometry"])
        else:
            pos = seg_idx.get(seg_id)
            if pos is None:
                raise ValueError(f"segment_id {seg_id} não encontrado na camada de segmentos.")
            geom = seg.iloc[pos].geometry

        rows.append(dict(segment_id=seg_id, classe=str(classe), usuario=(usuario or None), ts=ts, geometry=geom))

    if len(samples) == 0:
        samples = gpd.GeoDataFrame(rows, geometry="geometry", crs=seg.crs).reset_index(drop=True)
        created = len(rows)
    else:
        for row in rows:
            sid = int(row["segment_id"])
            if sid in existing_idx:
                idx = existing_idx[sid]
                for k, v in row.items():
                    samples.at[idx, k] = v
                updated += 1
            else:
                samples = gpd.GeoDataFrame(
                    pd.concat([samples, gpd.GeoDataFrame([row], geometry="geometry", crs=samples.crs)], ignore_index=True),
                    geometry="geometry",
                    crs=samples.crs
                )
                samples = samples.reset_index(drop=True)
                existing_idx[sid] = len(samples) - 1
                created += 1

    _atomic_write_geojson(samples, _samples_path(output_dir))
    return created, updated


def upsert_samples_from_ids(output_dir: str, amostras: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Alternativa sem GeoJSON:
    Recebe [{"segment_id":123, "classe":"Urbanizado", "usuario":"maria"}...]
    Busca geometria na camada de segmentos e faz UPSERT.
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
            "geometry": None
        })
    return upsert_samples_from_features(output_dir=output_dir, features=feats)


def list_samples(output_dir: str, classe: Optional[str] = None, bbox: Optional[Iterable[float]] = None) -> gpd.GeoDataFrame:
    """
    Retorna amostras do projeto, com filtros opcionais:
    - classe: string exata
    - bbox: [minx,miny,maxx,maxy] (assume CRS compatível com as geometrias)
    """
    gdf = _read_samples(output_dir)
    if len(gdf) == 0:
        return gdf

    if classe:
        gdf = gdf[gdf["classe"] == classe]

    if bbox and len(list(bbox)) == 4:
        minx, miny, maxx, maxy = map(float, bbox)
        gdf = gdf[gdf.intersects(box(minx, miny, maxx, maxy))]

    return gdf


def delete_sample(output_dir: str, segment_id: int) -> bool:
    """Remove a amostra pelo segment_id (no projeto)."""
    gdf = _read_samples(output_dir)
    if len(gdf) == 0:
        return Fals# services/samples.py — project-aware (por projeto)

import os
from datetime import datetime
from typing import Iterable, Optional, Tuple, List, Dict, Any

import pandas as pd
import geopandas as gpd
from shapely.geometry import shape as shp_shape, box


# ---------- utils ----------
def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _project_paths(output_dir: str) -> Dict[str, str]:
    """
    output_dir = pasta do projeto (ex: SENTINEL2_BANDAS/projects/<id>)
    """
    out_dir = os.path.abspath(output_dir)
    samples_path = os.path.join(out_dir, "samples.geojson")

    seg_geojson = os.path.join(out_dir, "segments.geojson")
    legacy_geojson = os.path.join(out_dir, "segments_slic_compactness05_step200.geojson")
    seg_shp = os.path.join(out_dir, "segments.shp")
    legacy_shp = os.path.join(out_dir, "segments_slic_compactness05_step200.shp")

    return {
        "out_dir": out_dir,
        "samples": samples_path,
        "seg_geojson": seg_geojson,
        "legacy_geojson": legacy_geojson,
        "seg_shp": seg_shp,
        "legacy_shp": legacy_shp,
    }


def _read_segments(output_dir: str) -> gpd.GeoDataFrame:
    p = _project_paths(output_dir)
    for fp in (p["seg_geojson"], p["legacy_geojson"], p["seg_shp"], p["legacy_shp"]):
        if os.path.exists(fp):
            gdf = gpd.read_file(fp)
            if "segment_id" not in gdf.columns:
                raise RuntimeError("Camada de segmentos não possui coluna 'segment_id'.")
            return gdf
    raise FileNotFoundError("Camada de segmentos não encontrada (rode /segmentar primeiro).")


def _empty_samples(crs=None) -> gpd.GeoDataFrame:
    cols = ["segment_id", "classe", "usuario", "ts", "geometry"]
    return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=crs)


def _read_samples(output_dir: str) -> gpd.GeoDataFrame:
    p = _project_paths(output_dir)
    if os.path.exists(p["samples"]):
        return gpd.read_file(p["samples"])

    # se ainda não existe, tenta herdar CRS da segmentação
    try:
        seg = _read_segments(output_dir)
        return _empty_samples(seg.crs)
    except Exception:
        return _empty_samples()


def _atomic_write_geojson(gdf: gpd.GeoDataFrame, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    # geopandas pode criar diretório se der tmp sem extensão;
    # então mantemos .geojson no tmp também:
    if not tmp.lower().endswith(".geojson.tmp"):
        tmp = path + ".geojson.tmp"
    gdf.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, path)


# ---------- API de alto nível ----------
def upsert_samples_from_features(output_dir: str, features: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    UPSERT por segment_id em samples.geojson do projeto.
    Cada feature deve ter properties: segment_id, classe.
    Se geometry não vier, busca no layer de segmentos do projeto.
    """
    if not isinstance(features, list):
        raise ValueError("Payload inválido: 'features' deve ser uma lista de GeoJSON features.")

    seg = _read_segments(output_dir)
    seg_ids = pd.to_numeric(seg["segment_id"], errors="coerce").astype("Int64")
    seg = seg.assign(segment_id=seg_ids)
    seg_idx = {int(r["segment_id"]): i for i, r in seg[["segment_id"]].dropna().reset_index(drop=True).iterrows()}

    samples = _read_samples(output_dir)
    if len(samples) > 0:
        samples["segment_id"] = pd.to_numeric(samples["segment_id"], errors="coerce").astype("Int64")

    existing_idx = {}
    if len(samples) > 0 and "segment_id" in samples.columns:
        existing_idx = {int(r["segment_id"]): i for i, r in samples[["segment_id"]].dropna().reset_index(drop=True).iterrows()}

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
        if feat.get("geometry"):
            geom = shp_shape(feat["geometry"])
        else:
            pos = seg_idx.get(seg_id)
            if pos is None:
                raise ValueError(f"segment_id {seg_id} não encontrado na camada de segmentos.")
            geom = seg.iloc[pos].geometry

        rows.append(dict(
            segment_id=seg_id,
            classe=str(classe).strip().lower(),
            usuario=(str(usuario).strip() if usuario else None),
            ts=ts,
            geometry=geom
        ))

    if len(samples) == 0:
        samples = gpd.GeoDataFrame(rows, geometry="geometry", crs=seg.crs)
        created = len(rows)
    else:
        for row in rows:
            sid = int(row["segment_id"])
            if sid in existing_idx:
                idx = existing_idx[sid]
                for k, v in row.items():
                    samples.at[idx, k] = v
                updated += 1
            else:
                samples = gpd.GeoDataFrame(
                    pd.concat([samples, gpd.GeoDataFrame([row], geometry="geometry", crs=samples.crs)], ignore_index=True),
                    geometry="geometry",
                    crs=samples.crs
                )
                existing_idx[sid] = len(samples) - 1
                created += 1

    out_path = _project_paths(output_dir)["samples"]
    _atomic_write_geojson(samples, out_path)
    return created, updated


def upsert_samples_from_ids(output_dir: str, amostras: List[Dict[str, Any]]) -> Tuple[int, int]:
    """
    Recebe [{"segment_id":123, "classe":"mata", "usuario":"maria"}...]
    Busca geometria no layer de segmentos do projeto e faz UPSERT.
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
            "geometry": None
        })
    return upsert_samples_from_features(output_dir, feats)


def list_samples(output_dir: str, classe: Optional[str] = None, bbox: Optional[Iterable[float]] = None) -> gpd.GeoDataFrame:
    """
    Lista samples.geojson do projeto com filtros opcionais.
    bbox: [minx,miny,maxx,maxy] no CRS do dado (no seu caso, EPSG:4326).
    """
    gdf = _read_samples(output_dir)
    if len(gdf) == 0:
        return gdf

    if classe:
        gdf = gdf[gdf["classe"] == str(classe).strip().lower()]

    if bbox and len(bbox) == 4:
        minx, miny, maxx, maxy = map(float, bbox)
        gdf = gdf[gdf.intersects(box(minx, miny, maxx, maxy))]

    return gdf


def delete_sample(output_dir: str, segment_id: int) -> bool:
    gdf = _read_samples(output_dir)
    if len(gdf) == 0:
        return False

    antes = len(gdf)
    gdf["segment_id"] = pd.to_numeric(gdf["segment_id"], errors="coerce").astype("Int64")
    gdf = gdf[gdf["segment_id"] != int(segment_id)]

    if len(gdf) < antes:
        out_path = _project_paths(output_dir)["samples"]
        _atomic_write_geojson(gdf, out_path)
        return True
    return False

    antes = len(gdf)
    gdf["segment_id"] = pd.to_numeric(gdf["segment_id"], errors="coerce").astype("Int64")
    gdf = gdf[gdf["segment_id"] != int(segment_id)]
    if len(gdf) < antes:
        _atomic_write_geojson(gdf, _samples_path(output_dir))
        return True
    return False
