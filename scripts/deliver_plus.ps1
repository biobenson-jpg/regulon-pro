param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Seed = "TP53",
  [string]$Sources = "string_ppi,encori_rbp_by_target",
  [string]$Extra = "string_depth=2&string_required_score=400&string_limit=100&string_depth2_limit=10",
  [int]$TopK = 3,
  [string]$OutRoot = "out\deliver",
  [switch]$SkipEnrich,
  [switch]$PaperTone
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repo

# 1) deliver
& (Join-Path $PSScriptRoot "deliver.ps1") -BaseUrl $BaseUrl -Seed $Seed -Sources $Sources -Extra $Extra -TopK $TopK -OutRoot $OutRoot -SkipEnrich:$SkipEnrich

# 2) pick latest run folder for this seed
$root = Join-Path $repo $OutRoot
$latest = Get-ChildItem $root -Directory |
  Where-Object { $_.Name -like "$Seed`_*" } |
  Sort-Object LastWriteTime |
  Select-Object -Last 1

if (-not $latest) { throw "Cannot find latest deliver folder under: $root" }

# 3) summarize + upgrade index
& (Join-Path $PSScriptRoot "summarize_deliver.ps1") -RunDir $latest.FullName -PaperTone:$PaperTone
