import Foundation

protocol CatalogModuleService: Sendable {
    func activatePersistedSelection(_ market: CatalogMarket) async throws
    func selectMarket(_ market: CatalogMarket) async throws
    func installedModules() async throws -> [InstalledCatalogModule]
    func availableRemoteMarkets() async throws -> [CatalogMarket]
    func installLatest(for market: CatalogMarket) async throws -> InstalledCatalogModule
    func removeDownloadedModule(for market: CatalogMarket) async throws
}

protocol CatalogModuleTransport: Sendable {
    func availableMarkets() async throws -> [CatalogMarket]
    func latestCandidate(for market: CatalogMarket) async throws -> CatalogModuleCandidate
}
