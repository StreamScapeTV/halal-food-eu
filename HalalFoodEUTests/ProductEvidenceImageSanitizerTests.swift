import Foundation
import ImageIO
import Testing
import UIKit
import UniformTypeIdentifiers
@testable import HalalFoodEU

@Suite("Product evidence image sanitation")
struct ProductEvidenceImageSanitizerTests {
    @Test("Re-encoding strips GPS metadata and bounds dimensions")
    func stripsMetadata() throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 1_600, height: 1_000))
        let image = renderer.image { context in
            UIColor.white.setFill()
            context.cgContext.fill(CGRect(x: 0, y: 0, width: 1_600, height: 1_000))
            UIColor.black.setFill()
            context.cgContext.fill(CGRect(x: 100, y: 100, width: 1_000, height: 200))
        }
        let cgImage = try #require(image.cgImage)
        let input = NSMutableData()
        let destination = try #require(CGImageDestinationCreateWithData(
            input as CFMutableData,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ))
        let metadata: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: 0.9,
            kCGImagePropertyGPSDictionary: [
                kCGImagePropertyGPSLatitude: 50.35,
                kCGImagePropertyGPSLatitudeRef: "N",
                kCGImagePropertyGPSLongitude: 7.59,
                kCGImagePropertyGPSLongitudeRef: "E",
            ],
        ]
        CGImageDestinationAddImage(destination, cgImage, metadata as CFDictionary)
        #expect(CGImageDestinationFinalize(destination))

        let result = try ProductEvidenceImageSanitizer().sanitize(
            input as Data,
            purpose: .front,
            id: UUID(uuidString: "12345678-1234-1234-1234-123456789ABC")!
        )
        #expect(result.pixelWidth <= ProductEvidenceImageSanitizer.maximumOutputDimension)
        #expect(result.pixelHeight <= ProductEvidenceImageSanitizer.maximumOutputDimension)
        #expect(result.data.count <= ProductEvidenceImageSanitizer.maximumOutputBytes)
        #expect(result.sha256.count == 64)

        let source = try #require(CGImageSourceCreateWithData(result.data as CFData, nil))
        let properties = try #require(
            CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        )
        #expect(properties[kCGImagePropertyGPSDictionary] == nil)
    }

    @Test("Tiny images are rejected before submission")
    func rejectsTinyImage() throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 200, height: 200))
        let data = try #require(renderer.image { _ in }.jpegData(compressionQuality: 0.9))
        #expect(throws: ProductEvidenceImageSanitizer.SanitizationError.dimensionsTooSmall) {
            try ProductEvidenceImageSanitizer().sanitize(data, purpose: .barcode)
        }
    }
}
