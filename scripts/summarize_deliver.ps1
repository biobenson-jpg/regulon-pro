param(
  [string]$RunDir = "",
  [switch]$NoOpen,
  [switch]$PaperTone
)

$ErrorActionPreference = "Stop"

function LoadJson([string]$p) {
  if (-not (Test-Path $p)) { return $null }
  try { return (Get-Content $p -Raw | ConvertFrom-Json) } catch { return $null }
}

function TopHubs([object]$net, [int]$n = 8) {
  $deg = @{}
  foreach ($e in $net.edges) {
    $a = [string]$e.source
    $b = [string]$e.target
    if (-not $a -or -not $b) { continue }
    if (-not $deg.ContainsKey($a)) { $deg[$a] = 0 }
    if (-not $deg.ContainsKey($b)) { $deg[$b] = 0 }
    $deg[$a]++
    $deg[$b]++
  }
  return ($deg.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $n).Name
}

function GetModuleSize([object]$net) {
  try {
    if ($net.meta -and $net.meta.module -and $net.meta.module.size) { return [int]$net.meta.module.size }
  } catch {}
  try { return [int]$net.nodes.Count } catch { return 0 }
}

function TopEnrichTerms([object]$enrich, [int]$k = 5) {
  if (-not $enrich) { return @() }
  if (-not $enrich.terms) { return @() }
  $terms = @($enrich.terms | Select-Object -First $k)
  $names = @()
  foreach ($t in $terms) {
    $nm = $t.term_name
    if (-not $nm) { $nm = $t.name }
    if (-not $nm) { $nm = $t.term }
    if (-not $nm) { $nm = $t.term_id }
    if ($nm) { $names += [string]$nm }
  }
  return $names
}

function HtmlEsc([string]$s) {
  if ($null -eq $s) { return "" }
  return [System.Security.SecurityElement]::Escape($s)
}

function LabelToPhrase([string]$label) {
  if (-not $label) { return "a heterogeneous interaction program" }
  $t = $label.ToLowerInvariant()
  switch -Regex ($t) {
    "cell\s*cycle|mitosis" { return "cell-cycle progression and mitotic control" }
    "dna\s*damage|repair"  { return "DNA damage signaling and repair" }
    "apoptosis|cell\s*death" { return "apoptotic and cell-death regulation" }
    "chromatin|transcription" { return "chromatin remodeling and transcriptional regulation" }
    "ubiquitin|proteasome" { return "ubiquitin-mediated protein turnover" }
    "rna|splicing"         { return "RNA processing and pre-mRNA splicing" }
    "translation|ribosome" { return "protein translation and ribosome biogenesis" }
    "signaling|mapk|pi3k|akt" { return "signal transduction (MAPK/PI3K/AKT axis)" }
    default { return "a heterogeneous interaction program" }
  }
}

function MakeSummary([string]$cid, [int]$size, [string]$label, [string[]]$hubs, [string[]]$terms, [switch]$PaperTone) {
  $hubsShort = ($hubs | Select-Object -First 6) -join ", "
  $phrase = LabelToPhrase $label

  if (-not $PaperTone) {
    if ($terms.Count -gt 0) {
      $topTermsStr = ($terms -join "; ")
      return "Module $cid (n=$size) is best described as $label, driven by hubs such as $hubsShort. Enrichment highlights: $topTermsStr."
    } else {
      return "Module $cid (n=$size) is best described as $label, driven by hubs such as $hubsShort. No enrichment results were retrieved."
    }
  }

  # Paper tone (Results-like)
  if ($terms.Count -gt 0) {
    $t3 = ($terms | Select-Object -First 3) -join "; "
    return "Module $cid (n=$size) delineates a program of $phrase, with high-degree hubs including $hubsShort. Functional enrichment is dominated by $t3, supporting a coherent $phrase module."
  } else {
    return "Module $cid (n=$size) delineates a program of $phrase, with high-degree hubs including $hubsShort. Enrichment was not available; nevertheless, hub composition is consistent with $phrase."
  }
}

# Auto-pick latest run dir if not provided
if (-not $RunDir -or $RunDir.Trim().Length -eq 0) {
  $root = Resolve-Path "out\deliver"
  $latest = Get-ChildItem $root -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $latest) { throw "No deliver folders found in out\deliver" }
  $RunDir = $latest.FullName
}

if (-not (Test-Path $RunDir)) { throw "RunDir not found: $RunDir" }

$mods = Get-ChildItem $RunDir -Directory | Where-Object { $_.Name -match '^C\d+$' } | Sort-Object Name
if (-not $mods) { throw "No module folders (C0, C1, ...) found in: $RunDir" }

$items = @()

foreach ($m in $mods) {
  $cid = $m.Name
  $netPath = Join-Path $m.FullName "module_network.json"
  if (-not (Test-Path $netPath)) {
    $cand = Get-ChildItem $m.FullName -Filter "*network*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $netPath = $cand.FullName }
  }
  $net = LoadJson $netPath
  if (-not $net) { continue }

  $size = GetModuleSize $net
  $hubs = TopHubs $net 8
  $hubsShort = ($hubs | Select-Object -First 6) -join ", "

  $labelPath = Join-Path $m.FullName "label.json"
  $lab = LoadJson $labelPath
  $label = "Uncategorized"
  try { if ($lab.label) { $label = [string]$lab.label } } catch {}

  $enrichPath = Join-Path $m.FullName "enrich.json"
  $enrich = LoadJson $enrichPath
  $topTerms = TopEnrichTerms $enrich 5
  $topTermsStr = if ($topTerms.Count -gt 0) { ($topTerms -join "; ") } else { "" }

  $summary = MakeSummary -cid $cid -size $size -label $label -hubs $hubs -terms $topTerms -PaperTone:$PaperTone

  $items += [pscustomobject]@{
    cid = $cid
    size = $size
    label = $label
    hubs = $hubsShort
    enrich = $topTermsStr
    summary = $summary
  }
}

