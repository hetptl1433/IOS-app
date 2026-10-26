#!/usr/bin/env python3
"""Check project references, assets, permissions and scheme on any Python host.

This is a structural check. It deliberately does not claim to replace Xcode's
Swift compiler, Simulator tests or physical Bluetooth/Wi-Fi verification.
"""
from __future__ import annotations

import json
import plistlib
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "ios"


def main() -> int:
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    project_path = IOS / "SPARC.xcodeproj/project.pbxproj"
    text = project_path.read_text()
    definitions = re.findall(r"^\s*([A-F0-9]{24}) = \{ isa = (\w+); (.*) \};$", text, re.M)
    ids = [uid for uid, _, _ in definitions]
    check(len(ids) == len(set(ids)), "Duplicate project object IDs")
    referenced = set(re.findall(r"\b[A-F0-9]{24}\b", text))
    check(referenced == set(ids), "Project contains unresolved object references")
    types = {uid: kind for uid, kind, _ in definitions}
    targets = [body for _, kind, body in definitions if kind == "PBXNativeTarget"]
    check(len(targets) == 3, "Expected app, unit test and UI test targets")
    for uid, kind, body in definitions:
        if kind == "PBXFileReference" and "sourceTree = SOURCE_ROOT" in body:
            match = re.search(r'path = ("(?:[^"\\]|\\.)*");', body)
            check(match is not None, f"Missing file path in {uid}")
            if match:
                path = json.loads(match.group(1))
                check((IOS / path).exists(), f"Missing referenced file: {path}")
        if kind == "PBXBuildFile":
            match = re.search(r"fileRef = ([A-F0-9]{24});", body)
            check(bool(match and types.get(match.group(1)) == "PBXFileReference"), f"Invalid build file: {uid}")
    source_paths = {path.relative_to(IOS).as_posix() for path in IOS.rglob("*.swift")}
    check(bool(source_paths), "No Swift source files")
    for path in source_paths:
        check(f"path = {json.dumps(path)};" in text, f"Swift source omitted from Xcode project: {path}")

    with (IOS / "SPARC/Info.plist").open("rb") as file:
        info = plistlib.load(file)
    for key in ("NSBluetoothAlwaysUsageDescription", "NSLocalNetworkUsageDescription"):
        check(isinstance(info.get(key), str) and len(info[key]) > 30, f"Missing specific purpose string: {key}")
    check("_sparc._tcp" in info.get("NSBonjourServices", []), "Missing SPARC Bonjour declaration")
    ats = info.get("NSAppTransportSecurity", {})
    check(ats.get("NSAllowsLocalNetworking") is True, "Local encrypted transport ATS allowance missing")
    check(not ats.get("NSAllowsArbitraryLoads"), "Broad ATS exception is forbidden")
    check(set(ats) == {"NSAllowsLocalNetworking", "NSExceptionDomains"},
          "ATS must contain only local networking and explicit private-IP exceptions")
    # iOS 17+ requires explicit IP/CIDR entries even with NSAllowsLocalNetworking.
    # Keep these identical to ControllerAddress's private, link-local and loopback ranges.
    local_ranges = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8"}
    exceptions = ats.get("NSExceptionDomains", {})
    check(isinstance(exceptions, dict) and set(exceptions) == local_ranges,
          "ATS IP exceptions must match only the supported private/local IPv4 ranges")
    if isinstance(exceptions, dict):
        for address, settings in exceptions.items():
            check(isinstance(settings, dict) and set(settings) == {"NSExceptionAllowsInsecureHTTPLoads"}
                  and settings.get("NSExceptionAllowsInsecureHTTPLoads") is True,
                  f"Unexpected ATS exception settings for {address}")

    with (IOS / "SPARC/PrivacyInfo.xcprivacy").open("rb") as file:
        privacy = plistlib.load(file)
    check(privacy.get("NSPrivacyTracking") is False, "Tracking must be disabled")
    check(privacy.get("NSPrivacyCollectedDataTypes") == [], "Unexpected collected-data declaration")
    check(any(item.get("NSPrivacyAccessedAPIType") == "NSPrivacyAccessedAPICategoryUserDefaults"
              and "CA92.1" in item.get("NSPrivacyAccessedAPITypeReasons", [])
              for item in privacy.get("NSPrivacyAccessedAPITypes", [])), "App settings required-reason declaration missing")

    assets = IOS / "SPARC/Resources/Assets.xcassets"
    for manifest in assets.rglob("Contents.json"):
        data = json.loads(manifest.read_text())
        for image in data.get("images", []):
            if "filename" in image:
                check((manifest.parent / image["filename"]).is_file(), f"Missing asset {image['filename']}")
    icon = assets / "AppIcon.appiconset/AppIcon.png"
    if icon.exists():
        header = icon.read_bytes()[:26]
        check(header[:8] == b"\x89PNG\r\n\x1a\n", "App icon must be PNG")
        check(len(header) >= 26 and struct.unpack(">II", header[16:24]) == (1024, 1024), "App icon must be 1024 x 1024")
        check(len(header) >= 26 and header[25] in (0, 2), "App icon must not have an alpha channel")
    else:
        check(False, "Missing app icon")

    scheme = ET.parse(IOS / "SPARC.xcodeproj/xcshareddata/xcschemes/SPARC.xcscheme").getroot()
    testables = scheme.findall("./TestAction/Testables/TestableReference")
    check(len(testables) == 2, "Shared scheme must run unit and UI tests")
    for buildable in scheme.findall(".//BuildableReference"):
        check(types.get(buildable.attrib["BlueprintIdentifier"]) == "PBXNativeTarget", "Scheme refers to missing target")
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors))
        return 1
    print(f"Validated {len(source_paths)} Swift files, 3 Xcode targets, shared test scheme, assets and privacy declarations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
