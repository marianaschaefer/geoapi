---GeoApi v3.0 — Segmentação, Classificação e Active Learning---

Aplicação Web SIG (Sistema de Informações Geográficas) integrada ao Sentinel-2, projetada para segmentação de imagens e classificação semi-supervisionada em ciclo iterativo human-in-the-loop.

Novidades da v3.0
Active Learning: Implementação de Entropia de Shannon para sugerir áreas de coleta de amostras onde o modelo possui menor confiança.

Exportação Multi-formato: Suporte nativo para download de amostras e mapas finais em Shapefile, GeoJSON, CSV e GeoTIFF.

Cores das classes: Dicionário de classes inteligente que mantém a consistência de cores entre sessões de coletas de amostras.

-------

Integração Sentinel-2: Download automático de bandas via STAC API (Earth Search).

Active Learning: Ciclo interativo de amostragem, treinamento e correção de rótulos em tempo real.

Extração de Atributos: Geração de estatísticas de bandas (média, desvio padrão) e índices espectrais (NDVI, EVI, NDSI, NDBI) salvos em formato Apache Parquet.

ML Semi-Supervisionado: Implementação de algoritmos do sklearn.semi_supervised (LabelPropagation, LabelSpreading e SelfTrainingClassifier) para propagação de rótulos.

Integração IBGE: Busca automática de malhas territoriais oficiais de municípios e estados brasileiros para definição de área de estudo (AOI).

--Como Rodar o Projeto
Pré-requisitos:
Python 3.12+
Bibliotecas Geoespaciais (GDAL/GEOS) instaladas no sistema.
Conexão com internet (para download das imagens de satélite).

--Instalação e Execução
Clone o repositório:
git clone https://github.com/marianaschaefer/geoapi.git
cd geoapi

Configure o ambiente virtual:
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
ou: .venv\Scripts\activate  # Windows
pip install -r requirements.txt

Inicie a aplicação:
python run.py
Acesse no navegador: http://localhost:5000

----Metodologia
O fluxo de trabalho da ferramenta segue os padrões de sensoriamento remoto para classificação baseada em objetos (OBIA):

Segmentação: Utiliza o algoritmo SLIC (Simple Linear Iterative Clustering) ou ASA para agrupar pixels em superpixels homogêneos.

Composição de Bandas: Permite visualização em Cor Real (RGB), Falsa Cor Vegetação (B8-B4-B3) e Solo/Urbano (B11-B8-B2) para facilitar a interpretação do analista.

Ciclo Active Learning:  
-O usuário coleta amostras iniciais.
-O modelo propaga os rótulos e calcula a Entropia (Incerteza).
-O sistema destaca polígonos com bordas tracejadas (vermelho/laranja) sugerindo novas coletas onde o modelo está "confuso".
-O usuário valida ou re-rotula, fechando o ciclo.

----Estrutura de Dados (Exportação)
Ao final do processo, o projeto gera na pasta SENTINEL2_BANDAS/projects/<id>:

segments.geojson: Vetores da segmentação original.
features.parquet / .csv: Banco de dados tabular com atributos estatísticos.
classificado.geojson / .shp: Amostras coletadas (Expert knowledge).
mapa_final.geojson / .zip (SHP): Classificação temática final com metadados de incerteza.