#!/bin/zsh
# Start FIFA 14 FUT, end to end, without anyone driving it.
#
#   tools/fut.sh            the whole thing: server, launch, patch, ready
#   tools/fut.sh --patch    apply the menu patch only (title already up)
#   tools/fut.sh --server   restart the server only
#
# Nothing to type in the middle of it. From a console on the dashboard to a
# patched title takes about half a minute.
#
# Entering FUT needs two runtime patches and they are not interchangeable:
#
#   1. at launch      hostnames, plaintext redirector, native FUT-resource
#                     redirect -- applied while the title boots
#   2. once the       three TU3 helperFunctions continuation branches
#      APT is in       -- and it has to land *before* Ultimate Team is picked
#      memory
#
# Applied from inside the FUT loader the second one does nothing: the launcher
# has already been walked past, the trace stops at accountinfo, /ut/auth never
# follows and CardsDLL is never mapped. That ordering is the whole reason this
# script exists.
#
# It also clears the account state and restarts the Blaze server first, because
# the title rewrites that state from its in-memory session within seconds of
# the file being cleared -- so re-entering FUT without a relaunch cannot work.
set -u

REPO=${REPO:-~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival}
PY="$REPO/.venv/bin/python"

cd "$REPO" || { print -u2 "repo introuvable: $REPO"; exit 1 }

# Where the server is, and which console to drive, come from
# `fifa14revival.ini` -- see `tools/revival_config.py` for the format and
# `docs/RELEASE.md` for the other reader it is meant to have: a Dashlaunch
# plugin on the console, which is what removes this machine from the picture
# entirely. Settling the format here means it gets exercised on every launch
# rather than invented later on paper.
#
# No file is needed. Every key falls back to what this script used to hardcode.
#
# The environment still wins over both: `MAC=... XBOX=... tools/fut.sh` is how
# every note in this repo says to override, and it keeps working.
#
# `server.host` resolves `auto` to this machine's LAN address. That was a zsh
# function here and is Python now, because the plugin and a self-hosted server
# on Linux need the same answer -- and because asking the routing table beats
# walking a list of interface names: the address here lives on en1, not en0.
config() { "$PY" tools/revival_config.py "$1" 2>/dev/null }

XBOX=${XBOX:-$(config console.address)}
MAC=${MAC:-$(config server.host)}
TITLE=${TITLE:-$(config console.title)}
CORE_PORT=${CORE_PORT:-$(config server.core_port)}
IDENTITY_PORT=${IDENTITY_PORT:-$(config server.identity_port)}

[[ -n $MAC && -n $XBOX ]] || { print -u2 "configuration illisible -- voir fifa14revival.example.ini"; exit 1 }

step() { print "\n== $1" }
fail() { print -u2 "\n!! $1"; exit 1 }

# 8094 is EAS FC's Blaze session port, and 8080 (the identity service's own
# extra listener, on by default) its catalogue. The connect hook redirects both
# by *port* now rather than relying on the endpoint strings being rewritten in
# time -- on 20 August those strings were read back from a running title,
# perfectly in place, while the server had still never seen one connection from
# that module. Redirecting by port does not depend on which endpoint the module
# kept. If nothing ever arrives on either, the module does not call connect at
# all, which is worth knowing for certain.
start_server() {
    step "serveur"
    print '{}' > runtime/local-account.json
    pkill -f "server/fifa14_blaze_server.py" 2>/dev/null
    pkill -f "fut-patch-watch" 2>/dev/null
    sleep 2
    local journal="runtime/live-easw-$(date +%Y%m%d-%H%M%S).jsonl"
    nohup "$PY" server/fifa14_blaze_server.py \
        --listen 0.0.0.0 --advertise "$MAC" \
        --core-port "$CORE_PORT" --identity-port "$IDENTITY_PORT" \
        --ports "$CORE_PORT",42124,42126,42127,8094 \
        --journal "$journal" \
        --account-state runtime/local-account.json \
        >> runtime/server.log 2>&1 &
    sleep 4
    if ! pgrep -f "server/fifa14_blaze_server.py" >/dev/null; then
        fail "le serveur n'a pas démarré -- voir runtime/server.log"
    fi
    print "   démarré, journal $journal"
}

# magicboot behaves differently depending on what is already running:
#
#   from the dashboard   FIFA starts
#   with FIFA running    the console reboots to the dashboard instead
#
# So launching on top of a running title -- which is exactly what you reach for
# when the FUT frontend has hung -- costs a reboot and lands nowhere. Worse,
# the reboot drops XBDM mid-command, the launcher reports "XBDM closed the
# connection", and that reads as a dead console when it is a healthy one twelve
# seconds from the dashboard.
running_title() {
    "$PY" - "$XBOX" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, "tools")
from fifa14_plain_send_hook import Xbdm
try:
    client = Xbdm(sys.argv[1])
    print(client.multiline("xbeinfo running")[1])
    client.close()
