$ErrorActionPreference = 'Stop'
$diagramRoot = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $diagramRoot '../../..')).Path
$cli = Join-Path $projectRoot '.runtime/tools/architecture-mermaid/node_modules/.bin/mmdc.cmd'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$puppeteerConfig = Join-Path $projectRoot '.runtime/tools/architecture-mermaid/puppeteer.json'
@{ executablePath = $chrome; headless = $true } | ConvertTo-Json | Set-Content -LiteralPath $puppeteerConfig -Encoding utf8

foreach ($source in Get-ChildItem -LiteralPath $diagramRoot -Filter '*.mmd') {
    foreach ($format in @('svg', 'png')) {
        $target = Join-Path $diagramRoot ($source.BaseName + '.' + $format)
        & $cli -i $source.FullName -o $target -c (Join-Path $diagramRoot 'mermaid.config.json') -p $puppeteerConfig -b white --size 2600 -s 1.5
        if ($LASTEXITCODE -ne 0) { throw "Mermaid render failed: $($source.Name) -> $format" }
    }
}
