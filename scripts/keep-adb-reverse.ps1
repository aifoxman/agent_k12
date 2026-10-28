param(
    [string[]]$Serials = @(),
    [ValidateRange(1, 65535)][int]$Port = 8002,
    [ValidateRange(1, 60)][int]$PollSeconds = 3,
    [switch]$Once
)

# PowerShell -File receives comma-separated arguments as one string.
$Serials = @($Serials | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

$bundledAdb = Join-Path (Split-Path -Parent $PSScriptRoot) '.tools\sdk\platform-tools\adb.exe'
if (Test-Path -LiteralPath $bundledAdb) {
    $adb = $bundledAdb
} else {
    $command = Get-Command adb -ErrorAction SilentlyContinue
    if (-not $command) { throw 'adb was not found. Install Android platform-tools first.' }
    $adb = $command.Source
}

do {
    $connected = @(& $adb devices | ForEach-Object {
        if ($_ -match '^(\S+)\s+device\s*$') { $Matches[1] }
    })
    $targets = if ($Serials.Count) { $Serials } else { $connected }
    foreach ($serial in $targets) {
        if ($connected -notcontains $serial) { continue }
        $entries = @(& $adb -s $serial reverse --list 2>$null)
        if ($LASTEXITCODE -ne 0) { continue }
        $mapped = @($entries | Where-Object { $_ -match "tcp:$Port\s+tcp:$Port\s*$" }).Count -gt 0
        if (-not $mapped) {
            & $adb -s $serial reverse "tcp:$Port" "tcp:$Port" | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Output "Restored API port $Port for $serial" }
        }
    }
    if (-not $Once) { Start-Sleep -Seconds $PollSeconds }
} while (-not $Once)
