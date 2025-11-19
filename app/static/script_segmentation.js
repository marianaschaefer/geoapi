// static/script_segmentation.js — v7.2
document.addEventListener("DOMContentLoaded", () => {
  // ===== MAPA =====
  const map = L.map("map", { zoomControl: true }).setView([-15.78, -47.93], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OSM"
  }).addTo(map);

  // Leaflet.Draw
  const drawnItems = new L.FeatureGroup();
  map.addLayer(drawnItems);
  const drawControl = new L.Control.Draw({
    draw: { polyline:false, marker:false, circle:false, circlemarker:false, rectangle:true, polygon:true },
    edit: { featureGroup: drawnItems }
  });
  map.addControl(drawControl);

  let lastAOI = null;
  map.on(L.Draw.Event.CREATED, (e) => {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    const f = e.layer.toGeoJSON();
    lastAOI = { type:"FeatureCollection", features:[f] };
  });

  // ===== UTIL =====
  const btn = document.getElementById("btnSegmentar");
  const btnText = document.getElementById("btnText");
  const btnSpinner = document.getElementById("btnSpinner");

  function setBusy(busy) {
    if (!btn) return;
    btn.disabled = busy;
    if (btnSpinner) btnSpinner.style.display = busy ? "inline-block" : "none";
    if (btnText) btnText.textContent = busy ? "PROCESSANDO..." : "SEGMENTAR";
  }

  function bboxFromGeoJSON(fc) {
    if (!fc || !fc.features || fc.features.length === 0) return null;
    let minx=Infinity, miny=Infinity, maxx=-Infinity, maxy=-Infinity;
    fc.features.forEach((f) => {
      const b = L.geoJSON(f).getBounds();
      minx = Math.min(minx, b.getWest());
      miny = Math.min(miny, b.getSouth());
      maxx = Math.max(maxx, b.getEast());
      maxy = Math.max(maxy, b.getNorth());
    });
    return [minx, miny, maxx, maxy];
  }

  // ===== CHAMADA =====
  async function executarSegmentacao() {
    try {
      setBusy(true);

      if (!lastAOI) {
        alert("Desenhe um retângulo ou polígono no mapa antes de segmentar.");
        setBusy(false);
        return;
      }

      const getVal = (id, fb=null) => {
        const el = document.getElementById(id);
        return (el && el.value !== "") ? el.value : fb;
      };
      const getBool = (id) => !!document.getElementById(id)?.checked;

      const data_inicio = getVal("data_inicio", null);
      const data_fim    = getVal("data_fim",   null);
      let   dias        = parseInt(getVal("dias", "180"), 10);
      const cloud_cover = parseInt(getVal("cloud_cover", "30"), 10);
      const region_px   = parseInt(getVal("region_px", "30"), 10);
      const compactness = parseFloat(getVal("compactness", "1.0"));
      const sigma       = parseFloat(getVal("sigma", "1.0"));
      const usar_ndvi   = getBool("usar_ndvi");

      // Se o usuário preencheu intervalo de datas, calcula 'dias' pelo range
      if (data_inicio && data_fim) {
        try {
          const d0 = new Date(data_inicio);
          const d1 = new Date(data_fim);
          const diff = Math.max(1, Math.round((d1 - d0) / (1000*60*60*24)));
          dias = diff;
        } catch(_) {}
      }

      const bbox = bboxFromGeoJSON(lastAOI);

      // payload alinhado com o backend
      const payload = {
        bbox,
        dias,
        resolucao: 10,
        cloud_cover_max: cloud_cover, // <— nome novo aceito no backend
        data_inicio,
        data_fim,
        region_px,
        compactness,
        sigma,
        usar_ndvi
      };

      // rota compatível (/segmentar e /api/segmentar existem no backend)
      const resp = await fetch("/segmentar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      const ct = resp.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        const txt = await resp.text();
        throw new Error(`Resposta não-JSON (${resp.status}). Trecho: ${txt.slice(0,300)}`);
        }

      const json = await resp.json();

      if (resp.ok && json.status === "sucesso") {
        const bb = (json.params && json.params.bbox) ? json.params.bbox : bbox;
        const q = new URLSearchParams({ bbox: bb.join(",") }).toString();
        window.location.href = "/classification?" + q;
      } else {
        throw new Error(json.mensagem || `Falha HTTP ${resp.status}`);
      }
    } catch (e) {
      console.error(e);
      alert("Falha na segmentação: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  if (btn) btn.addEventListener("click", executarSegmentacao);
});
