# Legacy direct-deployment entry point intentionally disabled.
#
# The former script unpacked files over the live checkout, copied an unchecked
# XML directly into nginx storage, and restarted an unrelated profile service.
# That bypassed the candidate/count/XML/lock/backup/rollback safety boundary.
#
# Use Avito Content Studio for an interactive publication, or prepare an
# allow-listed archive and invoke:
#   python -m avito_bridge.profile_publish --archive <archive> --config profiles/wreaths.yaml
# on the server. Both routes require an explicit publication action.

$ErrorActionPreference = "Stop"
Write-Error (
    "Прямой deploy_wreaths.ps1 отключён из соображений безопасности. " +
    "Используйте Avito Content Studio или avito_bridge.profile_publish; " +
    "они проверяют кандидат, счётчики, XML, lock, backup и rollback."
)
exit 2
