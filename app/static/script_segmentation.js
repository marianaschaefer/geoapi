document.addEventListener("DOMContentLoaded", () => {
    // 1. Inicializa o mapa focado no Brasil
    const map = L.map("map").setView([-15.78, -47.93], 5);
    
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap"
    }).addTo(map);

    // FIX: Garante que o mapa renderize corretamente após o carregamento do CSS
    setTimeout(() => {
        map.invalidateSize();
    }, 300);

    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
        draw: { polyline: false, marker: false, circle: false, circlemarker: false, rectangle: true, polygon: true },
        edit: { featureGroup: drawnItems }
    });
    map.addControl(drawControl);

    let lastAOI = null;
    let ibgeLayer = null;

    map.on(L.Draw.Event.CREATED, (e) => {
        if (ibgeLayer) { map.removeLayer(ibgeLayer); ibgeLayer = null; }
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);
        const geojson = e.layer.toGeoJSON();
        lastAOI = { type: "FeatureCollection", features: [geojson] };
        document.getElementById("ibgeStatus").textContent = "Área desenhada com sucesso.";
    });

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
        }
    });

    const btnSeg = document.getElementById("btnSegmentar");
    btnSeg?.addEventListener("click", async () => {
        const nomeProjeto = document.getElementById("projectName").value.trim();
        const status = document.getElementById("ibgeStatus");
        if (!lastAOI || !nomeProjeto) {
            alert("Preencha o nome do projeto e selecione uma área.");
            return;
        }
        try {
            btnSeg.disabled = true;
            btnSeg.textContent = "PROCESSANDO...";
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
            } else { throw new Error(json.mensagem); }
        } catch (e) {
            alert("Falha: " + e.message);
            btnSeg.disabled = false;
            btnSeg.textContent = "CRIAR E PROCESSAR";
        }
    });
});