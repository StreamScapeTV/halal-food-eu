import Foundation

struct SavedProductReference: Identifiable, Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        case history
        case favorite
    }

    let id: String
    let kind: Kind
    let barcode: Barcode
    let savedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker

    init(historyEntry: ScanHistoryEntry) {
        id = "history-\(historyEntry.id)"
        kind = .history
        barcode = historyEntry.barcode
        savedAt = historyEntry.scannedAt
        catalogVersion = historyEntry.catalogVersion
        versionMarker = historyEntry.versionMarker
    }

    init(favorite: FavoriteProduct) {
        id = "favorite-\(favorite.barcode.rawValue)"
        kind = .favorite
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

    var catalogVersionChanged: Bool {
        reference.catalogVersion != currentCatalogVersion
    }
}

struct ResolveSavedProduct: Sendable {
    private let catalog: any ProductCatalog
    private let currentCatalogVersion: String

    init(catalog: any ProductCatalog, currentCatalogVersion: String) {
        self.catalog = catalog
        self.currentCatalogVersion = currentCatalogVersion
    }

    func callAsFunction(_ reference: SavedProductReference) async throws -> ResolvedSavedProduct {
        try Task.checkCancellation()
        let product = try await catalog.product(for: reference.barcode)
        try Task.checkCancellation()
        return ResolvedSavedProduct(
            reference: reference,
            currentProduct: product,
            currentCatalogVersion: product?.catalogVersion ?? currentCatalogVersion,
            changeState: reference.versionMarker.comparison(with: product)
        )
    }
}
