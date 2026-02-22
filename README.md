---GeoApi v2.0 — Segmentação e Aprendizado Semi-Supervisionado Interativo---

Aplicação Web SIG (Sistema de Informações Geográficas) integrada ao Sentinel-2, projetada para segmentação de imagens e classificação semi-supervisionada em ciclo iterativo human-in-the-loop.


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
# ou: .venv\Scripts\activate  # Windows
pip install -r requirements.txt

Inicie a aplicação:
python run.py
Acesse no navegador: http://localhost:5000

----Metodologia Científica
O fluxo de trabalho da ferramenta segue os padrões de sensoriamento remoto para classificação baseada em objetos (OBIA):

Segmentação: Utiliza o algoritmo SLIC (Simple Linear Iterative Clustering) ou ASA para agrupar pixels em superpixels homogêneos.

Composição de Bandas: Permite visualização em Cor Real (RGB), Falsa Cor Vegetação (B8-B4-B3) e Solo/Urbano (B11-B8-B2) para facilitar a interpretação do analista.

Classificação: Através da interface, o usuário define classes e atribui rótulos a segmentos selecionados. O modelo de ML propaga essas classes para os demais segmentos baseando-se na similaridade dos atributos espectrais extraídos.

----Estrutura de Dados (Exportação)
Ao final do processo, o projeto gera na pasta SENTINEL2_BANDAS/projects/<id>:

segments.geojson: Vetores da segmentação.

features.parquet: Banco de dados tabular com todos os atributos estatísticos de cada segmento.

classificado.geojson: Amostras coletadas manualmente com persistência de cores.

resultado_ultima_propagacao.geojson: Mapa temático final gerado pelo modelo de ML.