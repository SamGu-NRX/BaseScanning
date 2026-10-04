import Foundation
import HouseScanKit
import Observation

/// The backend choice in Developer options: which remote processing the next scan uses. A scan
/// reads it once, when it starts (`ScanEngine.beginScan`), so a change applies to the next scan.
///
/// Kept in UserDefaults (`ProcessingBackendPreference`). In debug builds, `-processingBackend`
/// keeps it in memory instead, starting at that value, so a UI test's choice ends with its run.
@MainActor
@Observable
final class ProcessingBackendSetting {
    static let shared = ProcessingBackendSetting(options: LaunchOptions())

    private(set) var selection: ProcessingBackend
    /// Who answers a photo-processing scan in this build, for the setting's description. The
    /// engine sets it from its own setup when it starts.
    var photoProcessingAnswers: ProcessingProfile.Answers = .notSetUp("not known yet")
    private let defaults: UserDefaults?

    init(options: LaunchOptions) {
        #if DEBUG
        if let backend = options.processingBackend {
            defaults = nil
            selection = backend
            return
        }
        #endif
        defaults = .standard
        selection = ProcessingBackendPreference.read(.standard)
    }

    func choose(_ backend: ProcessingBackend) {
        selection = backend
        if let defaults { ProcessingBackendPreference.write(backend, to: defaults) }
    }
}
