import Foundation

// Same decoding discipline as Models.swift: every key optional at the wire
// level, so a server that predates a field renders a screen instead of an error.

private extension KeyedDecodingContainer {
    func str(_ key: Key, or fallback: String = "") -> String {
        ((try? decodeIfPresent(String.self, forKey: key)) ?? nil) ?? fallback
    }
    func flag(_ key: Key, or fallback: Bool = false) -> Bool {
        ((try? decodeIfPresent(Bool.self, forKey: key)) ?? nil) ?? fallback
    }
    func num(_ key: Key, or fallback: Double) -> Double {
        ((try? decodeIfPresent(Double.self, forKey: key)) ?? nil) ?? fallback
    }
    func count(_ key: Key, or fallback: Int = 0) -> Int {
        ((try? decodeIfPresent(Int.self, forKey: key)) ?? nil) ?? fallback
    }
    func list(_ key: Key) -> [String] {
        ((try? decodeIfPresent([String].self, forKey: key)) ?? nil) ?? []
    }
}

struct Watermark: Decodable, Equatable {
    var enabled = false
    var text = ""
    var position = "bottom-right"
    var opacity = 0.35
    var size = 3.0
    var fontAvailable = true
    var positions: [String] = ["bottom-right"]

    private enum Key: String, CodingKey {
        case enabled, text, position, opacity, size, positions
        case fontAvailable = "font_available"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        enabled = c.flag(.enabled)
        text = c.str(.text)
        position = c.str(.position, or: "bottom-right")
        opacity = c.num(.opacity, or: 0.35)
        size = c.num(.size, or: 3.0)
        fontAvailable = c.flag(.fontAvailable, or: true)
        let list = c.list(.positions)
        positions = list.isEmpty ? ["bottom-right"] : list
    }
}

struct ServerSettings: Decodable {
    var geminiKeySet = false
    var geminiModel = ""
    var whisperModel = "small"
    var ttsEngine = "kokoro"
    var ngrokTokenSet = false
    var defaultDubMusic = ""
    var defaultCaptionStyle = ""
    var defaultDubCaptions = ""
    var googleClientIdSet = false
    var googleClientSecretSet = false
    var tiktokClientKeySet = false
    var tiktokClientSecretSet = false
    var vpnRotation = false
    var useYouTubeCookies = false
    var watermark = Watermark()

    private enum Key: String, CodingKey {
        case watermark
        case geminiKeySet = "gemini_api_key_set"
        case geminiModel = "gemini_model"
        case whisperModel = "whisper_model"
        case ttsEngine = "tts_engine"
        case ngrokTokenSet = "ngrok_authtoken_set"
        case defaultDubMusic = "default_dub_music"
        case defaultCaptionStyle = "default_caption_style"
        case defaultDubCaptions = "default_dub_captions"
        case googleClientIdSet = "google_client_id_set"
        case googleClientSecretSet = "google_client_secret_set"
        case tiktokClientKeySet = "tiktok_client_key_set"
        case tiktokClientSecretSet = "tiktok_client_secret_set"
        case vpnRotation = "vpn_rotation"
        case useYouTubeCookies = "use_youtube_cookies"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        geminiKeySet = c.flag(.geminiKeySet)
        geminiModel = c.str(.geminiModel)
        whisperModel = c.str(.whisperModel, or: "small")
        ttsEngine = c.str(.ttsEngine, or: "kokoro")
        ngrokTokenSet = c.flag(.ngrokTokenSet)
        defaultDubMusic = c.str(.defaultDubMusic)
        defaultCaptionStyle = c.str(.defaultCaptionStyle)
        defaultDubCaptions = c.str(.defaultDubCaptions)
        googleClientIdSet = c.flag(.googleClientIdSet)
        googleClientSecretSet = c.flag(.googleClientSecretSet)
        tiktokClientKeySet = c.flag(.tiktokClientKeySet)
        tiktokClientSecretSet = c.flag(.tiktokClientSecretSet)
        vpnRotation = c.flag(.vpnRotation)
        useYouTubeCookies = c.flag(.useYouTubeCookies)
        watermark = ((try? c.decodeIfPresent(Watermark.self, forKey: .watermark)) ?? nil)
            ?? Watermark()
    }
}

struct CaptionStyle: Decodable, Identifiable, Hashable {
    var id = ""
    var name = ""

