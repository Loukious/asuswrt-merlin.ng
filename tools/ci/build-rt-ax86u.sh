#!/usr/bin/env bash

set -Eeuo pipefail

readonly repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly sdk_dir="$repo_root/release/src-rt-5.02L.07p2axhnd"
readonly output_dir="${1:-$repo_root/dist/rt-ax86u}"
readonly toolchain_base="${TOOLCHAIN_BASE:-/opt/toolchains}"

readonly toolchain_dirs=(
	"crosstools-arm-gcc-5.3-linux-4.1-glibc-2.22-binutils-2.25"
	"crosstools-aarch64-gcc-5.3-linux-4.1-glibc-2.22-binutils-2.25"
	"crosstools-arm-gcc-5.5-linux-4.1-glibc-2.26-binutils-2.28.1"
	"crosstools-aarch64-gcc-5.5-linux-4.1-glibc-2.26-binutils-2.28.1"
)

for toolchain_dir in "${toolchain_dirs[@]}"; do
	if [[ ! -d "$toolchain_base/$toolchain_dir/usr/bin" ]]; then
		echo "Missing toolchain: $toolchain_base/$toolchain_dir" >&2
		exit 1
	fi
done

export TOOLCHAIN_BASE="$toolchain_base"
export BUILDREV="${BUILDREV:-1}"
export PATH="$PATH:$toolchain_base/${toolchain_dirs[2]}/usr/bin:$toolchain_base/${toolchain_dirs[3]}/usr/bin:$toolchain_base/${toolchain_dirs[0]}/usr/bin:$toolchain_base/${toolchain_dirs[1]}/usr/bin"
unset LD_LIBRARY_PATH

find "$sdk_dir/targets/962118GW" -maxdepth 1 -type f \
	-name 'RT-AX86U_*pureubi.w' -delete
if [[ -d "$sdk_dir/image" ]]; then
	find "$sdk_dir/image" -maxdepth 1 -type f \
		-name 'RT-AX86U_*pureubi.w' -delete
fi

rm -f "$repo_root/release/src/router/zlib/stamp-h1"

make -C "$sdk_dir" rt-ax86u

image_dir="$sdk_dir/image"
if [[ ! -d "$image_dir" ]]; then
	echo "Build completed without creating $image_dir" >&2
	exit 1
fi

shopt -s nullglob
firmware_candidates=("$image_dir"/RT-AX86U_*_pureubi.w)
safe_candidates=()
for firmware_path in "${firmware_candidates[@]}"; do
	if [[ "$(basename "$firmware_path")" != *_cferom_* ]]; then
		safe_candidates+=("$firmware_path")
	fi
done

if (( ${#safe_candidates[@]} != 1 )); then
	echo "Expected one non-CFE RT-AX86U pureubi image, found ${#safe_candidates[@]}" >&2
	printf 'Candidate: %s\n' "${firmware_candidates[@]}" >&2
	exit 1
fi

firmware_path="${safe_candidates[0]}"
firmware_name="$(basename "$firmware_path")"
if [[ "$BUILDREV" != "1" && "$firmware_name" != *"-g${BUILDREV}_pureubi.w" ]]; then
	echo "Firmware name does not contain the requested revision: $firmware_name" >&2
	exit 1
fi
firmware_size="$(stat -c '%s' "$firmware_path")"
if (( firmware_size < 32 * 1024 * 1024 )); then
	echo "Refusing unexpectedly small firmware image: $firmware_size bytes" >&2
	exit 1
fi

rm -rf "$output_dir"
mkdir -p "$output_dir"

package_temp="$(mktemp -d)"
checksum_file="$package_temp/sha256sum.sha256"
trap 'rm -rf "$package_temp"' EXIT
(
	cd "$image_dir"
	sha256sum "$firmware_name" > "$checksum_file"
)

archive_name="${firmware_name%_pureubi.w}.zip"
zip -qj "$output_dir/$archive_name" \
	"$firmware_path" \
	"$repo_root/README-merlin.txt" \
	"$repo_root/Changelog-3006.txt" \
	"$checksum_file"
(
	cd "$output_dir"
	sha256sum "$archive_name" > "$archive_name.sha256"
)

echo "Packaged $output_dir/$archive_name"
