import MeasureGeometry
import SwiftUI

/// Picks two points, or a point and a wall, shows every distance that applies, and compares the
/// chosen one with a tape reading.
struct MeasureSheet: View {
    let session: LabSession
    @State private var fromID: String
    @State private var target: LabSession.MeasureTarget?
    @State private var referenceWall: String?
    @State private var compared: MeasuredQuantity?
    @State private var feetText = ""
    @State private var inchesText = ""
    @Environment(\.dismiss) private var dismiss

    init(session: LabSession) {
        self.session = session
        let points = session.manifest.points
        // Default to the two newest points, the usual pair right after marking them.
        _fromID = State(initialValue: points.dropLast().last?.id ?? points.last?.id ?? "")
        let defaultTarget: LabSession.MeasureTarget? = if points.count >= 2, let last = points.last {
            .point(last.id)
        } else if let wall = session.manifest.walls.last {
            .wall(wall.id)
        } else {
            nil
        }
        _target = State(initialValue: defaultTarget)
        _referenceWall = State(initialValue: session.manifest.walls.last?.id)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("From") {
                    Picker("Point", selection: $fromID) {
                        ForEach(session.manifest.points) { point in
                            Text(label(for: point)).tag(point.id)
                        }
                    }
                    .onChange(of: fromID) { _, newID in
                        if case .point(let id) = target, id == newID { target = nil }
                    }
                }
                Section("To") {
                    Picker("Point or wall", selection: $target) {
                        Text("Choose").tag(LabSession.MeasureTarget?.none)
                        ForEach(session.manifest.points.filter { $0.id != fromID }) { point in
                            Text(label(for: point)).tag(LabSession.MeasureTarget?.some(.point(point.id)))
                        }
                        ForEach(session.manifest.walls) { wall in
                            Text("\(wall.id) · wall line").tag(LabSession.MeasureTarget?.some(.wall(wall.id)))
                        }
                    }
                    if case .point = target, !session.manifest.walls.isEmpty {
                        Picker("Along-wall reference", selection: $referenceWall) {
                            ForEach(session.manifest.walls) { wall in
                                Text(wall.id).tag(String?.some(wall.id))
                            }
                        }
                    }
                }
                if !values.isEmpty {
                    Section {
                        ForEach(orderedQuantities, id: \.self) { quantity in
                            Button {
                                compared = quantity
                            } label: {
                                LabeledContent {
                                    Text(Format.length(values[quantity] ?? 0))
                                        .monospacedDigit()
                                } label: {
                                    Label(quantity.title, systemImage: selectedQuantity == quantity ? "largecircle.fill.circle" : "circle")
                                }
                            }
                            .tint(.primary)
                            .accessibilityAddTraits(selectedQuantity == quantity ? .isSelected : [])
                        }
                    } header: {
                        Text("Compare with the tape")
                    } footer: {
                        if !selectedWarnings.isEmpty {
                            Text("Saved as an abstention: \(selectedWarnings.map(\.message).joined(separator: "; ")).")
                                .foregroundStyle(Theme.warning)
                        }
                    }
                    Section {
                        HStack {
                            TextField("Feet", text: $feetText)
                                .keyboardType(.numbersAndPunctuation)
                            Text("ft").foregroundStyle(.secondary)
                            TextField("Inches", text: $inchesText)
                                .keyboardType(.numbersAndPunctuation)
                            Text("in").foregroundStyle(.secondary)
                        }
                        .monospacedDigit()
                    } header: {
                        Text("Tape reading")
                    } footer: {
                        Text(tapeFooter)
                            .foregroundStyle(tapeError == nil ? Color.secondary : Theme.refused)
                    }
                }
            }
            .navigationTitle("Measure")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: save)
                        .disabled(!canSave)
                }
            }
        }
    }

    private var values: [MeasuredQuantity: Double] {
        guard let target else { return [:] }
        if case .point(let id) = target, id == fromID { return [:] }
        return session.values(from: fromID, to: target, referenceWall: referenceWall)
    }

    private var orderedQuantities: [MeasuredQuantity] {
        MeasuredQuantity.allCases.filter { values[$0] != nil }
    }

    /// The chosen quantity, or the first one that applies.
    private var selectedQuantity: MeasuredQuantity? {
        if let compared, values[compared] != nil { return compared }
        return orderedQuantities.first
    }

    private var selectedWarnings: [MeasurementWarning] {
        guard let target, let quantity = selectedQuantity else { return [] }
        return session.warnings(from: fromID, to: target, referenceWall: referenceWall, compared: quantity)
    }

    private var tapeIsEmpty: Bool {
        feetText.trimmingCharacters(in: .whitespaces).isEmpty && inchesText.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var tape: Result<TapeReading, TapeEntryError>? {
        if tapeIsEmpty { return nil }
        return Result { () throws(TapeEntryError) in try TapeReading(feetText: feetText, inchesText: inchesText) }
    }

    private var tapeError: TapeEntryError? {
        if case .failure(let error) = tape { return error }
        return nil
    }

    private var tapeFooter: String {
        switch tape {
        case nil:
            return "Optional. Inches take decimals or fractions, like 3.25 or 3 1/4."
        case .failure(.unreadable(let field, let text)):
            return "Can't read \"\(text)\" as \(field.rawValue). Use a number like 3.25 or 3 1/4."
        case .failure(.empty):
            return "Enter feet, inches or both."
        case .failure(.negativeOrNotFinite):
            return "Enter a positive length."
        case .success(let reading):
            guard let quantity = selectedQuantity, let measured = values[quantity] else { return "" }
            let comparison = TapeComparison(measured: measured, tape: reading.meters)
            return "Tape \(Format.length(reading.meters)). App minus tape: \(Format.signedInches(comparison.error))."
        }
    }

    private var canSave: Bool {
        selectedQuantity != nil && tapeError == nil
    }

    private func save() {
        guard let target, let quantity = selectedQuantity else { return }
        let reading = if case .success(let reading) = tape { reading } else { TapeReading?.none }
        session.addMeasurement(from: fromID, to: target, referenceWall: referenceWall, compared: quantity, tape: reading)
        dismiss()
    }

    private func label(for point: PointRecord) -> String {
        let kind = switch point.kind {
        case .ground: "ground"
        case .wall: "on wall"
        case .twoView: "two-view"
        }
        return session.evidence(for: point).warnings.isEmpty ? "\(point.id) · \(kind)" : "\(point.id) · \(kind) · flagged"
    }
}
