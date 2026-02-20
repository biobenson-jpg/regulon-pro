param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Seed = "TP53",
  [string]$Sources = "string_ppi,encori_rbp_by_target",
  [string]$Extra = "string_depth=2&string_required_score=400&string_limit=100&string_depth2_limit=10",
  [int]$TopK = 3,
  [string]$OutRoot = "out\deliver",
  [switch]$SkipEnrich
)

$ErrorActionPreference = "Stop"

function Info($m){ Write-Host $m -ForegroundColor Cyan }
function Ok($m){ Write-Host "✅ $m" -ForegroundColor Green }
function Warn($m){ Write-Host "⚠️ $m" -ForegroundColor Yellow }

function XmlEsc([string]$s){
  if ($null -eq $s) { return "" }
  return [System.Security.SecurityElement]::Escape([string]$s)
}

function Write-GraphML($net, [string]$path){
  $sb = New-Object System.Text.StringBuilder
  $null = $sb.AppendLine('<?xml version="1.0" encoding="UTF-8"?>')
  $null = $sb.AppendLine('<graphml xmlns="http://graphml.graphdrawing.org/xmlns" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">')
  $null = $sb.AppendLine('  <key id="d0" for="node" attr.name="label" attr.type="string"/>')
  $null = $sb.AppendLine('  <key id="d1" for="node" attr.name="kind" attr.type="string"/>')
  $null = $sb.AppendLine('  <key id="d2" for="node" attr.name="sources" attr.type="string"/>')
  $null = $sb.AppendLine('  <key id="d3" for="edge" attr.name="kind" attr.type="string"/>')
  $null = $sb.AppendLine('  <key id="d4" for="edge" attr.name="source_db" attr.type="string"/>')
  $null = $sb.AppendLine('  <key id="d5" for="edge" attr.name="score" attr.type="double"/>')
  $null = $sb.AppendLine('  <key id="d6" for="edge" attr.name="support" attr.type="int"/>')
  $null = $sb.AppendLine('  <graph id="G" edgedefault="undirected">')

  foreach ($n in $net.nodes) {
    $id = XmlEsc([string]$n.id)
    if (-not $id) { continue }
    $lab = XmlEsc([string]($n.label))
    if (-not $lab) { $lab = $id }
    $kind = XmlEsc([string]$n.kind)
    $src = $n.sources
    if ($src -is [System.Array]) { $src = ($src -join ",") }
    $src = XmlEsc([string]$src)

    $null = $sb.AppendLine("    <node id=""$id"">")
    $null = $sb.AppendLine("      <data key=""d0"">$lab</data>")
    $null = $sb.AppendLine("      <data key=""d1"">$kind</data>")
    $null = $sb.AppendLine("      <data key=""d2"">$src</data>")
    $null = $sb.AppendLine("    </node>")
  }

  $eid = 0
  foreach ($e in $net.edges) {
    $u = XmlEsc([string]$e.source)
    $v = XmlEsc([string]$e.target)
    if (-not $u -or -not $v) { continue }

    $ek = XmlEsc([string]$e.kind)
    $db = XmlEsc([string]$e.source_db)

    $sc = 0.0
    try { if ($null -ne $e.score) { $sc = [double]$e.score } } catch {}
    $sp = 1
    try { if ($null -ne $e.support) { $sp = [int]$e.support } } catch {}

    $null = $sb.AppendLine("    <edge id=""e$eid"" source=""$u"" target=""$v"">")
    $null = $sb.AppendLine("      <data key=""d3"">$ek</data>")
    $null = $sb.AppendLine("      <data key=""d4"">$db</data>")
    $null = $sb.AppendLine("      <data key=""d5"">$sc</data>")
    $null = $sb.AppendLine("      <data key=""d6"">$sp</data>")
    $null = $sb.AppendLine("    </edge>")
    $eid++
  }

  $null = $sb.AppendLine('  </graph>')
  $null = $sb.AppendLine('</graphml>')
  Set-Content -Encoding UTF8 $path $sb.ToString()
}

