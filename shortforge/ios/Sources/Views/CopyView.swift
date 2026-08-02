import SwiftUI
import AVKit

/// Source channels and their shorts — the input side of the Copy pipeline.
struct CopyView: View {
    @EnvironmentObject private var state: AppState

    private enum Tab: String, CaseIterable { case sources, mine
        var title: String { self == .sources ? "Sources" : "My channels" }
    }

    @State private var tab: Tab = .sources
    @State private var channels: [SourceChannel] = []
    @State private var mine: [MyChannel] = []
    @State private var loading = true
    @State private var adding = false
    @State private var newValue = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: Theme.Space.md) {
                    Picker("Section", selection: $tab) {
                        ForEach(Tab.allCases, id: \.self) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: Theme.Space.md)],
                              spacing: Theme.Space.md) {
                        StatTile(value: state.stats.sources, label: "Sources")
                        StatTile(value: state.stats.dubs, label: "Translated",
                                 caption: "\(state.stats.dubsRunning) running",
                                 tone: Theme.success)
                        StatTile(value: state.stats.published, label: "Published",
                                 caption: "\(state.stats.publishPending) queued",
                                 tone: Theme.warning)
                    }

                    if tab == .sources { sourcesSection } else { mineSection }
                }
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationDestination(for: SourceChannel.self) { ChannelDetailView(channel: $0) }
            .navigationDestination(for: MyChannel.self) { MyChannelDetailView(channel: $0) }
            .navigationTitle("Copy")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { adding = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel(tab == .sources ? "Add a channel" : "Create a channel")
                }
            }
            .refreshable { await reload() }
            .task { await reload() }
            .onChange(of: tab) { Task { await reload() } }
            .alert(tab == .sources ? "Add a source channel" : "Create a channel",
                   isPresented: $adding) {
                TextField(tab == .sources ? "Channel URL or @handle" : "Channel name",
                          text: $newValue)
                    .textInputAutocapitalization(tab == .sources ? .never : .words)
                Button("Cancel", role: .cancel) { newValue = "" }
                Button("Add") { Task { await add() } }
            }
        }
    }

    @ViewBuilder
    private var sourcesSection: some View {
        if loading && channels.isEmpty {
            ProgressView().padding(.vertical, Theme.Space.xxl)
        } else if channels.isEmpty {
            EmptyState(icon: "waveform", title: "No source channels",
                       message: "Add a YouTube channel to list and translate its shorts.")
        } else {
            ForEach(channels) { channel in
                NavigationLink(value: channel) { ChannelRow(channel: channel) }
                    .buttonStyle(.plain)
            }
        }
    }

    @ViewBuilder
    private var mineSection: some View {
        if loading && mine.isEmpty {
            ProgressView().padding(.vertical, Theme.Space.xxl)
        } else if mine.isEmpty {
            EmptyState(icon: "tv", title: "No channels yet",
                       message: "Create one to collect translated videos, or connect a "
                                + "YouTube / TikTok account from the Cloud tab.")
        } else {
            ForEach(mine) { channel in
                NavigationLink(value: channel) { MyChannelRow(channel: channel) }
                    .buttonStyle(.plain)
            }
        }
    }

    private func reload() async {
        await state.perform("Could not load channels") {
            let fresh = try await APIClient.shared.channels()
            let mineFresh = (try? await APIClient.shared.myChannels()) ?? []
            await MainActor.run { channels = fresh; mine = mineFresh }
        }
        await state.refreshStats()
        loading = false
    }

    private func add() async {
        let value = newValue.trimmingCharacters(in: .whitespaces)
        newValue = ""
        guard !value.isEmpty else { return }
        await state.perform("Could not add the channel") {
            if tab == .sources {
                try await APIClient.shared.addChannel(url: value)
            } else {
                try await APIClient.shared.createMyChannel(name: value)
            }
        }
        await reload()
    }
}

private struct MyChannelRow: View {
    let channel: MyChannel

    private var icon: String {
        switch channel.kind {
        case "youtube": return "play.rectangle.fill"
        case "tiktok": return "music.note"
        default: return "folder"
        }
    }

