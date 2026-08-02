import Foundation

// The dashboard's API is hand-rolled JSON that has grown field by field, so an
// older server genuinely may not send everything a newer client knows about.
//
// Every model below decodes explicitly with `decodeIfPresent` rather than
// relying on synthesised conformance: a synthesised `init(from:)` throws
// `keyNotFound` for a missing non-optional key, which would blank a whole
// screen because one counter was added after the server was deployed.

private extension KeyedDecodingContainer {
    func int(_ key: Key, or fallback: Int) -> Int {
        ((try? decodeIfPresent(Int.self, forKey: key)) ?? nil) ?? fallback
    }
    func bool(_ key: Key, or fallback: Bool) -> Bool {
        ((try? decodeIfPresent(Bool.self, forKey: key)) ?? nil) ?? fallback
    }
    func string(_ key: Key, or fallback: String) -> String {
        ((try? decodeIfPresent(String.self, forKey: key)) ?? nil) ?? fallback
    }
    func string(_ key: Key) -> String? {
        (try? decodeIfPresent(String.self, forKey: key)) ?? nil
    }
    func double(_ key: Key) -> Double? {
        (try? decodeIfPresent(Double.self, forKey: key)) ?? nil
    }
    func int(_ key: Key) -> Int? {
        (try? decodeIfPresent(Int.self, forKey: key)) ?? nil
    }
}

struct Stats: Decodable {
    var jobs = 0
    var jobsRunning = 0
    var jobsFailed = 0
    var shorts = 0
    var sources = 0
    var dubs = 0
    var dubsRunning = 0
    var published = 0
    var publishPending = 0
    var myChannels = 0
    var accounts = 0
    var paused = false

    private enum Key: String, CodingKey {
        case jobs, shorts, sources, dubs, published, accounts, paused
        case jobsRunning = "jobs_running"
        case jobsFailed = "jobs_failed"
        case dubsRunning = "dubs_running"
        case publishPending = "publish_pending"
        case myChannels = "my_channels"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        jobs = c.int(.jobs, or: 0)
        jobsRunning = c.int(.jobsRunning, or: 0)
        jobsFailed = c.int(.jobsFailed, or: 0)
        shorts = c.int(.shorts, or: 0)
        sources = c.int(.sources, or: 0)
        dubs = c.int(.dubs, or: 0)
        dubsRunning = c.int(.dubsRunning, or: 0)
        published = c.int(.published, or: 0)
        publishPending = c.int(.publishPending, or: 0)
        myChannels = c.int(.myChannels, or: 0)
        accounts = c.int(.accounts, or: 0)
        paused = c.bool(.paused, or: false)
    }
}

struct Job: Decodable, Identifiable, Hashable {
    var id = ""
    var url: String?
    var title: String?
    var status = "queued"
    var stage: String?
    var message: String?
    var error: String?
    var progress = 0
    var duration: Double?
    var numShorts: Int?
    var createdAt: Double?

    private enum Key: String, CodingKey {
        case id, url, title, status, stage, message, error, progress, duration
        case numShorts = "num_shorts"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        url = c.string(.url)
        title = c.string(.title)
        status = c.string(.status, or: "queued")
        stage = c.string(.stage)
        message = c.string(.message)
        error = c.string(.error)
        progress = c.int(.progress, or: 0)
        duration = c.double(.duration)
        numShorts = c.int(.numShorts)
        createdAt = c.double(.createdAt)
    }

    var displayTitle: String {
        if let t = title, !t.isEmpty { return t }
        if let u = url, !u.isEmpty { return u }
        return "Untitled video"
    }
}

struct Short: Decodable, Identifiable, Hashable {
    var id = ""
    var idx: Int?
    var title: String?
    var reason: String?
    var score: Double?
    var start: Double?
    var end: Double?
    var hook: Double?
    var flow: Double?
    var value: Double?
    var trend: Double?
    var hookText: String?

    private enum Key: String, CodingKey {
        case id, idx, title, reason, score, start, end, hook, flow, value, trend
        case hookText = "hook_text"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        idx = c.int(.idx)
        title = c.string(.title)
        reason = c.string(.reason)
        score = c.double(.score)
        start = c.double(.start)
        end = c.double(.end)
        hook = c.double(.hook)
        flow = c.double(.flow)
        value = c.double(.value)
        trend = c.double(.trend)
        hookText = c.string(.hookText)
    }

