param(
    [string]$SourceRoot = "H:\voice_process\without_bgm_clarified",
    [string]$DatasetRevision = "v1"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$destination = Join-Path $projectRoot "assets\voices\hanser\training\$DatasetRevision"
$utf8 = [System.Text.UTF8Encoding]::new($false)

$wavFiles = @(Get-ChildItem -LiteralPath $source -Filter "*.wav" -File | Sort-Object Name)
if ($wavFiles.Count -eq 0) {
    throw "No WAV files found in $source"
}

New-Item -ItemType Directory -Force -Path $destination | Out-Null
$datasetLines = [System.Collections.Generic.List[string]]::new()
$manifestLines = [System.Collections.Generic.List[string]]::new()

foreach ($wav in $wavFiles) {
    $transcriptPath = Join-Path $source ($wav.BaseName + ".txt")
    if (-not (Test-Path -LiteralPath $transcriptPath -PathType Leaf)) {
        throw "Missing transcript for $($wav.Name): $transcriptPath"
    }
    $transcript = (Get-Content -Raw -LiteralPath $transcriptPath).Trim()
    if (-not $transcript -or $transcript.Contains("|") -or $transcript.Contains("`n")) {
        throw "Transcript must be one non-empty line without '|': $transcriptPath"
    }

    Copy-Item -LiteralPath $wav.FullName -Destination (Join-Path $destination $wav.Name) -Force
    Copy-Item -LiteralPath $transcriptPath -Destination (Join-Path $destination ($wav.BaseName + ".txt")) -Force
    $datasetLines.Add("$($wav.Name)|hanser|zh|$transcript")
    $manifestLines.Add((
        [ordered]@{
            asset_id = "hanser-train-$($wav.BaseName)"
            path = $wav.Name
            source_path = $wav.FullName
            transcript = $transcript
            review_status = "user_reviewed"
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $wav.FullName).Hash.ToLowerInvariant()
        } | ConvertTo-Json -Compress
    ))
}

[System.IO.File]::WriteAllLines((Join-Path $destination "dataset.list"), [string[]]$datasetLines, $utf8)
[System.IO.File]::WriteAllLines((Join-Path $destination "manifest.jsonl"), [string[]]$manifestLines, $utf8)

Write-Output "Prepared $($wavFiles.Count) reviewed voice clips in $destination"
