param(
    [Parameter(Mandatory = $true)]
    [string]$Window,

    [ValidateRange(1, 3600)]
    [int]$WatchSeconds = 600
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cli = Join-Path $repoRoot '.venv\Scripts\opencrbot.exe'
$checkpoint = Join-Path $repoRoot 'runs\clashai-replay-data\training-camp-generalist-20261121\colab-run-20261120T183911Z\gen_clashai_training_camp_generalist_fv3_colab_20261120T183911Z_s0.pt'
$weights = Join-Path $repoRoot '..\clash-ai\models\yolo\clashai_yolo11s.pt'
$templates = Join-Path $repoRoot '..\clash-ai\assets\card_templates.npz'
$clockTemplates = Join-Path $repoRoot 'runs\training-match-cp1\gpgpc-clock-templates.json'
$rubbleTemplate = Join-Path $repoRoot 'runs\training-match-cp1\princess-tower-rubble.png'
$nextTemplates = Join-Path $repoRoot 'runs\training-match-cp3-reader-holdout\analysis\next-card-templates-cp3.npz'
$calibration = Join-Path $repoRoot 'configs\gpgpc-windows-training.json'
$deck = Join-Path $repoRoot 'configs\training-camp-deck.yaml'
$clashAiRoot = Join-Path $repoRoot '..\ClashAI'
$runOutputDir = Join-Path $repoRoot ('runs\cp7-training-camp-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))

foreach ($requiredPath in @($cli, $checkpoint, $weights, $templates, $clockTemplates, $rubbleTemplate, $nextTemplates, $calibration, $deck, $clashAiRoot)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required OpenCRBOT/ClashAI asset not found: $requiredPath"
    }
}

Write-Host 'This run pauses before every suggested play.'
Write-Host 'Inspect the preview, then type PLAY TRAINING CAMP in this console to send that one action; press Enter to skip.'
Write-Host 'After the result screen appears, inspect the saved image and label WIN, LOSS, DRAW, or UNKNOWN.'

$arguments = @(
    'analyze',
    '--window', $Window,
    '--watch-seconds', $WatchSeconds.ToString(),
    '--weights', $weights,
    '--templates', $templates,
    '--clock-templates', $clockTemplates,
    '--rubble-template', $rubbleTemplate,
    '--regulation-clock',
    '--next-templates', $nextTemplates,
    '--calibration', $calibration,
    '--clashai-root', $clashAiRoot,
    '--clashai-checkpoint', $checkpoint,
    '--clashai-deck', $deck,
    '--clashai-costs', '3', '3', '4', '5', '3', '4', '3', '4',
    '--track',
    '--confirm-actions',
    '--out', $runOutputDir
)

Push-Location $repoRoot
try {
    & $cli @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "OpenCRBOT exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
