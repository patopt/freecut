import SwiftUI

struct ClipView: View {
    @EnvironmentObject private var state: AppState

    @State private var jobs: [Job] = []
    @State private var loading = true
    @State private var composing = false

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: Theme.Space.md) {
                    statsRow
                    jobList
                }
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationDestination(for: Job.self) { JobDetailView(job: $0) }
            .navigationTitle("Clip")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { composing = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("New shorts")
                }
            }
            .refreshable { await reload() }
            .task { await reload() }
            // Live jobs need to keep moving without the user pulling to refresh.
            .task { await pollWhileActive() }
            .sheet(isPresented: $composing) {
                ComposeJobView { await reload() }
            }
        }
    }

    private var statsRow: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: Theme.Space.md)],
                  spacing: Theme.Space.md) {
            StatTile(value: state.stats.shorts, label: "Shorts",
                     caption: "\(state.stats.jobs) videos")
            StatTile(value: state.stats.published, label: "Published",
                     caption: "\(state.stats.accounts) accounts", tone: Theme.success)
            StatTile(value: state.stats.jobsRunning, label: "Running",
                     caption: "\(state.stats.publishPending) to post", tone: Theme.warning)
            if state.stats.jobsFailed > 0 {
                StatTile(value: state.stats.jobsFailed, label: "Failed",
                         caption: "swipe to delete", tone: Theme.danger)
            }
        }
    }

    @ViewBuilder
    private var jobList: some View {
        if loading && jobs.isEmpty {
            ProgressView().padding(.vertical, Theme.Space.xxl)
        } else if jobs.isEmpty {
            EmptyState(icon: "scissors", title: "No videos yet",
                       message: "Paste a YouTube URL to generate your first batch of shorts.")
        } else {
            ForEach(jobs) { job in
                NavigationLink(value: job) { JobRow(job: job) }
                    .buttonStyle(.plain)
            }
            .animation(.smooth, value: jobs)
        }
    }

    private func reload() async {
        await state.perform("Could not load videos") {
            let fresh = try await APIClient.shared.jobs()
            await MainActor.run { jobs = fresh }
        }
        await state.refreshStats()
        loading = false
    }

    /// Polls only while something is actually in flight, so an idle app on a
    /// phone is not burning battery for nothing.
    private func pollWhileActive() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(5))
            guard jobs.contains(where: { !WorkStatus.isTerminal($0.status) }) else { continue }
            if let fresh = try? await APIClient.shared.jobs() { jobs = fresh }
            await state.refreshStats()
        }
    }
}

private struct JobRow: View {
    let job: Job

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text(job.displayTitle)
                .font(.subheadline.weight(.semibold))
                .lineLimit(2)
                .multilineTextAlignment(.leading)

            HStack(spacing: Theme.Space.sm) {
                StatusBadge(status: job.status)
                if let n = job.numShorts, n > 0 {
                    Label("\(n)", systemImage: "rectangle.stack")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if let d = job.duration, d > 0 {
                    Text(formatDuration(d)).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption2).foregroundStyle(.tertiary)
            }

            if !WorkStatus.isTerminal(job.status) {
                ProgressTrack(progress: job.progress)
                if let m = job.message, !m.isEmpty {
                    Text(m).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            } else if job.status == "error", let e = job.error, !e.isEmpty {
                Text(e).font(.caption2).foregroundStyle(Theme.danger).lineLimit(2)
            }
        }
        .cardSurface(padding: Theme.Space.md)
        .contentShape(Rectangle())
    }
}

func formatDuration(_ seconds: Double) -> String {
    let total = Int(seconds.rounded())
    let h = total / 3600, m = (total % 3600) / 60, s = total % 60
    return h > 0 ? String(format: "%d:%02d:%02d", h, m, s)
                 : String(format: "%d:%02d", m, s)
}
