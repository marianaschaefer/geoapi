document.addEventListener("DOMContentLoaded", () => {
    const map = L.map("map").setView([-15.78, -47.93], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap"
    }).addTo(map);

    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
        draw: { polyline: false, marker: false, circle: false, circlemarker: false, rectangle: true, polygon: true },
        edit: { featureGroup: drawnItems }
    });
    map.addControl(drawControl);

    let lastAOI = null;
    let ibgeLayer = null;

    // Captura o desenho manual
    map.on(L.Draw.Event.CREATED, (e) => {
        if (ibgeLayer) { map.removeLayer(ibgeLayer); ibgeLayer = null; }
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);
        
        const geojson = e.layer.toGeoJSON();
        lastAOI = { type: "FeatureCollection", features: [geojson] };
        document.getElementById("ibgeStatus").textContent = "Área desenhada com sucesso.";
    });

    // Busca IBGE
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
                drawnItems.clearLayers();
                if (ibgeLayer) map.removeLayer(ibgeLayer);

                ibgeLayer = L.geoJSON(data.geojson, { style: { color: "#187318", weight: 2, fillOpacity: 0.1 } }).addTo(map);
                map.fitBounds(ibgeLayer.getBounds());
                lastAOI = data.geojson;
                status.textContent = `✅ ${data.nome} carregado.`;
            }
        } catch (e) {
            status.textContent = "❌ Erro ao buscar localidade.";
            console.error(e);
        }
    });

    // Processamento
    const btnSeg = document.getElementById("btnSegmentar");
    btnSeg?.addEventListener("click", async () => {
        const nomeProjeto = document.getElementById("projectName").value.trim();
        const status = document.getElementById("ibgeStatus");

        if (!lastAOI) {
            alert("Erro: Desenhe uma área ou use a busca do IBGE antes de processar.");
            return;
        }
        if (!nomeProjeto) {
            alert("Erro: Digite um nome para o seu projeto.");
            return;
        }

        try {
            // Feedback visual de processamento
            btnSeg.disabled = true;
            btnSeg.textContent = "PROCESSANDO (Aguarde...)";
            status.textContent = "🛰️ Baixando imagens e segmentando... isso pode levar 1-2 minutos.";

            // Calcula o BBOX a partir da camada ativa
            const bounds = ibgeLayer ? ibgeLayer.getBounds() : drawnItems.getBounds();
            const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];

            const payload = {
                nome_projeto: nomeProjeto,
                bbox: bbox,
                aoi_geojson: lastAOI,
                algoritmo: document.getElementById("algoritmoSelect").value
            };

            const resp = await fetch("/api/segmentar", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const json = await resp.json();

            if (resp.ok && json.status === "sucesso") {
                window.location.href = `/classification?project_id=${json.project_id}&bbox=${bbox.join(",")}&t=${new Date().getTime()}`;
            } else {
                throw new Error(json.mensagem || "Erro interno no servidor.");
            }

        } catch (e) {
            alert("Falha no Processamento: " + e.message);
            btnSeg.disabled = false;
            btnSeg.textContent = "CRIAR E PROCESSAR";
            status.textContent = "❌ Falha ao processar.";
        }
    });
});