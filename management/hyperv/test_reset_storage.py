"""Run reset and installation ownership gates with fake Hyper-V cmdlets."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is unavailable")
class ResetStorageChecks(unittest.TestCase):
    def check_case(self, case, *, allowed=False, source_name="reset.ps1", method="iso"):
        source = str(Path(__file__).with_name(source_name)).replace("'", "''")
        script = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('SOURCE', [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}, $false)) {
    Invoke-Expression $function.Extent.Text
}
# File-system and hardware checks have separate coverage; simulate Windows paths
# so the actual reference gate can execute under PowerShell on Linux as well.
function CheckTree {}
function CheckAdapters($vm) {}
function Canonical([string]$path) { return $path.TrimEnd('\') }
$case = 'CASE'
$method = 'METHOD'
$Request = @{sha256='media-hash'}
$root = 'C:\owned'; $diskPath = 'C:\owned\system.vhdx'; $ownerPath = 'C:\owned\owner.json'
$s = @{ name='vm-demo-reset'; ownerId='owner'; vcpus=2; memoryMiB=1024; diskGiB=8 }
$target = [pscustomobject]@{ Id=[guid]'11111111-1111-1111-1111-111111111111'; Notes='owner'; State='Off'; Generation=2; Path='C:\owned\config'; ProcessorCount=2; MemoryStartup=1GB }
$peer = [pscustomobject]@{ Id=[guid]'22222222-2222-2222-2222-222222222222'; Path='C:\other\config' }
$script:writes = 0
$script:inspected = [Collections.Generic.List[string]]::new()
function Test-Path { $true }
function Get-Content { @{ownerId='owner'; vmId='11111111-1111-1111-1111-111111111111'; isoSha256='media-hash'; method=$method} | ConvertTo-Json }
function Get-VM { param($Name) if ($Name) { return $target }; return @($target, $peer) }
function Get-VMFirmware { [pscustomobject]@{SecureBoot='Off'} }
function Get-VMMemory { [pscustomobject]@{DynamicMemoryEnabled=$false} }
function Get-VMDvdDrive {}
function Get-VMSnapshot {
    param($VM)
    if ($VM.Id -eq $peer.Id -and $case -eq 'checkpoint') { [pscustomobject]@{Name='saved'} }
}
function Get-VMHardDiskDrive {
    param([Parameter(ValueFromPipeline=$true)]$VM, $VMSnapshot)
    process {
        $path = if ($VM -and $VM.Id -eq $target.Id) { $diskPath }
            elseif ($VMSnapshot -or $case -ne 'checkpoint') { 'C:\other\child.avhdx' }
            else { 'C:\other\unrelated.vhdx' }
        [pscustomobject]@{Path=$path}
    }
}
function Get-VHD {
    param($Path)
    $script:inspected.Add($Path)
    if ($Path -eq $diskPath -or $Path -eq 'C:\other\unrelated.vhdx') {
        return [pscustomobject]@{Path=$Path; Size=8GB; VhdType='Dynamic'; ParentPath=$null}
    }
    if ($case -eq 'unreadable') { throw 'Cannot inspect parent image' }
    $parent = switch ($case) {
        'unrelated' { 'C:\other\unrelated.vhdx' }
        'cycle' { 'C:\other\child.avhdx' }
        'relative' { '..\owned\system.vhdx' }
        'indirect' {
            if ($Path -eq 'C:\other\child.avhdx') { 'C:\other\middle.vhdx' } else { $diskPath }
        }
        default { $diskPath }
    }
    [pscustomobject]@{Path=$Path; Size=8GB; VhdType='Differencing'; ParentPath=$parent}
}
function Remove-VM { $script:writes++ }
$failure = $null
try { $vm = OwnedVm; Remove-VM -VM $vm } catch { $failure = $_.Exception.Message }
@{writes=$script:writes; failure=$failure; inspected=@($script:inspected)} | ConvertTo-Json -Compress
'''.replace("SOURCE", source).replace("CASE", case).replace("METHOD", method)
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["writes"], int(allowed), observed)
        self.assertEqual(observed["failure"] is None, allowed, observed)
        return observed

    def test_peer_differencing_disk_and_checkpoint_protect_parent(self):
        for case in ("direct", "indirect", "checkpoint"):
            with self.subTest(case=case):
                observed = self.check_case(case)
                self.assertIn("Another VM uses a file", observed["failure"])

    def test_unreadable_or_cyclic_disk_chain_refuses_removal(self):
        for case in ("unreadable", "cycle", "relative"):
            with self.subTest(case=case):
                self.check_case(case)

    def test_unrelated_parent_chain_allows_owned_vm_removal(self):
        self.check_case("unrelated", allowed=True)

    def test_image_and_iso_ownership_gate_rejects_shared_backing_disks(self):
        for method in ("image", "iso"):
            for case in ("direct", "indirect", "checkpoint", "unreadable", "cycle", "relative"):
                with self.subTest(method=method, case=case):
                    observed = self.check_case(case, source_name="host.ps1", method=method)
                    if case in ("direct", "indirect", "checkpoint"):
                        self.assertIn("The disk is shared with another VM.", observed["failure"])

    def test_image_and_iso_allow_unrelated_backing_disks(self):
        for method in ("image", "iso"):
            with self.subTest(method=method):
                self.check_case("unrelated", allowed=True, source_name="host.ps1", method=method)
