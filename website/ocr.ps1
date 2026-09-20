param([Parameter(Mandatory=$true)][string]$ImageDirectory)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Wait-WinRT($Operation, [Type]$ResultType) {
    $task = $taskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}
$language = [Windows.Globalization.Language]::new('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if (-not $engine) { throw 'Chinese OCR language is not installed.' }
$results = @()
foreach ($imageFile in (Get-ChildItem -LiteralPath $ImageDirectory -Filter '*.png' | Sort-Object Name)) {
    $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imageFile.FullName)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Wait-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $text = ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
            $results += @{ file=$imageFile.Name; text=$text }
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
}
ConvertTo-Json -InputObject @($results) -Depth 4 -Compress
