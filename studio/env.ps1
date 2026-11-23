# Dot-source in any PowerShell session:   . "<toolkit root>\env.ps1"
# Puts every recovery tool on PATH for THIS session only (nothing is changed machine-wide).
# Tool root: RE_TOOLS if already set, else the folder above studio\ when it holds 7-Zip, else <repo>\.tools
$studioDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$parentDir = Split-Path -Parent $studioDir
$T = if ($env:RE_TOOLS) { $env:RE_TOOLS } elseif (Test-Path (Join-Path $parentDir '7-Zip')) { $parentDir } else { Join-Path $parentDir '.tools' }
$env:RE_TOOLS          = $T
if (-not $env:JAVA_HOME -or -not (Test-Path $env:JAVA_HOME)) {
  $jdk = Get-ChildItem 'C:\Program Files\Eclipse Adoptium' -Directory -Filter 'jdk-*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
  if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
}
$env:GHIDRA_INSTALL_DIR = "$T\ghidra"                                # REA looks for this
$env:ANDROID_HOME      = "$T\android-sdk"
$env:ANDROID_SDK_ROOT  = "$T\android-sdk"
$ndk = Get-ChildItem "$T\android-sdk\ndk" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
if ($ndk) { $env:ANDROID_NDK_HOME = $ndk.FullName }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'

$add = @(
  "$env:JAVA_HOME\bin",
  "$T\7-Zip",
  "$T\ffmpeg\bin",
  "$T\llvm\bin",
  "$T\jadx\bin",
  "$T\ghidra\support",
  "$T\android-sdk\platform-tools",
  "$T\android-sdk\build-tools\36.0.0",
  "$T\android-sdk\cmdline-tools\latest\bin",
  "$T\pyenv\Scripts",
  "$T\Cpp2IL",
  "$T\Il2CppDumper",
  "$env:USERPROFILE\.dotnet\tools"
)
foreach ($p in $add) { if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" } }

# Helper: open an MSVC x64 developer prompt environment inside the current PowerShell session
function Enter-VsDev {
  $vsCandidates = @(
    'C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\Tools\VsDevCmd.bat',
    'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\Common7\Tools\VsDevCmd.bat',
    'C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\Tools\VsDevCmd.bat'
  )
  $vs = $vsCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($vs) {
    cmd /c "`"$vs`" -arch=x64 -no_logo && set" | ForEach-Object {
      if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
    }
  }
}
Write-Host "RE tools environment loaded from $T"
