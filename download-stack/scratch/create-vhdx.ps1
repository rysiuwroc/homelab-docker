#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Idempotent bootstrap of the qBittorrent scratch VHDX on the .69 Hyper-V host.

.DESCRIPTION
    Creates D:\qbit-scratch.vhdx (450GB dynamic) if it doesn't exist yet, and
    attaches it to the "Ubuntu LTS - Docker Host" VM as a SCSI disk if it isn't
    attached yet. Safe to re-run: both steps are guarded by existence checks.

    Run this FIRST (on .69, elevated PowerShell), then run setup-scratch.sh on
    .212 to format/mount it and (re)install the guard.

.NOTES
    Must run elevated (Run as Administrator) -- Hyper-V cmdlets require it.
#>
[CmdletBinding()]
param(
    [string]$VhdPath = "D:\qbit-scratch.vhdx",
    [string]$VMName  = "Ubuntu LTS - Docker Host",
    [uint64]$SizeBytes = 450GB
)

$ErrorActionPreference = "Stop"

Write-Host "==> [1/2] Checking VHDX at $VhdPath..."
if (-not (Test-Path $VhdPath)) {
    Write-Host "    Not found -- creating dynamic VHDX ($($SizeBytes / 1GB) GB)..."
    New-VHD -Path $VhdPath -SizeBytes $SizeBytes -Dynamic | Out-Null
    Write-Host "    Created $VhdPath."
} else {
    Write-Host "    Already exists -- skipping creation."
}

Write-Host "==> [2/2] Checking attachment to VM '$VMName'..."
$attached = Get-VMHardDiskDrive -VMName $VMName | Where-Object { $_.Path -eq $VhdPath }
if (-not $attached) {
    Write-Host "    Not attached -- adding as SCSI disk..."
    Add-VMHardDiskDrive -VMName $VMName -ControllerType SCSI -Path $VhdPath
    Write-Host "    Attached $VhdPath to $VMName."
} else {
    Write-Host "    Already attached (Controller $($attached.ControllerType) $($attached.ControllerNumber):$($attached.ControllerLocation)) -- skipping."
}

Write-Host "==> Done. Current state:"
Get-VHD -Path $VhdPath | Format-List Path, VhdType, Size, FileSize
Get-VMHardDiskDrive -VMName $VMName | Where-Object { $_.Path -eq $VhdPath } | Format-List VMName, ControllerType, ControllerNumber, ControllerLocation, Path
