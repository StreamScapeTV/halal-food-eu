import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

private let maximumInputBytes = 4_000_000
private let maximumOutputBytes = 4_000_000
private let maximumDimension = 2_400
private let maximumDecodedPixels = 12_000_000
private let supportedLanguageHints = Set(["de-DE", "en-US"])

private struct OCRRequest: Decodable {
    let schemaVersion: Int
    let submissionID: String
    let admissionID: String
    let languageHints: [String]
    let attachments: [RequestAttachment]
}

private struct RequestAttachment: Decodable {
    let fileName: String
    let purpose: String
    let inputSha256: String
}

private struct OCRReport: Encodable {
    let schemaVersion: Int
    let submissionID: String
    let admissionID: String
    let engine: String
    let engineVersion: String
    let generatedAt: String
    let verificationState: String
    let attachments: [ReportAttachment]
}

private struct ReportAttachment: Encodable {
    let fileName: String
    let purpose: String
    let inputSha256: String
    let sanitizedSha256: String
    let sanitizedByteSize: Int
    let pixelWidth: Int
    let pixelHeight: Int
    let ocrState: String
    let recognitionLanguages: [String]
    let lines: [RecognizedLine]
}

private struct RecognizedLine: Encodable {
    let text: String
    let confidence: Double
    let boundingBox: [Double]
}

private enum OCRToolError: LocalizedError {
    case usage
    case invalidRequest(String)
    case unsafeImage(String)
    case encodingFailed(String)
    case io(String)

    var errorDescription: String? {
        switch self {
        case .usage:
            return "usage: product_evidence_ocr --request <json> --input-dir <dir> --output-dir <dir> --report <json>"
        case let .invalidRequest(message), let .unsafeImage(message), let .encodingFailed(message), let .io(message):
            return message
        }
    }
}

private struct Arguments {
    let request: URL
    let inputDirectory: URL
    let outputDirectory: URL
    let report: URL

    init(_ raw: [String]) throws {
        guard raw.count == 8 else { throw OCRToolError.usage }
        var values: [String: String] = [:]
        var index = 0
        while index < raw.count {
            guard raw[index].hasPrefix("--"), index + 1 < raw.count else { throw OCRToolError.usage }
            values[raw[index]] = raw[index + 1]
            index += 2
        }
        guard let request = values["--request"],
              let input = values["--input-dir"],
              let output = values["--output-dir"],
              let report = values["--report"] else {
            throw OCRToolError.usage
        }
        self.request = URL(fileURLWithPath: request).standardizedFileURL
        inputDirectory = URL(fileURLWithPath: input).standardizedFileURL
        outputDirectory = URL(fileURLWithPath: output).standardizedFileURL
        self.report = URL(fileURLWithPath: report).standardizedFileURL
    }
}

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func safeAttachmentName(_ value: String) -> Bool {
    guard value == URL(fileURLWithPath: value).lastPathComponent,
          !value.contains("/"), !value.contains("\\") else { return false }
    let pattern = #"^(barcode|front|ingredients|certification|nutrition)-[1-9][0-9]*\.jpg$"#
    return value.range(of: pattern, options: .regularExpression) != nil
}

private func decodeBoundedImage(data: Data, fileName: String) throws -> CGImage {
    guard !data.isEmpty, data.count <= maximumInputBytes else {
        throw OCRToolError.unsafeImage("\(fileName): input byte bound exceeded")
    }
    guard let source = CGImageSourceCreateWithData(data as CFData, nil), CGImageSourceGetCount(source) == 1 else {
        throw OCRToolError.unsafeImage("\(fileName): image must decode as exactly one image")
    }
    guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
          let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
          width > 0, height > 0,
          width <= maximumDimension, height <= maximumDimension,
          width <= maximumDecodedPixels / max(height, 1) else {
        throw OCRToolError.unsafeImage("\(fileName): decoded dimensions exceed the reviewed bound")
    }
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: maximumDimension,
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
        throw OCRToolError.unsafeImage("\(fileName): ImageIO could not decode the submitted image")
    }
    guard image.width > 0, image.height > 0,
          image.width <= maximumDimension, image.height <= maximumDimension,
          image.width <= maximumDecodedPixels / max(image.height, 1) else {
        throw OCRToolError.unsafeImage("\(fileName): re-oriented image dimensions exceed the reviewed bound")
    }
    return image
}

private func encodeMetadataFreeJPEG(_ image: CGImage, fileName: String) throws -> Data {
    for quality in [0.86, 0.72, 0.58, 0.44] {
        let mutable = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            mutable as CFMutableData,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            throw OCRToolError.encodingFailed("\(fileName): JPEG encoder could not be created")
        }
        // A decoded CGImage carries pixels, not the source EXIF/GPS/XMP dictionaries.
        // Supplying only compression quality therefore deliberately does not copy
        // source metadata into the admitted review image.
        CGImageDestinationAddImage(
            destination,
            image,
            [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else {
            throw OCRToolError.encodingFailed("\(fileName): JPEG re-encode failed")
        }
        let candidate = mutable as Data
        if !candidate.isEmpty, candidate.count <= maximumOutputBytes {
            return candidate
        }
    }
    throw OCRToolError.encodingFailed("\(fileName): re-encoded image remains above the reviewed byte bound")
}

private func recognizeIngredients(in image: CGImage, hints: [String]) throws -> ([String], [RecognizedLine]) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let revision = request.revision
    let available = Set(try VNRecognizeTextRequest.supportedRecognitionLanguages(for: .accurate, revision: revision))
    let languages = hints.filter { supportedLanguageHints.contains($0) && available.contains($0) }
    if !languages.isEmpty {
        request.recognitionLanguages = languages
    }
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted { lhs, rhs in
        let yDelta = lhs.boundingBox.maxY - rhs.boundingBox.maxY
        if abs(yDelta) > 0.005 { return yDelta > 0 }
        return lhs.boundingBox.minX < rhs.boundingBox.minX
    }
    let lines = observations.compactMap { observation -> RecognizedLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        let box = observation.boundingBox
        return RecognizedLine(
            text: text,
            confidence: Double(candidate.confidence),
            boundingBox: [Double(box.minX), Double(box.minY), Double(box.width), Double(box.height)]
        )
    }
    return (languages, lines)
}

