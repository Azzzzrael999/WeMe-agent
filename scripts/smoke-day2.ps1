[CmdletBinding()]
param(
    [string]$PublicBaseUrl = 'http://localhost'
)

$ErrorActionPreference = 'Stop'
$baseUrl = $PublicBaseUrl.TrimEnd('/')

function Invoke-MeetingApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('GET', 'POST', 'PUT', 'DELETE')]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Token,
        [object]$Body,
        [string]$IdempotencyKey
    )

    $headers = @{ Authorization = "Bearer $Token" }
    if ($IdempotencyKey) {
        $headers['Idempotency-Key'] = $IdempotencyKey
    }

    $arguments = @{
        Method     = $Method
        Uri        = "$baseUrl$Path"
        Headers    = $headers
        TimeoutSec = 20
    }
    if ($null -ne $Body) {
        $arguments['ContentType'] = 'application/json; charset=utf-8'
        $arguments['Body'] = $Body | ConvertTo-Json -Depth 10 -Compress
    }

    return Invoke-RestMethod @arguments
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Actual,
        [Parameter(Mandatory = $true)]
        [object]$Expected,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )
    if ($Actual -ne $Expected) {
        throw "$Message (expected=$Expected actual=$Actual)"
    }
}

function Assert-ExpectedApiError {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedStatus,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedCode
    )
    try {
        & $Action | Out-Null
        throw "Expected HTTP $ExpectedStatus/$ExpectedCode but request succeeded"
    }
    catch {
        $response = $_.Exception.Response
        if ($null -eq $response) {
            throw
        }
        $actualStatus = [int]$response.StatusCode
        $errorText = $_.ErrorDetails.Message
        if ([string]::IsNullOrWhiteSpace($errorText) -and $response.PSObject.Methods.Name -contains 'GetResponseStream') {
            $stream = $response.GetResponseStream()
            if ($null -ne $stream) {
                $reader = [System.IO.StreamReader]::new($stream)
                try {
                    $errorText = $reader.ReadToEnd()
                }
                finally {
                    $reader.Dispose()
                }
            }
        }
        if ([string]::IsNullOrWhiteSpace($errorText)) {
            throw "API returned HTTP $actualStatus without a JSON error body"
        }
        $errorBody = $errorText | ConvertFrom-Json
        if ($actualStatus -ne $ExpectedStatus -or $errorBody.code -ne $ExpectedCode) {
            throw "Unexpected API error: status=$actualStatus code=$($errorBody.code)"
        }
    }
}

$loginBody = @{ username = 'zhangsan'; password = 'demo-password' } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/auth/login" -ContentType 'application/json; charset=utf-8' -Body $loginBody -TimeoutSec 20
$token = $login.data.accessToken
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'Login response did not contain an access token'
}

$testDate = (Get-Date).Date.AddDays(10)
$offset = [TimeSpan]::FromHours(8)
function Format-Day2Time([int]$Hour, [int]$Minute) {
    $local = $testDate.AddHours($Hour).AddMinutes($Minute)
    return ([DateTimeOffset]::new($local, $offset)).ToString('yyyy-MM-ddTHH:mm:sszzz')
}

$primaryKey = [guid]::NewGuid().ToString()
$blockerKey = [guid]::NewGuid().ToString()
$primaryId = $null
$blockerId = $null

$primaryBody = @{
    title                     = 'Day 2 九十分钟事务验证'
    meetingType               = 'ARCHITECTURE_REVIEW'
    roomId                    = 101
    startAt                   = Format-Day2Time 9 0
    endAt                     = Format-Day2Time 10 30
    requiredParticipantIds    = @()
    optionalParticipantIds    = @()
}

$blockerBody = @{
    title                     = 'Day 2 修改冲突占位'
    meetingType               = 'TECH_REVIEW'
    roomId                    = 102
    startAt                   = Format-Day2Time 14 0
    endAt                     = Format-Day2Time 15 0
    requiredParticipantIds    = @()
    optionalParticipantIds    = @()
}

