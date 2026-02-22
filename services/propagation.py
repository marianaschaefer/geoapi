# services/propagation.py — v7.30.0 (ACTIVE LEARNING - ENTROPY & MARGIN)
import os
import pandas as pd
import geopandas as gpd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.semi_supervised import LabelSpreading, SelfTrainingClassifier
from scipy.stats import entropy

def propagate_labels(method, params, output_dir):
    try:
        # 1. CARREGAMENTO DE DADOS
        df_all = pd.read_parquet(os.path.join(output_dir, "features.parquet"))
        gdf_amostras = gpd.read_file(os.path.join(output_dir, "classificado.geojson"))
        
        # Merge para obter o que já foi rotulado pelo usuário
        df_train = pd.merge(df_all, gdf_amostras[['segment_id', 'classe']], on='segment_id')

        # Preparação das matrizes
        X_all = df_all.drop(columns=['segment_id', 'geometry'], errors='ignore')
        y_train = df_train['classe']

        # Mapeamento de classes
        classes_unicas = sorted(y_train.unique())
        cls_map = {cls: i for i, cls in enumerate(classes_unicas)}
        inv_map = {i: cls for cls, i in cls_map.items()}

        # 2. SELEÇÃO DO MODELO E TREINAMENTO
        if "Label Spreading" in method or "Label Propagation" in method:
            clf = LabelSpreading(kernel='knn', alpha=0.2)
            y_semi = np.full(len(df_all), -1)
            mapping = dict(zip(df_train['segment_id'], y_train))
            for i, sid in enumerate(df_all['segment_id']):
                if sid in mapping:
                    y_semi[i] = cls_map[mapping[sid]]
            
            clf.fit(X_all, y_semi)
            df_all['classe_pred'] = [inv_map[i] for i in clf.transduction_]
            # Probabilidades do Grafo
            probs = clf.label_distributions_

        elif "Self-Training" in method:
            base = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
            clf = SelfTrainingClassifier(base, threshold=0.75)
            
            y_semi = np.full(len(df_all), -1, dtype=object)
            mapping = dict(zip(df_train['segment_id'], y_train))
            for i, sid in enumerate(df_all['segment_id']):
                if sid in mapping:
                    y_semi[i] = mapping[sid]
            
            clf.fit(X_all, y_semi)
            df_all['classe_pred'] = clf.predict(X_all)
            # Probabilidades da RF
            probs = clf.predict_proba(X_all)

        # 3. CÁLCULO DAS MEDIDAS DE INCERTEZA (ACTIVE LEARNING)
        # A. Entropia de Shannon (Incerteza Média)
        df_all['entropy'] = entropy(probs.T)
        
        # B. Margin Sampling (Diferença entre as duas classes mais prováveis)
        # Valores baixos indicam que o modelo está indeciso entre duas classes
        if probs.shape[1] >= 2:
            part = np.partition(-probs, 1, axis=1)
            df_all['margin'] = -part[:, 0] - (-part[:, 1])
        else:
            df_all['margin'] = 0

        # Normalização da Incerteza (0-1) para o Mapa
        if df_all['entropy'].max() > 0:
            df_all['uncertainty'] = df_all['entropy'] / df_all['entropy'].max()
        else:
            df_all['uncertainty'] = 0

        # 4. EXPORTAÇÃO FINAL
        path_seg = [f for f in os.listdir(output_dir) if f.startswith("segments")][0]
        gdf_final = gpd.read_file(os.path.join(output_dir, path_seg))
        
        # Merge seguro para manter a ordem dos polígonos
        gdf_final = gdf_final.merge(
            df_all[['segment_id', 'classe_pred', 'uncertainty', 'entropy', 'margin']], 
            on='segment_id', 
            how='left'
        )
        
        output_name = "resultado_ultima_propagacao.geojson"
        gdf_final.to_file(os.path.join(output_dir, output_name), driver="GeoJSON")
        
        return {"status": "sucesso", "output_geojson": output_name}

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"status": "erro", "mensagem": str(e)}