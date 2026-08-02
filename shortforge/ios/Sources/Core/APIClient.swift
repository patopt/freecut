import Foundation

enum APIError: LocalizedError {
    case notConfigured
    case unauthorized
    case server(String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "No server address set."
        case .unauthorized: return "Session expired — sign in again."
        case .server(let message): return message
        case .badResponse: return "The server sent something unexpected."
        }
    }
}

/// Talks to a ShortForge dashboard.
///
/// Auth is the dashboard's own signed session cookie, so the client only has to
/// keep the cookie jar alive; `URLSession` does that for us once the shared
/// storage is used.
actor APIClient {
    static let shared = APIClient()

    /// Prefilled on the sign-in screen and used when nothing is stored yet.
    /// Always overridable — the tunnel address changes whenever ngrok restarts
    /// without a reserved domain.
    static let defaultServer = "https://ultra-starless-lugged.ngrok-free.dev"

    /// The address currently in use, for display and for prefilling forms.
    static var storedServer: String {
        let saved = UserDefaults.standard.string(forKey: "sf.server") ?? ""
        return saved.isEmpty ? defaultServer : saved
    }

    static func setServer(_ raw: String) {
        UserDefaults.standard.set(normalize(raw), forKey: "sf.server")
    }

    private var session: URLSession
    private var decoder: JSONDecoder

    private init() {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.httpCookieAcceptPolicy = .always
        config.httpShouldSetCookies = true
        config.timeoutIntervalForRequest = 30
        config.waitsForConnectivity = true
        session = URLSession(configuration: config)
        decoder = JSONDecoder()
    }

    private var baseURL: URL? {
        guard let raw = UserDefaults.standard.string(forKey: "sf.server"),
              !raw.isEmpty else { return nil }
        return URL(string: Self.normalize(raw))
    }

    /// Users paste "my-tunnel.ngrok-free.app" as often as a full URL, and a
    /// trailing slash would double up on every path.
    static func normalize(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "https://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }

    func mediaURL(path: String) -> URL? {
        guard let base = baseURL else { return nil }
        return URL(string: base.absoluteString + path)
    }

    // MARK: - Transport

    @discardableResult
    private func send(_ path: String, method: String = "GET",
                      body: [String: Any]? = nil) async throws -> Data {
        guard let base = baseURL, let url = URL(string: base.absoluteString + path) else {
            throw APIError.notConfigured
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // ngrok's free tier serves an HTML interstitial to anything that looks
        // like a browser; this header opts out of it.
        request.setValue("true", forHTTPHeaderField: "ngrok-skip-browser-warning")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }

        switch http.statusCode {
        case 200..<300:
            return data
        case 401:
            throw APIError.unauthorized
        default:
            // FastAPI puts the useful part in `detail`.
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = object["detail"] as? String {
                throw APIError.server(detail)
            }
            throw APIError.server("Server error \(http.statusCode)")
        }
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        let data = try await send(path)
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.badResponse }
    }

    // MARK: - Auth

    func login(server: String, password: String) async throws {
        UserDefaults.standard.set(Self.normalize(server), forKey: "sf.server")
        _ = try await send("/api/login", method: "POST", body: ["password": password])
    }

    func logout() async {
        _ = try? await send("/api/logout", method: "POST")
        if let base = baseURL, let cookies = HTTPCookieStorage.shared.cookies(for: base) {
            cookies.forEach(HTTPCookieStorage.shared.deleteCookie)
        }
    }

    /// Cheapest authenticated call — used to decide whether a stored session is
    /// still good on cold launch.
    func probe() async -> Bool {
        (try? await send("/api/stats")) != nil
    }

    // MARK: - Reads

    // The API wraps every collection in a named key ({"jobs": [...]}), and the
    // detail endpoints embed their children rather than exposing sub-routes.

    private struct JobList: Decodable { let jobs: [Job] }
    private struct ChannelList: Decodable { let channels: [SourceChannel] }
    private struct ActivityList: Decodable { let activity: [Activity] }
    private struct JobDetail: Decodable { let shorts: [Short]? }
    private struct ChannelDetail: Decodable { let shorts: [ChannelShort]? }
    private struct SystemStatus: Decodable { let paused: Bool }

    func stats() async throws -> Stats { try await get("/api/stats", as: Stats.self) }

    func jobs() async throws -> [Job] {
        try await get("/api/jobs", as: JobList.self).jobs
    }

    func job(_ id: String) async throws -> Job {
        try await get("/api/jobs/\(id)", as: Job.self)
    }

    func shorts(jobId: String) async throws -> [Short] {
        try await get("/api/jobs/\(jobId)", as: JobDetail.self).shorts ?? []
    }

    func channels() async throws -> [SourceChannel] {
        try await get("/api/channels", as: ChannelList.self).channels
    }

    func channelShorts(_ id: String) async throws -> [ChannelShort] {
        try await get("/api/channels/\(id)", as: ChannelDetail.self).shorts ?? []
    }

    func activity(kind: String = "") async throws -> [Activity] {
        let path = "/api/activity" + (kind.isEmpty ? "" : "?kind=\(kind)")
        return try await get(path, as: ActivityList.self).activity
    }

    func isPaused() async throws -> Bool {
        try await get("/api/system/status", as: SystemStatus.self).paused
    }

    // MARK: - Writes

    func createJob(url: String, count: Int, captions: Bool,
                   minLen: Double, maxLen: Double, aspect: String) async throws {
        _ = try await send("/api/jobs", method: "POST", body: [
            "url": url, "count": count, "captions": captions,
            "min_len": minLen, "max_len": maxLen, "aspect": aspect,
        ])
    }

    func deleteJob(_ id: String) async throws {
        _ = try await send("/api/jobs/\(id)", method: "DELETE")
    }

    func retryJob(_ id: String) async throws {
        _ = try await send("/api/jobs/\(id)/retry", method: "POST")
    }

    func addChannel(url: String) async throws {
        _ = try await send("/api/channels", method: "POST", body: ["url": url])
    }

    func refreshChannel(_ id: String) async throws {
        _ = try await send("/api/channels/\(id)/refresh", method: "POST")
    }

    func setPaused(_ paused: Bool) async throws {
        _ = try await send(paused ? "/api/system/stop-all" : "/api/system/resume", method: "POST")
    }

    func clearFailedJobs() async throws {
        _ = try await send("/api/jobs/clear-failed", method: "POST")
    }

    func clearQueues() async throws {
        _ = try await send("/api/system/clear-queues", method: "POST")
    }

    // MARK: - Settings

    func settings() async throws -> ServerSettings {
        try await get("/api/settings", as: ServerSettings.self)
    }

    /// The settings endpoint takes a sparse body and only writes what it is
    /// given, so partial updates are the normal case rather than a special one.
    func updateSettings(_ patch: [String: Any]) async throws {
        _ = try await send("/api/settings", method: "POST", body: patch)
    }

    private struct CaptionStyleList: Decodable { let styles: [CaptionStyle] }
    private struct MusicList: Decodable { let music: [MusicTrack] }
    private struct YouTubeAccountList: Decodable { let accounts: [YouTubeAccount] }
    private struct TikTokAccountList: Decodable { let accounts: [TikTokAccount] }
    private struct MyChannelList: Decodable { let channels: [MyChannel] }

    func captionStyles() async throws -> [CaptionStyle] {
        try await get("/api/caption-styles", as: CaptionStyleList.self).styles
    }

    func music() async throws -> [MusicTrack] {
        try await get("/api/music", as: MusicList.self).music
    }

    func youtubeAccounts() async throws -> [YouTubeAccount] {
        try await get("/api/youtube/accounts", as: YouTubeAccountList.self).accounts
    }

    func tiktokAccounts() async throws -> [TikTokAccount] {
        try await get("/api/tiktok/accounts", as: TikTokAccountList.self).accounts
    }

    func myChannels() async throws -> [MyChannel] {
        try await get("/api/my-channels", as: MyChannelList.self).channels
    }

    // MARK: - VPN

    func vpnStatus() async throws -> VPNStatus {
        try await get("/api/vpn/status", as: VPNStatus.self)
    }

    func vpnRotate() async throws { _ = try await send("/api/vpn/rotate", method: "POST") }
    func vpnDisconnect() async throws { _ = try await send("/api/vpn/disconnect", method: "POST") }

    func vpnConnect(country: String) async throws {
        _ = try await send("/api/vpn/connect", method: "POST", body: ["country": country])
    }

    // MARK: - Cloud desktop

    func cloudStatus() async throws -> CloudStatus {
        try await get("/api/cloud/status", as: CloudStatus.self)
    }

    func cloudStart(width: Int, height: Int) async throws -> CloudSession {
        let data = try await send("/api/cloud/sessions", method: "POST",
                                  body: ["width": width, "height": height])
        return try decoder.decode(CloudSession.self, from: data)
    }

    func cloudStop(_ id: String) async throws {
        _ = try await send("/api/cloud/sessions/\(id)/stop", method: "POST")
    }

    func cloudStopAll() async throws {
        _ = try await send("/api/cloud/stop-all", method: "POST")
    }

    func cloudResize(_ id: String, width: Int, height: Int) async throws {
        _ = try await send("/api/cloud/sessions/\(id)/resize", method: "POST",
                           body: ["width": width, "height": height])
    }

    func cloudBrowser(_ id: String, url: String) async throws {
        _ = try await send("/api/cloud/sessions/\(id)/browser", method: "POST",
                           body: ["url": url])
    }

    /// The noVNC viewer address, with the connection parameters the page reads
    /// from its query string. Loaded in a web view that already carries the
    /// dashboard's session cookie.
    func cloudViewerURL(session: CloudSession, password: String, page: String) -> URL? {
        guard let base = baseURL, let host = base.host else { return nil }
        let secure = base.scheme == "https"
        var items = [
            URLQueryItem(name: "autoconnect", value: "1"),
            URLQueryItem(name: "reconnect", value: "1"),
            URLQueryItem(name: "resize", value: "scale"),
            URLQueryItem(name: "host", value: host),
            URLQueryItem(name: "port", value: base.port.map(String.init) ?? (secure ? "443" : "80")),
            URLQueryItem(name: "encrypt", value: secure ? "1" : "0"),
            URLQueryItem(name: "path", value: "api/cloud/ws/\(session.id)"),
        ]
        if !password.isEmpty { items.append(URLQueryItem(name: "password", value: password)) }
        var comps = URLComponents(string: base.absoluteString + "/novnc/" + page)
        comps?.queryItems = items
        return comps?.url
    }

    /// Cookies for the dashboard host, so a web view can be primed with the
    /// session before it loads the viewer.
    func sessionCookies() -> [HTTPCookie] {
        guard let base = baseURL else { return [] }
        return HTTPCookieStorage.shared.cookies(for: base) ?? []
    }
}
