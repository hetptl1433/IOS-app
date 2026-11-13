#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 scripts/ios/generate_project.py --check
python3 scripts/ios/validate_project.py
if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "Xcode is required. Run this script on a Mac with Xcode and an iOS Simulator runtime."
  exit 2
fi
mkdir -p artifacts/ios
if [ -n "${SPARC_SIMULATOR_ID:-}" ]; then
  simulator_id="$SPARC_SIMULATOR_ID"
else
  xcrun simctl list -j > artifacts/ios/simulators.json
  selection="$(python3 - <<'PY'
import json
import os

with open("artifacts/ios/simulators.json") as stream:
    inventory = json.load(stream)

def version(value):
    return tuple((list(map(int, value.split("."))) + [0, 0, 0])[:3])

small = os.environ.get("SPARC_SIMULATOR_KIND") == "small"
se_type = "com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation"
device_type = next((item for item in inventory["devicetypes"] if item["identifier"] == se_type), None)
runtimes = sorted((item for item in inventory["runtimes"]
                   if item.get("isAvailable") and ".iOS-" in item["identifier"] and version(item["version"]) >= (17,)),
                  key=lambda item: version(item["version"]), reverse=True)
compatible = []
for runtime in runtimes:
    if small:
        supported = runtime.get("supportedDeviceTypes")
        if supported is not None:
            if not any(item["identifier"] == se_type for item in supported):
                continue
        elif device_type is None or not (version(device_type["minRuntimeVersionString"]) <= version(runtime["version"])
                <= version(device_type["maxRuntimeVersionString"])):
            continue
        compatible.append(runtime)
    choices = [item for item in inventory["devices"].get(runtime["identifier"], [])
               if item.get("isAvailable") and ((item.get("deviceTypeIdentifier") == se_type or "SE (3rd generation)" in item["name"])
                                               if small else "iPhone" in item["name"])]
    choices.sort(key=lambda item: (item.get("state") == "Booted", "Pro" in item["name"], item["name"]), reverse=True)
    if choices:
        print("existing", choices[0]["udid"])
        break
else:
    if small and compatible:
        print("create", compatible[0]["identifier"])
    else:
        raise SystemExit("No compatible iOS 17+ iPhone simulator. Install an iOS runtime supporting "
                         + ("iPhone SE (3rd generation)." if small else "iPhone."))
PY
)"
  read -r selection_type simulator_value <<< "$selection"
  if [ "$selection_type" = "create" ]; then
    simulator_id="$(xcrun simctl create 'SPARC Small iPhone' com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation "$simulator_value")"
  else
    simulator_id="$simulator_value"
  fi
fi
if [ -z "$simulator_id" ]; then
  echo "No available iPhone Simulator. Install an iOS runtime in Xcode Settings > Components."
  exit 2
fi
echo "Testing SPARC on simulator $simulator_id"
xcodebuild -version
xcrun simctl boot "$simulator_id" 2>/dev/null || true
python3 - "$simulator_id" <<'PY'
import subprocess
import sys
subprocess.run(["xcrun", "simctl", "bootstatus", sys.argv[1], "-b"], check=True, timeout=180)
PY
result_bundle="artifacts/ios/SPARC-${SPARC_SIMULATOR_KIND:-regular}-$(date -u +%Y%m%dT%H%M%SZ)-$$.xcresult"
# macOS Bash 3.2 treats empty arrays as unset under nounset. Keep XCTest
# results/screenshots while avoiding slow sysdiagnose collection after failures.
test_options=("-enableCodeCoverage" "YES" "-collect-test-diagnostics" "never")
if [ "${SPARC_TEST_SCOPE:-}" = "ui" ]; then
  test_options+=("-only-testing:SPARCUITests")
fi
set +e
xcodebuild test -project ios/SPARC.xcodeproj -scheme SPARC \
  -destination "platform=iOS Simulator,id=$simulator_id" \
  -destination-timeout 120 \
  -derivedDataPath artifacts/ios/DerivedData \
  -parallel-testing-enabled NO -maximum-concurrent-test-simulator-destinations 1 \
  -test-timeouts-enabled YES -default-test-execution-time-allowance 300 -maximum-test-execution-time-allowance 600 \
  -resultBundlePath "$result_bundle" \
  "${test_options[@]}" \
  CODE_SIGNING_ALLOWED=NO 2>&1 | tee artifacts/ios/xcodebuild.log
status=${PIPESTATUS[0]}
set -e
xcrun simctl io "$simulator_id" screenshot artifacts/ios/final-screen.png >/dev/null 2>&1 || true
if [ -d "$result_bundle" ]; then
  xcrun xcresulttool export attachments --path "$result_bundle" --output-path artifacts/ios/attachments >/dev/null 2>&1 || true
fi
exit "$status"
