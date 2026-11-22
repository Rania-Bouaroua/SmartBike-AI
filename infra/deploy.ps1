<#
SmartBike AI - Azure resources (operational storage, secrets, governance, monitoring, budget).
Fabric items (workspace, Lakehouse, Eventstream, Eventhouse, notebooks, report) are created in the
Fabric portal - see docs/reproducibility.md.

Prerequisites: Azure CLI (az login), rights to create resources + role assignments on the subscription.
Usage:
  ./infra/deploy.ps1 -Suffix ab12 -BudgetEmail me@school.ma                                   # everything but the teammates' roles
  ./infra/deploy.ps1 -Suffix ab12 -BudgetEmail me@school.ma -EngineerUpn a@school.ma -AnalystUpn b@school.ma
Re-running is safe (idempotent): existing resources are updated, not duplicated.

Region: germanywestcentral by default - the university subscription only allows italynorth, norwayeast, switzerlandnorth,
polandcentral and germanywestcentral (tenant policy "Allowed resource deployment regions").
Naming convention: <resource-type>-smartbike-<env>[-suffix]   (storage/kv names must be globally unique)
#>
param(
    [string]$Env = "dev",
    [string]$Location = "germanywestcentral",
    [Parameter(Mandatory = $true)][string]$Suffix,
    [Parameter(Mandatory = $true)][string]$BudgetEmail,
    [string]$EngineerUpn = "",
    [string]$AnalystUpn = "",
    [int]$BudgetEur = 20
)
$ErrorActionPreference = "Stop"

# Windows PowerShell does not stop on a failing native command: check every az exit code.
function Invoke-Az {
    # warnings on stderr (e.g. "account already exists" on a re-run) must not stop the script: only the exit code counts
    $ErrorActionPreference = "Continue"
    $out = & az @args --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed (exit $LASTEXITCODE)" }
    return $out
}

$rg      = "rg-smartbike-$Env"
$st      = "stsmartbike$Env$Suffix"          # ADLS Gen2 landing zone (Bronze physical storage)
$kv      = "kv-smartbike-$Env-$Suffix"       # Key Vault (Eventstream connection string, etc.)
$law     = "log-smartbike-$Env"              # Log Analytics (audit logs)
$tags    = @("project=smartbike-ai", "env=$Env", "owner=cdda-group", "course=CDDA-2026-2027", "cost-center=education")
$subId   = Invoke-Az account show --query id -o tsv
$scopeRg = "/subscriptions/$subId/resourceGroups/$rg"

Write-Host "== 1. Resource group (tagged) in $Location" -ForegroundColor Cyan
Invoke-Az group create -n $rg -l $Location --tags $tags | Out-Null

