param(
    [string]$Candidate = ""
)

$ErrorActionPreference = "SilentlyContinue"
$candidates = New-Object System.Collections.Generic.List[string]

function Add-Candidate([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim().Trim('"'))
    if ((Test-Path -LiteralPath $expanded -PathType Leaf) -and
        -not $candidates.Contains($expanded)) {
        $candidates.Add($expanded)
    }
}

Add-Candidate $Candidate

$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Oracle Tasks Chile.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Oracle Tasks Chile\Oracle Tasks Chile.lnk")
)
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $shortcutPaths) {
    if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
        Add-Candidate ($shell.CreateShortcut($shortcutPath).TargetPath)
    }
}

foreach ($name in @("pythonw.exe", "python.exe")) {
    $command = Get-Command $name -CommandType Application | Select-Object -First 1
    if ($command) {
        Add-Candidate $command.Source
    }
}

$launcher = Get-Command "py.exe" -CommandType Application | Select-Object -First 1
if ($launcher) {
    $launcherPython = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Add-Candidate ($launcherPython | Select-Object -First 1)
    }
}

$patterns = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe"),
    (Join-Path $env:ProgramFiles "Python*\python.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Python*\python.exe"),
    "C:\Python*\python.exe"
)
foreach ($pattern in $patterns) {
    Get-ChildItem -Path $pattern -File | Sort-Object FullName -Descending | ForEach-Object {
        Add-Candidate $_.FullName
    }
}

foreach ($path in $candidates) {
    $directory = Split-Path -Parent $path
    $name = Split-Path -Leaf $path
    if ($name -ieq "pythonw.exe") {
        $python = Join-Path $directory "python.exe"
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            continue
        }
    } elseif ($name -ieq "python.exe") {
        $python = $path
    } else {
        continue
    }

    if ($python -like "*\WindowsApps\python*.exe") {
        continue
    }
    $resolved = & $python -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
        continue
    }
    $python = [System.IO.Path]::GetFullPath(($resolved | Select-Object -First 1))
    $pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        $pythonw = $python
    }
    Write-Output "PY=$python"
    Write-Output "PYTHONW=$pythonw"
    exit 0
}

exit 1
