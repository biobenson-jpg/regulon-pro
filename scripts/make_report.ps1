param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Seed = "TP53",
  [string]$Sources = "string_ppi,encori_rbp_by_target",
  [string]$Extra = ""
)

$ErrorActionPreference = "Stop"

function HtmlEncode([string]$s) {
  return [System.Net.WebUtility]::HtmlEncode($s)
}

# repo root & output dir
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$outDir = Join-Path $repo "out"
mkdir $outDir -Force | Out-Null

# health check
try {
  $h = Invoke-RestMethod "$BaseUrl/health"
} catch {
  Write-Host "❌ API 沒有在跑：請先在另一個 PowerShell 視窗執行： uvicorn app.main:app --reload" -ForegroundColor Red
  exit 1
}

# build query string
$q = "seed=$Seed&sources=$Sources"
if ($Extra -and $Extra.Trim().Length -gt 0) { $q = "$q&$Extra" }

$netUrl     = "$BaseUrl/viz/network?$q"
$pngUrl     = "$BaseUrl/viz/hubs.png?$q"
$metricsUrl = "$BaseUrl/viz/metrics?$q"

# fetch metrics
$metrics = Invoke-RestMethod $metricsUrl
$nodes = $metrics.nodes
$edges = $metrics.edges

# download hubs png and base64 it (for portable report)
$pngPath = Join-Path $outDir ("{0}_hubs.png" -f $Seed)
Invoke-WebRequest $pngUrl -OutFile $pngPath | Out-Null
$pngB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($pngPath))

# fetch network html
$netHtml = (Invoke-WebRequest $netUrl).Content

# build tables
$topDegRows = ""
foreach ($x in ($metrics.top_degree | Select-Object -First 20)) {
  $topDegRows += "<tr><td>$(HtmlEncode $x.node)</td><td style='text-align:right'>$(HtmlEncode $x.degree)</td></tr>`n"
}

$topBtwRows = ""
if ($metrics.top_betweenness) {
  foreach ($x in ($metrics.top_betweenness | Select-Object -First 20)) {
    $bv = "{0:N6}" -f $x.betweenness
    $topBtwRows += "<tr><td>$(HtmlEncode $x.node)</td><td style='text-align:right'>$(HtmlEncode $bv)</td></tr>`n"
  }
}
if (-not $topBtwRows) {
  $topBtwRows = "<tr><td colspan='2' style='color:#666'>no betweenness (or skipped)</td></tr>"
}

# header injected into the pyvis html body
$header = @"
<div style="font-family:Segoe UI, Arial; padding:16px 20px; max-width:1200px;">
  <h2 style="margin:0 0 6px 0;">Interactome report</h2>
  <div style="color:#666; margin-bottom:14px;">
    seed: <b>$(HtmlEncode $Seed)</b> |
    sources: <b>$(HtmlEncode $Sources)</b> |
    nodes: <b>$(HtmlEncode $nodes)</b> |
    edges: <b>$(HtmlEncode $edges)</b>
  </div>

  <div style="display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start;">
    <div style="flex:1; min-width:320px;">
      <h3>Top degree</h3>
      <table class="list"><tr><th>node</th><th style="text-align:right">degree</th></tr>
      $topDegRows
      </table>

      <h3 style="margin-top:14px;">Top betweenness</h3>
      <table class="list"><tr><th>node</th><th style="text-align:right">betweenness</th></tr>
      $topBtwRows
      </table>
    </div>

    <div style="flex:2; min-width:420px;">
      <h3>Top hubs (degree)</h3>
      <img src="data:image/png;base64,$pngB64" style="max-width:100%; border:1px solid #ddd; border-radius:6px;" />
      <div style="color:#666; margin-top:8px;">
        network url: <a href="$netUrl">$(HtmlEncode $netUrl)</a>
      </div>
    </div>
  </div>

  <hr style="margin:16px 0;">
  <div style="color:#666; margin-bottom:6px;">Interactive network below (drag / zoom / click nodes)</div>
</div>

<style>
  table.list { border-collapse: collapse; width:100%; }
  table.list th, table.list td { border:1px solid #eee; padding:6px 8px; }
  table.list th { background:#fafafa; }
</style>
"@

# inject after <body ...>
$reportHtml = [regex]::Replace($netHtml, "(?i)<body[^>]*>", { param($m) $m.Value + $header })

$reportPath = Join-Path $outDir ("report_{0}.html" -f $Seed)
Set-Content -Encoding UTF8 $reportPath $reportHtml

Write-Host "✅ Report saved: $reportPath" -ForegroundColor Green
Start-Process $reportPath
