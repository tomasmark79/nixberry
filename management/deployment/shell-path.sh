# Shared recipe selection for launchers, editors and completion; local read-only access.
source "${BASH_SOURCE[0]%/*}/messages.sh"

_nixberry_recipe_dir() (
  local recipe
  if [[ -n ${NIXBERRY_RECIPE:-} ]]; then
    recipe=$NIXBERRY_RECIPE
  else
    recipe=$1
  fi
  if [[ ! -f $recipe/flake.nix || ! -d $recipe/instances || ! -d $recipe/management/rpi/targets ]]; then
    nb_error 'The NixBerry recipe is incomplete or unavailable: %s' "$recipe"
    return 1
  fi
  cd -- "$recipe" && pwd -P
)
