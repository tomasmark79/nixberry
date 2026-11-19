{
  hstr,
  hstrSource,
  lib,
  runCommand,
  python3,
}:
let
  inherit (hstrSource) lastModifiedDate;
  date = lib.concatStringsSep "-" [
    (builtins.substring 0 4 lastModifiedDate)
    (builtins.substring 4 2 lastModifiedDate)
    (builtins.substring 6 2 lastModifiedDate)
  ];
  patched = hstr.overrideAttrs (old: {
    version = "unstable-${date}";
    src = hstrSource.outPath;
    passthru = (old.passthru or { }) // {
      tests.scroll =
        runCommand "hstr-scroll-test"
          {
            nativeBuildInputs = [ (python3.withPackages (ps: [ ps.pyte ])) ];
          }
          ''
            python ${patched.src}/test/src/test_scroll.py ${patched}/bin/hstr
            touch $out
          '';
    };
  });
in
patched
