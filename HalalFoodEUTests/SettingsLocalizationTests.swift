import Foundation
import Testing

@Suite("App shell and settings localization")
struct SettingsLocalizationTests {
    @Test("English and German app-shell tables cover the complete shell surface")
    func localizedResources() throws {
        let bundle = Bundle.main
        let englishPath = try #require(bundle.path(forResource: "en", ofType: "lproj"))
        let germanPath = try #require(bundle.path(forResource: "de", ofType: "lproj"))
        let english = try #require(Bundle(path: englishPath))
        let german = try #require(Bundle(path: germanPath))

        let keys = [
            "Check",
            "Saved",
            "Settings",
            "Appearance",
            "System",
            "Light",
            "Dark",
            "Uses the selected system color scheme without changing product evidence or assessments.",
            "Language",
            "Open iOS app settings",
            "Opens the system settings where iOS manages this app's language.",
            "Halal Food EU follows the app language selected in iOS. English and German are available; there is no in-app language override.",
            "Privacy and local data",
            "Catalog lookups and product evidence use the catalog bundled with the app and do not require a network connection.",
            "Ingredient OCR runs on device; its captured image and recognized text are not stored by the app.",
            "Favorites and optional scan history stay local. The app has no accounts, analytics, advertising, or tracking.",
            "Manage saved products and history",
            "Switches to Saved where favorites and optional scan history can be managed.",
            "About",
            "Version",
            "Build",
            "Catalog version",
            "Unavailable",
            "Scan a product",
            "Opens the camera barcode scanner.",
            "EAN, UPC, or GTIN",
            "Barcode number",
            "Look up barcode",
            "Check a packaged food",
            "Synthetic demonstration data",
            "Reviewed-halal oat drink — 0200000000004",
            "Not-halal gelatine sweets — 0200000000011",
            "Questionable dessert — 0200000000028",
            "Evidence, not a fatwa",
            "Always check current packaging, the manufacturer or certifier, and a trusted qualified scholar for consequential decisions. Formulations and supply chains change.",
            "Ready to scan",
            "Scan a barcode, search the catalog, or enter one manually.",
            "Looking up the bundled catalog…",
            "Looking up the offline product catalog",
            "Product not found",
            "GTIN %@ is not present in this catalog version. This does not mean the product is halal or not halal.",
            "Prepares a private local evidence package that you can review before sending or sharing.",
            "Invalid barcode. %@",
            "Catalog lookup failed",
            "Retry",
        ]

        for key in keys {
            #expect(english.localizedString(forKey: key, value: "__missing__", table: "AppShell") != "__missing__")
            #expect(german.localizedString(forKey: key, value: "__missing__", table: "AppShell") != "__missing__")
        }

        #expect(german.localizedString(forKey: "Settings", value: nil, table: "AppShell") == "Einstellungen")
        #expect(german.localizedString(forKey: "Saved", value: nil, table: "AppShell") == "Gespeichert")
        #expect(german.localizedString(forKey: "Dark", value: nil, table: "AppShell") == "Dunkel")
    }
}
