# Hotový disk pro přenos a první start bez instalačního ISO nebo internetu.
{ ... }:
{
  disko.imageBuilder.imageFormat = "qcow2";
  disko.imageBuilder.copyNixStore = true;
  disko.memSize = 4096;
}
