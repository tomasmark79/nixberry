# Only modify a new VM in its dedicated directory. The request is JSON,
# never a command assembled from host-provided names.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$s = $Request.spec
function Require($condition, [string]$message) { if (-not $condition) { throw $message } }
function Canonical([string]$path) { [IO.Path]::GetFullPath($path).TrimEnd('\') }
Require ($env:COMPUTERNAME -eq $s.windowsComputerName) 'Windows host does not match.'
Require ($s.name -cmatch '^(?!(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\z)[a-z][a-z0-9-]{0,62}\z') 'Disallowed VM name.'
Require ($s.ownerId -cmatch '^nixberry-hyperv-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') 'Invalid VM owner.'
Require ($s.generation -eq 2 -and -not $s.secureBoot) 'UEFI profile does not match.'
Require ($s.vcpus -ge 1 -and $s.memoryMiB -ge 256 -and $s.diskGiB -ge 2) 'Invalid VM capacity.'
Require ($s.mac -match '^[0-9A-Fa-f]{12}$') 'Invalid MAC address.'
Require ($s.storageDirectory -match '^[A-Za-z]:\\' -and $s.storageDirectory -notmatch '\.\.') 'Disallowed path.'
Import-Module Hyper-V -ErrorAction Stop
$null = Get-VMHost -ErrorAction Stop

# The first declared adapter is the management path; all NICs retain switch identity checks.
$adapters = if ($null -ne $s.networkAdapters) { @($s.networkAdapters) } else {
    @([pscustomobject]@{ name='management'; mac=$s.mac; switchName=$s.switchName; switchId=$s.switchId })
}
Require ($adapters.Count -ge 1 -and $adapters.Count -le 8) 'networkAdapters must contain 1–8 ordered adapters.'
Require ($adapters[0].mac -eq $s.mac -and $adapters[0].switchName -eq $s.switchName -and $adapters[0].switchId -eq $s.switchId) 'The first adapter must match the primary MAC and host attachment; it provides management access.'
Require (@($adapters | Select-Object -ExpandProperty mac -Unique).Count -eq $adapters.Count -and @($adapters | Select-Object -ExpandProperty name -Unique).Count -eq $adapters.Count) 'Network adapter names and MAC addresses must be unique within a VM.'
foreach ($adapter in $adapters) {
    Require ($adapter.mac -match '^[0-9A-Fa-f]{12}$' -and $adapter.mac -ne '000000000000' -and ([Convert]::ToInt32($adapter.mac.Substring(0,2),16) -band 1) -eq 0) 'Each network adapter requires a nonzero unicast MAC address.'
    $declaredSwitch = @(Get-VMSwitch -Name $adapter.switchName -ErrorAction Stop)
    Require ($declaredSwitch.Count -eq 1 -and $declaredSwitch[0].Id.ToString() -eq $adapter.switchId) 'Switch does not match.'
}
function CheckAdapters($vm) {
    $nics = @(Get-VMNetworkAdapter -VM $vm)
    Require ($nics.Count -eq $adapters.Count) 'VM network does not match.'
    foreach ($adapter in $adapters) {
        $matches = @($nics | Where-Object { $_.MacAddress -eq $adapter.mac -and $_.SwitchId.ToString() -eq $adapter.switchId })
        Require ($matches.Count -eq 1) 'VM network does not match.'
    }
}
$base = Canonical $s.storageDirectory
$root = Join-Path $base $s.name
$diskPath = Join-Path $root 'system.vhdx'
$media = Join-Path $base 'media'
Require ($Request.sha256 -match '^[a-f0-9]{64}$') 'The media hash is missing.'
$method = if ($s.deploymentMethod) { $s.deploymentMethod } else { 'image' }
Require ($method -in @('iso', 'image')) 'Deployment method must be iso or image.'
$extension = if ($method -eq 'iso') { '.iso' } else { '.vhdx' }
$mediaPath = Join-Path $media ($Request.sha256 + $extension)
$isoPath = if ($method -eq 'iso') { $mediaPath } else { $null }
$imagePath = if ($method -eq 'image') { $mediaPath } else { $null }
$ownerPath = Join-Path $root 'owner.json'
# Refuse storage redirection through junctions or symlinks.
$ancestor = $base
while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
        Require (-not ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'The storage is a reparse point.'
    }
    $parent = Split-Path -Parent $ancestor
    if ($parent -eq $ancestor) { break }
    $ancestor = $parent
}
function FreshTarget {
    Require (-not (Get-VM -Name $s.name -ErrorAction SilentlyContinue)) 'The VM already exists; it will not be overwritten.'
    Require (-not (Test-Path -LiteralPath $root)) 'The VM directory already exists.'
    $duplicate = @(Get-VM | Get-VMNetworkAdapter | Where-Object { $_.MacAddress -in @($adapters.mac) })
    Require ($duplicate.Count -eq 0) 'The MAC address is already used by another VM.'
    $drive = Get-PSDrive -Name $base.Substring(0,1)
    Require ($drive.Free -ge ([int64]$s.diskGiB * 1GB + [int64]$Request.mediaBytes + 2GB)) 'Not enough free space.'
}
function OwnedVm {
    Require (Test-Path -LiteralPath $ownerPath) 'The VM ownership record is missing.'
    $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
    $ownerMethod = if ($owner.method) { $owner.method } else { 'iso' }
    Require ($ownerMethod -eq $method) 'The method differs from the created VM; use the original --method.'
    # Keep isoSha256 in stored media identity for previously created VMs as well.
    Require ($owner.ownerId -eq $s.ownerId -and $owner.isoSha256 -eq $Request.sha256) 'Ownership or media does not match.'
    $vm = Get-VM -Name $s.name -ErrorAction Stop
    Require ($vm.Id.ToString() -eq $owner.vmId -and $vm.Notes -eq $s.ownerId) 'VM ID does not match.'
    Require ($vm.Generation -eq 2 -and (Canonical $vm.Path).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) 'Configuration directory does not match.'
    Require ($vm.ProcessorCount -eq $s.vcpus -and $vm.MemoryStartup -eq ([int64]$s.memoryMiB * 1MB)) 'CPU or RAM does not match.'
    Require (-not (Get-VMMemory -VM $vm).DynamicMemoryEnabled) 'RAM mode does not match.'
    Require ((Get-VMFirmware -VM $vm).SecureBoot.ToString() -eq 'Off') 'Secure Boot is not disabled.'
    $disks = @(Get-VMHardDiskDrive -VM $vm)
    Require ($disks.Count -eq 1 -and (Canonical $disks[0].Path) -eq $diskPath) 'Target VHDX does not match.'
    $vhd = Get-VHD -Path $diskPath
    Require ($vhd.Size -eq ([int64]$s.diskGiB * 1GB) -and -not $vhd.ParentPath -and $vhd.VhdType.ToString() -eq 'Dynamic') 'VHDX type or size does not match.'
    $users = @(Get-VM | Get-VMHardDiskDrive | Where-Object { $_.Path -and (Canonical $_.Path) -eq $diskPath })
    Require ($users.Count -eq 1) 'The disk is shared with another VM.'
    CheckAdapters $vm
    Require (@(Get-VMSnapshot -VM $vm).Count -eq 0) 'The VM has snapshots.'
    return $vm
}
function Snapshot($vm) {
    $nic = @(Get-VMNetworkAdapter -VM $vm | Where-Object { $_.MacAddress -eq $s.mac })[0]
    $dvd = @(Get-VMDvdDrive -VM $vm)
    # Linux DMI uses BIOSGUID, a different UUID from the Hyper-V VM identifier.
    $settings = @(Get-CimInstance -Namespace root/virtualization/v2 -ClassName Msvm_VirtualSystemSettingData | Where-Object {
        $_.VirtualSystemIdentifier -eq $vm.Id.ToString() -and $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized'
    })
    Require ($settings.Count -eq 1) 'Cannot unambiguously determine the VM BIOS UUID.'
    $biosGuid = ([Guid]::Parse($settings[0].BIOSGUID)).ToString()
    [ordered]@{
        name = $vm.Name; id = $vm.Id.ToString(); state = $vm.State.ToString()
        biosGuid = $biosGuid
        root = $root; diskPath = $diskPath; isoPath = $isoPath; imagePath = $imagePath
        mac = $nic.MacAddress; ips = @($nic.IPAddresses)
        dvd = @($dvd | ForEach-Object { $_.Path })
    }
}
$result = switch ($Request.action) {
    'plan' {
        FreshTarget
        [ordered]@{ computerName = $env:COMPUTERNAME; name = $s.name; generation = 2
            memoryMiB = $s.memoryMiB; vcpus = $s.vcpus; diskGiB = $s.diskGiB
            root = $root; diskPath = $diskPath; isoPath = $isoPath; imagePath = $imagePath
            switchName = $s.switchName; switchId = $s.switchId; mac = $s.mac; networkAdapters = $adapters; secureBoot = $false }
    }
    'prepare' {
        FreshTarget
        if (-not (Test-Path -LiteralPath $media)) { $null = New-Item -ItemType Directory -Path $media -Force }
        Require (-not ((Get-Item -LiteralPath $media).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'The media directory is a reparse point.'
        Require ($Request.token -match '^[a-f0-9]{32}$') 'Invalid upload identifier.'
        $upload = Join-Path $media ($Request.token + '.upload')
        Require (-not (Test-Path -LiteralPath $upload)) 'The upload already exists.'
        $cached = Test-Path -LiteralPath $mediaPath
        if ($cached) {
            Require ((Get-FileHash -LiteralPath $mediaPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Stored media hash does not match.'
        }
        [ordered]@{ uploadPath = $upload; cached = $cached }
    }
    'create' {
        FreshTarget
        if (-not (Test-Path -LiteralPath $mediaPath)) {
            Require ($Request.token -match '^[a-f0-9]{32}$') 'Invalid upload identifier.'
            $upload = Join-Path $media ($Request.token + '.upload')
            Require ((Get-Item -LiteralPath $upload).Length -eq $Request.mediaBytes) 'Upload length does not match.'
            Require ((Get-FileHash -LiteralPath $upload -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Upload hash does not match.'
            Move-Item -LiteralPath $upload -Destination $mediaPath
        }
        Require ((Get-FileHash -LiteralPath $mediaPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Media hash does not match.'
        $null = New-Item -ItemType Directory -Path $root
        if ($method -eq 'iso') {
            $null = New-VHD -Path $diskPath -SizeBytes ([int64]$s.diskGiB * 1GB) -Dynamic
        } else {
            Copy-Item -LiteralPath $imagePath -Destination $diskPath
            Require ((Get-FileHash -LiteralPath $diskPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'System disk copy hash does not match.'
        }
        $vm = New-VM -Name $s.name -Generation 2 -MemoryStartupBytes ([int64]$s.memoryMiB * 1MB) -VHDPath $diskPath -Path (Join-Path $root 'config') -SwitchName $s.switchName
        Set-VM -VM $vm -ProcessorCount $s.vcpus -AutomaticStartAction Nothing -AutomaticStopAction ShutDown -CheckpointType Disabled -Notes $s.ownerId
        Set-VMMemory -VM $vm -DynamicMemoryEnabled $false
        Set-VMNetworkAdapter -VM $vm -StaticMacAddress $s.mac
        foreach ($adapter in @($adapters | Select-Object -Skip 1)) {
            $null = Add-VMNetworkAdapter -VM $vm -Name $adapter.name -SwitchName $adapter.switchName -StaticMacAddress $adapter.mac
        }
        $disk = Get-VMHardDiskDrive -VM $vm
        if ($method -eq 'iso') {
            $dvd = Add-VMDvdDrive -VM $vm -Path $isoPath -Passthru
            Set-VMFirmware -VM $vm -EnableSecureBoot Off -BootOrder @($dvd,$disk)
        } else {
            Set-VMFirmware -VM $vm -EnableSecureBoot Off -BootOrder @($disk)
        }
        [ordered]@{ ownerId=$s.ownerId; vmId=$vm.Id.ToString(); isoSha256=$Request.sha256; method=$method } | ConvertTo-Json | Set-Content -LiteralPath $ownerPath -Encoding UTF8
        $vm = OwnedVm
        if ($method -eq 'iso') { Start-VM -VM $vm }
        Snapshot (Get-VM -Id $vm.Id)
    }
    'status' { Snapshot (OwnedVm) }
    'start-disk' {
        $vm = OwnedVm
        Require ($method -eq 'image') 'This operation requires the image method.'
        Require ($vm.State.ToString() -in @('Off', 'Running')) 'The VM is not ready to boot from disk.'
        Require (@(Get-VMDvdDrive -VM $vm | Where-Object { $_.Path }).Count -eq 0) 'The image method must not have an installation ISO attached.'
        if ($vm.State.ToString() -eq 'Off') { Start-VM -VM $vm }
        Snapshot (Get-VM -Id $vm.Id)
    }
    'boot-disk' {
        $vm = OwnedVm
        Require ($vm.State.ToString() -eq 'Off') 'The VM must be shut down gracefully.'
        $dvd = @(Get-VMDvdDrive -VM $vm)
        Require ($dvd.Count -eq 1 -and (Canonical $dvd[0].Path) -eq $isoPath) 'Installation DVD does not match.'
        Set-VMDvdDrive -VMDvdDrive $dvd[0] -Path $null
        Set-VMFirmware -VM $vm -BootOrder @(Get-VMHardDiskDrive -VM $vm)
        Start-VM -VM $vm
        Snapshot (Get-VM -Id $vm.Id)
    }
    default { throw 'Disallowed operation.' }
}
$result | ConvertTo-Json -Depth 8 -Compress
