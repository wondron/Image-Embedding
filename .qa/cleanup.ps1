param(
    [switch]$Apply
)

# Default: preview only. Use -Apply to remove obsolete QA artifacts.
$ErrorActionPreference = 'Stop'
$qaRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$workspaceRoot = [IO.Path]::GetDirectoryName($qaRoot)
$expectedQa = [IO.Path]::GetFullPath((Join-Path $workspaceRoot '.qa'))
if (-not [string]::Equals($qaRoot, $expectedQa, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Run this script from the workspace .qa folder.'
}
if (-not (Test-Path -LiteralPath (Join-Path $workspaceRoot '.git'))) {
    throw 'The expected repository root was not found.'
}
$qaPrefix = $qaRoot + [IO.Path]::DirectorySeparatorChar
$links = @(Get-ChildItem -LiteralPath $qaRoot -Recurse -Force |
    Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 })
if ($links.Count -gt 0) {
    throw 'Refusing cleanup: .qa contains a symbolic link or junction.'
}

# Retain final reports and the current bug investigation evidence in place.
# The repro scripts depend on their current paths relative to frontend/backend.
$keepRelative = @(
    'cleanup.ps1'
    'bugcheck-20261113-backend.log'
    'bugcheck-20261113-category-picker-probe.cjs'
    'bugcheck-20261113-frontend-build.log'
    'bugcheck-20261113-frontend-e2e.json'
    'bugcheck-20261113-frontend-unit.log'
    'bugcheck-20261113-ime.json'
    'bugcheck-20261113-ime.mjs'
    'bugcheck-20261113-ime.png'
    'bugcheck-20261113-reembed-delete-race.json'
    'bugcheck-20261113-reembed-delete-race.py'
    'bugcheck-20261113-report.md'
    'bugcheck-20261113-scripts.log'
    'ingest-tasks-acceptance-report.md'
    'oss-key-prefix-acceptance-report.md'
    'scene-completion-acceptance-report.md'
    'scene-current-acceptance-report.md'
    'scene-detail-layout-acceptance-report.md'
    'import-upload-layout/import-upload-layout-acceptance-report.md'
    'smoke-app-error-show-text-report.json'
    'smoke-backend-r0-r5-fe61-report.json'
    'smoke-ddd-d3-d6-report.json'
    'smoke-fe-pg-v01-m6-report.json'
    'smoke-pg-v01-m7-report.json'
)
$keepPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($relative in $keepRelative) {
    $absolute = [IO.Path]::GetFullPath((Join-Path $qaRoot $relative))
    if (-not $absolute.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A retained path escapes .qa.'
    }
    if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) {
        throw "A retained file is missing: $relative"
    }
    [void]$keepPaths.Add($absolute)
}

# Only target files from this QA snapshot. Later files require a new review.
$snapshotCutoff = ([datetimeoffset]'2026-11-14T00:04:00Z').UtcDateTime
$allFiles = @(Get-ChildItem -LiteralPath $qaRoot -Recurse -File -Force)
$deleteFiles = @($allFiles | Where-Object {
    -not $keepPaths.Contains($_.FullName) -and $_.LastWriteTimeUtc -le $snapshotCutoff
})
foreach ($file in $deleteFiles) {
    $absolute = [IO.Path]::GetFullPath($file.FullName)
    if (-not $absolute.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A deletion target escapes .qa.'
    }
}

$deletedBytes = ($deleteFiles | Measure-Object Length -Sum).Sum
[pscustomobject]@{
    Mode = $(if ($Apply) { 'APPLY' } else { 'PREVIEW ONLY' })
    Root = $qaRoot
    FilesToDelete = $deleteFiles.Count
    ReclaimMB = [math]::Round($deletedBytes / 1MB, 2)
    FilesToKeep = $allFiles.Count - $deleteFiles.Count
} | Format-List

if (-not $Apply) {
    $deleteFiles | ForEach-Object { $_.FullName.Substring($qaPrefix.Length) }
    Write-Output 'No files were deleted. Add -Apply to perform this cleanup.'
    return
}

foreach ($file in $deleteFiles) {
    Remove-Item -LiteralPath $file.FullName -Force
}

# Remove empty folders only, deepest first. Never recursively delete a folder.
$directories = @(Get-ChildItem -LiteralPath $qaRoot -Recurse -Directory -Force |
    Sort-Object { $_.FullName.Length } -Descending)
foreach ($directory in $directories) {
    $absolute = [IO.Path]::GetFullPath($directory.FullName)
    if (-not $absolute.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'An empty-folder deletion target escapes .qa.'
    }
    if (@(Get-ChildItem -LiteralPath $absolute -Force).Count -eq 0) {
        Remove-Item -LiteralPath $absolute -Force
    }
}
Write-Output "Deleted $($deleteFiles.Count) obsolete QA files. Final reports and bug evidence were retained."
