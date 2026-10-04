import SwiftUI

/// Colors, type and motion shared by every screen.
///
/// Colors live in `Assets.xcassets` so they have one definition each. The coverage colors follow
/// the HLD's display table (docs/05 section 2): gray not seen, amber seen, green covered.
enum Palette {
    /// Scrims and text on light surfaces.
    static let ink = Color("Ink")
    /// Text on dark scrims over the camera.
    static let chalk = Color("Chalk")
    /// Primary actions, the walking path and the aiming ring: blue always means "go here, do this".
    /// One value in both appearances: the lighter blue a dark variant would use drops white
    /// button labels below 4.5:1, and camera screens run in dark mode.
    static let signal = Color("Signal")
    /// The primary button's fill: Signal's hue (220 degrees), a little darker. White on Signal
    /// is about 4.9:1 on paper, but the audit rated "See it on your wall" as only nearly
    /// passing; white on this is about 6.2:1.
    static let signalFill = Color(red: 26 / 255.0, green: 88 / 255.0, blue: 214 / 255.0)
    /// A reply button on the dark instruction card: what 14% white over `ink` (#0B1220) looks like,
    /// as a solid colour (#2D333F), so the button's contrast with `chalk` text (about 11:1) doesn't
    /// depend on how the translucent layer is composited over the camera behind the card. The
    /// accessibility audit rejected the translucent version on CI's LiDAR replay.
    static let replyFill = Color(red: 45 / 255.0, green: 51 / 255.0, blue: 63 / 255.0)
    static let unseen = Color("CoverageUnseen")
    static let seen = Color("CoverageSeen")
    static let covered = Color("CoverageCovered")
    /// "I can't get there": neither fog nor evidence, so it gets its own slate and a hatch.
    static let skipped = Color("CoverageSkipped")
    /// Depth saw something in front of the wall: violet, a hue no other state uses, drawn as a
    /// dashed outline over a dark veil. The outline, not the hue, is what tells it apart in
    /// grayscale, where this violet sits close to the covered green.
    static let hidden = Color("CoverageHidden")
    static let caution = Color("Caution")
    static let danger = Color("Danger")
    /// Secondary text on the light screens. The system's secondary gray falls just under 4.5:1
    /// on Canvas, so this is a touch darker (lighter in dark mode).
    static let muted = Color("Muted")
    /// Blue text and icons on light or tinted backgrounds, where Signal itself is under 4.5:1.
    static let signalText = Color("SignalText")
    static let surface = Color("Surface")
    static let canvas = Color("Canvas")

    static func cell(_ state: CellState) -> Color {
        switch state {
        case .unseen: unseen
        case .seen: seen
        case .covered: covered
        case .skipped: skipped
        case .hidden: hidden
        }
    }

    /// The dash of a hidden stretch's outline, on the camera and on the wall map alike.
    static let hiddenDash: [CGFloat] = [5, 4]

    /// Darker outcome colors for text and icons on the light result screen, where the bright
    /// coverage colors fall below 4.5:1.
    static let passInk = Color(red: 0.08, green: 0.50, blue: 0.26)
    static let reviewInk = Color(red: 0.56, green: 0.36, blue: 0.0)
    static let failInk = Color(red: 0.76, green: 0.16, blue: 0.12)

    static func outcome(_ outcome: CheckOutcome) -> Color {
        switch outcome {
        case .pass: covered
        case .unsure: caution
        case .fail: danger
        }
    }

    /// A result's clearance zone in AR. Where the server's sweep passes, the proposal's blue
    /// rather than `covered` green, which read as ground confirmed clear: the checks pass there
    /// on recorded space, not on space anyone confirmed (B17).
    static func zone(_ outcome: CheckOutcome) -> Color {
        outcome == .pass ? signal : self.outcome(outcome)
    }

    static func outcomeInk(_ outcome: CheckOutcome) -> Color {
        switch outcome {
        case .pass: passInk
        case .unsure: reviewInk
        case .fail: failInk
        }
    }
}

enum Typeface {
    /// The one instruction on a camera screen. Rounded, heavy and large so it reads at arm's
    /// length in sun.
    static let instruction = Font.system(.title2, design: .rounded, weight: .bold)
    static let hint = Font.system(.body, design: .rounded, weight: .medium)
    static let screenTitle = Font.system(.largeTitle, design: .rounded, weight: .bold)
    static let sectionTitle = Font.system(.title3, design: .rounded, weight: .semibold)
    static let button = Font.system(.headline, design: .rounded, weight: .bold)
    static let caption = Font.system(.footnote, design: .rounded, weight: .semibold)
}

enum Metrics {
    /// Apple's minimum touch target; primary buttons over the camera are taller than this
    /// because the homeowner holds the phone in one hand, often while walking.
    static let minTarget: CGFloat = 44
    static let primaryButtonHeight: CGFloat = 58
    static let cardRadius: CGFloat = 22
    static let edge: CGFloat = 16
}

enum Motion {
    /// Screen-to-screen crossfades and card swaps.
    static let screen = Animation.easeOut(duration: 0.25)
    /// Instruction text swaps: fast enough to never delay reading the new line.
    static let text = Animation.easeOut(duration: 0.2)
    /// Pins, the meter ring and the battery reveal: critically damped by default so nothing
    /// wobbles over a live camera; a small bounce only on the pin the homeowner just placed.
    static let settle = Animation.spring(duration: 0.4, bounce: 0)
    static let pin = Animation.spring(duration: 0.45, bounce: 0.25)
    /// Fog lifting off a cell. Slower than UI chrome on purpose: it is the moment the homeowner
    /// learns "the phone saw that", and a longer ease-out reads as mist, not a flicker.
    static let fogLift: TimeInterval = 0.7
}
