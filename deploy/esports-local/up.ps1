#Requires -Version 7.0
param([string]$Distribution, [switch]$Hmr, [switch]$Open)
& (Join-Path $PSScriptRoot '../dev.ps1') -Action Up @PSBoundParameters
