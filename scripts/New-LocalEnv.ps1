[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TargetPath,
    [switch]$RepairEmptyGeneratedFile
)

$examplePath = Join-Path $RepositoryRoot '.env.example'
if ([string]::IsNullOrWhiteSpace($TargetPath)) {
    $targetPath = Join-Path $RepositoryRoot '.env'
}
else {
    $targetPath = [System.IO.Path]::GetFullPath($TargetPath)
}
$targetDirectory = Split-Path -Parent $targetPath

if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) {
    throw "Target directory does not exist: $targetDirectory"
}

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

# Several connection-string lines deliberately repeat the same safe placeholder.
# Replace every occurrence after generating the canonical secret values so Redis
# DB 0 and DB 1 always authenticate with the password used by the Redis service.
$placeholderValues = [ordered]@{
    '__REPLACE_WITH_RANDOM_ROOT_PASSWORD__'     = $replacements['MYSQL_ROOT_PASSWORD']
    '__REPLACE_WITH_RANDOM_BUSINESS_PASSWORD__' = $replacements['BUSINESS_DB_PASSWORD']
    '__REPLACE_WITH_RANDOM_AGENT_PASSWORD__'    = $replacements['AGENT_DB_PASSWORD']
    '__REPLACE_WITH_RANDOM_REDIS_PASSWORD__'    = $replacements['REDIS_PASSWORD']
    '__REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES__'  = $replacements['JWT_SECRET']
    '__REPLACE_WITH_ANOTHER_RANDOM_SECRET__'    = $replacements['AGENT_CONTEXT_JWT_SECRET']
    '__REPLACE_WITH_RANDOM_SERVICE_TOKEN__'     = $replacements['INTERNAL_SERVICE_TOKEN']
}

foreach ($entry in $placeholderValues.GetEnumerator()) {
    $content = $content.Replace([string]$entry.Key, [string]$entry.Value)
}

$unresolvedPlaceholders = $placeholderValues.Keys | Where-Object { $content.Contains([string]$_) }
if ($unresolvedPlaceholders) {
    throw 'Environment template still contains an unresolved generated-secret placeholder.'
}

[System.IO.File]::WriteAllText($targetPath, $content, [System.Text.UTF8Encoding]::new($false))
if ($targetPath -eq (Join-Path $RepositoryRoot '.env')) {
    Write-Output "Created ignored local environment file: $targetPath"
}
else {
    Write-Output "Created local environment file: $targetPath"
}
