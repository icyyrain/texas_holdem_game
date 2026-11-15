param(
    [string]$EnvName = "holdem-game",
    [string]$CondaExe = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$ExePath = Join-Path $ProjectRoot "dist\Holdem.exe"
$CardAssetsPath = Join-Path $ProjectRoot "assets\cards"
$SmallCardAssetsPath = Join-Path $ProjectRoot "assets\cards_small"
$EntryScript = "src\holdem\client\app.py"

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $EntryScript))) {
    throw "Client entry point $EntryScript does not exist yet."
}

function Resolve-CondaExe {
    param(
        [string]$ExplicitCondaExe
    )

    if ($ExplicitCondaExe) {
        return $ExplicitCondaExe
    }

    $CondaCommand = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($CondaCommand) {
        return $CondaCommand.Source
    }

    $CandidatePaths = @(
        (Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\miniconda3\Scripts\conda.exe",
        "C:\anaconda3\Scripts\conda.exe"
    )

    foreach ($CandidatePath in $CandidatePaths) {
        if (Test-Path -LiteralPath $CandidatePath) {
            return $CandidatePath
        }
    }

    return $null
}

$CondaExe = Resolve-CondaExe -ExplicitCondaExe $CondaExe
if (-not (Test-Path -LiteralPath $CondaExe)) {
    throw "Conda was not found. Pass -CondaExe with the path to conda.exe, for example: -CondaExe `"$env:USERPROFILE\miniconda3\Scripts\conda.exe`""
}

$RunningExe = Get-Process -Name "Holdem" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $ExePath }
if ($RunningExe) {
    throw "Close dist\Holdem.exe before building. It is currently running and Windows will not let PyInstaller replace it."
}

& $CondaExe run -n $EnvName python scripts\prepare_card_images.py
if ($LASTEXITCODE -ne 0) {
    throw "Card image preparation failed with exit code $LASTEXITCODE."
}
& $CondaExe run -n $EnvName python scripts\prepare_card_images.py --output-dir assets\cards_small --width 60 --height 84
if ($LASTEXITCODE -ne 0) {
    throw "Small card image preparation failed with exit code $LASTEXITCODE."
}

& $CondaExe run -n $EnvName python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name Holdem `
    --specpath build `
    --add-data "$CardAssetsPath;assets\cards" `
    --add-data "$SmallCardAssetsPath;assets\cards_small" `
    --paths src `
    $EntryScript

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

Write-Host "Built $ExePath"
