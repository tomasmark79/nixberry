# Shared local and transferred Bash messages. Formatting spawns no process.
_nb_emit() {
  local level=$1 template=$2 message prefix='[nixberry]' color REPLY
  shift 2
  _nb_translate "$template"
  template=$REPLY
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
nb_help() {
  local value REPLY
  for value in "$@"; do
    _nb_translate "$value"
    printf '%s\n' "$REPLY"
  done
}

# Pure Bash lookup for local launchers. Transferred scripts are translated locally.
_nb_translate() { REPLY=$1; }
if [[ ${NIXBERRY_LANG:-en} == cs && ${BASH_SOURCE[0]:-} && -r ${BASH_SOURCE[0]%/*}/locales/cs.sh ]]; then
  source "${BASH_SOURCE[0]%/*}/locales/cs.sh"
fi
