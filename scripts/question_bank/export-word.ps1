param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference = 'Stop'
$items = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$word.AutomationSecurity = 3
try {
  foreach ($item in $items) {
    if (Test-Path -LiteralPath $item.pdf) { continue }
    $document = $null
    try {
      $document = $word.Documents.Open([string]$item.source, $false, $true, $false)
      $document.ExportAsFixedFormat([string]$item.pdf, 17)
      Write-Output ([string]$item.id + ' exported')
    } catch {
      Write-Output ([string]$item.id + ' failed ' + $_.Exception.GetType().Name)
    } finally {
      if ($null -ne $document) {
        $document.Close(0)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document)
      }
    }
  }
} finally {
  $word.Quit(0)
  [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
