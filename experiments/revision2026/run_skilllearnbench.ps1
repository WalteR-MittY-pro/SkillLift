[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('deepseek', 'glm')]
    [string]$Model,
    [ValidateSet('generalization', 'pilot')]
    [string]$Mode = 'generalization',
    [ValidateSet('all', 'train', 'test')]
    [string]$Stage = 'all',
    [string]$RunRoot = 'runs/skilllearnbench-thinking-32768-v1',
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$python = Join-Path $repo '.venv-revision2026-windows/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $repo '.venv-revision2026/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Missing Windows Python environment. Run setup.sh skilllearnbench in Windows Git Bash (not WSL).'
}
$workers = 2
$protocol = Join-Path $PSScriptRoot 'protocol.skilllearnbench-thinking-32768.json'
Push-Location $repo
$originalDockerContext = $env:DOCKER_CONTEXT
try {
    if (-not $PlanOnly) {
        if (-not $env:DOCKER_HOST -and -not $env:DOCKER_CONTEXT) {
            $env:DOCKER_CONTEXT = 'desktop-linux'
        }
        & docker info --format 'Experiment Docker daemon: {{.Name}} ({{.ID}})'
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is unreachable.' }
    }
    $runDir = Join-Path (Join-Path $RunRoot $Model) $Mode
    $cliArgs = @('-X', 'utf8', '-m', 'experiments.revision2026', $Mode,
        '--models', $Model, '--workers', "$workers", '--protocol', $protocol,
        '--run-dir', $runDir, '--stage', $Stage, '--require-cached-images')
    if ($PlanOnly) { $cliArgs += '--plan-only' }
    Write-Host "SkillLearnBench: model=$Model workers=$workers mode=$Mode output_tokens=32768 thinking=enabled stream=false"
    Write-Host "Run directory: $runDir (reuse this directory to resume)"
    & $python @cliArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) { exit $code }
    # The underlying CLI can return zero with incomplete jobs; expose that to callers.
    if (-not $PlanOnly) {
        $summary = Get-Content -LiteralPath (Join-Path $runDir 'execution_summary.json') -Raw | ConvertFrom-Json
        if ($summary.status -ne 'complete') {
            Write-Warning "Jobs remain incomplete. Inspect $runDir and rerun the same command."
            exit 1
        }
    }
} finally {
    $env:DOCKER_CONTEXT = $originalDockerContext
    Pop-Location
}
