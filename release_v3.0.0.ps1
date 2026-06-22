# Release script for wootify_instance_manager v3.0.0
# Run from project root: .\release_v3.0.0.ps1
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
feat(release): v3.0.0 — bale_pv connector, sticker support, docs cleanup

BREAKING CHANGES
- bale_pv_enterprise connector is now feature-complete with full media
  support (photos, documents, audio, video, stickers).
- Inbound WEBP stickers are converted to JPEG via Pillow before upload
  to Chatwoot so they render inline without browser-side WEBP support.
- Sticker detection now handles both dedicated StickerMessage (proto
  field 12) and legacy DocumentMessage stickers (sticker*.png with
  image/jpeg MIME) through filename-prefix fallback detection.

IMPROVEMENTS
- bale_grpc_client: parse_ws_update handles StickerMessage (field 12),
  forward headers (field 7), reply-to refs (field 13), channel messages
  (field 162), and all known wrapper event types.
- BalePvAdapter.resolve_attachments: WEBP detection via magic bytes;
  WEBP→JPEG conversion with alpha-channel compositing; filename
  extension normalisation to match actual content type.
- EnterpriseBaleService._extract_attachments: WEBP→JPEG conversion
  added to mirror the bale_pv adapter pipeline.
- ws_client.py: BALE_WS_DEBUG_LOG now defaults to empty string
  (disabled) — was previously "bale_ws_debug.log", which generated
  hundreds of MB of log data unintentionally.

HOUSEKEEPING
- Removed AI-agent debug/diagnostic scripts and data dumps:
  _tmp_check_self.py, debug_nasim_upload_response.py, terminal_logs.txt,
  scripts/diagnose_bale_phone.py, scripts/diagnose_sticker.py,
  tests/test_ws_debug_decoder.py, bale_contacts_dump.json,
  bale_dialogs_dump.json, dump_bale_contacts.py, dump_bale_dialogs.py.
- Added data/bale_pv_sessions/ and all debug scripts to .gitignore.
- Replaced AI-generated boilerplate module docstrings with proper
  industry-standard docstrings across all modified modules.
- Restored truncated file endings in bale_pv_connector.py,
  enterprise_bale_service.py, bale_polling_service.py, ws_client.py,
  and update_parser.py.
- Removed duplicate lazy-importer definitions in bale_pv_connector.py.
"@

git commit -m $msg
if ($LASTEXITCODE -ne 0) { Write-Host "git commit failed" -ForegroundColor Red; exit 1 }
Write-Host "Committed OK" -ForegroundColor Green

Write-Host "`n=== Step 4: Create annotated tag v3.0.0 ===" -ForegroundColor Cyan
git tag -a v3.0.0 -m "Release v3.0.0 — bale_pv connector with full sticker/media support"
if ($LASTEXITCODE -ne 0) { Write-Host "git tag failed" -ForegroundColor Red; exit 1 }
Write-Host "Tagged OK" -ForegroundColor Green

Write-Host "`n=== Step 5: Push branch to GitLab ===" -ForegroundColor Cyan
git push gitlab HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "Push to gitlab failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 6: Push branch to GitHub (origin) ===" -ForegroundColor Cyan
git push origin HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "Push to origin failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 7: Push tag to GitLab ===" -ForegroundColor Cyan
git push gitlab v3.0.0
if ($LASTEXITCODE -ne 0) { Write-Host "Tag push to gitlab failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 8: Push tag to GitHub (origin) ===" -ForegroundColor Cyan
git push origin v3.0.0
if ($LASTEXITCODE -ne 0) { Write-Host "Tag push to origin failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Done! v3.0.0 released to gitlab and origin ===" -ForegroundColor Green
git log --oneline -3
git tag --sort=-v:refname | Select-Object -First 5
