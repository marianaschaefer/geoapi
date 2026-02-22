// static/script_classification.js — v7.25.1 - FIX: Definições de Estilo + Estabilidade de Zoom
document.addEventListener("DOMContentLoaded", async function () {
  const projectId = window.__PROJECT_ID__;
  if (!projectId) return;

  const params = new URLSearchParams(window.location.search);
  const bboxStr = params.get("bbox");
  const bbox = bboxStr ? bboxStr.split(",").map(parseFloat) : null;
  
  const map = L.map("map").setView([-15.78, -47.93], 5);
  if (bbox) map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]]);
  
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map);

  const sentinelLayerGroup = L.layerGroup().addTo(map);

  let segmentsLayer = null; 
  const classBySegment = new Map();
  const classesCriadas = {}; 
  const selectedIds = new Set();
  let propagatedById = new Map();
  let showPropagated = false;

  // --- FUNÇÕES AUXILIARES DE ESTILO E UI ---

  function colorForClass(nome) {
    const n = String(nome).trim().toLowerCase();
    return classesCriadas[n] || `hsl(${Math.abs([...n].reduce((a, c) => a * 31 + c.charCodeAt(0), 7)) % 360}, 65%, 45%)`;
  }

  function atualizarListaClasses() {
    const ul = document.getElementById("class-list");
    if (!ul) return;
    ul.innerHTML = "";
    Object.entries(classesCriadas).forEach(([nome, cor]) => {
      const li = document.createElement("li");
      li.style.display = "flex"; li.style.alignItems = "center"; li.style.gap = "8px";
      li.innerHTML = `<span style="width:12px; height:12px; background:${cor}; border:1px solid #000; border-radius:2px;"></span> <span>${nome}</span>`;
      ul.appendChild(li);
    });
  }

  function styleFeature(feature) {
    const sid = feature.properties.segment_id;
    const manual = classBySegment.get(sid);
    const prop = showPropagated ? propagatedById.get(sid) : null;
    const classe = manual?.name || prop;
    return {
      fillColor: manual ? manual.color : (prop ? colorForClass(prop) : "#ffcc00"),
      fillOpacity: selectedIds.has(sid) ? 0.75 : (classe ? 0.6 : 0.15),
      color: "#333", weight: 0.8
    };
  }

  // --- CONTROLE DE CAMADAS RASTER ---
  document.getElementById("band-options")?.addEventListener("change", async (e) => {
    const val = e.target.value;
    sentinelLayerGroup.clearLayers();
    if (val === "original") return;

    const url = `/bandas/${projectId}/${val}?t=${new Date().getTime()}`;
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Erro: ${response.status}`);
      const arrayBuffer = await response.arrayBuffer();
      const georaster = await parseGeoraster(arrayBuffer);
      const newLayer = new GeoRasterLayer({
        georaster: georaster, opacity: 0.8, resolution: 256, keepBuffer: false, updateWhenIdle: true
      });
      sentinelLayerGroup.addLayer(newLayer);
      if (segmentsLayer) segmentsLayer.bringToFront();
    } catch (err) {
      console.error("[RASTER] Erro:", err);
    }
  });

  map.on('zoomend', () => { if (segmentsLayer) segmentsLayer.bringToFront(); });

  // --- CARREGAMENTO DE DADOS ---
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
          const cor_salva = f.properties.cor; 
          if (sid != null && cl) {
            classesCriadas[cl] = cor_salva || colorForClass(cl);
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
    } catch (e) {
      console.error("[LOAD] Erro ao carregar dados:", e);
    }
  }

  // --- EVENTOS DE UI ---
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

  document.getElementById("btnSalvar")?.addEventListener("click", async () => {
    const feats = [];
    segmentsLayer.eachLayer(l => {
        const sid = l.feature.properties.segment_id;
        if (classBySegment.has(sid)) {
            const info = classBySegment.get(sid);
            feats.push({ 
                type: "Feature", 
                properties: { segment_id: sid, classe: info.name, cor: info.color }, 
                geometry: l.feature.geometry 
            });
        }
    });
    const resp = await fetch("/salvar_classificacao", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, features: feats })
    });
    if (resp.ok) alert("Amostras salvas com sucesso!");
  });

  document.getElementById("btnPropagar")?.addEventListener("click", async () => {
    const method = document.getElementById("mlMethod").value;
    const btn = document.getElementById("btnPropagar");
    btn.disabled = true; btn.textContent = "Processando...";
    try {
        const resp = await fetch("/api/propagate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project_id: projectId, method: method })
        });
        const json = await resp.json();
        if (json.status === "sucesso") {
          const rP = await fetch(`/resultado_propagado?project_id=${projectId}&path=${json.output_geojson}`);
          const gjP = await rP.json();
          propagatedById = new Map(gjP.features.map(f => [f.properties.segment_id, f.properties.classe_pred]));
          showPropagated = true;
          document.getElementById("chkProp").checked = true;
          segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
          alert("Propagação concluída!");
        } else { alert("Erro: " + json.mensagem); }
    } catch (e) { console.error(e); }
    btn.disabled = false; btn.textContent = "Propagar Rótulos";
  });

  document.getElementById("chkProp")?.addEventListener("change", (e) => {
    showPropagated = e.target.checked;
    segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
  });

  await loadData();
});