# Fast status for selected VMs without scanning disks, snapshots or host storage.
$ErrorActionPreference = 'Stop'
$hyperv = [bool](Get-Module -ListAvailable -Name Hyper-V)
$result = [ordered]@{
    computerName = $env:COMPUTERNAME
    hypervModule = $hyperv
    hypervError = $null
    vms = @()
}
if ($hyperv) {
    try {
        Import-Module Hyper-V -ErrorAction Stop
        $result.vms = @(Get-VM | Where-Object { $names -contains $_.Name } | ForEach-Object {
            $vm = $_
            [ordered]@{
                name = $vm.Name
                ownerId = $vm.Notes
                state = $vm.State.ToString()
                generation = $vm.Generation
                network = @(Get-VMNetworkAdapter -VM $vm | ForEach-Object {
                    [ordered]@{ switchName = $_.SwitchName; switchId = $_.SwitchId.ToString(); mac = $_.MacAddress; ips = @($_.IPAddresses) }
                })
            }
        })
    } catch {
        $result.hypervError = $_.Exception.Message
    }
}
$result | ConvertTo-Json -Depth 8 -Compress
