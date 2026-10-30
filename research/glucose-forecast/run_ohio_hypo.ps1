$ErrorActionPreference = 'Stop'
# 数据集不在仓库里：用 GLUCOSE_DATA_ROOT 指向本地解压目录，默认仓库旁的 data/glucose。
$here = $PSScriptRoot
$env:PYTHONPATH = Join-Path $here 'src'
$env:PYTHONIOENCODING = 'utf-8'
$out = if ($env:GLUCOSE_DATA_ROOT) { $env:GLUCOSE_DATA_ROOT } else { Join-Path $here 'data/glucose' }
Set-Location $here
foreach ($seed in 0, 1, 2) {
    Write-Output "===== ohio seed $seed ====="
    py -3 -m glucose_forecast.evaluate_hypo --dataset ohio --seed $seed --horizon-points 6 12 --json "$out\hypo-ohio-seed$seed.json" 2>&1 | Select-Object -Last 24
}
Write-Output 'done'
