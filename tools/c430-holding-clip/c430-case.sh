#!/usr/bin/env bash
# c430-case.sh <setup|1|2|3|4|cleanup>: TheRedWizard #1 C430 holding-clip test, one case at a time, owner
# watching (28.09.26). Each case is an .m3u playlist on the Shield: [holding clip, Friends S05E02 DV].
# The clip plays ~5 s, then PlayerControl(Next) steps Kodi's playlist onto the episode (the gapless path
# a real build would use, no home screen); the episode plays ~12 s, then Stop.
# Case 1 is the episode alone from the home screen. Guard (AlarmClock 2 min) first. Builtins only.
set -uo pipefail
S=10.1.1.30:5555; H=10.1.1.30; K=/sdcard/Android/data/org.xbmc.kodi/files/.kodi; KB=/root/scratch/kodi-builtin.py
D=/sdcard/Download/redlight-hold
TGT='dav://10.1.1.22:9999/dav/__realdebrid__/Friends.S05.2160p.UHD.Blu-ray.Remux.DV.HEVC.DTS-HD.MA.5.1-SiCFoI/Friends.S05E02.The.One.with.All.the.Kissing.2160p.UHD.Blu-ray.Remux.DV.HEVC.DTS-HD.MA.5.1-SiCFoI.mkv'
kb() { python3 $KB $H "$1" >/dev/null; echo "$(date +%T) sent $1"; }
adb connect $S >/dev/null 2>&1
case "$1" in
setup)
  adb -s $S shell "mkdir -p $D"
  for f in /root/scratch/c430/*.mkv; do adb -s $S push "$f" $D/ >/dev/null && echo "pushed $(basename $f)"; done
  printf '#EXTM3U\n%s\n' "$TGT" > /root/scratch/c430/case1.m3u
  for c in 2:sdr 3:hdr10 4:dv-p7; do n=${c%%:*}; t=${c#*:}; printf '#EXTM3U\n%s\n%s\n' "$D/hold-$t-2160p23976.mkv" "$TGT" > /root/scratch/c430/case$n.m3u; done
  for n in 1 2 3 4; do adb -s $S push /root/scratch/c430/case$n.m3u $D/ >/dev/null; done; echo "playlists pushed" ;;
1|2|3|4)
  names=(x 'home to DV episode' 'SDR clip then DV episode' 'HDR10 clip then DV episode' 'DV clip then DV episode')
  kb 'CancelAlarm(rltest,true)'; kb 'AlarmClock(rltest,PlayerControl(Stop),00:02:00,silent)'
  kb "Notification(Red Light test,Case $1/4: ${names[$1]},5000)"; sleep 6
  start=$(adb -s $S shell "wc -l < $K/temp/kodi.log" | tr -d '\r')
  kb "PlayMedia(special://profile/playlists/video/rltest-case$1.m3u)"
  if [ "$1" != 1 ]; then sleep 6; kb "Notification(Red Light test,Now switching to the episode,2500)"; kb 'PlayerControl(Next)'; fi
  sleep 14; kb 'PlayerControl(Stop)'; sleep 3; kb 'CancelAlarm(rltest,true)'
  adb -s $S shell "sed -n '$start,\$p' $K/temp/kodi.log" | grep -E 'OpenFile|OnLostDisplay|SetNativeResolution|CloseFile' | cut -c12-140 ;;
cleanup) adb -s $S shell "rm -rf $D $K/userdata/playlists/video/rltest-case*.m3u" && echo "clips and playlists removed from the Shield" ;;
esac
