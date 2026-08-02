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

struct ChannelDetailView: View {
    let channel: SourceChannel

    @EnvironmentObject private var state: AppState
    @State private var shorts: [ChannelShort] = []
    @State private var loading = true

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: Theme.Space.md)],
                      spacing: Theme.Space.md) {
                ForEach(shorts) { short in
                    VStack(alignment: .leading, spacing: 0) {
                        RemoteThumbnail(absolute: short.thumb)
                            .clipShape(UnevenRoundedRectangle(
                                topLeadingRadius: Theme.Radius.control,
                                topTrailingRadius: Theme.Radius.control, style: .continuous))
                        Text(short.title ?? "Short")
                            .font(.caption.weight(.medium))
                            .lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Theme.Space.sm)
                    }
                    .background(
                        RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                            .fill(.background.secondary))
                }
            }
            .padding(Theme.Space.lg)

            if loading && shorts.isEmpty {
                ProgressView().padding(.vertical, Theme.Space.xxl)
            } else if shorts.isEmpty {
                EmptyState(icon: "tray", title: "No shorts fetched yet",
                           message: "Pull to refresh, or press Refresh to fetch from YouTube.")
            }
        }
        .background(ForgeBackground())
        .scrollContentBackground(.hidden)
        .navigationTitle(channel.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task {
                        await state.perform("Refresh failed") {
                            try await APIClient.shared.refreshChannel(channel.id)
                        }
                        state.show("Fetching new videos…")
                    }
                } label: { Image(systemName: "arrow.clockwise") }
            }
        }
        .refreshable { await reload() }
        .task { await reload() }
    }

    private func reload() async {
        await state.perform("Could not load this channel") {
            let list = try await APIClient.shared.channelShorts(channel.id)
            await MainActor.run { shorts = list }
        }
        loading = false
    }
}
