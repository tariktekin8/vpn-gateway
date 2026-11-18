// VPN Gateway Callback: the macOS handler for globalprotectcallback: links. It forwards the link to the local VPN Gateway
// web app (POST /handler/callback) and quits. It never prints, logs or stores the link, the body or the token.
// Build and install with handlers/macos/install.sh. `CallbackHandler --set-default` makes it the scheme's default app.
import AppKit

let scheme = "globalprotectcallback"
let notRunning = "VPN Gateway web app is not running. Start ./vpn web, or paste the link into the VPN Gateway page."
let unreachable = "Could not reach the VPN Gateway web app. Paste the link into the VPN Gateway page instead."

// Port and handler token from data/secrets/web-handler.json; its path is written into Info.plist at install time.
func readHandlerFile() -> (port: Int, token: String)? {
    guard let path = Bundle.main.object(forInfoDictionaryKey: "HandlerFile") as? String, path.hasPrefix("/"),
          let data = FileManager.default.contents(atPath: path),
          let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let port = obj["port"] as? Int, (1...65535).contains(port),
          let token = obj["token"] as? String, !token.isEmpty else { return nil }
    return (port, token)
}

// nil on 2xx; else the server's "error" text (it never holds the link) or a generic message.
func failure(_ status: Int, _ data: Data?) -> String? {
    if (200..<300).contains(status) { return nil }
    if let data = data, let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
       let error = obj["error"] as? String, !error.isEmpty {
        return error
    }
    return unreachable
}

final class Delegate: NSObject, NSApplicationDelegate {
    var handled = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { _ in exit(0) }  // never linger
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            if self?.handled == false { NSApp.terminate(nil) }  // started without a link
        }
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard !handled, let url = urls.first(where: { $0.scheme?.lowercased() == scheme }) else { return }
        handled = true
        guard let handler = readHandlerFile(),
              let endpoint = URL(string: "http://127.0.0.1:\(handler.port)/handler/callback"),
              let body = try? JSONSerialization.data(withJSONObject: ["url": url.absoluteString]) else {
            return finish(notRunning)
        }
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
                                 timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(handler.token, forHTTPHeaderField: "X-Gateway-Handler-Token")
        request.httpBody = body
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]  // never through a proxy
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 10
        URLSession(configuration: config).dataTask(with: request) { data, response, _ in
            let message = failure((response as? HTTPURLResponse)?.statusCode ?? 0, data)
            DispatchQueue.main.async { self.finish(message) }
        }.resume()
    }

    func finish(_ message: String?) {
        if let message = message {
            NSApp.activate(ignoringOtherApps: true)
            let alert = NSAlert()
            alert.messageText = "VPN Gateway Callback"
            alert.informativeText = message
            alert.runModal()
        }
        NSApp.terminate(nil)
    }
}

let args = Array(CommandLine.arguments.dropFirst())
if args == ["--set-default"] {
    guard #available(macOS 12.0, *) else {
        print("error: needs macOS 12 or later")
        exit(1)
    }
    NSWorkspace.shared.setDefaultApplication(at: Bundle.main.bundleURL, toOpenURLsWithScheme: scheme) { error in
        if let error = error {
            print("error: could not make VPN Gateway Callback the default: \(error.localizedDescription)")
            exit(1)
        }
        print("ok: VPN Gateway Callback now opens \(scheme): links")
        exit(0)
    }
    RunLoop.main.run(until: Date(timeIntervalSinceNow: 30))
    print("error: timed out")
    exit(1)
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