    var body: some View {
        HStack(spacing: Theme.Space.md) {
            Image(systemName: icon)
                .foregroundStyle(Theme.accent)
                .frame(width: 30, height: 30)
                .background(Theme.accent.opacity(0.14), in: RoundedRectangle(
                    cornerRadius: Theme.Radius.small, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                Text(channel.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                HStack(spacing: Theme.Space.sm) {
                    Text("\(channel.published) published")
                        .font(.caption2).foregroundStyle(.secondary)
                    if channel.pending > 0 {
                        Text("· \(channel.pending) queued")
                            .font(.caption2).foregroundStyle(Theme.warning)
                    }
                    if channel.autoEnabled {
                        Text("AUTO").font(.system(size: 9, weight: .bold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Theme.accent.opacity(0.18), in: Capsule())
                    }
                }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }
        .cardSurface(padding: Theme.Space.md)
        .contentShape(Rectangle())
    }
}

private struct ChannelRow: View {
    let channel: SourceChannel

    var body: some View {
        HStack(spacing: Theme.Space.md) {
            VStack(alignment: .leading, spacing: 3) {
                Text(channel.displayName)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                HStack(spacing: Theme.Space.sm) {
                    if let status = channel.status, !status.isEmpty {
                        StatusBadge(status: status)
                    }
                    if let n = channel.shortsCount {
                        Text("\(n) shorts").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }
        .cardSurface(padding: Theme.Space.md)
        .contentShape(Rectangle())
    }
}

// MARK: - Channel detail

struct ChannelDetailView: View {
    let channel: SourceChannel

    @EnvironmentObject private var state: AppState
    @State private var shorts: [ChannelShort] = []
    @State private var loading = true
    @State private var refreshing = false
    @State private var selected: ChannelShort?
    @State private var openDub: String?
    @State private var query = ""

    private var filtered: [ChannelShort] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return shorts }
        return shorts.filter { ($0.title ?? "").lowercased().contains(q) }
    }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: Theme.Space.md) {
                if loading && shorts.isEmpty {
                    ProgressView().padding(.vertical, Theme.Space.xxl)
                } else if shorts.isEmpty {
                    EmptyState(icon: "tray", title: "No shorts fetched yet",
                               message: "Press ⟳ to fetch this channel's videos from YouTube. "
                                        + "It runs in the background and can take a minute.")
                } else if filtered.isEmpty {
                    EmptyState(icon: "magnifyingglass", title: "No match",
                               message: "Nothing here matches “\(query)”.")
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 158), spacing: Theme.Space.md)],
                              spacing: Theme.Space.md) {
                        ForEach(filtered) { short in
                            Menu {
                                Button {
                                    selected = short
                                } label: { Label("Translate…", systemImage: "globe") }

                                if !short.dubs.isEmpty {
                                    Divider()
                                    ForEach(short.dubs) { dub in
                                        Button { openDub = dub.id } label: {
                                            Label(dub.label, systemImage: "waveform")
                                        }
                                    }
                                }
                            } label: {
                                SourceShortCard(short: short)
                            } primaryAction: {
                                // A plain tap does the common thing; long-press
                                // opens the menu for existing translations.
                                selected = short
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(Theme.Space.lg)
        }
        .background(ForgeBackground())
        .scrollContentBackground(.hidden)
        .searchable(text: $query, prompt: "Search this channel")
        .navigationTitle(channel.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await refresh() } } label: {
                    if refreshing { ProgressView().controlSize(.small) }
                    else { Image(systemName: "arrow.clockwise") }
                }
                .disabled(refreshing)
                .accessibilityLabel("Fetch new videos")
            }
        }
        .refreshable { await reload() }
        .task { await reload() }
        .sheet(item: $selected) { short in
            TranslateSheet(short: short)
        }
        .sheet(item: Binding(
            get: { openDub.map(IdentifiedString.init) },
            set: { if $0 == nil { openDub = nil } }
        )) { boxed in
            DubDetailView(dubId: boxed.value)
        }
    }

    private func reload() async {
        await state.perform("Could not load this channel") {
            let list = try await APIClient.shared.channelShorts(channel.id)
            await MainActor.run { shorts = list }
        }
        loading = false
    }

    /// The server fetches asynchronously, so poll for a while instead of
    /// leaving the user staring at an unchanged grid.
    private func refresh() async {
        refreshing = true
        await state.perform("Refresh failed") {
            try await APIClient.shared.refreshChannel(channel.id)
        }
        state.show("Fetching new videos…")
        let before = shorts.count
        for _ in 0..<10 {
            try? await Task.sleep(for: .seconds(3))
            await reload()
            if shorts.count != before { break }
        }
        refreshing = false
    }
}

/// One translation, tinted by status.
struct DubChip: View {
    let dub: DubRef

    private var tone: Color {
        switch dub.status {
        case "done": return Theme.success
        case "error": return Theme.danger
        default: return Theme.info
        }
    }

