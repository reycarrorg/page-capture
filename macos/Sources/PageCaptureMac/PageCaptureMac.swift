import AppKit
import SwiftUI
import PageCaptureCore

@main
struct PageCaptureMacApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 520, minHeight: 360)
        }
    }
}

struct ContentView: View {
    @State private var session: PageCaptureSession?
    @State private var message = "No session open."

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Page Capture — Early macOS Core Milestone")
                .font(.title2.bold())
            Text("Local session ordering, exact duplicate checks, undo recovery, and image-based PDF export are implemented.")
                .foregroundStyle(.secondary)
            Text("Region capture and Screen Recording permission are not implemented in this milestone.")
                .font(.headline)
                .foregroundStyle(.orange)
            Divider()
            Text(session.map { "Session: \($0.directory.lastPathComponent)" } ?? "Create or open a local session.")
            Text("Pages: \(session?.pageCount ?? 0)")
            HStack {
                Button("Create Session") { createSession() }
                Button("Open Session") { openSession() }
                Button("Undo Latest") { undo() }.disabled(session?.pageCount ?? 0 == 0)
                Button("Export PDF") { export() }.disabled(session?.pageCount ?? 0 == 0)
            }
            Text(message).font(.callout).foregroundStyle(.secondary)
            Spacer()
        }
        .padding(24)
    }

    private func createSession() {
        do {
            let root = try FileManager.default.url(
                for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
            ).appendingPathComponent("Page Capture Sessions", isDirectory: true)
            session = try PageCaptureSession(root: root)
            message = "Ready for manually supplied PNG pages. Capture is not available yet."
        } catch { message = error.localizedDescription }
    }

    private func openSession() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            do {
                session = try PageCaptureSession.open(directory: url)
                message = "Opened existing local session."
            } catch { message = error.localizedDescription }
        }
    }

    private func undo() {
        do { _ = try session?.undoLatest(); message = "Latest page moved to Undone." }
        catch { message = error.localizedDescription }
    }

    private func export() {
        guard let session else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        panel.nameFieldStringValue = "Page Capture.pdf"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try session.exportPDF(to: url); message = "Exported \(session.pageCount) page(s) atomically." }
        catch { message = error.localizedDescription }
    }
}
