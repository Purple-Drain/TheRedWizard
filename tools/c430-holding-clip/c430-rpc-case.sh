#!/usr/bin/env bash
# c430-rpc-case.sh <2|3|4>: TheRedWizard #1 C430 holding-clip case via Kodi JSON-RPC (owner watching,
# 28.09.26). Builds Kodi's video playlist [holding clip, Friends S05E02 DV], plays the clip ~6 s, then
# Player.GoTo next: the same in-player step a real build would use (no home screen). ~12 s of the
# episode, then Stop. The webserver login is read from guisettings.xml into a mode-600 temp file and
# deleted on exit; it is never printed. Guard (AlarmClock) first.
set -uo pipefail
S=10.1.1.30:5555; H=10.1.1.30; K=/sdcard/Android/data/org.xbmc.kodi/files/.kodi; KB=/root/scratch/kodi-builtin.py
D=/sdcard/Download/redlight-hold
TGT='dav://10.1.1.22:9999/dav/__realdebrid__/Friends.S05.2160p.UHD.Blu-ray.Remux.DV.HEVC.DTS-HD.MA.5.1-SiCFoI/Friends.S05E02.The.One.with.All.the.Kissing.2160p.UHD.Blu-ray.Remux.DV.HEVC.DTS-HD.MA.5.1-SiCFoI.mkv'
CFG=/root/.claude/jobs/9deabba0/tmp/rpc.cfg; trap 'rm -f "$CFG"' EXIT
adb -s $S exec-out cat $K/userdata/guisettings.xml | python3 -c "
import re,sys; x=sys.stdin.read()
g=lambda k:(re.search(r'<setting id=\"services.%s\"[^>]*>([^<]*)<'%k,x) or [None,''])[1]
print('user = \"%s:%s\"'%(g('webserverusername'),g('webserverpassword')))" > "$CFG"; chmod 600 "$CFG"
rpc() { curl -s -m 20 -K "$CFG" -H 'Content-Type: application/json' -d "$1" "http://$H:8080/jsonrpc"; echo; }
kb() { python3 $KB $H "$1" >/dev/null; echo "$(date +%T) sent $1"; }
clip=([1]=none [2]=hold-sdr-2160p23976.mkv [3]=hold-hdr10-2160p23976.mkv [4]=hold-dv-p7-2160p23976.mkv)
name=([1]='home to DV episode' [2]='SDR clip then DV episode' [3]='HDR10 clip then DV episode' [4]='DV clip then DV episode')
kb 'CancelAlarm(rltest,true)'; kb 'AlarmClock(rltest,PlayerControl(Stop),00:02:00,silent)'
kb "Notification(Red Light test,Case $1/4: ${name[$1]},5000)"; sleep 6
start=$(adb -s $S shell "wc -l < $K/temp/kodi.log" | tr -d '\r')
rpc '{"jsonrpc":"2.0","id":1,"method":"Playlist.Clear","params":{"playlistid":1}}' >/dev/null
[ "$1" != 1 ] && rpc "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"Playlist.Add\",\"params\":{\"playlistid\":1,\"item\":{\"file\":\"$D/${clip[$1]}\"}}}" >/dev/null
rpc "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"Playlist.Add\",\"params\":{\"playlistid\":1,\"item\":{\"file\":\"$TGT\"}}}" >/dev/null
echo "$(date +%T) play clip"; rpc '{"jsonrpc":"2.0","id":4,"method":"Player.Open","params":{"item":{"playlistid":1,"position":0}}}'
if [ "$1" = 1 ]; then sleep 0.5; else sleep 6; echo "$(date +%T) step to episode"; fi
[ "$1" != 1 ] && rpc '{"jsonrpc":"2.0","id":5,"method":"Player.GoTo","params":{"playerid":1,"to":"next"}}'
# Kodi's own busy spinner (the skin's darkening overlay) shows while a direct play opens; Red Light hides it
# on its own plays, so hide it here too for ~8 s to keep it out of what the owner is judging.
for i in $(seq 1 5); do python3 $KB $H 'Dialog.Close(busydialog,true)' >/dev/null; python3 $KB $H 'Dialog.Close(busydialognocancel,true)' >/dev/null; sleep 0.3; done
sleep 6; kb 'PlayerControl(Stop)'; sleep 3; kb 'CancelAlarm(rltest,true)'
rpc '{"jsonrpc":"2.0","id":6,"method":"Playlist.Clear","params":{"playlistid":1}}' >/dev/null
adb -s $S shell "sed -n '$start,\$p' $K/temp/kodi.log" | grep -E 'OpenFile|OnLostDisplay|SetNativeResolution|CloseFile' | cut -c12-150
