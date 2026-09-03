import Foundation
import Testing

@Suite("Local history and favorites localization")
struct UserProductLibraryLocalizationTests {
    @Test("English and German saved-product resources are present")
    func localizedResources() throws {
        let bundle = Bundle.main
        let english = try #require(bundle.path(forResource: "en", ofType: "lproj"))
        let german = try #require(bundle.path(forResource: "de", ofType: "lproj"))
        let englishBundle = try #require(Bundle(path: english))
        let germanBundle = try #require(Bundle(path: german))

        #expect(
            englishBundle.localizedString(
                forKey: "Saved products",
                value: nil,
                table: "UserLibrary"
            ) == "Saved products"
        )
        #expect(
            germanBundle.localizedString(
                forKey: "Saved products",
                value: nil,
                table: "UserLibrary"
            ) == "Gespeicherte Produkte"
        )
        #expect(
            germanBundle.localizedString(
                forKey: "Save scan history",
                value: nil,
                table: "UserLibrary"
            ) == "Scanverlauf speichern"
        )
        #expect(
            germanBundle.localizedString(
                forKey: "Favorites stay on this device and do not enable scan history.",
                value: nil,
                table: "UserLibrary"
            ) == "Favoriten bleiben auf diesem Gerät und aktivieren den Scanverlauf nicht."
        )
    }
}
