import AppKit
import CryptoKit
import Foundation
import ImageIO

private struct Request: Encodable {
    let schemaVersion = 1
    let submissionID = "hfeu-submission-12345678-1234-1234-1234-123456789abc"
    let admissionID = "hfeu-admission-12345678-1234-1234-1234-123456789abc"
    let languageHints = ["de-DE", "en-US"]
    let attachments: [Attachment]
}
private struct Attachment: Encodable {
    let fileName: String
    let purpose: String
    let inputSha256: String
}
private struct Report: Decodable {
    let engine: String
    let verificationState: String
    let attachments: [ReportAttachment]
}
private struct ReportAttachment: Decodable {
    let fileName: String
    let purpose: String
    let inputSha256: String
    let sanitizedSha256: String
    let ocrState: String
    let lines: [Line]
}
private struct Line: Decodable { let text: String }

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

@MainActor
private func jpeg(text: String?) throws -> Data {
    let size = NSSize(width: 1200, height: 800)
    let image = NSImage(size: size)
    image.lockFocus()
    NSColor.white.setFill()
    NSRect(origin: .zero, size: size).fill()
    if let text {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .left
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 70, weight: .medium),
            .foregroundColor: NSColor.black,
            .paragraphStyle: paragraph,
        ]
        NSString(string: text).draw(in: NSRect(x: 60, y: 250, width: 1080, height: 360), withAttributes: attributes)
    }
    image.unlockFocus()
    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let data = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.92]) else {
        throw NSError(domain: "smoke", code: 1)
    }
    return data
}

@main
struct Smoke {
    @MainActor
    static func main() throws {
        guard CommandLine.arguments.count == 2 else {
            throw NSError(domain: "smoke", code: 2, userInfo: [NSLocalizedDescriptionKey: "expected OCR binary path"])
        }
        let binary = URL(fileURLWithPath: CommandLine.arguments[1])
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent("product-evidence-ocr-smoke-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: root) }
        let input = root.appendingPathComponent("input")
        let output = root.appendingPathComponent("output")
        try fm.createDirectory(at: input, withIntermediateDirectories: true)
        let values: [(String, String, Data)] = [
            ("front-1.jpg", "front", try jpeg(text: "Test Produkt 250 g")),
            ("ingredients-1.jpg", "ingredients", try jpeg(text: "Zutaten: Wasser, Zucker, Hafer")),
            ("ingredients-2.jpg", "ingredients", try jpeg(text: nil)),
        ]
        let attachments = try values.map { name, purpose, data -> Attachment in
            try data.write(to: input.appendingPathComponent(name), options: .atomic)
            return Attachment(fileName: name, purpose: purpose, inputSha256: sha256(data))
        }
        let requestURL = root.appendingPathComponent("request.json")
        let reportURL = root.appendingPathComponent("report.json")
        try JSONEncoder().encode(Request(attachments: attachments)).write(to: requestURL)
        let process = Process()
        process.executableURL = binary
        process.arguments = [
            "--request", requestURL.path,
            "--input-dir", input.path,
            "--output-dir", output.path,
            "--report", reportURL.path,
        ]
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw NSError(domain: "smoke", code: 3, userInfo: [NSLocalizedDescriptionKey: "OCR process failed"])
        }
        let report = try JSONDecoder().decode(Report.self, from: Data(contentsOf: reportURL))
        guard report.engine == "apple-vision-local", report.verificationState == "unverified" else {
            throw NSError(domain: "smoke", code: 4, userInfo: [NSLocalizedDescriptionKey: "trust-state mismatch"])
        }
        guard let front = report.attachments.first(where: { $0.fileName == "front-1.jpg" }),
              front.ocrState == "not-requested", front.lines.isEmpty else {
            throw NSError(domain: "smoke", code: 5, userInfo: [NSLocalizedDescriptionKey: "non-ingredient OCR boundary failed"])
        }
        guard let ingredients = report.attachments.first(where: { $0.fileName == "ingredients-1.jpg" }),
              ingredients.ocrState == "recognized",
              ingredients.lines.contains(where: { $0.text.localizedCaseInsensitiveContains("Wasser") }) else {
            throw NSError(domain: "smoke", code: 6, userInfo: [NSLocalizedDescriptionKey: "German ingredient OCR did not recognize expected text"])
        }
        guard let blank = report.attachments.first(where: { $0.fileName == "ingredients-2.jpg" }),
              blank.ocrState == "unreadable", blank.lines.isEmpty else {
            throw NSError(domain: "smoke", code: 7, userInfo: [NSLocalizedDescriptionKey: "unreadable OCR did not fail closed"])
        }
        for attachment in report.attachments {
            guard attachment.inputSha256 != attachment.sanitizedSha256 else {
                throw NSError(domain: "smoke", code: 8, userInfo: [NSLocalizedDescriptionKey: "image was not re-encoded"])
            }
            let imageURL = output.appendingPathComponent(attachment.fileName)
            guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  properties[kCGImagePropertyGPSDictionary] == nil else {
                throw NSError(domain: "smoke", code: 9, userInfo: [NSLocalizedDescriptionKey: "GPS metadata survived re-encode"])
            }
        }
        print("product evidence OCR smoke passed")
    }
}
