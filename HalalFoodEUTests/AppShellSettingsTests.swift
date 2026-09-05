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
}
