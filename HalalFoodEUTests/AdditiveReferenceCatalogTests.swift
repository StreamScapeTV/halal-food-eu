import Foundation
import Testing
@testable import HalalFoodEU

@Suite("EU additive reference runtime")
struct AdditiveReferenceCatalogTests {
    private func catalog() throws -> AdditiveReferenceCatalog {
        try AdditiveReferenceCatalogLoader.load(bundle: .main)
    }

    @Test("Bundled reference stays identity-only and reviewed")
    func bundledReferenceLoads() throws {
        let catalog = try catalog()
        #expect(catalog.schemaVersion == 1)
        #expect(catalog.identityOnly)
        #expect(catalog.source.sourceKey == "eu-additives")
        #expect(catalog.source.licenseIdentifier == "CC-BY-4.0")
        #expect(catalog.entries.count >= 9)
    }

    @Test("E-number spacing and subtype preserve the exact package span")
    func eNumberSubtypeSpan() throws {
        let catalog = try catalog()
        let matches = catalog.matches(
            in: "Farbstoff: E 1 6 0 a ( i )",
            languageCode: "de-DE"
        )
        let match = try #require(matches.first(where: { $0.additiveID == "E160a(i)" }))
        #expect(match.sourceSpan.text == "E 1 6 0 a ( i )")
        #expect(match.sourceSpan.endUTF16 > match.sourceSpan.startUTF16)
    }

    @Test("German functional-class syntax matches a reviewed additive name")
    func germanNameMatch() throws {
        let catalog = try catalog()
        let matches = catalog.matches(
            in: "Emulgator: Lecithine, Salz",
            languageCode: "de"
        )
        let match = try #require(matches.first(where: { $0.additiveID == "E322" }))
        #expect(match.displayName == "Lecithine")
        #expect(match.sourceSpan.text == "Lecithine")
    }

    @Test("E322 preserves every cited origin possibility")
    func multipleOriginsRemainUnresolved() throws {
        let catalog = try catalog()
        let match = try #require(
            catalog.matches(in: "E322", languageCode: "en")
                .first(where: { $0.additiveID == "E322" })
        )
        #expect(match.originPossibilities.map(\.kind) == ["animal-derived", "plant-derived"])
    }

    @Test("Name matching uses boundaries rather than fuzzy prefixes")
    func nameBoundary() throws {
        let catalog = try catalog()
        #expect(catalog.matches(in: "Schellackharz", languageCode: "de").contains(where: { $0.additiveID == "E904" }))
        #expect(!catalog.matches(in: "Schellackharzig", languageCode: "de").contains(where: { $0.additiveID == "E904" }))
    }

    @Test("Reference UI explicitly separates additive identity from halal status")
    func localizedSafetyMeaning() throws {
        let english = AdditiveReferenceText(bundle: .main, locale: Locale(identifier: "en_DE"))
        #expect(english.string("additive.footer").contains("not a halal ruling"))

        let path = try #require(Bundle.main.path(forResource: "de", ofType: "lproj"))
        let germanBundle = try #require(Bundle(path: path))
        let german = AdditiveReferenceText(bundle: germanBundle, locale: Locale(identifier: "de_DE"))
        #expect(german.string("additive.footer").contains("kein Halal-Urteil"))
    }
}