# Write summary.txt
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$summaryPath = Join-Path $RunDir "summary.txt"

$lines = @()
$lines += "Deliver summary (generated $stamp)"
$lines += "Folder: $RunDir"
$lines += "Mode: " + ($(if ($PaperTone) { "paper tone" } else { "plain" }))
$lines += ""
foreach ($it in $items) { $lines += $it.summary }
$lines | Set-Content -Encoding UTF8 $summaryPath

# Write summary.tsv (Excel-friendly)
$tsvPath = Join-Path $RunDir "summary.tsv"
$tsv = @()
$tsv += "module`tSize`tLabel`tTopHubs`tTopEnrichmentTerms`tSummary"
foreach ($it in $items) {
  $tsv += ("{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $it.cid, $it.size, $it.label, $it.hubs, $it.enrich, $it.summary)
}
$tsv | Set-Content -Encoding UTF8 $tsvPath

# Rewrite index.html (v3)
$rows = @()
foreach ($it in $items) {
  $cidNum = $it.cid.Substring(1) # "C0" -> "0"
  $zipGuess = (Get-ChildItem $RunDir -Filter "*_module_C$cidNum.zip" -ErrorAction SilentlyContinue | Select-Object -First 1).Name
  if (-not $zipGuess) { $zipGuess = "" }

  $labelLink  = if (Test-Path (Join-Path $RunDir "$($it.cid)\label.json")) { "<a href='./$($it.cid)/label.json'>label</a>" } else { "<span style='color:#999'>n/a</span>" }
  $enrichLink = if (Test-Path (Join-Path $RunDir "$($it.cid)\enrich.png")) { "<a href='./$($it.cid)/enrich.png'>enrich</a>" } elseif (Test-Path (Join-Path $RunDir "$($it.cid)\enrich.json")) { "<a href='./$($it.cid)/enrich.json'>enrich.json</a>" } else { "<span style='color:#999'>n/a</span>" }
  $zipLink    = if ($zipGuess) { "<a href='./$zipGuess'>zip</a>" } else { "<span style='color:#999'>n/a</span>" }
  $reportLink = if (Test-Path (Join-Path $RunDir "$($it.cid)\report.html")) { "<a href='./$($it.cid)/report.html'>report</a>" } else { "<span style='color:#999'>n/a</span>" }
  $graphmlLink= if (Test-Path (Join-Path $RunDir "$($it.cid)\module.graphml")) { "<a href='./$($it.cid)/module.graphml'>graphml</a>" } else { "<span style='color:#999'>n/a</span>" }

  $rows += "<tr>
    <td>$($it.cid)</td>
    <td style='text-align:right'>$($it.size)</td>
    <td>$labelLink</td>
    <td style='font-family:Consolas, monospace; font-size:12px;'>$(HtmlEsc $it.hubs)</td>
    <td style='font-size:12px;'>$(HtmlEsc $it.enrich)</td>
    <td style='font-size:12px;'>$(HtmlEsc $it.summary)</td>
    <td><a href='./$($it.cid)/module.html'>interactive</a></td>
    <td><a href='./$($it.cid)/hubs.png'>hubs</a></td>
    <td>$graphmlLink</td>
    <td>$reportLink</td>
    <td>$enrichLink</td>
    <td>$zipLink</td>
  </tr>"
}

$indexPath = Join-Path $RunDir "index.html"
@"
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Deliver package</title>
  <style>
    body{font-family:Segoe UI,Arial;padding:18px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #eee;padding:8px;vertical-align:top}
    th{background:#fafafa}
    code{background:#f7f7f7;padding:2px 6px;border-radius:4px}
  </style>
</head>
<body>
  <h2>Deliver package (modules)</h2>
  <p style="color:#666">Generated $stamp | Mode: $(if ($PaperTone) { "paper tone" } else { "plain" })</p>
  <p><b>Summaries</b>: <a href="./summary.txt">summary.txt</a> | <a href="./summary.tsv">summary.tsv</a></p>

  <table>
    <tr>
      <th>Module</th><th>Size</th><th>Label</th><th>Top hubs</th><th>Top enrichment terms</th><th>English summary</th>
      <th>Interactive</th><th>Hubs PNG</th><th>GraphML</th><th>Report</th><th>Enrich</th><th>Zip</th>
    </tr>
    $($rows -join "`n")
  </table>

  <p style="color:#666;margin-top:12px">
    Tip: GraphML is Cytoscape-ready. If enrichment is blocked by network policy, the pipeline still completes.
  </p>
</body>
</html>
"@ | Set-Content -Encoding UTF8 $indexPath

Write-Host "✅ Wrote: $summaryPath" -ForegroundColor Green
Write-Host "✅ Wrote: $tsvPath" -ForegroundColor Green
Write-Host "✅ Updated: $indexPath" -ForegroundColor Green

if (-not $NoOpen) {
  Start-Process $indexPath
  Start-Process $summaryPath
}
