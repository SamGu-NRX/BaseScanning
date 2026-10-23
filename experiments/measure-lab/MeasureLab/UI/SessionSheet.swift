import MeasureGeometry
import SwiftUI

/// What the session holds, the LiDAR comparison switch, sharing, and starting over.
struct SessionSheet: View {
    let session: LabSession
    /// Starts a new session; the argument is whether it records LiDAR scene depth.
    let onNewSession: (Bool) -> Void
    @State private var isConfirmingNewSession = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Session", value: session.manifest.session.id)
                    LabeledContent("Phone", value: session.manifest.session.deviceModel)
                    LabeledContent("LiDAR", value: session.lidarAvailable ? "Yes" : "No")
                    LabeledContent("Mesh reconstruction", value: session.meshReconstructionSupported ? "Supported" : "Not supported")
                    LabeledContent("Keyframes", value: "\(session.manifest.keyframes.count)")
                    LabeledContent("Refused taps", value: "\(session.manifest.refusals.count)")
                } footer: {
                    Text("The no-LiDAR question needs a phone without LiDAR. On a LiDAR phone ARKit still uses the sensor for planes even with depth off.")
                }

                if session.lidarAvailable {
                    Section {
                        Toggle("Record LiDAR depth", isOn: Binding(
                            get: { session.sceneDepthEnabled },
                            set: { enabled in onNewSession(enabled) }
                        ))
                        .disabled(!session.canChangeSceneDepth)
                    } footer: {
                        Text("For comparison runs only. Set it before the first tap; changing it starts a new session.")
                    }
                }

                Section("Measurements") {
                    if session.manifest.measurements.isEmpty {
                        Text("None yet").foregroundStyle(.secondary)
                    }
                    ForEach(session.manifest.measurements) { measurement in
                        MeasurementRow(measurement: measurement)
                    }
                }

                Section("Walls") {
                    if session.manifest.walls.isEmpty {
                        Text("None yet").foregroundStyle(.secondary)
                    }
                    ForEach(session.manifest.walls) { wall in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(wall.id) · \(wall.contacts.joined(separator: " → "))").font(.headline)
                            Text(Format.length(wall.length)).monospacedDigit()
                            ForEach(wall.validations, id: \.point) { check in
                                ValidationRow(check: check, contact: session.manifest.points.first { $0.id == check.point })
                            }
                        }
                        .font(.subheadline)
                    }
                }

                Section {
                    if session.folder != nil {
                        ShareLink(
                            item: SessionArchive(session: session),
                            preview: SharePreview("Measure Lab session \(session.manifest.session.id)")
                        ) {
                            Label("Share session (.zip)", systemImage: "square.and.arrow.up")
                        }
                    }
                    Button("New session", systemImage: "plus.circle") {
                        isConfirmingNewSession = true
                    }
                } footer: {
                    Text("Sessions are also in the Files app under On My iPhone › Measure Lab › Sessions.")
                }
            }
            .navigationTitle("Session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .confirmationDialog("Start a new session?", isPresented: $isConfirmingNewSession, titleVisibility: .visible) {
                Button("New session") {
                    onNewSession(session.sceneDepthEnabled)
                    dismiss()
                }
            } message: {
                Text("This one stays saved. The new one starts a fresh AR map, so points from this session can't be measured against it.")
            }
        }
    }
}

private struct MeasurementRow: View {
    let measurement: MeasurementRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(measurement.id) · \(measurement.from) → \(measurement.to) · \(title)")
                .font(.headline)
            if let value = measurement.values[measurement.compared] {
                Text(Format.length(value)).monospacedDigit()
            }
            if let tape = measurement.tape, let error = measurement.errorMeters {
                Text("Tape \(Format.length(tape.meters)) · error \(Format.signedInches(error))")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
        }
        .font(.subheadline)
    }

    private var title: String {
        MeasuredQuantity(rawValue: measurement.compared)?.title.lowercased() ?? measurement.compared
    }
}

/// One saved check on a wall. A contact within tolerance confirms the wall only when it has no
/// warning of its own (`WallCheck.outcome`), so the row names the warnings instead of "passes".
private struct ValidationRow: View {
    let check: WallRecord.Validation
    /// The check contact's point record, nil if the session has no such point.
    let contact: PointRecord?

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(check.point): \(Format.inches(check.residual)) off · \(verdict)")
                .monospacedDigit()
                .foregroundStyle(outcome == .confirmed ? Color.secondary : Theme.warning)
            if outcome == .unconfirmed {
                Text(reasons)
                    .font(.caption)
                    .foregroundStyle(Theme.warning)
            }
        }
    }

    private var outcome: WallCheck.Outcome {
        WallCheck.outcome(passes: check.passes, contactWarnings: contact?.flags)
    }

    private var verdict: String {
        switch outcome {
        case .confirmed: "confirms"
        case .unconfirmed: "within tolerance, not confirmed"
        case .failed: "fails"
        }
    }

    private var reasons: String {
        guard let contact else { return "No record of this check point" }
        return contact.flags.map(\.message).joined(separator: "; ")
    }
}
