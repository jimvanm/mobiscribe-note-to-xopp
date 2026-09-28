param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Paths
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$converter = Join-Path $scriptDir "mobiscribe_to_xopp.py"
$python = $env:NOTE2XOPP_PYTHON
if ([string]::IsNullOrWhiteSpace($python)) { $python = "python" }
$force = $env:NOTE2XOPP_FORCE -eq "1"

if (-not (Test-Path -LiteralPath $converter)) {
    Write-Host "Can't find mobiscribe_to_xopp.py -- it must sit in the same folder as note2xopp.bat." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    Write-Host "Can't run '$python'. Install Python (python.org), or edit NOTE2XOPP_PYTHON in note2xopp.bat to the full path of python.exe." -ForegroundColor Red
    exit 1
}

if (-not $Paths -or $Paths.Count -eq 0) {
    Write-Host "Drag one or more .note files, or a folder containing them, onto note2xopp.bat."
    exit 0
}

function Get-NoteFiles {
    param([string]$p)
    if (Test-Path -LiteralPath $p -PathType Container) {
        Get-ChildItem -LiteralPath $p -Filter *.note -Recurse -File
    } elseif (Test-Path -LiteralPath $p -PathType Leaf) {
        if ($p -like "*.note") {
            Get-Item -LiteralPath $p
        } else {
            Write-Host "Skipping $p (not a .note file)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Not found: $p" -ForegroundColor Yellow
    }
}

$notes = @()
foreach ($p in $Paths) {
    $notes += Get-NoteFiles -p $p
}
$notes = $notes | Sort-Object FullName -Unique

if ($notes.Count -eq 0) {
    Write-Host "No .note files found."
    exit 0
}

Write-Host "Found $($notes.Count) notebook(s)."
Write-Host ""

$ok = 0
$skipped = 0
$failed = 0

foreach ($note in $notes) {
    $outPath = [System.IO.Path]::ChangeExtension($note.FullName, ".xopp")

    if ((Test-Path -LiteralPath $outPath) -and -not $force) {
        Write-Host "Skip (already converted): $($note.Name)" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    Write-Host "Converting: $($note.Name)"
    & $python $converter $note.FullName -o $outPath
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  -> $(Split-Path -Leaf $outPath)" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  FAILED: $($note.Name)" -ForegroundColor Red
        $failed++
    }
    Write-Host ""
}

Write-Host "Done. $ok converted, $skipped skipped, $failed failed."
