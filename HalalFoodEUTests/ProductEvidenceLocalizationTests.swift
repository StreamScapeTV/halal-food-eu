import Foundation
import Testing

private final class ProductEvidenceLocalizationBundleToken: NSObject {}

@Suite("Product evidence localization")
struct ProductEvidenceLocalizationTests {
    @Test("English and German submission resources are present")
    func localizedResources() throws {
        let bundle = Bundle.main
        let english = try #require(bundle.path(forResource: "en", ofType: "lproj"))
        let german = try #require(bundle.path(forResource: "de", ofType: "lproj"))
        let englishBundle = try #require(Bundle(path: english))
        let germanBundle = try #require(Bundle(path: german))

        #expect(
            englishBundle.localizedString(
                forKey: "Submit product evidence",
                value: nil,
                table: "Localizable"
            ) == "Submit product evidence"
        )
        #expect(
            germanBundle.localizedString(
                forKey: "Submit product evidence",
                value: nil,
                table: "Localizable"
            ) == "Produktnachweise einreichen"
        )
        #expect(
            germanBundle.localizedString(
                forKey: "Review email",
                value: nil,
                table: "Localizable"
            ) == "E-Mail prüfen"
        )
    }
}
