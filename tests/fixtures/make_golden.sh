#!/bin/sh
# Rebuilds the test footage. Only rerun on purpose: frozen transcripts, envelopes and the expected timeline depend
# on these exact files (afterwards: uv run python tests/fixtures/freeze.py, then UPDATE_GOLDEN=1 uv run pytest).
#   make_golden.sh         golden/footage/golden.mp4: a synthetic EN+ES talking clip with a filler, a false start,
#                          a retake cue, a mark cue and an editor note (25 fps, room tone), and phone_broll.mp4
#   make_golden.sh broll   only golden/footage/phone_broll.mp4 (vertical 8 s fractal + brown-noise ambience)
#   make_golden.sh face    face/footage/face.mp4 (a public-domain portrait, below)
#   make_golden.sh runon   runon/footage/runon.mp4 (a speaker who never pauses)
# Voices: Piper TTS (https://github.com/OHF-Voice/piper1-gpl, run here as a tool, not shipped) with
# en_US-ljspeech-medium (LJ Speech, public domain) and es_ES-davefx-medium (CC0), from rhasspy/piper-voices (MIT):
#   uvx --from piper-tts==1.8.0 python -m piper.download_voices --data-dir ~/.cache/jianji/piper \
#       en_US-ljspeech-medium es_ES-davefx-medium
set -eu
cd "$(dirname "$0")"
FF=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
[ -x "$FF" ] || FF=ffmpeg
PIPER=${PIPER:-"uvx --quiet --from piper-tts==1.8.0 piper"}
VOICES=${VOICES:-$HOME/.cache/jianji/piper}
EN="$VOICES/en_US-ljspeech-medium.onnx"
ES="$VOICES/es_ES-davefx-medium.onnx"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
N=0

# tts <voice> <text>: one spoken piece at 48 kHz mono, appended to the list (no sampling noise; Piper still varies slightly per run)
tts() {
  N=$((N + 1))
  printf '%s' "$2" | $PIPER -m "$1" --noise-scale 0 --noise-w-scale 0 -f "$T/raw.wav" >/dev/null 2>&1
  "$FF" -v error -y -i "$T/raw.wav" -ar 48000 -ac 1 "$T/$N.wav"
  echo "file '$T/$N.wav'" >> "$T/list.txt"
}
# sil <seconds>: digital silence appended to the list (room tone goes under everything at the end)
sil() {
  N=$((N + 1))
  "$FF" -v error -y -f lavfi -i anullsrc=r=48000:cl=mono -t "$1" "$T/$N.wav"
  echo "file '$T/$N.wav'" >> "$T/list.txt"
}
voice() { "$FF" -v error -y -f concat -safe 0 -i "$T/list.txt" "$T/voice.wav"; }

mkdir -p golden/footage
if [ -z "${1:-}" ] || [ "${1:-}" = broll ]; then
  "$FF" -v error -y -f lavfi -i "mandelbrot=size=360x640:rate=25" \
    -f lavfi -i "anoisesrc=color=brown:amplitude=0.03:r=48000:seed=7" -t 8 \
    -map 0:v -map 1:a -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -b:a 96k -ac 2 \
    golden/footage/phone_broll.mp4
  echo "wrote golden/footage/phone_broll.mp4"
fi
[ "${1:-}" = broll ] && exit 0

# Source: "Official portrait of NASA astronaut Jack Hathaway" (NASA JSC, jsc2025e068207, images.nasa.gov): a NASA
# work, public domain. Used only as a test fixture (no endorsement). 6 s vertical 540x960, the face in the upper
# middle, slow pan, room tone.
if [ "${1:-}" = face ]; then
  curl -sf -o "$T/p.jpg" "https://images-assets.nasa.gov/image/jsc2025e068207_alt/jsc2025e068207_alt~large.jpg"
  mkdir -p face/footage
  "$FF" -v error -y -loop 1 -framerate 25 -i "$T/p.jpg" -f lavfi -i "anoisesrc=color=pink:amplitude=0.004:r=48000:seed=7" \
    -filter_complex "[0]scale=-2:1100,pad=iw:ih+30:0:30:black,crop=540:960:190+t*4:0,format=yuv420p[v];[1]aformat=channel_layouts=stereo[a]" \
    -map "[v]" -map "[a]" -t 6 -r 25 -c:v libx264 -preset veryfast -crf 30 -c:a aac -b:a 96k face/footage/face.mp4
  echo "wrote face/footage/face.mp4"
  exit 0
fi

# three sentences with every pause squeezed out, 1.5 s of silence first, room tone under everything
if [ "${1:-}" = runon ]; then
  tts "$EN" "The cache was cold. Every single request went straight to the database. So we added a second layer."
  voice
  "$FF" -v error -y -i "$T/voice.wav" \
    -af "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.03:stop_threshold=-45dB" \
    "$T/r.wav"
  "$FF" -v error -f lavfi -i anullsrc=r=48000:cl=mono -t 1.5 "$T/head.wav"
  "$FF" -v error -f lavfi -i anullsrc=r=48000:cl=mono -t 1.0 "$T/tail.wav"
  printf "file '%s'\nfile '%s'\nfile '%s'\n" "$T/head.wav" "$T/r.wav" "$T/tail.wav" > "$T/l.txt"
  "$FF" -v error -f concat -safe 0 -i "$T/l.txt" "$T/v.wav"
  mkdir -p runon/footage
  "$FF" -v error -y -i "$T/v.wav" -f lavfi -i "anoisesrc=color=pink:amplitude=0.004:r=48000:seed=7" \
    -f lavfi -i "testsrc=size=640x360:rate=25" \
    -filter_complex "[0][1]amix=inputs=2:duration=first:normalize=0,aformat=channel_layouts=stereo[a]" \
    -map 2:v -map "[a]" -shortest -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -b:a 128k \
    runon/footage/runon.mp4
  echo "wrote runon/footage/runon.mp4"
  exit 0
fi

sil 2.0
tts "$EN" "Today we talk about the saga pattern."; sil 0.8; tts "$EN" "Um,"; sil 0.45
tts "$EN" "it is how services agree without a global lock."; sil 1.3
tts "$EN" "Distributed transactions are,"; sil 0.35; tts "$EN" "are hard because"; sil 1.0; tts "$EN" "retake"; sil 1.0
tts "$EN" "Distributed transactions are hard because every service owns its own data."; sil 1.3
tts "$EN" "Idempotency is not optional. It is the contract."; sil 1.0; tts "$EN" "mark that"; sil 1.3
tts "$ES" "Ahora en español. Una saga es una secuencia de pasos locales, y cada paso tiene su compensación."; sil 1.3
tts "$ES" "Nota de edición: aquí va el diagrama."; sil 1.3
tts "$EN" "That is the whole idea. Thanks for watching."; sil 1.3
voice
"$FF" -v error -y -i "$T/voice.wav" \
  -f lavfi -i "anoisesrc=color=pink:amplitude=0.004:r=48000:seed=7" \
  -f lavfi -i "testsrc=size=640x360:rate=25" \
  -filter_complex "[0][1]amix=inputs=2:duration=first:normalize=0,aformat=channel_layouts=stereo[a]" \
  -map 2:v -map "[a]" -shortest -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -b:a 128k \
  golden/footage/golden.mp4
echo "wrote golden/footage/golden.mp4"
