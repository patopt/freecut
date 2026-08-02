import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var state: AppState

    @State private var server = APIClient.storedServer
    @State private var password = ""
    @State private var busy = false
    @State private var error = ""
    @FocusState private var focus: Field?

    private enum Field { case server, password }

    var body: some View {
        ScrollView {
            VStack(spacing: Theme.Space.lg) {
                Spacer(minLength: 60)

                VStack(spacing: Theme.Space.sm) {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(Theme.accentGradient)
                        .frame(width: 62, height: 62)
                        .overlay(Image(systemName: "bolt.fill")
                            .font(.title).foregroundStyle(.white))
                        .shadow(color: Theme.accent.opacity(0.5), radius: 18, y: 8)
                    Text("ShortForge").font(.title.bold())
                    Text("Connect to your video factory")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .padding(.bottom, Theme.Space.sm)

                VStack(alignment: .leading, spacing: Theme.Space.md) {
                    field("Server", systemImage: "server.rack") {
                        TextField("my-tunnel.ngrok-free.app", text: $server)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                            .textContentType(.URL)
                            .focused($focus, equals: .server)
                            .submitLabel(.next)
                            .onSubmit { focus = .password }
                    }

                    field("Password", systemImage: "lock") {
                        SecureField("Dashboard password", text: $password)
                            .textContentType(.password)
                            .focused($focus, equals: .password)
                            .submitLabel(.go)
                            .onSubmit { Task { await signIn() } }
                    }

                    if !error.isEmpty {
                        Text(error).font(.footnote).foregroundStyle(Theme.danger)
                    }

                    Button(action: { Task { await signIn() } }) {
                        HStack {
                            if busy { ProgressView().controlSize(.small).tint(.white) }
                            Text(busy ? "Connecting…" : "Sign in").fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Theme.accentGradient, in: RoundedRectangle(
                            cornerRadius: Theme.Radius.control, style: .continuous))
                        .foregroundStyle(.white)
                    }
                    .disabled(busy || server.isEmpty || password.isEmpty)
                    .opacity(busy || server.isEmpty || password.isEmpty ? 0.55 : 1)
                }
                .cardSurface()

                Text("The address is the same one you open in a browser — including https:// if you use it.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Space.lg)

                Spacer(minLength: 40)
            }
            .frame(maxWidth: 420)
            .frame(maxWidth: .infinity)
            .padding(Theme.Space.lg)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    @ViewBuilder
    private func field<Content: View>(_ label: String, systemImage: String,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(label, systemImage: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            content()
                .padding(.horizontal, Theme.Space.md)
                .padding(.vertical, 11)
                .background(.background.tertiary,
                            in: RoundedRectangle(cornerRadius: Theme.Radius.control,
                                                 style: .continuous))
        }
    }

    private func signIn() async {
        guard !busy else { return }
        busy = true
        error = ""
        do {
            try await state.signIn(server: server, password: password)
        } catch {
            self.error = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        busy = false
    }
}