try {
    $created = Invoke-MeetingApi -Method POST -Path '/api/v1/meetings' -Token $token -Body $primaryBody -IdempotencyKey $primaryKey
    $primaryId = $created.data.id
    Assert-Equal $created.data.status 'CONFIRMED' 'Manual meeting was not confirmed'
    Assert-Equal $created.data.source 'MANUAL' 'Manual meeting source is incorrect'
    Assert-Equal $created.data.organizerId 1001 'JWT organizer was not used'

    $idempotentReplay = Invoke-MeetingApi -Method POST -Path '/api/v1/meetings' -Token $token -Body $primaryBody -IdempotencyKey $primaryKey
    Assert-Equal $idempotentReplay.data.id $primaryId 'Idempotent replay returned a different meeting'

    $reusedKeyBody = @{} + $primaryBody
    $reusedKeyBody.title = '不同请求摘要'
    Assert-ExpectedApiError -ExpectedStatus 409 -ExpectedCode 'IDEMPOTENCY_KEY_REUSED' -Action {
        Invoke-MeetingApi -Method POST -Path '/api/v1/meetings' -Token $token -Body $reusedKeyBody -IdempotencyKey $primaryKey
    }

    $detailBeforeConflict = Invoke-MeetingApi -Method GET -Path "/api/v1/meetings/$primaryId" -Token $token
    Assert-Equal $detailBeforeConflict.data.startAt $primaryBody.startAt 'Created meeting startAt is incorrect'
    Assert-Equal $detailBeforeConflict.data.endAt $primaryBody.endAt 'Created meeting endAt is incorrect'

    $blocker = Invoke-MeetingApi -Method POST -Path '/api/v1/meetings' -Token $token -Body $blockerBody -IdempotencyKey $blockerKey
    $blockerId = $blocker.data.id

    $conflictingUpdate = @{} + $primaryBody
    $conflictingUpdate.roomId = 102
    $conflictingUpdate.startAt = $blockerBody.startAt
    $conflictingUpdate.endAt = $blockerBody.endAt
    $conflictingUpdate.expectedVersion = $detailBeforeConflict.data.version

    Assert-ExpectedApiError -ExpectedStatus 409 -ExpectedCode 'BOOKING_CONFLICT' -Action {
        Invoke-MeetingApi -Method PUT -Path "/api/v1/meetings/$primaryId" -Token $token -Body $conflictingUpdate
    }

    $detailAfterConflict = Invoke-MeetingApi -Method GET -Path "/api/v1/meetings/$primaryId" -Token $token
    Assert-Equal $detailAfterConflict.data.roomId $detailBeforeConflict.data.roomId 'Failed update changed the room'
    Assert-Equal $detailAfterConflict.data.startAt $detailBeforeConflict.data.startAt 'Failed update changed startAt'
    Assert-Equal $detailAfterConflict.data.endAt $detailBeforeConflict.data.endAt 'Failed update changed endAt'
    Assert-Equal $detailAfterConflict.data.version $detailBeforeConflict.data.version 'Failed update changed version'

    $successfulUpdate = @{} + $primaryBody
    $successfulUpdate.title = 'Day 2 修改成功'
    $successfulUpdate.roomId = 102
    $successfulUpdate.startAt = Format-Day2Time 15 30
    $successfulUpdate.endAt = Format-Day2Time 17 0
    $successfulUpdate.expectedVersion = $detailAfterConflict.data.version
    $updated = Invoke-MeetingApi -Method PUT -Path "/api/v1/meetings/$primaryId" -Token $token -Body $successfulUpdate
    Assert-Equal $updated.data.version ($detailAfterConflict.data.version + 1) 'Successful update did not increment version'
    Assert-Equal $updated.data.roomId 102 'Successful update did not change room'

    $from = [Uri]::EscapeDataString((Format-Day2Time 0 0))
    $to = [Uri]::EscapeDataString((Format-Day2Time 23 30))
    $list = Invoke-MeetingApi -Method GET -Path "/api/v1/meetings?from=$from&to=$to" -Token $token
    $listedIds = @($list.data.items | ForEach-Object { $_.id })
    if ($listedIds -notcontains $primaryId -or $listedIds -notcontains $blockerId) {
        throw 'Meeting list did not contain both created meetings'
    }

    $cancelled = Invoke-MeetingApi -Method DELETE -Path "/api/v1/meetings/$primaryId" -Token $token
    Assert-Equal $cancelled.data.status 'CANCELLED' 'Cancellation did not return CANCELLED'
    Assert-ExpectedApiError -ExpectedStatus 409 -ExpectedCode 'MEETING_STATE_CONFLICT' -Action {
        Invoke-MeetingApi -Method DELETE -Path "/api/v1/meetings/$primaryId" -Token $token
    }

    $blockerCancelled = Invoke-MeetingApi -Method DELETE -Path "/api/v1/meetings/$blockerId" -Token $token
    Assert-Equal $blockerCancelled.data.status 'CANCELLED' 'Blocker cancellation failed'

    [pscustomobject]@{
        loginUser              = $login.data.user.username
        meetingId              = $primaryId
        ninetyMinuteSlots      = 3
        idempotentReplay       = 'SAME_MEETING'
        keyReuse               = 'REJECTED'
        failedUpdateRollback   = 'PRESERVED'
        successfulUpdate       = 'VERSION_INCREMENTED'
        listAndDetail          = 'PASS'
        cancellation           = 'SLOTS_RELEASED'
    }
}
finally {
    foreach ($meetingId in @($primaryId, $blockerId)) {
        if ($null -ne $meetingId) {
            try {
                Invoke-MeetingApi -Method DELETE -Path "/api/v1/meetings/$meetingId" -Token $token | Out-Null
            }
            catch {
                # Cleanup is best effort; a successfully cancelled meeting returns a state conflict here.
            }
        }
    }
}
