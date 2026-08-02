import SwiftUI
import WebKit

/// The VPS's remote desktop, streamed into the app.
///
/// The viewer is the dashboard's own noVNC page in a `WKWebView` rather than a
/// hand-rolled RFB client: the server side already works, and reimplementing
/// the protocol would buy nothing but bugs.
///
/// Two things make it actually connect and actually be usable on a phone:
///
///  * the session token travels in the URL. A `WebSocket` opened from inside a
///    web view does not reliably carry the app's cookie, and the bridge closed
///    the socket with 1008 — a silent black rectangle.
///  * noVNC reports its state back over a script-message handler, so a failure
///    says why instead of showing nothing, and a toolbar sends the keys a
///    touchscreen has no way to produce.
struct CloudView: View {
    @EnvironmentObject private var state: AppState

    @State private var status = CloudStatus()
    @State private var active: CloudSession?
    @State private var token = ""
    @State private var loading = true
    @State private var busy = false
    @State private var fullscreen = false
    @State private var showPassword = false
    @State private var viewer = ViewerState()

    var body: some View {
        NavigationStack {
            Group {
                if !status.missing.isEmpty {
                    unavailable
                } else if let session = active {
                    viewerPane(session)
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
                    FullscreenDesktop(session: session, status: status,
                                      token: token, viewer: viewer)
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
                    Button { viewer.reconnect() } label: {
                        Label("Reconnect", systemImage: "arrow.clockwise")
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
            .disabled(!status.missing.isEmpty)
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

    private func viewerPane(_ session: CloudSession) -> some View {
        VStack(spacing: Theme.Space.sm) {
            if status.sessions.count > 1 { sessionChips(session) }

            DesktopSurface(session: session, status: status, token: token, viewer: viewer)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control,
                                            style: .continuous))
                .padding(.horizontal, Theme.Space.sm)

            RemoteKeyBar(viewer: viewer)
                .padding(.horizontal, Theme.Space.sm)

            Text(viewer.statusLine(fallback: "\(session.desktop) · \(session.resolution)"))
                .font(.caption2)
                .foregroundStyle(viewer.phase.isFailed ? Theme.danger : .tertiary)
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

    private func sessionChips(_ current: CloudSession) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Theme.Space.sm) {
                ForEach(Array(status.sessions.enumerated()), id: \.element.id) { i, s in
                    Button {
                        guard s.id != current.id else { return }
                        active = s
                        viewer.reset()
                    } label: {
                        Text("Desktop \(i + 1)")
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(s.id == current.id ? Theme.accent : .clear, in: Capsule())
                            .foregroundStyle(s.id == current.id ? .white : .secondary)
                            .overlay(Capsule().strokeBorder(
                                Color.primary.opacity(s.id == current.id ? 0 : 0.15)))
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
        }
    }

    // MARK: - Actions

    private func reload() async {
        token = await APIClient.shared.cloudToken()
        await state.perform("Could not reach the desktop service") {
            let fresh = try await APIClient.shared.cloudStatus()
            await MainActor.run {
                status = fresh
                if let current = active, fresh.sessions.contains(where: { $0.id == current.id }) {
                    return
                }
                active = fresh.sessions.first
                viewer.reset()
            }
        }
        loading = false
    }

    private func start() async {
        busy = true
        // A desktop matching the phone's portrait viewport is unusable — menus
        // and dialogs assume a landscape-ish canvas. Give it a laptop shape and
        // let noVNC scale it into whatever space the view has.
        await state.perform("Could not start a desktop") {
            let session = try await APIClient.shared.cloudStart(width: 1440, height: 900)
            await MainActor.run { active = session; viewer.reset() }
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
        viewer.reset()
        await reload()
    }

    private func stopAll() async {
        await state.perform("Could not stop the desktops") {
            try await APIClient.shared.cloudStopAll()
        }
        active = nil
        viewer.reset()
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

// MARK: - Viewer state

/// Shared handle between SwiftUI and the web view: connection phase, plus the
/// commands the toolbar needs to push into noVNC.
///
/// Deliberately not `@MainActor`: every mutation below already happens on the
/// main thread (SwiftUI callbacks, or an explicit hop from the coordinator),
/// and isolating the type would force awaits into plain view bodies.
final class ViewerState: ObservableObject {
    enum Phase: Equatable {
        case idle, connecting, connected, failed(String)

        var isFailed: Bool { if case .failed = self { return true }; return false }
    }

    @Published var phase: Phase = .idle
    /// Bumped to force a fresh load; the web view watches it.
    @Published var generation = 0
    /// Set by the toolbar, drained by the web view coordinator.
    @Published var pendingScript: String?

    func reset() {
        phase = .idle
        generation += 1
    }

    func reconnect() {
        phase = .connecting
        generation += 1
    }

    func run(_ script: String) { pendingScript = script }

    /// noVNC's `UI` object is module-scoped and never reaches `window`, so an
    /// injected classic script cannot call it. Its control bar is plain DOM
    /// though, and clicking those buttons is exactly what a user does — same
    /// code path, no module access needed.
    private func tap(_ elementID: String) {
        run("(function(){var b=document.getElementById('\(elementID)');"
            + "if(b){b.click();}})();")
    }

    func toggleKeyboard() { tap("noVNC_keyboard_button") }
    func toggleCtrl() { tap("noVNC_toggle_ctrl_button") }
    func toggleAlt() { tap("noVNC_toggle_alt_button") }
    func sendTab() { tap("noVNC_send_tab_button") }
    func sendEsc() { tap("noVNC_send_esc_button") }
    func sendCtrlAltDel() { tap("noVNC_send_ctrl_alt_del_button") }

    func statusLine(fallback: String) -> String {
        switch phase {
        case .idle: return fallback
        case .connecting: return "Connecting…"
        case .connected: return fallback
        case .failed(let reason): return "Disconnected — \(reason)"
        }
    }
}

/// Keys a phone keyboard has no way to send, plus a keyboard toggle.
private struct RemoteKeyBar: View {
    @ObservedObject var viewer: ViewerState

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                key("keyboard") { viewer.toggleKeyboard() }
                key("Ctrl", wide: true) { viewer.toggleCtrl() }
                key("Alt", wide: true) { viewer.toggleAlt() }
                key("Tab", wide: true) { viewer.sendTab() }
                key("Esc", wide: true) { viewer.sendEsc() }
                key("Ctrl+Alt+Del", wide: true) { viewer.sendCtrlAltDel() }
            }
            .padding(.vertical, 2)
        }
        .disabled(viewer.phase != .connected)
        .opacity(viewer.phase == .connected ? 1 : 0.4)
    }

    private func key(_ label: String, wide: Bool = false,
                     action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Group {
                if wide { Text(label).font(.caption.weight(.semibold)) }
                else { Image(systemName: label).font(.caption) }
            }
            .padding(.horizontal, wide ? 11 : 10)
            .padding(.vertical, 7)
            .background(.background.secondary, in: RoundedRectangle(
                cornerRadius: Theme.Radius.small, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.Radius.small, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.1)))
        }
        .buttonStyle(.plain)
    }
}

/// The viewer plus a status overlay, so a failure is visible instead of black.
private struct DesktopSurface: View {
    let session: CloudSession
    let status: CloudStatus
    let token: String
    @ObservedObject var viewer: ViewerState

    var body: some View {
        ZStack {
            Color.black
            DesktopWebView(session: session, status: status, token: token, viewer: viewer)

            switch viewer.phase {
            case .connecting, .idle:
                VStack(spacing: Theme.Space.sm) {
                    ProgressView().tint(.white)
                    Text("Connecting to the desktop…")
                        .font(.caption).foregroundStyle(.white.opacity(0.75))
                }
            case .failed(let reason):
                VStack(spacing: Theme.Space.md) {
                    Image(systemName: "bolt.horizontal.circle")
                        .font(.largeTitle).foregroundStyle(Theme.danger)
                    Text("Could not connect").font(.headline).foregroundStyle(.white)
                    Text(reason)
                        .font(.caption).foregroundStyle(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Theme.Space.lg)
                    Button("Retry") { viewer.reconnect() }
                        .buttonStyle(.borderedProminent)
                }
            case .connected:
                EmptyView()
            }
        }
    }
}

private struct FullscreenDesktop: View {
    let session: CloudSession
    let status: CloudStatus
    let token: String
    @ObservedObject var viewer: ViewerState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                DesktopSurface(session: session, status: status, token: token, viewer: viewer)
                RemoteKeyBar(viewer: viewer)
                    .padding(.horizontal, Theme.Space.sm)
                    .padding(.vertical, 6)
            }
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

// MARK: - Web view

/// Hosts the dashboard's noVNC page, primed with the session cookie and wired
/// to report its connection state back to SwiftUI.
struct DesktopWebView: UIViewRepresentable {
    let session: CloudSession
    let status: CloudStatus
    let token: String
    @ObservedObject var viewer: ViewerState

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        // noVNC dispatches connect/disconnect events on its RFB object; relay
        // them so a failure surfaces as a reason instead of a black rectangle.
        // noVNC's UI object is a module singleton, unreachable from an injected
        // classic script — so watch what it does to the DOM instead. It toggles
        // noVNC_connecting / noVNC_connected on <html> and writes failures into
        // #noVNC_status. That is a stable, observable contract.
        let bridge = """
        (function () {
          function post(kind, detail) {
            try { window.webkit.messageHandlers.vnc.postMessage({ kind: kind, detail: detail }); }
            catch (e) {}
          }
          var last = '';
          function report() {
            var root = document.documentElement;
            var status = document.getElementById('noVNC_status');
            var failed = status && status.classList.contains('noVNC_open')
                         && status.classList.contains('noVNC_status_error');
            var kind, detail = '';
            if (failed) {
              kind = 'failed';
              detail = (status.textContent || '').trim();
            } else if (root.classList.contains('noVNC_connected')) {
              kind = 'connected';
            } else if (root.classList.contains('noVNC_connecting')
                       || root.classList.contains('noVNC_reconnecting')) {
              kind = 'connecting';
            } else {
              return;   // 'init' and 'disconnected' are not worth reporting yet
            }
            var signature = kind + '|' + detail;
            if (signature !== last) { last = signature; post(kind, detail); }
          }

          function watch() {
            var status = document.getElementById('noVNC_status');
            if (!status) { return false; }
            new MutationObserver(report).observe(document.documentElement,
              { attributes: true, attributeFilter: ['class'] });
            new MutationObserver(report).observe(status,
              { attributes: true, childList: true, subtree: true,
                attributeFilter: ['class'] });
            report();
            return true;
          }

          var tries = 0;
          var timer = setInterval(function () {
            if (watch() || ++tries > 100) { clearInterval(timer); }
            if (tries > 100) { post('failed', 'the viewer did not load'); }
          }, 100);

          // A connection that never completes leaves noVNC on 'connecting'
          // forever; say so rather than spinning indefinitely.
          setTimeout(function () {
            if (!document.documentElement.classList.contains('noVNC_connected')) {
              post('failed', 'timed out reaching the desktop');
            }
          }, 25000);
        })();
        """
        config.userContentController.addUserScript(
            WKUserScript(source: bridge, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController.add(context.coordinator, name: "vnc")

        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false
        view.backgroundColor = .black
        view.scrollView.bounces = false
        // The desktop handles its own panning; letting the scroll view respond
        // makes every drag fight the remote pointer.
        view.scrollView.isScrollEnabled = false
        view.scrollView.contentInsetAdjustmentBehavior = .never
        context.coordinator.viewer = viewer
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        context.coordinator.viewer = viewer

        if context.coordinator.loadedGeneration != viewer.generation
            || context.coordinator.loadedSession != session.id {
            context.coordinator.loadedGeneration = viewer.generation
            context.coordinator.loadedSession = session.id
            Task { await load(into: view) }
        }

        if let script = viewer.pendingScript {
            // Cleared first so a re-render cannot replay the same keystroke.
            Task { @MainActor in
                viewer.pendingScript = nil
                view.evaluateJavaScript(script)
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        var loadedSession: String?
        var loadedGeneration = -1
        weak var viewer: ViewerState?

        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any],
                  let kind = body["kind"] as? String else { return }
            let detail = body["detail"] as? String ?? ""
            Task { @MainActor [weak self] in
                switch kind {
                case "connecting": self?.viewer?.phase = .connecting
                case "connected": self?.viewer?.phase = .connected
                default: self?.viewer?.phase = .failed(detail.isEmpty ? "disconnected" : detail)
                }
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!,
                     withError error: Error) {
            Task { @MainActor [weak self] in
                self?.viewer?.phase = .failed(error.localizedDescription)
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            Task { @MainActor [weak self] in
                self?.viewer?.phase = .failed(error.localizedDescription)
            }
        }
    }

    @MainActor
    private func load(into view: WKWebView) async {
        viewer.phase = .connecting

        let cookies = await APIClient.shared.sessionCookies()
        let store = view.configuration.websiteDataStore.httpCookieStore
        for cookie in cookies { await store.setCookie(cookie) }

        guard let url = await APIClient.shared.cloudViewerURL(
            session: session, password: status.vncPassword,
            page: status.novncPage, token: token) else {
            viewer.phase = .failed("no server address configured")
            return
        }
        var request = URLRequest(url: url)
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        view.load(request)
    }
}
