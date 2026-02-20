from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get('/viz/paper/canvas', response_class=HTMLResponse)
async def get_paper_viz_ui():
    return """<!DOCTYPE html>
<html><head><title>Regulon Pro - NAR Publication Mode</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
body { margin: 0; padding: 0; overflow: hidden; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background: #ffffff; color: #333; display: flex; height: 100vh; }
#sidebar { width: 400px; min-width: 400px; height: 100vh; background: #f8f9fa; border-right: 1px solid #e5e7eb; padding: 25px; box-sizing: border-box; overflow-y: auto; z-index: 10; }
#network-container { flex-grow: 1; position: relative; height: 100vh; background: #ffffff; }
#mynetwork { width: 100%; height: 100%; outline: none; cursor: grab; }
#mynetwork:active { cursor: grabbing; }

/* 🚀 新增：資料表容器 (預設隱藏) */
#table-container { display: none; flex-grow: 1; height: 100vh; background: #f3f4f6; padding: 30px; box-sizing: border-box; overflow-y: auto; }
.table-wrapper { background: #fff; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; overflow: hidden; }
#regulonTable { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem; }
#regulonTable th { background: #f8f9fa; padding: 15px; font-weight: 700; color: #4b5563; border-bottom: 2px solid #e5e7eb; position: sticky; top: 0; z-index: 5;}
#regulonTable td { padding: 12px 15px; border-bottom: 1px solid #e5e7eb; vertical-align: middle; }
#regulonTable tr:hover { background: #fef3c7; }
.progress-bg { width: 100px; background: #e5e7eb; border-radius: 4px; height: 8px; margin-top: 5px; overflow: hidden; }
.progress-fill { height: 100%; background: #E64B35; }
.seed-tag { display: inline-block; background: #fca5a5; color: #7f1d1d; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin: 2px; font-weight: bold;}

#panel-inspector { position: absolute; top: 20px; right: 20px; width: 320px; background: rgba(255,255,255,0.95); border-radius: 12px; border: 1px solid #e5e7eb; box-shadow: 0 10px 25px rgba(0,0,0,0.15); font-size: 0.9rem; line-height: 1.6; color: #374151; z-index: 1000; pointer-events: auto; transition: height 0.3s ease; overflow: hidden; }
#panel-header { padding: 12px 20px; background: #f8f9fa; border-bottom: 2px solid #eee; border-radius: 12px 12px 0 0; cursor: grab; display: flex; justify-content: space-between; align-items: center; }
#panel-header:active { cursor: grabbing; }
#panel-header h3 { margin: 0; font-size: 1.05rem; color: #111827; }
.btn-collapse { background: transparent; border: none; font-size: 1.2rem; cursor: pointer; color: #6b7280; padding: 0; }
.btn-collapse:hover { color: #E64B35; }
#inspect-content { padding: 20px; }

.grp { margin-bottom: 12px; background: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
label { font-weight: 600; display: block; margin-bottom: 8px; font-size: 0.85rem; color: #4b5563; }
select, textarea, input[type="range"] { width: 100%; background: #f9fafb; color: #1f2937; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px; box-sizing: border-box; font-family: monospace; font-size: 0.9rem; }
input[type="range"] { padding: 0; cursor: pointer; }
.val-display { float: right; color: #059669; font-weight: bold; }
button { width: 100%; padding: 10px; color: #fff; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; margin-top: 5px; text-transform: uppercase; letter-spacing: 1px; }
button:hover { opacity: 0.9; transform: translateY(-1px); }
.btn-run { background: linear-gradient(135deg, #E64B35, #c0392b); }
.btn-view-toggle { background: #2563eb; }
.btn-fit { background: #6b7280; flex:1;}
.btn-export { background: #00A087; flex:1;}

.badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-right: 4px; margin-bottom: 5px; }
.badge-db { background: #f3f4f6; border: 1px solid #d1d5db; color: #374151; }
#progress-container { width: 100%; background: #e5e7eb; height: 12px; border-radius: 6px; margin-top: 10px; display: none; overflow: hidden; }
#progress-bar { width: 0%; height: 100%; background: linear-gradient(90deg, #00A087, #4DBBD5); transition: width 0.4s ease; }
#status { color: #059669; margin-top: 8px; font-weight: bold; font-size: 0.85rem; text-align: center; }
</style></head><body>

<div id="sidebar">
  <h1 style="color:#E64B35;margin:0 0 15px 0;font-size:1.4rem;font-weight:800;">🧬 Regulon Pro</h1>
  <div class="grp"><label>1. Seed Molecular Class</label><select id="seedType"><option value="Protein">🔵 Protein (Proteins / Enz.)</option><option value="RNA" selected>🧬 RNA (miRNA / lncRNA / mRNA)</option></select></div>
  <div class="grp">
    <label>2. Input Seeds (Bait) <span style="font-weight:normal;font-size:0.75rem;color:#6b7280;float:right;">Supports Excel Paste</span></label>
    <textarea id="seeds" style="height:80px;" placeholder="Paste gene list here...">CTSS&#10;USP24&#10;KLHL20&#10;CD274</textarea>
  </div>
  <div class="grp"><label>3. Target Interactome</label><select id="interactomeMode"><option value="All">🌐 Full Multi-Omics</option><option value="RNA">🧬 RNA Targets Only</option><option value="Protein" selected>🔵 Protein Targets Only</option></select></div>
  <div class="grp"><label>4. Network Density</label><select id="density"><option value="1500">Scientific (Top 1500)</option><option value="4000">Deep Discovery (Top 4000)</option><option value="10000" selected>🔥 Extreme (Top 10000)</option></select></div>
  <div class="grp">
    <label>5. Min Overlap Hits (Max: <span id="maxHitDisplay" style="color:#E64B35;">? Validated</span>)</label>
    <input type="range" id="minHits" min="1" max="4" value="2" oninput="document.getElementById('hitVal').innerText=this.value; if(window.hasData) { applyFilterAndRender(); buildTable(); }">
    <div style="text-align:right; font-size:0.8rem; color:#666;">Current Set: <span id="hitVal" class="val-display" style="float:none;">2</span></div>
  </div>
  <div class="grp"><label>6. Evidence Stringency</label><select id="stringency" onchange="if(window.hasData) { applyFilterAndRender(); buildTable(); }"><option value="any">Level 1: Any Database</option><option value="consensus">Level 2: Cross-DB Consensus</option></select></div>
  <button class="btn-run" onclick="fetchData()">🚀 Run Analysis</button>
  <div id="progress-container"><div id="progress-bar"></div></div>
  <div id="status">Ready.</div>
  
  <button id="btnToggle" class="btn-view-toggle" onclick="toggleView()" style="display:none; margin-top:15px;">🗂️ Switch to Data Table View</button>
  <div style="display:flex;gap:10px;margin-top:10px;">
      <button class="btn-fit" onclick="if(net){net.fit();}">🔍 Fit View</button>
      <button class="btn-export" onclick="exportCSV()">📊 Export CSV</button>
  </div>
</div>

<div id="network-container">
    <div id="mynetwork"></div>
    <div id="panel-inspector">
        <div id="panel-header">
            <h3>🔍 Element Inspector</h3>
            <button class="btn-collapse" onclick="toggleInspector()">−</button>
        </div>
        <div id="inspect-content" style="color:#6b7280;font-style:italic;">Hover or click a node/edge to view evidence.</div>
    </div>
</div>

<div id="table-container">
    <h2 style="margin-top:0; color:#111827;">🗂️ Master Regulon Candidates</h2>
    <p style="color:#6b7280; font-size:0.9rem; margin-bottom:20px;">This table summarizes the common interactors (hubs) that bind to multiple input seeds, sorted by their regulon coverage.</p>
    <div class="table-wrapper">
        <table id="regulonTable">
            <thead>
                <tr>
                    <th style="width:60px;">Rank</th>
                    <th>Candidate Regulator</th>
                    <th>Molecule Type</th>
                    <th>Regulon Coverage (Hits)</th>
                    <th>Bound Seeds</th>
                    <th>Evidence Diversity</th>
                </tr>
            </thead>
            <tbody id="tableBody">
                </tbody>
        </table>
    </div>
</div>

<script>
var net=null; var rawEdges=[]; var nodeProps={}; var nodeDBs={}; var overlap={}; var currentSeeds=[]; window.hasData=false;
var selectedNode = null; 
var actualValidatedSeeds = 0; // 全域變數供表格計算覆蓋率
var currentView = 'network';

function toggleView() {
    const netCont = document.getElementById('network-container');
    const tblCont = document.getElementById('table-container');
    const btn = document.getElementById('btnToggle');
    
    if(currentView === 'network') {
        netCont.style.display = 'none';
        tblCont.style.display = 'block';
        currentView = 'table';
        btn.innerText = '🕸️ Switch to Network View';
        btn.style.background = '#059669'; // 變綠色
        buildTable(); // 切換時構建表格
    } else {
        tblCont.style.display = 'none';
        netCont.style.display = 'block';
        currentView = 'network';
        btn.innerText = '🗂️ Switch to Data Table View';
        btn.style.background = '#2563eb'; // 變藍色
    }
}

// 🚀 核心：動態建立樞紐分析表
function buildTable() {
    if(!window.hasData) return;
    const minHits = parseInt(document.getElementById('minHits').value);
    const stringency = document.getElementById('stringency').value;
    const tbody = document.getElementById('tableBody');
    tbody.innerHTML = '';
    
    // 整理每個 Target 的數據
    let targetStats = {};
    rawEdges.forEach(e => {
        let dbSet = nodeDBs[e.to];
        let isConsensus = dbSet && dbSet.size > 1;
        if(stringency === 'consensus' && !isConsensus) return;
        
        // 我們只把「非 Seed 本身」的節點列入候選人分析 (除非你想看 Seed 互調控)
        if(currentSeeds.includes(e.to)) return; 

        if (!targetStats[e.to]) {
            targetStats[e.to] = { id: e.to, type: nodeProps[e.to], hits: 0, boundSeeds: new Set(), dbs: new Set() };
        }
        if (!targetStats[e.to].boundSeeds.has(e.from)) {
            targetStats[e.to].hits++;
            targetStats[e.to].boundSeeds.add(e.from);
        }
        e.db.split(',').forEach(d => targetStats[e.to].dbs.add(d.trim()));
    });
    
    // 過濾與排序 (依 Hits 降冪排列)
    let candidates = Object.values(targetStats)
        .filter(t => t.hits >= minHits)
        .sort((a, b) => b.hits - a.hits);
        
    if(candidates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:30px; color:#9ca3af;">No candidate regulons found under current filter settings.</td></tr>';
        return;
    }
    
    candidates.forEach((t, index) => {
        let hitPercent = Math.round((t.hits / actualValidatedSeeds) * 100);
        let seedsHtml = Array.from(t.boundSeeds).map(s => `<span class="seed-tag">${s}</span>`).join('');
        let dbsHtml = Array.from(t.dbs).map(d => `<span class="badge badge-db" style="font-size:0.65rem;">${d}</span>`).join('');
        let extLink = `https://www.ncbi.nlm.nih.gov/gene/?term=${t.id}`;
        
        let row = `<tr>
            <td style="font-weight:bold; color:#6b7280;">#${index + 1}</td>
            <td><a href="${extLink}" target="_blank" style="color:#2563eb; font-weight:bold; text-decoration:none;">${t.id} ↗</a></td>
            <td>${t.type === 'RNA' ? '🧬 RNA' : '🔵 Protein'}</td>
            <td>
                <div style="font-weight:bold; color:#E64B35;">${t.hits} / ${actualValidatedSeeds} Seeds (${hitPercent}%)</div>
                <div class="progress-bg"><div class="progress-fill" style="width:${hitPercent}%;"></div></div>
            </td>
            <td><div style="max-width:250px; display:flex; flex-wrap:wrap;">${seedsHtml}</div></td>
            <td><div style="max-width:200px;">${dbsHtml}</div></td>
        </tr>`;
        tbody.insertAdjacentHTML('beforeend', row);
    });
}

document.getElementById('seeds').addEventListener('input', function() {
    let seedsArray = this.value.split(/[\s,;]+/).map(x=>x.trim()).filter(x=>x);
    let maxSeeds = seedsArray.length > 0 ? seedsArray.length : 1;
    let slider = document.getElementById('minHits');
    slider.max = maxSeeds;
    document.getElementById('maxHitDisplay').innerText = maxSeeds;
    if(parseInt(slider.value) > maxSeeds) {
        slider.value = maxSeeds;
        document.getElementById('hitVal').innerText = maxSeeds;
    }
});

dragElement(document.getElementById("panel-inspector"));
function dragElement(elmnt) {
  var pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
  document.getElementById("panel-header").onmousedown = dragMouseDown;
  function dragMouseDown(e) { e = e || window.event; e.preventDefault(); pos3 = e.clientX; pos4 = e.clientY; document.onmouseup = closeDragElement; document.onmousemove = elementDrag; }
  function elementDrag(e) { e = e || window.event; e.preventDefault(); pos1 = pos3 - e.clientX; pos2 = pos4 - e.clientY; pos3 = e.clientX; pos4 = e.clientY; elmnt.style.top = (elmnt.offsetTop - pos2) + "px"; elmnt.style.left = (elmnt.offsetLeft - pos1) + "px"; elmnt.style.right = "auto"; }
  function closeDragElement() { document.onmouseup = null; document.onmousemove = null; }
}

let inspectorOpen = true;
function toggleInspector() {
    const content = document.getElementById('inspect-content');
    const btn = document.querySelector('.btn-collapse');
    if(inspectorOpen) { content.style.display = 'none'; btn.innerText = '+'; } else { content.style.display = 'block'; btn.innerText = '−'; }
    inspectorOpen = !inspectorOpen;
}

async function fetchData(){
    const seedsStr = document.getElementById('seeds').value;
    currentSeeds = seedsStr.split(/[\s,;]+/).map(x=>x.trim().toUpperCase()).filter(x=>x);
    const mode = document.getElementById('interactomeMode').value;
    const queryLimit = document.getElementById('density').value;
    const status = document.getElementById('status');
    const pBar = document.getElementById('progress-bar');
    const pCont = document.getElementById('progress-container');
    
    document.getElementById('maxHitDisplay').innerText = "... Validating";
    pCont.style.display = 'block'; pBar.style.width = '0%';
    status.innerText = "Initializing Deep Query...";
    selectedNode = null;
    rawEdges=[]; nodeProps={}; nodeDBs={}; overlap={};
    const seedsParam = encodeURIComponent(currentSeeds.join(','));
    actualValidatedSeeds = 0; 

    for(let i=0; i<currentSeeds.length; i++){
        let seed = currentSeeds[i];
        status.innerText = `⏳ Querying DB for: ${seed}...`;
        try {
            const url = `/network?seed=${seed}&mode=${mode}&all_seeds=${seedsParam}&limit=${queryLimit}`;
            const res = await fetch(url);
            const r = await res.json();
            if(r && r.edges && r.edges.length > 0){
                actualValidatedSeeds++; 
                r.edges.forEach(e=>{
                    let t = e.target.toUpperCase(); if(seed===t) return; 
                    rawEdges.push({from:seed, to:t, db:e.database});
                    nodeProps[t]=e.mol_type;
                    if(!nodeDBs[t]) nodeDBs[t]=new Set();
                    e.database.split(',').forEach(d=>nodeDBs[t].add(d.trim()));
                    overlap[t]=(overlap[t]||0)+1;
                });
            }
        } catch(err) { console.error("API Error", err); }
        pBar.style.width = Math.round(((i + 1) / currentSeeds.length) * 100) + '%';
    }
    
    let finalMax = actualValidatedSeeds > 0 ? actualValidatedSeeds : 1;
    let slider = document.getElementById('minHits');
    slider.max = finalMax;
    document.getElementById('maxHitDisplay').innerText = finalMax + " Validated";
    if(parseInt(slider.value) > finalMax) {
        slider.value = finalMax;
        document.getElementById('hitVal').innerText = finalMax;
    }
    window.hasData = true; 
    applyFilterAndRender(); 
    if(currentView === 'table') buildTable(); // 如果在表格模式下重新分析，自動更新表
    
    document.getElementById('btnToggle').style.display = 'block'; // 分析成功後顯示切換按鈕
    status.innerText = `✅ Found ${actualValidatedSeeds} Valid Seeds.`;
}

function updateInspectorNode(id) {
    const content = document.getElementById('inspect-content');
    if(!id) { content.innerHTML = "<div style='color:#6b7280;font-style:italic;'>Hover or click a node/edge to view evidence.</div>"; return; }
    const isSeed = currentSeeds.includes(id);
    const dbs = Array.from(nodeDBs[id]||[]).map(d=>`<span class="badge badge-db">${d}</span>`).join('');
    content.innerHTML = `<h3 style="color:#2563eb;margin-top:0;">${id}</h3><b>Type:</b> ${isSeed ? 'Input Seed (Bait)' : (nodeProps[id]||'Target')}<br><b>Regulon Hit Rate:</b> ${overlap[id]||'-'}<br><div style='margin-top:10px;'><b>Evidence Databases (Union):</b><br>${isSeed ? 'User Defined Origin' : dbs}</div>`;
}

function updateInspectorEdge(edgeId) {
    const content = document.getElementById('inspect-content');
    if(!edgeId || typeof edgeId !== 'string' || !edgeId.includes('_')) return; 
    const parts = edgeId.split("_");
    const rawE = rawEdges.find(e => e.from === parts[0] && e.to === parts[1]);
    if(rawE) {
        const dbs = rawE.db.split(',').map(d=>`<span class="badge badge-db">${d.trim()}</span>`).join('');
        content.innerHTML = `<h3 style="color:#E64B35;margin-top:0;">🔗 Single Interaction</h3><b>From:</b> ${rawE.from}<br><b>To:</b> ${rawE.to}<br><div style='margin-top:10px;'><b>Supported by Databases:</b><br>${dbs}</div>`;
    }
}

function applyFilterAndRender() {
    const minHits = parseInt(document.getElementById('minHits').value);
    const stringency = document.getElementById('stringency').value;
    const seedTypeInput = document.getElementById('seedType').value;
    let validNodes = new Set(currentSeeds);
    let edgesForVis = [];

    rawEdges.forEach(e=>{
        let dbSet = nodeDBs[e.to];
        let isConsensus = dbSet && dbSet.size > 1;
        if(stringency === 'consensus' && !isConsensus && !currentSeeds.includes(e.to)) return;
        
        let hitCount = overlap[e.to] || 0;
        if(hitCount >= minHits || currentSeeds.includes(e.to)){
            let isShared = hitCount >= 2;
            edgesForVis.push({
                id: e.from + "_" + e.to, 
                from: e.from, to: e.to, 
                width: isShared ? 2 : 1,
                color: { color: isShared ? "#94a3b8" : "#e5e7eb", opacity: isShared ? 0.9 : 0.4, hover: "#E64B35", highlight: "#E64B35" }
            });
            validNodes.add(e.to);
        }
    });

    let nodesForVis = Array.from(validNodes).map(id=>{
        let isSeed = currentSeeds.includes(id);
        let mType = nodeProps[id] || "Unknown";
        let count = overlap[id]||0;
        let isShared = count >= 2 && !isSeed;
        
        let nShape = "dot";
        if (isSeed) {
            nShape = (seedTypeInput === "RNA") ? "diamond" : "dot";
        } else {
            nShape = (mType === "RNA") ? "diamond" : "dot";
        }

        let maxTargetSize = 45; 
        let baseSize = isSeed ? 20 : Math.min(maxTargetSize, 12 + (count * 4));
        let fontSize = isSeed ? 14 : Math.min(18, 11 + (count * 1.5));

        return {
            id: id, label: id, shape: nShape,
            color: { background: isSeed ? "#E64B35" : (mType === "RNA" ? "#00A087" : "#4DBBD5"), border: isShared ? "#F39C12" : "#ccc", hover: { border: "#F39C12", background: "#fde047" }, highlight: { border: "#E64B35", background: "#fca5a5" } },
            borderWidth: isShared ? 4 : 1.5,
            size: baseSize,
            font: { face:'Arial', size: fontSize, color: "#333", strokeWidth: 4, strokeColor: "#ffffff", bold: isShared || isSeed },
            shadow: isShared ? {enabled: true, color: 'rgba(243, 156, 18, 0.3)', size: 10, x: 0, y: 0} : false
        };
    });

    if(net) net.destroy();
    const container = document.getElementById('mynetwork');
    net = new vis.Network(container, {nodes:nodesForVis, edges:edgesForVis}, {
        physics: {
            solver: 'forceAtlas2Based',
            forceAtlas2Based: { gravitationalConstant: -120, centralGravity: 0.01, springLength: 200, springConstant: 0.05, damping: 0.4, avoidOverlap: 0.8 },
            stabilization: {iterations: 300}
        },
        interaction: { dragView: true, zoomView: true, hover: true, hoverConnectedEdges: true }, 
        edges: { smooth: { type: 'continuous', roundness: 0.1 } }
    });

    net.once("stabilizationIterationsDone", function() {
        net.setOptions({ physics: { enabled: false } });
    });

    net.on("hoverNode", (params)=>{ if(!selectedNode) updateInspectorNode(params.node); });
    net.on("hoverEdge", (params)=>{ if(!selectedNode) updateInspectorEdge(params.edge); });
    net.on("blurNode", ()=>{ if(!selectedNode) updateInspectorNode(null); });
    net.on("blurEdge", ()=>{ if(!selectedNode) updateInspectorNode(null); });
    net.on("click", (params)=>{
        if(params.nodes.length){ selectedNode = params.nodes[0]; updateInspectorNode(selectedNode); } 
        else if(params.edges.length) { selectedNode = params.edges[0]; updateInspectorEdge(selectedNode); } 
        else { selectedNode = null; updateInspectorNode(null); }
    });
}

function exportCSV() {
    if(!window.hasData || rawEdges.length === 0) { alert("No data to export!"); return; }
    let csvContent = "data:text/csv;charset=utf-8,Seed,Target,Target_Type,Databases\\n";
    rawEdges.forEach(e => {
        let tType = nodeProps[e.to] || "Unknown";
        let dbStr = Array.from(nodeDBs[e.to] || []).join(" | ");
        csvContent += `${e.from},${e.to},${tType},${dbStr}\\n`;
    });
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a"); link.setAttribute("href", encodedUri); link.setAttribute("download", "Regulon_Analysis_Export.csv");
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
}
</script></body></html>"""
