# Dropwheel - interactive installer for Windows 10 and 11 (preview).
#
#   irm https://raw.githubusercontent.com/haxurus/dropwheel/main/install.ps1 | iex
#   powershell -ExecutionPolicy Bypass -File install.ps1      (from a clone)
#
# Asks before every change. Installs Python and Git with winget when they are missing, gets the
# code (or uses this clone), installs Dropwheel with pip for the current user, the optional
# modules and programs you pick, then runs "dropwheel setup windows" and starts it.
# Nothing needs administrator rights. The file is plain ASCII so that Windows PowerShell 5.1
# reads it correctly whatever the system code page.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$Repo = 'https://github.com/haxurus/dropwheel'
$IT = (Get-UICulture).TwoLetterISOLanguageName -eq 'it'
function T([string]$it, [string]$en) { if ($IT) { $it } else { $en } }

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# ------------------------------------------------------------------------------------ output

$script:StepNo = 0
function Step([string]$text) {
    $script:StepNo++
    Write-Host ''
    Write-Host -NoNewline ("== {0} " -f $script:StepNo) -ForegroundColor Blue
    Write-Host $text -ForegroundColor White
}
function Info([string]$text) { Write-Host "    $text" }
function Ok([string]$text) { Write-Host -NoNewline '  + ' -ForegroundColor Green; Write-Host $text }
function Warn([string]$text) { Write-Host -NoNewline '  ! ' -ForegroundColor Yellow; Write-Host $text }
function Cmd([string]$text) { Write-Host "      > $text" -ForegroundColor DarkGray }
function Fail([string]$text) {
    Write-Host "x $text" -ForegroundColor Red
    throw $text
}

function Banner {
    # '#' and the letters stand for box-drawing characters, so that this file stays ASCII
    $art = @(
        '######7 ######7  ######7 ######7 ##7    ##7##7  ##7#######7#######7##7',
        '##r==##7##r==##7##r===##7##r==##7##|    ##|##|  ##|##r====j##r====j##|',
        '##|  ##|######rj##|   ##|######rj##| #7 ##|#######|#####7  #####7  ##|',
        '##|  ##|##r==##7##|   ##|##r===j ##|###7##|##r==##|##r==j  ##r==j  ##|',
        '######rj##|  ##|L######rj##|     L###r###rj##|  ##|#######7#######7#######7',
        'L=====j L=j  L=j L=====j L=j      L==jL==j L=j  L=jL======jL======jL======j'
    )
    $map = @{ '#' = 0x2588; '7' = 0x2557; 'r' = 0x2554; '=' = 0x2550; '|' = 0x2551; 'j' = 0x255D; 'L' = 0x255A }
    $colours = @('Cyan', 'Cyan', 'DarkCyan', 'Blue', 'DarkBlue', 'Magenta')
    $wide = $true
    try { $wide = $Host.UI.RawUI.WindowSize.Width -ge 78 } catch { }
    Write-Host ''
    if ($wide) {
        for ($i = 0; $i -lt $art.Count; $i++) {
            $line = -join ($art[$i].ToCharArray() | ForEach-Object {
                $k = [string]$_
                if ($map.ContainsKey($k)) { [char]$map[$k] } else { $_ }
            })
            Write-Host "  $line" -ForegroundColor $colours[$i]
        }
    } else {
        Write-Host '  D R O P W H E E L' -ForegroundColor Cyan
    }
    Write-Host ''
    Write-Host -NoNewline ('  ' + (T 'La ruota delle azioni sui file' 'A radial wheel of file actions')) -ForegroundColor White
    Write-Host '  -  by Haxurus' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host ('  {0,-16} {1}' -f (T 'Codice' 'Code'), $Repo)
    Write-Host ('  {0,-16} {1}' -f (T 'Documentazione' 'Documentation'), "$Repo/blob/main/docs/WINDOWS.md")
    Write-Host ('  {0,-16} {1}' -f (T 'Segnalazioni' 'Issues'), "$Repo/issues")
    Write-Host ('  {0,-16} {1}' -f (T 'Novita' 'Changelog'), "$Repo/blob/main/CHANGELOG.md")
}

