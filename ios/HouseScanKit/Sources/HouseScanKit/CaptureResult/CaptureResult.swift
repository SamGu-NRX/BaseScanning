import Foundation

/// The capture API's device result, `GET /v1/captures/{id}/result`, written from the server's
/// documented wire contract. The whole body is camelCase.
///
/// Only the fields the app shows or gates on are decoded. The verdict and its URL are left out on
/// purpose: the server masks a reject as manual review for the device, and the app must not read
/// around that mask. Unknown keys are ignored, because the server serves a result even when it has
/// drifted from its own schema. A status or outcome kind the app doesn't know decodes as
/// `.unknown`, so a new server value never hides the message and views that came with it.
public enum CaptureResult {
    public struct Response: Decodable, Sendable, Equatable {
        /// Nil before the server has started a run for the capture.
        public var runId: String?
        public var status: Status
        public var viewsNeeded: [ViewNeeded]
        public var memberActions: [String]
        /// Nil until the run writes its result: while uploading, awaiting files or processing.
        public var outcome: Outcome?
        /// Nil when the server sent no criteria. The app shows none rather than inventing rows.
        public var criteria: [Criterion]?
        /// A signed URL that expires; nil when the run made no preview.
        public var previewUrl: String?
        /// A path on the API, not a full URL.
        public var reviewUrl: String?
    }

    public enum Status: Sendable, Equatable, Decodable {
        case uploading, awaitingFiles, processing, needsViews, complete, manualReview, failed, expired
        case unknown(String)

        public init(rawValue: String) {
            self = switch rawValue {
            case "uploading": .uploading
            case "awaiting_files": .awaitingFiles
            case "processing": .processing
            case "needs_views": .needsViews
            case "complete": .complete
            case "manual_review": .manualReview
            case "failed": .failed
            case "expired": .expired
            default: .unknown(rawValue)
            }
        }

        public init(from decoder: any Decoder) throws {
            self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
        }
    }

    /// The decision shown to the homeowner. It is already masked for the device.
    public struct Outcome: Decodable, Sendable, Equatable {
        public var kind: OutcomeKind
        public var profile: String
        /// Text for the homeowner, shown as written.
        public var message: String
        public var viewsNeeded: [ViewNeeded]
        /// Empty when the server shows a reject as manual review.
        public var reasons: [Reason]
        /// The ARKit world the spatial fields are in. Nil when the scene was not built in one.
        public var arkitEpoch: String?
        public var recommendedPlacement: RecommendedPlacement?
    }

    public enum OutcomeKind: Sendable, Equatable, Decodable {
        case eligible, needsMorePhotos, notEligible, manualReview
        case unknown(String)

        public init(rawValue: String) {
            self = switch rawValue {
            case "eligible": .eligible
            case "needs_more_photos": .needsMorePhotos
            case "not_eligible": .notEligible
            case "manual_review": .manualReview
            default: .unknown(rawValue)
            }
        }

        public init(from decoder: any Decoder) throws {
            self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
        }
    }

    public struct RecommendedPlacement: Decodable, Sendable, Equatable {
        public var wallId: String
        public var startSM: Double
        public var confidence: Double
        /// Nil when the scene was not built in an ARKit world.
        public var boxArkitWorld: BoxArkitWorld?
    }

    /// The box in ARKit world coordinates, as sent. The numbers are checked only when the app asks
    /// for an AR placement (`Record.placement`), so a malformed box withholds AR and leaves the
    /// rest of the result readable.
    public struct BoxArkitWorld: Decodable, Sendable, Equatable {
        /// Box centre to world, 16 numbers column by column. Local +x runs along the wall (left to
        /// right seen from outside), +y up, +z out of the wall.
        public var pose: [Double]
        /// Width, height and depth in ARKit metres.
        public var size: [Double]
    }

    public struct ViewNeeded: Decodable, Sendable, Equatable {
        public var id: String
        /// The server's view kind, kept as text so a new kind still shows its prompt.
        public var kind: String
        public var criteria: [String]
        public var why: String
        public var promptId: String
        /// Rendered text; show it verbatim.
        public var prompt: Prompt
        public var band: String?
        public var wallId: String?
        public var target: String?
        /// Set in the photo flow instead of ARKit anchors.
        public var referenceCrop: ReferenceCrop?
    }

    public struct Prompt: Decodable, Sendable, Equatable {
        public var title: String
        public var body: String
    }

    public struct ReferenceCrop: Decodable, Sendable, Equatable {
        public var image: String
        /// Pixel box in `image`: x0, y0, x1, y1.
        public var boxPx: [Double]
    }

    public struct Reason: Decodable, Sendable, Equatable {
        public var criterion: String
        public var text: String
        public var measuredFt: Double?
        public var plusMinusFt: Double?
        public var thresholdFt: Double?
        public var comparison: String?
    }

    public struct Criterion: Decodable, Sendable, Equatable {
        public var id: String
        public var outcome: CriterionOutcome
        public var unsureCause: String?
        public var measuredFt: Double?
        public var plusMinusFt: Double?
        public var thresholdFt: Double?
        public var coverage: String?
    }

    public enum CriterionOutcome: Sendable, Equatable, Decodable {
        case pass, fail, unsure
        case unknown(String)

        public init(rawValue: String) {
            self = switch rawValue {
            case "pass": .pass
            case "fail": .fail
            case "unsure": .unsure
            default: .unknown(rawValue)
            }
        }

        public init(from decoder: any Decoder) throws {
            self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
        }
    }
}
