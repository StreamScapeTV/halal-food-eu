import Testing
@testable import HalalFoodEU

@Suite("Lookup use case")
struct LookupProductByBarcodeTests {
    @Test("Invalid input does not call the catalog")
    func invalidInputSkipsCatalog() async {
        let catalog = CountingCatalog()
        let lookup = LookupProductByBarcode(catalog: catalog)

        do {
            _ = try await lookup("0200000000005")
            Issue.record("Expected invalid barcode failure")
        } catch let error as Barcode.ValidationError {
            #expect(error == .invalidCheckDigit)
        } catch {
            Issue.record("Unexpected error: \(error)")
        }

        let calls = await catalog.callCount
        #expect(calls == 0)
    }

    @Test("Valid input returns canonical barcode even when product is absent")
    func exposesCanonicalBarcode() async throws {
        let catalog = CountingCatalog()
        let lookup = LookupProductByBarcode(catalog: catalog)
        let result = try await lookup("0200000000035")

        #expect(result.barcode.rawValue == "00200000000035")
        #expect(result.product == nil)
        let calls = await catalog.callCount
        #expect(calls == 1)
    }
}

private actor CountingCatalog: ProductCatalog {
    private(set) var callCount = 0

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        callCount += 1
        return nil
    }
}
