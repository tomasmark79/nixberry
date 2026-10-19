# Zasahujeme pouze novou VM ve vyhrazeném adresáři. Request je JSON,
# nikoli příkaz složený z názvů na hostiteli.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$s = $Request.spec
function Require($condition, [string]$message) { if (-not $condition) { throw $message } }
function Canonical([string]$path) { [IO.Path]::GetFullPath($path).TrimEnd('\') }
Require ($env:COMPUTERNAME -eq $s.windowsComputerName) 'Nesouhlasí Windows hostitel.'
Require ($s.name -cmatch '^nixberry-[a-z0-9][a-z0-9-]{0,52}$') 'Nepovolený název VM.'
Require ($s.ownerId -cmatch '^nixberry-hyperv-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') 'Neplatný vlastník VM.'
Require ($s.generation -eq 2 -and -not $s.secureBoot) 'Nesouhlasí profil UEFI.'
Require ($s.vcpus -ge 1 -and $s.memoryMiB -ge 256 -and $s.diskGiB -ge 2) 'Neplatná kapacita VM.'
Require ($s.mac -match '^[0-9A-Fa-f]{12}$') 'Neplatná MAC.'
Require ($s.storageDirectory -match '^[A-Za-z]:\\' -and $s.storageDirectory -notmatch '\.\.') 'Nepovolená cesta.'
Import-Module Hyper-V -ErrorAction Stop
$null = Get-VMHost -ErrorAction Stop
$switch = @(Get-VMSwitch -Name $s.switchName -ErrorAction Stop)
Require ($switch.Count -eq 1 -and $switch[0].Id.ToString() -eq $s.switchId) 'Nesouhlasí switch.'
$base = Canonical $s.storageDirectory
$root = Join-Path $base $s.name
$diskPath = Join-Path $root 'system.vhdx'
$media = Join-Path $base 'media'
Require ($Request.sha256 -match '^[a-f0-9]{64}$') 'Chybí hash média.'
$method = if ($s.deploymentMethod) { $s.deploymentMethod } else { 'image' }
Require ($method -in @('iso', 'image')) 'Metoda nasazení musí být iso nebo image.'
$extension = if ($method -eq 'iso') { '.iso' } else { '.vhdx' }
$mediaPath = Join-Path $media ($Request.sha256 + $extension)
$isoPath = if ($method -eq 'iso') { $mediaPath } else { $null }
$imagePath = if ($method -eq 'image') { $mediaPath } else { $null }
$ownerPath = Join-Path $root 'owner.json'
# Odmítáme přesměrování vlastního úložiště přes junction nebo symlink.
$ancestor = $base
while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
        Require (-not ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Úložiště je reparse point.'
    }
    $parent = Split-Path -Parent $ancestor
    if ($parent -eq $ancestor) { break }
    $ancestor = $parent
}
function FreshTarget {
    Require (-not (Get-VM -Name $s.name -ErrorAction SilentlyContinue)) 'VM už existuje; nepřepisujeme ji.'
    Require (-not (Test-Path -LiteralPath $root)) 'Adresář VM už existuje.'
    $duplicate = @(Get-VM | Get-VMNetworkAdapter | Where-Object { $_.MacAddress -eq $s.mac })
    Require ($duplicate.Count -eq 0) 'MAC už používá jiná VM.'
    $drive = Get-PSDrive -Name $base.Substring(0,1)
    Require ($drive.Free -ge ([int64]$s.diskGiB * 1GB + [int64]$Request.mediaBytes + 2GB)) 'Nedostatek místa.'
}
function OwnedVm {
    Require (Test-Path -LiteralPath $ownerPath) 'Chybí záznam vlastnictví VM.'
    $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
    $ownerMethod = if ($owner.method) { $owner.method } else { 'iso' }
    Require ($ownerMethod -eq $method) 'Metoda neodpovídá vytvořené VM; použij původní --method.'
    # Název isoSha256 v uložené identitě zachováváme i pro dříve vytvořené VM.
    Require ($owner.ownerId -eq $s.ownerId -and $owner.isoSha256 -eq $Request.sha256) 'Nesouhlasí vlastnictví nebo médium.'
    $vm = Get-VM -Name $s.name -ErrorAction Stop
    Require ($vm.Id.ToString() -eq $owner.vmId -and $vm.Notes -eq $s.ownerId) 'Nesouhlasí VM ID.'
    Require ($vm.Generation -eq 2 -and (Canonical $vm.Path).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) 'Nesouhlasí konfigurační adresář.'
    Require ($vm.ProcessorCount -eq $s.vcpus -and $vm.MemoryStartup -eq ([int64]$s.memoryMiB * 1MB)) 'Nesouhlasí CPU nebo RAM.'
    Require (-not (Get-VMMemory -VM $vm).DynamicMemoryEnabled) 'Nesouhlasí režim RAM.'
    Require ((Get-VMFirmware -VM $vm).SecureBoot.ToString() -eq 'Off') 'Secure Boot není vypnutý.'
    $disks = @(Get-VMHardDiskDrive -VM $vm)
    Require ($disks.Count -eq 1 -and (Canonical $disks[0].Path) -eq $diskPath) 'Nesouhlasí cílový VHDX.'
    $vhd = Get-VHD -Path $diskPath
    Require ($vhd.Size -eq ([int64]$s.diskGiB * 1GB) -and -not $vhd.ParentPath -and $vhd.VhdType.ToString() -eq 'Dynamic') 'Nesouhlasí typ nebo velikost VHDX.'
    $users = @(Get-VM | Get-VMHardDiskDrive | Where-Object { $_.Path -and (Canonical $_.Path) -eq $diskPath })
    Require ($users.Count -eq 1) 'Disk je sdílený jinou VM.'
    $nics = @(Get-VMNetworkAdapter -VM $vm)
    Require ($nics.Count -eq 1 -and $nics[0].MacAddress -eq $s.mac -and $nics[0].SwitchId.ToString() -eq $s.switchId) 'Nesouhlasí síť VM.'
    Require (@(Get-VMSnapshot -VM $vm).Count -eq 0) 'VM má snapshoty.'
    return $vm
}
function Snapshot($vm) {
    $nic = Get-VMNetworkAdapter -VM $vm
    $dvd = @(Get-VMDvdDrive -VM $vm)
    # Linux DMI používá BIOSGUID. To je jiné UUID než identifikátor Hyper-V VM.
    $settings = @(Get-CimInstance -Namespace root/virtualization/v2 -ClassName Msvm_VirtualSystemSettingData | Where-Object {
        $_.VirtualSystemIdentifier -eq $vm.Id.ToString() -and $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized'
    })
    Require ($settings.Count -eq 1) 'Nelze jednoznačně určit BIOS UUID VM.'
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
            switchName = $s.switchName; switchId = $s.switchId; mac = $s.mac; secureBoot = $false }
    }
    'prepare' {
        FreshTarget
        if (-not (Test-Path -LiteralPath $media)) { $null = New-Item -ItemType Directory -Path $media -Force }
        Require (-not ((Get-Item -LiteralPath $media).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Adresář médií je reparse point.'
        Require ($Request.token -match '^[a-f0-9]{32}$') 'Neplatný identifikátor uploadu.'
        $upload = Join-Path $media ($Request.token + '.upload')
        Require (-not (Test-Path -LiteralPath $upload)) 'Upload už existuje.'
        $cached = Test-Path -LiteralPath $mediaPath
        if ($cached) {
            Require ((Get-FileHash -LiteralPath $mediaPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Nesouhlasí hash uloženého média.'
        }
        [ordered]@{ uploadPath = $upload; cached = $cached }
    }
    'create' {
        FreshTarget
        if (-not (Test-Path -LiteralPath $mediaPath)) {
            Require ($Request.token -match '^[a-f0-9]{32}$') 'Neplatný identifikátor uploadu.'
            $upload = Join-Path $media ($Request.token + '.upload')
            Require ((Get-Item -LiteralPath $upload).Length -eq $Request.mediaBytes) 'Nesouhlasí délka uploadu.'
            Require ((Get-FileHash -LiteralPath $upload -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Nesouhlasí hash uploadu.'
            Move-Item -LiteralPath $upload -Destination $mediaPath
        }
        Require ((Get-FileHash -LiteralPath $mediaPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Nesouhlasí hash média.'
        $null = New-Item -ItemType Directory -Path $root
        if ($method -eq 'iso') {
            $null = New-VHD -Path $diskPath -SizeBytes ([int64]$s.diskGiB * 1GB) -Dynamic
        } else {
            Copy-Item -LiteralPath $imagePath -Destination $diskPath
            Require ((Get-FileHash -LiteralPath $diskPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Request.sha256) 'Nesouhlasí hash kopie systémového disku.'
        }
        $vm = New-VM -Name $s.name -Generation 2 -MemoryStartupBytes ([int64]$s.memoryMiB * 1MB) -VHDPath $diskPath -Path (Join-Path $root 'config') -SwitchName $s.switchName
        Set-VM -VM $vm -ProcessorCount $s.vcpus -AutomaticStartAction Nothing -AutomaticStopAction ShutDown -CheckpointType Disabled -Notes $s.ownerId
        Set-VMMemory -VM $vm -DynamicMemoryEnabled $false
        Set-VMNetworkAdapter -VM $vm -StaticMacAddress $s.mac
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
        Require ($method -eq 'image') 'Tato operace vyžaduje metodu image.'
        Require ($vm.State.ToString() -in @('Off', 'Running')) 'VM není připravená ke spuštění z disku.'
        Require (@(Get-VMDvdDrive -VM $vm | Where-Object { $_.Path }).Count -eq 0) 'Metoda image nesmí mít připojené instalační ISO.'
        if ($vm.State.ToString() -eq 'Off') { Start-VM -VM $vm }
        Snapshot (Get-VM -Id $vm.Id)
    }
    'boot-disk' {
        $vm = OwnedVm
        Require ($vm.State.ToString() -eq 'Off') 'VM musí být korektně vypnutá.'
        $dvd = @(Get-VMDvdDrive -VM $vm)
        Require ($dvd.Count -eq 1 -and (Canonical $dvd[0].Path) -eq $isoPath) 'Nesouhlasí instalační DVD.'
        Set-VMDvdDrive -VMDvdDrive $dvd[0] -Path $null
        Set-VMFirmware -VM $vm -BootOrder @(Get-VMHardDiskDrive -VM $vm)
        Start-VM -VM $vm
        Snapshot (Get-VM -Id $vm.Id)
    }
    default { throw 'Nepovolená operace.' }
}
$result | ConvertTo-Json -Depth 8 -Compress
