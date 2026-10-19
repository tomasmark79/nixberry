# Samostatný reset nepotřebuje původní ISO ani sestavení cílového systému.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$s = $Request.spec
function Require($condition, [string]$message) { if (-not $condition) { throw $message } }
function Canonical([string]$path) { [IO.Path]::GetFullPath($path).TrimEnd('\') }
Require ($Request.allowReset -is [bool] -and $Request.allowReset -eq $true) 'Reset není výslovně povolený.'
Require ($Request.apply -is [bool]) 'Reset vyžaduje jednoznačný režim apply.'
Require ($env:COMPUTERNAME -eq $s.windowsComputerName) 'Nesouhlasí Windows hostitel.'
Require ($s.name -cmatch '^nixberry-[a-z0-9][a-z0-9-]{0,52}$') 'Nepovolený název VM.'
Require ($s.ownerId -ceq ('nixberry-hyperv-' + $s.uuid)) 'Nesouhlasí deklarované UUID a vlastník.'
Require ($s.storageDirectory -match '^[A-Za-z]:\\' -and $s.storageDirectory -notmatch '\.\.') 'Nepovolená cesta.'
Import-Module Hyper-V -ErrorAction Stop
$base = Canonical $s.storageDirectory
$root = Join-Path $base $s.name
$diskPath = Join-Path $root 'system.vhdx'
$ownerPath = Join-Path $root 'owner.json'

function CheckTree {
    $ancestor = $root
    while ($ancestor) {
        $item = Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop
        Require (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Cesta VM obsahuje reparse point.'
        $parent = Split-Path -Parent $ancestor
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath ($pending.Pop()) -Force -ErrorAction Stop) {
            Require (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and -not $item.LinkType) 'Adresář VM obsahuje odkaz; reset odmítám.'
            Require ($item.Extension -ne '.avhdx') 'VM obsahuje rozdílový disk; reset odmítám.'
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function OwnedVm {
    CheckTree
    $owner = Get-Content -LiteralPath $ownerPath -Raw -ErrorAction Stop | ConvertFrom-Json
    Require ($owner.ownerId -ceq $s.ownerId -and $owner.vmId) 'Nesouhlasí záznam vlastnictví VM.'
    $vm = Get-VM -Name $s.name -ErrorAction Stop
    Require ($vm.Id.ToString() -eq $owner.vmId -and $vm.Notes -ceq $s.ownerId) 'Nesouhlasí skutečné VM ID nebo vlastník.'
    Require ($vm.State.ToString() -in @('Running', 'Off')) 'VM je v nepodporovaném stavu; reset odmítám.'
    Require ($vm.Generation -eq 2 -and (Canonical $vm.Path).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) 'Nesouhlasí konfigurační adresář nebo generace VM.'
    Require ($vm.ProcessorCount -eq $s.vcpus -and $vm.MemoryStartup -eq ([int64]$s.memoryMiB * 1MB)) 'Nesouhlasí CPU nebo RAM.'
    Require ((Get-VMFirmware -VM $vm).SecureBoot.ToString() -eq 'Off') 'Nesouhlasí Secure Boot.'
    $disks = @(Get-VMHardDiskDrive -VM $vm)
    Require ($disks.Count -eq 1 -and (Canonical $disks[0].Path) -eq $diskPath) 'Nesouhlasí systémový disk nebo je připojen další disk.'
    $vhd = Get-VHD -Path $diskPath -ErrorAction Stop
    Require ($vhd.Size -eq ([int64]$s.diskGiB * 1GB) -and -not $vhd.ParentPath -and $vhd.VhdType.ToString() -eq 'Dynamic') 'Nesouhlasí velikost nebo původ VHDX.'
    $nics = @(Get-VMNetworkAdapter -VM $vm)
    Require ($nics.Count -eq 1 -and $nics[0].MacAddress -eq $s.mac -and $nics[0].SwitchId.ToString() -eq $s.switchId) 'Nesouhlasí síť VM.'
    Require (@(Get-VMSnapshot -VM $vm).Count -eq 0) 'VM má checkpointy; reset odmítám.'
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
                Require (-not (Canonical $reference).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -and (Canonical $reference) -ne $root) 'Soubor této VM používá jiná VM; reset odmítám.'
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
    # Bez TurnOff a Force: nefunkční korektní vypnutí nesmí přejít na tvrdé.
    $null = Stop-VM -VM $vm -Confirm:$false -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ((Get-VM -Id $expectedId -ErrorAction Stop).State.ToString() -ne 'Off') {
        Require ([DateTime]::UtcNow -lt $deadline) 'VM se korektně nevypnula; nic nemažu.'
        Start-Sleep -Seconds 2
    }
}
$vm = OwnedVm
Require ($vm.State.ToString() -eq 'Off' -and $vm.Id.ToString() -eq $expectedId) 'VM se během resetu změnila; nic nemažu.'
$null = Remove-VM -VM $vm -Force -ErrorAction Stop
Require (-not (Get-VM -Id $expectedId -ErrorAction SilentlyContinue)) 'Odstranění VM se nepodařilo potvrdit.'
Require (-not (Get-VM -Name $s.name -ErrorAction SilentlyContinue)) 'Po odstranění je znovu přítomná VM stejného jména; adresář ponechávám.'
CheckTree
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop
Require (-not (Test-Path -LiteralPath $root)) 'Adresář VM po odstranění zůstal.'
# Sdílený adresář media není součástí resetu jedné VM.
[ordered]@{ removed = $s.uuid; vmId = $expectedId; directory = $root; sharedMediaRetained = $true } | ConvertTo-Json -Compress
