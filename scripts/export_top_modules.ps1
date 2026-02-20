param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Seed = "TP53",
  [string]$Sources = "string_ppi,encori_rbp_by_target",
  [string]$Extra = "string_depth=2&string_required_score=400&string_limit=100&string_depth2_limit=10",
  [int]$TopK = 3,
  [string]$OutRoot = "out\modules"
)

$ErrorActionPreference = "Stop"

# repo root
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repo

# build query (no cid here)
$q = "seed=$Seed&sources=$Sources"
if ($Extra -and $Extra.Trim().Length -gt 0) { $q = "$q&$Extra" }

# get community sizes (already sorted by size desc in our API)
$c = Invoke-RestMethod "$BaseUrl/viz/communities?$q"
$sizes = $c.sizes
if (-not $sizes) { throw "No community sizes returned. Is /viz/communities working?" }

$k = [Math]::Min($TopK, $sizes.Count)

$outDir = Join-Path $repo (Join-Path $OutRoot $Seed)
mkdir $outDir -Force | Out-Null

$rows = @()

for ($cid=0; $cid -lt $k; $cid++) {
  $size = $sizes[$cid]
  Write-Host ("⬇️  Exporting C{0} (size={1})" -f $cid, $size) -ForegroundColor Cyan

  # 1) download module zip (portable: JSON/CSV/module.html/hubs.png)
  $zipName = "{0}_module_C{1}.zip" -f $Seed, $cid
  $zipPath = Join-Path $outDir $zipName
  Invoke-WebRequest "$BaseUrl/export_module.zip?cid=$cid&$q" -OutFile $zipPath | Out-Null

  # 2) extract zip to folder
  $dest = Join-Path $outDir ("C{0}" -f $cid)
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  Expand-Archive $zipPath -DestinationPath $dest -Force

  # 3) save module report page (nice to read; note: links inside are meant for online API)
  $repName = "{0}_C{1}_report.html" -f $Seed, $cid
  $repPath = Join-Path $outDir $repName
  Invoke-WebRequest "$BaseUrl/module/report?cid=$cid&$q" -OutFile $repPath | Out-Null

  $rows += "<tr>
    <td>C$cid</td>
    <td style='text-align:right'>$size</td>
    <td><a href='./C$cid/module.html'>module.html</a></td>
    <td><a href='./C$cid/hubs.png'>hubs.png</a></td>
    <td><a href='./$zipName'>zip</a></td>
    <td><a href='./$repName'>report (saved)</a></td>
    <td><a href='$BaseUrl/module/report?cid=$cid&$q'>report (online)</a></td>
  </tr>"
}

$index = @"
<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Top $k modules: $Seed</title>
  <style>
    body{font-family:Segoe UI,Arial; padding:18px;}
    table{border-collapse:collapse; width:100%;}
    th,td{border:1px solid #eee; padding:8px;}
    th{background:#fafafa;}
    code{background:#f7f7f7; padding:2px 6px; border-radius:4px;}
  </style>
</head>
<body>
  <h2>Top $k modules for $Seed</h2>
  <p>Query: <code>$q</code></p>
  <table>
    <tr>
      <th>Module</th><th>Size</th><th>Interactive</th><th>Hubs</th><th>Zip</th><th>Report (saved)</th><th>Report (online)</th>
    </tr>
    $($rows -join "`n")
  </table>
  <p style="color:#666; margin-top:12px;">
    Tip: zip 內的 module.html + hubs.png 是最「可攜」的成果（給別人/換電腦都不怕）。
  </p>
</body>
</html>
"@

$indexPath = Join-Path $outDir "index.html"
Set-Content -Encoding UTF8 $indexPath $index

Write-Host "✅ Done. Open index: $indexPath" -ForegroundColor Green
Start-Process $indexPath
