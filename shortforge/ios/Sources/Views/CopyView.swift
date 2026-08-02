import SwiftUI

/// Source channels and their shorts — the input side of the Copy pipeline.
struct CopyView: View {
    @EnvironmentObject private var state: AppState

    @State private var channels: [SourceChannel] = []
    @State private var loading = true
    @State private var adding = false
    @State private var newURL = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: Theme.Space.md) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: Theme.Space.md)],
                              spacing: Theme.Space.md) {
                        StatTile(value: state.stats.sources, label: "Channels")
                        StatTile(value: state.stats.dubs, label: "Translated",
                                 caption: "\(state.stats.dubsRunning) running",
                                 tone: Theme.success)
                    }

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
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationDestination(for: SourceChannel.self) { ChannelDetailView(channel: $0) }
            .navigationTitle("Copy")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { adding = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("Add a channel")
                }
            }
            .refreshable { await reload() }
            .task { await reload() }
            .alert("Add a source channel", isPresented: $adding) {
                TextField("Channel URL or @handle", text: $newURL)
                    .textInputAutocapitalization(.never)
                Button("Cancel", role: .cancel) { newURL = "" }
                Button("Add") { Task { await add() } }
            }
        }
    }

    private func reload() async {
        await state.perform("Could not load channels") {
            let fresh = try await APIClient.shared.channels()
            await MainActor.run { channels = fresh }
        }
        await state.refreshStats()
        loading = false
    }

    private func add() async {
        let url = newURL.trimmingCharacters(in: .whitespaces)
        newURL = ""
        guard !url.isEmpty else { return }
        await state.perform("Could not add the channel") {
            try await APIClient.shared.addChannel(url: url)
        }
        await reload()
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
                            Button { selected = short } label: { SourceShortCard(short: short) }
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

            VStack(alignment: .leading, spacing: 4) {
                Text(short.title ?? "Untitled")
                    .font(.caption.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Label("Translate", systemImage: "globe")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(Theme.accent)
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
