document.addEventListener("DOMContentLoaded", async function () {
    const params = new URLSearchParams(window.location.search);
    const bboxStr = params.get('bbox');
    const bbox = bboxStr.split(',').map(parseFloat);
    const bounds = [[bbox[1], bbox[0]], [bbox[3], bbox[2]]];

    const map = L.map('map').fitBounds(bounds);

    let layerAtual = null;
    let geojsonLayer = null;
    let selectedPolygons = {};
    let classesCriadas = {}; 

    // Função para atualizar a lista de classes no frontend
    function atualizarListaClasses() {
        const ul = document.getElementById("class-list");
        ul.innerHTML = "";
        Object.entries(classesCriadas).forEach(([nome, cor]) => {
            const li = document.createElement("li");

            // Quadradinho da cor
            const colorBox = document.createElement("span");
            colorBox.className = "color-box";
            colorBox.style.backgroundColor = cor;
            li.appendChild(colorBox);

            // Nome da classe em preto
            const nameSpan = document.createElement("span");
            nameSpan.className = "class-name";
            nameSpan.textContent = nome;
            li.appendChild(nameSpan);

            // Botão de remover
            const delBtn = document.createElement("button");
            delBtn.textContent = "X";
            delBtn.onclick = () => {
                delete classesCriadas[nome];
                atualizarListaClasses();

                geojsonLayer.eachLayer(layer => {
                    if (layer.feature.properties.class === nome) {
                        delete layer.feature.properties.class;
                        layer.setStyle({ color: 'yellow' });
                    }
                });
            };
            li.appendChild(delBtn);

            ul.appendChild(li);
        });

    }

    async function carregarBanda(valor) {
        try {
            if (layerAtual) {
                map.removeLayer(layerAtual);
                layerAtual = null;
            }

            if (valor === "original") {
                layerAtual = L.tileLayer(
                    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
                ).addTo(map);
            } else {
                const response = await fetch('/bandas/' + valor);
                const arrayBuffer = await response.arrayBuffer();
                const georaster = await parseGeoraster(arrayBuffer);

                layerAtual = new GeoRasterLayer({
                    georaster,
                    opacity: 1,
                    resolution: 256
                }).addTo(map);

                map.fitBounds(layerAtual.getBounds());
            }
        } catch (error) {
            console.error("Erro ao carregar a banda:", error);
        }
    }

    await carregarBanda(document.getElementById("band-options").value);
    document.getElementById("band-options").addEventListener("change", function (e) {
        carregarBanda(e.target.value);
    });

    fetch('/resultado_geojson')
        .then(res => res.json())
        .then(data => {
            geojsonLayer = L.geoJSON(data, {
                style: function (feature) {
                    return {
                        color: feature.properties.color || 'yellow',
                        weight: 1,
                        fillOpacity: 0.2
                    };
                },
                onEachFeature: function (feature, layer) {
                    layer.on("click", function () {
                        const id = feature.properties.segment_id;

                        if (selectedPolygons[id]) {
                            delete selectedPolygons[id];
                            layer.setStyle({ color: feature.properties.color || 'yellow' }); // cor original
                        } else {
                            selectedPolygons[id] = feature;
                            layer.setStyle({ color: 'red' });
                        }
                    });

                }
            }).addTo(map);
        })
        .catch(err => console.error("Erro ao carregar GeoJSON:", err));

    // Função para aplicar classe aos selecionados 
    window.applyClass = function () {
    const className = document.getElementById("className").value.trim();
    const classColor = document.getElementById("classColor").value;

    if (!className) {
        alert("Digite o nome da classe!");
        return;
    }

    // Verifica se a classe já existe
    if (classesCriadas[className]) {
        // Caso já exista, mantém a cor original
        const originalColor = classesCriadas[className];
        alert(`A classe "${className}" já existe e não pode ter sua cor alterada.`);

        // Aplica a classe mantendo a cor original
        Object.values(selectedPolygons).forEach(feature => {
            feature.properties.class = className;
            feature.properties.color = originalColor;

            geojsonLayer.eachLayer(layer => {
                if (layer.feature.properties.segment_id === feature.properties.segment_id) {
                    layer.setStyle({ color: originalColor });
                }
            });
        });

    } else {
        // Cria nova classe
        classesCriadas[className] = classColor;
        atualizarListaClasses();

        Object.values(selectedPolygons).forEach(feature => {
            feature.properties.class = className;
            feature.properties.color = classColor;

            geojsonLayer.eachLayer(layer => {
                if (layer.feature.properties.segment_id === feature.properties.segment_id) {
                    layer.setStyle({ color: classColor });
                }
            });
        });
    }

    selectedPolygons = {}; // limpa seleção
};

    // Função para salvar no backend 
    window.salvar = function () {
        // Filtra apenas os polígonos classificados
        const data = {
            type: "FeatureCollection",
            features: geojsonLayer.toGeoJSON().features.filter(feature => feature.properties.class)
        };

        fetch("/salvar_classificacao", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        })
            .then(r => r.json())
            .then(res => alert("Classificação salva!"))
            .catch(err => console.error("Erro ao salvar:", err));
    };
});