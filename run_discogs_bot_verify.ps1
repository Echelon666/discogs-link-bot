param(
    [int]$Limit = 20
)

$ErrorActionPreference = 'Stop'
$secureToken = Read-Host 'Enter Discogs API token (input hidden)' -AsSecureString
$tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
$exitCode = 1

try {
    $env:DISCOGS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
    $candidateFile = Join-Path $PSScriptRoot 'discogs_bot_preflight_output\stage1_unique_release_candidates.tsv'
    $verifyScript = Join-Path $PSScriptRoot 'discogs_bot_verify.py'
    $outputDir = Join-Path $PSScriptRoot 'discogs_bot_verify_output'
    $cacheDir = Join-Path $outputDir 'cache'

    if (-not (Test-Path -LiteralPath $candidateFile)) {
        throw "Candidate file not found: $candidateFile"
    }
    if (-not (Test-Path -LiteralPath $verifyScript)) {
        throw "Verification script not found: $verifyScript"
    }

    if ($Limit -gt 0) {
        Write-Host "V5 pilot verification: first $Limit records. Read-only; no MusicBrainz edits will be submitted."
    } else {
        Write-Host 'V5 full verification. Read-only; no MusicBrainz edits will be submitted.'
    }
    Write-Host 'V5 evaluates all available fields using conservative normalization.'
    Write-Host "Report folder: $outputDir"
    Write-Host "Cache folder: $cacheDir"

    & py $verifyScript $candidateFile --limit $Limit --out-dir $outputDir --cache-dir $cacheDir
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:DISCOGS_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    Remove-Variable secureToken, tokenPtr -ErrorAction SilentlyContinue
}

exit $exitCode
