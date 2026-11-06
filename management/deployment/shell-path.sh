# Shared recipe selection for launchers, editors and completion; local read-only access.
source "${BASH_SOURCE[0]%/*}/messages.sh"

_nixberry_recipe_dir() (
  local recipe
  if [[ -n ${NIXBERRY_RECIPE:-} ]]; then
    recipe=$NIXBERRY_RECIPE
  else
    recipe=$1
  fi
  if [[ ! -f $recipe/flake.nix || ! -d $recipe/machines/vm || ! -d $recipe/machines/rpi ]]; then
    nb_error 'The NixBerry recipe is incomplete or unavailable: %s' "$recipe"
    return 1
  fi
  cd -- "$recipe" && pwd -P
)

_nixberry_recipe_uri() (
  local LC_ALL=C path=$1 character encoded index
  printf 'path:'
  for ((index = 0; index < ${#path}; index++)); do
    character=${path:index:1}
    case $character in
      [a-zA-Z0-9.~_/-]) printf '%s' "$character" ;;
      *) printf -v encoded '%%%02X' "'$character"; printf '%s' "$encoded" ;;
    esac
  done
)
