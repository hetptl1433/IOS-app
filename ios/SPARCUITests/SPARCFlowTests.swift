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
            bottom = min(bottom, tab.frame.minY)
            let footer = app.staticTexts["Demo mode. All charging data is sample data."]
            if footer.exists { bottom = min(bottom, footer.frame.minY) }
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
        let content = page.children(matching: .other).firstMatch
        var previousFrame: CGRect?
        var stableSince = Date()
        let stable = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
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
        XCTAssertTrue(app.descendants(matching: .any)["demo.banner"].firstMatch.exists)
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

    func testDemoChargeCanBeStartedPausedAndResumed() {
        launch()
        reveal(app.buttons["home.chargeNow"])
        app.buttons["home.chargeNow"].tap()
        let dialog = app.sheets.firstMatch
        XCTAssertTrue(dialog.waitForExistence(timeout: 3))
        dialog.buttons["Charge now"].tap()
        waitForDismissal(dialog)
        let charging = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "Charging with good energy"),
                                                 object: app.staticTexts["home.chargeStatus"])
        XCTAssertEqual(XCTWaiter.wait(for: [charging], timeout: 5), .completed)
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

    func testControllerSetupIsReachableWithoutPretendingToPair() {
        launch()
        app.buttons["home.settings"].tap()
        let connect = app.buttons["settings.connectController"]
        waitForPage("Make Sparc yours.")
        reveal(connect)
        connect.tap()
        waitForPage("Meet your home controller.")
        screenshot("controller-setup-entry")
        XCTAssertFalse(app.staticTexts["Controller connected"].exists)
    }

    func testAccessibilityTextKeepsTabsAndScheduleReachable() {
        launch(extra: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"])
        for tab in ["home", "charge", "plan", "insights", "records"] {
            XCTAssertTrue(app.buttons["tab.\(tab)"].isHittable)
        }
        openSchedule()
        reveal(app.buttons["schedule.today"])
        screenshot("schedule-accessibility-text")
        app.buttons["schedule.today"].tap()
        waitForPage("Just this departure.")
        reveal(app.sliders["schedule.target"])
        screenshot("schedule-editor-accessibility-text")
    }
}
