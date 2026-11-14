param(
    [int]$Limit = 20,
    [string]$InputFile = 'discogs_bot_preflight_output\stage1_unique_release_candidates.tsv'
)

$ErrorActionPreference = 'Stop'
$secureToken = Read-Host 'Enter Discogs API token (input hidden)' -AsSecureString
$tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
$exitCode = 1

try {
    $env:DISCOGS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)

    if ([IO.Path]::IsPathRooted($InputFile)) {
        $candidateFile = $InputFile
    } else {
        $candidateFile = Join-Path $PSScriptRoot $InputFile
    }

    $verifyScript = Join-Path $PSScriptRoot 'discogs_bot_verify_v6.py'
    $outputDir = Join-Path $PSScriptRoot 'discogs_bot_verify_output_v6'
    $cacheDir = Join-Path $outputDir 'cache'

    if ($Limit -lt 0) {
        throw 'Limit cannot be negative. Use 0 to verify all rows in the selected input file.'
    }
    if (-not (Test-Path -LiteralPath $candidateFile -PathType Leaf)) {
        throw "Input TSV not found: $candidateFile"
    }
    if (-not (Test-Path -LiteralPath $verifyScript -PathType Leaf)) {
        throw "Verification script not found: $verifyScript"
    }

    if ($Limit -gt 0) {
        Write-Host "V6 pilot verification: first $Limit rows from the selected input. Read-only; no MusicBrainz edits will be submitted."
    } else {
        Write-Host 'V6 verification: all rows from the selected input. Read-only; no MusicBrainz edits will be submitted.'
    }
    Write-Host 'V6 checks artist, title, barcode, medium format/count, release year, country, label and catalog number.'
    Write-Host "Input file: $candidateFile"
    Write-Host "Report folder: $outputDir"
    Write-Host "Dedicated v6 cache folder: $cacheDir"

    & py $verifyScript $candidateFile --limit $Limit --out-dir $outputDir --cache-dir $cacheDir
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:DISCOGS_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    Remove-Variable secureToken, tokenPtr -ErrorAction SilentlyContinue
}

exit $exitCode