    var body: some View {
        Text(dub.label)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(tone)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(tone.opacity(0.16), in: Capsule())
            .lineLimit(1)
    }
}

/// A source video in the grid.
///
/// The stored thumbnail is YouTube's 16:9 `hqdefault`, even for vertical
/// Shorts — rendering it in a 9:16 frame cropped away everything but a strip
/// down the middle, which is why these looked broken.
struct SourceShortCard: View {
    let short: ChannelShort

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RemoteThumbnail(absolute: short.thumb, aspect: 16.0 / 9.0, contentMode: .fill)
                .overlay(alignment: .bottomTrailing) {
                    if let d = short.duration, d > 0 {
                        Text(formatDuration(d))
                            .font(.caption2.monospacedDigit())
                            .padding(.horizontal, 5).padding(.vertical, 2)
                            .background(.black.opacity(0.6), in: Capsule())
                            .foregroundStyle(.white)
                            .padding(6)
                    }
                }
                .clipShape(UnevenRoundedRectangle(
                    topLeadingRadius: Theme.Radius.control,
                    topTrailingRadius: Theme.Radius.control, style: .continuous))

            VStack(alignment: .leading, spacing: 5) {
                Text(short.title ?? "Untitled")
                    .font(.caption.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)

                if short.dubs.isEmpty {
                    Label("Translate", systemImage: "globe")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(Theme.accent)
                } else {
                    // Mirrors the web's chips: one per translation, tinted by
                    // status, so the state of a video is readable at a glance.
                    HStack(spacing: 4) {
                        ForEach(short.dubs.prefix(3)) { DubChip(dub: $0) }
                        if short.dubs.count > 3 {
                            Text("+\(short.dubs.count - 3)")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .padding(Theme.Space.sm)
        }
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(.background.secondary))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1))
        .contentShape(Rectangle())
    }
}

// MARK: - Translate

/// Queues a translation for one source video: pick a language, optionally a
/// destination channel, and send it.
struct TranslateSheet: View {
    let short: ChannelShort

    // Presented from ChannelDetailView; the private members below would make
    // the memberwise initializer private.
    init(short: ChannelShort) { self.short = short }

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var state: AppState

    @State private var languages: [(code: String, name: String)] = []
    @State private var destinations: [MyChannel] = []
    @State private var lang = "fr"
    @State private var destination = ""
    @State private var busy = false
    @State private var loading = true

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    RemoteThumbnail(absolute: short.thumb, aspect: 16.0 / 9.0)
                        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.small,
                                                    style: .continuous))
                        .listRowInsets(EdgeInsets())
                    Text(short.title ?? "Untitled")
                        .font(.subheadline.weight(.medium))
                    if let urlString = short.url, let url = URL(string: urlString) {
                        Link(destination: url) {
                            Label("Open on YouTube", systemImage: "arrow.up.right.square")
                        }
                    }
                }

                Section("Translate into") {
                    if loading {
                        HStack { ProgressView().controlSize(.small); Text("Loading languages…") }
                    } else {
                        Picker("Language", selection: $lang) {
                            ForEach(languages, id: \.code) { Text($0.name).tag($0.code) }
                        }
                    }
                }

                Section {
                    Picker("Destination", selection: $destination) {
                        Text("Keep in the library").tag("")
                        ForEach(destinations) { Text($0.name).tag($0.id) }
                    }
                } header: {
                    Text("Publish to")
                } footer: {
                    Text(destinations.isEmpty
                         ? "Connect a channel to publish automatically once the dub is ready."
                         : "The finished video is queued for this channel.")
                }

                Section {
                    Button { Task { await submit() } } label: {
                        HStack {
                            Spacer()
                            if busy { ProgressView().controlSize(.small) }
                            Text(busy ? "Queueing…" : "Translate").fontWeight(.semibold)
                            Spacer()
                        }
                    }
                    .disabled(busy || loading)
                } footer: {
                    Text("Transcription, translation, voice-over and rendering all run on the "
                         + "server — you can close the app.")
                }
            }
            .navigationTitle("Translate")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .task { await load() }
    }

    private func load() async {
        await state.perform("Could not load languages") {
            let langs = try await APIClient.shared.languages()
            let dests = (try? await APIClient.shared.myChannels()) ?? []
            await MainActor.run {
                languages = langs
                destinations = dests
                if !langs.contains(where: { $0.code == lang }) { lang = langs.first?.code ?? "" }
            }
        }
        loading = false
    }

    private func submit() async {
        busy = true
        var ok = true
        await state.perform("Could not queue the translation") {
            do {
                try await APIClient.shared.dub(shortId: short.id, lang: lang,
                                               destChannelId: destination)
            } catch {
                ok = false
                throw error
            }
        }
        busy = false
        if ok {
            state.show("Translation queued.", isError: false)
            await state.refreshStats()
            dismiss()
        }
    }
}

