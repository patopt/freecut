import SwiftUI
import AVFoundation
import AVKit
import Photos

/// On-device editor for a rendered short.
///
/// The heavy lifting — reframing, captions, watermark — already happened on the
/// server. What is missing is the last mile: tightening the in/out points,
/// changing pace, muting, and getting the result out to Photos or a share
/// sheet. That is what this does, locally, so it stays instant and works even
/// when the tunnel is slow.
struct EditorView: View {
    let short: Short

    // Presented from JobDetailView, another file: the private members below
    // would otherwise make the synthesized memberwise initializer private.
    init(short: Short) { self.short = short }

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var state: AppState

    @State private var localURL: URL?
    @State private var asset: AVURLAsset?
    @State private var player: AVPlayer?
    @State private var duration: Double = 0
    @State private var trimStart: Double = 0
    @State private var trimEnd: Double = 0
    @State private var playhead: Double = 0
    @State private var speed: Double = 1.0
    @State private var muted = false
    @State private var thumbs: [UIImage] = []
    @State private var downloading = true
    @State private var exporting = false
    @State private var exportProgress: Double = 0
    @State private var exportedURL: URL?
    @State private var timeObserver: Any?

    private let speeds: [Double] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

    var body: some View {
        NavigationStack {
            Group {
                if downloading {
                    VStack(spacing: Theme.Space.md) {
                        ProgressView()
                        Text("Fetching the clip…").font(.footnote).foregroundStyle(.secondary)
                    }
                } else if asset == nil {
                    EmptyState(icon: "exclamationmark.triangle",
                               title: "Could not open this clip",
                               message: "The download failed or the file is unreadable.")
                } else {
                    editor
                }
            }
            .background(ForgeBackground())
            .navigationTitle("Edit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await export() }
                    } label: {
                        if exporting { ProgressView().controlSize(.small) }
                        else { Text("Export").fontWeight(.semibold) }
                    }
                    .disabled(exporting || asset == nil || trimmedDuration < 0.4)
                }
            }
        }
        .task { await load() }
        .onDisappear { teardown() }
        .sheet(item: Binding(
            get: { exportedURL.map(ExportedFile.init) },
            set: { if $0 == nil { exportedURL = nil } }
        )) { file in
            ExportSheet(url: file.url)
        }
    }

    // MARK: - Editor UI

    private var editor: some View {
        ScrollView {
            VStack(spacing: Theme.Space.lg) {
                ZStack {
                    Color.black
                    if let player { VideoPlayer(player: player) }
                }
                .aspectRatio(9.0 / 16.0, contentMode: .fit)
                .frame(maxHeight: 380)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control,
                                            style: .continuous))

                timeline

                VStack(alignment: .leading, spacing: Theme.Space.md) {
                    HStack {
                        Label("Trim", systemImage: "scissors")
                            .font(.caption.weight(.bold)).foregroundStyle(.secondary)
                        Spacer()
                        Text("\(timecode(trimStart)) → \(timecode(trimEnd))")
                            .font(.caption.monospacedDigit())
                        Text("(\(String(format: "%.1fs", trimmedDuration)))")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }

                    HStack(spacing: Theme.Space.sm) {
                        Button("Set in") { trimStart = min(playhead, trimEnd - 0.4) }
                            .buttonStyle(.bordered).controlSize(.small)
                        Button("Set out") { trimEnd = max(playhead, trimStart + 0.4) }
                            .buttonStyle(.bordered).controlSize(.small)
                        Spacer()
                        Button("Reset") { trimStart = 0; trimEnd = duration }
                            .buttonStyle(.borderless).controlSize(.small)
                    }
                }
                .cardSurface()

                VStack(alignment: .leading, spacing: Theme.Space.md) {
                    Label("Speed", systemImage: "speedometer")
                        .font(.caption.weight(.bold)).foregroundStyle(.secondary)
                    Picker("Speed", selection: $speed) {
                        ForEach(speeds, id: \.self) { s in
                            Text(s == 1.0 ? "1×" : "\(s.formatted())×").tag(s)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: speed) { player?.rate = player?.rate == 0 ? 0 : Float(speed) }

                    Toggle(isOn: $muted) {
                        Label("Mute audio", systemImage: muted ? "speaker.slash" : "speaker.wave.2")
                            .font(.subheadline)
                    }
                    .onChange(of: muted) { player?.isMuted = muted }
                }
                .cardSurface()

                if exporting {
                    VStack(alignment: .leading, spacing: Theme.Space.sm) {
                        Text("Exporting… \(Int(exportProgress * 100))%")
                            .font(.caption).foregroundStyle(.secondary)
                        ProgressTrack(progress: Int(exportProgress * 100))
                    }
                    .cardSurface()
                }
            }
            .padding(Theme.Space.lg)
        }
        .scrollContentBackground(.hidden)
    }

    /// Filmstrip with draggable in/out handles. The thumbnails are what make
    /// trimming possible at all — a bare slider gives no idea where you are.
    private var timeline: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                HStack(spacing: 0) {
                    ForEach(thumbs.indices, id: \.self) { i in
                        Image(uiImage: thumbs[i])
                            .resizable().scaledToFill()
                            .frame(width: w / CGFloat(max(thumbs.count, 1)), height: 58)
                            .clipped()
                    }
                }
                .frame(width: w, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.small, style: .continuous))

                // Dim what the export will drop.
                Rectangle().fill(.black.opacity(0.55))
                    .frame(width: max(0, x(trimStart, w)))
                Rectangle().fill(.black.opacity(0.55))
                    .frame(width: max(0, w - x(trimEnd, w)))
                    .offset(x: x(trimEnd, w))

                handle(at: x(trimStart, w)) { dx in
                    trimStart = clamp(value(dx, w), 0, trimEnd - 0.4)
                }
                handle(at: x(trimEnd, w) - 12) { dx in
                    trimEnd = clamp(value(dx + 12, w), trimStart + 0.4, duration)
                }

                Rectangle().fill(.white)
                    .frame(width: 2, height: 66)
                    .offset(x: x(playhead, w))
                    .shadow(radius: 2)
                    .allowsHitTesting(false)
            }
            .frame(height: 66)
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { g in
                seek(to: clamp(value(g.location.x, w), trimStart, trimEnd))
            })
        }
        .frame(height: 66)
    }

    private func handle(at offset: CGFloat, onDrag: @escaping (CGFloat) -> Void) -> some View {
        RoundedRectangle(cornerRadius: 4, style: .continuous)
            .fill(Theme.accent)
            .frame(width: 12, height: 66)
            .overlay(Capsule().fill(.white.opacity(0.85)).frame(width: 2, height: 22))
            .offset(x: offset)
            .gesture(DragGesture().onChanged { g in onDrag(g.location.x) })
    }

    // MARK: - Geometry helpers

    private var trimmedDuration: Double { max(0, trimEnd - trimStart) }
    private func x(_ t: Double, _ w: CGFloat) -> CGFloat {
        guard duration > 0 else { return 0 }
        return CGFloat(t / duration) * w
    }
    private func value(_ x: CGFloat, _ w: CGFloat) -> Double {
        guard w > 0 else { return 0 }
        return Double(x / w) * duration
    }
    private func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double {
        min(max(v, lo), max(lo, hi))
    }
    private func timecode(_ t: Double) -> String {
        String(format: "%d:%05.2f", Int(t) / 60, t.truncatingRemainder(dividingBy: 60))
    }

    // MARK: - Loading

    private func load() async {
        guard let remote = await APIClient.shared.mediaURL(
            path: "/api/shorts/\(short.id)/video") else { downloading = false; return }

        // Editing needs random access to the file, so it is copied locally
        // instead of being streamed — seeking over a tunnel would be unusable.
        var request = URLRequest(url: remote)
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        do {
            let (tmp, _) = try await URLSession.shared.download(for: request)
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("edit-\(short.id).mp4")
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.moveItem(at: tmp, to: dest)
            localURL = dest

            let a = AVURLAsset(url: dest)
            let d = try await a.load(.duration).seconds
            await MainActor.run {
                asset = a
                duration = d.isFinite ? d : 0
                trimEnd = duration
                let item = AVPlayerItem(asset: a)
                let p = AVPlayer(playerItem: item)
                player = p
                observe(p)
            }
            await buildFilmstrip(a)
        } catch {
            state.show("Could not download the clip: \(error.localizedDescription)", isError: true)
        }
        downloading = false
    }

    private func observe(_ p: AVPlayer) {
        timeObserver = p.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.05, preferredTimescale: 600), queue: .main
        ) { time in
            playhead = time.seconds
            // Loop inside the trimmed range so what you hear is what you export.
            if playhead >= trimEnd - 0.02 { seek(to: trimStart) }
        }
    }

    private func seek(to t: Double) {
        playhead = t
        player?.seek(to: CMTime(seconds: t, preferredTimescale: 600),
                     toleranceBefore: .zero, toleranceAfter: .zero)
    }

    private func buildFilmstrip(_ asset: AVURLAsset) async {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 120, height: 200)
        generator.requestedTimeToleranceBefore = CMTime(seconds: 0.4, preferredTimescale: 600)
        generator.requestedTimeToleranceAfter = CMTime(seconds: 0.4, preferredTimescale: 600)

        let count = 12
        var out: [UIImage] = []
        for i in 0..<count {
            let t = duration * Double(i) / Double(count)
            let time = CMTime(seconds: t, preferredTimescale: 600)
            if let cg = try? await generator.image(at: time).image {
                out.append(UIImage(cgImage: cg))
            }
        }
        await MainActor.run { thumbs = out }
    }

    private func teardown() {
        if let timeObserver { player?.removeTimeObserver(timeObserver) }
        timeObserver = nil
        player?.pause()
        player = nil
    }

    // MARK: - Export

    private func export() async {
        guard let asset, let source = localURL else { return }
        exporting = true
        exportProgress = 0
        defer { exporting = false }

        let output = FileManager.default.temporaryDirectory
            .appendingPathComponent("shortforge-\(short.id)-\(Int(Date().timeIntervalSince1970)).mp4")
        try? FileManager.default.removeItem(at: output)

        do {
            let composition = try await buildComposition(from: asset)
            guard let session = AVAssetExportSession(
                asset: composition, presetName: AVAssetExportPresetHighestQuality) else {
                throw NSError(domain: "export", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "No usable export preset."])
            }
            session.outputURL = output
            session.outputFileType = .mp4
            session.shouldOptimizeForNetworkUse = true

            let poll = Task {
                while !Task.isCancelled {
                    await MainActor.run { exportProgress = Double(session.progress) }
                    try? await Task.sleep(for: .milliseconds(200))
                }
            }
            // The async `export()` is iOS 18; this app targets 17, so the
            // callback form is wrapped instead of raising the deployment floor.
            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                session.exportAsynchronously { cont.resume() }
            }
            poll.cancel()

            guard session.status == .completed else {
                throw session.error ?? NSError(
                    domain: "export", code: 2,
                    userInfo: [NSLocalizedDescriptionKey: "Export did not complete."])
            }
            _ = source   // keep the download alive until the export has read it
            exportedURL = output
        } catch {
            state.show("Export failed: \(error.localizedDescription)", isError: true)
        }
    }

    /// Trim + optional speed change + optional mute, as an editable composition.
    private func buildComposition(from asset: AVURLAsset) async throws -> AVComposition {
        let composition = AVMutableComposition()
        let range = CMTimeRange(
            start: CMTime(seconds: trimStart, preferredTimescale: 600),
            duration: CMTime(seconds: trimmedDuration, preferredTimescale: 600))

        if let sourceVideo = try await asset.loadTracks(withMediaType: .video).first,
           let track = composition.addMutableTrack(withMediaType: .video,
                                                   preferredTrackID: kCMPersistentTrackID_Invalid) {
            try track.insertTimeRange(range, of: sourceVideo, at: .zero)
            track.preferredTransform = try await sourceVideo.load(.preferredTransform)
            if speed != 1.0 {
                track.scaleTimeRange(CMTimeRange(start: .zero, duration: track.timeRange.duration),
                                     toDuration: CMTime(seconds: trimmedDuration / speed,
                                                        preferredTimescale: 600))
            }
        }

        if !muted,
           let sourceAudio = try await asset.loadTracks(withMediaType: .audio).first,
           let track = composition.addMutableTrack(withMediaType: .audio,
                                                   preferredTrackID: kCMPersistentTrackID_Invalid) {
            try track.insertTimeRange(range, of: sourceAudio, at: .zero)
            if speed != 1.0 {
                track.scaleTimeRange(CMTimeRange(start: .zero, duration: track.timeRange.duration),
                                     toDuration: CMTime(seconds: trimmedDuration / speed,
                                                        preferredTimescale: 600))
            }
        }
        return composition
    }
}

