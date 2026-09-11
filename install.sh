#!/bin/sh
# jobsift in one line, on macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/kimlj/jobsift/main/install.sh | sh
#
# Puts jobsift in ~/jobsift (or $JOBSIFT_DIR), gives it its own Python
# environment, installs what it needs, and starts the setup. Safe to run again: an
# existing copy is updated with git pull, and config.yaml, .env, your resume and
# data/ are never touched. It needs git and Python 3.10 or newer, and says how to
# get either if it is missing. Read it first if you would rather: it is this file.
set -u

REPO=https://github.com/kimlj/jobsift.git
DIR=${JOBSIFT_DIR:-"$HOME/jobsift"}

stop() { printf '\n%s\n' "$1" >&2; exit 1; }

command -v git >/dev/null 2>&1 ||
    stop "jobsift needs git. Install it (sudo apt install git, or brew install git), then run the same line again."

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY=$candidate
        break
    fi
done
[ -n "$PY" ] ||
    stop "jobsift needs Python 3.10 or newer. Install it (sudo apt install python3, or brew install python), then run the same line again."

if [ -d "$DIR/.git" ]; then
    echo "Updating jobsift in $DIR"
    git -C "$DIR" pull --ff-only --quiet || stop "git could not update jobsift; the message above says why."
else
    echo "Downloading jobsift into $DIR"
    git clone --quiet "$REPO" "$DIR" || stop "git could not download jobsift; the message above says why."
fi

cd "$DIR" || stop "could not open $DIR"
if [ ! -x .venv/bin/python ]; then
    echo "Making its Python environment (.venv)"
    "$PY" -m venv .venv ||
        stop "Python could not make .venv. On Debian or Ubuntu: sudo apt install python3-venv, then run this again."
fi
echo "Installing what it needs (a minute or two the first time)"
./.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt ||
    stop "pip could not install the requirements; the message above says why."

if [ -n "${JOBSIFT_SKIP_SETUP:-}" ]; then
    echo "Installed. Setup skipped (JOBSIFT_SKIP_SETUP is set)."
else
    # Piped into sh, this script's stdin is the download itself, so setup's
    # questions would read the rest of the script as answers. They read the
    # terminal instead.
    ./.venv/bin/python -m jobsift --setup </dev/tty
fi

# On a Linux server, keeping it running is the natural next step. It is offered
# only where one command can do it: systemd running, and this script run as root,
# as on a fresh cloud server. Anywhere else, docs/deploy.md has the steps.
UNIT_FILE=/etc/systemd/system/jobsift.service
if [ -z "${JOBSIFT_SKIP_SETUP:-}" ] && [ -d /run/systemd/system ] && [ "$(id -u)" = 0 ]; then
    if [ -f "$UNIT_FILE" ]; then
        printf '\nA jobsift service is already set up here. Restart it to use this version? [Y/n] '
        read -r answer </dev/tty || answer=n
        case "$answer" in
            [nN]*) echo "Left running as it was." ;;
            *) systemctl restart jobsift && echo "Restarted." ;;
        esac
    else
        printf '\nKeep jobsift running on this server, now and after every restart? [Y/n] '
        read -r answer </dev/tty || answer=n
        case "$answer" in
            [nN]*) echo "Not started. docs/deploy.md, Option A, has the steps for later." ;;
            *)
                mkdir -p "$DIR/data" "$DIR/logs"
                cat >"$UNIT_FILE" <<UNIT
[Unit]
Description=jobsift
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -u -m jobsift
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1
# It reads a mailbox, holds API keys and writes two folders; nothing more.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$DIR/data $DIR/logs

[Install]
WantedBy=multi-user.target
UNIT
                if systemctl daemon-reload && systemctl enable --now jobsift; then
                    echo "Running, and it will start again after every restart."
                    echo "Watch it with:  journalctl -u jobsift -f"
                else
                    echo "systemd could not start it; journalctl -u jobsift says why."
                fi
                ;;
        esac
    fi
fi

cat <<EOF

From now on, in $DIR :
  ./jobsift.sh --setup                 change any answer
  ./jobsift.sh --once --no-telegram    one pass, no alerts
  ./jobsift.sh                         keep it running
EOF
