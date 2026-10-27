import XCTest

final class SPARCFlowTests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
    }

    override func tearDownWithError() throws {
        if let testRun, testRun.failureCount > 0 {
            screenshot("failure-\(name)")
            let description = app.debugDescription
            print(description)
            let hierarchy = XCTAttachment(string: description)
            hierarchy.name = "failure-accessibility-hierarchy"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
        }
        app.terminate()
    }

    private func launch(state: String = "waiting", extra: [String] = []) {
        let textSize = extra.contains("-UIPreferredContentSizeCategoryName") ? [] :
            ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL"]
        app.launchArguments = ["--uitesting", "--state", state] + textSize + extra
        app.launch()
        // The profile control stays at the top even when Dynamic Type moves the CTA below the fold.
        XCTAssertTrue(app.buttons["home.settings"].waitForExistence(timeout: 8))
    }

    private func reveal(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        // Offscreen SwiftUI children can be absent from the accessibility tree.
        // isHittable alone can also report true for a button below the viewport.
        for _ in 0..<10 {
            if let page = visiblePage() {
                let viewport = visibleContentFrame(of: page)
                if element.exists && element.isHittable && viewport.contains(element.frame) { return }
                let needsDownwardScroll = element.exists && element.frame.minY < viewport.minY
                dragPage(in: viewport, downward: needsDownwardScroll)
                waitForScrollToSettle(page, file: file, line: line)
            } else {
                app.swipeUp()
            }
        }
        let fullyVisible = element.exists && (visiblePage().map { visibleContentFrame(of: $0).contains(element.frame) } ?? false)
        XCTAssertTrue(element.exists && element.isHittable && fullyVisible,
                      "Element is not reachable after scrolling: \(element)", file: file, line: line)
    }

    private func visiblePage() -> XCUIElement? {
        // XCUIElementQuery predicates cannot query the computed 'hittable' property.
        app.scrollViews.allElementsBoundByIndex.last(where: { $0.isHittable })
    }

    private func visibleContentFrame(of page: XCUIElement) -> CGRect {
        let pageFrame = page.frame.intersection(app.frame)
        var top = pageFrame.minY
        var bottom = pageFrame.maxY
        if let navigation = app.navigationBars.allElementsBoundByIndex.last(where: { $0.isHittable }) {
            top = max(top, navigation.frame.maxY)
        }
        let tab = app.buttons["tab.home"]
        if tab.exists && tab.isHittable {
            // The tab bar floats over the page, so its top edge is where the visible content ends.
            bottom = min(bottom, tab.frame.minY)
        }
        return CGRect(x: pageFrame.minX, y: top, width: pageFrame.width,
                      height: max(0, bottom - top)).insetBy(dx: 1, dy: 4)
    }

    private func dragPage(in viewport: CGRect, downward: Bool) {
        // Use the padding gutter so dragging cannot press a centered navigation link or picker.
        let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
        let gutterX = viewport.minX + 3 - app.frame.minX
        let upper = origin.withOffset(CGVector(dx: gutterX,
                                              dy: viewport.minY + viewport.height * 0.25 - app.frame.minY))
        let lower = origin.withOffset(CGVector(dx: gutterX,
                                              dy: viewport.minY + viewport.height * 0.8 - app.frame.minY))
        if downward { upper.press(forDuration: 0.1, thenDragTo: lower, withVelocity: .slow, thenHoldForDuration: 0.2) }
        else { lower.press(forDuration: 0.1, thenDragTo: upper, withVelocity: .slow, thenHoldForDuration: 0.2) }
    }

    private func waitForScrollToSettle(_ page: XCUIElement, file: StaticString, line: UInt) {
        // A page whose content is one container exposes it as an "other" child; a flat page falls back to its first child.
        func scrollContent() -> XCUIElement {
            let container = page.children(matching: .other).firstMatch
            return container.exists ? container : page.children(matching: .any).firstMatch
        }
        var previousFrame: CGRect?
        var stableSince = Date()
        let stable = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            let content = scrollContent()
            guard content.exists else { return false }
            let frame = content.frame
            if frame != previousFrame {
                previousFrame = frame
                stableSince = Date()
                return false
            }
            return Date().timeIntervalSince(stableSince) >= 0.25
        }, object: page)
        XCTAssertEqual(XCTWaiter.wait(for: [stable], timeout: 4), .completed,
                       "Scrolling must settle before interacting with a control", file: file, line: line)
    }

    private func waitForPage(_ title: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 5),
                      "Expected page: \(title)", file: file, line: line)
    }

    private func waitForDismissal(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        let disappeared = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [disappeared], timeout: 5), .completed,
                       "Expected the presented view to finish dismissing", file: file, line: line)
    }

    private func waitForLabel(_ element: XCUIElement, _ label: String, timeout: TimeInterval = 5,
                              file: StaticString = #filePath, line: UInt = #line) {
        let matches = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", label), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [matches], timeout: timeout), .completed,
                       "Expected \(element) to read \"\(label)\"", file: file, line: line)
    }

    private func waitForSelected(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        let selected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "selected == true"), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [selected], timeout: 5), .completed,
                       "Expected the control to be selected: \(element)", file: file, line: line)
    }

    /// Nothing on screen may mention the demo data or the virtual car; it is plain Home for everyone.
    private func assertNoDemoWording(file: StaticString = #filePath, line: UInt = #line) {
        let wording = NSPredicate(format: "label CONTAINS[c] 'demo' OR label CONTAINS[c] 'virtual'")
        let matches = app.descendants(matching: .any).matching(wording)
        XCTAssertEqual(matches.count, 0,
                       "No visible text may mention demo or virtual: \(matches.allElementsBoundByIndex.map(\.label))",
                       file: file, line: line)
    }

    /// A button of the confirmation dialog. The page's own End button has the same label, so page controls are skipped.
    private func dialogButton(_ title: String, file: StaticString = #filePath, line: UInt = #line) -> XCUIElement {
        let predicate = NSPredicate(format: "label == %@ AND NOT (identifier BEGINSWITH %@)", title, "charge.")
        let button = app.buttons.matching(predicate).firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 3), "Expected the dialog button: \(title)", file: file, line: line)
        return button
    }

    private func tapTab(_ name: String, file: StaticString = #filePath, line: UInt = #line) {
        let button = app.buttons["tab.\(name)"]
        let hittable = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND hittable == true"), object: button)
        XCTAssertEqual(XCTWaiter.wait(for: [hittable], timeout: 5), .completed,
                       "Tab must remain reachable: \(name)", file: file, line: line)
        button.tap()
        let selected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "selected == true"), object: button)
        XCTAssertEqual(XCTWaiter.wait(for: [selected], timeout: 5), .completed,
                       "Tab did not open: \(name)", file: file, line: line)
    }

    private func openSchedule() {
        let schedule = app.buttons["home.schedule"]
        reveal(schedule)
        schedule.tap()
        waitForPage("Current schedule.")
    }

    private func openPlan() {
        tapTab("plan")
        waitForPage("Your charging rhythm.")
    }

    private func openWeeklyEditor() {
        let weekly = app.buttons["schedule.weekly"]
        reveal(weekly)
        weekly.tap()
        waitForPage("Make it your routine.")
        reveal(app.sliders["schedule.target"])
    }

    @discardableResult
    private func selectMaximumTarget(_ slider: XCUIElement,
                                     file: StaticString = #filePath, line: UInt = #line) -> String {
        reveal(slider)
        slider.adjust(toNormalizedSliderPosition: 1.0)
        let value = app.staticTexts["schedule.targetValue"]
        let maximum = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "100%"), object: value)
        XCTAssertEqual(XCTWaiter.wait(for: [maximum], timeout: 3), .completed,
                       "The maximum slider position must select 100%", file: file, line: line)
        XCTAssertEqual(value.label, "100%", file: file, line: line)
        return value.label
    }

    private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testFreshLaunchUsesWaitingDemoAndAllFiveTabsAreReachable() {
        launch()
        XCTAssertTrue(app.staticTexts["Waiting for a better charging time"].exists)
        assertNoDemoWording()
        for tab in ["charge", "plan", "insights", "records", "home"] {
            tapTab(tab)
            screenshot("tab-\(tab)")
        }
        reveal(app.buttons["home.chargeNow"])
        XCTAssertEqual(app.buttons["home.chargeNow"].label, "Charge now")
    }

    func testHomeChargeNowLabelAndHeadlightMatchEveryState() {
        for state in ["waiting", "charging", "paused", "complete", "unplugged", "offline"] {
            launch(state: state)
            let button = app.buttons["home.chargeNow"]
            reveal(button)
            XCTAssertEqual(button.label, "Charge now", "Home CTA changed for \(state)")
            assertNoDemoWording()
            let car = app.descendants(matching: .any)["cybertruck.model"].firstMatch
            reveal(car)
            XCTAssertTrue(car.exists)
            XCTAssertTrue(car.label.contains(state == "charging" ? "illuminated while charging" : "off"))
            XCTAssertGreaterThan(car.frame.width, 80)
            XCTAssertGreaterThan(car.frame.height, 40)
            XCTAssertLessThanOrEqual(car.frame.maxX, app.frame.maxX)
            screenshot("home-\(state)")
            app.terminate()
        }
    }

    func testHomeShortcutsOpenConnectionInsightsAndPlan() {
        launch()
        let connection = app.buttons["home.connection"]
        reveal(connection)
        connection.tap()
        let sync = app.buttons["connection.syncNow"]
        XCTAssertTrue(sync.waitForExistence(timeout: 5), "The connection pill must open the connection details")
        screenshot("home-connection")
        app.buttons["sheet.done"].tap()
        waitForDismissal(sync)

        let savings = app.buttons["home.savings"]
        reveal(savings)
        savings.tap()
        waitForSelected(app.buttons["tab.insights"])

        tapTab("home")
        let rate = app.buttons["home.rate"]
        reveal(rate)
        rate.tap()
        waitForSelected(app.buttons["tab.plan"])
        waitForPage("Your charging rhythm.")

        tapTab("home")
        let viewPlan = app.buttons["home.viewPlan"]
        reveal(viewPlan)
        viewPlan.tap()
        waitForSelected(app.buttons["tab.plan"])
        waitForPage("Your charging rhythm.")
    }

    func testTodayEditDoesNotChangeWeeklyRoutineAndCancelDiscardsDraft() throws {
        launch()
        openSchedule()
        let today = app.buttons["schedule.today"]
        reveal(today)
        today.tap()
        waitForPage("Just this departure.")
        let slider = app.sliders["schedule.target"]
        reveal(slider)
        let todayTarget = selectMaximumTarget(slider)
        let save = app.buttons["schedule.save"]
        reveal(save)
        save.tap()
        waitForDismissal(app.navigationBars.buttons["Back"])
        let summaryPage = try XCTUnwrap(visiblePage(), "The current schedule must remain visible after saving")
        let savedTarget = summaryPage.staticTexts[todayTarget].firstMatch
        reveal(savedTarget)
        XCTAssertEqual(savedTarget.label, todayTarget)
        reveal(app.buttons["schedule.weekly"])
        screenshot("schedule-today-saved")
        openWeeklyEditor()
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, "80%")
        selectMaximumTarget(app.sliders["schedule.target"])
        let back = app.navigationBars.buttons["Back"]
        XCTAssertTrue(back.isHittable)
        back.tap()
        waitForDismissal(back)
        openWeeklyEditor()
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, "80%")
        screenshot("weekly-draft-cancelled")
    }

    func testWeeklyEditSavesAndCanBeReopened() {
        launch()
        openSchedule()
        openWeeklyEditor()
        let target = app.sliders["schedule.target"]
        let savedTarget = selectMaximumTarget(target)
        let save = app.buttons["schedule.save"]
        reveal(save)
        save.tap()
        waitForDismissal(app.navigationBars.buttons["Back"])
        openWeeklyEditor()
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, savedTarget)
    }

    func testPlanTabWeeklyRoutineSavesAndUnsavedEditsAreDropped() {
        launch()
        openPlan()
        let slider = app.sliders["schedule.target"]
        reveal(slider)
        XCTAssertTrue(app.descendants(matching: .any)["plan.edit"].firstMatch.exists,
                      "The weekly routine card edits in place on the Plan tab")
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, "80%")

        // An edit that is never saved is only a draft: leaving the tab and coming back restores the saved routine.
        selectMaximumTarget(slider)
        tapTab("charge")
        openPlan()
        reveal(app.sliders["schedule.target"])
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, "80%")
        screenshot("plan-weekly-draft-dropped")

        // A saved edit confirms in place and is still there after leaving and returning.
        let savedTarget = selectMaximumTarget(app.sliders["schedule.target"])
        let save = app.buttons["schedule.save"]
        reveal(save)
        save.tap()
        waitForLabel(save, "Saved to your home controller")
        screenshot("plan-weekly-saved")
        tapTab("charge")
        openPlan()
        reveal(app.sliders["schedule.target"])
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, savedTarget)
    }

    func testPlanTabJustThisDepartureLeavesTheWeeklyRoutineUntouched() {
        launch()
        openPlan()
        let open = app.buttons["plan.once.open"]
        reveal(open)

        // Cancel closes the one-time editor without saving anything.
        open.tap()
        let cancel = app.buttons["plan.once.cancel"]
        reveal(cancel)
        cancel.tap()
        waitForDismissal(cancel)
        XCTAssertFalse(app.buttons["schedule.removeToday"].exists)

        reveal(open)
        open.tap()
        let hundred = app.buttons["plan.once.target.100"]
        reveal(hundred)
        hundred.tap()
        waitForSelected(hundred)
        let save = app.buttons["plan.once.save"]
        reveal(save)
        save.tap()
        waitForDismissal(save)
        let remove = app.buttons["schedule.removeToday"]
        XCTAssertTrue(remove.waitForExistence(timeout: 5), "The one-time change must be listed with a way to remove it")
        screenshot("plan-once-saved")

        // The weekly routine above keeps its own target.
        reveal(app.sliders["schedule.target"])
        XCTAssertEqual(app.staticTexts["schedule.targetValue"].label, "80%")

        reveal(remove)
        remove.tap()
        waitForDismissal(remove)
    }

    func testDemoChargeCanBeStartedPausedAndResumed() {
        launch()
        let chargeNow = app.buttons["home.chargeNow"]
        reveal(chargeNow)
        chargeNow.tap()
        // Charge now opens a bottom sheet with its own buttons, not a system confirmation dialog.
        let confirm = app.buttons["home.chargeNow.confirm"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["home.chargeNow.wait"].exists)
        screenshot("home-charge-now-sheet")
        confirm.tap()
        waitForDismissal(confirm)
        waitForLabel(app.staticTexts["home.chargeStatus"], "Charging with good energy")
        tapTab("charge")
        waitForPage("Your charge.")
        let pause = app.buttons["charge.pause"]
        reveal(pause)
        pause.tap()
        let resume = app.buttons["charge.resume"]
        reveal(resume)
        resume.tap()
        reveal(pause)
    }

    func testChargeNowSheetCanBeDismissedWithoutStartingACharge() {
        launch()
        let chargeNow = app.buttons["home.chargeNow"]
        reveal(chargeNow)
        chargeNow.tap()
        let wait = app.buttons["home.chargeNow.wait"]
        XCTAssertTrue(wait.waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["home.chargeNow.confirm"].exists)
        wait.tap()
        waitForDismissal(wait)
        waitForLabel(app.staticTexts["home.chargeStatus"], "Waiting for a better charging time")
    }

    func testHomeControlsPauseResumeAndOpenTheChargeTab() {
        launch(state: "charging")
        let pause = app.buttons["home.pause"]
        reveal(pause)
        pause.tap()
        let resume = app.buttons["home.resume"]
        XCTAssertTrue(resume.waitForExistence(timeout: 5), "Pausing swaps Pause for Resume in place")
        waitForLabel(app.staticTexts["home.chargeStatus"], "Charging paused")
        screenshot("home-paused")
        reveal(resume)
        resume.tap()
        XCTAssertTrue(pause.waitForExistence(timeout: 5), "Resuming swaps Resume back for Pause")
        waitForLabel(app.staticTexts["home.chargeStatus"], "Charging with good energy")
        let manage = app.buttons["home.manage"]
        reveal(manage)
        manage.tap()
        waitForSelected(app.buttons["tab.charge"])
        waitForPage("Your charge.")
    }

    func testChargeTabStartsImmediatelyAndEndAsksFirst() {
        launch()
        tapTab("charge")
        waitForPage("Your charge.")
        let start = app.buttons["charge.start"]
        reveal(start)
        start.tap()
        // Starting needs no confirmation: charging controls are there straight away.
        let pause = app.buttons["charge.pause"]
        XCTAssertTrue(pause.waitForExistence(timeout: 5), "Charge now on the Charge tab must start charging at once")
        screenshot("charge-started")

        // Keep charging closes the dialog and leaves the charge running.
        let end = app.buttons["charge.end"]
        reveal(end)
        end.tap()
        let keep = dialogButton("Keep charging")
        keep.tap()
        waitForDismissal(keep)
        XCTAssertTrue(app.buttons["charge.pause"].exists)

        // End session ends it, and the page offers to start again.
        reveal(end)
        end.tap()
        let confirmEnd = dialogButton("End session")
        confirmEnd.tap()
        waitForDismissal(confirmEnd)
        XCTAssertTrue(app.buttons["charge.start"].waitForExistence(timeout: 5), "Ending the session returns to Charge now")
    }

    func testControllerSetupIsReachableWithoutPretendingToPair() {
        launch()
        app.buttons["home.settings"].tap()
        let connect = app.buttons["settings.connectController"]
        waitForPage("Settings")
        reveal(connect)
        connect.tap()
        waitForPage("Meet your home controller.")
        let host = app.textFields["setup.host"]
        reveal(host)
        XCTAssertTrue(host.exists)
        let key = app.secureTextFields["setup.lanKey"]
        reveal(key)
        XCTAssertTrue(key.exists)
        let pair = app.buttons["setup.pairLAN"]
        reveal(pair)
        XCTAssertFalse(pair.isEnabled, "A private setup key is required before first-time LAN pairing")
        screenshot("controller-setup-entry")
        XCTAssertFalse(app.staticTexts["You're connected."].exists)
        XCTAssertFalse(app.buttons["setup.finish"].exists)
    }

    func testSettingsHasANewTitleAndOpensTheChargingSchedule() {
        launch()
        app.buttons["home.settings"].tap()
        waitForPage("Settings")
        XCTAssertFalse(app.staticTexts["Make Sparc yours."].exists)
        let schedule = app.buttons["settings.schedule"]
        reveal(schedule)
        schedule.tap()
        waitForPage("Current schedule.")
        screenshot("settings-schedule")
    }

    func testDeveloperOptionsAreHiddenInSettingsByDefault() {
        launch()
        app.buttons["home.settings"].tap()
        waitForPage("Settings")
        // The version line is the last thing on the page; the developer card would sit just above it.
        reveal(app.buttons["settings.version"])
        for identifier in ["settings.demoMode", "settings.virtualCar", "settings.forgetController"] {
            XCTAssertFalse(app.descendants(matching: .any)[identifier].firstMatch.exists, "\(identifier) must stay hidden")
        }
        screenshot("settings-default")
    }

    func testDeveloperOptionsAppearInSettingsWithTheLaunchArgument() {
        launch(extra: ["-SPARCShowDeveloper"])
        app.buttons["home.settings"].tap()
        waitForPage("Settings")
        for identifier in ["settings.demoMode", "settings.virtualCar"] {
            let toggle = app.descendants(matching: .any)[identifier].firstMatch
            reveal(toggle)
            XCTAssertTrue(toggle.exists, "\(identifier) must show with -SPARCShowDeveloper")
        }
        screenshot("settings-developer-options")
    }

    func testAccessibilityTextKeepsTabsAndScheduleReachable() {
        launch(extra: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"])
        for tab in ["home", "charge", "plan", "insights", "records"] {
            XCTAssertTrue(app.buttons["tab.\(tab)"].isHittable)
        }
        openPlan()
        reveal(app.sliders["schedule.target"])
        reveal(app.buttons["schedule.save"])
        reveal(app.buttons["plan.once.open"])
        screenshot("plan-accessibility-text")
        tapTab("home")
        openSchedule()
        reveal(app.buttons["schedule.today"])
        screenshot("schedule-accessibility-text")
        app.buttons["schedule.today"].tap()
        waitForPage("Just this departure.")
        reveal(app.sliders["schedule.target"])
        screenshot("schedule-editor-accessibility-text")
    }
}
