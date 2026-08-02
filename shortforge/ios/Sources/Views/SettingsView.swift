import SwiftUI

/// Full parity with the web dashboard's settings, minus the flows that need a
/// desktop browser (OAuth consent, TikTok login) — those live behind the Cloud
/// tab, which is a real browser on the VPS.
struct SettingsView: View {
    @EnvironmentObject private var state: AppState

    @State private var settings = ServerSettings()
    @State private var paused = false
    @State private var reachable: Bool?
    @State private var loading = true
    @State private var busy = false

    @State private var confirmSignOut = false
    @State private var editingServer = false
    @State private var serverDraft = ""

    var body: some View {
        NavigationStack {
            Form {
                serverSection
                queueSection

                Section("Configuration") {
                    NavigationLink { AISettingsView(settings: $settings) } label: {
                        row("AI & transcription", "sparkles",
                            settings.geminiKeySet ? settings.geminiModel : "No Gemini key")
                    }
                    NavigationLink { WatermarkSettingsView(settings: $settings) } label: {
                        row("Watermark", "signature",
                            settings.watermark.enabled
                                ? (settings.watermark.text.isEmpty ? "On" : settings.watermark.text)
                                : "Off")
                    }
                    NavigationLink { DefaultsSettingsView(settings: $settings) } label: {
                        row("Captions & music", "textformat",
                            settings.defaultCaptionStyle.isEmpty ? "Default"
                                                                : settings.defaultCaptionStyle)
                    }
                }

                Section("Infrastructure") {
                    NavigationLink { AccountsView() } label: {
                        row("Publishing accounts", "person.2", "\(state.stats.accounts)")
                    }
                    NavigationLink { VPNView(settings: $settings) } label: {
                        row("VPN", "shield.lefthalf.filled",
                            settings.vpnRotation ? "Auto-rotate on" : "Manual")
                    }
                    NavigationLink { DownloadSettingsView(settings: $settings) } label: {
                        row("YouTube downloads", "arrow.down.circle",
                            settings.useYouTubeCookies ? "Using cookies" : "No cookies")
                    }
                }

                Section("Pipeline") {
                    counter("Source videos", state.stats.jobs, "film")
                    counter("Shorts generated", state.stats.shorts, "rectangle.stack")
                    counter("Translated", state.stats.dubs, "globe")
                    counter("Published", state.stats.published, "paperplane")
                    counter("My channels", state.stats.myChannels, "tv")
                    if state.stats.jobsFailed > 0 {
                        Button(role: .destructive) { Task { await clearFailed() } } label: {
                            Label("Clear \(state.stats.jobsFailed) failed", systemImage: "trash")
                        }
                    }
                }

                Section {
                    Button(role: .destructive) { confirmSignOut = true } label: {
                        Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                } footer: {
                    Text("Connecting a Google or TikTok account needs a real browser — "
                         + "use the Cloud tab, which runs one on the VPS.")
                }
            }
            .navigationTitle("Settings")
            .refreshable { await reload() }
            .task { await reload() }
            .confirmationDialog("Sign out of this server?",
                                isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) { Task { await state.signOut() } }
            }
            .alert("Server address", isPresented: $editingServer) {
                TextField("https://…", text: $serverDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                Button("Cancel", role: .cancel) {}
                Button("Save") { Task { await saveServer() } }
            } message: {
                Text("Changing the address signs you out, so you can sign in to the new one.")
            }
        }
    }

    // MARK: - Sections

    private var serverSection: some View {
        Section {
            LabeledContent("Address") {
                Text(state.serverAddress)
                    .font(.footnote).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.head)
            }
            LabeledContent("Status") {
                switch reachable {
                case .some(true):
                    Label("Connected", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(Theme.success).font(.footnote)
                case .some(false):
                    Label("Unreachable", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.danger).font(.footnote)
                case nil:
                    ProgressView().controlSize(.small)
                }
            }
            Button {
                serverDraft = state.serverAddress
                editingServer = true
            } label: {
                Label("Change server address", systemImage: "pencil")
            }
        } header: {
            Text("Server")
        } footer: {
            Text("An ngrok tunnel gets a new address every restart unless you reserve a "
                 + "domain — change it here when it moves.")
        }
    }

    private var queueSection: some View {
        Section {
            Toggle("Pause all queues", isOn: Binding(
                get: { paused },
                set: { value in Task { await setPaused(value) } }
            ))
            .disabled(busy)

            Button(role: .destructive) { Task { await clearQueues() } } label: {
                Label("Clear every queue", systemImage: "xmark.bin")
            }
        } header: {
            Text("Queues")
        } footer: {
            Text("Pausing stops new work from starting and disables auto-publishing. "
                 + "Clearing also deletes everything still waiting.")
        }
    }

    private func row(_ title: String, _ icon: String, _ value: String) -> some View {
        LabeledContent {
            Text(value).font(.footnote).foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.tail)
        } label: {
            Label(title, systemImage: icon)
        }
    }

