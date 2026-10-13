# C430 holding-clip experiment (shelved 28.09.26)

Idea (owner, TheRedWizard #1): while Red Light searches and resolves, play a short black clip in the
**same format as the next file** (SDR, HDR10 or Dolby Vision, 2160p 23.976) behind its screen, then step
Kodi's playlist onto the real file. The TV would stay in the right mode, with no SDR/HDR/DV switch.

## What was measured (Shield, pd.101-104)

- Kodi side (log): with any 2160p 23.976 clip first, the step to the DV episode caused no
  `SetNativeResolution`/`OnLostDisplay`. From the 1080p home screen there was a real resolution switch.
- TV side (owner watching): case 1 (home→DV) and case 2 (SDR clip→DV) showed a mode adjust, and case 2
  started on the clip's black. Case 3 (HDR10→DV) wasn't clearly observed, and case 4 (DV clip→DV) wasn't run.
- A brief dark flick a few seconds into *direct* test plays is most likely Kodi's busy-spinner overlay,
  which Red Light hides on its own plays. It wasn't seen on normal Red Light plays.

## Resuming

1. Make the clips (`/root/scratch/c430` on dev-runner, not committed; about 37 MB for DV):
   - SDR: `ffmpeg -f lavfi -i color=c=0x101010:s=3840x2160:r=24000/1001:d=10 -c:v libx264 ...`
   - HDR10: the same with `libx265 -pix_fmt yuv420p10le -x265-params hdr10=1:colorprim=bt2020:transfer=smpte2084:...`
   - DV: stream-copy the first 8 s of a DV remux (`-c copy -t 8`), so the DV profile is unchanged.
2. `c430-case.sh setup` pushes the clips to `/sdcard/Download/redlight-hold`. `c430-rpc-case.sh <1..4>` runs one case
   through Kodi JSON-RPC (Playlist.Add, Player.Open, Player.GoTo next), with an AlarmClock guard first.
   `c430-case.sh cleanup` removes everything.
3. A tracker window is needed, and the owner watches the TV and reports per case.
