import simd

/// The assumed AC footprint used by clearance export and both renderers. One tap measures
/// position only. Keep the whole square in one wall segment's frame, even across a corner.
public struct ACUnitGeometry: Sendable {
    /// The existing three-foot square assumption from #72, not a measured condenser size.
    public static let assumedSide: Float = 0.9144
    /// Illustrative height retained from the result model, not a clearance measurement.
    public static let displayHeight: Float = 0.8

    public let groundCenter: SIMD3<Float>
    public let along: SIMD3<Float>
    public let outward: SIMD3<Float>

    public init(tap: SIMD3<Float>, along: SIMD3<Float>, outward: SIMD3<Float>, groundY: Float) {
        groundCenter = SIMD3(tap.x, groundY, tap.z)
        self.along = along
        self.outward = outward
    }

    public var size: SIMD3<Float> {
        SIMD3(Self.assumedSide, Self.displayHeight, Self.assumedSide)
    }

    public var boxCenter: SIMD3<Float> {
        groundCenter + SIMD3(0, Self.displayHeight / 2, 0)
    }

    /// Back-left, back-right, front-right, front-left. Never clamp the back to the wall:
    /// doing so would move the center away from the measured tap for nearby units.
    public var footprint: [SIMD3<Float>] {
        let right = along * (Self.assumedSide / 2)
        let front = outward * (Self.assumedSide / 2)
        return [groundCenter - right - front, groundCenter + right - front,
                groundCenter + right + front, groundCenter - right + front]
    }

    public var top: [SIMD3<Float>] {
        footprint.map { $0 + SIMD3(0, Self.displayHeight, 0) }
    }
}
