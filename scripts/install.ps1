# Install a stele-context 2.x Windows binary from a GitHub Release.
$Version = if ($env:STELE_VERSION) { $env:STELE_VERSION } else { "2.0.1" }
$Prefix = if ($env:STELE_PREFIX) { $env:STELE_PREFIX } else { Join-Path $env:LOCALAPPDATA "stele-context" }
$Repo = if ($env:STELE_REPO) { $env:STELE_REPO } else { "IronAdamant/stele-context" }
$Arch = if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") {
    "aarch64-pc-windows-msvc"
} else {
    "x86_64-pc-windows-msvc"
}
$Url = "https://github.com/$Repo/releases/download/v$Version/stele-context-$Arch.exe"
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
$Dest = Join-Path $Prefix "stele-context.exe"
Invoke-WebRequest -Uri $Url -OutFile $Dest
Write-Output "installed $Dest $Version ($Arch)"
