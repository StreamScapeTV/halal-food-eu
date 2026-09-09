import Foundation
import Testing
import UIKit
@testable import HalalFoodEU

@Suite("App shell and settings composition")
@MainActor
struct AppShellSettingsTests {
    @Test("The app exposes exactly Check, Saved, and Settings tabs")
    func topLevelTabs() {
        #expect(AppTab.allCases == [.check, .saved, .settings])
        let navigation = AppNavigationModel()
        #expect(navigation.selectedTab == .check)
        navigation.showSaved()
        #expect(navigation.selectedTab == .saved)
    }

    @Test("Runtime identity normalizes app, build, and catalog values")
    func runtimeIdentity() {
        let identity = AppRuntimeIdentity(
            version: " 0.1.0 ",
            build: " 7 ",
            catalogVersion: " catalog-v3 "
        )
        #expect(identity.version == "0.1.0")
        #expect(identity.build == "7")
        #expect(identity.catalogVersion == "catalog-v3")

        let unavailable = AppRuntimeIdentity(version: " ", build: nil, catalogVersion: "\n")
        #expect(unavailable.version == nil)
        #expect(unavailable.build == nil)
        #expect(unavailable.catalogVersion == nil)
    }

    @Test("Runtime identity reads the bundled catalog and local app metadata")
    func bundledRuntimeIdentity() throws {
        let bundle = Bundle.main
        let manifestURL = try #require(bundle.url(forResource: "catalog-manifest", withExtension: "json"))
        let expectedCatalog = try CatalogRuntimeIdentityLoader.load(manifestURL: manifestURL)
        let identity = AppRuntimeIdentity(bundle: bundle)

        #expect(identity.catalogVersion == expectedCatalog.catalogVersion)
        #expect(identity.version == bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
        #expect(identity.build == bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String)
    }

    @Test("Language handoff uses the public iOS app-settings URL")
    func systemSettingsURL() {
        #expect(AppSettingsLink.url.absoluteString == UIApplication.openSettingsURLString)
    }

    @Test("Catalog settings restores persisted market only through the service protocol")
    func catalogSettingsUsesServiceProtocol() async throws {
        let suite = "AppShellSettingsTests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("FR", forKey: AppPreferences.selectedMarketKey)
        let preferences = AppPreferences(defaults: defaults)
        let service = SettingsCatalogModuleService()
        let model = CatalogModuleSettingsModel(
            service: service,
            preferences: preferences,
            bundledCatalogVersion: "1.0.0"
        )

        await model.load()

        #expect(model.selectedMarket == CatalogMarket(rawValue: "FR"))
        #expect(await service.activatedMarkets() == [CatalogMarket(rawValue: "FR")!])
    }

    @Test("Catalog settings fails back to bundled Germany when persisted activation is unavailable")
    func catalogSettingsFallsBackToGermany() async throws {
        let suite = "AppShellSettingsTests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("FR", forKey: AppPreferences.selectedMarketKey)
        let preferences = AppPreferences(defaults: defaults)
        let service = SettingsCatalogModuleService(activationError: CatalogModuleError.unavailableMarket("FR"))
        let model = CatalogModuleSettingsModel(
            service: service,
            preferences: preferences,
            bundledCatalogVersion: "1.0.0"
        )

        await model.load()

        #expect(model.selectedMarket == .germany)
        #expect(await service.selectedMarkets() == [.germany])
        #expect(model.errorMessage != nil)
    }
}

private actor SettingsCatalogModuleService: CatalogModuleService {
    private let activationError: Error?
    private var activations: [CatalogMarket] = []
    private var selections: [CatalogMarket] = []

    init(activationError: Error? = nil) {
        self.activationError = activationError
    }

    func activatePersistedSelection(_ market: CatalogMarket) async throws {
        activations.append(market)
        if let activationError { throw activationError }
    }

    func selectMarket(_ market: CatalogMarket) async throws {
        selections.append(market)
    }

    func installedModules() async throws -> [InstalledCatalogModule] { [] }
    func availableRemoteMarkets() async throws -> [CatalogMarket] { [] }
    func installLatest(for market: CatalogMarket) async throws -> InstalledCatalogModule {
        throw CatalogModuleError.updatesUnavailable
    }
    func removeDownloadedModule(for market: CatalogMarket) async throws {}

    func activatedMarkets() -> [CatalogMarket] { activations }
    func selectedMarkets() -> [CatalogMarket] { selections }
}
