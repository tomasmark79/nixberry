# Standalone reset needs neither the original ISO nor target system builds.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$s = $Request.spec
function Require($condition, [string]$message) { if (-not $condition) { throw $message } }
function Canonical([string]$path) { [IO.Path]::GetFullPath($path).TrimEnd('\') }
Require ($Request.allowReset -is [bool] -and $Request.allowReset -eq $true) 'Reset is not explicitly allowed.'
Require ($Request.apply -is [bool]) 'Reset requires an unambiguous apply mode.'
Require ($env:COMPUTERNAME -eq $s.windowsComputerName) 'Windows host does not match.'
Require ($s.name -cmatch '^nixberry-[a-z0-9][a-z0-9-]{0,52}$') 'Disallowed VM name.'
Require ($s.ownerId -ceq ('nixberry-hyperv-' + $s.uuid)) 'Declared UUID and owner do not match.'
Require ($s.storageDirectory -match '^[A-Za-z]:\\' -and $s.storageDirectory -notmatch '\.\.') 'Disallowed path.'
Import-Module Hyper-V -ErrorAction Stop
$base = Canonical $s.storageDirectory
$root = Join-Path $base $s.name
$diskPath = Join-Path $root 'system.vhdx'
$ownerPath = Join-Path $root 'owner.json'

function CheckTree {
    $ancestor = $root
    while ($ancestor) {
        $item = Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop
        Require (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'The VM path contains a reparse point.'
        $parent = Split-Path -Parent $ancestor
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath ($pending.Pop()) -Force -ErrorAction Stop) {
            Require (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and -not $item.LinkType) 'The VM directory contains a link; refusing reset.'
            Require ($item.Extension -ne '.avhdx') 'The VM contains a differencing disk; refusing reset.'
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function OwnedVm {
    CheckTree
    $owner = Get-Content -LiteralPath $ownerPath -Raw -ErrorAction Stop | ConvertFrom-Json
    Require ($owner.ownerId -ceq $s.ownerId -and $owner.vmId) 'VM ownership record does not match.'
    $vm = Get-VM -Name $s.name -ErrorAction Stop
    Require ($vm.Id.ToString() -eq $owner.vmId -and $vm.Notes -ceq $s.ownerId) 'Actual VM ID or owner does not match.'
    Require ($vm.State.ToString() -in @('Running', 'Off')) 'The VM is in an unsupported state; refusing reset.'
    Require ($vm.Generation -eq 2 -and (Canonical $vm.Path).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) 'Configuration directory or VM generation does not match.'
    Require ($vm.ProcessorCount -eq $s.vcpus -and $vm.MemoryStartup -eq ([int64]$s.memoryMiB * 1MB)) 'CPU or RAM does not match.'
    Require ((Get-VMFirmware -VM $vm).SecureBoot.ToString() -eq 'Off') 'Secure Boot does not match.'
    $disks = @(Get-VMHardDiskDrive -VM $vm)
    Require ($disks.Count -eq 1 -and (Canonical $disks[0].Path) -eq $diskPath) 'The system disk does not match or an additional disk is attached.'
    $vhd = Get-VHD -Path $diskPath -ErrorAction Stop
    Require ($vhd.Size -eq ([int64]$s.diskGiB * 1GB) -and -not $vhd.ParentPath -and $vhd.VhdType.ToString() -eq 'Dynamic') 'VHDX size or origin does not match.'
    $nics = @(Get-VMNetworkAdapter -VM $vm)
    Require ($nics.Count -eq 1 -and $nics[0].MacAddress -eq $s.mac -and $nics[0].SwitchId.ToString() -eq $s.switchId) 'VM network does not match.'
    Require (@(Get-VMSnapshot -VM $vm).Count -eq 0) 'The VM has checkpoints; refusing reset.'
    foreach ($peer in Get-VM) {
        if ($peer.Id -eq $vm.Id) { continue }
        $references = @($peer.Path, $peer.ConfigurationLocation, $peer.SnapshotFileLocation, $peer.SmartPagingFilePath)
        $references += @(Get-VMHardDiskDrive -VM $peer | ForEach-Object { $_.Path })
        $references += @(Get-VMDvdDrive -VM $peer | ForEach-Object { $_.Path })
        foreach ($checkpoint in Get-VMSnapshot -VM $peer) {
            $references += @(Get-VMHardDiskDrive -VMSnapshot $checkpoint | ForEach-Object { $_.Path })
        }
        foreach ($reference in $references) {
            if ($reference) {
                Require (-not (Canonical $reference).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -and (Canonical $reference) -ne $root) 'Another VM uses a file belonging to this VM; refusing reset.'
            }
        }
    }
    return $vm
}

$vm = OwnedVm
$expectedId = $vm.Id.ToString()
$plan = [ordered]@{ uuid = $s.uuid; vmId = $expectedId; directory = $root; disk = $diskPath; state = $vm.State.ToString(); sharedMediaRetained = $true; readOnly = -not $Request.apply }
if (-not $Request.apply) {
    $plan | ConvertTo-Json -Compress
    return
}
if ($vm.State.ToString() -eq 'Running') {
    # No TurnOff or Force: failed graceful shutdown must not escalate to power-off.
    $null = Stop-VM -VM $vm -Confirm:$false -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ((Get-VM -Id $expectedId -ErrorAction Stop).State.ToString() -ne 'Off') {
        Require ([DateTime]::UtcNow -lt $deadline) 'The VM did not shut down gracefully; nothing will be deleted.'
        Start-Sleep -Seconds 2
    }
}
$vm = OwnedVm
Require ($vm.State.ToString() -eq 'Off' -and $vm.Id.ToString() -eq $expectedId) 'The VM changed during reset; nothing will be deleted.'
$null = Remove-VM -VM $vm -Force -ErrorAction Stop
Require (-not (Get-VM -Id $expectedId -ErrorAction SilentlyContinue)) 'Could not confirm VM removal.'
Require (-not (Get-VM -Name $s.name -ErrorAction SilentlyContinue)) 'A VM with the same name is present again after removal; keeping the directory.'
CheckTree
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop
Require (-not (Test-Path -LiteralPath $root)) 'The VM directory remains after removal.'
# Shared media storage is outside a single VM reset.
[ordered]@{ removed = $s.uuid; vmId = $expectedId; directory = $root; sharedMediaRetained = $true } | ConvertTo-Json -Compress
