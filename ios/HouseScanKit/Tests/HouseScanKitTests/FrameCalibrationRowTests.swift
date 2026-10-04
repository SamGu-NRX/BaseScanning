import HouseScanKit
import simd
import Testing

@Suite struct FrameCalibrationRowTests {
    /// ARKit's intrinsics, column-major: fx and fy on the diagonal, cx and cy in the last column.
    static func intrinsics(fx: Float, fy: Float, cx: Float, cy: Float) -> simd_float3x3 {
        simd_float3x3(SIMD3(fx, 0, 0), SIMD3(0, fy, 0), SIMD3(cx, cy, 1))
    }

    /// A trajectory row as the recorder writes it: t, tracking state and reason, then the pose's
    /// 16 values column by column.
    static func trajectoryRow(_ t: Double, x: Float) -> [Double] {
        var m = matrix_identity_float4x4
        m.columns.3 = SIMD4(x, 1.5, 0, 1)
        let columns = [m.columns.0, m.columns.1, m.columns.2, m.columns.3]
        return [t, 0, 0] + columns.flatMap { [Double($0.x), Double($0.y), Double($0.z), Double($0.w)] }
    }

    @Test func theRowIsTheFramesOwnValuesInTheirOriginalUnits() {
        // Every value distinct, so a swapped column or axis would show.
        let k = Self.intrinsics(fx: 1445.25, fy: 1446.5, cx: 961.75, cy: 719.125)
        #expect(FrameCalibrationRow.row(t: 12.345, intrinsics: k, imageWidth: 1920, imageHeight: 1440)
            == [12.345, 1445.25, 1446.5, 961.75, 719.125, 1920, 1440])
        // Off-diagonal entries (skew, the bottom row) don't leak into the row.
        var skewed = k
        skewed.columns.1.x = 3
        skewed.columns.0.z = 7
        #expect(FrameCalibrationRow.row(t: 1, intrinsics: skewed, imageWidth: 1920, imageHeight: 1440)
            == FrameCalibrationRow.row(t: 1, intrinsics: k, imageWidth: 1920, imageHeight: 1440))
    }

    /// Calibration and resolution change from frame to frame independently; each pose gets the
    /// calibration of the frame with its own t, through #192's join.
    @Test func eachPoseJoinsItsOwnFramesCalibration() throws {
        let frames: [(t: Double, k: SIMD4<Float>, size: SIMD2<Double>)] = [
            (100.000, SIMD4(1445, 1445, 960, 720), SIMD2(1920, 1440)),
            (100.017, SIMD4(1450, 1449, 958, 722), SIMD2(1920, 1440)),
            (100.033, SIMD4(1450, 1449, 958, 722), SIMD2(1280, 960)),
            (100.050, SIMD4(963, 964, 640, 480), SIMD2(1280, 960)),
        ]
        let trajectory = frames.enumerated().map { Self.trajectoryRow($1.t, x: Float($0)) }
        let intrinsics = frames.map {
            FrameCalibrationRow.row(
                t: $0.t, intrinsics: Self.intrinsics(fx: $0.k.x, fy: $0.k.y, cx: $0.k.z, cy: $0.k.w), imageWidth: $0.size.x, imageHeight: $0.size.y)
        }
        // Rows out of order, as nothing promises they arrive sorted.
        let poses = Packet04Streams.poseRows(trajectory: trajectory, intrinsics: intrinsics.reversed(), tracking: PacketTracking.init(recorderState:reason:))
        #expect(poses.map(\.t) == frames.map(\.t))
        #expect(poses.map(\.intrinsics) == frames.map(\.k))
        #expect(poses.map { $0.cameraToWorld.columns.3.x } == [0, 1, 2, 3])
        #expect(intrinsics.map { [$0[5], $0[6]] } == frames.map { [$0.size.x, $0.size.y] })
    }

    /// A pose whose frame has no calibration row is left out, and a row at another t, however
    /// close, isn't borrowed for it.
    @Test func aPoseWithoutItsOwnRowIsOmittedNotGuessed() {
        let k = Self.intrinsics(fx: 1445, fy: 1445, cx: 960, cy: 720)
        let trajectory = [100.0, 100.5, 101.0].enumerated().map { Self.trajectoryRow($1, x: Float($0)) }
        let intrinsics = [
            FrameCalibrationRow.row(t: 100.0, intrinsics: k, imageWidth: 1920, imageHeight: 1440),
            // 100.5 has no row; 101.0's row is a hair off its pose's t.
            FrameCalibrationRow.row(t: 101.0.nextUp, intrinsics: k, imageWidth: 1920, imageHeight: 1440),
        ]
        let poses = Packet04Streams.poseRows(trajectory: trajectory, intrinsics: intrinsics, tracking: PacketTracking.init(recorderState:reason:))
        #expect(poses.map(\.t) == [100.0])
    }
}
