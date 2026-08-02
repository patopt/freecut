import SwiftUI

struct LogsView: View {
    @EnvironmentObject private var state: AppState

    @State private var items: [Activity] = []
    @State private var kind = ""
    @State private var loading = true

    private let filters: [(label: String, value: String)] = [
        ("All", ""), ("Clips", "clip"), ("Translations", "dub"), ("Publishing", "publish"),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: Theme.Space.sm) {
                    Picker("Filter", selection: $kind) {
                        ForEach(filters, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.bottom, Theme.Space.sm)

                    if loading && items.isEmpty {
                        ProgressView().padding(.vertical, Theme.Space.xxl)
                    } else if items.isEmpty {
                        EmptyState(icon: "list.bullet.rectangle",
                                   title: "Nothing here yet",
                                   message: "Activity from clipping, translating and publishing shows up here.")
                    } else {
                        ForEach(items) { ActivityRow(item: $0) }
                    }
                }
                .padding(Theme.Space.lg)
            }
            .background(ForgeBackground())
            .scrollContentBackground(.hidden)
            .navigationTitle("Activity")
            .refreshable { await reload() }
            .task { await reload() }
            .onChange(of: kind) { Task { await reload() } }
        }
    }

    private func reload() async {
        await state.perform("Could not load activity") {
            let fresh = try await APIClient.shared.activity(kind: kind)
            await MainActor.run { items = fresh }
        }
        loading = false
    }
}

private struct ActivityRow: View {
    let item: Activity

    private var tone: Color {
        switch item.level {
        case "error": return Theme.danger
        case "success": return Theme.success
        default: return Theme.info
        }
    }

    private var icon: String {
        switch item.kind {
        case "clip": return "scissors"
        case "dub": return "waveform"
        case "publish": return "paperplane.fill"
        default: return "circle.fill"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: Theme.Space.md) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(tone)
                .frame(width: 26, height: 26)
                .background(tone.opacity(0.14), in: Circle())

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title ?? "—")
                    .font(.footnote.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                if let detail = item.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let ts = item.createdAt {
                    Text(Date(timeIntervalSince1970: ts), style: .relative)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            Spacer(minLength: 0)
        }
        .cardSurface(padding: Theme.Space.md)
    }
}
