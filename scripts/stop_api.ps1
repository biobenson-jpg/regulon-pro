Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "uvicorn\s+app\.main:app" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
