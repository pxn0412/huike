param([Parameter(Mandatory=$true)][string]$SourcePath,
      [Parameter(Mandatory=$true)][string]$TargetPath,
      [Parameter(Mandatory=$true)][string]$OwnerFile,
      [switch]$CleanupOnly)
$ErrorActionPreference = 'Stop'
if ($CleanupOnly) {
    if (Test-Path -LiteralPath $OwnerFile) {
        $record = Get-Content -LiteralPath $OwnerFile -Raw | ConvertFrom-Json
        $owned = Get-Process -Id $record.ProcessId -ErrorAction SilentlyContinue
        if ($owned -and $owned.ProcessName -eq 'WINWORD' -and
            $owned.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.StartTicks) {
            Stop-Process -Id $owned.Id
        }
    }
    exit
}
$existingIds = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class WordProcessOwner { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId); }'
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    [uint32]$wordProcessId = 0
    [void][WordProcessOwner]::GetWindowThreadProcessId([IntPtr]$word.Hwnd, [ref]$wordProcessId)
    if ($wordProcessId -and $existingIds -notcontains $wordProcessId) {
        $owned = Get-Process -Id $wordProcessId
        @{ ProcessId=$wordProcessId; StartTicks=$owned.StartTime.ToUniversalTime().Ticks.ToString() } |
            ConvertTo-Json | Set-Content -LiteralPath $OwnerFile -Encoding UTF8
    }
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    # Read-only, no recent-file entry; a supplied nonempty password prevents an interactive prompt.
    $document = $word.Documents.Open([ref]$SourcePath, [ref]$false, [ref]$true, [ref]$false, [ref]'huike-no-password')
    $document.SaveAs2([ref]$TargetPath, [ref]12)
} finally {
    if ($null -ne $document) {
        $document.Close([ref]0)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($null -ne $word) {
        $word.Quit([ref]0)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
    }
}
