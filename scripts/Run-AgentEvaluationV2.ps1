param(
    [switch]$SkipRebuildAgent
)

$ErrorActionPreference = "Stop"
$evaluationWorkspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$evaluationArtifactDir = Join-Path $evaluationWorkspace "artifacts\agent-eval-v2"
$evaluationAgentDir = Join-Path $evaluationWorkspace "agent-service"
$evaluationFailures = [System.Collections.Generic.List[string]]::new()

function Assert-EvaluationCommand {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force -Path $evaluationArtifactDir | Out-Null

Push-Location $evaluationAgentDir
try {
    uv run python -m app.evaluation --output "$evaluationArtifactDir\fixture-120.json"
    Assert-EvaluationCommand "fixture evaluation"
}
finally {
    Pop-Location
}

Push-Location $evaluationWorkspace
try {
    docker compose config --quiet
    Assert-EvaluationCommand "Compose configuration"
    if (-not $SkipRebuildAgent) {
        docker compose build agent-service
        Assert-EvaluationCommand "agent-service image build"
        docker compose up -d --no-deps agent-service
        Assert-EvaluationCommand "agent-service replacement"
    }

    $evaluationContainerId = (docker compose ps -q agent-service).Trim()
    Assert-EvaluationCommand "agent-service container lookup"
    if (-not $evaluationContainerId) {
        throw "agent-service container is not running"
    }

    $evaluationHealthy = $false
    for ($evaluationAttempt = 0; $evaluationAttempt -lt 30; $evaluationAttempt++) {
        $evaluationHealth = (docker inspect --format "{{.State.Health.Status}}" $evaluationContainerId).Trim()
        Assert-EvaluationCommand "agent-service health inspection"
        if ($evaluationHealth -eq "healthy") {
            $evaluationHealthy = $true
            break
        }
        if ($evaluationHealth -eq "unhealthy") {
            throw "agent-service became unhealthy"
        }
        Start-Sleep -Seconds 2
    }
    if (-not $evaluationHealthy) {
        throw "agent-service did not become healthy within 60 seconds"
    }

    docker compose exec -T agent-service python -m app.evaluation.live --suite core --repeats 3 --output /tmp/live-core-30x3.json
    $evaluationCoreExitCode = $LASTEXITCODE
    docker cp "$($evaluationContainerId):/tmp/live-core-30x3.json" "$evaluationArtifactDir\live-core-30x3.json"
    Assert-EvaluationCommand "live core evidence copy"
    if ($evaluationCoreExitCode -ne 0) {
        $evaluationFailures.Add("live core evaluation (exit $evaluationCoreExitCode)")
    }

    docker compose exec -T agent-service python -m app.evaluation.live --suite full --repeats 1 --output /tmp/live-full-120x1.json
    $evaluationFullExitCode = $LASTEXITCODE
    docker cp "$($evaluationContainerId):/tmp/live-full-120x1.json" "$evaluationArtifactDir\live-full-120x1.json"
    Assert-EvaluationCommand "live full evidence copy"
    if ($evaluationFullExitCode -ne 0) {
        $evaluationFailures.Add("live full evaluation (exit $evaluationFullExitCode)")
    }

    python scripts/live-model-trajectory.py --output "$evaluationArtifactDir\trajectory-8.json"
    if ($LASTEXITCODE -ne 0) {
        $evaluationFailures.Add("isolated product trajectories (exit $LASTEXITCODE)")
    }
    python scripts/evaluate-product-scenarios.py --output "$evaluationArtifactDir\product-scenarios-16.json"
    if ($LASTEXITCODE -ne 0) {
        $evaluationFailures.Add("public API product scenarios (exit $LASTEXITCODE)")
    }
    python scripts/build-agent-evaluation-report.py --input-dir $evaluationArtifactDir
    if ($LASTEXITCODE -ne 0) {
        $evaluationFailures.Add("Agent evaluation summary (exit $LASTEXITCODE)")
    }
    if ($evaluationFailures.Count -gt 0) {
        throw "Agent evaluation finished with failures: $($evaluationFailures -join '; ')"
    }
}
finally {
    Pop-Location
}