    private enum Key: String, CodingKey { case id, name, label, title }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        // The preset list has used more than one key for its display name.
        let candidates = [c.str(.name), c.str(.label), c.str(.title)]
        name = candidates.first { !$0.isEmpty } ?? id
    }
}

struct MusicTrack: Decodable, Identifiable, Hashable {
    var id = ""
    var name = ""

    private enum Key: String, CodingKey { case id, name }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        name = c.str(.name, or: "Track")
    }
}

struct YouTubeChannelRef: Decodable, Identifiable, Hashable {
    var id = ""
    var title = ""

    private enum Key: String, CodingKey { case id, title }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        title = c.str(.title, or: "Channel")
    }
}

struct YouTubeAccount: Decodable, Identifiable, Hashable {
    var id = ""
    var email = ""
    var channels: [YouTubeChannelRef] = []

    private enum Key: String, CodingKey { case id, email, channels }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        email = c.str(.email, or: "Google account")
        channels = ((try? c.decodeIfPresent([YouTubeChannelRef].self, forKey: .channels)) ?? nil) ?? []
    }
}

struct TikTokAccount: Decodable, Identifiable, Hashable {
    var id = ""
    var name = ""
    var autoEnabled = false
    var mode = "api"
    var cookieCount = 0
    var autoReady = false

    private enum Key: String, CodingKey {
        case id, name, mode
        case autoEnabled = "auto_enabled"
        case cookieCount = "cookie_count"
        case autoReady = "auto_ready"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        name = c.str(.name, or: "TikTok account")
        autoEnabled = c.flag(.autoEnabled)
        mode = c.str(.mode, or: "api")
        cookieCount = c.count(.cookieCount)
        autoReady = c.flag(.autoReady)
    }

    var profileURL: URL? {
        URL(string: "https://www.tiktok.com/@\(id)")
    }
}

struct MyChannel: Decodable, Identifiable, Hashable {
    var id = ""
    var name = ""
    var kind = "local"
    var published = 0
    var pending = 0
    var autoEnabled = false

    private enum Key: String, CodingKey {
        case id, name, kind, title, published, pending
        case autoEnabled = "auto_enabled"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        let n = c.str(.name)
        name = n.isEmpty ? c.str(.title, or: "Channel") : n
        kind = c.str(.kind, or: "local")
        published = c.count(.published)
        pending = c.count(.pending)
        autoEnabled = c.flag(.autoEnabled)
    }
}

struct VPNStatus: Decodable {
    var installed = false
    var loggedIn = false
    var connected = false
    var server = ""
    var country = ""
    var ip = ""
    var rotationEnabled = false
    var countries: [String] = []

    private enum Key: String, CodingKey {
        case installed, connected, server, country, ip, countries
        case loggedIn = "logged_in"
        case rotationEnabled = "rotation_enabled"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        installed = c.flag(.installed)
        loggedIn = c.flag(.loggedIn)
        connected = c.flag(.connected)
        server = c.str(.server)
        country = c.str(.country)
        ip = c.str(.ip)
        rotationEnabled = c.flag(.rotationEnabled)
        countries = c.list(.countries)
    }
}

struct CloudSession: Decodable, Identifiable, Hashable {
    var id = ""
    var width = 0
    var height = 0
    var desktop = ""
    var running = false

    private enum Key: String, CodingKey { case id, width, height, desktop, running }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.str(.id)
        width = c.count(.width)
        height = c.count(.height)
        desktop = c.str(.desktop)
        running = c.flag(.running, or: true)
    }

    var resolution: String { "\(width)×\(height)" }
}

struct CloudStatus: Decodable {
    var sessions: [CloudSession] = []
    var maxSessions = 3
    var desktop = ""
    var missing: [String] = []
    var vncPassword = ""
    var novncPage = "vnc.html"

    private enum Key: String, CodingKey {
        case sessions, desktop, missing
        case maxSessions = "max_sessions"
        case vncPassword = "vnc_password"
        case novncPage = "novnc_page"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        sessions = ((try? c.decodeIfPresent([CloudSession].self, forKey: .sessions)) ?? nil) ?? []
        maxSessions = c.count(.maxSessions, or: 3)
        desktop = c.str(.desktop)
        missing = c.list(.missing)
        vncPassword = c.str(.vncPassword)
        novncPage = c.str(.novncPage, or: "vnc.html")
    }
}
