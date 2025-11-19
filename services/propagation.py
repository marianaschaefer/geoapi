# services/propagation.py
from __future__ import annotations
import os
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.semi_supervised import LabelSpreading, LabelPropagation
from sklearn.preprocessing import StandardScaler

OUTPUT_DIR = "./SENTINEL2_BANDAS"
FEATS_PATH = os.path.join(OUTPUT_DIR, "features_by_segment.parquet")
LABS_PATH  = os.path.join(OUTPUT_DIR, "classificado.geojson")

# colunas de atributos que costumam existir no features_by_segment.parquet
# ajuste livre conforme seu build_features
DEFAULT_FEATURE_COLS = [
    # Sentinel bands (exemplos comuns; o build_features pode ter nomes levemente diferentes)
    "B02_mean","B03_mean","B04_mean","B05_mean","B06_mean","B07_mean","B08_mean","B11_mean","B12_mean",
    "NDVI_mean","NDSI_mean","EBBI_mean",
]

def _latest_existing(path_prefix: str) -> str | None:
    """Retorna o arquivo mais recente que comece com path_prefix (sem extensão fixa)."""
    if not os.path.isdir(OUTPUT_DIR):
        return None
    cands = []
    for fn in os.listdir(OUTPUT_DIR):
        if fn.startswith(path_prefix) and fn.lower().endswith(".geojson"):
            cands.append(os.path.join(OUTPUT_DIR, fn))
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]

def _encode_labels(series: pd.Series) -> tuple[np.ndarray, dict]:
    """Converte rótulos string -> inteiros {classe: id} e devolve também o mapa."""
    classes = series.fillna("unlabeled").astype(str).str.strip().str.lower()
    uniq = sorted([c for c in classes.unique() if c != "unlabeled"])
    mapping = {c:i for i,c in enumerate(uniq)}
    y = np.full(len(series), -1, dtype=int)
    labeled_mask = classes != "unlabeled"
    y[labeled_mask] = classes[labeled_mask].map(mapping).to_numpy()
    return y, mapping

def _decode_labels(ids: np.ndarray, mapping: dict) -> list[str]:
    inv = {v:k for k,v in mapping.items()}
    return [inv.get(int(i), "unlabeled") for i in ids]

def _pick_feature_cols(df: pd.DataFrame) -> list[str]:
    cols = [c for c in DEFAULT_FEATURE_COLS if c in df.columns]
    # fallback: pega qualquer coluna numérica (exceto segment_id)
    if not cols:
        cols = [c for c in df.columns if c != "segment_id" and pd.api.types.is_numeric_dtype(df[c])]
    return cols

def propagate_labels(method: str = "label_spreading", params: dict | None = None) -> dict:
    """
    Roda Label Spreading/Propagation usando features_by_segment + classificado.geojson.
    Retorna: dict com status, métricas e caminho do geojson salvo.
    """
    params = params or {}

    if not os.path.exists(FEATS_PATH):
        raise FileNotFoundError(f"Features não encontradas: {FEATS_PATH}")
    if not os.path.exists(LABS_PATH):
        raise FileNotFoundError(f"Arquivo de rótulos não encontrado: {LABS_PATH}")

    feats = pd.read_parquet(FEATS_PATH)
    labs  = gpd.read_file(LABS_PATH)

    # normaliza tipos
    feats["segment_id"] = pd.to_numeric(feats["segment_id"], errors="coerce").astype("Int64")
    if "class" in labs.columns and "classe" not in labs.columns:
        labs = labs.rename(columns={"class":"classe"})
    labs["segment_id"] = pd.to_numeric(labs["segment_id"], errors="coerce").astype("Int64")

    # junta
    df = feats.merge(labs[["segment_id", "classe"]], on="segment_id", how="left")
    feature_cols = _pick_feature_cols(df)
    if not feature_cols:
        raise RuntimeError("Nenhuma coluna de feature numérica encontrada para treinar.")

    X = df[feature_cols].astype(float).to_numpy()
    y, mapping = _encode_labels(df["classe"])

    # limpa linhas totalmente inválidas
    valid_rows = np.isfinite(X).all(axis=1)
    X = X[valid_rows]
    y = y[valid_rows]
    segids = df.loc[valid_rows, "segment_id"].to_numpy()

    # padroniza
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    # escolhe modelo
    method = (method or "label_spreading").strip().lower()
    if method == "label_propagation":
        model = LabelPropagation(**{k:v for k,v in params.items() if v is not None})
    else:
        method = "label_spreading"
        model = LabelSpreading(**{k:v for k,v in params.items() if v is not None})

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # silencia warnings numéricos internos
        model.fit(Xs, y)

    y_pred = model.transduction_
    # acurácia interna somente nos rotulados válidos e com >=2 classes
    labeled_mask = y >= 0
    acc = None
    if labeled_mask.sum() >= 2 and len(set(y[labeled_mask])) >= 2:
        acc = float((y_pred[labeled_mask] == y[labeled_mask]).mean())

    # decodifica rótulos e monta GeoDataFrame para salvar
    rotulos = _decode_labels(y_pred, mapping)
    out = pd.DataFrame({"segment_id": segids, "classe_pred": rotulos})
    # junta geometria dos segmentos
    # tentar geojson de segmentos; se não achar, usa o classificado (tem geometria dos selecionados)
    seg_base = os.path.join(OUTPUT_DIR, "segments_slic_compactness05_step200")
    seg_path = seg_base + ".geojson" if os.path.exists(seg_base + ".geojson") else seg_base + ".shp"
    if os.path.exists(seg_path):
        gseg = gpd.read_file(seg_path)
        gseg["segment_id"] = pd.to_numeric(gseg["segment_id"], errors="coerce").astype("Int64")
        gout = gseg.merge(out, on="segment_id", how="left")
    else:
        # fallback: joga só os rotulados (tem geometria em labs)
        gout = labs.merge(out, on="segment_id", how="left")

    # salva GeoJSON com timestamp
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"propagado_{method}_{ts}.geojson"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    gout.to_file(out_path, driver="GeoJSON")

    return {
        "status": "sucesso",
        "method": method,
        "n_total": int(len(segids)),
        "n_labeled": int((y >= 0).sum()),
        "consistency_acc_on_labeled": acc,  # pode ser None se não der p/ calcular
        "classes_": sorted(list(mapping.keys())),
        "output_geojson": out_path,
    }
