import SwiftUI
import AVKit

struct JobDetailView: View {
    let job: Job

    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var current: Job
    @State private var shorts: [Short] = []
    @State private var playing: Short?
    @State private var confirmDelete = false

    init(job: Job) {
        self.job = job
        _current = State(initialValue: job)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Space.lg) {
                header
                if !shorts.isEmpty {
                    Text("Generated shorts")
                        .font(.caption.weight(.bold))
                        .tracking(0.8)
                        .foregroundStyle(.secondary)
                    grid
                } else if WorkStatus.isTerminal(current.status) {
                    EmptyState(icon: "rectangle.on.rectangle",
                               title: "No shorts",
                               message: current.error ?? "This job produced nothing.")
                }
            }
            .padding(Theme.Space.lg)
        }
        .background(ForgeBackground())
        .scrollContentBackground(.hidden)
        .navigationTitle(current.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        Task { await state.perform("Retry failed") {
                            try await APIClient.shared.retryJob(current.id)
                        }; await reload() }
                    } label: { Label("Retry", systemImage: "arrow.clockwise") }

                    Button(role: .destructive) { confirmDelete = true } label: {
                        Label("Delete", systemImage: "trash")
                    }
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
        .confirmationDialog("Delete this video and its shorts?",
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                Task {
                    await state.perform("Delete failed") {
                        try await APIClient.shared.deleteJob(current.id)
                    }
                    dismiss()
                }
            }
        }
        .task { await reload() }
        .task { await pollWhileActive() }
        .sheet(item: $playing) { short in
            ShortPlayerView(short: short)
        }
        .refreshable { await reload() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            HStack {
                StatusBadge(status: current.status)
                Spacer()
                if let d = current.duration, d > 0 {
                    Text(formatDuration(d)).font(.caption).foregroundStyle(.secondary)
                }
            }
            if let stage = current.stage, !WorkStatus.isTerminal(current.status) {
                Text(stage).font(.subheadline.weight(.medium))
            }
            if !WorkStatus.isTerminal(current.status) {
                ProgressTrack(progress: current.progress)
                if let m = current.message, !m.isEmpty {
                    Text(m).font(.caption).foregroundStyle(.secondary)
                }
            }
            if current.status == "error", let e = current.error, !e.isEmpty {
                Text(e).font(.caption).foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .cardSurface()
    }

    private var grid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: Theme.Space.md)],
                  spacing: Theme.Space.md) {
            ForEach(shorts) { short in
                Button { playing = short } label: { ShortCard(short: short) }
                    .buttonStyle(.plain)
            }
        }
    }

    private func reload() async {
        await state.perform("Could not load this video") {
            let job = try await APIClient.shared.job(current.id)
            let list = try await APIClient.shared.shorts(jobId: current.id)
            await MainActor.run { current = job; shorts = list }
        }
    }

    private func pollWhileActive() async {
        while !Task.isCancelled && !WorkStatus.isTerminal(current.status) {
            try? await Task.sleep(for: .seconds(3))
            await reload()
        }
    }
}

struct ShortCard: View {
    let short: Short

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RemoteThumbnail(path: "/api/shorts/\(short.id)/thumb")
                .clipShape(UnevenRoundedRectangle(
                    topLeadingRadius: Theme.Radius.control,
                    topTrailingRadius: Theme.Radius.control, style: .continuous))
                .overlay(alignment: .topTrailing) {
                    if let score = short.score {
                        Text("\(Int(score))")
                            .font(.caption2.bold().monospacedDigit())
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(.black.opacity(0.55), in: Capsule())
                            .foregroundStyle(.white)
                            .padding(6)
                    }
                }
                .overlay(alignment: .bottomLeading) {
                    Text(formatDuration(short.duration))
                        .font(.caption2.monospacedDigit())
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(.black.opacity(0.55), in: Capsule())
                        .foregroundStyle(.white)
                        .padding(6)
                }

            VStack(alignment: .leading, spacing: 3) {
                Text(short.title ?? "Short")
                    .font(.caption.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                if let reason = short.reason, !reason.isEmpty {
                    Text(reason).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Theme.Space.sm)
        }
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(.background.secondary)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }
}

struct ShortPlayerView: View {
    let short: Short
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVPlayer?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Space.lg) {
                    ZStack {
                        Color.black
                        if let player {
                            VideoPlayer(player: player)
                        } else {
                            ProgressView().tint(.white)
                        }
                    }
                    .aspectRatio(9.0 / 16.0, contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control,
                                                style: .continuous))

                    if let reason = short.reason, !reason.isEmpty {
                        Text(reason).font(.footnote).foregroundStyle(.secondary)
                    }

                    if hasSignals {
                        VStack(alignment: .leading, spacing: Theme.Space.sm) {
                            Text("Virality signals")
                                .font(.caption.weight(.bold)).foregroundStyle(.secondary)
                            SignalBar(label: "Hook", value: short.hook ?? 0)
                            SignalBar(label: "Flow", value: short.flow ?? 0)
                            SignalBar(label: "Value", value: short.value ?? 0)
                            SignalBar(label: "Trend", value: short.trend ?? 0)
                        }
                        .cardSurface()
                    }
                }
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationTitle(short.title ?? "Short")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if let url = downloadURL { ShareLink(item: url) }
                }
            }
        }
        .task { await preparePlayer() }
        .onDisappear { player?.pause() }
    }

    private var hasSignals: Bool { short.signalTotal > 0 }

    // Resolved once the player is prepared; the API client is an actor, so the
    // address cannot be computed inline in the view body.
    @State private var downloadURL: URL?

    private func preparePlayer() async {
        guard let url = await APIClient.shared.mediaURL(path: "/api/shorts/\(short.id)/video")
        else { return }
        downloadURL = await APIClient.shared.mediaURL(
            path: "/api/shorts/\(short.id)/download")
        // Cookies live in the shared storage, and AVURLAsset reads them from
        // there when told to — otherwise the request is anonymous and 401s.
        let asset = AVURLAsset(url: url, options: [
            AVURLAssetHTTPCookiesKey: HTTPCookieStorage.shared.cookies(for: url) ?? [],
        ])
        player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        player?.play()
    }
}
