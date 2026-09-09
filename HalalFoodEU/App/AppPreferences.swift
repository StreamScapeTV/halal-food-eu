import Foundation
import Observation
import SwiftUI

enum AppAppearance: String, CaseIterable, Identifiable, Sendable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

@MainActor
@Observable
final class AppPreferences {
    static let appearanceKey = "settings.appearance"
    static let selectedMarketKey = "settings.catalogMarket"

    var appearance: AppAppearance {
        didSet { defaults.set(appearance.rawValue, forKey: Self.appearanceKey) }
    }

    var selectedMarket: CatalogMarket {
        didSet { defaults.set(selectedMarket.rawValue, forKey: Self.selectedMarketKey) }
    }

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        appearance = AppAppearance(rawValue: defaults.string(forKey: Self.appearanceKey) ?? "") ?? .system
        selectedMarket = CatalogMarket(rawValue: defaults.string(forKey: Self.selectedMarketKey) ?? "") ?? .germany
    }
}
