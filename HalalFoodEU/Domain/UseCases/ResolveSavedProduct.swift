import Foundation

struct SavedProductReference: Identifiable, Equatable, Sendable {
    enum Kind: Equatable, Sendable { case history, favorite }

    let id: String
    let kind: Kind
    let market: CatalogMarket
    let barcode: Barcode
    let savedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker

    init(historyEntry: ScanHistoryEntry) {
        id = "history-\(historyEntry.id)"
        kind = .history
        market = historyEntry.market
        barcode = historyEntry.barcode
        savedAt = historyEntry.scannedAt
        catalogVersion = historyEntry.catalogVersion
        versionMarker = historyEntry.versionMarker
    }

    init(favorite: FavoriteProduct) {
        id = "favorite-\(favorite.market.rawValue)-\(favorite.barcode.rawValue)"
        kind = .favorite
        market = favorite.market
        barcode = favorite.barcode
        savedAt = favorite.savedAt
        catalogVersion = favorite.catalogVersion
        versionMarker = favorite.versionMarker
    }
}

struct ResolvedSavedProduct: Equatable, Sendable {
    let reference: SavedProductReference
    let currentProduct: ProductRecord?
    let currentCatalogVersion: String
    let changeState: SavedProductChangeState

    var catalogVersionChanged: Bool { reference.catalogVersion != currentCatalogVersion }
}

struct ResolveSavedProduct: Sendable {
    private let catalog: any ProductCatalog
    private let fallbackCatalogVersion: String

    init(catalog: any ProductCatalog, currentCatalogVersion: String) {
        self.catalog = catalog
        fallbackCatalogVersion = currentCatalogVersion
    }

    func callAsFunction(_ reference: SavedProductReference) async throws -> ResolvedSavedProduct {
        try Task.checkCancellation()
        let product: ProductRecord?
        let catalogVersion: String
        if let marketCatalog = catalog as? any MarketScopedProductCatalog {
            product = try await marketCatalog.product(for: reference.barcode, market: reference.market)
            catalogVersion = try await marketCatalog.catalogVersion(for: reference.market)
        } else {
            guard reference.market == .germany else {
                throw ProductCatalogError.unavailable(
                    CatalogModuleError.unavailableMarket(reference.market.rawValue).localizedDescription
                )
            }
            product = try await catalog.product(for: reference.barcode)
            catalogVersion = product?.catalogVersion ?? fallbackCatalogVersion
        }
        try Task.checkCancellation()
        return ResolvedSavedProduct(
            reference: reference,
            currentProduct: product,
            currentCatalogVersion: catalogVersion,
            changeState: reference.versionMarker.comparison(with: product)
        )
    }
}
