import Foundation
import SwiftUI
import Testing
@testable import HalalFoodEU

@Suite("Local app preferences")
@MainActor
struct AppPreferencesTests {
    @Test("Appearance defaults to the system and maps only accepted values")
    func defaultAndColorSchemes() throws {
        let (defaults, suiteName) = try isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let preferences = AppPreferences(defaults: defaults)
        #expect(preferences.appearance == .system)
        #expect(preferences.appearance.colorScheme == nil)
        #expect(AppAppearance.light.colorScheme == .light)
        #expect(AppAppearance.dark.colorScheme == .dark)
        #expect(AppAppearance.allCases == [.system, .light, .dark])
    }

    @Test("Appearance and selected market persist as bounded local preferences")
    func persistenceIsBounded() throws {
        let (defaults, suiteName) = try isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let preferences = AppPreferences(defaults: defaults)
        preferences.appearance = .dark
        preferences.selectedMarket = try #require(CatalogMarket(rawValue: "FR"))

        let reloaded = AppPreferences(defaults: defaults)
        #expect(reloaded.appearance == .dark)
        #expect(reloaded.selectedMarket.rawValue == "FR")
        let persisted = defaults.persistentDomain(forName: suiteName) ?? [:]
        #expect(Set(persisted.keys) == [AppPreferences.appearanceKey, AppPreferences.selectedMarketKey])
        #expect(persisted[AppPreferences.appearanceKey] as? String == AppAppearance.dark.rawValue)
        #expect(persisted[AppPreferences.selectedMarketKey] as? String == "FR")
    }

    @Test("Unknown persisted appearance fails back to System")
    func invalidValueFallsBackToSystem() throws {
        let (defaults, suiteName) = try isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        defaults.set("sepia", forKey: AppPreferences.appearanceKey)
        defaults.set("not-a-market", forKey: AppPreferences.selectedMarketKey)

        let preferences = AppPreferences(defaults: defaults)
        #expect(preferences.appearance == .system)
        #expect(preferences.selectedMarket == .germany)
    }

    private func isolatedDefaults() throws -> (UserDefaults, String) {
        let suiteName = "HalalFoodEU.AppPreferencesTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        return (defaults, suiteName)
    }
}
