cd "C:\Users\biobe\Desktop\API_Interactomes"
.\.venv\Scripts\Activate.ps1

$BaseUrl="http://127.0.0.1:8000"
$Seed="TP53"
$Sources="string_ppi,encori_rbp_by_target"
$Extra="string_depth=2&string_required_score=400&string_limit=100&string_depth2_limit=10"
$q="seed=$Seed&sources=$Sources&$Extra"

# 建立輸出資料夾
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path (Resolve-Path .) ("out\deliver\{0}_{1}" -f $Seed, $stamp)
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# 讀 openapi 判斷哪些功能你現在的 API 真的有（有就下載，沒有就自動略過）
$paths = (irm "$BaseUrl/openapi.json").paths.PSObject.Properties.Name
$hasLabel     = $paths -contains "/module/label"
$hasEnrichJson= $paths -contains "/module/enrich"
$hasEnrichPng = $paths -contains "/viz/module/enrich.png"
$hasGraphml   = $paths -contains "/module/graphml"

# 拿 communities size，取 TopK=3
$c = irm "$BaseUrl/viz/communities?$q"
$sizes = $c.sizes
$TopK = 3
$k = [Math]::Min($TopK, $sizes.Count)

# 逐一匯出 C0~C(k-1)
for ($cid=0; $cid -lt $k; $cid++) {
  $modDir = Join-Path $outDir ("C{0}" -f $cid)
  New-Item -ItemType Directory -Path $modDir -Force | Out-Null

  # module zip（內含 module.html / hubs.png / nodes.csv / edges.csv / module_network.json）
  $zipName = "{0}_module_C{1}.zip" -f $Seed, $cid
  $zipPath = Join-Path $outDir $zipName
  iwr "$BaseUrl/export_module.zip?cid=$cid&$q" -OutFile $zipPath | Out-Null
  Expand-Archive -Path $zipPath -DestinationPath $modDir -Force

  # module 報表（如果你有 /module/report）
  try { iwr "$BaseUrl/module/report?cid=$cid&$q" -OutFile (Join-Path $modDir "report.html") | Out-Null } catch {}

  # label（如果你有 /module/label）
  if ($hasLabel) {
    try { irm "$BaseUrl/module/label?cid=$cid&$q" | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $modDir "label.json") } catch {}
  }

  # graphml（如果你有 /module/graphml）
  if ($hasGraphml) {
    try { iwr "$BaseUrl/module/graphml?cid=$cid&$q" -OutFile (Join-Path $modDir "module.graphml") | Out-Null } catch {}
  }

  # enrichment（如果你有 /module/enrich 與 /viz/module/enrich.png）
  if ($hasEnrichJson) {
    try { irm "$BaseUrl/module/enrich?cid=$cid&$q&tool=auto&gost_sources=GO:BP,REAC,KEGG&top=15" | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $modDir "enrich.json") } catch {}
  }
  if ($hasEnrichPng) {
    try { iwr "$BaseUrl/viz/module/enrich.png?cid=$cid&$q&tool=auto&gost_sources=GO:BP,REAC,KEGG&top=10" -OutFile (Join-Path $modDir "enrich.png") | Out-Null } catch {}
  }
}

# 產 index.html（可點進每個 module）
$rows = @()
for ($cid=0; $cid -lt $k; $cid++) {
  $size = $sizes[$cid]
  $folder = "C$cid"
  $zipName = "{0}_module_C{1}.zip" -f $Seed, $cid

  $labelCell   = if (Test-Path (Join-Path $outDir "$folder\label.json")) { "<a href='./$folder/label.json'>label.json</a>" } else { "<span style='color:#999'>n/a</span>" }
  $graphmlCell = if (Test-Path (Join-Path $outDir "$folder\module.graphml")) { "<a href='./$folder/module.graphml'>graphml</a>" } else { "<span style='color:#999'>n/a</span>" }
  $enrichCell  = if (Test-Path (Join-Path $outDir "$folder\enrich.png")) { "<a href='./$folder/enrich.png'>enrich.png</a>" } else { "<span style='color:#999'>n/a</span>" }

  $rows += "<tr><td>C$cid</td><td style='text-align:right'>$size</td><td>$labelCell</td><td><a href='./$folder/module.html'>module.html</a></td><td><a href='./$folder/hubs.png'>hubs.png</a></td><td>$graphmlCell</td><td><a href='./$folder/report.html'>report.html</a></td><td>$enrichCell</td><td><a href='./$zipName'>zip</a></td></tr>"
}

$indexPath = Join-Path $outDir "index.html"
$indexLines = @(
"<!doctype html>",
"<html><head><meta charset='utf-8'><title>Deliver: $Seed</title>",
"<style>body{font-family:Segoe UI,Arial;padding:18px} table{border-collapse:collapse;width:100%} th,td{border:1px solid #eee;padding:8px;vertical-align:top} th{background:#fafafa} code{background:#f7f7f7;padding:2px 6px;border-radius:4px}</style>",
"</head><body>",
"<h2>Deliver package: $Seed (Top $k modules)</h2>",
"<p>Query: <code>$q</code></p>",
"<table><tr><th>Module</th><th>Size</th><th>Label</th><th>Interactive</th><th>Hubs</th><th>GraphML</th><th>Report</th><th>Enrich</th><th>Zip</th></tr>",
($rows -join "`n"),
"</table>",
"<p style='color:#666;margin-top:12px'>GraphML/Enrich 若你的 API 沒有對應路由會顯示 n/a（其他不受影響）。</p>",
"</body></html>"
)
($indexLines -join "`n") | Set-Content -Encoding UTF8 $indexPath

# 再把整個 deliver 資料夾壓成一個 zip（方便換電腦）
$zipAll = Join-Path (Split-Path $outDir -Parent) ("deliver_{0}_{1}.zip" -f $Seed, $stamp)
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipAll -Force

Write-Host "✅ Done: $outDir" -ForegroundColor Green
Write-Host "✅ Zip : $zipAll" -ForegroundColor Green
Start-Process $indexPath
Start-Process (Split-Path $outDir)
