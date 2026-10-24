# Source this file; resolve the recipe independently of the working directory.
_nixberry_completion_recipe=${BASH_SOURCE[0]}
if [[ $_nixberry_completion_recipe == */* ]]; then
    _nixberry_completion_recipe=${_nixberry_completion_recipe%/*}
else
    _nixberry_completion_recipe=.
fi
_nixberry_completion_recipe=$(cd -- "$_nixberry_completion_recipe" && pwd -P)
source "$_nixberry_completion_recipe/management/deployment/shell-path.sh"

_nixberry_complete() {
    local current=${COMP_WORDS[COMP_CWORD]} action= target= word file name
    local skip_value=0 i
    local recipe
    COMPREPLY=()
    recipe=$(_nixberry_recipe_dir "$_nixberry_completion_recipe" 2>/dev/null) || return 0
    local -a actions=(
        list show status ssh probe plan create install provision reset
        new-libvirt-vm new-hyperv-vm new-proxmox-vm new-virtualbox-vm new-vmware-vm
        reproduce verify update diff diffg export doctor cleanup prepare-image unlock
    )
    local -a options=(--help --version)
    local -A used=()

    # Skip option values; offer no local completions after an SSH command separator.
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if ((skip_value)); then
            skip_value=0
            continue
        fi
        case $word in
            --) return 0 ;;
            --host|--ssh|--output|--address|--artifact-root|--method|--configuration|--security|--key-file)
                used[$word]=1; skip_value=1; continue ;;
            -*) used[${word%%=*}]=1; continue ;;
        esac
        if [[ -z $action ]]; then
            action=$word
            [[ $action == new-*-vm ]] && action=${action%-vm}
        elif [[ -z $target ]]; then
            target=$word
        else
            return 0
        fi
    done
    if ((skip_value)); then
        if [[ $word == --host && $action == new-* && -f $recipe/hosts.nix ]]; then
            local hosts_file=$recipe/hosts.nix
            [[ -f $recipe/hosts.local.nix ]] && hosts_file=$recipe/hosts.local.nix
            local backend=${action#new-} line host=
            local host_pattern='^[[:space:]]*([a-z][a-z0-9-]*)[[:space:]]*=[[:space:]]*\{'
            local backend_pattern='^[[:space:]]*backend[[:space:]]*=[[:space:]]*"([a-z]+)"[[:space:]]*;'
            # Read host declarations directly, without Nix or network access.
            while IFS= read -r line || [[ -n $line ]]; do
                if [[ $line =~ $host_pattern ]]; then
                    host=${BASH_REMATCH[1]}
                    line=${line#*\{}
                fi
                if [[ -n $host && $line =~ $backend_pattern ]]; then
                    if [[ ${BASH_REMATCH[1]} == "$backend" && $host == "$current"* ]]; then
                        COMPREPLY+=("$host")
                    fi
                    host=
                fi
            done < "$hosts_file"
        elif [[ $word == --security ]]; then
            for name in none luks confidential; do
                [[ $name == "$current"* ]] && COMPREPLY+=("$name")
            done
        elif [[ $word == --method ]]; then
            for name in iso image; do
                [[ $name == "$current"* ]] && COMPREPLY+=("$name")
            done
        elif [[ $word == --configuration ]]; then
            local -A configurations=()
            for file in "$recipe"/configurations/*.nix "$recipe"/configurations/*/default.nix; do
                [[ -f $file && ! -L $file ]] || continue
                name=${file##*/}
                if [[ $file == "$recipe"/configurations/*/default.nix ]]; then
                    file=${file%/default.nix}
                    name=${file##*/}
                else
                    name=${name%.nix}
                fi
                [[ $name =~ ^[a-z][a-z0-9-]*$ && $name == "$current"* && ! ${configurations[$name]-} ]] || continue
                configurations[$name]=1
                COMPREPLY+=("$name")
            done
        fi
        return 0
    fi
    if [[ $current == -* ]]; then
        case $action in
            list|show|status|verify|export|doctor|cleanup|prepare-image) options+=(--json) ;;
        esac
        case $action in
            update|create|install|provision|reset|reproduce) options+=(--apply --dry-run) ;;
            new-*|plan|diff|diffg|export|prepare-image|unlock) options+=(--dry-run) ;;
        esac
        case $action in
            new-*|show|plan|create|install|provision|reproduce|export) options+=(--method) ;;
        esac
        case $action in
            new-*) options+=(--host --offline --configuration --security) ;;
            update|ssh|verify|status|doctor|diff|diffg) options+=(--host --ssh) ;;
            export) options+=(--output) ;;
            prepare-image) options+=(--output --key-file) ;;
            unlock) options+=(--address) ;;
            cleanup) options+=(--artifact-root) ;;
        esac
        if [[ $action == plan || $action == reproduce ]] && [[ -n $target ]] &&
            [[ -f $recipe/management/rpi/targets/$target.nix ]]; then
            options+=(--address --erase-existing)
        fi
        for word in "${options[@]}"; do
            [[ $word == --method && -n $target && -f $recipe/management/rpi/targets/$target.nix ]] && continue
            # Only image roots are repeatable; do not offer mutually exclusive options.
            [[ $word != --artifact-root && ${used[$word]-} ]] && continue
            case $word in
                --apply) [[ ${used[--dry-run]-} ]] && continue ;;
                --dry-run) [[ ${used[--apply]-} ]] && continue ;;
                --host) [[ ${used[--ssh]-} || ${used[--offline]-} ]] && continue ;;
                --offline) [[ ${used[--host]-} ]] && continue ;;
                --ssh) [[ ${used[--host]-} ]] && continue ;;
            esac
            [[ $word == "$current"* ]] && COMPREPLY+=("$word")
        done
        return 0
    fi

    [[ -n $target ]] && return 0

    if [[ -z $action ]]; then
        for word in "${actions[@]}"; do
            [[ $word == "$current"* ]] && COMPREPLY+=("$word")
        done
    else
        case $action in
            new-*) return 0 ;;
        esac
        case $action in
            list|show|export|status|doctor) ;;
            *)
                # Read capabilities through Nix rather than parsing declarations with regex.
                # Without Nix or with invalid declarations, do not guess target capabilities.
                [[ -f $recipe/management/deployment/completion-targets.nix ]] || return 0
                command -v nix >/dev/null 2>&1 || return 0
                local direct=0
                [[ ${used[--host]-} || ${used[--ssh]-} ]] && direct=1
                while IFS= read -r name || [[ -n $name ]]; do
                    [[ -n $name && $name == "$current"* ]] && COMPREPLY+=("$name")
                done < <(NIXBERRY_COMPLETION_ACTION="$action" NIXBERRY_COMPLETION_DIRECT="$direct" \
                    nix eval --offline --impure --raw --option allow-import-from-derivation false \
                    --file "$recipe/management/deployment/completion-targets.nix" 2>/dev/null)
                return 0 ;;
        esac
        # Only regular declarations, matching Nix loaders; never cache this inventory.
        for file in "$recipe"/instances/*.nix "$recipe"/management/rpi/targets/*.nix; do
            [[ -f $file && ! -L $file ]] || continue
            name=${file##*/}
            [[ $name == default.nix ]] && continue
            name=${name%.nix}
            [[ $name == "$current"* ]] && COMPREPLY+=("$name")
        done
    fi
    return 0
}

complete -F _nixberry_complete nixberry

# Complete aliases as real CLI commands, including their implicit arguments.
_nixberry_complete_shortcut() {
    local -a original_words=("${COMP_WORDS[@]}") prefix=(nixberry)
    local original_cword=$COMP_CWORD
    case ${original_words[0]} in
        nvdiffberry) prefix+=(diff) ;;
        nvdiffberryg) prefix+=(diffg) ;;
        nixrebuildberry) prefix+=(update --apply) ;;
        nixberry-switch) prefix+=(update nixberry --apply) ;;
        nixberryedit)
            COMPREPLY=()
            ((original_cword == 1)) || return 0
            if [[ ${original_words[original_cword]} == -* ]]; then
                [[ --help == "${original_words[original_cword]}"* ]] && COMPREPLY=(--help)
                return 0
            fi
            prefix+=(show) ;;
        *) return 0 ;;
    esac
    local -a COMP_WORDS=("${prefix[@]}" "${original_words[@]:1}")
    local COMP_CWORD=$((original_cword + ${#prefix[@]} - 1))
    _nixberry_complete
}

complete -F _nixberry_complete_shortcut nvdiffberry nvdiffberryg nixrebuildberry nixberry-switch nixberryedit
complete -W '--help' nixberrycfg
