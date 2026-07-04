# Release script for wootify_instance_manager v4.0.0
# Run from project root: .\release_v4.0.0.ps1
# Requires git credentials for github (origin) and gitlab remote.

Set-Location $PSScriptRoot

Write-Host "=== Step 1: Remove stale git lock ===" -ForegroundColor Cyan
if (Test-Path ".git\index.lock") {
    Remove-Item ".git\index.lock" -Force
    Write-Host "Removed .git\index.lock" -ForegroundColor Green
} else {
    Write-Host "No lock file found" -ForegroundColor Gray
}

Write-Host "`n=== Step 2: Stage all changes ===" -ForegroundColor Cyan
git add -A
if ($LASTEXITCODE -ne 0) { Write-Host "git add failed" -ForegroundColor Red; exit 1 }
Write-Host "Staged OK" -ForegroundColor Green

Write-Host "`n=== Step 3: Commit ===" -ForegroundColor Cyan
$msg = @"
feat(release): v4.0.0 — local bale_pv_connector, stable sender IDs, name resolution

BREAKING CHANGES
- Replaced the external bale_grpc_client dependency with a local,
  async-first bale_pv_connector package maintained inside the repo.
- All bale_pv_enterprise imports now use bale_pv_connector; legacy
  bale_grpc_client folder has been removed.

NEW FEATURES
- bale_pv_connector package (v0.2.4) ships with its own tests, README,
  and pyproject.toml built with hatchling.
- Added bale.groups.v1.Groups/LoadGroups support (LoadGroupsRequest,
  GroupOutPeer, BaleMessagingClient.load_groups()) for authoritative
  group/channel title resolution.
- Added BALE_WS_DEBUG_LOG raw-frame capture hook in ws_client.py for
  production debugging of WebSocket frames.

BUG FIXES
- Fixed: inbound Bale PV messages were silently dropped after the
  migration because parse_ws_update raised UnboundLocalError when
  handling peer_info (UpdateMessage field 14) before the result dict
  was initialized.
- Fixed: the same user appeared with a different sender ID on every
  message. Root cause: UpdateMessage field 9 was always interpreted as
  {uid, access_hash} and used to override the canonical sender_uid.
  In practice one of the two integers is the access_hash, so every
  message got a new "user id". Fix: use the stable sender_uid from
  UpdateMessage field 2 and derive access_hash from the field-9 value
  that does not match it.
- Fixed: private-chat contacts showed "User {id}" because the sender
  access_hash was missing. Fix: extract candidate access_hash from
  field-14 peer_info for 1-on-1 messages and use the peer_info title
  as a display-name fallback.
- Fixed: group/channel sender names stayed as raw IDs when LoadUsers
  returned empty. Added fallbacks to LoadDialogs and LoadHistory user
  lists, and continued the dialog cache refresh even when GetContacts
  fails.

IMPROVEMENTS
- UserOutPeer / GroupOutPeer IDs are now encoded as int64 to match
  Bale's large user/group identifiers.
- Added INFO-level logging for unresolved senders so operators can see
  whether the remaining gap is missing access_hash, empty peer_info,
  or an empty user cache.
- Cleaner connector reconnection logic and runtime state management.

HOUSEKEEPING
- Removed leftover AI-agent debug scripts: _tmp_*.py, start_capture.bat,
  and backend.log.* files.
- Renamed release script to release_v4.0.0.ps1.
"@

git commit -m $msg
if ($LASTEXITCODE -ne 0) { Write-Host "git commit failed" -ForegroundColor Red; exit 1 }
Write-Host "Committed OK" -ForegroundColor Green

Write-Host "`n=== Step 4: Create annotated tag 4.0.0 ===" -ForegroundColor Cyan
$tagMsg = @"
Release v4.0.0 — local bale_pv_connector, stable sender IDs, name resolution

Highlights since v3.0.0:
- Replaced bale_grpc_client with local bale_pv_connector package.
- Fixed inbound message drops caused by UnboundLocalError in peer_info parsing.
- Fixed per-message random sender IDs caused by misinterpreting field 9.
- Added LoadGroups support and robust sender-name resolution fallbacks.
- Added production debug hooks and logging for unresolved senders.
"@
git tag -a 4.0.0 -m $tagMsg
if ($LASTEXITCODE -ne 0) { Write-Host "git tag failed" -ForegroundColor Red; exit 1 }
Write-Host "Tagged OK" -ForegroundColor Green

Write-Host "`n=== Step 5: Push branch to GitLab ===" -ForegroundColor Cyan
git push gitlab HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "Push to gitlab failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 6: Push branch to GitHub (origin) ===" -ForegroundColor Cyan
git push origin HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "Push to origin failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 7: Push tag to GitLab ===" -ForegroundColor Cyan
git push gitlab 4.0.0
if ($LASTEXITCODE -ne 0) { Write-Host "Tag push to gitlab failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 8: Push tag to GitHub (origin) ===" -ForegroundColor Cyan
git push origin 4.0.0
if ($LASTEXITCODE -ne 0) { Write-Host "Tag push to origin failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Done! v4.0.0 released to gitlab and origin ===" -ForegroundColor Green
git log --oneline -3
git tag --sort=-v:refname | Select-Object -First 5
