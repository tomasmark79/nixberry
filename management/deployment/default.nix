let
  hosts = import ../../hosts.nix;
  configurations = import ./configurations.nix;
  vmInstances = import ../../instances;
  instances = builtins.mapAttrs (
    name: instance:
    let
      offline = !(instance ? host);
      host =
        if offline then
          assert
            builtins.elem (instance.backend or "") [
              "libvirt"
              "hyperv"
              "proxmox"
              "virtualbox"
              "vmware"
            ]
            || throw "Instance ${name}: offline příprava vyžaduje podporovaný VM backend.";
          {
            backend = instance.backend;
            connection.transport = "offline";
          }
        else
          hosts.${instance.host} or (throw "Instance ${name}: hostitel ${instance.host} není deklarovaný.");
      expectedProfile =
        {
          libvirt = "nixberry-vm";
          hyperv = "nixberry-hyperv";
          proxmox = "nixberry-proxmox";
          virtualbox = "nixberry-virtualbox";
          vmware = "nixberry-vmware";
        }
        .${host.backend} or (throw "Instance ${name}: nepodporovaný backend ${host.backend}.");
    in
    assert
      builtins.isBool (instance.allowReset or false)
      || throw "Instance ${name}: allowReset musí být true nebo false v deklaraci VM.";
    assert
      (instance.profile or expectedProfile) == expectedProfile
      || throw "Instance ${name}: profil neodpovídá backendu ${host.backend}.";
    assert
      builtins.elem (instance.deploymentMethod or "image") [
        "iso"
        "image"
      ]
      || throw "Instance ${name}: deploymentMethod musí být iso nebo image.";
    assert
      (instance.access or null) == null
      || offline
      || throw "Instance ${name}: access podporuje offline VM bez správy hypervizoru.";
    assert builtins.all (key: !(builtins.hasAttr key instance.spec)) (
      builtins.attrNames host.connection
    );
    instance
    // {
      inherit offline;
      allowReset = instance.allowReset or false;
      configuration = configurations.select instance;
      deploymentMethod = instance.deploymentMethod or "image";
      capabilities = {
        build = true;
        export = true;
        system = !offline || (instance.access or null) != null;
        hypervisor = !offline;
      };
      host = instance.host or "offline:${name}";
      inherit (host) backend;
      profile = instance.profile or expectedProfile;
      guestHostName = instance.guestHostName or instance.spec.name;
      spec =
        (instance.deployment or { })
        // host.connection
        // instance.spec
        // (
          if host.backend == "libvirt" then
            {
              volume = instance.spec.volume or "${instance.spec.name}.qcow2";
              allowReset = instance.allowReset or false;
            }
          else
            { }
        );
    }
  ) vmInstances;
  rpiTargets = builtins.mapAttrs (name: target: {
    host = target.hostName;
    profile = name;
    backend = "rpi";
    configuration = configurations.select target;
    capabilities = {
      build = true;
      export = true;
      system = true;
      hypervisor = false;
    };
    spec = builtins.removeAttrs target [
      "adminKeyFile"
      "configuration"
    ];
  }) (import ../rpi/targets);
in
assert builtins.intersectAttrs instances rpiTargets == { };
{
  inherit hosts instances;
  # Jednotný seznam zahrnuje fyzické stroje i VM; údaje RPi se neduplikují.
  targets = instances // rpiTargets;
}
