import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Saved product marker validation")
struct SavedProductVersionMarkerValidationTests {
    @Test("A valid generated marker round-trips through the persisted JSON contract")
    func validMarkerRoundTrips() throws {
        let marker = SavedProductVersionMarker(product: nil)
        let encoded = try JSONEncoder().encode(marker)
        let decoded = try JSONDecoder().decode(SavedProductVersionMarker.self, from: encoded)
        #expect(decoded == marker)
    }

    @Test("A present saved product rejects a malformed persisted fingerprint")
    func malformedPresentFingerprintFailsClosed() throws {
        let json = #"{"fingerprintSchemaVersion":1,"wasPresent":true,"recordFingerprint":"not-a-sha256"}"#
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }

    @Test("A previously missing product cannot carry a stale record fingerprint")
    func missingProductFingerprintFailsClosed() throws {
        let fingerprint = String(repeating: "a", count: 64)
        let json = "{\"fingerprintSchemaVersion\":1,\"wasPresent\":false,\"recordFingerprint\":\"\(fingerprint)\"}"
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }

    @Test("Unknown fingerprint schemas fail closed instead of guessing comparison semantics")
    func unknownFingerprintSchemaFailsClosed() throws {
        let json = #"{"fingerprintSchemaVersion":99,"wasPresent":false,"recordFingerprint":null}"#
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }
}
