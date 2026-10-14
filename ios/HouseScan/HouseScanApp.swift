import SwiftUI

@main
struct HouseScanApp: App {
    /// Runs once per process, before any window exists: deletes the share copies earlier runs
    /// left, whose share sheets can't still be reading them. This run's copies live in its own
    /// session folder (`SavedScansLocation.staging`), so they are never among them.
    init() {
        let staging = SavedScansLocation.staging
        Task.detached(priority: .utility) { staging.removeOtherSessions() }
    }

    private var engineCheckScenario: String? {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "-engineGuidanceCheck"), index + 1 < arguments.count { return arguments[index + 1] }
        #endif
        return nil
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if let scenario = engineCheckScenario {
                    #if DEBUG
                    Text(ScanEngine.checkGuidanceEngine(scenario)).accessibilityIdentifier("engine.check")
                    #endif
                } else if ProcessInfo.processInfo.arguments.contains("-uiDemo") {
                    UIDemo.makeRoot()
                } else {
                    AppRuntime.makeRoot()
                }
            }
            // Compiles the live fog's shaders in the background long before the walk needs them.
            .task { LiveFogSupport.shared.prepare() }
        }
    }
}