except Exception:
    print("")
PY
}

# Bare `magicboot`, which returns to the dashboard. Not `magicboot cold`: that
# took this console off the network entirely on 12 August and it needed the
# power button.
reboot_to_dashboard() {
    "$PY" -c "
import socket, sys
try:
    s = socket.create_connection(('$XBOX', 730), timeout=8)
    s.recv(256)
    s.sendall(b'magicboot\r\n')
    s.close()
except Exception:
    pass
" >/dev/null 2>&1
    local waited=0
    while [ $waited -lt 200 ]; do
        sleep 5
        waited=$((waited + 5))
        case "$(running_title)" in
            *dash.xex*) return 0 ;;
        esac
    done
    print -u2 "   le dashboard n'est pas revenu"
    return 1
}

launch_title() {
    step "lancement du titre"
    case "$(running_title)" in
        *FIFA*|*fifa*)
            # This console auto-boots FIFA from the dashboard, so "quit the
            # title and run me again" is advice nobody can follow: by the time
            # anyone looks, the title is back. Reboot and launch into the gap
            # instead. The server is already up by now, which is what makes the
            # gap wide enough to land in.
            print "   FIFA tourne déjà -- reboot, puis lancement immédiat"
            reboot_to_dashboard || return 1
            ;;
    esac
    local out
    out=$("$PY" tools/fifa14_early_local_server.py "$XBOX" \
        --local-ip "$MAC" --identity-port "$IDENTITY_PORT" \
        --timeout 900 --launch-title "$TITLE" \
        --redirector-transport plaintext --redirect-fut-resource 2>&1 | tail -2)
    print "$out" | sed 's/^/   /'
    # Without these the console never reaches this server at all, and the game
    # says only "connectez-vous a Xbox Live et aux serveurs EA" -- which names
    # neither the patch nor the step that failed.
    case "$out" in
        *"hostnames preserved"*) ;;
        *) return 1 ;;
    esac
    # The EAS FC session is a second Blaze connection, from powdllzf, to
    # endpoints the launch patch above does not touch -- so the client resolves
    # them for real, reaches nothing, and the menu eventually says "Vous avez
    # perdu la connexion avec les serveurs EA". powdllzf is not mapped yet at
    # this point, so the patcher polls for it.
    step "endpoints EAS FC"
    "$PY" tools/fifa14_easfc_endpoint_patch.py "$XBOX" --local-ip "$MAC" \
        --core-port "$CORE_PORT" --identity-port "$IDENTITY_PORT" \
        --timeout 90 2>&1 | sed 's/^/   /'
    return 0
}

# Keeping the patch applied without anybody watching for the main menu.
#
# The patch has to be in place when Ultimate Team is entered, and until now a
# human said "menu" and ran --patch. That is not a thing anyone can ship.
#
# Patching once, early, does not work -- and the reason is not the one this
# script used to assume. **The title loads helperFunctions more than once.**
# A patch applied seconds after launch verifies, and then reads back
# `original` a minute later: the copy that was patched has been replaced by
# the one the frontend loads afterwards, and only the last one counts. That is
# what waiting for the main menu was really buying.
#
# So the patch is watched rather than applied. The watcher polls the hinted
# window -- 4 MB around the last address the APT actually turned up at, which
# runtime/helperfunctions-apt.json remembers between runs -- and re-applies
# every time it reads `original` again. The patcher validates the header, the
# length and all three branch contexts before writing a byte and reports
# "already patched" when there is nothing to do, so polling it costs one small
# read and can never write into the wrong place.
#
# The full heap sweep stays out of the loop. It reads 8 MB at a time, and
# running it against a title still on the splash once froze this console hard
# enough to drop it off the network.
HINT_GRACE=${HINT_GRACE:-150}
WATCH_INTERVAL=${WATCH_INTERVAL:-5}
WATCH_MISSES=${WATCH_MISSES:-4}
# Once Ultimate Team is entered the APT is gone from memory for good, and the
# watcher would keep sweeping the heap for nothing. Give up after this many
# consecutive misses.
WATCH_GIVE_UP=${WATCH_GIVE_UP:-20}
# Combien la console encaisse d'un coup, en lecture XBDM.
#
# Ce balayage lisait par blocs de 4 Mo. Les mesures du 22 août donnent une
# tolérance d'environ 300 Ko de `getmem` pendant que le titre tourne, au-delà
# de laquelle XBDM se fige et la console tombe du réseau -- quatre fois dans
# l'après-midi, bouton d'alimentation à chaque fois. Ce surveillant s'infligeait
# donc, tout seul et en boucle, plus de dix fois la dose. Il précédait deux des
# trois chutes notées en août, et personne n'avait fait le rapprochement.
#
# Le balayage est un peu plus lent ainsi. Une console qui répond lentement vaut
# mieux qu'une console qu'il faut aller rallumer.
WATCH_SWEEP_CHUNK=${WATCH_SWEEP_CHUNK:-0x40000}
WATCH_LOG=runtime/patch-watch.log