Write-Host "== 2. Governance: custom Azure Policies (policy-as-code) assigned on the resource group" -ForegroundColor Cyan
$policies = @(
    @{ name = "smartbike-allowed-locations"; file = "security/policies/allowed_locations.json";
       params = '{\"allowedLocations\":{\"value\":[\"germanywestcentral\",\"global\"]}}' },
    @{ name = "smartbike-require-project-tag"; file = "security/policies/require_tag.json";
       params = '{\"tagName\":{\"value\":\"project\"}}' },
    @{ name = "smartbike-deny-public-blob"; file = "security/policies/deny_storage_public_access.json"; params = '{}' }
)
foreach ($p in $policies) {
    $def = Get-Content $p.file -Raw | ConvertFrom-Json
    $rules = $def.properties.policyRule | ConvertTo-Json -Depth 20 -Compress
    $pdef  = $def.properties.parameters  | ConvertTo-Json -Depth 20 -Compress
    $rules | Set-Content "$env:TEMP\rule.json"; $pdef | Set-Content "$env:TEMP\params.json"
    Invoke-Az policy definition create -n $p.name --display-name $def.properties.displayName `
        --rules "$env:TEMP\rule.json" --params "$env:TEMP\params.json" --mode $def.properties.mode | Out-Null
    Invoke-Az policy assignment create -n $p.name --policy $p.name --scope $scopeRg --params $p.params | Out-Null
    Write-Host "   $($p.name)"
}

Write-Host "== 3. ADLS Gen2 storage account (private by default, TLS 1.2, no shared keys)" -ForegroundColor Cyan
Invoke-Az storage account create -n $st -g $rg -l $Location --sku Standard_LRS --kind StorageV2 `
    --hns true --min-tls-version TLS1_2 --allow-blob-public-access false `
    --allow-shared-key-access false --https-only true --tags $tags | Out-Null
$stId = Invoke-Az storage account show -n $st -g $rg --query id -o tsv
$me = Invoke-Az ad signed-in-user show --query id -o tsv
# Shared keys are disabled, so even the deployer needs a data-plane role to create the container.
Invoke-Az role assignment create --assignee-object-id $me --assignee-principal-type User `
    --role "Storage Blob Data Owner" --scope $stId | Out-Null
$ok = $false
$ErrorActionPreference = "Continue"   # a failed attempt must not stop the script (stderr is not fatal here)
foreach ($i in 1..12) {  # role assignments take up to a few minutes to propagate
    & az storage container create --account-name $st -n landing --auth-mode login --only-show-errors 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Write-Host "   waiting for the data role to propagate ($i/12)..."; Start-Sleep -Seconds 20
}
$ErrorActionPreference = "Stop"
if (-not $ok) { throw "could not create container 'landing' (role not propagated yet?) - re-run the script" }
# Lifecycle: raw archives move to Cool after 30 days, are deleted after 365 days (retention policy)
Invoke-Az storage account management-policy create --account-name $st -g $rg --policy "@infra/lifecycle_policy.json" | Out-Null

Write-Host "== 4. Key Vault (RBAC mode, purge protection, soft delete)" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
$existingKv = & az keyvault show -n $kv --query id -o tsv 2>$null
$ErrorActionPreference = "Stop"
if (-not $existingKv) {
    Invoke-Az keyvault create -n $kv -g $rg -l $Location --enable-rbac-authorization true `
        --enable-purge-protection true --retention-days 7 --tags $tags | Out-Null
}
$kvId = Invoke-Az keyvault show -n $kv --query id -o tsv
# Admin (deployer): Owner already. Needs to write the secret once:
Invoke-Az role assignment create --assignee-object-id $me --assignee-principal-type User `
    --role "Key Vault Secrets Officer" --scope $kvId | Out-Null

Write-Host "== 5. Least-privilege role assignments (3 personas)" -ForegroundColor Cyan
if ($EngineerUpn -and $AnalystUpn) {
    $engineer = Invoke-Az ad user show --id $EngineerUpn --query id -o tsv
    $analyst  = Invoke-Az ad user show --id $AnalystUpn  --query id -o tsv
    # Data Engineer: read/write data in the landing zone + read secrets used by the collector. No Owner/Contributor on the RG.
    Invoke-Az role assignment create --assignee-object-id $engineer --assignee-principal-type User `
        --role "Storage Blob Data Contributor" --scope $stId | Out-Null
    Invoke-Az role assignment create --assignee-object-id $engineer --assignee-principal-type User `
        --role "Key Vault Secrets User" --scope $kvId | Out-Null
    # Analyst / Reader: NO access to raw storage nor secrets. Only the Fabric workspace "Viewer" role (set in Fabric).
    Invoke-Az role assignment create --assignee-object-id $analyst --assignee-principal-type User `
        --role "Reader" --scope $scopeRg | Out-Null
    Write-Host "   Engineer = $EngineerUpn ; Analyst = $AnalystUpn"
} else {
    Write-Host "   skipped (no -EngineerUpn / -AnalystUpn): re-run the script with both to add the teammates' roles" -ForegroundColor Yellow
}

Write-Host "== 6. Monitoring & audit: Key Vault + Storage logs to Log Analytics" -ForegroundColor Cyan
Invoke-Az monitor log-analytics workspace create -g $rg -n $law -l $Location --retention-time 30 --tags $tags | Out-Null
$lawId = Invoke-Az monitor log-analytics workspace show -g $rg -n $law --query id -o tsv
Invoke-Az monitor diagnostic-settings create -n diag-kv --resource $kvId --workspace $lawId `
    --logs '[{\"category\":\"AuditEvent\",\"enabled\":true}]' | Out-Null
Invoke-Az monitor diagnostic-settings create -n diag-st-blob --resource "$stId/blobServices/default" --workspace $lawId `
    --logs '[{\"category\":\"StorageRead\",\"enabled\":true},{\"category\":\"StorageWrite\",\"enabled\":true},{\"category\":\"StorageDelete\",\"enabled\":true}]' | Out-Null

Write-Host "== 7. Cost control: monthly budget" -ForegroundColor Cyan
$start = (Get-Date -Day 1).ToString("yyyy-MM-01")
$end   = (Get-Date -Day 1).AddMonths(6).ToString("yyyy-MM-01")
$ErrorActionPreference = "Continue"
& az consumption budget create --budget-name budget-smartbike-$Env --amount $BudgetEur --category cost `
    --time-grain monthly --start-date $start --end-date $end --resource-group $rg --only-show-errors 2>&1 | Out-Null
$budgetOk = $LASTEXITCODE -eq 0
$ErrorActionPreference = "Stop"
if ($budgetOk) {
    Write-Host "   budget $BudgetEur EUR/month on $rg; add the 80 % e-mail alert to $BudgetEmail in Cost Management > Budgets"
} else {
    # Some offers (e.g. Azure for Students / sponsorships) do not support budgets through this API.
    Write-Host "   budget API not available on this subscription offer: cost is tracked with the credit balance" -ForegroundColor Yellow
    Write-Host "   (portal > Subscriptions > Azure for Students) and the cost analysis filtered on tag project=smartbike-ai"
}

Write-Host "`n== Inventory (tag project=smartbike-ai)" -ForegroundColor Green
Invoke-Az resource list -g $rg --query "[].{name:name, type:type, location:location}" -o table

Write-Host "`nDone. Next steps:" -ForegroundColor Green
Write-Host " 1. Create the Eventstream custom endpoint in Fabric, then store its connection string:"
Write-Host "    az keyvault secret set --vault-name $kv -n eventstream-conn --value '<eventstream-connection-string>'"
Write-Host " 2. In the Lakehouse lh_smartbike: New shortcut > ADLS Gen2 > https://$st.dfs.core.windows.net/landing  -> name it 'bronze' under Files/"
Write-Host " 3. Run the collector: python scripts/gbfs_collector.py --keyvault $kv --only-changes"