    private func counter(_ label: String, _ value: Int, _ icon: String) -> some View {
        LabeledContent {
            Text("\(value)").monospacedDigit().foregroundStyle(.secondary)
        } label: {
            Label(label, systemImage: icon)
        }
    }

    // MARK: - Actions

    private func reload() async {
        reachable = await APIClient.shared.probe()
        await state.perform("Could not load settings") {
            let fresh = try await APIClient.shared.settings()
            await MainActor.run { settings = fresh }
        }
        await state.refreshStats()
        paused = state.stats.paused
        loading = false
    }

    private func saveServer() async {
        let draft = serverDraft.trimmingCharacters(in: .whitespaces)
        guard !draft.isEmpty else { return }
        await state.signOut()
        APIClient.setServer(draft)
    }

    private func setPaused(_ value: Bool) async {
        busy = true
        await state.perform("Could not change the queue state") {
            try await APIClient.shared.setPaused(value)
        }
        await reload()
        busy = false
    }

    private func clearQueues() async {
        await state.perform("Could not clear the queues") {
            try await APIClient.shared.clearQueues()
        }
        state.show("Queues cleared.", isError: false)
        await reload()
    }

    private func clearFailed() async {
        await state.perform("Could not clear failed jobs") {
            try await APIClient.shared.clearFailedJobs()
        }
        await state.refreshStats()
    }
}

// MARK: - AI & transcription

struct AISettingsView: View {
    @Binding var settings: ServerSettings
    @EnvironmentObject private var state: AppState

    @State private var geminiKey = ""
    @State private var model = ""
    @State private var whisper = "small"
    @State private var tts = "kokoro"
    @State private var ngrok = ""
    @State private var saving = false

    private let whisperModels = ["tiny", "base", "small", "medium", "large-v3"]
    private let ttsEngines = ["kokoro", "piper", "edge"]

    var body: some View {
        Form {
            Section {
                SecureField(settings.geminiKeySet ? "Key configured — leave blank to keep"
                                                  : "Paste your Gemini API key",
                            text: $geminiKey)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("gemini-2.5-pro", text: $model)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Gemini")
            } footer: {
                Text(settings.geminiKeySet
                     ? "A key is configured. Highlight selection and translation use it."
                     : "Without a key, highlights fall back to even spacing and translation "
                       + "keeps the original language.")
            }

            Section {
                Picker("Model", selection: $whisper) {
                    ForEach(whisperModels, id: \.self) { Text($0).tag($0) }
                }
            } header: {
                Text("Transcription")
            } footer: {
                Text("Bigger is more accurate and much slower. On a 4 GB box, "
                     + "'small' is the practical ceiling for long videos.")
            }

            Section("Voice") {
                Picker("TTS engine", selection: $tts) {
                    ForEach(ttsEngines, id: \.self) { Text($0.capitalized).tag($0) }
                }
            }

            Section {
                SecureField(settings.ngrokTokenSet ? "Token configured — leave blank to keep"
                                                   : "ngrok auth token",
                            text: $ngrok)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Tunnel")
            }
        }
        .navigationTitle("AI & transcription")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await save() } } label: {
                    if saving { ProgressView().controlSize(.small) }
                    else { Text("Save").fontWeight(.semibold) }
                }
                .disabled(saving)
            }
        }
        .onAppear {
            model = settings.geminiModel
            whisper = settings.whisperModel
            tts = settings.ttsEngine
        }
    }

    private func save() async {
        saving = true
        // Secrets are only sent when non-empty: the server treats a blank value
        // as "keep what you have", and overwriting a good key with "" would be
        // an easy way to break the whole pipeline from a phone.
        var patch: [String: Any] = [
            "gemini_model": model, "whisper_model": whisper, "tts_engine": tts,
        ]
        if !geminiKey.isEmpty { patch["gemini_api_key"] = geminiKey }
        if !ngrok.isEmpty { patch["ngrok_authtoken"] = ngrok }

        await state.perform("Could not save") {
            try await APIClient.shared.updateSettings(patch)
            let fresh = try await APIClient.shared.settings()
            await MainActor.run { settings = fresh }
        }
        geminiKey = ""; ngrok = ""
        state.show("Saved.", isError: false)
        saving = false
    }
}

