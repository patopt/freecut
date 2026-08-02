import SwiftUI
import WebKit

/// The VPS's remote desktop, streamed into the app.
///
/// The viewer is the dashboard's own noVNC page in a `WKWebView` rather than a
/// hand-rolled RFB client: the server side already works, and reimplementing
/// the protocol would buy nothing but bugs. The session cookie is injected into
/// the web view's store before the first load, so the page authenticates the
/// same way the browser does.
struct CloudView: View {
    @EnvironmentObject private var state: AppState

    @State private var status = CloudStatus()
    @State private var active: CloudSession?
    @State private var loading = true
    @State private var busy = false
    @State private var fullscreen = false
    @State private var showPassword = false

    var body: some View {
        NavigationStack {
            Group {
                if !status.missing.isEmpty {
                    unavailable
                } else if let session = active {
                    viewer(session)
                } else {
                    idle
                }
            }
            .background(ForgeBackground())
            .navigationTitle("Cloud")
            .toolbar { toolbar }
            .task { await reload() }
            .refreshable { await reload() }
            .fullScreenCover(isPresented: $fullscreen) {
                if let session = active {
                    FullscreenDesktop(session: session, status: status)
                }
            }
        }
    }

    // MARK: - Pieces

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                if active != nil {
                    Button { fullscreen = true } label: {
                        Label("Fullscreen", systemImage: "arrow.up.left.and.arrow.down.right")
                    }
                    Button { Task { await openBrowser() } } label: {
                        Label("Open a browser", systemImage: "globe")
                    }
                    Button { showPassword = true } label: {
                        Label("Show VNC password", systemImage: "key")
                    }
                    Divider()
                    Button(role: .destructive) { Task { await stop() } } label: {
                        Label("Stop this desktop", systemImage: "stop.circle")
                    }
                }
                if status.sessions.count > 1 {
                    Button(role: .destructive) { Task { await stopAll() } } label: {
                        Label("Stop all desktops", systemImage: "xmark.octagon")
                    }
                }
            } label: { Image(systemName: "ellipsis.circle") }
            .disabled(status.missing.isEmpty == false)
        }
    }

    private var unavailable: some View {
        EmptyState(icon: "exclamationmark.triangle",
                   title: "Desktop unavailable",
                   message: "Missing on the server: \(status.missing.joined(separator: ", ")). "
                            + "Run ./setup.sh again.")
    }

    private var idle: some View {
        ScrollView {
            VStack(spacing: Theme.Space.lg) {
                if loading {
                    ProgressView().padding(.vertical, Theme.Space.xxl)
                } else {
                    EmptyState(icon: "desktopcomputer",
                               title: "No desktop running",
                               message: status.desktop.isEmpty
                                   ? "Start one to control the VPS from here."
                                   : "\(status.desktop) is installed and ready.")

                    Button { Task { await start() } } label: {
                        HStack {
                            if busy { ProgressView().controlSize(.small).tint(.white) }
                            Text(busy ? "Starting…" : "Start a desktop").fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Theme.accentGradient, in: RoundedRectangle(
                            cornerRadius: Theme.Radius.control, style: .continuous))
                        .foregroundStyle(.white)
                    }
                    .disabled(busy || status.sessions.count >= status.maxSessions)

                    Text("A desktop uses roughly 600 MB while it runs. Stop it when you are done.")
                        .font(.caption2).foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
            }
            .padding(Theme.Space.lg)
        }
        .scrollContentBackground(.hidden)
    }

    private func viewer(_ session: CloudSession) -> some View {
        VStack(spacing: 0) {
            if status.sessions.count > 1 {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Theme.Space.sm) {
                        ForEach(Array(status.sessions.enumerated()), id: \.element.id) { i, s in
                            Button { active = s } label: {
                                Text("Desktop \(i + 1)")
                                    .font(.caption.weight(.semibold))
                                    .padding(.horizontal, 12).padding(.vertical, 6)
                                    .background(s.id == session.id ? Theme.accent : .clear,
                                                in: Capsule())
                                    .foregroundStyle(s.id == session.id ? .white : .secondary)
                                    .overlay(Capsule().strokeBorder(
                                        Color.primary.opacity(s.id == session.id ? 0 : 0.15)))
                            }
                        }
                        if status.sessions.count < status.maxSessions {
                            Button { Task { await start() } } label: {
                                Image(systemName: "plus").font(.caption.weight(.bold))
                                    .padding(.horizontal, 12).padding(.vertical, 6)
                                    .overlay(Capsule().strokeBorder(Color.primary.opacity(0.15)))
                            }
                        }
                    }
                    .padding(.horizontal, Theme.Space.lg)
                    .padding(.vertical, Theme.Space.sm)
                }
            }

            DesktopWebView(session: session, status: status)
                .background(.black)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control,
                                            style: .continuous))
                .padding(.horizontal, Theme.Space.sm)
                .padding(.bottom, Theme.Space.sm)

            Text("\(session.desktop) · \(session.resolution)")
                .font(.caption2).foregroundStyle(.tertiary)
                .padding(.bottom, Theme.Space.sm)
        }
        .alert("VNC password", isPresented: $showPassword) {
            Button("Copy") { UIPasteboard.general.string = String(status.vncPassword.prefix(8)) }
            Button("Done", role: .cancel) {}
        } message: {
            // RFB auth truncates at 8 characters, so showing the stored 12 would
            // send people to a login that cannot succeed.
            Text("\(String(status.vncPassword.prefix(8)))\n\nVNC ignores anything past 8 characters.")
        }
    }

    // MARK: - Actions

    private func reload() async {
        await state.perform("Could not reach the desktop service") {
            let fresh = try await APIClient.shared.cloudStatus()
            await MainActor.run {
                status = fresh
                if let current = active, fresh.sessions.contains(where: { $0.id == current.id }) {
                    return
                }
                active = fresh.sessions.first
            }
        }
        loading = false
    }

    private func start() async {
        busy = true
        // A phone screen is narrow; a landscape-ish desktop is far more usable
        // than one matching the portrait viewport exactly.
        let size = UIScreen.main.bounds.size
        let long = Int(max(size.width, size.height))
        let short = Int(min(size.width, size.height))
        await state.perform("Could not start a desktop") {
            let session = try await APIClient.shared.cloudStart(
                width: max(1024, long * 2), height: max(700, short * 2))
            await MainActor.run { active = session }
        }
        await reload()
        busy = false
    }

    private func stop() async {
        guard let session = active else { return }
        await state.perform("Could not stop the desktop") {
            try await APIClient.shared.cloudStop(session.id)
        }
        active = nil
        await reload()
    }

    private func stopAll() async {
        await state.perform("Could not stop the desktops") {
            try await APIClient.shared.cloudStopAll()
        }
        active = nil
        await reload()
    }

    private func openBrowser() async {
        guard let session = active else { return }
        await state.perform("Could not open the browser") {
            try await APIClient.shared.cloudBrowser(session.id, url: "https://www.google.com")
        }
        state.show("Opening Chromium on the desktop…")
    }
}

