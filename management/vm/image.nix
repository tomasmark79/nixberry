# Prebuilt disk for transfer and first boot without installation ISO or internet.
{ ... }:
{
  disko.imageBuilder.imageFormat = "qcow2";
  disko.imageBuilder.copyNixStore = true;
  disko.memSize = 4096;
}