// MARK: - Watermark

struct WatermarkSettingsView: View {
    @Binding var settings: ServerSettings
    @EnvironmentObject private var state: AppState

    @State private var enabled = false
    @State private var text = ""
    @State private var position = "bottom-right"
    @State private var size = 3.0
    @State private var opacity = 0.35
    @State private var saving = false

    var body: some View {
        Form {
            Section {
                Toggle("Add a watermark", isOn: $enabled)
                TextField("@yourhandle", text: $text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } footer: {
                Text(settings.watermark.fontAvailable
                     ? "Burned into every video the factory renders, so it survives re-uploads."
                     : "No font found on the server — run: sudo apt install -y fonts-dejavu-core")
            }

            Section("Placement") {
                Picker("Position", selection: $position) {
                    ForEach(settings.watermark.positions, id: \.self) {
                        Text($0.replacingOccurrences(of: "-", with: " ").capitalized).tag($0)
                    }
                }
                VStack(alignment: .leading) {
                    Text("Size — \(String(format: "%.1f", size))% of height")
                        .font(.caption).foregroundStyle(.secondary)
                    Slider(value: $size, in: 1...8, step: 0.5)
                }
                VStack(alignment: .leading) {
                    Text("Opacity — \(Int(opacity * 100))%")
                        .font(.caption).foregroundStyle(.secondary)
                    Slider(value: $opacity, in: 0.05...1, step: 0.05)
                }
            }

            Section("Preview") {
                WatermarkPreview(text: text, position: position, size: size, opacity: opacity)
            }
        }
        .navigationTitle("Watermark")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await save() } } label: {
                    if saving { ProgressView().controlSize(.small) }
                    else { Text("Save").fontWeight(.semibold) }
                }
                .disabled(saving)
            }
        }
        .onAppear {
            let w = settings.watermark
            enabled = w.enabled; text = w.text; position = w.position
            size = w.size; opacity = w.opacity
        }
    }

    private func save() async {
        saving = true
        await state.perform("Could not save the watermark") {
            try await APIClient.shared.updateSettings([
                "watermark_enabled": enabled,
                "watermark_text": text,
                "watermark_position": position,
                "watermark_size": String(format: "%.1f", size),
                "watermark_opacity": String(format: "%.2f", opacity),
            ])
            let fresh = try await APIClient.shared.settings()
            await MainActor.run { settings = fresh }
        }
        state.show("Watermark saved.", isError: false)
        saving = false
    }
}

/// Approximates how the mark will sit on a 9:16 frame. Not pixel-exact with
/// ffmpeg's drawtext, but it answers the only question that matters: which
/// corner, and how loud.
private struct WatermarkPreview: View {
    let text: String
    let position: String
    let size: Double
    let opacity: Double

    private var alignment: Alignment {
        switch position {
        case "bottom-left": return .bottomLeading
        case "top-right": return .topTrailing
        case "top-left": return .topLeading
        case "bottom-center": return .bottom
        case "top-center": return .top
        case "center": return .center
        default: return .bottomTrailing
        }
    }

    var body: some View {
        ZStack(alignment: alignment) {
            LinearGradient(colors: [.gray.opacity(0.45), .gray.opacity(0.2)],
                           startPoint: .top, endPoint: .bottom)
            Text(text.isEmpty ? "@yourhandle" : text)
                .font(.system(size: max(7, 220 * size / 100), weight: .bold))
                .foregroundStyle(.white.opacity(opacity))
                .shadow(color: .black.opacity(min(1, opacity + 0.25)), radius: 1, x: 1, y: 1)
                .padding(6)
        }
        .frame(width: 124, height: 220)
        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.small, style: .continuous))
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Captions & music defaults