# ---- local auto-label (fallback; 不靠 /module/label 也能跑) ----
$Rules = @(
  @{ Label="Cell cycle / mitosis"; Regex=@('^(CDK|CCN|CDC|MCM|AURK|PLK|BUB|MAD|E2F|SKP|GADD45|TOP2A|UBE2C|CDC20)') },
  @{ Label="DNA damage / repair"; Regex=@('^(BRCA|RAD|ATM|ATR|CHEK|TP53BP|PARP|FANCD|FANCI|XRCC|MRE11|NBN|MSH|MLH|RRM2B)') },
  @{ Label="Apoptosis / cell death"; Regex=@('^(BCL|CASP|FAS|TNFR|BAX|BAK|BBC3|BIRC|XIAP)') },
  @{ Label="Chromatin / transcription"; Regex=@('^(HDAC|KAT|EP300|CREBBP|SMARC|ARID|EZH|KDM|BRD|MED|POLR|SP1|MYC|JUN|FOS)') },
  @{ Label="Ubiquitin / proteasome"; Regex=@('^(UBE|UBC|USP|PSM|PSMA|PSMB|CUL|RBX|FBX|TRIM)') },
  @{ Label="RNA processing / splicing"; Regex=@('^(HNRNP|SRSF|SF3|PRPF|DDX|DHX|RBM|ELAVL|U2AF|FUS|TARDBP)') },
  @{ Label="Translation / ribosome"; Regex=@('^(RPL|RPS|EIF|EEF)') },
  @{ Label="Signaling (MAPK/PI3K/AKT)"; Regex=@('^(MAPK|MAP2K|PIK3|AKT|MTOR|RAS|RAF|STAT)') }
)

function TopHubs($net, [int]$n=20){
  $deg = @{}
  foreach ($e in $net.edges) {
    $a = [string]$e.source
    $b = [string]$e.target
    if (-not $deg.ContainsKey($a)) { $deg[$a] = 0 }
    if (-not $deg.ContainsKey($b)) { $deg[$b] = 0 }
    $deg[$a]++
    $deg[$b]++
  }
  return ($deg.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $n).Name
}

function AutoLabel($genes, $hubs){
  $genesU = @($genes | ForEach-Object { ([string]$_).Trim().ToUpper() } | Where-Object { $_ })
  $hubsU  = @($hubs  | ForEach-Object { ([string]$_).Trim().ToUpper() } | Where-Object { $_ })

  $best = [ordered]@{ label="Uncategorized"; score=0; hub_hits=@(); gene_hits=@() }

  foreach ($r in $Rules) {
    $hubHits  = @($hubsU  | Where-Object { $x=$_; $r.Regex | Where-Object { $x -match $_ } } | Select-Object -Unique | Sort-Object)
    $geneHits = @($genesU | Where-Object { $x=$_; $r.Regex | Where-Object { $x -match $_ } } | Select-Object -Unique | Sort-Object)
    $score = 3*$hubHits.Count + 1*$geneHits.Count
    if ($score -gt $best.score) {
      $best = [ordered]@{
        label = $r.Label
        score = $score
        hub_hits  = $hubHits  | Select-Object -First 30
        gene_hits = $geneHits | Select-Object -First 80
      }
    }
  }
  return $best
}

# ---- repo root ----
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repo

# ---- health ----
$h = irm "$BaseUrl/health"
Ok ("API OK: " + $h.app + " v" + $h.version)

# ---- openapi paths (for feature detection) ----
$paths = (irm "$BaseUrl/openapi.json").paths.PSObject.Properties.Name
$hasLabel      = $paths -contains "/module/label"
$hasReport     = $paths -contains "/module/report"
$hasGraphml    = $paths -contains "/module/graphml"
$hasEnrichJson = $paths -contains "/module/enrich"
$hasEnrichPng  = $paths -contains "/viz/module/enrich.png"

# ---- query ----
$q = "seed=$Seed&sources=$Sources"
if ($Extra -and $Extra.Trim().Length -gt 0) { $q = "$q&$Extra" }

# ---- output folder ----
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $repo (Join-Path $OutRoot ("{0}_{1}" -f $Seed, $stamp))
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# ---- communities sizes ----
$c = irm "$BaseUrl/viz/communities?$q"
$sizes = $c.sizes
if (-not $sizes) { throw "No community sizes returned from /viz/communities" }

$k = [Math]::Min($TopK, $sizes.Count)
Info "Exporting TopK=$k modules for $Seed ..."
$rows = @()

