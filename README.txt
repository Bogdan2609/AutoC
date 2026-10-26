Autocodex v0.33.0 - validation encoding fix for Windows PowerShell 5.1
Files: scripts/Validate-v0330.ps1 only.
Changes: avoid non-ASCII RUB literal passing through PowerShell -> Docker CLI by calling require_rub('RUB'), which is a supported alias; save ps1 as UTF-8 with BOM.
This script contains only configuration, migration, syntax, import/contract and health checks. No supplier orders or payments are sent.
Usage from project root: Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\autocodex_v0330_validation_encoding_fix.zip" -DestinationPath "." -Force
Then: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Validate-v0330.ps1
ROSKKO read-only probe with actual account and destination UUID remains separate.
