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

    if (-not (Test-Path -LiteralPath $candidateFile)) {
        throw "Candidate file not found: $candidateFile"
    }
    if (-not (Test-Path -LiteralPath $verifyScript)) {
        throw "Verification script not found: $verifyScript"
    }

    if ($Limit -gt 0) {
        Write-Host "Pilot verification: first $Limit records. No MusicBrainz edits will be submitted."
    } else {
        Write-Host 'Full candidate verification. No MusicBrainz edits will be submitted.'
    }

    & py $verifyScript $candidateFile --limit $Limit
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:DISCOGS_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    Remove-Variable secureToken, tokenPtr -ErrorAction SilentlyContinue
}

exit $exitCode
