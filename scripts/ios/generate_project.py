#!/usr/bin/env python3
"""Generate the checked-in Xcode project without XcodeGen or package dependencies.

Run after adding/removing Swift source files. IDs depend on paths, so reruns produce
small stable diffs. `--check` validates freshness without writing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "ios"
PROJECT = IOS / "SPARC.xcodeproj"


def identifier(key: str) -> str:
    return hashlib.sha1(("sparc-ios:" + key).encode()).hexdigest()[:24].upper()


def quote(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render() -> tuple[str, str]:
    objects: list[tuple[str, str, str]] = []

    def add(key: str, kind: str, body: str) -> str:
        uid = identifier(key)
        objects.append((uid, kind, body))
        return uid

    def objlist(values: list[str]) -> str:
        return "(" + ", ".join(values) + ("," if values else "") + ")"

    source_sets = {
        "SPARC": sorted((IOS / "SPARC").rglob("*.swift")),
        "SPARCTests": sorted((IOS / "SPARCTests").rglob("*.swift")),
        "SPARCUITests": sorted((IOS / "SPARCUITests").rglob("*.swift")),
    }
    file_refs: dict[str, str] = {}
    source_groups: list[str] = []
    phases: dict[str, tuple[str, str, str]] = {}
    products: list[str] = []
    target_ids = {target: identifier("target:" + target) for target in source_sets}

    for target, sources in source_sets.items():
        refs: list[str] = []
        builds: list[str] = []
        for path in sources:
            rel = path.relative_to(IOS).as_posix()
            ref = add("file:" + rel, "PBXFileReference", f"lastKnownFileType = sourcecode.swift; path = {quote(rel)}; sourceTree = SOURCE_ROOT;")
            file_refs[rel] = ref
            refs.append(ref)
            builds.append(add("build:" + rel, "PBXBuildFile", f"fileRef = {ref};"))
        group = add("group:" + target, "PBXGroup", f"children = {objlist(refs)}; name = {quote(target)}; sourceTree = \"<group>\";")
        source_groups.append(group)
        source_phase = add("sources:" + target, "PBXSourcesBuildPhase", f"buildActionMask = 2147483647; files = {objlist(builds)}; runOnlyForDeploymentPostprocessing = 0;")
        framework_phase = add("frameworks:" + target, "PBXFrameworksBuildPhase", "buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0;")
        resources: list[str] = []
        resource_files = []
        if target == "SPARC":
            resource_files = [("SPARC/Resources/Assets.xcassets", "folder.assetcatalog"), ("SPARC/PrivacyInfo.xcprivacy", "text.xml")]
        elif target == "SPARCTests":
            resource_files = [("../controller/fixtures/wire-v1.json", "text.json")]
        for rel, filetype in resource_files:
            ref = add("file:" + rel, "PBXFileReference", f"lastKnownFileType = {filetype}; path = {quote(rel)}; sourceTree = SOURCE_ROOT;")
            refs.append(ref)
            resources.append(add("build:" + rel, "PBXBuildFile", f"fileRef = {ref};"))
        # Update the group's copy of children after adding resources.
        objects = [(uid, kind, f"children = {objlist(refs)}; name = {quote(target)}; sourceTree = \"<group>\";" if uid == group else body) for uid, kind, body in objects]
        resource_phase = add("resources:" + target, "PBXResourcesBuildPhase", f"buildActionMask = 2147483647; files = {objlist(resources)}; runOnlyForDeploymentPostprocessing = 0;")
        phases[target] = (source_phase, framework_phase, resource_phase)
        ext = "app" if target == "SPARC" else "xctest"
        filetype = "wrapper.application" if target == "SPARC" else "wrapper.cfbundle"
        products.append(add("product:" + target, "PBXFileReference", f"explicitFileType = {filetype}; includeInIndex = 0; path = {quote(target + '.' + ext)}; sourceTree = BUILT_PRODUCTS_DIR;"))

    products_group = add("group:products", "PBXGroup", f"children = {objlist(products)}; name = Products; sourceTree = \"<group>\";")
    main_group = add("group:main", "PBXGroup", f"children = {objlist(source_groups + [products_group])}; sourceTree = \"<group>\";")

    def configuration(key: str, name: str, settings: dict[str, object]) -> str:
        body = " ".join(f"{setting} = {quote(value)};" for setting, value in sorted(settings.items()))
        return add(key + ":" + name, "XCBuildConfiguration", f"buildSettings = {{ {body} }}; name = {name};")

    def configlist(key: str, ids: list[str]) -> str:
        return add(key, "XCConfigurationList", f"buildConfigurations = {objlist(ids)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;")

    common = {
        "CLANG_ENABLE_MODULES": "YES", "CLANG_ENABLE_OBJC_ARC": "YES",
        "CLANG_WARN_DOCUMENTATION_COMMENTS": "YES", "CLANG_WARN_UNGUARDED_AVAILABILITY": "YES_AGGRESSIVE",
        "COPY_PHASE_STRIP": "NO", "GCC_C_LANGUAGE_STANDARD": "gnu17",
        "GCC_WARN_64_TO_32_BIT_CONVERSION": "YES", "GCC_WARN_UNDECLARED_SELECTOR": "YES",
        "IPHONEOS_DEPLOYMENT_TARGET": "17.0", "SDKROOT": "iphoneos",
        "SWIFT_VERSION": "5.0", "SWIFT_STRICT_CONCURRENCY": "targeted",
        "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
    }
    project_configs = configlist("configs:project", [
        configuration("config:project", "Debug", {**common, "DEBUG_INFORMATION_FORMAT": "dwarf", "ENABLE_TESTABILITY": "YES", "GCC_OPTIMIZATION_LEVEL": "0", "ONLY_ACTIVE_ARCH": "YES", "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG $(inherited)", "SWIFT_OPTIMIZATION_LEVEL": "-Onone"}),
        configuration("config:project", "Release", {**common, "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym", "SWIFT_COMPILATION_MODE": "wholemodule", "SWIFT_OPTIMIZATION_LEVEL": "-O", "VALIDATE_PRODUCT": "YES"}),
    ])
    for index, target in enumerate(source_sets):
        settings: dict[str, object] = {
            "CODE_SIGN_STYLE": "Automatic", "CURRENT_PROJECT_VERSION": "1",
            "MARKETING_VERSION": "0.1.0", "PRODUCT_NAME": "$(TARGET_NAME)",
            "PRODUCT_BUNDLE_IDENTIFIER": "com.hetpatel.sparc" + ("" if target == "SPARC" else "." + target),
            "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator", "TARGETED_DEVICE_FAMILY": "1",
            "LD_RUNPATH_SEARCH_PATHS": "$(inherited) @executable_path/Frameworks",
            "GENERATE_INFOPLIST_FILE": "YES" if target != "SPARC" else "NO",
        }
        if target == "SPARC":
            settings.update({"INFOPLIST_FILE": "SPARC/Info.plist", "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon", "ENABLE_PREVIEWS": "YES"})
        elif target == "SPARCTests":
            settings.update({"BUNDLE_LOADER": "$(TEST_HOST)", "TEST_HOST": "$(BUILT_PRODUCTS_DIR)/SPARC.app/SPARC"})
        else:
            settings["TEST_TARGET_NAME"] = "SPARC"
        configs = configlist("configs:" + target, [configuration("config:" + target, name, settings) for name in ("Debug", "Release")])
        dependencies: list[str] = []
        if target != "SPARC":
            proxy = add("proxy:" + target, "PBXContainerItemProxy", f"containerPortal = {identifier('project')}; proxyType = 1; remoteGlobalIDString = {target_ids['SPARC']}; remoteInfo = SPARC;")
            dependencies.append(add("dependency:" + target, "PBXTargetDependency", f"target = {target_ids['SPARC']}; targetProxy = {proxy};"))
        product_type = "com.apple.product-type.application" if target == "SPARC" else "com.apple.product-type.bundle.unit-test" if target == "SPARCTests" else "com.apple.product-type.bundle.ui-testing"
        add("target:" + target, "PBXNativeTarget", f"buildConfigurationList = {configs}; buildPhases = {objlist(list(phases[target]))}; buildRules = (); dependencies = {objlist(dependencies)}; name = {target}; productName = {target}; productReference = {products[index]}; productType = {quote(product_type)};")

    attributes = " ".join(f"{uid} = {{CreatedOnToolsVersion = 16.0; ProvisioningStyle = Automatic;" + (f" TestTargetID = {target_ids['SPARC']};" if name != "SPARC" else "") + "};" for name, uid in target_ids.items())
    add("project", "PBXProject", f"attributes = {{BuildIndependentTargetsInParallel = 1; LastUpgradeCheck = 1600; TargetAttributes = {{{attributes}}};}}; buildConfigurationList = {project_configs}; compatibilityVersion = \"Xcode 14.0\"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base); mainGroup = {main_group}; productRefGroup = {products_group}; projectDirPath = \"\"; projectRoot = \"\"; targets = {objlist(list(target_ids.values()))};")
    output = ["// !$*UTF8*$!", "{", "archiveVersion = 1;", "classes = {};", "objectVersion = 56;", "objects = {"]
    for uid, kind, body in sorted(objects, key=lambda entry: (entry[1], entry[0])):
        output.append(f"  {uid} = {{ isa = {kind}; {body} }};")
    output.extend(["};", f"rootObject = {identifier('project')};", "}", ""])

    app = target_ids["SPARC"]
    unit = target_ids["SPARCTests"]
    ui = target_ids["SPARCUITests"]

    def ref(uid: str, name: str, product: str) -> str:
        return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{uid}" BuildableName="{product}" BlueprintName="{name}" ReferencedContainer="container:SPARC.xcodeproj"/>'

    scheme = f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.7">
  <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries>
    <BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{ref(app, "SPARC", "SPARC.app")}</BuildActionEntry>
  </BuildActionEntries></BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES">
    <Testables>
      <TestableReference skipped="NO" parallelizable="NO">{ref(unit, "SPARCTests", "SPARCTests.xctest")}</TestableReference>
      <TestableReference skipped="NO" parallelizable="NO">{ref(ui, "SPARCUITests", "SPARCUITests.xctest")}</TestableReference>
    </Testables>
  </TestAction>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref(app, "SPARC", "SPARC.app")}</BuildableProductRunnable></LaunchAction>
  <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref(app, "SPARC", "SPARC.app")}</BuildableProductRunnable></ProfileAction>
  <AnalyzeAction buildConfiguration="Debug"/>
  <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
'''
    return "\n".join(output), scheme


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    project, scheme = render()
    files = {
        PROJECT / "project.pbxproj": project,
        PROJECT / "xcshareddata/xcschemes/SPARC.xcscheme": scheme,
    }
    stale = []
    for path, content in files.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print("Project is stale. Run python3 scripts/ios/generate_project.py:")
        print("\n".join(stale))
        return 1
    print("Xcode project is current." if args.check else "Generated ios/SPARC.xcodeproj and shared SPARC scheme.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
