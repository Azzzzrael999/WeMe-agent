[CmdletBinding()]
param(
    [string]$RepositoryRoot = '',
    [ValidateRange(1025, 65535)]
    [int]$FrontendPort = 18081,
    [string]$ProjectName = "meeting-scheduler-day7-$([Guid]::NewGuid().ToString('N').Substring(0, 8))",
    [switch]$KeepProject,
    [switch]$SkipBuild
)

<#!
.SYNOPSIS
Runs the Day 7 empty-volume acceptance without touching the developer's stack.

.DESCRIPTION
The script generates a temporary safe environment file, starts a distinct
Compose project with fresh project-scoped named volumes, runs the full Day 5
golden path three times (the first includes a real Agent checkpoint restart),
and checks every long-running container is healthy.  On success it stops only
the temporary containers and networks with `down --remove-orphans`; it never
passes `--volumes`/`-v`, so the fresh named volumes are deliberately retained.

Use -KeepProject when inspecting a failed or successful temporary stack.  A
failure also leaves the temporary project and generated environment file in
place for diagnosis instead of deleting evidence.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}

function Assert-AvailablePort {
    param([int]$Port)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    }
    catch {
        throw "Frontend port $Port is already in use. Choose -FrontendPort with an unused port."
    }
    finally {
        $listener.Stop()
    }
}

if ($ProjectName -notmatch '^[a-z0-9][a-z0-9_-]+$') {
    throw 'ProjectName must use only lowercase letters, digits, hyphens, or underscores.'
}
if ($ProjectName -eq 'meeting-scheduler') {
    throw 'The Day 7 empty-volume Smoke must use a distinct Compose project, not meeting-scheduler.'
}

$repositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot)
$composeFile = Join-Path $repositoryRoot 'compose.yaml'
$environmentGenerator = Join-Path $repositoryRoot 'scripts\New-LocalEnv.ps1'
$smokeScript = Join-Path $repositoryRoot 'scripts\smoke-day5.py'
foreach ($requiredPath in @($composeFile, $environmentGenerator, $smokeScript)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required Day 7 Smoke file is missing: $requiredPath"
    }
}

Assert-AvailablePort -Port $FrontendPort

$temporaryRoot = [System.IO.Path]::GetTempPath()
$temporaryDirectory = Join-Path $temporaryRoot ("meeting-scheduler-day7-" + [Guid]::NewGuid().ToString('N'))
$environmentFile = Join-Path $temporaryDirectory '.env'
$previousEnvironment = @{}
$succeeded = $false

function Invoke-IsolatedCompose {
    param([string[]]$Arguments)

    & docker compose --project-name $ProjectName --env-file $environmentFile --file $composeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed for isolated project $ProjectName."
    }
}

try {
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    & $environmentGenerator -RepositoryRoot $repositoryRoot -TargetPath $environmentFile
    if ($LASTEXITCODE -ne 0) {
        throw 'New-LocalEnv.ps1 did not create the temporary environment file.'
    }

    foreach ($name in @('FRONTEND_PORT', 'APP_IMAGE_TAG')) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $env:FRONTEND_PORT = [string]$FrontendPort
    $env:APP_IMAGE_TAG = 'day7'

    Invoke-IsolatedCompose -Arguments @('config', '--quiet')
    $upArguments = @('up', '-d', '--wait', '--wait-timeout', '360')
    if (-not $SkipBuild) {
        $upArguments += '--build'
    }
    Invoke-IsolatedCompose -Arguments $upArguments

    $psJson = @(Invoke-IsolatedCompose -Arguments @('ps', '--format', 'json'))
    $containers = @($psJson | Where-Object { $_ } | ConvertFrom-Json)
    $requiredHealthy = @(
        'mysql', 'redis', 'rocketmq-namesrv', 'rocketmq-broker', 'qdrant',
        'business-service', 'agent-service', 'frontend'
    )
    foreach ($service in $requiredHealthy) {
        $container = $containers | Where-Object { $_.Service -eq $service } | Select-Object -First 1
        if ($null -eq $container -or $container.Health -ne 'healthy') {
            throw "Isolated service $service is not healthy after Compose --wait."
        }
    }

    $publicBase = "http://127.0.0.1:$FrontendPort"
    for ($pass = 1; $pass -le 3; $pass++) {
        $smokeArguments = @($smokeScript, '--public-base', $publicBase, '--public-trace')
        if ($pass -eq 1) {
            $smokeArguments += @(
                '--restart-agent-service',
                '--compose-project', $ProjectName,
                '--compose-env-file', $environmentFile,
                '--compose-file', $composeFile
            )
        }
        & python @smokeArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Golden path pass $pass of 3 failed."
        }
    }

    $succeeded = $true
    [ordered]@{
        day7EmptyVolumeSmoke = 'PASS'
        project              = $ProjectName
        frontendPort         = $FrontendPort
        goldenPathPasses     = 3
        checkpointRestart    = 'PASS'
        qdrantSeed           = 'exercised by Policy retrieval in every golden path'
        cleanup              = if ($KeepProject) { 'containers retained by request; volumes retained' } else { 'containers stopped; named volumes retained' }
    } | ConvertTo-Json -Depth 3
}
finally {
    if ($succeeded -and -not $KeepProject) {
        Invoke-IsolatedCompose -Arguments @('down', '--remove-orphans')
    }

    if ($succeeded -and (Test-Path -LiteralPath $temporaryDirectory)) {
        $resolvedTemporaryDirectory = [System.IO.Path]::GetFullPath($temporaryDirectory)
        $resolvedTemporaryRoot = [System.IO.Path]::GetFullPath($temporaryRoot)
        if (-not $resolvedTemporaryDirectory.StartsWith($resolvedTemporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a path outside the system temporary directory: $resolvedTemporaryDirectory"
        }
        Remove-Item -LiteralPath $resolvedTemporaryDirectory -Recurse -Force
    }

    foreach ($name in $previousEnvironment.Keys) {
        $previous = $previousEnvironment[$name]
        if ($null -eq $previous) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$name" -Value $previous
        }
    }
}
