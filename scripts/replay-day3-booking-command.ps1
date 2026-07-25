[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string]$RequestNo
)

$ErrorActionPreference = 'Stop'

function Invoke-BusinessSql([string]$Sql) {
    $output = $Sql | docker compose -f compose.yaml -f compose.dev.yaml exec -T mysql `
        sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -D meeting_business'
    if ($LASTEXITCODE -ne 0) {
        throw 'MySQL verification command failed.'
    }
    return @($output)
}

$row = @(Invoke-BusinessSql @"
SELECT event_id, payload_json
FROM message_outbox
WHERE aggregate_id = '$RequestNo' AND event_type = 'BOOKING_COMMAND'
ORDER BY id
LIMIT 1;
"@)

if ($row.Count -ne 1) {
    throw "Expected exactly one BOOKING_COMMAND Outbox row for $RequestNo"
}

$parts = $row[0] -split "`t", 2
if ($parts.Count -ne 2) {
    throw 'Unexpected Outbox query shape.'
}
$eventId = $parts[0]
$payload = $parts[1]
$envelope = $payload | ConvertFrom-Json
if ($envelope.eventId -ne $eventId -or $envelope.aggregateId -ne $RequestNo) {
    throw 'Outbox envelope identity does not match its columns.'
}
$wirePayload = ($envelope | ConvertTo-Json -Depth 30 -Compress) -replace ' ', '\u0020'
if ($wirePayload -match '\s') {
    throw 'Replay payload still contains shell-breaking whitespace.'
}

$before = @(Invoke-BusinessSql @"
SELECT
  (SELECT COUNT(*) FROM meeting WHERE request_no = '$RequestNo'),
  (SELECT COUNT(*) FROM event_consume_record WHERE consumer_group = 'meeting-booking-finalizer' AND event_id = '$eventId');
"@)
$beforeParts = $before[0] -split "`t"
if ([int]$beforeParts[0] -ne 1 -or [int]$beforeParts[1] -ne 1) {
    throw "Precondition failed: meetingCount=$($beforeParts[0]) consumeCount=$($beforeParts[1])"
}

$wirePayload | docker compose -f compose.yaml -f compose.dev.yaml exec -T `
    rocketmq-broker sh -c `
    'DAY3_REPLAY_BODY=$(cat) && sh mqadmin sendMessage -n rocketmq-namesrv:9876 -t meeting-booking -c BOOKING_COMMAND -k "$1" -p "$DAY3_REPLAY_BODY"' `
    sh $eventId
if ($LASTEXITCODE -ne 0) {
    throw 'RocketMQ duplicate command send failed.'
}

$null = Start-Sleep -Seconds 2
$afterParts = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $after = @(Invoke-BusinessSql @"
SELECT
  (SELECT COUNT(*) FROM meeting WHERE request_no = '$RequestNo'),
  (SELECT COUNT(*) FROM event_consume_record WHERE consumer_group = 'meeting-booking-finalizer' AND event_id = '$eventId');
"@)
    $afterParts = $after[0] -split "`t"
    if ([int]$afterParts[0] -eq 1 -and [int]$afterParts[1] -eq 1) {
        break
    }
}

if ([int]$afterParts[0] -ne 1 -or [int]$afterParts[1] -ne 1) {
    throw "Duplicate delivery changed final state: meetingCount=$($afterParts[0]) consumeCount=$($afterParts[1])"
}

$consumerProgress = docker compose -f compose.yaml -f compose.dev.yaml exec -T `
    rocketmq-broker sh mqadmin consumerProgress -n rocketmq-namesrv:9876 -g meeting-booking-finalizer
if ($LASTEXITCODE -ne 0 -or (($consumerProgress -join "`n") -notmatch 'Diff Total:\s+0')) {
    throw 'Booking consumer still has message backlog after duplicate replay.'
}

[pscustomobject]@{
    requestNo              = $RequestNo
    replayedEventId        = $eventId
    meetingCountAfter      = [int]$afterParts[0]
    consumeRecordCountAfter = [int]$afterParts[1]
    duplicateDelivery      = 'IGNORED'
}
