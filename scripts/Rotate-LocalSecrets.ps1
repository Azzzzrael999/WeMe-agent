[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$envPath = Join-Path $RepositoryRoot '.env'

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing local environment file: $envPath"
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -Encoding UTF8 -LiteralPath $Path) {
        if ($line -match '^([^#=]+)=(.*)$') {
            $values[$matches[1]] = $matches[2]
        }
    }
    return $values
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

$current = Read-DotEnv $envPath
$required = @(
    'MYSQL_ROOT_PASSWORD',
    'BUSINESS_DB_USER',
    'BUSINESS_DB_PASSWORD',
    'AGENT_DB_USER',
    'AGENT_DB_PASSWORD',
    'REDIS_PASSWORD',
    'JWT_SECRET',
    'AGENT_CONTEXT_JWT_SECRET',
    'INTERNAL_SERVICE_TOKEN'
)
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace($current[$name])) {
        throw "Cannot rotate because $name is missing or empty."
    }
}

foreach ($identifierName in @('BUSINESS_DB_USER', 'AGENT_DB_USER')) {
    if ($current[$identifierName] -notmatch '^[A-Za-z0-9_]+$') {
        throw "$identifierName contains unsupported characters."
    }
}

Push-Location $RepositoryRoot
try {
    $runningApps = @(docker compose ps --services --status running 2>$null) |
        Where-Object { $_ -in @('business-service', 'agent-service') }
    if ($runningApps.Count -gt 0) {
        throw 'Stop business-service and agent-service before rotating local infrastructure credentials.'
    }

    $replacement = [ordered]@{
        MYSQL_ROOT_PASSWORD      = New-RandomHex 24
        BUSINESS_DB_PASSWORD     = New-RandomHex 24
        AGENT_DB_PASSWORD        = New-RandomHex 24
        REDIS_PASSWORD           = New-RandomHex 24
        JWT_SECRET               = New-RandomHex 48
        AGENT_CONTEXT_JWT_SECRET = New-RandomHex 48
        INTERNAL_SERVICE_TOKEN   = New-RandomHex 32
    }

    $businessUser = $current['BUSINESS_DB_USER']
    $agentUser = $current['AGENT_DB_USER']
    $sql = @"
ALTER USER '${businessUser}'@'%' IDENTIFIED BY '$($replacement.BUSINESS_DB_PASSWORD)';
ALTER USER '${agentUser}'@'%' IDENTIFIED BY '$($replacement.AGENT_DB_PASSWORD)';
ALTER USER 'root'@'%' IDENTIFIED BY '$($replacement.MYSQL_ROOT_PASSWORD)';
ALTER USER 'root'@'localhost' IDENTIFIED BY '$($replacement.MYSQL_ROOT_PASSWORD)';
FLUSH PRIVILEGES;
"@
    $sql | docker compose exec -T mysql bash -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot'
    if ($LASTEXITCODE -ne 0) {
        throw 'MySQL credential rotation failed.'
    }

    docker compose exec -T `
        -e "NEW_REDIS_PASSWORD=$($replacement.REDIS_PASSWORD)" `
        redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" CONFIG SET requirepass "$NEW_REDIS_PASSWORD" >/dev/null'
    if ($LASTEXITCODE -ne 0) {
        throw 'Redis credential rotation failed.'
    }

    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $envPath
    foreach ($entry in $replacement.GetEnumerator()) {
        $pattern = '(?m)^' + [Regex]::Escape($entry.Key) + '=.*$'
        $content = [Regex]::Replace($content, $pattern, "$($entry.Key)=$($entry.Value)")
    }
    [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))

    docker compose up -d --force-recreate mysql redis
    if ($LASTEXITCODE -ne 0) {
        throw 'Credentials changed, but MySQL/Redis container recreation failed.'
    }

    Write-Output 'Local MySQL, Redis, JWT, and service-token values were rotated without printing them.'
}
finally {
    Pop-Location
}