# ------------------------------------------------------------------------------------ questions

function Ask([string]$question, [bool]$default = $true) {
    $hint = if ($default) { T '[S/n]' '[Y/n]' } else { T '[s/N]' '[y/N]' }
    while ($true) {
        Write-Host -NoNewline '  ? ' -ForegroundColor Yellow
        $reply = (Read-Host "$question $hint").Trim().ToLower()
        if ($reply -eq '') { return $default }
        if (@('s', 'si', 'y', 'yes') -contains $reply) { return $true }
        if (@('n', 'no') -contains $reply) { return $false }
        Info (T 'Rispondi s oppure n.' 'Please answer y or n.')
    }
}

# Choose "title" @(labels) -> the chosen indices (Enter = all, n = none, or numbers)
function Choose([string]$title, [string[]]$labels) {
    Write-Host "  $title" -ForegroundColor White
    for ($i = 0; $i -lt $labels.Count; $i++) {
        Write-Host -NoNewline ('    {0,2}  ' -f ($i + 1)) -ForegroundColor Cyan
        Write-Host $labels[$i]
    }
    while ($true) {
        Write-Host -NoNewline '  ? ' -ForegroundColor Yellow
        $reply = (Read-Host (T 'Quali installo? [Invio = tutti, n = nessuno, oppure i numeri, es. 1 3]' `
                                'Which ones? [Enter = all, n = none, or the numbers, e.g. 1 3]')).Trim().ToLower()
        if ($reply -eq '') { return @(0..($labels.Count - 1)) }
        if (@('n', 'no', '0', 'none', 'nessuno') -contains $reply) { return @() }
        $picked = @()
        $good = $true
        foreach ($w in ($reply -split '[,\s]+')) {
            $n = 0
            if ([int]::TryParse($w, [ref]$n) -and $n -ge 1 -and $n -le $labels.Count) { $picked += ($n - 1) }
            else { $good = $false }
        }
        if ($good) { return $picked }
        Info ((T 'Numeri da 1 a ' 'Numbers from 1 to ') + $labels.Count + '.')
    }
}

# ------------------------------------------------------------------------------------ helpers

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Winget-Install([string]$id) {
    # Out-Host: winget's output must not become the function's return value
    & winget install --id $id -e --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
    if ($LASTEXITCODE -ne 0) {
        # some packages have no per-user installer
        & winget install --id $id -e --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
    }
    Refresh-Path
    return ($LASTEXITCODE -eq 0)
}

# The interpreter to use: from the py launcher, else a python.exe that is not the Store alias.
function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $candidates += (& py -3 -c 'import sys; print(sys.executable)' 2>$null) } catch { }
    }
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -notmatch '\\WindowsApps\\') { $candidates += $c.Source }
    }
    foreach ($exe in $candidates) {
        if (-not $exe) { continue }
        try {
            $ok = & $exe -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>$null
            if ("$ok".Trim() -eq '1') { return "$exe".Trim() }
        } catch { }
    }
    return $null
}

# ------------------------------------------------------------------------------------ main

function Main {
    Banner
    Write-Host ''
    Warn (T "Anteprima: non ancora provata su un vero Windows. Segnala cio' che trovi: $Repo/issues" `
            "Preview: not yet tested on a real Windows machine. Please report what you find: $Repo/issues")
    Write-Host ''
    if (-not (Ask (T 'Iniziamo?' 'Shall we start?'))) { return }

    # --- winget
    Step 'winget'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn (T 'winget non trovato: installa "Programma di installazione app" dal Microsoft Store, poi riapri PowerShell.' `
                'winget not found: install "App Installer" from the Microsoft Store, then reopen PowerShell.')
        Info 'https://apps.microsoft.com/detail/9NBLGGH4NNS1'
        return
    }
    Ok 'winget'

    # --- Python
    Step 'Python'
    $python = Find-Python
    if (-not $python) {
        Info (T 'Serve Python 3.11 o successivo.' 'Python 3.11 or later is needed.')
        if (-not (Ask (T 'Installo Python 3.12 con winget?' 'Install Python 3.12 with winget?'))) {
            Cmd 'winget install --id Python.Python.3.12 -e'
            return
        }
        [void](Winget-Install 'Python.Python.3.12')
        $python = Find-Python
        if (-not $python) { Fail (T 'Python non trovato dopo l''installazione: riapri PowerShell e riprova.' 'Python not found after installing it: reopen PowerShell and try again.') }
    }
    Ok "$python  ($(& $python -c 'import platform; print(platform.python_version())'))"

    # --- the code
    Step (T 'Codice di Dropwheel' 'Dropwheel code')
    $here = $null
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'src\dropwheel\__init__.py'))) { $here = $PSScriptRoot }
    if ($here) {
        $src = $here
        Ok (T "Uso questa cartella: $src" "Using this folder: $src")
    } else {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            if (-not (Ask (T 'Serve Git per scaricare il codice. Lo installo con winget?' 'Git is needed to get the code. Install it with winget?'))) {
                Cmd 'winget install --id Git.Git -e'
                return
            }
            [void](Winget-Install 'Git.Git')
            if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail (T 'Git non trovato: riapri PowerShell e riprova.' 'Git not found: reopen PowerShell and try again.') }
        }
        $default = Join-Path $env:LOCALAPPDATA 'dropwheel\src'
        Info (T "Il codice resta in una cartella (l'installazione vi rimane collegata): $default" `
                "The code stays in a folder (the installation stays linked to it): $default")
        $src = $default
        if (Test-Path (Join-Path $src '.git')) {
            & git -C $src pull --ff-only
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path $src) | Out-Null
            & git clone --depth 1 "$Repo.git" $src
        }
        if ($LASTEXITCODE -ne 0) { Fail 'git' }
        Ok $src
    }

    # --- optional Python modules
    Step (T 'Moduli Python facoltativi' 'Optional Python modules')
    $extras = @(
        @('image', (T 'Immagini (HEIC, JPEG XL)' 'Images (HEIC, JPEG XL)')),
        @('pdf', 'PDF'),
        @('docs', (T 'Documenti e dati' 'Documents and data')),
        @('media', (T 'Audio e video (metadati)' 'Audio and video (metadata)')),
        @('fonts', 'Font'),
        @('archive', (T 'Archivi 7z e RAR' '7z and RAR archives')),
        @('qr', (T 'Codici QR' 'QR codes'))
    )
    $picked = Choose (T 'Ogni gruppo abilita altre azioni della ruota' 'Each group enables more actions of the wheel') ($extras | ForEach-Object { $_[1] })
    $names = @($picked | ForEach-Object { $extras[$_][0] })
    $target = if ($names.Count) { "$src[" + ($names -join ',') + ']' } else { $src }

    Step (T 'Installo Dropwheel' 'Installing Dropwheel')
    Info (T 'Con pip, per il tuo utente (PySide6 sono circa 80 MB).' 'With pip, for your user (PySide6 is about 80 MB).')
    & $python -m pip install --disable-pip-version-check --upgrade -e $target
    if ($LASTEXITCODE -ne 0) {
        if ($names.Count) {
            Warn (T 'Un modulo facoltativo non si installa: riprovo senza.' 'An optional module did not install: trying again without them.')
            & $python -m pip install --disable-pip-version-check --upgrade -e $src
        }
        if ($LASTEXITCODE -ne 0) { Fail 'pip install' }
    }
    Ok 'Dropwheel'

    # --- external programs
    Step (T 'Programmi esterni facoltativi' 'Optional external programs')
    $programs = @(
        @('Gyan.FFmpeg', (T 'FFmpeg: audio e video' 'FFmpeg: audio and video')),
        @('ImageMagick.ImageMagick', (T 'ImageMagick: immagini' 'ImageMagick: images')),
        @('OliverBetz.ExifTool', (T 'ExifTool: metadati' 'ExifTool: metadata')),
        @('TheDocumentFoundation.LibreOffice', (T 'LibreOffice: documenti Office' 'LibreOffice: Office documents')),
        @('JohnMacFarlane.Pandoc', (T 'Pandoc: conversione dei documenti' 'Pandoc: document conversion')),
        @('QPDF.QPDF', (T 'QPDF: PDF' 'QPDF: PDF')),
        @('oschwartz10612.Poppler', (T 'Poppler: PDF' 'Poppler: PDF')),
        @('UB-Mannheim.TesseractOCR', (T 'Tesseract: OCR' 'Tesseract: OCR')),
        @('7zip.7zip', (T '7-Zip: archivi' '7-Zip: archives')),
        @('yt-dlp.yt-dlp', (T 'yt-dlp: video dai link' 'yt-dlp: videos from links'))
    )
    $picked = Choose (T 'Con winget (ognuno abilita le sue azioni)' 'With winget (each one enables its own actions)') ($programs | ForEach-Object { $_[1] })
    foreach ($i in $picked) {
        $id = $programs[$i][0]
        Info $id
        if (Winget-Install $id) { Ok $id } else { Warn (T "$id non installato" "$id not installed") }
    }

    # --- the command in PATH
    $scripts = & $python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
    $dw = Join-Path "$scripts".Trim() 'dropwheel.exe'
    if (-not (Get-Command dropwheel -ErrorAction SilentlyContinue) -and (Test-Path $dw)) {
        $dir = Split-Path $dw
        if (Ask (T "Aggiungo $dir al PATH, cosi' il comando dropwheel funziona in ogni terminale?" `
                   "Add $dir to PATH, so that the dropwheel command works in every terminal?")) {
            $user = [Environment]::GetEnvironmentVariable('Path', 'User')
            [Environment]::SetEnvironmentVariable('Path', (@($user, $dir) | Where-Object { $_ }) -join ';', 'User')
            Refresh-Path
            Ok 'PATH'
        }
    }

    # --- Explorer, shortcut, start
    Step (T 'Esplora file e avvio' 'Explorer and start')
    Info (T 'Aggiunge "Apri in Dropwheel" al menu contestuale, "Invia a", il menu Start, l''avvio all''accesso e il font delle icone.' `
            'Adds "Open in Dropwheel" to the context menu, "Send to", the Start menu, starting at login and the icon font.')
    if (Ask (T 'Procedo?' 'Go ahead?')) {
        & $python -m dropwheel setup windows
    } else {
        Cmd 'dropwheel setup windows'
    }
    & $python -m dropwheel start
    if ($LASTEXITCODE -eq 0) { Ok (T 'Dropwheel e'' in esecuzione' 'Dropwheel is running') }
    else { Warn (T 'Avvio non riuscito: dropwheel doctor dice cosa manca.' 'Start failed: dropwheel doctor tells what is missing.') }

    Step (T 'Fatto' 'Done')
    Info (T 'Mentre trascini dei file in Esplora file tocca Shift: la ruota compare sotto il puntatore.' `
            'While dragging files in Explorer tap Shift: the wheel appears under the pointer.')
    Info (T 'Senza trascinare: copia dei file e premi Ctrl+Alt+D, oppure tasto destro -> Apri in Dropwheel.' `
            'Without dragging: copy some files and press Ctrl+Alt+D, or right click -> Open in Dropwheel.')
    Info (T 'Controllo degli strumenti: dropwheel doctor' 'Check the tools: dropwheel doctor')
    Info (T 'Per togliere tutto: dropwheel setup windows --remove, poi py -m pip uninstall dropwheel' `
            'To remove everything: dropwheel setup windows --remove, then py -m pip uninstall dropwheel')
}

try {
    Main
} catch {
    Write-Host ''
    Write-Host ((T 'Installazione interrotta: ' 'Installation stopped: ') + $_.Exception.Message) -ForegroundColor Red
}