/// Fullscreen presentation of the same viewer — the only way a desktop is
/// genuinely usable on a phone.
private struct FullscreenDesktop: View {
    let session: CloudSession
    let status: CloudStatus
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.ignoresSafeArea()
            DesktopWebView(session: session, status: status)
                .ignoresSafeArea(edges: .bottom)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.subheadline.weight(.bold))
                    .padding(10)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .padding(Theme.Space.md)
        }
    }
}

/// Hosts the dashboard's noVNC page, primed with the session cookie.
struct DesktopWebView: UIViewRepresentable {
    let session: CloudSession
    let status: CloudStatus

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        let view = WKWebView(frame: .zero, configuration: config)
        view.isOpaque = false
        view.backgroundColor = .black
        view.scrollView.bounces = false
        // The desktop handles its own panning and zooming; letting the scroll
        // view also respond makes every drag fight the remote pointer.
        view.scrollView.isScrollEnabled = false
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        guard context.coordinator.loadedSession != session.id else { return }
        context.coordinator.loadedSession = session.id
        Task { await load(into: view) }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var loadedSession: String?
    }

    @MainActor
    private func load(into view: WKWebView) async {
        let cookies = await APIClient.shared.sessionCookies()
        let store = view.configuration.websiteDataStore.httpCookieStore
        for cookie in cookies { await store.setCookie(cookie) }

        guard let url = await APIClient.shared.cloudViewerURL(
            session: session, password: status.vncPassword, page: status.novncPage) else { return }
        var request = URLRequest(url: url)
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        view.load(request)
    }
}
