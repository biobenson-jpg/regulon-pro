$ErrorActionPreference="Continue"
$base="http://127.0.0.1:8000"

function Try($name, $url) {
  try {
    $r = irm $url
    Write-Host "✅ $name" -ForegroundColor Green
  } catch {
    Write-Host "❌ $name -> $($_.Exception.Message)" -ForegroundColor Red
  }
}

Try "health"   "$base/health"
Try "routes"   "$base/routes"
Try "sources"  "$base/sources"
Try "report"   "$base/report?seed=TP53&sources=string_ppi,encori_rbp_by_target"
Try "hubs png" "$base/viz/hubs.png?seed=TP53&sources=string_ppi,encori_rbp_by_target"
Try "communities json" "$base/viz/communities?seed=TP53&sources=string_ppi,encori_rbp_by_target"
Try "community sizes png" "$base/viz/community_sizes.png?seed=TP53&sources=string_ppi,encori_rbp_by_target"