// MARK: - Translated video

/// A finished (or in-flight) translation: watch it, publish it elsewhere,
/// retry it, delete it. The web's dub modal, on a phone.
struct DubDetailView: View {
    let dubId: String

    init(dubId: String) { self.dubId = dubId }

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var state: AppState

    @State private var dub: Dub?
    @State private var player: AVPlayer?
    @State private var destinations: [MyChannel] = []
    @State private var target = ""
    @State private var busy = false
    @State private var confirmDelete = false
    @State private var downloadURL: URL?

    private var isDone: Bool { dub?.status == "done" }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Space.lg) {
                    if let dub {
                        if isDone {
                            ZStack {
                                Color.black
                                if let player { VideoPlayer(player: player) }
                                else { ProgressView().tint(.white) }
                            }
                            .aspectRatio(9.0 / 16.0, contentMode: .fit)
                            .frame(maxHeight: 420)
                            .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control,
                                                        style: .continuous))
                        }

                        status(dub)
                        if isDone { publishBlock }
                        actions(dub)

                        if let log = dub.log, !log.isEmpty {
                            DisclosureGroup("Log") {
                                Text(log)
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                            }
                            .cardSurface()
                        }
                    } else {
                        ProgressView().frame(maxWidth: .infinity).padding(.vertical, Theme.Space.xxl)
                    }
                }
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationTitle(dub.map { $0.lang.uppercased() } ?? "Translation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    if let url = downloadURL { ShareLink(item: url) }
                }
            }
            .confirmationDialog("Delete this translation?",
                                isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    Task {
                        await state.perform("Delete failed") {
                            try await APIClient.shared.deleteDub(dubId)
                        }
                        dismiss()
                    }
                }
            }
        }
        .task { await load() }
        .task { await pollWhileActive() }
        .onDisappear { player?.pause() }
    }

    private func status(_ dub: Dub) -> some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            HStack {
                StatusBadge(status: dub.status)
                Spacer()
                Text(dub.lang.uppercased())
                    .font(.caption.weight(.bold)).foregroundStyle(.secondary)
            }
            if !WorkStatus.isTerminal(dub.status) {
                if let stage = dub.stage, !stage.isEmpty {
                    Text(stage).font(.subheadline.weight(.medium))
                }
                ProgressTrack(progress: dub.progress)
                if let m = dub.message, !m.isEmpty {
                    Text(m).font(.caption).foregroundStyle(.secondary)
                }
            }
            if let e = dub.error, !e.isEmpty {
                Text(e).font(.caption).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .cardSurface()
    }

    private var publishBlock: some View {
        VStack(alignment: .leading, spacing: Theme.Space.md) {
            Label("Publish to a channel", systemImage: "paperplane")
                .font(.caption.weight(.bold)).foregroundStyle(.secondary)
            if destinations.isEmpty {
                Text("No destination channels yet.")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Picker("Destination", selection: $target) {
                    Text("Pick a channel").tag("")
                    ForEach(destinations) { Text($0.name).tag($0.id) }
                }
                .pickerStyle(.menu)
                Button {
                    Task { await publish() }
                } label: {
                    Label("Send", systemImage: "paperplane.fill")
                        .frame(maxWidth: .infinity).padding(.vertical, 10)
                        .background(Theme.accentGradient, in: RoundedRectangle(
                            cornerRadius: Theme.Radius.control, style: .continuous))
                        .foregroundStyle(.white)
                }
                .disabled(busy || target.isEmpty)
                .opacity(busy || target.isEmpty ? 0.5 : 1)
            }
        }
        .cardSurface()
    }

    private func actions(_ dub: Dub) -> some View {
        HStack(spacing: Theme.Space.sm) {
            if dub.status == "error" {
                Button {
                    Task {
                        await state.perform("Retry failed") {
                            try await APIClient.shared.retryDub(dubId)
                        }
                        await load()
                    }
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
            Button(role: .destructive) { confirmDelete = true } label: {
                Label("Delete", systemImage: "trash").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
    }

    private func load() async {
        await state.perform("Could not load this translation") {
            let fresh = try await APIClient.shared.dubDetail(dubId)
            let dests = (try? await APIClient.shared.myChannels()) ?? []
            await MainActor.run { dub = fresh; destinations = dests }
        }
        if dub?.status == "done", player == nil { await preparePlayer() }
    }

    private func preparePlayer() async {
        guard let url = await APIClient.shared.mediaURL(path: "/api/dubs/\(dubId)/video")
        else { return }
        downloadURL = await APIClient.shared.mediaURL(path: "/api/dubs/\(dubId)/download")
        // AVURLAsset is anonymous unless handed the cookies explicitly.
        let asset = AVURLAsset(url: url, options: [
            AVURLAssetHTTPCookiesKey: HTTPCookieStorage.shared.cookies(for: url) ?? [],
        ])
        player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
    }

    private func pollWhileActive() async {
        while !Task.isCancelled {
            guard let current = dub, !WorkStatus.isTerminal(current.status) else { return }
            try? await Task.sleep(for: .seconds(3))
            await load()
        }
    }

    private func publish() async {
        busy = true
        await state.perform("Could not publish") {
            try await APIClient.shared.publishDub(dubId, to: target)
        }
        state.show("Queued for publishing.", isError: false)
        await state.refreshStats()
        busy = false
    }
}

// MARK: - My channel detail

/// The translated videos collected by one destination channel.
struct MyChannelDetailView: View {
    let channel: MyChannel

    @EnvironmentObject private var state: AppState
    @State private var dubs: [Dub] = []
    @State private var loading = true
    @State private var openDub: String?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: Theme.Space.md) {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: Theme.Space.md)],
                          spacing: Theme.Space.md) {
                    StatTile(value: channel.published, label: "Published", tone: Theme.success)
                    StatTile(value: channel.pending, label: "Queued", tone: Theme.warning)
                }

                if loading && dubs.isEmpty {
                    ProgressView().padding(.vertical, Theme.Space.xxl)
                } else if dubs.isEmpty {
                    EmptyState(icon: "tray", title: "Nothing here yet",
                               message: "Translate a source video and send it to this channel.")
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: Theme.Space.md)],
                              spacing: Theme.Space.md) {
                        ForEach(dubs) { dub in
                            Button { openDub = dub.id } label: { DubCard(dub: dub) }
                                .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(Theme.Space.lg)
        }
        .background(ForgeBackground())
        .scrollContentBackground(.hidden)
        .navigationTitle(channel.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if channel.kind != "local" {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task {
                            await state.perform("Could not publish") {
                                try await APIClient.shared.publishNow(targetId: channel.id)
                            }
                            state.show("Publishing the next video…", isError: false)
                        }
                    } label: { Image(systemName: "paperplane") }
                    .accessibilityLabel("Publish now")
                }
            }
        }
        .refreshable { await reload() }
        .task { await reload() }
        .sheet(item: Binding(
            get: { openDub.map(IdentifiedString.init) },
            set: { if $0 == nil { openDub = nil } }
        )) { boxed in
            DubDetailView(dubId: boxed.value)
        }
    }

    private func reload() async {
        await state.perform("Could not load this channel") {
            let list = try await APIClient.shared.dubsForChannel(channel.id)
            await MainActor.run { dubs = list }
        }
        loading = false
    }
}

/// `sheet(item:)` needs an Identifiable; a bare id String is not one.
struct IdentifiedString: Identifiable {
    let value: String
    var id: String { value }
    init(_ value: String) { self.value = value }
}

private struct DubCard: View {
    let dub: Dub

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack {
                RemoteThumbnail(path: "/api/dubs/\(dub.id)/thumb")
                if dub.status != "done" {
                    Rectangle().fill(.black.opacity(0.45))
                    VStack(spacing: 4) {
                        Text(dub.status == "error" ? "✕" : "\(dub.progress)%")
                            .font(.headline).foregroundStyle(.white)
                        Text(WorkStatus.label(dub.status))
                            .font(.caption2).foregroundStyle(.white.opacity(0.8))
                    }
                }
            }
            .clipShape(UnevenRoundedRectangle(
                topLeadingRadius: Theme.Radius.control,
                topTrailingRadius: Theme.Radius.control, style: .continuous))

            HStack {
                Text(dub.lang.uppercased())
                    .font(.caption.weight(.bold)).foregroundStyle(Theme.accent)
                Spacer()
                StatusBadge(status: dub.status)
            }
            .padding(Theme.Space.sm)
        }
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(.background.secondary))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1))
        .contentShape(Rectangle())
    }
}
