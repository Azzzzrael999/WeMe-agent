[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$RepairEmptyGeneratedFile
)

$examplePath = Join-Path $RepositoryRoot '.env.example'
$targetPath = Join-Path $RepositoryRoot '.env'

if (Test-Path -LiteralPath $targetPath) {
    if (-not $RepairEmptyGeneratedFile) {
        Write-Output "Existing .env preserved: $targetPath"
        exit 0
    }

    $existingContent = Get-Content -Raw -Encoding UTF8 -LiteralPath $targetPath
    $secretKeys = @(
        'MYSQL_ROOT_PASSWORD',
        'BUSINESS_DB_PASSWORD',
        'AGENT_DB_PASSWORD',
        'REDIS_PASSWORD',
        'JWT_SECRET',
        'AGENT_CONTEXT_JWT_SECRET',
        'INTERNAL_SERVICE_TOKEN'
    )
    $hasNonEmptySecret = $secretKeys | Where-Object {
        $existingContent -match ('(?m)^' + [Regex]::Escape($_) + '=.+$')
    }
    if ($hasNonEmptySecret) {
        throw 'Repair refused because the existing .env contains non-empty secrets.'
    }
}

if (-not (Test-Path -LiteralPath $examplePath)) {
    throw "Missing template: $examplePath"
}

function New-RandomHex([int]$ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    }
    finally {
        $random.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

$content = Get-Content -Raw -Encoding UTF8 -LiteralPath $examplePath
$replacements = [ordered]@{
    MYSQL_ROOT_PASSWORD      = New-RandomHex 24
    BUSINESS_DB_PASSWORD     = New-RandomHex 24
    AGENT_DB_PASSWORD        = New-RandomHex 24
    REDIS_PASSWORD           = New-RandomHex 24
    JWT_SECRET               = New-RandomHex 48
    AGENT_CONTEXT_JWT_SECRET = New-RandomHex 48
    INTERNAL_SERVICE_TOKEN   = New-RandomHex 32
}

foreach ($entry in $replacements.GetEnumerator()) {
    $pattern = '(?m)^' + [Regex]::Escape($entry.Key) + '=.*$'
    $content = [Regex]::Replace($content, $pattern, "$($entry.Key)=$($entry.Value)")
}

[System.IO.File]::WriteAllText($targetPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Output "Created ignored local environment file: $targetPath"
