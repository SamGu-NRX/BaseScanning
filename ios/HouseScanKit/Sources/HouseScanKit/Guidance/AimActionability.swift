import Foundation

public enum AimCoaching: Equatable, Sendable {
    case initializing
    case slowDown
    case needsTexture
    case tooDark
    /// Frames have been mostly too dark for a long while: waiting won't help, daylight will
    /// (`AimCoachingDebouncer.GateProblem.persistentlyDark`).
    case tooDarkToMeasure
    case holdSteady
    /// Turning or tilting the phone faster than the capture gate keeps photos at, as opposed to
    /// walking too fast (#26).
    case turnSlowly
    case relocalizing
    case trackingLost
    /// The phone stands past an end it can't see back from, so the walk keeps no photos: nothing
    /// in them would count (`CoverageMap.unexploredEndPassed`).
    case pastWallEnd
}

extension AimCoaching {
    public var replacesAim: Bool {
        switch self {
        case .initializing, .relocalizing, .trackingLost, .pastWallEnd: true
        case .slowDown, .needsTexture, .tooDark, .tooDarkToMeasure, .holdSteady, .turnSlowly: false
        }
    }
}

/// Whether the current aim is available, independent of frame delivery or task selection.
public enum AimActionability {
    public enum Screen: Sendable, Equatable, CaseIterable {
        case guidance, tiltUp, question, nextWall, marking
    }

    public static func canAct(on task: GuidanceTask?, coverage: CoverageMap?, screen: Screen,
                              coaching: AimCoaching?, interrupted: Bool) -> Bool {
        guard screen == .guidance, !interrupted, coaching?.replacesAim != true else { return false }
        guard let task, let coverage else { return true }
        switch task {
        case .aimAtGround(let s): return !mostlyHidden(.ground, around: s, coverage: coverage)
        case .aimAtWall(let s): return !mostlyHidden(.wall, around: s, coverage: coverage)
        case .seeBehind: return false
        case .walk, .markEnd, .stepBack, .complete: return true
        }
    }

    /// At least half the open cells in the aim window are hidden, matching see-behind routing.
    public static func mostlyHidden(_ band: SurfaceBand, around s: Float, coverage: CoverageMap) -> Bool {
        let cells = coverage.indices(overlapping: (s - 0.3)...(s + 0.3)).filter {
            coverage.isWithinEnds($0) && coverage.level(band, $0) != .covered && coverage.level(band, $0) != .skipped
        }
        let hidden = cells.filter { coverage.level(band, $0) == .hidden }.count
        return hidden > 0 && hidden * 2 >= cells.count
    }
}
