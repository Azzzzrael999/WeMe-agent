[CmdletBinding()]
param(
    [string]$FrontendBaseUrl = 'http://localhost',
    [string]$BusinessBaseUrl = 'http://localhost:8080',
    [string]$AgentBaseUrl = 'http://localhost:8000',
    [string]$Username = 'zhangsan',
    [string]$Password = 'demo-password'
)

$ErrorActionPreference = 'Stop'

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if ($Actual -ne $Expected) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

$nginx = Invoke-RestMethod -Uri "$FrontendBaseUrl/health" -Method Get
Assert-Equal $nginx.status 'UP' 'Nginx health failed'

$readiness = Invoke-RestMethod -Uri "$BusinessBaseUrl/actuator/health/readiness" -Method Get
Assert-Equal $readiness.status 'UP' 'Java readiness failed'

$agentHealth = Invoke-RestMethod -Uri "$AgentBaseUrl/internal/v1/health" -Method Get
if ($agentHealth.status -notin @('UP', 'DEGRADED')) {
    throw "Python health returned unexpected status '$($agentHealth.status)'"
}

$loginBody = @{ username = $Username; password = $Password } | ConvertTo-Json
$login = Invoke-RestMethod -Uri "$FrontendBaseUrl/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $loginBody
$token = $login.data.accessToken
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'Login response did not contain data.accessToken'
}

$headers = @{ Authorization = "Bearer $token" }
$me = Invoke-RestMethod -Uri "$FrontendBaseUrl/api/v1/auth/me" -Method Get -Headers $headers
Assert-Equal $me.data.username $Username 'Current-user response mismatch'

$rooms = Invoke-RestMethod -Uri "$FrontendBaseUrl/api/v1/rooms" -Method Get -Headers $headers
if ($rooms.data.total -lt 1 -or $rooms.data.items.Count -lt 1) {
    throw 'Room query returned no demo rooms'
}

[pscustomobject]@{
    nginx = $nginx.status
    java = $readiness.status
    python = $agentHealth.status
    user = $me.data.username
    roomCount = $rooms.data.total
} | Format-List

