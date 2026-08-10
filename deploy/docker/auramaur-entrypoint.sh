#!/bin/sh
set -eu

mkdir -p "${AURAMAUR_STATE_DIR:-/app/state}" "${AURAMAUR_LOG_DIR:-/app/logs}"

if [ -f "${AURAMAUR_KILL_SWITCH_PATH:-/app/state/KILL_SWITCH}" ]; then
    echo "KILL_SWITCH present; refusing to start Auramaur" >&2
    exit 0
fi

runtime_uid=${AURAMAUR_RUNTIME_UID:-1000}
runtime_gid=${AURAMAUR_RUNTIME_GID:-1000}
groupmod -o -g "$runtime_gid" auramaur
usermod -o -u "$runtime_uid" -g "$runtime_gid" auramaur

# Claude stores its primary config beside ~/.claude, while Compose persists
# only the ~/.claude directory. Restore the CLI-managed backup after a
# container recreation so model routes do not fail solely because the
# ephemeral sibling file disappeared.
claude_config=/home/auramaur/.claude.json
if [ ! -s "$claude_config" ]; then
    claude_backup=
    for candidate in /home/auramaur/.claude/backups/.claude.json.backup.*; do
        if [ -s "$candidate" ]; then
            claude_backup=$candidate
        fi
    done
    if [ -n "$claude_backup" ]; then
        cp "$claude_backup" "$claude_config"
        chown "$runtime_uid:$runtime_gid" "$claude_config"
        chmod 600 "$claude_config"
        echo "Restored Claude config from persisted backup"
    fi
fi

for dir in "${AURAMAUR_STATE_DIR:-/app/state}" "${AURAMAUR_LOG_DIR:-/app/logs}" "$HOME/.claude"; do
    if ! gosu auramaur test -w "$dir"; then
        echo "Runtime path is not writable by host UID $runtime_uid: $dir" >&2
        echo "Set AURAMAUR_UID/AURAMAUR_GID to the host owner and re-run." >&2
        exit 1
    fi
done

exec gosu auramaur "$@"
