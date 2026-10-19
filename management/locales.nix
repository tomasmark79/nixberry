# Compile the source catalogue once; remote hosts do not need gettext.
{ pkgs }:
pkgs.runCommand "nixberry-locales" { nativeBuildInputs = [ pkgs.gettext ]; } ''
  mkdir -p "$out/cs/LC_MESSAGES"
  msgfmt --check --output-file="$out/cs/LC_MESSAGES/nixberry.mo" \
    ${./deployment/locales/cs/LC_MESSAGES/nixberry.po}
''
