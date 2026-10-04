import simd

extension CaptureResult {
    /// The local identities a result belongs to. The response carries a run id and an ARKit epoch
    /// but not the session or capture, and epochs repeat ("e1" starts every session), so only the
    /// caller can say which scan a result is for.
    public struct Association: Sendable, Equatable, Hashable {
        /// The app's id for one run of the ARKit session. A new session gets a new id, even when it
        /// reuses the epoch string.
        public var sessionID: String
        public var captureID: String
        /// The run that finalize started for the capture.
        public var runID: String
        /// The ARKit epoch the packet was captured in.
        public var epoch: String

        public init(sessionID: String, captureID: String, runID: String, epoch: String) {
            self.sessionID = sessionID
            self.captureID = captureID
            self.runID = runID
            self.epoch = epoch
        }
    }

    /// Whether the caller knows the server ran real analysis for this result. A stubbed server
    /// returns a result that passes the contract without reconstructing anything, and nothing in
    /// the JSON tells the two apart, so only the caller can grant `.verified`.
    public enum Analysis: Sendable, Equatable {
        case unverified
        case verified
    }

    /// A decoded result kept with the local scan it was fetched for.
    public struct Record: Sendable, Equatable {
        public let response: Response
        public let association: Association
        public let analysis: Analysis

        public init(response: Response, association: Association, analysis: Analysis = .unverified) {
            self.response = response
            self.association = association
            self.analysis = analysis
        }

        /// The recommended box in the meter anchor's frame, or the reason there is none.
        ///
        /// - Parameters:
        ///   - world: the session, capture, finalized run and epoch on screen now.
        ///   - meterAnchor: the meter anchor's anchor-to-world pose, in the same world coordinates
        ///     as the uploaded packet. Attaching the returned box to the anchor then carries it
        ///     through ARKit's later corrections.
        public func placement(in world: Association, meterAnchor: simd_float4x4) -> Placement {
            guard let outcome = response.outcome else { return .unavailable(.noOutcome(response.status)) }
            guard outcome.kind == .eligible else { return .unavailable(.notEligible(outcome.kind)) }
            guard response.status == .complete else { return .unavailable(.statusNotComplete(response.status)) }
            guard analysis == .verified else { return .unavailable(.analysisUnverified) }

            guard world.sessionID == association.sessionID else { return .unavailable(.mismatch(.session)) }
            guard world.captureID == association.captureID else { return .unavailable(.mismatch(.capture)) }
            guard world.runID == association.runID, response.runId == association.runID else {
                return .unavailable(.mismatch(.run))
            }
            guard let epoch = outcome.arkitEpoch,
                  let box = outcome.recommendedPlacement?.boxArkitWorld
            else {
                return .unavailable(outcome.recommendedPlacement == nil ? .noPlacement : .notInARKitWorld)
            }
            guard world.epoch == association.epoch, epoch == association.epoch else {
                return .unavailable(.mismatch(.epoch))
            }

            guard box.pose.count == 16 else { return .unavailable(.malformedBox(.poseCount(box.pose.count))) }
            guard box.size.count == 3 else { return .unavailable(.malformedBox(.sizeCount(box.size.count))) }
            guard (box.pose + box.size).allSatisfy(\.isFinite) else { return .unavailable(.malformedBox(.nonFinite)) }
            guard box.size.allSatisfy({ $0 > 0 }) else { return .unavailable(.malformedBox(.nonPositiveSize)) }
            let worldFromBox = Self.matrix(columnMajor: box.pose)
            // The server divides the world transform's scale out of the pose's rotation and
            // multiplies it into `size` instead. A pose that still carries a scale was built some
            // other way, and using it would scale the box a second time.
            guard Self.isRigid(worldFromBox) else { return .unavailable(.malformedBox(.notRigid)) }

            let worldFromAnchor = Self.double(meterAnchor)
            guard Self.isRigid(worldFromAnchor) else { return .unavailable(.invalidMeterAnchor) }

            let anchorFromBox = Self.float(worldFromAnchor.inverse * worldFromBox)
            let size = SIMD3<Float>(Float(box.size[0]), Float(box.size[1]), Float(box.size[2]))
            // Finite Doubles can overflow Float, and positive sizes can round to zero.
            let columns = [anchorFromBox.columns.0, anchorFromBox.columns.1, anchorFromBox.columns.2, anchorFromBox.columns.3]
            guard columns.allSatisfy({ c in (0..<4).allSatisfy { c[$0].isFinite } }),
                  (0..<3).allSatisfy({ size[$0].isFinite })
            else { return .unavailable(.malformedBox(.nonFinite)) }
            guard (0..<3).allSatisfy({ size[$0] > 0 }) else { return .unavailable(.malformedBox(.nonPositiveSize)) }
            return .box(AnchoredBox(anchorFromBox: anchorFromBox, size: size))
        }

