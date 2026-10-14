param([string]$JavaHome = $env:JAVA_HOME)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$previousJavaHome = $env:JAVA_HOME
$previousPython = $env:MCP_PYTHON_EXECUTABLE
try {
    if ($JavaHome) { $env:JAVA_HOME = $JavaHome }
    Push-Location (Join-Path $taskRoot 'agent-service')
    try {
        uv sync --frozen --group dev
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency sync failed' }
        $env:MCP_PYTHON_EXECUTABLE = (uv run python -c 'import sys; print(sys.executable)').Trim()
    } finally { Pop-Location }
    Push-Location (Join-Path $taskRoot 'business-service')
    try {
        .\mvnw.cmd -B -ntp '-Dtest=AgentMcpIntegrationTest' test
        if ($LASTEXITCODE -ne 0) { throw 'MCP cross-language tests failed' }
    } finally { Pop-Location }
} finally {
    $env:JAVA_HOME = $previousJavaHome
    $env:MCP_PYTHON_EXECUTABLE = $previousPython
}
