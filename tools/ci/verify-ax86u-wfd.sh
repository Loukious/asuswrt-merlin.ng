#!/usr/bin/env bash

# Check both the built module and the filesystem used to package the image.
set -Eeuo pipefail

readonly repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly sdk_dir="$repo_root/release/src-rt-5.02L.07p2axhnd"
readonly built_module="$sdk_dir/bcmdrivers/opensource/net/wfd/impl1/wfd.ko"
readonly rootfs="$sdk_dir/targets/962118GW/fs"
readonly marker="WFD_RX_LIFECYCLE_FIX_V1"

verify_module() {
	local module_path="$1"
	if [[ ! -s "$module_path" ]]; then
		echo "Missing WFD module: $module_path" >&2
		return 1
	fi
	if ! readelf -h "$module_path" | grep -q 'Machine:.*AArch64'; then
		echo "WFD module has the wrong architecture: $module_path" >&2
		return 1
	fi
	# Do not accept a marker left only in debug information.
	if ! readelf -p .rodata "$module_path" | grep -F "$marker" >/dev/null; then
		echo "WFD lifecycle fixes were not compiled into $module_path" >&2
		return 1
	fi
}

verify_module "$built_module"
if [[ ! -d "$rootfs/lib/modules" ]]; then
	echo "Missing packaged module tree: $rootfs/lib/modules" >&2
	exit 1
fi
mapfile -d '' installed_modules < <(
	find "$rootfs/lib/modules" -type f -name wfd.ko -print0
)
if (( ${#installed_modules[@]} != 1 )); then
	echo "Expected exactly one packaged WFD module, found ${#installed_modules[@]}" >&2
	exit 1
fi
verify_module "${installed_modules[0]}"

printf 'WFD source marker verified in compiled and packaged AArch64 modules.\n'
sha256sum "$built_module" "${installed_modules[0]}"