        private static func matrix(columnMajor v: [Double]) -> simd_double4x4 {
            simd_double4x4(
                SIMD4(v[0], v[1], v[2], v[3]),
                SIMD4(v[4], v[5], v[6], v[7]),
                SIMD4(v[8], v[9], v[10], v[11]),
                SIMD4(v[12], v[13], v[14], v[15]))
        }

        private static func double(_ m: simd_float4x4) -> simd_double4x4 {
            simd_double4x4(
                SIMD4<Double>(m.columns.0), SIMD4<Double>(m.columns.1),
                SIMD4<Double>(m.columns.2), SIMD4<Double>(m.columns.3))
        }

        private static func float(_ m: simd_double4x4) -> simd_float4x4 {
            simd_float4x4(
                SIMD4<Float>(m.columns.0), SIMD4<Float>(m.columns.1),
                SIMD4<Float>(m.columns.2), SIMD4<Float>(m.columns.3))
        }

        /// A finite rotation with no scale or shear, a translation, and a bottom row of 0 0 0 1.
        /// The server writes each entry to six decimals, so its rotations are orthonormal to about
        /// 1e-6; 1e-3 accepts that and ARKit's float poses, and rejects a 0.1% scale (whose
        /// squared column length is off by 2e-3).
        private static func isRigid(_ m: simd_double4x4) -> Bool {
            let tolerance = 1e-3
            let columns = [m.columns.0, m.columns.1, m.columns.2, m.columns.3]
            guard columns.allSatisfy({ c in (0..<4).allSatisfy { c[$0].isFinite } }) else { return false }
            let bottom = SIMD4(m.columns.0.w, m.columns.1.w, m.columns.2.w, m.columns.3.w)
            guard simd_reduce_max(simd_abs(bottom - SIMD4(0, 0, 0, 1))) <= tolerance else { return false }
            let r = simd_double3x3(
                SIMD3(m.columns.0.x, m.columns.0.y, m.columns.0.z),
                SIMD3(m.columns.1.x, m.columns.1.y, m.columns.1.z),
                SIMD3(m.columns.2.x, m.columns.2.y, m.columns.2.z))
            let gram = r.transpose * r
            let identity = matrix_identity_double3x3
            for i in 0..<3 where simd_reduce_max(simd_abs(gram[i] - identity[i])) > tolerance { return false }
            return r.determinant > 0
        }
    }

    public enum Placement: Sendable, Equatable {
        case box(AnchoredBox)
        case unavailable(Unavailable)
    }

    public struct AnchoredBox: Sendable, Equatable {
        /// Box centre to meter anchor, rigid. Local +x along the wall, +y up, +z out of the wall.
        public var anchorFromBox: simd_float4x4
        /// Width, height and depth in metres, as the server stated them. The server has already
        /// applied the scene-to-ARKit scale, so the app uses these as they are.
        public var size: SIMD3<Float>
    }

    /// Why a result has no AR placement. The rest of the result stays readable in every case.
    public enum Unavailable: Sendable, Equatable {
        /// The run has not written a result yet.
        case noOutcome(Status)
        /// Manual review, more photos, not eligible, or a kind this app doesn't know.
        case notEligible(OutcomeKind)
        case statusNotComplete(Status)
        /// The caller has not confirmed that real analysis produced the result.
        case analysisUnverified
        /// The result belongs to another session, capture, run or ARKit world than the one on
        /// screen.
        case mismatch(IdentityField)
        /// An eligible outcome without a recommended placement, which breaks the contract.
        case noPlacement
        /// The scene was not built in an ARKit world, so there is no box to anchor.
        case notInARKitWorld
        case malformedBox(BoxProblem)
        /// The caller's meter anchor pose is not finite and rigid.
        case invalidMeterAnchor
    }

    public enum IdentityField: Sendable, Equatable, CaseIterable {
        case session, capture, run, epoch
    }

    public enum BoxProblem: Sendable, Equatable {
        case poseCount(Int)
        case sizeCount(Int)
        case nonFinite
        case nonPositiveSize
        case notRigid
    }
}
