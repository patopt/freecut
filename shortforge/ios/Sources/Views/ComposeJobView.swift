import SwiftUI

/// The "new shorts" composer. Every field the web form has, so a job queued
/// from the phone is the same job.
struct ComposeJobView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var state: AppState

    var onCreated: () async -> Void

    // Private @State/@Environment members make the memberwise initializer
    // private, and this view is presented from another file.
    init(onCreated: @escaping () async -> Void) {
        self.onCreated = onCreated
    }

    @State private var url = ""
    @State private var count = 6
    @State private var captions = true
    @State private var lengthIndex = 0
    @State private var aspect = "9:16"
    @State private var reframe = "face"
    @State private var captionStyle = ""
    @State private var musicId = ""
    @State private var styles: [CaptionStyle] = []
    @State private var tracks: [MusicTrack] = []
    @State private var busy = false

    /// Same presets as the web Length dropdown.
    private let lengths: [(label: String, min: Double, max: Double)] = [
        ("60–90s", 60, 90), ("45–75s", 45, 75), ("60–120s", 60, 120),
        ("90–180s", 90, 180), ("20–45s", 20, 45),
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("Source") {
                    TextField("YouTube URL", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }

                Section("Output") {
                    Stepper("Clips: \(count)", value: $count, in: 1...20)

                    Picker("Length", selection: $lengthIndex) {
                        ForEach(lengths.indices, id: \.self) { i in
                            Text(lengths[i].label).tag(i)
                        }
                    }

                    Picker("Format", selection: $aspect) {
                        Text("9:16 Vertical").tag("9:16")
                        Text("1:1 Square").tag("1:1")
                        Text("16:9 Wide").tag("16:9")
                    }

                    Picker("Reframe", selection: $reframe) {
                        Text("Face tracking").tag("face")
                        Text("Center crop").tag("center")
                    }
                }

                Section("Captions") {
                    Toggle("Burn in captions", isOn: $captions)
                    if captions {
                        Picker("Style", selection: $captionStyle) {
                            Text("Server default").tag("")
                            ForEach(styles) { Text($0.name).tag($0.id) }
                        }
                    }
                }

                Section {
                    Picker("Background music", selection: $musicId) {
                        Text("None").tag("")
                        ForEach(tracks) { Text($0.name).tag($0.id) }
                    }
                } footer: {
                    Text(tracks.isEmpty
                         ? "Upload MP3s from the web dashboard to use them here."
                         : "Mixed under the original audio with side-chain ducking.")
                }

                Section {
                    Button(action: { Task { await create() } }) {
                        HStack {
                            Spacer()
                            if busy { ProgressView().controlSize(.small) }
                            Text(busy ? "Queueing…" : "Generate shorts").fontWeight(.semibold)
                            Spacer()
                        }
                    }
                    .disabled(busy || url.trimmingCharacters(in: .whitespaces).isEmpty)
                } footer: {
                    Text("The job runs on the server — you can close the app while it works.")
                }
            }
            .navigationTitle("New shorts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await loadOptions() }
        }
    }

    private func loadOptions() async {
        // Best effort: the composer stays usable on an older server that has
        // no preset endpoints.
        let s = (try? await APIClient.shared.captionStyles()) ?? []
        let m = (try? await APIClient.shared.music()) ?? []
        await MainActor.run { styles = s; tracks = m }
    }

    private func create() async {
        busy = true
        let preset = lengths[lengthIndex]
        await state.perform("Could not queue the job") {
            try await APIClient.shared.createJob(
                url: url.trimmingCharacters(in: .whitespaces), count: count,
                captions: captions, minLen: preset.min, maxLen: preset.max,
                aspect: aspect, reframe: reframe,
                captionStyle: captions ? captionStyle : "", musicId: musicId)
        }
        busy = false
        await onCreated()
        dismiss()
    }
}
