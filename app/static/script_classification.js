document.addEventListener("DOMContentLoaded", async function () {
    const projectId = window.__PROJECT_ID__;
    if (!projectId) return;

    const params = new URLSearchParams(window.location.search);
    const bboxStr = params.get("bbox");
    const bbox = bboxStr ? bboxStr.split(",").map(parseFloat) : null;
    
    // 1. INICIALIZAÇÃO DO MAPA E LAYERS
    const map = L.map("map").setView([-15.78, -47.93], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map);

    const forceResize = () => {
        map.invalidateSize();
        if (bbox) map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]]);
    };
    setTimeout(forceResize, 250);

    const sentinelLayerGroup = L.layerGroup().addTo(map);
    let segmentsLayer = null; 
    
    // Estados da Aplicação
    const classBySegment = new Map();
    const classesCriadas = {}; 
    const selectedIds = new Set();
    let propagatedById = new Map();
    let uncertaintyMap = new Map();
    
    let showPropagated = false;
    let showUncertainty = false;

    // --- 2. GESTÃO DE CORES E UI ---

    // Recupera cor automaticamente ao digitar nome existente
    document.getElementById("className")?.addEventListener("input", (e) => {
        const nome = e.target.value.trim().toLowerCase();
        if (classesCriadas[nome]) {
            document.getElementById("classColor").value = classesCriadas[nome];
        }
    });

    function colorForClass(nome) {
        const n = String(nome).trim().toLowerCase();
        return classesCriadas[n] || "#ffcc00";
    }

    function atualizarListaClasses() {
        const ul = document.getElementById("class-list");
        if (!ul) return;
        ul.innerHTML = "";
        Object.entries(classesCriadas).forEach(([nome, cor]) => {
            const li = document.createElement("li");
            li.style.display = "flex"; li.style.alignItems = "center"; li.style.gap = "8px"; li.style.marginBottom = "5px";
            li.innerHTML = `<span style="width:12px; height:12px; background:${cor}; border:1px solid #000; border-radius:2px;"></span> 
                            <span style="text-transform:capitalize;">${nome}</span>`;
            ul.appendChild(li);
        });
    }

    // --- 3. LÓGICA DE ESTILO VETORIAL (ACTIVE LEARNING) ---

    function styleFeature(feature) {
        const sid = feature.properties.segment_id;
        const manual = classBySegment.get(sid);
        const prop = showPropagated ? propagatedById.get(sid) : null;
        const uValue = uncertaintyMap.get(sid) || 0;

        let style = {
            weight: 0.8,
            color: "#333",
            fillOpacity: 0.1,
            fillColor: "#ccc",
            dashArray: null
        };

        // Regra 1: Amostra Coletada (Preenchimento Sólido)
        if (manual) {
            style.fillColor = manual.color;
            style.fillOpacity = 0.8;
            style.weight = 1.5;
            style.color = "#000";
        } 
        // Regra 2: Resultado ML (Preenchimento Médio)
        else if (showPropagated && prop) {
            style.fillColor = colorForClass(prop);
            style.fillOpacity = 0.5;
        }

        // Regra 3: Sugestão AL (BORDA GROSSA E TRACEJADA - Sem cor de fundo)
        // Isso permite ver o satélite por baixo enquanto destaca a dúvida do modelo
        if (showUncertainty && uValue > 0.6 && !manual) {
            style.color = uValue > 0.85 ? "#ff0000" : "#ff8c00"; 
            style.weight = 3.5;
            style.dashArray = "5, 5";
            if (!manual && !prop) style.fillOpacity = 0; 
        }

        // Regra 4: Seleção (Destaque Ciano)
        if (selectedIds.has(sid)) {
            style.color = "#00ffff";
            style.weight = 4;
            style.fillOpacity = 0.7;
        }

        return style;
    }

    // --- 4. CARREGAMENTO E SINCRONIZAÇÃO ---

    async function loadData() {
        try {
            const r = await fetch(`/resultado_geojson?project_id=${projectId}`);
            const gj = await r.json();
            
            const rA = await fetch(`/resultado_propagado?project_id=${projectId}&path=classificado.geojson`);
            if (rA.ok) {
                const gjA = await rA.json();
                gjA.features.forEach(f => {
                    const sid = f.properties.segment_id;
                    const cl = f.properties.classe;
                    if (sid != null && cl) {
                        classesCriadas[cl] = f.properties.cor || "#ffcc00";
                        classBySegment.set(sid, { name: cl, color: classesCriadas[cl] });
                    }
                });
                atualizarListaClasses();
            }

            if (segmentsLayer) map.removeLayer(segmentsLayer);
            segmentsLayer = L.geoJSON(gj, {
                style: styleFeature,
                onEachFeature: (f, l) => {
                    l.on("click", () => {
                        const sid = f.properties.segment_id;
                        if (selectedIds.has(sid)) selectedIds.delete(sid);
                        else selectedIds.add(sid);
                        segmentsLayer.eachLayer(ly => ly.setStyle(styleFeature(ly.feature)));
                    });
                }
            }).addTo(map);
        } catch (e) { console.error("Erro loadData:", e); }
    }

    // --- 5. EVENTOS DE INTERFACE ---

    document.getElementById("btnApplyClass")?.addEventListener("click", () => {
        const name = document.getElementById("className").value.trim().toLowerCase();
        const color = document.getElementById("classColor").value;
        if (!name || selectedIds.size === 0) return;

        classesCriadas[name] = color;
        selectedIds.forEach(id => classBySegment.set(id, { name, color }));
        
        atualizarListaClasses();
        segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
        selectedIds.clear();
    });

    document.getElementById("band-options")?.addEventListener("change", async (e) => {
        const val = e.target.value;
        sentinelLayerGroup.clearLayers();
        if (val === "original") return;
        const url = `/bandas/${projectId}/${val}?t=${new Date().getTime()}`;
        try {
            const resp = await fetch(url);
            const arrayBuffer = await resp.arrayBuffer();
            const georaster = await parseGeoraster(arrayBuffer);
            const newLayer = new GeoRasterLayer({ 
                georaster, opacity: 0.8, resolution: 256, keepBuffer: false,
                pane: 'tilePane' // Mantém o raster abaixo do GeoJSON
            });
            sentinelLayerGroup.addLayer(newLayer);
            if (segmentsLayer) segmentsLayer.bringToFront();
        } catch (err) { console.error(err); }
    });

    document.getElementById("chkUncertainty")?.addEventListener("change", (e) => {
        showUncertainty = e.target.checked;
        if (segmentsLayer) segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
    });

    document.getElementById("btnPropagar")?.addEventListener("click", async () => {
        const method = document.getElementById("mlMethod").value;
        const btn = document.getElementById("btnPropagar");
        btn.disabled = true; btn.textContent = "Calculando...";
        try {
            const resp = await fetch("/api/propagate", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: projectId, method })
            });
            const json = await resp.json();
            if (json.status === "sucesso") {
                const rP = await fetch(`/resultado_propagado?project_id=${projectId}&path=${json.output_geojson}`);
                const gjP = await rP.json();
                
                uncertaintyMap = new Map(gjP.features.map(f => [f.properties.segment_id, f.properties.uncertainty || 0]));
                propagatedById = new Map(gjP.features.map(f => [f.properties.segment_id, f.properties.classe_pred]));
                
                showPropagated = true;
                document.getElementById("chkProp").checked = true;
                segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
                alert("Propagação e Análise de Incerteza finalizadas.");
            }
        } catch (e) { console.error(e); }
        btn.disabled = false; btn.textContent = "2. Propagar e Calcular AL";
    });

    document.getElementById("btnSalvar")?.addEventListener("click", async () => {
        const feats = [];
        segmentsLayer.eachLayer(l => {
            const sid = l.feature.properties.segment_id;
            if (classBySegment.has(sid)) {
                const info = classBySegment.get(sid);
                feats.push({ type: "Feature", properties: { segment_id: sid, classe: info.name, cor: info.color }, geometry: l.feature.geometry });
            }
        });
        await fetch("/salvar_classificacao", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_id: projectId, features: feats })
        });
        alert("Amostras salvas com sucesso!");
    });

    document.getElementById("chkProp")?.addEventListener("change", (e) => {
        showPropagated = e.target.checked;
        segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
    });

    await loadData();
});