struct DefaultsSettingsView: View {
    @Binding var settings: ServerSettings
    @EnvironmentObject private var state: AppState

    @State private var styles: [CaptionStyle] = []
    @State private var tracks: [MusicTrack] = []
    @State private var clipStyle = ""
    @State private var dubCaptions = ""
    @State private var dubMusic = ""
    @State private var saving = false

    var body: some View {
        Form {
            Section("Clip") {
                Picker("Caption style", selection: $clipStyle) {
                    ForEach(styles) { Text($0.name).tag($0.id) }
                }
            }

            Section {
                Picker("Captions", selection: $dubCaptions) {
                    Text("None").tag("")
                    ForEach(styles) { Text($0.name).tag($0.id) }
                }
                Picker("Background music", selection: $dubMusic) {
                    Text("None").tag("")
                    ForEach(tracks) { Text($0.name).tag($0.id) }
                }
            } header: {
                Text("Translated shorts")
            } footer: {
                Text(tracks.isEmpty
                     ? "Upload MP3s from the web dashboard to use them here."
                     : "Applied to every translated short unless overridden.")
            }
        }
        .navigationTitle("Captions & music")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await save() } } label: {
                    if saving { ProgressView().controlSize(.small) }
                    else { Text("Save").fontWeight(.semibold) }
                }
                .disabled(saving)
            }
        }
        .task {
            await state.perform("Could not load presets") {
                let s = try await APIClient.shared.captionStyles()
                let m = try await APIClient.shared.music()
                await MainActor.run { styles = s; tracks = m }
            }
            clipStyle = settings.defaultCaptionStyle
            dubCaptions = settings.defaultDubCaptions
            dubMusic = settings.defaultDubMusic
        }
    }

    private func save() async {
        saving = true
        await state.perform("Could not save") {
            try await APIClient.shared.updateSettings([
                "default_caption_style": clipStyle,
                "default_dub_captions": dubCaptions,
                "default_dub_music": dubMusic,
            ])
            let fresh = try await APIClient.shared.settings()
            await MainActor.run { settings = fresh }
        }
        state.show("Saved.", isError: false)
        saving = false
    }
}

// MARK: - Downloads

struct DownloadSettingsView: View {
    @Binding var settings: ServerSettings
    @EnvironmentObject private var state: AppState

    @State private var useCookies = false

    var body: some View {
        Form {
            Section {
                Toggle("Use captured YouTube cookies", isOn: $useCookies)
                    .onChange(of: useCookies) { Task { await save() } }
            } footer: {
                Text("YouTube blocks downloads from datacenter IPs. Cookies help, but forcing "
                     + "them alongside certain player clients makes YouTube expose no formats "
                     + "at all — turn this off if downloads suddenly stop working.")
            }
        }
        .navigationTitle("YouTube downloads")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { useCookies = settings.useYouTubeCookies }
    }

    private func save() async {
        await state.perform("Could not save") {
            try await APIClient.shared.updateSettings(["use_youtube_cookies": useCookies])
            let fresh = try await APIClient.shared.settings()
            await MainActor.run { settings = fresh }
        }
    }
}

// MARK: - VPN

struct VPNView: View {
    @Binding var settings: ServerSettings
    @EnvironmentObject private var state: AppState

    @State private var status = VPNStatus()
    @State private var rotation = false
    @State private var busy = false
    @State private var loading = true

