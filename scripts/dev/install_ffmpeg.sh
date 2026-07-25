#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
target_dir="${project_root}/var/tools/ffmpeg"
archive_path="${TMPDIR:-/tmp}/mediaflow-ffmpeg-${$}.tar.xz"
release_url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"

cleanup() {
  rm -f "${archive_path}"
}
trap cleanup EXIT

mkdir -p "${target_dir}"
curl -fL --retry 2 "${release_url}" -o "${archive_path}"
tar -xf "${archive_path}" \
  -C "${target_dir}" \
  --strip-components=2 \
  ffmpeg-master-latest-linux64-gpl/bin/ffmpeg \
  ffmpeg-master-latest-linux64-gpl/bin/ffprobe
chmod +x "${target_dir}/ffmpeg" "${target_dir}/ffprobe"

"${target_dir}/ffmpeg" -version | sed -n '1p'
"${target_dir}/ffprobe" -version | sed -n '1p'
