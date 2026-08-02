import SwiftUI

/// One observable object for session state and the counters the whole app
/// shows, so a pull-to-refresh anywhere keeps the tab badges honest.
@MainActor
final class AppState: ObservableObject {
    enum Phase: Equatable {
        case checking
        case signedOut
        case signedIn
    }

    @Published var phase: Phase = .checking
    @Published var stats = Stats()
    @Published var banner: Banner?

    struct Banner: Identifiable, Equatable {
        let id = UUID()
        let message: String
        let isError: Bool
    }

    var serverAddress: String { APIClient.storedServer }

    func bootstrap() async {
        guard !serverAddress.isEmpty else {
            phase = .signedOut
            return
        }
        phase = await APIClient.shared.probe() ? .signedIn : .signedOut
        if phase == .signedIn { await refreshStats() }
    }

    func signIn(server: String, password: String) async throws {
        try await APIClient.shared.login(server: server, password: password)
        phase = .signedIn
        await refreshStats()
    }

    func signOut() async {
        await APIClient.shared.logout()
        phase = .signedOut
    }

    func refreshStats() async {
        if let fresh = try? await APIClient.shared.stats() { stats = fresh }
    }

    func show(_ message: String, isError: Bool = false) {
        banner = Banner(message: message, isError: isError)
    }

    /// Runs `work`, surfacing any failure as a banner instead of swallowing it,
    /// and signs out when the session has expired.
    func perform(_ description: String, _ work: @escaping () async throws -> Void) async {
        do {
            try await work()
        } catch APIError.unauthorized {
            phase = .signedOut
            show("Session expired — sign in again.", isError: true)
        } catch {
            show("\(description): \(error.localizedDescription)", isError: true)
        }
    }
}
