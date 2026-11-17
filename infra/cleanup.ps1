<#
SmartBike AI - stop costs after the demo.
Usage: ./infra/cleanup.ps1 [-Env dev] [-Suffix ab12] [-KeepData]
#>
param([string]$Env = "dev", [string]$Suffix = "", [switch]$KeepData)
$ErrorActionPreference = "Stop"
$rg = "rg-smartbike-$Env"

Write-Host "1. Stop the GBFS collector (Ctrl+C in its terminal / stop the scheduled task)."
Write-Host "2. Fabric: pause the capacity (Azure portal > Fabric capacity > Pause) or let the trial expire;"
Write-Host "   delete the Eventstream (main streaming cost) and the Real-Time dashboard auto-refresh."
Write-Host "   Optional CLI: az fabric capacity suspend --resource-group <rg> --capacity-name <name>"

if ($KeepData) {
    Write-Host "3. KeepData: resource group kept; only the policy assignments are removed." -ForegroundColor Yellow
} else {
    Write-Host "3. Deleting resource group $rg (storage, key vault, log analytics, budget)..." -ForegroundColor Red
    az group delete -n $rg --yes --no-wait
    if ($Suffix) {
        # Key Vault has purge protection: the name stays reserved for the retention period (7 days)
        Write-Host "   Key Vault kv-smartbike-$Env-$Suffix will be purged automatically after 7 days (purge protection)."
    }
}
foreach ($p in "smartbike-allowed-locations", "smartbike-require-project-tag", "smartbike-deny-public-blob") {
    az policy definition delete -n $p 2>$null
}
Write-Host "4. Verify: az resource list --tag project=smartbike-ai -o table   (must be empty)" -ForegroundColor Green
