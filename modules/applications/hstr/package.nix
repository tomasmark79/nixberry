# Stejný připnutý fork jako v Nixonu; recept je místní kvůli samostatnému exportu nixberry.
{
  hstr,
  fetchFromGitHub,
  runCommand,
  python3,
}:
let
  patched = hstr.overrideAttrs (old: {
    version = "3.2.0-unstable-2026-10-11";
    src = fetchFromGitHub {
      owner = "tomasmark79";
      repo = "hstr";
      rev = "9d726581716060ae6dba2214549b58b1d6dab188";
      hash = "sha256-HUd/d6OFlsTQzSqSB+YkqZYGLOGyk0B5urjYlVIVHgg=";
    };
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
