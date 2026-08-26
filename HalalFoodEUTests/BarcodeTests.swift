import Foundation
import Testing
@testable import HalalFoodEU

@Suite("GTIN parsing and normalization")
struct BarcodeTests {
    @Test("EAN-13 is validated and normalized to GTIN-14")
    func normalizesEAN13() throws {
        let barcode = try Barcode(validating: "0200000000004")
        #expect(barcode.rawValue == "00200000000004")
    }

    @Test("Visual spaces and hyphens are ignored")
    func removesVisualSeparators() throws {
        let barcode = try Barcode(validating: " 0200-0000 0000 4 ")
        #expect(barcode.rawValue == "00200000000004")
    }

    @Test("Invalid check digits are rejected")
    func rejectsInvalidCheckDigit() {
        #expect(throws: Barcode.ValidationError.invalidCheckDigit) {
            try Barcode(validating: "0200000000005")
        }
    }

    @Test("GS1 Digital Link AI 01 is extracted")
    func extractsDigitalLink() throws {
        let barcode = try BarcodePayloadParser().parse(
            "https://id.gs1.org/01/00200000000004",
            symbology: .qr
        )
        #expect(barcode.rawValue == "00200000000004")
    }

    @Test("UPC-E can be expanded when scanner symbology is known")
    func expandsUPCE() throws {
        let barcode = try Barcode(validating: "01234565", symbology: .upce)
        #expect(barcode.rawValue == "00012345000065")
    }

    @Test("Ordinary QR URLs are not treated as products")
    func rejectsOrdinaryQR() {
        #expect(throws: Barcode.ValidationError.unsupportedPayload) {
            try BarcodePayloadParser().parse("https://example.com/recipe", symbology: .qr)
        }
    }
}
