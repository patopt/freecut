import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var state: AppState

    @State private var paused = false
    @State private var busy = false
    @State private var confirmSignOut = false
    @State private var editingServer = false
    @State private var serverDraft = ""
    @State private var reachable: Bool?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Address") {
                        Text(state.serverAddress)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.head)
                    }
                    LabeledContent("Status") {
                        switch reachable {
                        case .some(true):
                            Label("Connected", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(Theme.success).font(.footnote)
                        case .some(false):
                            Label("Unreachable", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(Theme.danger).font(.footnote)
                        case nil:
                            ProgressView().controlSize(.small)
                        }
                    }
                    Button {
                        serverDraft = state.serverAddress
                        editingServer = true
                    } label: {
                        Label("Change server address", systemImage: "pencil")
                    }
                } header: {
                    Text("Server")
                } footer: {
                    Text("An ngrok tunnel gets a new address every restart unless you reserve a "
                         + "domain — change it here when it moves.")
                }

                Section("Accounts") {
                    counterRow("Publishing accounts", state.stats.accounts, "person.2")
                    counterRow("My channels", state.stats.myChannels, "tv")
                }

                Section {
                    Toggle("Pause all queues", isOn: Binding(
                        get: { paused },
                        set: { newValue in Task { await setPaused(newValue) } }
                    ))
                    .disabled(busy)
                } footer: {
                    Text("Pausing stops new work from starting and disables auto-publishing. "
                         + "Anything already running finishes.")
                }

                Section("Pipeline") {
                    counterRow("Source videos", state.stats.jobs, "film")
                    counterRow("Shorts generated", state.stats.shorts, "rectangle.stack")
                    counterRow("Translated", state.stats.dubs, "globe")
                    counterRow("Published", state.stats.published, "paperplane")
                    if state.stats.jobsFailed > 0 {
                        Button(role: .destructive) {
                            Task {
                                await state.perform("Could not clear failed jobs") {
                                    try await APIClient.shared.clearFailedJobs()
                                }
                                await state.refreshStats()
                            }
                        } label: {
                            Label("Clear \(state.stats.jobsFailed) failed", systemImage: "trash")
                        }
                    }
                }

                Section {
                    Button(role: .destructive) { confirmSignOut = true } label: {
                        Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                } footer: {
                    Text("Full settings — API keys, accounts, watermark, cloud desktop — live in "
                         + "the web dashboard.")
                }
            }
            .navigationTitle("Settings")
            .refreshable { await reload() }
            .task { await reload() }
            .confirmationDialog("Sign out of this server?",
                                isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) { Task { await state.signOut() } }
            }
            .alert("Server address", isPresented: $editingServer) {
                TextField("https://…", text: $serverDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                Button("Cancel", role: .cancel) {}
                Button("Save") { Task { await saveServer() } }
            } message: {
                Text("Changing the address signs you out, so you can sign in to the new one.")
            }
        }
    }

    private func counterRow(_ label: String, _ value: Int, _ icon: String) -> some View {
        LabeledContent {
            Text("\(value)").monospacedDigit().foregroundStyle(.secondary)
        } label: {
            Label(label, systemImage: icon)
        }
    }

    private func reload() async {
        reachable = await APIClient.shared.probe()
        await state.refreshStats()
        paused = state.stats.paused
    }

    /// A different host means a different session cookie, so the only honest
    /// thing to do is drop the current one and let the user sign in again.
    private func saveServer() async {
        let draft = serverDraft.trimmingCharacters(in: .whitespaces)
        guard !draft.isEmpty else { return }
        await state.signOut()
        APIClient.setServer(draft)
    }

    private func setPaused(_ value: Bool) async {
        busy = true
        await state.perform("Could not change the queue state") {
            try await APIClient.shared.setPaused(value)
        }
        await reload()
        busy = false
    }
}
