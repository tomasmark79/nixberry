# Read-only. Never use host names, paths or options as commands.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$os = Get-CimInstance Win32_OperatingSystem
$hyperv = [bool](Get-Module -ListAvailable -Name Hyper-V)
$result = [ordered]@{
    computerName = $env:COMPUTERNAME
    user = $identity.Name
    elevatedAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    os = $os.Caption
    osVersion = $os.Version
    powerShell = $PSVersionTable.PSVersion.ToString()
    hypervModule = $hyperv
    drives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
        [ordered]@{ path = $_.DeviceID; sizeBytes = $_.Size; freeBytes = $_.FreeSpace }
    })
    hostSettings = $null
    switches = @()
    vms = @()
    images = @()
    inventoryErrors = @()
    hypervError = $null
}
if ($hyperv) {
    try {
        Import-Module Hyper-V -ErrorAction Stop
        $hostInfo = Get-VMHost -ErrorAction Stop
        $result.hostSettings = [ordered]@{
            virtualMachinePath = $hostInfo.VirtualMachinePath
            virtualHardDiskPath = $hostInfo.VirtualHardDiskPath
            logicalProcessors = $hostInfo.LogicalProcessorCount
            memoryCapacityBytes = $hostInfo.MemoryCapacity
        }
        $result.switches = @(Get-VMSwitch | ForEach-Object {
            [ordered]@{ name = $_.Name; id = $_.Id.ToString(); type = $_.SwitchType.ToString() }
        })
        $result.vms = @(Get-VM | ForEach-Object {
            $vm = $_
            $diskPaths = @(Get-VMHardDiskDrive -VM $vm | ForEach-Object { $_.Path })
            $checkpointPaths = @(Get-VMSnapshot -VM $vm | ForEach-Object {
                Get-VMHardDiskDrive -VMSnapshot $_ | ForEach-Object { $_.Path }
            })
            $allocated = 0L
            $allocationComplete = $true
            $allDisks = @{}
            foreach ($diskPath in @($diskPaths + $checkpointPaths | Select-Object -Unique)) {
                try {
                    $seen = @{}
                    $path = $diskPath
                    while ($path) {
        if ($seen.ContainsKey($path)) { throw 'A backing disk cycle was detected.' }
                        $seen[$path] = $true
                        $vhd = Get-VHD -Path $path -ErrorAction Stop
                        if (-not $allDisks.ContainsKey($path)) {
                            $allocated += $vhd.FileSize
                            $allDisks[$path] = $true
                        }
                        $result.images += [ordered]@{
                            path = $path; logicalBytes = $vhd.Size; allocatedBytes = $vhd.FileSize
                            classification = 'protected'; protectedBy = @('VM/checkpoint:' + $vm.Name)
                            parentPath = $vhd.ParentPath
                        }
                        $path = $vhd.ParentPath
                    }
                } catch {
                    $allocationComplete = $false
                    $result.inventoryErrors += $diskPath + ': ' + $_.Exception.Message
                }
            }
            if (-not $allocationComplete) { $allocated = $null }
            [ordered]@{
                name = $vm.Name
                id = $vm.Id.ToString()
                ownerId = $vm.Notes
                state = $vm.State.ToString()
                generation = $vm.Generation
                path = $vm.Path
                disks = $diskPaths
                checkpointDisks = $checkpointPaths
                allocatedBytes = $allocated
                classification = 'protected'
                network = @(Get-VMNetworkAdapter -VM $vm | ForEach-Object {
                    [ordered]@{ switchName = $_.SwitchName; mac = $_.MacAddress; ips = @($_.IPAddresses) }
                })
            }
        })
    } catch {
        $result.hypervError = $_.Exception.Message
    }
}
$result | ConvertTo-Json -Depth 8 -Compress