    var duration: Double {
        guard let s = start, let e = end else { return 0 }
        return max(0, e - s)
    }

    /// Zero when the model returned no virality breakdown, which is how the UI
    /// decides whether the signal bars are worth showing at all.
    var signalTotal: Double {
        (hook ?? 0) + (flow ?? 0) + (value ?? 0) + (trend ?? 0)
    }
}

struct SourceChannel: Decodable, Identifiable, Hashable {
    var id = ""
    var url: String?
    var name: String?
    var status: String?
    var shortsCount: Int?

    private enum Key: String, CodingKey {
        case id, url, name, status
        case shortsCount = "shorts_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        url = c.string(.url)
        name = c.string(.name)
        status = c.string(.status)
        shortsCount = c.int(.shortsCount)
    }

    var displayName: String {
        if let n = name, !n.isEmpty { return n }
        return url ?? "Channel"
    }
}

/// A translation attached to a source video, as embedded in the channel listing.
struct DubRef: Decodable, Identifiable, Hashable {
    var id = ""
    var lang = ""
    var status = "queued"
    var progress = 0

    private enum Key: String, CodingKey { case id, lang, status, progress }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        lang = c.string(.lang, or: "??")
        status = c.string(.status, or: "queued")
        progress = c.int(.progress, or: 0)
    }

    /// What the chip reads: language, plus progress while it is still working.
    var label: String {
        switch status {
        case "done": return "▶ " + lang.uppercased()
        case "error": return lang.uppercased() + " ✕"
        default: return "\(lang.uppercased()) \(progress)%"
        }
    }
}

struct ChannelShort: Decodable, Identifiable, Hashable {
    var id = ""
    var videoId: String?
    var title: String?
    var url: String?
    /// An absolute YouTube CDN address, not a dashboard route — the server
    /// stores what the listing returned rather than re-hosting the image.
    var thumb: String?
    var duration: Double?
    /// Translations already queued or finished for this video.
    var dubs: [DubRef] = []

    private enum Key: String, CodingKey {
        case id, title, url, thumb, duration, dubs
        case videoId = "video_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        videoId = c.string(.videoId)
        title = c.string(.title)
        url = c.string(.url)
        thumb = c.string(.thumb)
        duration = c.double(.duration)
        dubs = ((try? c.decodeIfPresent([DubRef].self, forKey: .dubs)) ?? nil) ?? []
    }
}

/// A translation in full, as returned by /api/dubs/{id}.
struct Dub: Decodable, Identifiable, Hashable {
    var id = ""
    var shortId = ""
    var lang = ""
    var status = "queued"
    var progress = 0
    var stage: String?
    var message: String?
    var error: String?
    var log: String?

    private enum Key: String, CodingKey {
        case id, lang, status, progress, stage, message, error, log
        case shortId = "short_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        shortId = c.string(.shortId, or: "")
        lang = c.string(.lang, or: "")
        status = c.string(.status, or: "queued")
        progress = c.int(.progress, or: 0)
        stage = c.string(.stage)
        message = c.string(.message)
        error = c.string(.error)
        log = c.string(.log)
    }
}

struct Activity: Decodable, Identifiable, Hashable {
    var id = ""
    var kind: String?
    var title: String?
    var detail: String?
    var level: String?
    var createdAt: Double?

    private enum Key: String, CodingKey {
        case id, kind, title, detail, level
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = c.string(.id, or: "")
        kind = c.string(.kind)
        title = c.string(.title)
        detail = c.string(.detail)
        level = c.string(.level)
        createdAt = c.double(.createdAt)
    }
}

/// Status vocabulary shared by jobs and dubs.
enum WorkStatus {
    static func isTerminal(_ status: String) -> Bool {
        status == "done" || status == "error" || status == "canceled"
    }

    static func label(_ status: String) -> String {
        [
            "queued": "Queued", "downloading": "Downloading", "transcribing": "Transcribing",
            "analyzing": "Analyzing", "translating": "Translating", "dubbing": "Voicing",
            "rendering": "Rendering", "done": "Done", "error": "Failed",
            "canceled": "Canceled", "ready": "Ready", "fetching": "Fetching",
        ][status] ?? status.capitalized
    }
}
