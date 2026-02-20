from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/report/paper/ui", response_class=HTMLResponse)
async def get_report_ui():
    return """<!DOCTYPE html>
<html><head><title>Regulon Overlap Report</title>
<style>
body{font-family:"Segoe UI",sans-serif;margin:0;padding:20px;background:#f8f9fa;color:#333;}
.container{max-width:1200px;margin:0 auto;background:#fff;padding:20px;border-radius:8px;box-shadow:0 4px 6px rgba(0,0,0,0.05);}
h2{border-bottom:2px solid #3b82f6;padding-bottom:10px;margin-top:0;}
.controls{display:flex;gap:15px;margin-bottom:20px;flex-wrap:wrap;align-items:flex-end;background:#f1f5f9;padding:15px;border-radius:6px;}
.form-group{display:flex;flex-direction:column;}
label{font-weight:600;margin-bottom:5px;font-size:0.9rem;}
input[type=text], select{padding:8px;border:1px solid #ccc;border-radius:4px;min-width:200px;}
button{padding:10px 16px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:bold;}
button:hover{background:#2563eb;}
button.success{background:#10b981;} button.success:hover{background:#059669;}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:0.9rem;border:1px solid #e2e8f0;}
th,td{padding:12px 10px;text-align:left;border-bottom:1px solid #e2e8f0;}
th{background:#f8fafc;position:sticky;top:0;font-weight:600;}
tr:hover{background:#f1f5f9;}
.badge{padding:4px 8px;border-radius:12px;font-size:0.75rem;font-weight:bold;color:#fff;}
.bg-gold{background:#f59e0b;} .bg-green{background:#10b981;} .bg-gray{background:#64748b;font-weight:normal;}
</style></head><body>
<div class="container">
<h2>📊 Regulon Overlap Report</h2>
<div class="controls">
<div class="form-group"><label>Seeds (Comma separated):</label><input type="text" id="seeds" value="TP53, MDM2"></div>
<div class="form-group"><label>Interactor Type:</label><select id="typeFilter" onchange="renderTable()">
<option value="ALL">All Interactors</option><option value="Protein">Protein Only</option><option value="RNA">RNA Only</option>
</select></div>
<div class="form-group"><label>Databases:</label><div>
<label style="margin-right:10px;font-weight:normal;"><input type="checkbox" id="db_str" checked> STRING</label>
<label style="font-weight:normal;"><input type="checkbox" id="db_enc" checked> ENCORI</label>
</div></div>
<button onclick="fetchData()">🔄 Generate Report</button>
<button class="success" onclick="exportCSV()">📥 Export to CSV</button>
</div>
<div id="status" style="margin-bottom:10px;font-weight:bold;color:#2563eb;">Ready. Click Generate Report to fetch data.</div>
<div style="overflow-x:auto;">
<table id="reportTable"><thead><tr>
<th>Interactor</th><th>Inferred Type</th><th>Overlap Count</th><th>Connected Seeds</th><th>Total Score</th><th>Evidence DBs</th>
</tr></thead><tbody id="tbody"></tbody></table>
</div></div>
<script>
var reportData = [], currentSeeds = [];
async function fetchData(){
  document.getElementById("status").innerHTML="⏳ Querying API... please wait.";
  let sVal=document.getElementById("seeds").value;
  currentSeeds=sVal.split(",").map(x=>x.trim().toUpperCase()).filter(x=>x);
  let srcs=[];
  if(document.getElementById("db_str").checked) srcs.push("string_ppi");
  if(document.getElementById("db_enc").checked) {srcs.push("encori_rbp_by_target"); srcs.push("encori_rna_rna");}
  try{
    let url = "/network?seed=" + currentSeeds.join(",") + "&sources=" + srcs.join(",");
    let res = await fetch(url);
    if(!res.ok) throw new Error("API call failed");
    let rawData = await res.json();
    processData(rawData);
  }catch(e){
    document.getElementById("status").innerHTML="❌ Error: " + e.message;
  }
}
function processData(data){
  if(!data.edges || data.edges.length===0){ document.getElementById("status").innerHTML="No interactions found."; return; }
  let interactors = {};
  data.edges.forEach(e=>{
    let f=(e.source||e.from).toUpperCase(), t=(e.target||e.to).toUpperCase();
    let score = e.score || 0;
    let db = e.database || e.source_db || "unknown";
    let isFSeed = currentSeeds.includes(f), isTSeed = currentSeeds.includes(t);
    if(!isFSeed && !isTSeed) return; 
    if(isFSeed && isTSeed) return; 
    let interactor = isFSeed ? t : f;
    let seed = isFSeed ? f : t;
    if(!interactors[interactor]){
       interactors[interactor] = { id: interactor, seeds: new Set(), dbs: new Set(), score: 0, rna_ev: false, prot_ev: false };
    }
    interactors[interactor].seeds.add(seed);
    interactors[interactor].dbs.add(db);
    interactors[interactor].score += score;
    if(db.includes("rna_rna") || db.includes("rbp_by_target")) interactors[interactor].rna_ev = true;
    if(db.includes("string_ppi") || db.includes("rbp_by_target")) interactors[interactor].prot_ev = true;
  });
  reportData = Object.values(interactors).map(i => {
    let type = "Unknown";
    if(i.rna_ev && !i.prot_ev) type = "RNA";
    else if(i.prot_ev && !i.rna_ev) type = "Protein";
    else if(i.prot_ev && i.rna_ev) type = "Protein/RNA (Mixed)";
    else type = "Protein";
    return { id: i.id, type: type, count: i.seeds.size, seedsStr: Array.from(i.seeds).join(", "), score: i.score.toFixed(2), dbs: Array.from(i.dbs).join(", ") };
  });
  reportData.sort((a,b) => b.count - a.count || b.score - a.score);
  renderTable();
}
function renderTable(){
  let filter = document.getElementById("typeFilter").value;
  let html = "";
  let filtered = reportData.filter(r => filter==="ALL" || r.type.includes(filter));
  filtered.forEach(r => {
    let badge = r.count>1 ? `<span class="badge bg-gold">Shared (${r.count})</span>` : `<span class="badge bg-green">Unique (1)</span>`;
    html += `<tr><td><b>${r.id}</b></td><td>${r.type}</td><td>${badge}</td><td>${r.seedsStr}</td><td>${r.score}</td><td><span class="badge bg-gray">${r.dbs}</span></td></tr>`;
  });
  document.getElementById("tbody").innerHTML = html;
  document.getElementById("status").innerHTML = "✅ Report generated! Showing " + filtered.length + " interactors.";
}
function exportCSV(){
  if(reportData.length===0) return alert("No data to export!");
  let filter = document.getElementById("typeFilter").value;
  let filtered = reportData.filter(r => filter==="ALL" || r.type.includes(filter));
  let csv = "Interactor,Type,Overlap_Count,Connected_Seeds,Total_Score,Evidence_DBs\n";
  filtered.forEach(r => { csv += `"${r.id}","${r.type}",${r.count},"${r.seedsStr}",${r.score},"${r.dbs}"\n`; });
  let blob = new Blob([csv], {type: "text/csv;charset=utf-8;"});
  let a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "Regulon_Overlap_Report.csv";
  a.click();
}
</script></body></html>"""