for ($cid=0; $cid -lt $k; $cid++) {
  $size = $sizes[$cid]
  Info ("--- C{0} (size={1}) ---" -f $cid, $size)

  $modDir = Join-Path $outDir ("C{0}" -f $cid)
  New-Item -ItemType Directory -Path $modDir -Force | Out-Null

  # 1) export_module.zip (必備)
  $zipName = "{0}_module_C{1}.zip" -f $Seed, $cid
  $zipPath = Join-Path $outDir $zipName
  iwr "$BaseUrl/export_module.zip?cid=$cid&$q" -OutFile $zipPath | Out-Null
  Expand-Archive -Path $zipPath -DestinationPath $modDir -Force

  # 2) load module network json (from zip)
  $netJson = Join-Path $modDir "module_network.json"
  if (-not (Test-Path $netJson)) {
    # fallback: search any json
    $cand = Get-ChildItem $modDir -Filter "*network*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $netJson = $cand.FullName }
  }
  $net = $null
  if (Test-Path $netJson) {
    $net = Get-Content $netJson -Raw | ConvertFrom-Json
  } else {
    $net = irm "$BaseUrl/module?cid=$cid&$q"
    ($net | ConvertTo-Json -Depth 15) | Set-Content -Encoding UTF8 (Join-Path $modDir "module_network.json")
  }

  # 3) report.html（有就下載）
  if ($hasReport) {
    try { iwr "$BaseUrl/module/report?cid=$cid&$q" -OutFile (Join-Path $modDir "report.html") | Out-Null } catch {}
  }

  # 4) label（優先用 API，沒有就本地 heuristic）
  if ($hasLabel) {
    try {
      irm "$BaseUrl/module/label?cid=$cid&$q" | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $modDir "label.json")
    } catch {
      # fallback
      $genes = @($net.nodes | ForEach-Object { $_.id })
      $hubs  = TopHubs $net 20
      (AutoLabel $genes $hubs | ConvertTo-Json -Depth 6) | Set-Content -Encoding UTF8 (Join-Path $modDir "label.json")
    }
  } else {
    $genes = @($net.nodes | ForEach-Object { $_.id })
    $hubs  = TopHubs $net 20
    (AutoLabel $genes $hubs | ConvertTo-Json -Depth 6) | Set-Content -Encoding UTF8 (Join-Path $modDir "label.json")
  }

  # 5) graphml（有 API 就下載；沒有就本地輸出）
  $graphmlPath = Join-Path $modDir "module.graphml"
  if ($hasGraphml) {
    try { iwr "$BaseUrl/module/graphml?cid=$cid&$q" -OutFile $graphmlPath | Out-Null } catch { Write-GraphML $net $graphmlPath }
  } else {
    Write-GraphML $net $graphmlPath
  }

  # 6) enrichment（可選：有路由就抓；沒路由就略過）
  if (-not $SkipEnrich) {
    if ($hasEnrichJson) {
      try { irm "$BaseUrl/module/enrich?cid=$cid&$q&tool=auto&gost_sources=GO:BP,REAC,KEGG&top=15" | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 (Join-Path $modDir "enrich.json") } catch {}
    }
    if ($hasEnrichPng) {
      try { iwr "$BaseUrl/viz/module/enrich.png?cid=$cid&$q&tool=auto&gost_sources=GO:BP,REAC,KEGG&top=10" -OutFile (Join-Path $modDir "enrich.png") | Out-Null } catch {}
    }
  }

  # index row
  $labelCell  = if (Test-Path (Join-Path $modDir "label.json")) { "<a href='./C$cid/label.json'>label.json</a>" } else { "<span style='color:#999'>n/a</span>" }
  $enrichCell = if (Test-Path (Join-Path $modDir "enrich.png"))  { "<a href='./C$cid/enrich.png'>enrich.png</a>" } else { "<span style='color:#999'>n/a</span>" }

  $rows += "<tr><td>C$cid</td><td style='text-align:right'>$size</td><td>$labelCell</td><td><a href='./C$cid/module.html'>module.html</a></td><td><a href='./C$cid/hubs.png'>hubs.png</a></td><td><a href='./C$cid/module.graphml'>graphml</a></td><td><a href='./$zipName'>zip</a></td><td>$enrichCell</td></tr>"
}

# index.html
$indexPath = Join-Path $outDir "index.html"
@"
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Deliver: $Seed</title>
  <style>
    body{font-family:Segoe UI,Arial;padding:18px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #eee;padding:8px;vertical-align:top}
    th{background:#fafafa}
    code{background:#f7f7f7;padding:2px 6px;border-radius:4px}
  </style>
</head>
<body>
  <h2>Deliver package: $Seed (Top $k modules)</h2>
  <p>Query: <code>$q</code></p>
  <table>
    <tr><th>Module</th><th>Size</th><th>Label</th><th>Interactive</th><th>Hubs</th><th>GraphML</th><th>Zip</th><th>Enrich</th></tr>
    $($rows -join "`n")
  </table>
  <p style="color:#666;margin-top:12px">
    graphml 可直接匯入 Cytoscape；enrich 若 API 沒有/網路被擋會自動顯示 n/a（不影響交付包）。
  </p>
</body>
</html>
"@ | Set-Content -Encoding UTF8 $indexPath

# zip whole deliver folder
$zipAll = Join-Path (Split-Path $outDir -Parent) ("deliver_{0}_{1}.zip" -f $Seed, $stamp)
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipAll -Force

Ok "Done: $outDir"
Ok "Zip : $zipAll"
Start-Process $indexPath
Start-Process (Split-Path $outDir)
