// static/script_classification.js — v7.7 (pinta propagação + tooltip)
document.addEventListener("DOMContentLoaded", async function () {
  // ====== Estado ======
  const params = new URLSearchParams(window.location.search);
  const bboxStr = params.get("bbox");
  const bbox = bboxStr ? bboxStr.split(",").map(parseFloat) : null;
  const bounds = bbox ? [[bbox[1], bbox[0]], [bbox[3], bbox[2]]] : null;

  const map = L.map("map").setView([-15.78, -47.93], 6);
  if (bounds) map.fitBounds(bounds);

  // Base
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution:"&copy; OSM" }).addTo(map);

  let backdropLayer = null;      // raster escolhido
  let segmentsLayer = null;      // camada de segmentos
  const classBySegment = new Map();  // segment_id -> { name, color } (manuais)
  const selectedIds = new Set();
  const classesCriadas = {};     // catálogo de classes (nome -> cor)

  // Propagação (resultado)
  let showPropagated = false;
  const propagatedById = new Map();   // segment_id -> classe (string)
  const propagatedColors = new Map(); // classe -> cor (se vier nova classe não cadastrada)

  // ====== util: cor por classe ======
  function colorForClass(nome) {
    if (!nome) return "#ffcc00"; // default amarelo
    if (classesCriadas[nome]) return classesCriadas[nome];
    if (propagatedColors.has(nome)) return propagatedColors.get(nome);
    // cor determinística caso a classe não exista no catálogo
    const h = Math.abs([...nome].reduce((a,c)=>a*31 + c.charCodeAt(0), 7)) % 360;
    const c = `hsl(${h}, 65%, 45%)`;
    propagatedColors.set(nome, c);
    return c;
  }

  // ====== bandas (raster) ======
  async function carregarBanda(filename) {
    try {
      if (backdropLayer) { map.removeLayer(backdropLayer); backdropLayer = null; }
      if (!filename || filename === "original") return;

      const resp = await fetch("/bandas/" + filename);
      if (!resp.ok) throw new Error("Falha ao carregar banda: " + filename);
      const ab = await resp.arrayBuffer();
      const georaster = await parseGeoraster(ab);

      backdropLayer = new GeoRasterLayer({
        georaster, opacity: filename.includes("RGB") ? 0.9 : 0.85, resolution: 256
      }).addTo(map);
      map.fitBounds(backdropLayer.getBounds());
    } catch (e) {
      console.error(e);
      alert("Não foi possível renderizar a banda selecionada.");
    }
  }

  const bandSelect = document.getElementById("band-options");
  if (bandSelect) {
    await carregarBanda(bandSelect.value);
    bandSelect.addEventListener("change", (e)=>carregarBanda(e.target.value));
  }

  // ====== estilo dos segmentos ======
  function styleFeature(feature) {
    const sid = feature?.properties?.segment_id;
    // 1) manual tem prioridade quando selecionado para aplicar classe
    const manual = classBySegment.get(sid);
    // 2) se toggle ligado, usa classe propagada como fallback
    const propagated = showPropagated ? propagatedById.get(sid) : null;

    // regra de cor
    const classe = manual?.name || propagated || null;
    const baseColor = classe ? colorForClass(String(classe).trim().toLowerCase()) : "#ffcc00";
    const isSelected = selectedIds.has(sid);

    return {
      color: isSelected ? "#000000" : baseColor,
      weight: isSelected ? 2.4 : 1.0,
      fillColor: baseColor,
      fillOpacity: classe ? 0.45 : 0.22
    };
  }

  function onEachFeature(feature, layer) {
    const sid = feature?.properties?.segment_id;
    layer.on("click", () => {
      if (!sid) return;
      if (selectedIds.has(sid)) selectedIds.delete(sid); else selectedIds.add(sid);
      layer.setStyle(styleFeature(feature));
      // tooltip dinâmica com origem/manual/propagada
      const manual = classBySegment.get(sid);
      const prop   = propagatedById.get(sid);
      const classe = manual?.name || prop || "(sem classe)";
      const origem = manual ? "manual" : (prop ? "propagada" : "—");
      layer.bindPopup(
        `<b>segment_id:</b> ${sid}<br><b>classe:</b> ${classe}<br><i>origem:</i> ${origem}`
      ).openPopup();
    });
  }

  // ====== carregar segmentos (base) ======
  async function loadSegments() {
    try {
      const r = await fetch("/resultado_geojson");
      if (!r.ok) { alert("Execute a segmentação antes."); return; }
      const gj = await r.json();
      if (segmentsLayer) map.removeLayer(segmentsLayer);
      segmentsLayer = L.geoJSON(gj, { style: styleFeature, onEachFeature }).addTo(map);
      if (!bounds) map.fitBounds(segmentsLayer.getBounds());
    } catch (e) {
      console.error("Erro ao carregar GeoJSON:", e);
    }
  }
  await loadSegments();

  // ====== UI: classes (lado direito) ======
  function atualizarListaClasses() {
    const ul = document.getElementById("class-list");
    ul.innerHTML = "";
    Object.entries(classesCriadas).forEach(([nome, cor]) => {
      const li = document.createElement("li");

      const colorBox = document.createElement("span");
      colorBox.className = "color-box"; colorBox.style.backgroundColor = cor;
      li.appendChild(colorBox);

      const nameSpan = document.createElement("span");
      nameSpan.className = "class-name"; nameSpan.textContent = nome;
      li.appendChild(nameSpan);

      const delBtn = document.createElement("button");
      delBtn.textContent = "X";
      delBtn.onclick = () => {
        delete classesCriadas[nome];
        // remove classe aplicada manualmente aos segmentos com esse nome
        segmentsLayer.eachLayer(layer => {
          const sid = layer.feature?.properties?.segment_id;
          const manual = classBySegment.get(sid);
          if (manual?.name === nome) {
            classBySegment.delete(sid);
            layer.setStyle(styleFeature(layer.feature));
          }
        });
        ul.removeChild(li);
      };
      li.appendChild(delBtn);

      ul.appendChild(li);
    });
  }

  function applyClass() {
    const name = document.getElementById("className").value.trim();
    const color = document.getElementById("classColor").value;
    if (!name) { alert("Digite o nome da classe!"); return; }

    const finalColor = classesCriadas[name] || color;
    if (!classesCriadas[name]) {
      classesCriadas[name] = finalColor;
      atualizarListaClasses();
    }
    if (selectedIds.size === 0) { alert("Selecione ao menos um polígono no mapa."); return; }

    selectedIds.forEach(id => classBySegment.set(id, { name, color: finalColor }));
    segmentsLayer.eachLayer(layer => {
      const sid = layer.feature?.properties?.segment_id;
      if (sid && classBySegment.has(sid)) layer.setStyle(styleFeature(layer.feature));
    });
    selectedIds.clear();
  }

  async function salvar() {
    const feats = [];
    segmentsLayer.eachLayer(layer => {
      const f = layer.feature;
      const sid = f?.properties?.segment_id;
      const manual = classBySegment.get(sid);
      if (sid && manual) {
        feats.push({
          type: "Feature",
          properties: { ...f.properties, classe: manual.name, color: manual.color },
          geometry: f.geometry
        });
      }
    });
    if (feats.length === 0) { alert("Nenhum segmento classificado para salvar."); return; }
    try {
      const r = await fetch("/salvar_classificacao", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ features: feats })
      });
      const j = await r.json();
      if (j.status === "ok") alert("Classificação salva!"); 
      else alert("Falha ao salvar: " + (j.mensagem || "desconhecida"));
    } catch (e) {
      console.error(e); 
      alert("Erro ao salvar.");
    }
  }

  // ====== Propagação ======
  async function carregarPropagado(pathOrNull = null) {
    // baixa o último (ou um específico) e indexa por segment_id
    const url = pathOrNull
      ? `/resultado_propagado?path=${encodeURIComponent(pathOrNull)}`
      : "/resultado_propagado";

    console.log("[PROP] carregando propagado de:", url);

    const r = await fetch(url);
    if (!r.ok) {
      console.warn("[PROP] Nenhum propagado encontrado. HTTP", r.status);
      propagatedById.clear();
      return;
    }

    const gj = await r.json();
    console.log(
      "[PROP] GeoJSON propagado: features=",
      gj.features?.length,
      "exemplo properties=",
      gj.features && gj.features[0] ? gj.features[0].properties : null
    );

    propagatedById.clear();
    (gj.features || []).forEach(f => {
      const props = f.properties || {};
      const sid = props.segment_id;

      // aceita vários nomes possíveis; no seu caso, `classe_pred`
      const rawClass =
        props.classe_pred ??
        props.classe ??
        props.label ??
        props.class_name ??
        "";

      const cl = rawClass.toString().trim().toLowerCase();

      if (sid != null && cl) {
        propagatedById.set(sid, cl);
      }
    });

    console.log("[PROP] total IDs indexados em propagatedById:", propagatedById.size);
  }

  async function propagar() {
    try {
      // tenta ler o método da UI; se não existir, usa padrão
      const methodEl = document.getElementById("propMethod");
      const method = (methodEl && methodEl.value) ? methodEl.value : "label_spreading";

      const statusEl = document.getElementById("propStatus");
      if (statusEl) {
        statusEl.textContent = "Executando propagação…";
      }

      const resp = await fetch("/api/propagate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method })
      });
      const json = await resp.json();
      if (resp.ok && json.status === "sucesso") {
        if (statusEl) {
          statusEl.textContent =
            `OK (${method}) — acurácia interna: ${Number(json.consistency_acc_on_labeled).toFixed(3)}`;
        }

        // carrega o arquivo recém-criado e liga o toggle
        await carregarPropagado(json.output_geojson_relative || null);
        showPropagated = true;
        const chk = document.getElementById("chkProp");
        if (chk) chk.checked = true;

        // restiliza
        segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
        alert(
          "Propagação concluída. Resultado salvo em:\n" +
          (json.output_geojson || json.output_geojson_relative)
        );
      } else {
        throw new Error(json.mensagem || "Erro ao rodar a propagação.");
      }
    } catch (e) {
      console.error(e);
      alert("Erro ao rodar a propagação.");
    }
  }

  // ====== Toggle “Mostrar resultado propagado” ======
  const chkProp = document.getElementById("chkProp");
  if (chkProp) {
    chkProp.addEventListener("change", async (e) => {
      showPropagated = e.target.checked;
      if (showPropagated && propagatedById.size === 0) {
        await carregarPropagado(); // tenta último disponível
      }
      segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
    });

    // Se o checkbox já vier marcado por template, tenta carregar o último
    if (chkProp.checked) {
      await carregarPropagado();
      showPropagated = true;
      segmentsLayer.eachLayer(l => l.setStyle(styleFeature(l.feature)));
    }
  }

  // ====== Wire-up ======
  document.getElementById("btnApplyClass")?.addEventListener("click", applyClass);
  document.getElementById("btnSalvar")?.addEventListener("click", salvar);
  document.getElementById("btnPropagar")?.addEventListener("click", propagar);
});
