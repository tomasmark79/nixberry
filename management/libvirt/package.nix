# Manifest, XML and management commands use the same pinned inputs.
{
  pkgs,
  installerIso,
  isoName,
  targetSystem,
  diskoScript,
  spec,
  guestHostName ? "nixberry-vm",
  image ? null,
  deploymentMethod ? "image",
  allowReset ? spec.allowReset or false,
  security ? { mode = "none"; },
  securityTarget ? null,
}:
let
  commonSource = import ../messages.nix { inherit pkgs; };
  lib = pkgs.lib;
  escape = lib.escapeXML;
  isoPath = "${installerIso}/iso/${isoName}";
  firmware = pkgs.OVMF.fd;
  diskPath = "${spec.storageDirectory}/${spec.volume}";
  manifest = pkgs.writeText "nixberry-libvirt-manifest.json" (
    builtins.toJSON (
      spec
      // lib.optionalAttrs (security.mode != "none") { inherit security securityTarget; }
      // {
        inherit diskPath deploymentMethod;
        isoPath = if deploymentMethod == "iso" then isoPath else null;
        imagePath = if deploymentMethod == "image" then "${image}/system.qcow2" else null;
        inherit guestHostName;
        systemPath = toString targetSystem;
        diskoPath = toString diskoScript;
      }
    )
  );
  domainXml = pkgs.writeText "nixberry-libvirt-domain.xml" ''
    <domain type="kvm">
      <name>${escape spec.name}</name>
      <uuid>${spec.uuid}</uuid>
      ${lib.optionalString (spec ? ownerId)
        ''<metadata><nixberry:owner xmlns:nixberry="urn:nixberry:deployment" id="${escape spec.ownerId}"/></metadata>''
      }
      <memory unit="MiB">${toString spec.memoryMiB}</memory>
      <vcpu placement="static">${toString spec.vcpus}</vcpu>
      <os>
        <type arch="x86_64" machine="${spec.machine}">hvm</type>
        <loader readonly="yes" type="pflash">${firmware}/FV/OVMF_CODE.fd</loader>
        <nvram template="${firmware}/FV/OVMF_VARS.fd"/>
      </os>
      <features><acpi/><apic/></features>
      <cpu mode="host-model" check="partial"/>
      <clock offset="utc"/>
      <on_poweroff>destroy</on_poweroff>
      <on_reboot>restart</on_reboot>
      <on_crash>destroy</on_crash>
      <devices>
        <disk type="file" device="disk">
          <driver name="qemu" type="qcow2"/>
          <source file="${escape diskPath}"/>
          <target dev="vda" bus="virtio"/>
          <boot order="${if deploymentMethod == "iso" then "2" else "1"}"/>
        </disk>
        ${lib.optionalString (deploymentMethod == "iso") ''
          <disk type="file" device="cdrom">
                    <driver name="qemu" type="raw"/>
                    <source file="${escape isoPath}"/>
                    <target dev="sda" bus="sata"/>
                    <readonly/>
                    <boot order="1"/>
                  </disk>''}
        <controller type="sata" index="0"/>
        <interface type="network">
          <mac address="${spec.mac}"/>
          <source network="${escape spec.network}"/>
          <model type="virtio"/>
        </interface>
        <serial type="pty"><target port="0"/></serial>
        <console type="pty"><target type="serial" port="0"/></console>
        <channel type="unix">
          <target type="virtio" name="org.qemu.guest_agent.0"/>
        </channel>
        <rng model="virtio"><backend model="random">/dev/urandom</backend></rng>
        <video><model type="none"/></video>
      </devices>
    </domain>
  '';
  create = pkgs.writeShellApplication {
    name = "nixberry-vm-create";
    runtimeInputs = with pkgs; [
      bash
      coreutils
      gnugrep
      jq
      libvirt
      libxml2
      python3
    ];
    text = ''
      export NIXBERRY_MANIFEST=${manifest}
      export NIXBERRY_DOMAIN_XML=${domainXml}
      export NIXBERRY_POLICY=${./policy.py}
      export NIXBERRY_MESSAGES_SH=${commonSource}/messages.sh
      export PYTHONPATH=${commonSource}
      exec python3 ${commonSource}/shell_runner.py ${./create.sh} "$@"
    '';
  };
  install = pkgs.writeShellApplication {
    name = "nixberry-vm-install";
    runtimeInputs = with pkgs; [
      python3
      libvirt
      openssh
    ];
    text = ''
      export NIXBERRY_MANIFEST=${manifest}
      export NIXBERRY_GUEST_INSTALL=${./guest-install.sh}
      export PYTHONPATH=${commonSource}:${
        lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./policy.py
            ./storage.py
          ];
        }
      }
      exec python3 ${./install.py} "$@"
    '';
  };
  reset = import ../deployment/reset-package.nix {
    inherit pkgs;
    commandName = "nixberry-vm-reset";
    target = {
      inherit spec allowReset;
      backend = "libvirt";
    };
  };
  probe = import ./probe-package.nix { inherit pkgs spec; };
  provision = pkgs.writeShellApplication {
    name = "nixberry-vm-provision";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      export NIXBERRY_CREATE=${create}/bin/nixberry-vm-create
      export NIXBERRY_INSTALL=${install}/bin/nixberry-vm-install
      export PYTHONPATH=${commonSource}
      exec python3 ${./provision.py} "$@"
    '';
  };
in
pkgs.symlinkJoin {
  name = "nixberry-libvirt-tools";
  paths = [
    create
    probe
    install
    reset
    provision
  ];
  passthru = {
    inherit
      manifest
      domainXml
      installerIso
      reset
      probe
      ;
  };
}
