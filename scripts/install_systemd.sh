#!/bin/sh
set -eu

release_time="${1:-00:00}"
config_path="${2:-$PWD/config.yaml}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$unit_dir"
umask 077
cat > "$unit_dir/hayden-room-booker.service" <<EOF
[Unit]
Description=Hayden Room Booker idempotent due run

[Service]
Type=oneshot
ExecStart=hayden-booker --config $config_path run --due --live
EOF

cat > "$unit_dir/hayden-room-booker.timer" <<EOF
[Unit]
Description=Run Hayden Room Booker at Arizona release time

[Timer]
OnCalendar=*-*-* $release_time:00 America/Phoenix
Persistent=true
Unit=hayden-room-booker.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hayden-room-booker.timer
echo "Installed and started hayden-room-booker.timer using America/Phoenix."
