import Foundation

// Where the result's marks go, shared by the result card's 3D model (`ResultScene3D`) and the AR
// view (`ResultARModel`, `BatteryOverlay`): which spot the camera faces, how the clearance zones
// stack, where a footprint outline sits above them and how it is dashed. Display only; nothing
// here changes where the server put the spot or what it decided.

/// Where the result's marks sit on the wall, shared by the result card's 3D model
/// (`ResultScene3D`) and the AR view (`ResultARModel`, `BatteryOverlay`): which mark a spot
/// gets, where the camera aims, how the clearance zones and the footprint outline stack, and how
/// the outline's dashes run. Display only: nothing here changes where the server put the spot or
/// what it decided.
public enum ResultMarkLayout {
    /// What stands at the spot. Only a clean fit (`ResultReading.spotIsClean`) gets a battery; a
    /// spot that might stand in the meter's working space gets the outline of its footprint, as
    /// on the result card, so no view shows a battery the card doesn't.
    public enum SpotMark: Equatable, Sendable {
        case battery
        case outline
    }

    public static func spotMark(spotIsClean: Bool) -> SpotMark {
        spotIsClean ? .battery : .outline
    }

    /// The height on the wall "See it on your wall" aims at for a spot `batteryHeight` tall: the
    /// middle of a battery, or the ground an outline lies on. The edge chevron and the check that
    /// the AR scene has the spot in view both use it, so they look where the mark is drawn.
    public static func focusHeight(mark: SpotMark, batteryHeight: Float) -> Float {
        switch mark {
        case .battery: return batteryHeight / 2
        case .outline: return 0
        }
    }

    /// The s the camera faces: the middle of the spot, else of the closest spot tried, else the
    /// meter (0). The same spot the camera circles, so a spot round a corner is seen from its front.
    public static func focusS(spot: ClosedRange<Float>?, nearest: ClosedRange<Float>?) -> Float {
        guard let span = spot ?? nearest else { return 0 }
        return (span.lowerBound + span.upperBound) / 2
    }

    /// How far apart stacked clearance zones sit, in meters, so overlapping ones don't flicker.
    public static let zoneStep: Float = 0.003

    /// The height of the clearance zone at `index`, stacked from `base`.
    public static func zoneLift(index: Int, base: Float) -> Float {
        base + Float(index) * zoneStep
    }

    /// The center height of a footprint outline `thickness` tall drawn over `zoneCount` zones
    /// stacked from `base`: its underside one step above the top zone, so no zone covers it or
    /// fights it for depth however many there are, and never below `minimum`.
    public static func outlineLift(zoneCount: Int, base: Float, thickness: Float, minimum: Float = 0) -> Float {
        max(minimum, zoneLift(index: max(zoneCount, 0), base: base) + thickness / 2)
    }

    /// The dashes along an edge `length` long: where each starts, measured from one end, and how
    /// long they are. A dash starts at each end and the gaps stretch to fit, so every corner reads;
    /// an edge too short for two dashes is one solid line.
    public static func dashes(along length: Float, dash: Float, gap: Float) -> (starts: [Float], length: Float) {
        guard length > 0 else { return ([], 0) }
        // No more dashes than fit end to end: more would overlap, which reads as one solid line.
        let count = max(1, min(Int(((length + gap) / (dash + gap)).rounded()), Int(length / dash + 0.001)))
        guard count > 1 else { return ([0], length) }
        let stretched = (length - Float(count) * dash) / Float(count - 1)
        return ((0..<count).map { Float($0) * (dash + stretched) }, dash)
    }
}
