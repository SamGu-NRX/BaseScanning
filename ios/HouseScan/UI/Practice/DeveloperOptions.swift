import HouseScanKit
import SwiftUI

/// The way into the developer options, on the first screen and the photo-processing screen. Only
/// development and TestFlight installs show it (`DeveloperSettings.isAvailable`); an App Store
/// install shows nothing here. While practice meter or photo processing is on it says so, so
/// nobody starts a scan without knowing.
struct DeveloperOptionsButton: View {
    /// The backend of the scan under way, for the sheet to say which one it keeps; nil before a
    /// scan starts.
    let scanBackend: ProcessingBackend?
    @AppStorage(DeveloperSettings.practiceMeterKey) private var practiceMeter = false
    @State private var showing = false
    private let settings = DeveloperSettings.shared
    private let processing = ProcessingBackendSetting.shared

    init(scanBackend: ProcessingBackend? = nil) {
        self.scanBackend = scanBackend
    }

    /// The practice meter's words stay as they were; photo processing adds its own sentence.
    private var spokenLabel: String {
        var parts: [String] = []
        if practiceMeter { parts.append("Practice meter is on.") }
        if processing.selection == .photoProcessing { parts.append("Photo processing is chosen.") }
        return parts.isEmpty ? "Developer options" : (["Developer options."] + parts).joined(separator: " ")
    }

    /// What the chip says next to the wrench, when anything differs from an ordinary scan.
    private var notice: String? {
        let photo = processing.selection == .photoProcessing
        return switch (practiceMeter, photo) {
        case (true, true): "Practice meter on · Photo processing"
        case (true, false): "Practice meter on"
        case (false, true): "Photo processing"
        case (false, false): nil
        }
    }

    var body: some View {
        if settings.isAvailable {
            Button {
                showing = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "wrench.and.screwdriver.fill")
                    if let notice {
                        // Wraps at the largest text sizes rather than dropping out.
                        Text(notice)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .font(Typeface.caption)
                .foregroundStyle(notice != nil ? Palette.ink : Palette.muted)
                .padding(.horizontal, notice != nil ? 10 : 0)
                .padding(.vertical, 5)
                .background {
                    if notice != nil { Capsule().fill(Palette.caution) }
                }
                .frame(minWidth: Metrics.minTarget, minHeight: Metrics.minTarget)
                .contentShape(.rect)
            }
            .buttonStyle(PressableStyle())
            .accessibilityLabel(spokenLabel)
            .accessibilityIdentifier("action.developerOptions")
            .sheet(isPresented: $showing) {
                DeveloperOptionsSheet(environment: settings.environment, scanBackend: scanBackend)
            }
        }
    }
}

/// Practice meter, and which remote processing the next scan uses.
private struct DeveloperOptionsSheet: View {
    var environment: PracticeMeter.InstallEnvironment
    var scanBackend: ProcessingBackend?
    @AppStorage(DeveloperSettings.practiceMeterKey) private var practiceMeter = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("Practice meter", isOn: $practiceMeter)
                        .accessibilityIdentifier("developer.practiceMeter")
                } footer: {
                    Text("For trying the scan where there's no electric meter. Tap any spot on a wall and a sample meter is drawn there. A photo of it stands in for the meter close-up, and the rest of the scan runs for real. Each screen and the scan's stamp say it's practice. Starts with the next scan.")
                }
                ProcessingChoice(scanBackend: scanBackend)
                Section {
                    LabeledContent("Build", value: Self.name(environment))
                }
            }
            .navigationTitle("Developer options")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .accessibilityIdentifier("action.closeDeveloperOptions")
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private static func name(_ environment: PracticeMeter.InstallEnvironment) -> String {
        switch environment {
        case .development: "Development"
        case .testFlight: "TestFlight"
        case .appStore: "App Store"
        case .unknown: "Unknown"
        }
    }
}

/// The two remote backends as a radio list. Both are services off the phone; neither decides on
/// it. A scan keeps the backend it started with, so the footer says which one a scan under way
/// uses and that a change waits for the next scan.
private struct ProcessingChoice: View {
    var scanBackend: ProcessingBackend?
    private let setting = ProcessingBackendSetting.shared

    var body: some View {
        Section {
            ForEach(ProcessingBackend.allCases, id: \.self) { backend in
                let selected = setting.selection == backend
                Button {
                    setting.choose(backend)
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(ProcessingCopy.name(backend))
                                .foregroundStyle(.primary)
                            Text(ProcessingCopy.summary(backend))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            if backend == .photoProcessing, let note = ProcessingCopy.setupNote(setting.photoProcessingAnswers) {
                                Text(note)
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(.primary)
                            }
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        Image(systemName: "checkmark")
                            .font(.body.weight(.semibold))
                            .foregroundStyle(.tint)
                            .opacity(selected ? 1 : 0)
                            .accessibilityHidden(true)
                    }
                    .contentShape(.rect)
                }
                .accessibilityAddTraits(selected ? .isSelected : [])
                .accessibilityIdentifier("developer.processing.\(backend.rawValue)")
            }
        } header: {
            Text("Processing")
        } footer: {
            Text(ProcessingCopy.choiceFooter(scanBackend: scanBackend))
                .accessibilityIdentifier("developer.processing.footer")
        }
    }
}
