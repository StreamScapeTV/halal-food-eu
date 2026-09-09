import Foundation
import Observation

@MainActor
@Observable
final class CatalogModuleSettingsModel {
    private(set) var installedModules: [InstalledCatalogModule] = []
    private(set) var remoteMarkets: [CatalogMarket] = []
    private(set) var isWorking = false
    private(set) var statusMessage: String?
    var errorMessage: String?

    private let service: (any CatalogModuleService)?
    private let preferences: AppPreferences
    private let bundledCatalogVersion: String
    var onMarketDidChange: (@MainActor @Sendable (CatalogMarket) -> Void)?

    init(
        service: (any CatalogModuleService)?,
        preferences: AppPreferences,
        bundledCatalogVersion: String
    ) {
        self.service = service
        self.preferences = preferences
        self.bundledCatalogVersion = bundledCatalogVersion
    }

    var selectedMarket: CatalogMarket { preferences.selectedMarket }

    var availableMarkets: [CatalogMarket] {
        Set([.germany] + installedModules.map(\.manifest.market) + remoteMarkets).sorted()
    }

    var installedSelectableMarkets: [CatalogMarket] {
        Set([.germany, selectedMarket] + installedModules.map(\.manifest.market)).sorted()
    }

    var downloadableMarkets: [CatalogMarket] {
        let installed = Set(installedModules.map(\.manifest.market))
        return remoteMarkets.filter { !installed.contains($0) }.sorted()
    }

    var activeInstalledModule: InstalledCatalogModule? {
        installedModules.first(where: { $0.manifest.market == selectedMarket })
    }

    var activeCatalogVersion: String? {
        activeInstalledModule?.manifest.catalogVersion
            ?? (selectedMarket == .germany ? bundledCatalogVersion.nilIfEmpty : nil)
    }

    var activeInstalledBytes: Int? { activeInstalledModule?.manifest.installedBytes }
    var activeCoverageLimitations: [String] { activeInstalledModule?.manifest.coverage.limitations ?? [] }
    var usesBundledGermanyFallback: Bool { selectedMarket == .germany && activeInstalledModule == nil }
    var canRemoveActiveDownload: Bool { activeInstalledModule != nil }

    func load() async {
        guard let service else {
            errorMessage = CatalogModuleError.updatesUnavailable.localizedDescription
            return
        }
        await perform {
            installedModules = try await service.installedModules()
            try await service.activatePersistedSelection(preferences.selectedMarket)
        }
    }

    func selectMarket(_ market: CatalogMarket) async {
        guard let service else {
            guard market == .germany else {
                errorMessage = CatalogModuleError.unavailableMarket(market.rawValue).localizedDescription
                return
            }
            preferences.selectedMarket = .germany
            onMarketDidChange?(.germany)
            return
        }
        await perform {
            try await service.selectMarket(market)
            preferences.selectedMarket = market
            statusMessage = String(
                format: String(localized: "Active market changed to %@.", table: "AppShell"),
                locale: .current,
                market.rawValue
            )
            onMarketDidChange?(market)
        }
    }

    func checkAvailableMarkets() async {
        guard let service else {
            errorMessage = CatalogModuleError.updatesUnavailable.localizedDescription
            return
        }
        await perform {
            remoteMarkets = try await service.availableRemoteMarkets()
            statusMessage = String(localized: "Available signed market modules refreshed.", table: "AppShell")
        }
    }

    func installLatest(for market: CatalogMarket) async {
        guard let service else {
            errorMessage = CatalogModuleError.updatesUnavailable.localizedDescription
            return
        }
        await perform {
            let module = try await service.installLatest(for: market)
            installedModules = try await service.installedModules()
            try await service.selectMarket(market)
            preferences.selectedMarket = market
            statusMessage = String(
                format: String(localized: "Installed catalog %@ for %@.", table: "AppShell"),
                locale: .current,
                module.manifest.catalogVersion,
                market.rawValue
            )
            onMarketDidChange?(market)
        }
    }

    func removeActiveDownload() async {
        guard let service, let module = activeInstalledModule else { return }
        let market = module.manifest.market
        await perform {
            try await service.removeDownloadedModule(for: market)
            installedModules = try await service.installedModules()
            if market == .germany {
                preferences.selectedMarket = .germany
                onMarketDidChange?(.germany)
                statusMessage = String(localized: "Downloaded catalog removed. The bundled Germany catalog remains available.", table: "AppShell")
            } else {
                onMarketDidChange?(market)
                statusMessage = String(
                    format: String(localized: "Downloaded %@ catalog removed. This market is unavailable until reinstalled or another market is selected.", table: "AppShell"),
                    locale: .current,
                    market.rawValue
                )
            }
        }
    }

    private func perform(_ operation: () async throws -> Void) async {
        guard !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            try await operation()
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