private struct ExportedFile: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

/// What to do with the finished file.
private struct ExportSheet: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    @State private var savedMessage = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: Theme.Space.lg) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 46)).foregroundStyle(Theme.success)
                Text("Clip exported").font(.headline)

                ShareLink(item: url) {
                    Label("Share", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity).padding(.vertical, 12)
                        .background(Theme.accentGradient, in: RoundedRectangle(
                            cornerRadius: Theme.Radius.control, style: .continuous))
                        .foregroundStyle(.white)
                }

                Button {
                    Task { savedMessage = await saveToPhotos() }
                } label: {
                    Label("Save to Photos", systemImage: "photo.badge.plus")
                        .frame(maxWidth: .infinity).padding(.vertical, 12)
                        .background(.background.secondary, in: RoundedRectangle(
                            cornerRadius: Theme.Radius.control, style: .continuous))
                }

                if !savedMessage.isEmpty {
                    Text(savedMessage).font(.footnote).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(Theme.Space.lg)
            .background(ForgeBackground())
            .navigationTitle("Done")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
        }
    }

    private func saveToPhotos() async -> String {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else {
            return "Photos access was denied — use Share instead."
        }
        do {
            try await PHPhotoLibrary.shared().performChanges {
                // The request registers itself with the change block; the
                // returned object is only needed to set extra properties.
                _ = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
            }
            return "Saved to Photos."
        } catch {
            return "Could not save: \(error.localizedDescription)"
        }
    }
}
