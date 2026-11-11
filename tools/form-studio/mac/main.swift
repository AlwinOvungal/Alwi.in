// Form Studio: the native Mac shell.
//
// A small AppKit app that starts the Python server (hidden, in the background),
// shows a "Starting…" screen until it answers, then shows the interface in a
// WebKit view. Quitting the app stops the server. Built by scripts/build-app.sh.

import AppKit
import WebKit

let paper = NSColor(srgbRed: 0.965, green: 0.961, blue: 0.945, alpha: 1)

/// A free TCP port on 127.0.0.1, preferring `preferred`.
func freePort(preferred: Int) -> Int {
    for candidate in [preferred, 0] {
        let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { continue }
        defer { Darwin.close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(candidate).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if bound != 0 { continue }
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.getsockname(fd, $0, &len) }
        }
        if named == 0 { return Int(UInt16(bigEndian: addr.sin_port)) }
    }
    return preferred
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate
{
    var window: NSWindow!
    var webView: WKWebView!
    var loadingView: NSStackView!
    var statusLabel: NSTextField!
    var server: Process?
    var port = 8765
    var startedAt = Date()
    var pollTimer: Timer?
    var ready = false
    var failed = false
    var stopping = false

    let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("FormStudio")
    var logURL: URL { home.appendingPathComponent("formstudio.log") }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    // MARK: lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { stopServer() }

    // MARK: server

    func startServer() {
        guard let root = Bundle.main.object(forInfoDictionaryKey: "FSProjectRoot") as? String else {
            return fail("This copy of Form Studio.app doesn't know where its files are. Run scripts/build-app.sh again.")
        }
        let python = root + "/.venv/bin/python"
        guard FileManager.default.isExecutableFile(atPath: python) else {
            return fail("Form Studio's Python environment wasn't found at\n\(python)\n\nRun scripts/setup-mac.sh in the form-studio folder.")
        }

        port = freePort(preferred: 8765)
        try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        // -P and starting in backend/: a stray "formstudio" folder elsewhere can't shadow the real package
        p.arguments = ["-P", "-m", "formstudio.desktop", "--serve", "--port", String(port)]
        p.currentDirectoryURL = URL(fileURLWithPath: root + "/backend")
        var env = ProcessInfo.processInfo.environment
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.seekToEndOfFile()
            log.write("\n=== Form Studio starting \(Date()) on port \(port) ===\n".data(using: .utf8)!)
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            DispatchQueue.main.async { self?.serverExited(status) }
        }
        do {
            try p.run()
        } catch {
            return fail("Couldn't start Python: \(error.localizedDescription)")
        }
        server = p
        startedAt = Date()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.3, repeats: true) { [weak self] _ in self?.poll() }
    }

    func poll() {
        if ready || failed { return }
        let waited = Date().timeIntervalSince(startedAt)
        if waited > 180 {
            return fail("The server didn't start within 3 minutes.")
        }
        if waited > 12 {
            statusLabel.stringValue = "Starting Form Studio… (the first start takes longer)"
        }
        var req = URLRequest(url: baseURL.appendingPathComponent("api/health"))
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { [weak self] _, response, _ in
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return }
            DispatchQueue.main.async { self?.serverReady() }
        }.resume()
    }

    func serverReady() {
        if ready || failed { return }
        ready = true
        pollTimer?.invalidate()
        webView.load(URLRequest(url: baseURL))
    }

    func serverExited(_ status: Int32) {
        if stopping { return }
        fail("The Form Studio server stopped unexpectedly (exit code \(status)).")
    }

    func stopServer() {
        stopping = true
        pollTimer?.invalidate()
        guard let p = server, p.isRunning else { return }
        p.terminate()  // SIGTERM: uvicorn shuts down cleanly
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { usleep(50_000) }
        if p.isRunning { Darwin.kill(p.processIdentifier, SIGKILL) }
    }

    func lastLogLines(_ n: Int) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).suffix(n)
        let joined = lines.joined(separator: "\n")
        return joined.count > 1800 ? "…" + String(joined.suffix(1800)) : joined
    }

    func fail(_ message: String) {
        if failed { return }
        failed = true
        pollTimer?.invalidate()
        stopServer()
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Form Studio couldn't start"
        let tail = lastLogLines(14)
        alert.informativeText = message + (tail.isEmpty ? "" : "\n\nLast lines of the log:\n" + tail)
        alert.addButton(withTitle: "Open Log")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(logURL)
        }
        NSApp.terminate(nil)
    }

    // MARK: window

    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 880),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Form Studio"
        window.minSize = NSSize(width: 980, height: 660)
        window.backgroundColor = paper
        window.appearance = NSAppearance(named: .aqua)  // the interface is designed light
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        _ = window.setFrameAutosaveName("FormStudioMainWindow")

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.isHidden = true
        webView.translatesAutoresizingMaskIntoConstraints = false

        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.startAnimation(nil)
        statusLabel = NSTextField(labelWithString: "Starting Form Studio…")
        statusLabel.textColor = NSColor(srgbRed: 0.29, green: 0.29, blue: 0.27, alpha: 1)
        statusLabel.font = NSFont.systemFont(ofSize: 13)
        loadingView = NSStackView(views: [spinner, statusLabel])
        loadingView.orientation = .vertical
        loadingView.spacing = 12
        loadingView.translatesAutoresizingMaskIntoConstraints = false

        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = paper.cgColor
        content.addSubview(webView)
        content.addSubview(loadingView)
        window.contentView = content
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            loadingView.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            loadingView.centerYAnchor.constraint(equalTo: content.centerYAnchor),
        ])
        window.makeKeyAndOrderFront(nil)
    }

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Form Studio", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Form Studio", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Form Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let reload = view.addItem(withTitle: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        let library = view.addItem(withTitle: "Library", action: #selector(showLibrary), keyEquivalent: "l")
        library.target = self
        let log = view.addItem(withTitle: "Show Log", action: #selector(showLog), keyEquivalent: "")
        log.target = self
        viewItem.submenu = view
        main.addItem(viewItem)

        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    @objc func reloadPage() { if ready { webView.reload() } }
    @objc func showLibrary() { if ready { webView.load(URLRequest(url: baseURL)) } }
    @objc func showLog() { NSWorkspace.shared.open(logURL) }

    // MARK: web view: page loading

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.isHidden = false
        loadingView.isHidden = true
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // the server answered /api/health but the page didn't load yet: try again shortly
        if ready && webView.isHidden {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { webView.load(URLRequest(url: self.baseURL)) }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload {
            return decisionHandler(.download)
        }
        if let url = navigationAction.request.url, let scheme = url.scheme, scheme.hasPrefix("http"),
           url.host != "127.0.0.1" {
            NSWorkspace.shared.open(url)  // external links open in your browser
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition")?.lowercased() ?? ""
        if !navigationResponse.canShowMIMEType || disposition.hasPrefix("attachment") {
            return decisionHandler(.download)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    // MARK: downloads ("Save" buttons)

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let url = panel.url else { return completionHandler(nil) }
            try? FileManager.default.removeItem(at: url)  // the panel already asked about replacing
            completionHandler(url)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let alert = NSAlert()
        alert.messageText = "The file couldn't be saved"
        alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window, completionHandler: nil)
    }

    // MARK: web view: file picker, alert, confirm, new windows

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