private func run(_ arguments: Arguments) throws {
    let requestData: Data
    do {
        requestData = try Data(contentsOf: arguments.request, options: [.mappedIfSafe])
    } catch {
        throw OCRToolError.io("request: \(error.localizedDescription)")
    }
    let request: OCRRequest
    do {
        request = try JSONDecoder().decode(OCRRequest.self, from: requestData)
    } catch {
        throw OCRToolError.invalidRequest("request: invalid JSON contract: \(error.localizedDescription)")
    }
    guard request.schemaVersion == 1,
          !request.submissionID.isEmpty,
          !request.admissionID.isEmpty,
          !request.attachments.isEmpty,
          request.attachments.count <= 8,
          Set(request.languageHints).isSubset(of: supportedLanguageHints) else {
        throw OCRToolError.invalidRequest("request: unsupported schema, identity, count, or language hint")
    }
    guard FileManager.default.fileExists(atPath: arguments.inputDirectory.path) else {
        throw OCRToolError.io("input directory does not exist")
    }
    if FileManager.default.fileExists(atPath: arguments.outputDirectory.path) {
        try FileManager.default.removeItem(at: arguments.outputDirectory)
    }
    try FileManager.default.createDirectory(at: arguments.outputDirectory, withIntermediateDirectories: true)

    var seenNames = Set<String>()
    var seenInputHashes = Set<String>()
    var seenSanitizedHashes = Set<String>()
    var results: [ReportAttachment] = []
    for attachment in request.attachments.sorted(by: { $0.fileName < $1.fileName }) {
        guard safeAttachmentName(attachment.fileName),
              ["barcode", "front", "ingredients", "certification", "nutrition"].contains(attachment.purpose),
              !seenNames.contains(attachment.fileName) else {
            throw OCRToolError.invalidRequest("request: unsafe, unsupported, or duplicate attachment")
        }
        seenNames.insert(attachment.fileName)
        let inputURL = arguments.inputDirectory.appendingPathComponent(attachment.fileName, isDirectory: false).standardizedFileURL
        guard inputURL.deletingLastPathComponent() == arguments.inputDirectory else {
            throw OCRToolError.invalidRequest("\(attachment.fileName): path escaped the input directory")
        }
        let inputData = try Data(contentsOf: inputURL, options: [.mappedIfSafe])
        let inputHash = sha256(inputData)
        guard inputHash == attachment.inputSha256 else {
            throw OCRToolError.unsafeImage("\(attachment.fileName): input SHA-256 changed after validation")
        }
        guard !seenInputHashes.contains(inputHash) else {
            throw OCRToolError.unsafeImage("\(attachment.fileName): duplicate input image bytes")
        }
        seenInputHashes.insert(inputHash)
        let image = try decodeBoundedImage(data: inputData, fileName: attachment.fileName)
        let sanitizedData = try encodeMetadataFreeJPEG(image, fileName: attachment.fileName)
        let sanitizedHash = sha256(sanitizedData)
        guard !seenSanitizedHashes.contains(sanitizedHash) else {
            throw OCRToolError.unsafeImage("\(attachment.fileName): duplicate re-encoded image bytes")
        }
        seenSanitizedHashes.insert(sanitizedHash)
        let outputURL = arguments.outputDirectory.appendingPathComponent(attachment.fileName, isDirectory: false)
        try sanitizedData.write(to: outputURL, options: [.atomic])

        let languages: [String]
        let lines: [RecognizedLine]
        let state: String
        if attachment.purpose == "ingredients" {
            let recognized = try recognizeIngredients(in: image, hints: request.languageHints)
            languages = recognized.0
            lines = recognized.1
            state = lines.isEmpty ? "unreadable" : "recognized"
        } else {
            languages = []
            lines = []
            state = "not-requested"
        }
        results.append(
            ReportAttachment(
                fileName: attachment.fileName,
                purpose: attachment.purpose,
                inputSha256: inputHash,
                sanitizedSha256: sanitizedHash,
                sanitizedByteSize: sanitizedData.count,
                pixelWidth: image.width,
                pixelHeight: image.height,
                ocrState: state,
                recognitionLanguages: languages,
                lines: lines
            )
        )
    }

    let engineVersion = "Vision/VNRecognizeTextRequest/revision-\(VNRecognizeTextRequest().revision)"
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let report = OCRReport(
        schemaVersion: 1,
        submissionID: request.submissionID,
        admissionID: request.admissionID,
        engine: "apple-vision-local",
        engineVersion: engineVersion,
        generatedAt: formatter.string(from: Date()),
        verificationState: "unverified",
        attachments: results
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let reportData = try encoder.encode(report)
    try reportData.write(to: arguments.report, options: [.atomic])
}

do {
    let arguments = try Arguments(Array(CommandLine.arguments.dropFirst()))
    try run(arguments)
} catch {
    fputs("product-evidence-ocr: \(error.localizedDescription)\n", stderr)
    exit(2)
}
