import SwiftUI

@main
struct ShortForgeApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .task { await state.bootstrap() }
                .tint(Theme.accent)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        ZStack {
            ForgeBackground()

            switch state.phase {
            case .checking:
                ProgressView().controlSize(.large)
            case .signedOut:
                LoginView()
            case .signedIn:
                MainTabView()
            }
        }
        .animation(.smooth(duration: 0.3), value: state.phase)
        .overlay(alignment: .top) { bannerView }
    }

    @ViewBuilder
    private var bannerView: some View {
        if let banner = state.banner {
            HStack(spacing: Theme.Space.sm) {
                Image(systemName: banner.isError ? "exclamationmark.triangle.fill"
                                                 : "checkmark.circle.fill")
                    .foregroundStyle(banner.isError ? Theme.danger : Theme.success)
                Text(banner.message)
                    .font(.footnote.weight(.medium))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Theme.Space.lg)
            .padding(.vertical, Theme.Space.md)
            .glassLayer(cornerRadius: Theme.Radius.control)
            .padding(.horizontal, Theme.Space.lg)
            .transition(.move(edge: .top).combined(with: .opacity))
            .task(id: banner.id) {
                try? await Task.sleep(for: .seconds(4))
                withAnimation(.smooth) { state.banner = nil }
            }
            .onTapGesture { withAnimation(.smooth) { state.banner = nil } }
        }
    }
}

struct MainTabView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        TabView {
            ClipView()
                .tabItem { Label("Clip", systemImage: "scissors") }
                .badge(state.stats.jobsRunning)

            CopyView()
                .tabItem { Label("Copy", systemImage: "waveform") }
                .badge(state.stats.dubsRunning)

            CloudView()
                .tabItem { Label("Cloud", systemImage: "desktopcomputer") }

            LogsView()
                .tabItem { Label("Activity", systemImage: "list.bullet.rectangle") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
