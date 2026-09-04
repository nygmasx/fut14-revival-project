#!/bin/sh
# Lance le serveur du revival. Aucune dépendance : Python 3.10+ nu suffit.
#
# La configuration vient de fifa14revival.ini (voir fifa14revival.example.ini).
# `server.host` doit être l'adresse que la CONSOLE joindra -- sur un VPS, son
# IP publique. Ce n'est pas `auto` ici : `auto` résout l'adresse LAN de la
# machine, ce qui est juste sur un Mac de bureau et faux sur un VPS derrière
# une IP publique.
set -eu

# The package root is wherever `server/` lives, found by walking up from this
# script. So run.sh works whether it sits at the root or under deploy/, and a
# systemd ExecStart can point at either.
HERE=$(cd "$(dirname "$0")" && pwd)
while [ "$HERE" != "/" ] && [ ! -d "$HERE/server" ]; do
    HERE=$(dirname "$HERE")
done
if [ ! -d "$HERE/server" ]; then
    echo "run.sh: introuvable -- pas de dossier server/ au-dessus de $0" >&2
    exit 1
fi
PY=${PYTHON:-python3}

conf() { "$PY" "$HERE/tools/revival_config.py" "$1" 2>/dev/null; }

HOST=$(conf server.host)
CORE=$(conf server.core_port)
IDENT=$(conf server.identity_port)

if [ -z "$HOST" ] || [ "$HOST" = "auto" ]; then
    echo "fifa14revival.ini: server.host doit être l'IP publique du serveur," >&2
    echo "pas 'auto' -- la console doit pouvoir la joindre." >&2
    exit 1
fi

mkdir -p "$HERE/runtime/clubs"

# 8094 is EAS FC's Blaze port and 8080 its catalogue (the latter is on by
# default via --identity-extra-ports). The console's connect hook redirects
# both by port, so something has to answer there or the redirect lands on a
# closed door. Keep this list in step with LOCAL_PLAINTEXT_PORTS in
# tools/fifa14_connect_redirect.py.

# The seasons and cups list only appear when a mode is set; without this the
# client reads "Les saisons ne sont pas disponibles". It is set here rather
# than left to be remembered.
#
# `native` was pinned here while the code default was something else, which is
# how three branches came to be eliminated on the grounds that they served
# identical documents -- true, and useless, because they shared a default that
# this line was overriding. The code default is `ac` now, and this line no
# longer disagrees with it.
#
# Set FIFA14_SEASON_MODE=native to go back if the screen freezes.
export FIFA14_SEASON_MODE="${FIFA14_SEASON_MODE:-ac}"

exec "$PY" "$HERE/server/fifa14_blaze_server.py" \
    --listen 0.0.0.0 \
    --advertise "$HOST" \
    --core-port "$CORE" \
    --identity-port "$IDENT" \
    --ports "$CORE",42124,42126,42127,8094 \
    --journal "$HERE/runtime/blaze-server.jsonl" \
    --account-state "$HERE/runtime/local-account.json"
