try {
  $h = irm "http://127.0.0.1:8000/health"
  $h | Format-Table
} catch {
  Write-Host "API not responding on http://127.0.0.1:8000 (is uvicorn running?)" -ForegroundColor Yellow
}
