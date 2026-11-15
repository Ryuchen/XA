#Requires -Version 7.0
param([string]$Distribution)
& (Join-Path $PSScriptRoot '../dev.ps1') -Action Down @PSBoundParameters
