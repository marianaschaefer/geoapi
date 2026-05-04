document.addEventListener("DOMContentLoaded", () => {
    // 1. Inicializa o mapa focado no Brasil
    const map = L.map("map").setView([-15.78, -47.93], 5);
    
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap"
    }).addTo(map);

    // Garante renderização correta do container
    setTimeout(() => {
        map.invalidateSize();
    }, 300);

    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
        draw: { 
            polyline: false, 
            marker: false, 
            circle: false, 
            circlemarker: false, 
            rectangle: true, 
            polygon: true 
        },
        edit: { featureGroup: drawnItems }
    });
    map.addControl(drawControl);

    let lastAOI = null;
    let ibgeLayer = null;

    function atualizarLabelCompactness() {
        const algo = document.getElementById("algoritmoSelect")?.value;
        const label = document.getElementById("compactnessLabel");
        const help = document.getElementById("compactnessHelp");
        const input = document.getElementById("compactness");

        if (!label || !help || !input) return;
        label.textContent = "Compactness";
        help.textContent = "Peso espacial do SLIC/SLIC-0.";

        
    }


    // EVENTO: Desenho manual no mapa
    map.on(L.Draw.Event.CREATED, (e) => {
        // Limpa buscas anteriores do IBGE
        if (ibgeLayer) { map.removeLayer(ibgeLayer); ibgeLayer = null; }
        
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);
        
        const geojson = e.layer.toGeoJSON();
        lastAOI = { type: "FeatureCollection", features: [geojson] };
        document.getElementById("ibgeStatus").textContent = "Área desenhada com sucesso.";
    });

    // EVENTO: Busca por localidade (IBGE)
    document.getElementById("btnIBGE")?.addEventListener("click", async () => {
        const tipo = document.getElementById("ibgeType").value;
        const nome = document.getElementById("ibgeSearch").value.trim();
        const status = document.getElementById("ibgeStatus");
        
        if (!nome) return;
        
        status.textContent = "Buscando no IBGE...";
        
        try {
            const resp = await fetch(`/api/ibge/${tipo}/${encodeURIComponent(nome)}`);
            if (!resp.ok) throw new Error("Localidade não encontrada");
            
            const data = await resp.json();
            
            if (data.geojson) {
                // Limpa desenhos manuais anteriores
                drawnItems.clearLayers();
                if (ibgeLayer) map.removeLayer(ibgeLayer);
                
                ibgeLayer = L.geoJSON(data.geojson, { 
                    style: { color: "#187318", weight: 2, fillOpacity: 0.1 } 
                }).addTo(map);
                
                map.fitBounds(ibgeLayer.getBounds());
                lastAOI = data.geojson;
                status.textContent = `✅ ${data.nome} carregado.`;
            }
        } catch (e) {
            status.textContent = "❌ Erro ao buscar localidade no servidor.";
            console.error(e);
        }
    });

    document.getElementById("algoritmoSelect")?.addEventListener("change", atualizarLabelCompactness);
    atualizarLabelCompactness();

    // EVENTO: Disparo do processamento
    const btnSeg = document.getElementById("btnSegmentar");
    btnSeg?.addEventListener("click", async () => {
        const nomeProjeto = document.getElementById("projectName").value.trim();
        
        if (!lastAOI || !nomeProjeto) {
            alert("Preencha o nome do projeto e selecione uma área (desenho ou IBGE).");
            return;
        }

        try {
            btnSeg.disabled = true;
            btnSeg.textContent = "PROCESSANDO...";
            
            // Define o BBOX baseado na camada ativa
            let bounds;
            if (ibgeLayer) {
                bounds = ibgeLayer.getBounds();
            } else if (drawnItems.getLayers().length > 0) {
                bounds = drawnItems.getBounds();
            }

            if (!bounds) throw new Error("Não foi possível calcular os limites da área.");

            const bbox = [
                bounds.getWest(), 
                bounds.getSouth(), 
                bounds.getEast(), 
                bounds.getNorth()
            ];

            const payload = {
                nome_projeto: nomeProjeto,
                bbox: bbox,
                aoi_geojson: lastAOI,
                algoritmo: document.getElementById("algoritmoSelect").value,
                region_px: parseInt(document.getElementById("region_px").value) || 30,
                compactness: parseFloat(document.getElementById("compactness").value) || 1.0,
                sigma: parseFloat(document.getElementById("sigma").value) || 1.0,
                cloud_cover: parseInt(document.getElementById("cloud_cover").value) || 10,
                data_busca: document.getElementById("data_busca").value || null,
                janela_dias: parseInt(document.getElementById("janela_dias").value) || 30
            };

            const resp = await fetch("/api/segmentar", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const json = await resp.json();
            
            if (resp.ok && json.status === "sucesso") {
                // Redireciona para a tela de classificação com timestamp para evitar cache
                window.location.href = `/classification?project_id=${json.project_id}&bbox=${bbox.join(",")}&t=${new Date().getTime()}`;
            } else { 
                throw new Error(json.mensagem || "Erro interno no processamento."); 
            }
        } catch (e) {
            alert("Falha no Processamento: " + e.message);
            btnSeg.disabled = false;
            btnSeg.textContent = "CRIAR E PROCESSAR";
        }
    });
});