stop_watch() {
    pkill -f "fut-patch-watch" 2>/dev/null
}

# Re-applies the patch for as long as the APT is in memory.
#
# The hinted window is tried first because it costs one small read. It goes
# stale -- the APT is loaded more than once and does not land in the same
# place twice -- so after WATCH_MISSES misses in a row the loop pays for one
# full sweep, which relocates the APT and rewrites the remembered address for
# the next hinted pass.
#
# Every line is logged, success or not. The first version of this logged only
# the lines that matched, so a watcher that had been failing for ten minutes
# looked exactly like a watcher with nothing to do.
start_watch() {
    stop_watch
    nohup zsh -c "
        # fut-patch-watch
        misses=0
        dry=0
        while [ \$dry -lt $WATCH_GIVE_UP ]; do
            if [ \$misses -ge $WATCH_MISSES ]; then
                out=\$('$PY' tools/fifa14_tu3_helperfunctions_runtime_patch.py '$XBOX' --timeout 20 --chunk-size $WATCH_SWEEP_CHUNK 2>&1 | tail -1)
                misses=0
            else
                out=\$('$PY' tools/fifa14_tu3_helperfunctions_runtime_patch.py '$XBOX' --hint-only --timeout 8 --interval 2 --chunk-size 0x100000 2>&1 | tail -1)
            fi
            print \"\$(date +%T) \$out\"
            case \"\$out\" in
                Verified:*) misses=0; dry=0 ;;
                *) misses=\$((misses + 1)); dry=\$((dry + 1)) ;;
            esac
            sleep $WATCH_INTERVAL
        done
        print \"\$(date +%T) APT introuvable, surveillance arrêtée\"
    " >> "$WATCH_LOG" 2>&1 &
    print "   surveillance du patch active (voir $WATCH_LOG)"
}

# The first patch of a run: poll the hinted window until the APT shows up.
# start_watch keeps it applied afterwards, which is the part that matters --
# the title loads helperFunctions again and the first patch does not survive.
await_patch() {
    step "patch helperFunctions (automatique)"
    local out deadline
    deadline=$(( $(date +%s) + HINT_GRACE ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        out=$("$PY" tools/fifa14_tu3_helperfunctions_runtime_patch.py "$XBOX" \
            --hint-only --timeout 20 --interval 3 --chunk-size 0x100000 2>&1 | tail -1)
        case "$out" in
            Verified:*) print "   $out"; return 0 ;;
        esac
        sleep 2
    done
    # The heap moved, or this console has no remembered address yet. The title
    # is past the splash by now, so the sweep is safe.
    print "   la fenêtre hintée n'a rien donné, balayage complet"
    apply_patch
}

apply_patch() {
    step "patch helperFunctions"
    local out
    out=$("$PY" tools/fifa14_tu3_helperfunctions_runtime_patch.py "$XBOX" \
        --timeout 540 --chunk-size 0x800000 2>&1 | tail -1)
    print "   $out"
    # The patch can fail -- the APT is only in memory once the title is far
    # enough along -- and printing READY anyway sends you into FUT with an
    # unpatched launcher, which hangs on the loader with nothing to explain it.
    case "$out" in
        Verified:*) return 0 ;;
        *) return 1 ;;
    esac
}

release_pad() {
    "$PY" tools/xbox360_virtual_input.py "$XBOX" restore >/dev/null 2>&1
}

case "${1:-}" in
    --server) start_server; exit 0 ;;
    --patch)  apply_patch; release_pad; exit 0 ;;
    # Same as the full run. Kept because it is the spelling this project is
    # used to typing; the patch applies on its own either way.
    --launch)
        start_server
        launch_title || fail "les correctifs de lancement n'ont pas pris"
        release_pad
        await_patch || fail "le patch n'a pas pris -- n'entre pas dans FUT"
        start_watch
        print "\n=========================================="
        print " PRÊT. Titre lancé et patché, manette à toi."
        print " Tu peux entrer dans Ultimate Team."
        print "=========================================="
        exit 0
        ;;
esac

# The whole run, with nothing to type in the middle of it.
#
# The screen navigator used to drive the pad to the main menu so the patch had
# something to land on. It needs the framebuffer, XBDM screenshot capture is
# not dependable on this console, and a navigator that timed out left the
# memory sweep running against the splash. None of it was ever necessary:
# await_patch waits for the APT itself rather than for a screen.
start_server
launch_title || fail "les correctifs de lancement n'ont pas pris -- la console n'atteindra pas le serveur"
release_pad
await_patch || fail "le patch n'a pas pris -- n'entre pas dans FUT, relance tools/fut.sh"
start_watch

print "\n=========================================="
print " PRÊT. Rien à faire, tout est en place."
print " Entre dans Ultimate Team quand tu veux."
print "=========================================="
