import SwiftUI

/// A single number in the overview row.
struct StatTile: View {
    let value: Int
    let label: String
    var caption: String = ""
    var tone: Color = Theme.accent

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)")
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .monospacedDigit()
                .contentTransition(.numericText())
            Text(label.uppercased())
                .font(.system(size: 10, weight: .bold))
                .tracking(0.7)
                .foregroundStyle(.secondary)
            if !caption.isEmpty {
                Text(caption).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.Space.md)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(.background.secondary)
        )
        .overlay(alignment: .bottom) {
            LinearGradient(colors: [tone, tone.opacity(0)],
                           startPoint: .leading, endPoint: .trailing)
                .frame(height: 2)
                .clipShape(Capsule())
                .padding(.horizontal, 1)
        }
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }
}

struct StatusBadge: View {
    let status: String

    private var tone: Color {
        switch status {
        case "done", "ready": return Theme.success
        case "error": return Theme.danger
        case "queued": return Theme.warning
        case "canceled": return .secondary
        default: return Theme.info
        }
    }

    var body: some View {
        Text(WorkStatus.label(status).uppercased())
            .font(.system(size: 10, weight: .bold))
            .tracking(0.6)
            .foregroundStyle(tone)
            .padding(.horizontal, 9)
            .padding(.vertical, 3)
            .background(tone.opacity(0.16), in: Capsule())
    }
}

struct ProgressTrack: View {
    let progress: Int

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule()
                    .fill(Theme.accentGradient)
                    .frame(width: geo.size.width * min(1, max(0, Double(progress) / 100)))
            }
        }
        .frame(height: 5)
        .animation(.smooth(duration: 0.4), value: progress)
    }
}

/// One signal of the virality breakdown (hook / flow / value / trend).
struct SignalBar: View {
    let label: String
    let value: Double

    var body: some View {
        HStack(spacing: Theme.Space.sm) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .frame(width: 44, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule().fill(Theme.accentGradient)
                        .frame(width: geo.size.width * min(1, max(0, value / 100)))
                }
            }
            .frame(height: 4)
            Text("\(Int(value))")
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 26, alignment: .trailing)
        }
    }
}

struct EmptyState: View {
    let icon: String
    let title: String
    var message: String = ""

    var body: some View {
        VStack(spacing: Theme.Space.sm) {
            Image(systemName: icon)
                .font(.system(size: 34))
                .foregroundStyle(.tertiary)
            Text(title).font(.headline)
            if !message.isEmpty {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Theme.Space.xxl)
    }
}

/// Loads a thumbnail from the dashboard. `AsyncImage` cannot be used directly:
/// the images sit behind the session cookie, and its default loader does not
/// share our configured `URLSession`.
struct RemoteThumbnail: View {
    /// Dashboard-relative path (needs the session cookie).
    var path: String?
    /// Already-absolute address, e.g. a YouTube CDN thumbnail the server only
    /// stored the URL of rather than re-hosting.
    var absolute: String?
    var aspect: CGFloat = 9.0 / 16.0
    /// `.fill` crops to the frame, `.fit` letterboxes. Source thumbnails from
    /// YouTube are 16:9 even for vertical Shorts, so cropping them to 9:16
    /// shows a narrow strip of the middle and nothing recognisable.
    var contentMode: ContentMode = .fill

    @State private var image: UIImage?
    @State private var failed = false

    // Explicit: the private @State above would otherwise make the synthesized
    // memberwise initializer private, and every call site is in another file.
    init(path: String? = nil, absolute: String? = nil,
         aspect: CGFloat = 9.0 / 16.0, contentMode: ContentMode = .fill) {
        self.path = path
        self.absolute = absolute
        self.aspect = aspect
        self.contentMode = contentMode
    }

    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary)
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
            } else if failed {
                Image(systemName: "photo").foregroundStyle(.tertiary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .aspectRatio(aspect, contentMode: .fit)
        .clipped()
        .task(id: path ?? absolute ?? "") { await load() }
    }

    private func load() async {
        guard image == nil else { return }
        let url: URL?
        if let absolute, !absolute.isEmpty {
            url = URL(string: absolute)
        } else if let path {
            url = await APIClient.shared.mediaURL(path: path)
        } else {
            url = nil
        }
        guard let url else { failed = true; return }

        var request = URLRequest(url: url)
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        do {
            let (data, _) = try await URLSession.shared.data(for: request)
            if let ui = UIImage(data: data) { image = ui } else { failed = true }
        } catch {
            failed = true
        }
    }
}
