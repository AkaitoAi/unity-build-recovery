# Dot-source in any PowerShell session:   . "<toolkit root>\env.ps1"
# Puts every recovery tool on PATH for THIS session only (nothing is changed machine-wide).
$T = if ($PSScriptRoot) { $PSScriptRoot } else { (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$env:RE_TOOLS          = $T
$env:JAVA_HOME         = 'C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot'
$env:GHIDRA_INSTALL_DIR = "$T\ghidra"                                # REA looks for this
$env:ANDROID_HOME      = "$T\android-sdk"
$env:ANDROID_SDK_ROOT  = "$T\android-sdk"
$env:ANDROID_NDK_HOME  = "$T\android-sdk\ndk\27.2.12479018"
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
