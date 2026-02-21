# services/propagation.py — v7.16.6
import os
import pandas as pd
import geopandas as gpd
from sklearn.ensemble import RandomForestClassifier
from sklearn.semi_supervised import LabelSpreading, SelfTrainingClassifier
import numpy as np

def propagate_labels(method, params, output_dir):
    try:
        df_all = pd.read_parquet(os.path.join(output_dir, "features.parquet"))
        gdf_amostras = gpd.read_file(os.path.join(output_dir, "classificado.geojson"))
        df_train = pd.merge(df_all, gdf_amostras[['segment_id', 'classe']], on='segment_id')

        X_train = df_train.drop(columns=['segment_id', 'classe', 'geometry'], errors='ignore')
        y_train = df_train['classe']
        X_all = df_all.drop(columns=['segment_id', 'geometry'], errors='ignore')

        if "Label Spreading" in method or "Label Propagation" in method:
            clf = LabelSpreading(kernel='knn', alpha=0.2)
            classes_unicas = y_train.unique()
            cls_map = {cls: i for i, cls in enumerate(classes_unicas)}
            y_semi = np.full(len(df_all), -1)
            mapping = dict(zip(df_train['segment_id'], y_train))
            for i, sid in enumerate(df_all['segment_id']):
                if sid in mapping: y_semi[i] = cls_map[mapping[sid]]
            clf.fit(X_all, y_semi)
            inv_map = {i: cls for cls, i in cls_map.items()}
            df_all['classe_pred'] = [inv_map[i] for i in clf.transduction_]
        elif "Self-Training" in method:
            base = RandomForestClassifier(n_estimators=50)
            clf = SelfTrainingClassifier(base, threshold=0.75)
            y_semi = np.full(len(df_all), -1, dtype=object)
            mapping = dict(zip(df_train['segment_id'], y_train))
            for i, sid in enumerate(df_all['segment_id']):
                if sid in mapping: y_semi[i] = mapping[sid]
            clf.fit(X_all, y_semi)
            df_all['classe_pred'] = clf.predict(X_all)

        path_seg = [f for f in os.listdir(output_dir) if f.startswith("segments")][0]
        gdf_final = gpd.read_file(os.path.join(output_dir, path_seg))
        gdf_final['classe_pred'] = df_all['classe_pred'].values
        
        output_name = "resultado_ultima_propagacao.geojson"
        gdf_final.to_file(os.path.join(output_dir, output_name), driver="GeoJSON")
        return {"status": "sucesso", "output_geojson": output_name}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}