$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainFile = 'DoAn.tex'

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $root
$watcher.Filter = '*.tex'
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = [System.IO.NotifyFilters]::LastWrite -bor [System.IO.NotifyFilters]::FileName -bor [System.IO.NotifyFilters]::CreationTime
$watcher.EnableRaisingEvents = $true

function Invoke-Build {
    Push-Location $root
    try {
        # Clean stale listing/cache artifacts that can cause wrong code content reuse.
        $cleanupTargets = @(
            (Join-Path $root 'DoAn.listing'),
            (Join-Path $root 'Chuong\DoAn.listing'),
            (Join-Path $root 'DoAn-lst-*.tex'),
            (Join-Path $root 'DoAn-term-*.tex'),
            (Join-Path $root 'temp-*.tex')
        )
        foreach ($target in $cleanupTargets) {
            Remove-Item -Path $target -Force -ErrorAction SilentlyContinue
        }

        for ($pass = 1; $pass -le 2; $pass++) {
            $args = @(
                '-interaction=nonstopmode',
                '-halt-on-error',
                '--enable-installer',
                '--enable-write18',
                $mainFile
            )

            $process = Start-Process -FilePath 'xelatex' -ArgumentList $args -Wait -NoNewWindow -PassThru
            if ($process.ExitCode -ne 0) {
                return $false
            }
        }

        $pdfPath = Join-Path $root 'DoAn.pdf'
        $parentPdfPath = Join-Path (Split-Path -Parent $root) 'DoAn.pdf'
        if (Test-Path $pdfPath) {
            Copy-Item -Path $pdfPath -Destination $parentPdfPath -Force
        }

        return $true
    }
    finally {
        Pop-Location
    }
}

Write-Host "Dang theo doi thay doi cua *.tex trong $root... (Nhan Ctrl+C de thoat)" -ForegroundColor Cyan

while ($true) {
    $result = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All)
    if ($result.TimedOut) {
        continue
    }

    Start-Sleep -Milliseconds 200
    Write-Host "`n[$([DateTime]::Now.ToString('HH:mm:ss'))] Phat hien thay doi. Dang build PDF..." -ForegroundColor Yellow

    if (Invoke-Build) {
        Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] Build thanh cong." -ForegroundColor Green
    }
    else {
        Write-Host "[$([DateTime]::Now.ToString('HH:mm:ss'))] Build that bai. Kiem tra log." -ForegroundColor Red
    }

    Start-Sleep -Seconds 1
}
