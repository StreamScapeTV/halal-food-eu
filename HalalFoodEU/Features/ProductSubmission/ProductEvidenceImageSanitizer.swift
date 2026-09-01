import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

struct ProductEvidenceImageSanitizer: Sendable {
    static let maximumInputBytes = 30_000_000
    static let maximumDecodedPixels = 60_000_000
    static let maximumOutputDimension = 2_400
    static let minimumLongDimension = 800
    static let minimumShortDimension = 400
    static let maximumOutputBytes = 4_000_000

    enum SanitizationError: LocalizedError, Equatable, Sendable {
        case empty
        case inputTooLarge
        case unreadable
        case dimensionsTooSmall
        case dimensionsTooLarge
        case outputTooLarge
        case encodingFailed

        var errorDescription: String? {
            switch self {
            case .empty:
                String(localized: "The selected photo is empty.")
            case .inputTooLarge:
                String(localized: "The selected photo file is too large. Choose a smaller package photo.")
            case .unreadable:
                String(localized: "The selected photo could not be read as an image.")
            case .dimensionsTooSmall:
                String(localized: "The selected photo is too small to review reliably.")
            case .dimensionsTooLarge:
                String(localized: "The selected photo has unsafe decoded dimensions. Choose a smaller image.")
            case .outputTooLarge:
                String(localized: "The prepared photo is still too large for an email submission.")
            case .encodingFailed:
                String(localized: "The selected photo could not be prepared safely.")
            }
        }
    }

    func sanitize(
        _ input: Data,
        purpose: ProductEvidencePhotoPurpose,
        id: UUID = UUID()
    ) throws -> SanitizedProductEvidenceAttachment {
        guard !input.isEmpty else { throw SanitizationError.empty }
        guard input.count <= Self.maximumInputBytes else { throw SanitizationError.inputTooLarge }
        guard let source = CGImageSourceCreateWithData(input as CFData, nil),
              CGImageSourceGetCount(source) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
              let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
              width > 0,
              height > 0 else {
            throw SanitizationError.unreadable
        }

        let longDimension = max(width, height)
        let shortDimension = min(width, height)
        guard longDimension >= Self.minimumLongDimension,
              shortDimension >= Self.minimumShortDimension else {
            throw SanitizationError.dimensionsTooSmall
        }
        guard width <= Self.maximumDecodedPixels / max(height, 1) else {
            throw SanitizationError.dimensionsTooLarge
        }

        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: Self.maximumOutputDimension,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            throw SanitizationError.unreadable
        }

        var encoded: Data?
        for quality in [0.86, 0.72, 0.58, 0.44] {
            let mutable = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(
                mutable as CFMutableData,
                UTType.jpeg.identifier as CFString,
                1,
                nil
            ) else {
                throw SanitizationError.encodingFailed
            }
            CGImageDestinationAddImage(
                destination,
                image,
                [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
            )
            guard CGImageDestinationFinalize(destination) else {
                throw SanitizationError.encodingFailed
            }
            let candidate = mutable as Data
            if candidate.count <= Self.maximumOutputBytes {
                encoded = candidate
                break
            }
        }
        guard let encoded else { throw SanitizationError.outputTooLarge }

        let digest = SHA256.hash(data: encoded).map { String(format: "%02x", $0) }.joined()
        return SanitizedProductEvidenceAttachment(
            id: id,
            purpose: purpose,
            data: encoded,
            pixelWidth: image.width,
            pixelHeight: image.height,
            sha256: digest
        )
    }
}
