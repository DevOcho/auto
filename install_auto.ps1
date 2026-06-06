<#
.SYNOPSIS
    Installs the `auto` CLI natively on Windows (PowerShell, no WSL needed to run auto itself).

.DESCRIPTION
    Downloads the latest windows-x86_64 release of `auto` from GitHub, installs it to
    %USERPROFILE%\.auto, preserves any existing config\local.yaml, and adds the install
    directory to your USER PATH.

    With -InstallDeps it also installs the prerequisites auto orchestrates. Docker
    Desktop + WSL2 require administrator rights and a one-time RESTART, so the script
    self-elevates (UAC) for that part and then tells you exactly what to do after the
    reboot. It is idempotent: re-run it anytime; it skips whatever is already installed.

.EXAMPLE
    # Install auto only (no admin needed)
    iwr -useb https://www.devocho.com/auto.ps1 | iex

.EXAMPLE
    # Install prerequisites (Docker Desktop, WSL2, k3d, kubectl, helm, git, mkcert) + auto
    .\install_auto.ps1 -InstallDeps
#>
[CmdletBinding()]
param(
    [switch]$InstallDeps
)

$ErrorActionPreference = 'Stop'
$Repo = 'devocho/auto'
$AssetSuffix = 'windows-x86_64'
$AutoDir = Join-Path $env:USERPROFILE '.auto'

function Write-Step($msg) { Write-Host " - $msg" }
function Test-Tool($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

# Tracks whether the container engine was freshly installed (=> a reboot is required).
$script:FreshEngine = $false

# 1. Architecture check (we ship x86_64/amd64 Windows builds for now)
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -ne 'AMD64') {
    Write-Warning "auto ships x86_64 (amd64) Windows builds; detected '$arch'. The binary may not run."
}
Write-Step "Detected Windows / $arch (asset: $AssetSuffix)"

# 2. Installing Docker Desktop + WSL2 needs administrator rights. Self-elevate (UAC)
#    for -InstallDeps so the engine can be installed; the elevated window finishes the job.
if ($InstallDeps -and -not (Test-Admin)) {
    if ($PSCommandPath) {
        Write-Host "Docker Desktop and WSL2 need administrator rights -- requesting elevation (accept the UAC prompt)..."
        Start-Process -FilePath 'powershell' -Verb RunAs -ArgumentList @(
            '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`"", '-InstallDeps'
        )
        return
    }
    Write-Warning "Please re-run this in an elevated PowerShell (Run as administrator) so Docker Desktop and WSL2 can be installed."
}

