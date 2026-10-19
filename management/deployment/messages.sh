# Společné hlášky pro místní i předávané Bash skripty. Žádný proces pro formátování.
_nb_emit() {
  local level=$1 template=$2 message prefix='[nixberry]' color
  shift 2
  printf -v message -- "$template" "$@"
  case $level in
    info) color=36 ;;
    success) color=32 ;;
    warning) color=33 ;;
    error) color=31 ;;
  esac
  if [[ ! ${NO_COLOR+x} ]] && { [[ ${NIXBERRY_COLOR:-auto} == always ]] ||
    { [[ ${NIXBERRY_COLOR:-auto} == auto && -t 2 && ${TERM:-} != dumb ]]; }; }; then
    printf '\033[%sm%s\033[0m %s\n' "$color" "$prefix" "$message" >&2
  else
    printf '%s %s\n' "$prefix" "$message" >&2
  fi
}
nb_info() { _nb_emit info "$@"; }
nb_success() { _nb_emit success "$@"; }
nb_warning() { _nb_emit warning "$@"; }
nb_error() { _nb_emit error "$@"; }
nb_help() { printf '%s\n' "$@"; }
