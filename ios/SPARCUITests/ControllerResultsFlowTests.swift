import XCTest

final class ControllerResultsFlowTests: XCTestCase {
    private var app: XCUIApplication!
    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--uitesting", "--history-interface-test", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryL"]
        app.launch()
        XCTAssertTrue(app.buttons["tab.insights"].waitForExistence(timeout: 10))
        // Wait for the first controller refresh before navigating away from Home.
        // A SwiftUI launch layout can expose the tab before it accepts its first tap.
        XCTAssertTrue(app.buttons["home.savings"].waitForExistence(timeout: 10))
    }
    override func tearDownWithError() throws { app.terminate() }
    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
    private func openTab(_ name: String) {
        let button = app.buttons["tab.\(name)"]
        let hittable = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true AND hittable == true"), object: button)
        XCTAssertEqual(XCTWaiter.wait(for: [hittable], timeout: 5), .completed)
        button.tap()
        let selected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "selected == true"), object: button)
        XCTAssertEqual(XCTWaiter.wait(for: [selected], timeout: 5), .completed)
    }

    func testRecordsKeepExtraCostAndUnavailableAmountsVisible() {
        openTab("home")
        app.buttons["home.settings"].tap()
        XCTAssertTrue(app.staticTexts["Settings"].waitForExistence(timeout: 5))
        let history = app.buttons["settings.history"]
        for _ in 0..<8 {
            if history.exists && history.isHittable && app.frame.contains(history.frame) { break }
            app.swipeUp()
        }
        XCTAssertTrue(history.isHittable)
        history.tap()
        XCTAssertTrue(app.staticTexts["Price unavailable"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["$0.30 extra"].exists, "A night that cost more says so, in dollars")
        capture("controller-records-missing-price")
        let row = app.buttons["history.session.00000000-0000-4000-8000-000000000002"]
        row.tap()
        XCTAssertTrue(app.staticTexts["Charging session"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Unavailable"].firstMatch.exists)
        capture("controller-session-unavailable")
    }

    func testFiniteScheduleCanBeReviewedAndSavedOnSmallScreens() {
        openTab("plan")
        XCTAssertTrue(app.staticTexts["Your nightly schedule."].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["7 nights · Day-ahead + live prices"].exists)
        capture("controller-seven-night-plan")
        let save = app.buttons["campaign.save"]
        for _ in 0..<8 {
            if save.exists && save.isHittable && save.frame.maxY < app.buttons["tab.home"].frame.minY { break }
            app.swipeUp()
        }
        if !save.exists { print(app.debugDescription) }
        capture("controller-nightly-editor-before-save")
        XCTAssertTrue(save.isHittable)
        capture("controller-nightly-editor-save")
        save.tap()
        XCTAssertTrue(app.buttons["Schedule saved"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["campaign.error"].exists)
    }
}