function Install-Deps {
    Write-Host "Installing prerequisites..."
    $hasWinget = Test-Tool winget
    if (-not $hasWinget) {
        Write-Warning "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    }

    # --- Container engine: WSL2 + Docker Desktop (need admin; a fresh install needs a reboot) ---
    # Consider the engine present if the `docker` command is on PATH (covers Docker
    # Desktop, Rancher Desktop, or a custom install) OR Docker Desktop is installed
    # in its standard location. If so, we leave it completely untouched.
    $dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    $enginePresent = (Test-Tool docker) -or (Test-Path $dockerExe)
    if ($enginePresent) {
        Write-Step "Container engine already present (docker detected) -- leaving it as-is."
    }
    else {
        Write-Step "Enabling WSL2 (Docker Desktop's backend)..."
        try { wsl --install --no-distribution } catch {
            Write-Warning "wsl --install reported: $($_.Exception.Message). You may need to enable WSL2 manually."
        }
        if ($hasWinget) {
            Write-Step "Installing Docker Desktop..."
            winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        }
        $script:FreshEngine = $true
    }

    # --- CLI tools (no reboot needed). Skip any that are already on PATH so we
    #     never re-install or upgrade tools you already have. ---
    if ($hasWinget) {
        $tools = @(
            @{ Name = 'kubectl'; Id = 'Kubernetes.kubectl'; Cmd = 'kubectl' },
            @{ Name = 'Helm';    Id = 'Helm.Helm';          Cmd = 'helm'    },
            @{ Name = 'Git';     Id = 'Git.Git';            Cmd = 'git'     },
            @{ Name = 'mkcert';  Id = 'FiloSottile.mkcert'; Cmd = 'mkcert'  }
        )
        foreach ($t in $tools) {
            if (Test-Tool $t.Cmd) {
                Write-Step "$($t.Name) already installed -- skipping."
            }
            else {
                Write-Step "Installing $($t.Name)..."
                winget install -e --id $t.Id --accept-source-agreements --accept-package-agreements
            }
        }
    }

    # k3d has no reliable winget package: prefer scoop, then choco, else download into ~/.auto.
    if (-not (Test-Tool k3d)) {
        if (Test-Tool scoop) { Write-Step "Installing k3d via scoop..."; scoop install k3d }
        elseif (Test-Tool choco) { Write-Step "Installing k3d via choco..."; choco install k3d -y }
        else {
            Write-Step "Downloading k3d.exe into $AutoDir ..."
            New-Item -ItemType Directory -Force -Path $AutoDir | Out-Null
            $k3dUrl = "https://github.com/k3d-io/k3d/releases/latest/download/k3d-windows-amd64.exe"
            Invoke-WebRequest -Uri $k3dUrl -OutFile (Join-Path $AutoDir 'k3d.exe')
        }
    }
    # The mkcert CA is trusted on the first `auto start` with https:true (the Windows
    # certificate prompt appears then), so nothing to trust at install time.
}

# 3. Create the install directory
New-Item -ItemType Directory -Force -Path $AutoDir | Out-Null
Write-Step "Directory $AutoDir ready"

# 4. Preserve an existing local.yaml across reinstalls
$localYaml = Join-Path $AutoDir 'config\local.yaml'
$configBak = $null
if (Test-Path $localYaml) {
    Write-Step "Previous install detected; saving local.yaml"
    $configBak = Join-Path $env:TEMP 'auto-local.yaml.bak'
    Copy-Item -Force $localYaml $configBak
}

# 5. Optionally install prerequisites
if ($InstallDeps) { Install-Deps }

# 6. Download + extract the auto release (non-fatal if no Windows release is published yet)
try {
    Write-Step "Looking up the latest release on GitHub..."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ 'User-Agent' = 'auto-installer' }
    $asset = $release.assets | Where-Object { $_.name -like "auto-*$AssetSuffix.zip" } | Select-Object -First 1
    if (-not $asset) { throw "No '$AssetSuffix' asset in the latest release." }

    $tmpZip = Join-Path $env:TEMP 'auto-latest.zip'
    Write-Step "Downloading $($asset.name)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmpZip

    $tmpDir = Join-Path $env:TEMP ('auto-extract-' + [System.Guid]::NewGuid().ToString('N'))
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    $staged = Get-ChildItem -Path $tmpDir -Directory | Select-Object -First 1
    if (-not $staged) { $staged = Get-Item $tmpDir }
    Copy-Item -Recurse -Force (Join-Path $staged.FullName '*') $AutoDir
    Write-Step "auto installed into $AutoDir"

    if ($configBak) {
        Copy-Item -Force $configBak $localYaml
        Write-Step "Restored local.yaml"
        Remove-Item -Force $configBak -ErrorAction SilentlyContinue
    }
    Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}
catch {
    Write-Warning "Could not download the auto release: $($_.Exception.Message)"
    Write-Warning "If no Windows release is published yet, publish one (git tag vX.Y.Z) and re-run,"
    Write-Warning "or copy a built auto.exe into $AutoDir manually."
}

# 7. Add ~/.auto to the USER PATH (persists across sessions)
$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($null -eq $userPath) { $userPath = '' }
if ($userPath -notlike "*$AutoDir*") {
    if ($userPath) { $newPath = "$userPath;$AutoDir" } else { $newPath = $AutoDir }
    [Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
    Write-Step "Added $AutoDir to your USER PATH."
}

# 8. Next steps -- honest hand-off at the unavoidable reboot boundary
Write-Host ""
Write-Host "================  auto installed  ================"
if ($script:FreshEngine) {
    Write-Host ""
    Write-Host "Docker Desktop + WSL2 were just installed and need a one-time RESTART:"
    Write-Host "  1) Restart your computer."
    Write-Host "  2) After restart, Docker Desktop will launch -- accept its terms and"
    Write-Host "     wait until it shows 'Engine running'."
    Write-Host "  3) Open a NEW terminal and run:   auto start"
    Write-Host ""
    Write-Host "This installer is safe to re-run; it skips whatever is already installed."
}
else {
    Write-Host "Open a NEW terminal, then:"
    Write-Host "  auto doctor    # check that all prerequisites are present and Docker is running"
    Write-Host "  auto start     # create the local cluster"
}