    var body: some View {
        Form {
            Section("Status") {
                LabeledContent("Installed") {
                    Image(systemName: status.installed ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(status.installed ? Theme.success : .secondary)
                }
                LabeledContent("Connected") {
                    Image(systemName: status.connected ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(status.connected ? Theme.success : .secondary)
                }
                if !status.server.isEmpty {
                    LabeledContent("Server", value: status.server)
                }
                if !status.country.isEmpty {
                    LabeledContent("Country", value: status.country)
                }
                if !status.ip.isEmpty {
                    LabeledContent("IP") { Text(status.ip).font(.footnote.monospaced()) }
                }
            }

            Section {
                Toggle("Rotate automatically when blocked", isOn: $rotation)
                    .onChange(of: rotation) { Task { await saveRotation() } }
            } footer: {
                Text("When a download is refused, ShortForge switches to another server and "
                     + "retries instead of failing the job.")
            }

            Section("Actions") {
                Button { Task { await run("Rotate failed") {
                    try await APIClient.shared.vpnRotate() } } } label: {
                    Label("Change server", systemImage: "arrow.triangle.2.circlepath")
                }
                Button(role: .destructive) { Task { await run("Disconnect failed") {
                    try await APIClient.shared.vpnDisconnect() } } } label: {
                    Label("Disconnect", systemImage: "bolt.slash")
                }
            }
            .disabled(busy || !status.installed)

            if !status.countries.isEmpty {
                Section("Connect to a country") {
                    ForEach(status.countries.prefix(24), id: \.self) { country in
                        Button(country) {
                            Task { await run("Connect failed") {
                                try await APIClient.shared.vpnConnect(country: country) } }
                        }
                    }
                }
                .disabled(busy)
            }

            if !status.installed {
                Section {
                    Text("NordVPN's CLI is not installed on the server. Run ./setup.sh, then "
                         + "sign in from the Cloud tab's browser.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("VPN")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if loading { ProgressView() } }
        .refreshable { await reload() }
        .task { await reload() }
    }

    private func reload() async {
        await state.perform("Could not read the VPN status") {
            let fresh = try await APIClient.shared.vpnStatus()
            await MainActor.run { status = fresh; rotation = fresh.rotationEnabled }
        }
        loading = false
    }

    private func run(_ label: String, _ work: @escaping () async throws -> Void) async {
        busy = true
        await state.perform(label, work)
        await reload()
        busy = false
    }

    private func saveRotation() async {
        await state.perform("Could not save") {
            try await APIClient.shared.updateSettings(["vpn_rotation": rotation])
        }
    }
}

// MARK: - Accounts

struct AccountsView: View {
    @EnvironmentObject private var state: AppState

    @State private var youtube: [YouTubeAccount] = []
    @State private var tiktok: [TikTokAccount] = []
    @State private var mine: [MyChannel] = []
    @State private var loading = true

    var body: some View {
        List {
            Section("YouTube") {
                if youtube.isEmpty {
                    Text("No Google account connected.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(youtube) { account in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(account.email).font(.subheadline.weight(.medium))
                            ForEach(account.channels) { channel in
                                Label(channel.title, systemImage: "play.rectangle")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            Section {
                if tiktok.isEmpty {
                    Text("No TikTok account connected.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(tiktok) { account in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(account.name).font(.subheadline.weight(.medium))
                                HStack(spacing: 6) {
                                    Image(systemName: account.autoReady
                                          ? "checkmark.seal.fill" : "exclamationmark.triangle")
                                        .foregroundStyle(account.autoReady ? Theme.success
                                                                           : Theme.warning)
                                    Text(account.autoReady
                                         ? "Auto-upload ready (\(account.cookieCount) cookies)"
                                         : "Needs a login")
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            if account.autoEnabled {
                                Text("AUTO").font(.system(size: 9, weight: .bold))
                                    .padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(Theme.accent.opacity(0.18), in: Capsule())
                            }
                            if let url = account.profileURL {
                                Link(destination: url) {
                                    Image(systemName: "arrow.up.right.square")
                                }
                            }
                        }
                    }
                }
            } header: {
                Text("TikTok")
            } footer: {
                Text("Connecting an account needs a browser session — open the Cloud tab and "
                     + "sign in there.")
            }

            Section("My channels") {
                if mine.isEmpty {
                    Text("None yet.").font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(mine) { channel in
                        LabeledContent {
                            Text("\(channel.published) published")
                                .font(.caption).foregroundStyle(.secondary)
                        } label: {
                            Label(channel.name,
                                  systemImage: channel.kind == "local" ? "folder" : "play.rectangle")
                        }
                    }
                }
            }
        }
        .navigationTitle("Accounts")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if loading { ProgressView() } }
        .refreshable { await reload() }
        .task { await reload() }
    }

    private func reload() async {
        await state.perform("Could not load accounts") {
            let yt = try await APIClient.shared.youtubeAccounts()
            let tt = try await APIClient.shared.tiktokAccounts()
            let mc = try await APIClient.shared.myChannels()
            await MainActor.run { youtube = yt; tiktok = tt; mine = mc }
        }
        loading = false
    }
}
